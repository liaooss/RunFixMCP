"""Tamper-evident audit trail for every privileged RunFix action.

Each entry is chained to the previous one by SHA-256, so removing or editing an entry
breaks verification rather than going unnoticed. Entries rotate by month; the chain
continues across files because every entry stores the previous hash.

Auditing must never destroy or block real work: a failure to write is reported on stderr and
returned to the caller, and the operation still proceeds. The failure stays visible instead of
being swallowed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .common import redact

AUDIT_DIR = ("audit",)
AUDIT_SCHEMA_VERSION = 1
MAX_DETAIL_CHARS = 4000
_LOCK = threading.RLock()

PRIVILEGED_ACTIONS = (
    "patch.apply",
    "patch.rollback",
    "patch.recover",
    "environment.build",
    "task.start",
    "task.stop",
    "agent.run",
    "reproduce.run",
    "approval.grant",
    "approval.revoke",
)


def _audit_root(project: Path) -> Path:
    state = project / ".runfix"
    if state.is_symlink():
        raise PermissionError("The project .runfix directory must not be a symlink.")
    state.mkdir(parents=True, exist_ok=True)
    base = state / "audit"
    if base.is_symlink():
        raise PermissionError("The .runfix/audit directory must not be a symlink.")
    base.mkdir(parents=True, exist_ok=True)
    resolved = base.resolve()
    try:
        resolved.relative_to(state.resolve())
    except ValueError as exc:
        raise PermissionError("Audit storage escapes the RunFix state directory.") from exc
    return resolved


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _tail_entry(path: Path) -> dict[str, Any] | None:
    """Read the last complete JSON line without loading the whole file."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            block = min(size, 16384)
            handle.seek(size - block)
            data = handle.read()
    except OSError:
        return None
    for raw in reversed(data.splitlines()):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _audit_files(root: Path) -> list[Path]:
    base = root / ".runfix" / "audit"
    if not base.is_dir():
        return []
    return sorted(
        (item for item in base.glob("audit-*.jsonl") if item.is_file()),
        key=lambda item: item.name,
    )


def _last_state(root: Path) -> tuple[int, str]:
    files = _audit_files(root)
    if not files:
        return 0, "0" * 64
    tail = _tail_entry(files[-1])
    if not tail:
        return 0, "0" * 64
    return int(tail.get("seq", 0)), str(tail.get("hash", "0" * 64))


def audit(project_path: str | Path,
          action: str,
          target: str = "",
          outcome: str = "ok",
          actor: str = "cli",
          details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Append one entry to the project's audit trail.

    Returns ``{"recorded": bool, "entry": {...}}``. ``recorded`` is False when the write
    failed; the reason is then in ``error`` and was printed to stderr.
    """
    project = Path(project_path).expanduser().resolve()
    if not project.is_dir():
        raise NotADirectoryError(str(project))

    now = datetime.now(timezone.utc)
    safe_details = {}
    for key, value in (details or {}).items():
        text = redact(str(value))
        if len(text) > MAX_DETAIL_CHARS:
            text = text[:MAX_DETAIL_CHARS] + "…[truncated]"
        safe_details[str(key)[:80]] = text

    try:
        with _LOCK:
            base = _audit_root(project)
            seq, prev_hash = _last_state(project)
            entry: dict[str, Any] = {
                "schema": AUDIT_SCHEMA_VERSION,
                "seq": seq + 1,
                "ts": now.isoformat(),
                "actor": redact(str(actor))[:80],
                "action": redact(str(action))[:120],
                "target": redact(str(target))[:400],
                "outcome": redact(str(outcome))[:40],
                "details": safe_details,
                "prev_hash": prev_hash,
            }
            entry["hash"] = _digest(entry)
            path = base / f"audit-{now.strftime('%Y-%m')}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return {"recorded": True, "entry": entry}
    except Exception as exc:  # auditing must never block the operation
        message = f"RunFix audit warning: could not record {action}: {type(exc).__name__}: {exc}"
        print(message, file=sys.stderr)
        return {"recorded": False, "error": message}


def read_audit(project_path: str | Path, limit: int = 200,
               action: str | None = None) -> list[dict[str, Any]]:
    """Return the most recent entries (newest last), optionally filtered by action."""
    project = Path(project_path).expanduser().resolve()
    if not project.is_dir():
        raise NotADirectoryError(str(project))
    limit = max(1, min(int(limit), 5000))
    entries: list[dict[str, Any]] = []
    for path in _audit_files(project):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(value, dict) and (action is None or value.get("action") == action):
                        entries.append(value)
        except OSError:
            continue
    return entries[-limit:]


def verify_chain(project_path: str | Path) -> dict[str, Any]:
    """Recompute the hash chain and report the first broken entry, if any."""
    project = Path(project_path).expanduser().resolve()
    if not project.is_dir():
        raise NotADirectoryError(str(project))

    expected_prev = "0" * 64
    expected_seq = 1
    checked = 0
    files = _audit_files(project)
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return {"ok": False, "checked": checked, "file": path.name,
                    "reason": f"unreadable: {type(exc).__name__}: {exc}"}
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                return {"ok": False, "checked": checked, "file": path.name,
                        "reason": "unparsable line"}
            if not isinstance(entry, dict):
                return {"ok": False, "checked": checked, "file": path.name,
                        "reason": "entry is not an object"}
            recorded_hash = entry.pop("hash", None)
            recomputed = _digest(entry)
            entry["hash"] = recorded_hash
            if recorded_hash != recomputed:
                return {"ok": False, "checked": checked, "file": path.name,
                        "seq": entry.get("seq"), "reason": "hash mismatch (entry modified)"}
            if entry.get("prev_hash") != expected_prev:
                return {"ok": False, "checked": checked, "file": path.name,
                        "seq": entry.get("seq"), "reason": "prev_hash mismatch (entry removed or reordered)"}
            if entry.get("seq") != expected_seq:
                return {"ok": False, "checked": checked, "file": path.name,
                        "seq": entry.get("seq"), "reason": "sequence gap"}
            expected_prev = recorded_hash
            expected_seq += 1
            checked += 1
    return {"ok": True, "checked": checked, "files": [path.name for path in files],
            "last_hash": expected_prev}


def latest_entries(project_path: str | Path, actions: Iterable[str], limit: int = 20) -> list[dict[str, Any]]:
    wanted = set(actions)
    entries = read_audit(project_path, limit=limit * 10)
    return [item for item in entries if item.get("action") in wanted][-limit:]
