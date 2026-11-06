"""Durable checkpoints so a long agent run can resume after a crash or a restart.

A checkpoint is written after every completed step, before the next decision. Resuming loads
the observations, the tool box state and the event history, so the agent continues from the
next step instead of replaying side effects such as patch applications.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CHECKPOINT_DIR = ("checkpoints",)
MAX_CHECKPOINTS = 50


def _root(project: Path) -> Path:
    state = project / ".runfix"
    if state.is_symlink():
        raise PermissionError("The project .runfix directory must not be a symlink.")
    state.mkdir(parents=True, exist_ok=True)
    base = state / "checkpoints"
    if base.is_symlink():
        raise PermissionError("The .runfix/checkpoints directory must not be a symlink.")
    base.mkdir(parents=True, exist_ok=True)
    resolved = base.resolve()
    try:
        resolved.relative_to(state.resolve())
    except ValueError as exc:
        raise PermissionError("Checkpoint storage escapes the RunFix state directory.") from exc
    return resolved


def _validate_run_id(run_id: str) -> str:
    """Run ids become directory names, so allow only a conservative filename alphabet."""
    if not isinstance(run_id, str) or not run_id or len(run_id) > 64:
        raise ValueError("run_id must be a non-empty string of at most 64 characters.")
    if not all(ch.isalnum() or ch in "-_" for ch in run_id):
        raise ValueError("run_id may only contain letters, digits, '-' and '_'.")
    return run_id


def save_checkpoint(project_path: str | Path, run_id: str, payload: dict[str, Any]) -> Path:
    project = Path(project_path).expanduser().resolve()
    if not project.is_dir():
        raise NotADirectoryError(str(project))
    run_id = _validate_run_id(run_id)
    base = _root(project)
    directory = base / run_id
    directory.mkdir(parents=True, exist_ok=True)
    record = dict(payload)
    record["run_id"] = run_id
    record["updated_at"] = datetime.now(timezone.utc).isoformat()

    path = directory / "checkpoint.json"
    temporary = directory / "checkpoint.json.tmp"
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)
    _prune(base)
    return path


def load_checkpoint(project_path: str | Path, run_id: str) -> dict[str, Any] | None:
    project = Path(project_path).expanduser().resolve()
    if not project.is_dir():
        raise NotADirectoryError(str(project))
    run_id = _validate_run_id(run_id)
    path = _root(project) / run_id / "checkpoint.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Checkpoint {run_id} is unreadable: {exc}") from exc
    return value if isinstance(value, dict) else None


def list_checkpoints(project_path: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    project = Path(project_path).expanduser().resolve()
    if not project.is_dir():
        raise NotADirectoryError(str(project))
    base = _root(project)
    items: list[dict[str, Any]] = []
    for directory in base.iterdir():
        if not directory.is_dir():
            continue
        path = directory / "checkpoint.json"
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items.append({
            "run_id": directory.name,
            "status": record.get("status"),
            "step": record.get("step"),
            "goal": record.get("goal"),
            "updated_at": record.get("updated_at"),
            "outcome": (record.get("report") or {}).get("outcome"),
            "stop_reason": (record.get("report") or {}).get("stop_reason"),
        })
    items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return items[: max(1, min(int(limit), 200))]


def _prune(base: Path, keep: int = MAX_CHECKPOINTS) -> None:
    directories = [item for item in base.iterdir() if item.is_dir()]
    if len(directories) <= keep:
        return
    directories.sort(key=lambda item: item.stat().st_mtime)
    for directory in directories[: len(directories) - keep]:
        # Keep anything not finished, so an in-flight run is never pruned.
        try:
            record = json.loads((directory / "checkpoint.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("status") != "finished":
            continue
        for child in directory.iterdir():
            try:
                child.unlink()
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass
