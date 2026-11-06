from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import redact, resolve_python

_LOCK = threading.RLock()
_PROCESSES: dict[str, subprocess.Popen] = {}
_TASK_ID = re.compile(r"^[0-9a-f]{32}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _redact_log_file(path: Path) -> None:
    """Redact common credentials after a task ends, without loading the whole log."""
    if not path.is_file():
        return
    temporary = path.with_suffix(".redacted.tmp")
    try:
        with path.open("r", encoding="utf-8", errors="replace") as source, temporary.open(
            "w", encoding="utf-8", newline=""
        ) as target:
            for line in source:
                target.write(redact(line))
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)


SUPERVISOR_SOURCE = '''"""RunFix task supervisor: runs one command and records its exit code durably."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

argv = sys.argv[1:]
directory = os.path.dirname(os.path.abspath(__file__))
result_path = os.path.join(directory, "process_result.json")
record = {"returncode": None, "started_at": datetime.now(timezone.utc).isoformat(), "argv": argv}
try:
    child = subprocess.Popen(argv)
    record["returncode"] = child.wait()
except Exception as exc:
    record["returncode"] = 127
    record["error"] = type(exc).__name__ + ": " + str(exc)
record["finished_at"] = datetime.now(timezone.utc).isoformat()
temporary = result_path + ".tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(record, handle)
os.replace(temporary, result_path)
sys.exit(record["returncode"] or 0)
'''


def _read_process_result(task_dir: Path) -> dict[str, Any] | None:
    """Read the exit-code record the supervisor writes when the command finishes."""
    path = task_dir / "process_result.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _task_base(project: Path) -> Path:
    root = Path(project).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    state_dir = root / ".runfix"
    if state_dir.is_symlink():
        raise PermissionError("The project .runfix directory must not be a symlink.")
    state_dir.mkdir(parents=True, exist_ok=True)
    resolved_state = state_dir.resolve()
    try:
        resolved_state.relative_to(root)
    except ValueError as exc:
        raise PermissionError("RunFix state storage resolves outside the project directory.") from exc

    base = state_dir / "tasks"
    if base.is_symlink():
        raise PermissionError("The project .runfix/tasks directory must not be a symlink.")
    base.mkdir(parents=True, exist_ok=True)
    resolved = base.resolve()
    try:
        resolved.relative_to(resolved_state)
    except ValueError as exc:
        raise PermissionError("Task storage resolves outside the project RunFix directory.") from exc
    return resolved


def _task_dir(project: Path, task_id: str) -> Path:
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise ValueError("task_id must be a 32-character RunFix task identifier.")
    base = _task_base(project)
    path = (base / task_id).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise PermissionError("Task path escapes the task storage directory.") from exc
    if not path.is_dir():
        raise FileNotFoundError(f"RunFix task not found: {task_id}")
    return path


def _read_meta(task_dir: Path) -> dict[str, Any]:
    path = task_dir / "task.json"
    if not path.is_file():
        raise FileNotFoundError("Task metadata is missing.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Task metadata is unreadable or corrupted.") from exc
    if not isinstance(value, dict):
        raise ValueError("Task metadata has an invalid format.")
    return value


def _process_identity(pid: int) -> str | None:
    """Return an OS process-start identity to avoid acting on a reused PID."""
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", wintypes.DWORD),
                            ("dwHighDateTime", wintypes.DWORD)]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                creation = FILETIME()
                exit_time = FILETIME()
                kernel_time = FILETIME()
                user_time = FILETIME()
                if not kernel32.GetProcessTimes(
                    handle, ctypes.byref(creation), ctypes.byref(exit_time),
                    ctypes.byref(kernel_time), ctypes.byref(user_time)
                ):
                    return None
                value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
                return str(value)
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None
    stat = Path(f"/proc/{pid}/stat")
    try:
        content = stat.read_text(encoding="utf-8", errors="replace")
        fields = content[content.rfind(")") + 2:].split()
        return fields[19] if len(fields) > 19 else None
    except OSError:
        return None


