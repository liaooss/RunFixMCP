"""Execute one reproduction attempt and collect its evidence.

An attempt is only "completed" when the process exits successfully *and* the output
contains positive evidence that the experiment reached a terminal state (a metric
series, a completion marker, or the caller's own completion regex). A zero exit code
on its own is reported as ``process_exited`` and explicitly not as a reproduction.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..common import redact, resolve_python, stamp
from ..diagnostics import diagnose_environment
from .metrics import extract_metrics

COMPLETION_MARKERS = (
    "training finished",
    "training complete",
    "training completed",
    "finished training",
    "all epochs",
    "final results",
    "final test",
    "best model saved",
    "saved checkpoint",
    "evaluation results",
    "test results",
    "experiment finished",
    "reproduction complete",
)

IGNORED_DIRS = {".git", ".runfix", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str | None:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                text=True, timeout=6, shell=False,
                                encoding="utf-8", errors="replace")
        if result.returncode == 0:
            return redact(result.stdout.strip())[:500]
    except Exception:
        pass
    return None


def _git_is_dirty(root: Path) -> bool | None:
    """Whether the repository has changes that predate this run.

    RunFix's own ``.runfix`` state directory and Python bytecode caches are excluded, so a
    clean checkout does not look dirty merely because RunFix wrote its evidence.
    """
    try:
        result = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                                capture_output=True, text=True, timeout=8, shell=False,
                                encoding="utf-8", errors="replace")
    except Exception:
        return None
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        path = line[3:].strip().strip('"').replace("\\", "/")
        if not path:
            continue
        if path == ".runfix" or path.startswith(".runfix/"):
            continue
        if "__pycache__" in path or path.endswith(".pyc"):
            continue
        return True
    return False


def _manifest_hashes(root: Path) -> dict[str, str]:
    names = ("requirements.txt", "environment.yml", "environment.yaml", "pyproject.toml",
             "poetry.lock", "uv.lock", "setup.py", "setup.cfg")
    hashes: dict[str, str] = {}
    for name in names:
        digest = _sha256(root / name)
        if digest:
            hashes[name] = digest
    return hashes


def _hardware(python: str) -> dict[str, Any]:
    probe = diagnose_environment(python, use_env_doctor=False)
    torch = probe.get("torch") or {}
    nvidia = probe.get("nvidia_smi") or {}
    summary = nvidia.get("summary") or ""
    devices = []
    for line in summary.splitlines():
        if "," in line and "MiB" in line:
            devices.append(line.strip())
    return {
        "python_executable": probe.get("python_executable"),
        "python_version": probe.get("python_version"),
        "platform": probe.get("platform"),
        "packages": probe.get("packages"),
        "torch_version": torch.get("version"),
        "torch_cuda": torch.get("cuda_version"),
        "cuda_available": torch.get("cuda_available"),
        "gpu_count": torch.get("gpu_count"),
        "gpu_names": torch.get("gpu_names"),
        "nvidia_smi_available": bool(nvidia.get("available")),
        "nvidia_devices": devices[:8],
    }


def _completion_evidence(log_text: str, metrics: dict[str, Any],
                         completion_regex: str | None) -> dict[str, Any]:
    lowered = (log_text or "").lower()
    matched_markers = [marker for marker in COMPLETION_MARKERS if marker in lowered]
    regex_hit = None
    regex_error = None
    if completion_regex:
        try:
            match = re.search(completion_regex, log_text or "", re.IGNORECASE | re.MULTILINE)
            regex_hit = match.group(0)[:300] if match else None
        except re.error as exc:
            regex_error = str(exc)
    return {
        "metrics_present": metrics["point_count"] > 0,
        "metric_series_count": len(metrics["metric_keys"]),
        "markers": matched_markers[:10],
        "completion_regex_match": regex_hit,
        "completion_regex_error": regex_error,
        "has_positive_evidence": bool(matched_markers) or metrics["point_count"] > 0 or bool(regex_hit),
    }


def _artifacts(root: Path, started_at: float, run_dir: Path, limit: int = 200) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
        for name in files:
            path = Path(base) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < started_at:
                continue
            if path.is_symlink():
                continue
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if relative.startswith(".runfix/"):
                continue
            found.append({"path": relative, "size_bytes": stat.st_size,
                          "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
            if len(found) >= limit:
                break
        if len(found) >= limit:
            break
    found.sort(key=lambda item: item["modified_at"], reverse=True)
    return found


def _prepare_run_dir(root: Path) -> Path:
    run_dir = root / ".runfix" / "repro" / (stamp() + "-" + os.urandom(3).hex())
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def run_reproduction(project_path: str | Path,
                     command: list[str] | None = None,
                     python_executable: str | None = None,
                     timeout: int = 1800,
                     expected_metrics: dict[str, float] | None = None,
                     completion_regex: str | None = None,
                     log_tail_bytes: int = 200_000) -> dict[str, Any]:
    """Run one explicit command and record everything needed to judge it."""
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    if not command:
        raise ValueError("An explicit command is required; RunFix never invents a training invocation.")
    if not isinstance(command, list) or any(not isinstance(item, str) or not item for item in command):
        raise ValueError("command must be a non-empty list of non-empty strings.")
    if len(command) > 64:
        raise ValueError("command may contain at most 64 arguments.")

    timeout = max(30, min(int(timeout), 24 * 3600))
    python = resolve_python(python_executable)
    argv = list(command)
    if Path(argv[0]).name.lower() in {"python", "python.exe", "py"}:
        argv[0] = python

    run_dir = _prepare_run_dir(root)
    log_path = run_dir / "run.log"
    metadata_path = run_dir / "run.json"

    started_at = time.time()
    record: dict[str, Any] = {
        "run_id": run_dir.name,
        "project_path": str(root),
        "command": [redact(item)[:1000] for item in argv],
        "started_at": _now(),
        "started_epoch": started_at,
        "python_executable": python,
        "timeout_seconds": timeout,
        "fingerprint": {
            "git": {
                "commit": _git(root, "rev-parse", "HEAD"),
                "branch": _git(root, "branch", "--show-current"),
                "origin": _git(root, "remote", "get-url", "origin"),
                "dirty": _git_is_dirty(root),
            },
            "dependency_manifests": _manifest_hashes(root),
        },
        "hardware": _hardware(python),
        "status": "running",
    }
    metadata_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    timed_out = False
    returncode: int | None = None
    error = None
    with log_path.open("wb") as log_handle:
        try:
            process = subprocess.Popen(
                argv, cwd=str(root), stdin=subprocess.DEVNULL,
                stdout=log_handle, stderr=subprocess.STDOUT, shell=False, close_fds=True,
            )
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                try:
                    returncode = process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    returncode = None
        except OSError as exc:
            error = redact(f"{type(exc).__name__}: {exc}")

    finished_at = time.time()
    raw_log = ""
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - log_tail_bytes))
            raw_log = handle.read().decode("utf-8", "replace")
        log_size = size
    except OSError:
        log_size = 0

    log_text = redact(raw_log)
    log_path.write_text(log_text, encoding="utf-8", errors="replace")

    metrics = extract_metrics(log_text)
    evidence = _completion_evidence(log_text, metrics, completion_regex)
    artifacts = _artifacts(root, started_at, run_dir)

    if error:
        status = "failed_to_start"
    elif timed_out:
        status = "timed_out"
    elif returncode != 0:
        status = "failed"
    elif evidence["has_positive_evidence"]:
        status = "completed"
    else:
        status = "process_exited"

    comparison = None
    if expected_metrics:
        from .metrics import compare_metrics
        comparison = compare_metrics(metrics["final"], expected_metrics)

    record.update({
        "status": status,
        "finished_at": _now(),
        "duration_seconds": round(finished_at - started_at, 3),
        "returncode": returncode,
        "timed_out": timed_out,
        "start_error": error,
        "log": {"path": str(log_path.relative_to(root)), "size_bytes": log_size},
        "metrics": metrics,
        "completion_evidence": evidence,
        "metric_comparison": comparison,
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
        "artifact_total_bytes": sum(item["size_bytes"] for item in artifacts),
    })
    metadata_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return record


def latest_reproduction_runs(project_path: str | Path, limit: int = 10) -> list[dict[str, Any]]:
    root = Path(project_path).expanduser().resolve()
    base = root / ".runfix" / "repro"
    if not base.is_dir():
        return []
    records = []
    for directory in base.iterdir():
        if not directory.is_dir():
            continue
        metadata = directory / "run.json"
        if not metadata.is_file():
            continue
        try:
            records.append(json.loads(metadata.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    records.sort(key=lambda item: item.get("started_at", ""), reverse=True)
    return records[: max(1, min(int(limit), 100))]
