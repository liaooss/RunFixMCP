from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .common import redact

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

ALLOWED = {
    ".py", ".pyi", ".toml", ".txt", ".yaml", ".yml", ".json",
    ".ini", ".cfg", ".md", ".rst", ".sh", ".ps1",
}
TRANSACTION_ID_LENGTH = 32


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def patch_paths(text: str) -> list[str]:
    found = []
    for line in text.splitlines():
        if line.startswith(("+++ ", "--- ")):
            raw = line[4:].strip().split("\t", 1)[0]
            if raw == "/dev/null":
                continue
            if raw.startswith(("a/", "b/")):
                raw = raw[2:]
            found.append(raw)
    return sorted(set(found))


def _normalized_path(root: Path, raw: str) -> tuple[Path, str]:
    normalized = raw.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
        or relative.parts[0].endswith(":")
    ):
        raise ValueError(f"Unsafe patch path: {raw}")
    if relative.parts[0].lower() in {".git", ".runfix"}:
        raise ValueError("Patches may not modify .git or RunFix metadata.")
    if Path(normalized).suffix.lower() not in ALLOWED:
        raise ValueError(f"File extension is not allowed: {raw}")

    destination = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PermissionError(f"Patch paths may not traverse symlinks: {raw}")
    resolved = destination.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"Patch escapes project root: {raw}") from exc
    return resolved, relative.as_posix()


def _validate_ast_in_temporary_tree(root: Path, text: str, paths: list[str]) -> dict[str, Any]:
    python_paths = [p for p in paths if Path(p).suffix.lower() in {".py", ".pyi"}]
    if not python_paths:
        return {"checked": False, "status": "not_applicable", "files": []}

    details = []
    with tempfile.TemporaryDirectory(prefix="runfix-ast-") as temporary:
        temp_root = Path(temporary)
        for raw in paths:
            source, normalized = _normalized_path(root, raw)
            target = temp_root.joinpath(*PurePosixPath(normalized).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_file():
                shutil.copy2(source, target)

        try:
            result = subprocess.run(
                ["git", "apply", "--recount", "-"],
                cwd=temp_root,
                input=text,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "checked": True,
                "status": "error",
                "files": [],
                "reason": redact(f"{type(exc).__name__}: {exc}"),
            }
        if result.returncode != 0:
            return {
                "checked": True,
                "status": "error",
                "files": [],
                "reason": "The patch could not be applied to the isolated AST validation tree.",
                "details": redact((result.stderr or result.stdout)[-2000:]),
            }

        for raw in python_paths:
            normalized = PurePosixPath(raw.replace("\\", "/")).as_posix()
            candidate = temp_root.joinpath(*PurePosixPath(normalized).parts)
            if not candidate.exists():
                details.append({"path": normalized, "status": "deleted"})
                continue
            try:
                source_text = candidate.read_text(encoding="utf-8")
                ast.parse(source_text, filename=normalized, type_comments=True)
                details.append({"path": normalized, "status": "passed"})
            except UnicodeDecodeError:
                details.append({
                    "path": normalized,
                    "status": "failed",
                    "error": "Python source is not valid UTF-8.",
                })
            except SyntaxError as exc:
                details.append({
                    "path": normalized,
                    "status": "failed",
                    "line": exc.lineno,
                    "offset": exc.offset,
                    "error": redact(exc.msg),
                })
    failed = [item for item in details if item["status"] == "failed"]
    return {
        "checked": True,
        "status": "failed" if failed else "passed",
        "files": details,
    }


def validate_patch(project_path: str | Path, text: str) -> dict[str, Any]:
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    if not isinstance(text, str) or not text.strip():
        return {"valid": False, "reason": "Patch is empty.", "paths": []}
    if len(text) > 1_000_000:
        return {"valid": False, "reason": "Patch exceeds 1 MB.", "paths": []}
    if "GIT binary patch" in text or "Binary files " in text:
        return {"valid": False, "reason": "Binary patches are not accepted.", "paths": []}
    if any(
        line.strip() in {"new file mode 120000", "old mode 120000",
                         "new mode 120000", "new file mode 160000",
                         "old mode 160000", "new mode 160000"}
        for line in text.splitlines()
    ):
        return {"valid": False, "reason": "Symlink and submodule patches are not accepted.", "paths": []}

    paths = patch_paths(text)
    if not paths:
        return {"valid": False, "reason": "No unified-diff paths found.", "paths": []}

    try:
        for raw in paths:
            _normalized_path(root, raw)
    except (ValueError, PermissionError) as exc:
        return {"valid": False, "reason": str(exc), "paths": paths}

    try:
        check = subprocess.run(
            ["git", "apply", "--check", "--recount", "-"],
            cwd=root,
            input=text,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            shell=False,
        )
    except FileNotFoundError:
        return {
            "valid": False,
            "reason": "Git is required to validate/apply unified diffs.",
            "paths": paths,
        }
    except subprocess.TimeoutExpired:
        return {"valid": False, "reason": "Patch validation timed out.", "paths": paths}

    if check.returncode != 0:
        return {
            "valid": False,
            "reason": "git apply --check rejected the patch.",
            "details": redact((check.stderr or check.stdout)[-2500:]),
            "paths": paths,
        }

    ast_check = _validate_ast_in_temporary_tree(root, text, paths)
    if ast_check.get("status") in {"failed", "error"}:
        return {
            "valid": False,
            "reason": "Patch failed isolated Python AST validation.",
            "paths": paths,
            "ast_validation": ast_check,
        }
    return {
        "valid": True,
        "reason": "Patch passed path, git apply and isolated Python AST checks.",
        "paths": paths,
        "ast_validation": ast_check,
    }


def _transaction_root(root: Path) -> Path:
    state = root / ".runfix"
    if state.is_symlink():
        raise PermissionError("The project .runfix directory must not be a symlink.")
    state.mkdir(parents=True, exist_ok=True)
    resolved_state = state.resolve()
    try:
        resolved_state.relative_to(root)
    except ValueError as exc:
        raise PermissionError("RunFix state directory escapes the project root.") from exc

    base = state / "patch_transactions"
    if base.is_symlink():
        raise PermissionError("The patch transaction directory must not be a symlink.")
    base.mkdir(parents=True, exist_ok=True)
    resolved = base.resolve()
    try:
        resolved.relative_to(resolved_state)
    except ValueError as exc:
        raise PermissionError("Patch transaction storage escapes RunFix state.") from exc
    return resolved


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


LOCK_TIMEOUT_SECONDS = 30.0
TRANSACTION_RETENTION = 200
_THREAD_LOCK = threading.RLock()


def _acquire_lock(handle, timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    if msvcrt is not None:  # Windows
        handle.seek(0)
        if handle.read(1) == b"":
            handle.write(b"\0")
            handle.flush()
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        "Timed out waiting for the RunFix patch transaction lock."
                    ) from None
                time.sleep(0.05)
    elif fcntl is not None:  # POSIX
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        "Timed out waiting for the RunFix patch transaction lock."
                    ) from None
                time.sleep(0.05)