def _process_alive(pid: int, expected_identity: str | None = None) -> bool | None:
    if pid <= 0:
        return False
    actual_identity = _process_identity(pid)
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not handle:
                error = ctypes.get_last_error()
                return False if error == 87 else None
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return None
                if exit_code.value != 259:  # not STILL_ACTIVE
                    return False
                if expected_identity and actual_identity is None:
                    return None
                if expected_identity and actual_identity != expected_identity:
                    return False
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except OSError:
        return None

    if expected_identity and actual_identity is None:
        return None
    if expected_identity and actual_identity != expected_identity:
        return False

    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        try:
            content = proc_stat.read_text(encoding="utf-8", errors="replace")
            fields = content[content.rfind(")") + 2:].split()
            if fields and fields[0] == "Z":
                return False
        except OSError:
            pass
    return True


def _git_value(project: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True,
            text=True,
            timeout=4,
            shell=False,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return redact(result.stdout.strip())[:500]
    except Exception:
        pass
    return None


def _manifest_fingerprints(project: Path) -> dict[str, str]:
    import hashlib

    names = (
        "environment.yml", "environment.yaml", "requirements.txt",
        "requirements-dev.txt", "pyproject.toml", "poetry.lock", "uv.lock",
        "Pipfile", "Pipfile.lock", "conda-lock.yml",
    )
    result: dict[str, str] = {}
    for name in names:
        path = project / name
        if path.is_file() and not path.is_symlink():
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                result[name] = digest
            except OSError:
                continue
    return result


class TaskManager:
    """Persistent, explicit-command background task manager for research runs."""

    def start(
        self,
        project_path: str | Path,
        command: list[str],
        python_executable: str | None = None,
        max_runtime_seconds: int = 86400,
        label: str = "",
    ) -> dict[str, Any]:
        project = Path(project_path).expanduser().resolve()
        if not project.is_dir():
            raise NotADirectoryError(str(project))
        if not isinstance(command, list) or not command or len(command) > 64:
            raise ValueError("command must be a non-empty list containing at most 64 arguments.")
        if any(not isinstance(item, str) or not item or len(item) > 8192 for item in command):
            raise ValueError("Every command argument must be a non-empty string of at most 8192 characters.")
        if sum(len(item) for item in command) > 32768:
            raise ValueError("The total command length exceeds 32768 characters.")

        max_runtime_seconds = max(60, min(int(max_runtime_seconds), 604800))
        args = list(command)
        if args[0].lower() in {"python", "python.exe", "py"}:
            args[0] = resolve_python(python_executable)
        elif python_executable and Path(args[0]).name.lower() in {"python", "python.exe"}:
            args[0] = resolve_python(python_executable)

        task_id = uuid.uuid4().hex
        base = _task_base(project)
        task_dir = base / task_id
        task_dir.mkdir(parents=False, exist_ok=False)
        log_path = task_dir / "execution.log"
        metadata_path = task_dir / "task.json"

        started_epoch = time.time()
        metadata: dict[str, Any] = {
            "task_id": task_id,
            "label": redact(label)[:200],
            "status": "starting",
            "created_at": _now(),
            "started_at": None,
            "completed_at": None,
            "pid": None,
            "returncode": None,
            "project_path": str(project),
            "working_directory": str(project),
            "command": [redact(item)[:1000] for item in args],
            "python_executable": args[0] if Path(args[0]).name.lower().startswith("python") else None,
            "max_runtime_seconds": max_runtime_seconds,
            "deadline_epoch": started_epoch + max_runtime_seconds,
            "git": {
                "commit": _git_value(project, "rev-parse", "HEAD"),
                "branch": _git_value(project, "branch", "--show-current"),
                "origin": _git_value(project, "remote", "get-url", "origin"),
            },
            "dependency_manifests_sha256": _manifest_fingerprints(project),
            "log_file": str(log_path),
            "supervised": True,
            "supervisor_python": sys.executable,
        }
        _atomic_json(metadata_path, metadata)

        creation_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            creation_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            creation_kwargs["start_new_session"] = True

        supervisor_path = task_dir / "supervisor.py"
        supervisor_path.write_text(SUPERVISOR_SOURCE, encoding="utf-8")
        supervisor_args = [sys.executable, str(supervisor_path), *args]

        try:
            with log_path.open("ab") as output:
                process = subprocess.Popen(
                    supervisor_args,
                    cwd=str(project),
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    close_fds=True,
                    **creation_kwargs,
                )
        except Exception as exc:
            metadata.update({
                "status": "failed_to_start",
                "completed_at": _now(),
                "start_error": redact(f"{type(exc).__name__}: {exc}"),
            })
            _atomic_json(metadata_path, metadata)
            raise

        metadata.update({
            "status": "running",
            "pid": process.pid,
            "process_identity": _process_identity(process.pid),
            "started_at": _now(),
            "experiment_fingerprint": self._fingerprint(metadata),
        })
        _atomic_json(metadata_path, metadata)
        with _LOCK:
            _PROCESSES[task_id] = process
        from .audit import audit as _audit
        _audit(project, "task.start", target=task_id, outcome="started",
               actor="runfix.tasks",
               details={"command": " ".join(args)[:400], "label": metadata.get("label"),
                        "pid": metadata.get("pid"),
                        "fingerprint": metadata.get("experiment_fingerprint")})
        return self._public(metadata)

    @staticmethod
    def _fingerprint(metadata: dict[str, Any]) -> str:
        import hashlib

        evidence = {
            "project_path": metadata.get("project_path"),
            "command": metadata.get("command"),
            "git": metadata.get("git"),
            "dependency_manifests_sha256": metadata.get("dependency_manifests_sha256"),
            "python_executable": metadata.get("python_executable"),
        }
        canonical = json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _public(metadata: dict[str, Any]) -> dict[str, Any]:
        result = dict(metadata)
        result.pop("log_file", None)
        result.pop("deadline_epoch", None)
        return result

    def status(self, project_path: str | Path, task_id: str) -> dict[str, Any]:
        task_dir = _task_dir(Path(project_path), task_id)
        metadata = _read_meta(task_dir)
        with _LOCK:
            process = _PROCESSES.get(task_id)

        if metadata.get("status") in {"running", "starting", "stop_requested"}:
            if time.time() > float(metadata.get("deadline_epoch", 0)):
                self.stop(project_path, task_id, reason="max_runtime_exceeded")
                metadata = _read_meta(task_dir)
            elif process is not None:
                returncode = process.poll()
                if returncode is not None:
                    metadata.update({
                        "status": "completed" if returncode == 0 else "failed",
                        "returncode": returncode,
                        "completed_at": _now(),
                        "exit_code_source": "live_process",
                    })
                    _atomic_json(task_dir / "task.json", metadata)
                    _redact_log_file(task_dir / "execution.log")
                    with _LOCK:
                        _PROCESSES.pop(task_id, None)
            else:
                alive = _process_alive(int(metadata.get("pid") or 0), metadata.get("process_identity"))
                recorded = _read_process_result(task_dir) if alive is False else None
                if alive is False and recorded and recorded.get("returncode") is not None:
                    returncode = int(recorded["returncode"])
                    metadata.update({
                        "status": "completed" if returncode == 0 else "failed",
                        "returncode": returncode,
                        "completed_at": recorded.get("finished_at") or _now(),
                        "exit_code_source": "supervisor_record_after_restart",
                        "note": "The server restarted while this task was running; the exit code was recovered from the supervisor record.",
                    })
                    _atomic_json(task_dir / "task.json", metadata)
                    _redact_log_file(task_dir / "execution.log")
                elif alive is False:
                    metadata.update({
                        "status": "ended_unobserved",
                        "completed_at": _now(),
                        "exit_code_source": None,
                        "note": "The server restarted while this task was running; its exit code is unavailable.",
                    })
                    _atomic_json(task_dir / "task.json", metadata)
                    _redact_log_file(task_dir / "execution.log")
                elif alive is None:
                    metadata["status"] = "status_unknown"
                    metadata["note"] = "The process state could not be determined after server restart."
                    _atomic_json(task_dir / "task.json", metadata)

        metadata = _read_meta(task_dir)
        log_path = task_dir / "execution.log"
        metadata["log_size_bytes"] = log_path.stat().st_size if log_path.exists() else 0
        return self._public(metadata)

    def stop(
        self,
        project_path: str | Path,
        task_id: str,
        reason: str = "user_requested",
    ) -> dict[str, Any]:
        task_dir = _task_dir(Path(project_path), task_id)
        metadata = _read_meta(task_dir)
        status = metadata.get("status")
        if status not in {"running", "starting", "stop_requested"}:
            return {"stopped": False, "reason": f"Task is not running (status={status}).",
                    "task": self._public(metadata)}

        pid = int(metadata.get("pid") or 0)
        with _LOCK:
            process = _PROCESSES.get(task_id)

        if process is not None:
            existing_returncode = process.poll()
            if existing_returncode is not None:
                metadata.update({
                    "status": "completed" if existing_returncode == 0 else "failed",
                    "returncode": existing_returncode,
                    "completed_at": metadata.get("completed_at") or _now(),
                })
                _atomic_json(task_dir / "task.json", metadata)
                _redact_log_file(task_dir / "execution.log")
                with _LOCK:
                    _PROCESSES.pop(task_id, None)
                return {"stopped": False, "reason": "Task had already finished.",
                        "task": self._public(metadata)}
        elif _process_alive(int(metadata.get("pid") or 0), metadata.get("process_identity")) is not True:
            alive = _process_alive(int(metadata.get("pid") or 0), metadata.get("process_identity"))
            metadata.update({
                "status": "ended_unobserved" if alive is False else "status_unknown",
                "completed_at": metadata.get("completed_at") or (_now() if alive is False else None),
                "note": (
                    "The process had already exited or its PID was reused; no process was terminated."
                    if alive is False else
                    "The process identity could not be verified after server restart; refusing to terminate an uncertain PID."
                ),
            })
            _atomic_json(task_dir / "task.json", metadata)
            _redact_log_file(task_dir / "execution.log")
            return {"stopped": False, "reason": "The original process could not be safely confirmed.",
                    "task": self._public(metadata)}

        metadata["status"] = "stop_requested"
        metadata["stop_reason"] = reason
        _atomic_json(task_dir / "task.json", metadata)

        terminated = False
        try:
            if os.name == "nt":
                if process is not None:
                    taskkill = subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True, text=True, timeout=15, shell=False,
                    )
                    terminated = taskkill.returncode == 0
                    if not terminated and process.poll() is None:
                        process.terminate()
                else:
                    taskkill = subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True, text=True, timeout=15, shell=False,
                    )
                    terminated = taskkill.returncode == 0
            else:
                os.killpg(pid, signal.SIGTERM)
                terminated = True
                if process is not None:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(pid, signal.SIGKILL)
                        process.wait(timeout=5)
            if process is not None and process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            terminated = terminated or (process is not None and process.poll() is not None)
        except ProcessLookupError:
            terminated = True
        except Exception as exc:
            metadata["stop_error"] = redact(f"{type(exc).__name__}: {exc}")

        if terminated:
            metadata.update({
                "status": "timed_out" if reason == "max_runtime_exceeded" else "stopped",
                "completed_at": _now(),
            })
            if process is not None and process.poll() is not None:
                metadata["returncode"] = process.returncode
            with _LOCK:
                _PROCESSES.pop(task_id, None)
        else:
            metadata["status"] = "stop_requested"
        _atomic_json(task_dir / "task.json", metadata)
        from .audit import audit as _audit
        _audit(Path(project_path), "task.stop", target=task_id,
               outcome=str(metadata.get("status")), actor="runfix.tasks",
               details={"reason": reason, "returncode": metadata.get("returncode")})
        return {"stopped": bool(terminated), "task": self._public(metadata)}

    def read_log(
        self,
        project_path: str | Path,
        task_id: str,
        tail_bytes: int = 16000,
    ) -> dict[str, Any]:
        task_dir = _task_dir(Path(project_path), task_id)
        metadata = _read_meta(task_dir)
        path = task_dir / "execution.log"
        tail_bytes = max(256, min(int(tail_bytes), 262144))
        if not path.is_file():
            return {"task_id": task_id, "log": "", "log_size_bytes": 0}
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - tail_bytes))
            content = stream.read().decode("utf-8", "replace")
        return {
            "task_id": task_id,
            "status": metadata.get("status"),
            "log": redact(content),
            "log_size_bytes": size,
            "truncated": size > tail_bytes,
        }

    def list_tasks(self, project_path: str | Path, limit: int = 20) -> dict[str, Any]:
        base = _task_base(Path(project_path))
        limit = max(1, min(int(limit), 100))
        items = []
        for task_dir in base.iterdir():
            if not task_dir.is_dir() or not _TASK_ID.fullmatch(task_dir.name):
                continue
            try:
                item = self.status(project_path, task_dir.name)
                items.append(item)
            except (OSError, ValueError):
                continue
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return {"project_path": str(Path(project_path).resolve()),
                "count": len(items[:limit]), "tasks": items[:limit]}