def _release_lock(handle) -> None:
    try:
        if msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def _transaction_lock(root: Path):
    """Serialise patch transactions for one project across threads and processes.

    Windows byte-range locks are held by the process, so sibling threads would not
    exclude each other; the module lock closes that gap while the file lock covers
    separate RunFix processes (MCP server plus CLI, for example).
    """
    with _THREAD_LOCK:
        base = _transaction_root(root)
        handle = (base / ".lock").open("a+b")
        try:
            _acquire_lock(handle)
            try:
                yield base
            finally:
                _release_lock(handle)
        finally:
            handle.close()


def _prune_transactions(base: Path, keep: int = TRANSACTION_RETENTION) -> list[str]:
    """Remove only finished, no-longer-rollbackable transactions.

    Applied transactions are kept, because deleting one would destroy the ability to
    roll the patch back. Only states that can no longer be rolled back are pruned, and
    only once the directory holds more than ``keep`` transactions.
    """
    prunable_status = {"rolled_back", "apply_failed"}
    directories = [item for item in base.iterdir() if item.is_dir()]
    if len(directories) <= keep:
        return []
    pruned: list[str] = []
    ordered = sorted(directories, key=lambda item: item.stat().st_mtime)
    for directory in ordered[: max(0, len(directories) - keep)]:
        metadata_path = directory / "transaction.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if metadata.get("status") not in prunable_status:
            continue
        try:
            shutil.rmtree(directory)
            pruned.append(directory.name)
        except OSError:
            continue
    return pruned


def _recover_interrupted_locked(root: Path) -> dict[str, Any]:
    """Resolve transactions left in the ``applying`` state by a crash or a kill.

    The caller must already hold the project transaction lock. A transaction that
    still holds the pre-patch hash never modified the file; one that holds the
    post-patch hash did apply and is restored from its backups; anything else changed
    the file after the crash, so it is reported for review and left untouched.
    """
    base = _transaction_root(root)
    recovered: list[dict[str, Any]] = []
    needs_review: list[dict[str, Any]] = []
    for directory in sorted(base.iterdir()):
        if not directory.is_dir():
            continue
        metadata_path = directory / "transaction.json"
        if not metadata_path.is_file():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if metadata.get("status") != "applying":
            continue

        to_restore: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        for item in metadata.get("files", []):
            try:
                target, normalized = _normalized_path(root, item["path"])
                current = _sha256(target)
            except (ValueError, PermissionError, OSError) as exc:
                conflicts.append({"path": str(item.get("path", "")),
                                  "reason": redact(f"{type(exc).__name__}: {exc}")})
                continue
            if current == item.get("sha256_after"):
                to_restore.append(item)
            elif current == item.get("sha256_before"):
                continue
            else:
                conflicts.append({"path": normalized,
                                  "expected_sha256": item.get("sha256_after"),
                                  "actual_sha256": current})

        if conflicts:
            metadata.update({
                "status": "interrupted_needs_review",
                "recovered_at": _now(),
                "conflicts": conflicts,
            })
            _write_json_atomic(metadata_path, metadata)
            needs_review.append({"transaction_id": directory.name, "conflicts": conflicts})
            continue

        _restore_original_files(root, directory, to_restore)
        metadata.update({
            "status": "recovered_from_interrupt",
            "recovered_at": _now(),
            "restored_files": [item["path"] for item in to_restore],
        })
        _write_json_atomic(metadata_path, metadata)
        recovered.append({"transaction_id": directory.name,
                          "restored_files": [item["path"] for item in to_restore]})

    pruned = _prune_transactions(base)
    return {"recovered": recovered, "needs_review": needs_review, "pruned": pruned}


def recover_interrupted_transactions(project_path: str | Path) -> dict[str, Any]:
    """Recover patch transactions that a previous process left half-applied."""
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    with _transaction_lock(root):
        return _recover_interrupted_locked(root)


def _read_transaction(root: Path, transaction_id: str) -> tuple[Path, dict[str, Any]]:
    if (
        not isinstance(transaction_id, str)
        or len(transaction_id) != TRANSACTION_ID_LENGTH
        or any(ch not in "0123456789abcdef" for ch in transaction_id)
    ):
        raise ValueError("transaction_id must be a 32-character lowercase hexadecimal identifier.")
    base = _transaction_root(root)
    candidate = base / transaction_id
    if candidate.is_symlink():
        raise PermissionError("Patch transaction directories may not be symlinks.")
    directory = candidate.resolve()
    try:
        directory.relative_to(base)
    except ValueError as exc:
        raise PermissionError("Transaction path escapes RunFix metadata.") from exc
    metadata_path = directory / "transaction.json"
    if metadata_path.is_symlink():
        raise PermissionError("Patch transaction metadata may not be a symlink.")
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Patch transaction not found: {transaction_id}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Patch transaction metadata is unreadable.") from exc
    if not isinstance(metadata, dict) or metadata.get("transaction_id") != transaction_id:
        raise ValueError("Patch transaction metadata is invalid.")
    return directory, metadata


def apply_patch(
    project_path: str | Path,
    text: str,
    confirmed: bool = False,
) -> dict[str, Any]:
    if not confirmed:
        return {"applied": False, "reason": "Explicit confirmation is required."}

    root = Path(project_path).expanduser().resolve()
    check = validate_patch(root, text)
    if not check.get("valid"):
        return {"applied": False, "validation": check}

    with _transaction_lock(root):
        recovery = _recover_interrupted_locked(root)
        result = _apply_patch_transaction(root, check, text)
        if recovery["recovered"] or recovery["needs_review"]:
            result["recovery"] = recovery
        return result


def _apply_patch_transaction(root: Path, check: dict[str, Any], text: str) -> dict[str, Any]:
    transaction_id = uuid.uuid4().hex
    transaction_dir = _transaction_root(root) / transaction_id
    backup_dir = transaction_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=False)

    files = []
    for raw in check["paths"]:
        source, normalized = _normalized_path(root, raw)
        existed = source.is_file()
        backup_relative = None
        before_hash = _sha256(source) if existed else None
        if existed:
            backup_relative = str(Path("backups") / Path(*PurePosixPath(normalized).parts))
            backup_path = transaction_dir / backup_relative
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, backup_path)
        files.append({
            "path": normalized,
            "existed_before": existed,
            "backup_path": backup_relative,
            "sha256_before": before_hash,
            "sha256_after": None,
        })

    metadata: dict[str, Any] = {
        "transaction_id": transaction_id,
        "status": "applying",
        "created_at": _now(),
        "project_path": str(root),
        "files": files,
    }
    _write_json_atomic(transaction_dir / "transaction.json", metadata)

    try:
        result = subprocess.run(
            ["git", "apply", "--recount", "-"],
            cwd=root,
            input=text,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
        )
    except Exception as exc:
        metadata.update({
            "status": "apply_failed",
            "completed_at": _now(),
            "error": redact(f"{type(exc).__name__}: {exc}"),
        })
        _write_json_atomic(transaction_dir / "transaction.json", metadata)
        return {"applied": False, "transaction_id": transaction_id, "reason": metadata["error"]}

    if result.returncode != 0:
        # Restore snapshots even if git reports a failed application.
        _restore_original_files(root, transaction_dir, metadata["files"])
        metadata.update({
            "status": "apply_failed",
            "completed_at": _now(),
            "returncode": result.returncode,
            "stderr": redact(result.stderr[-2000:]),
        })
        _write_json_atomic(transaction_dir / "transaction.json", metadata)
        return {
            "applied": False,
            "transaction_id": transaction_id,
            "returncode": result.returncode,
            "stderr": redact(result.stderr[-2000:]),
        }

    for item in metadata["files"]:
        target = root.joinpath(*PurePosixPath(item["path"]).parts)
        item["sha256_after"] = _sha256(target)
    metadata.update({
        "status": "applied",
        "applied_at": _now(),
    })
    _write_json_atomic(transaction_dir / "transaction.json", metadata)
    return {
        "applied": True,
        "transaction_id": transaction_id,
        "files": [item["path"] for item in files],
        "backup_count": sum(item["existed_before"] for item in files),
        "validation": check,
    }


def _restore_original_files(root: Path, transaction_dir: Path, files: list[dict[str, Any]]) -> None:
    # Preflight the entire restore plan before changing the first project file.
    plan = []
    transaction_root = transaction_dir.resolve()
    for item in files:
        target, _ = _normalized_path(root, item["path"])
        backup = None
        if item["existed_before"]:
            backup_relative = PurePosixPath(str(item.get("backup_path") or ""))
            if backup_relative.is_absolute() or ".." in backup_relative.parts or not backup_relative.parts:
                raise PermissionError("Patch backup path is unsafe.")
            backup = transaction_dir.joinpath(*backup_relative.parts).resolve()
            try:
                backup.relative_to(transaction_root)
            except ValueError as exc:
                raise PermissionError("Patch backup path escapes its transaction.") from exc
            if backup.is_symlink() or not backup.is_file():
                raise FileNotFoundError(f"Patch backup is missing or unsafe: {item['path']}")
        elif target.exists() and target.is_dir() and not target.is_symlink():
            raise IsADirectoryError(str(target))
        plan.append((target, backup, bool(item["existed_before"])))

    for target, backup, existed_before in plan:
        if existed_before:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=".runfix-restore-", suffix=".tmp", dir=str(target.parent)
            )
            os.close(fd)
            temporary = Path(temporary_name)
            try:
                shutil.copy2(backup, temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        elif target.exists() or target.is_symlink():
            target.unlink()


def rollback_patch(
    project_path: str | Path,
    transaction_id: str,
    confirmed: bool = False,
) -> dict[str, Any]:
    if not confirmed:
        return {"rolled_back": False, "reason": "Explicit confirmation is required."}
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    with _transaction_lock(root):
        recovery = _recover_interrupted_locked(root)
        result = _rollback_patch_locked(root, transaction_id)
        if recovery["recovered"] or recovery["needs_review"]:
            result["recovery"] = recovery
        return result


def _rollback_patch_locked(root: Path, transaction_id: str) -> dict[str, Any]:
    transaction_dir, metadata = _read_transaction(root, transaction_id)

    if metadata.get("status") == "rolled_back":
        return {
            "rolled_back": True,
            "already_rolled_back": True,
            "transaction_id": transaction_id,
            "files": [item["path"] for item in metadata.get("files", [])],
        }
    if metadata.get("status") != "applied":
        return {
            "rolled_back": False,
            "transaction_id": transaction_id,
            "reason": f"Only applied transactions can be rolled back (status={metadata.get('status')}).",
        }

    conflicts = []
    for item in metadata.get("files", []):
        try:
            target, normalized = _normalized_path(root, item["path"])
            current_hash = _sha256(target)
            if current_hash != item.get("sha256_after"):
                conflicts.append({
                    "path": normalized,
                    "expected_sha256": item.get("sha256_after"),
                    "actual_sha256": current_hash,
                })
        except (ValueError, PermissionError, OSError) as exc:
            conflicts.append({
                "path": str(item.get("path", "")),
                "reason": redact(f"{type(exc).__name__}: {exc}"),
            })
    if conflicts:
        return {
            "rolled_back": False,
            "transaction_id": transaction_id,
            "reason": "Rollback refused because one or more files changed after the patch was applied.",
            "conflicts": conflicts,
        }

    _restore_original_files(root, transaction_dir, metadata.get("files", []))
    metadata.update({
        "status": "rolled_back",
        "rolled_back_at": _now(),
    })
    _write_json_atomic(transaction_dir / "transaction.json", metadata)
    return {
        "rolled_back": True,
        "transaction_id": transaction_id,
        "files": [item["path"] for item in metadata.get("files", [])],
    }


def get_patch_transaction(project_path: str | Path, transaction_id: str) -> dict[str, Any]:
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    _, metadata = _read_transaction(root, transaction_id)
    return metadata
