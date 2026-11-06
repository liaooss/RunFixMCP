"""Explicit, expiring, one-shot approvals for privileged operations.

RunFix already asks for a per-call confirmation. Approvals add a second, durable gate for
environments where a human authorizes work ahead of time (a shared workstation, an unattended
MCP host): the operator grants an action for N uses and M minutes, and every consumption is
recorded in the audit trail.

Approvals can only *narrow* what happens. They never grant a capability the server or the CLI
permission flags did not already enable.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

APPROVAL_ACTIONS = (
    "patch.apply",
    "patch.rollback",
    "environment.build",
    "task.start",
    "task.stop",
    "agent.run",
    "reproduce.run",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state_root(project: Path) -> Path:
    state = project / ".runfix"
    if state.is_symlink():
        raise PermissionError("The project .runfix directory must not be a symlink.")
    state.mkdir(parents=True, exist_ok=True)
    resolved = state.resolve()
    try:
        resolved.relative_to(project)
    except ValueError as exc:
        raise PermissionError("Approval storage escapes the project directory.") from exc
    return resolved


def _path(project: Path) -> Path:
    return _state_root(project) / "approvals.json"


def _policy_path(project: Path) -> Path:
    return _state_root(project) / "policy.json"


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return dict(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False,
                                         dir=str(path.parent), suffix=".tmp")
    try:
        with handle as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        Path(handle.name).replace(path)
    finally:
        Path(handle.name).unlink(missing_ok=True)


def load_policy(project_path: str | Path) -> dict[str, Any]:
    """Project approval policy: ``{"require_approval": ["patch.apply", ...]}``."""
    project = Path(project_path).expanduser().resolve()
    policy = _read_json(_policy_path(project), {"require_approval": []})
    actions = [item for item in policy.get("require_approval") or [] if item in APPROVAL_ACTIONS]
    return {"require_approval": sorted(set(actions))}


def save_policy(project_path: str | Path, require_approval: list[str]) -> dict[str, Any]:
    project = Path(project_path).expanduser().resolve()
    unknown = [item for item in require_approval if item not in APPROVAL_ACTIONS]
    if unknown:
        raise ValueError(f"Unknown approval action(s): {', '.join(unknown)}")
    payload = {"require_approval": sorted(set(require_approval))}
    _write_json(_policy_path(project), payload)
    return payload


def requires_approval(project_path: str | Path, action: str) -> bool:
    return action in load_policy(project_path)["require_approval"]


def _prune(grants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = _now()
    kept = []
    for grant in grants:
        expires = grant.get("expires_at")
        try:
            expiry = datetime.fromisoformat(str(expires))
        except (TypeError, ValueError):
            continue
        if expiry <= now:
            continue
        if int(grant.get("uses_remaining") or 0) <= 0:
            continue
        kept.append(grant)
    return kept


def grant(project_path: str | Path, action: str, uses: int = 1, ttl_minutes: int = 60,
          note: str = "", actor: str = "cli") -> dict[str, Any]:
    """Grant an action for ``uses`` consumptions within ``ttl_minutes``."""
    project = Path(project_path).expanduser().resolve()
    if not project.is_dir():
        raise NotADirectoryError(str(project))
    if action not in APPROVAL_ACTIONS:
        raise ValueError(f"Unknown approval action {action!r}; expected one of {', '.join(APPROVAL_ACTIONS)}")
    uses = max(1, min(int(uses), 1000))
    ttl_minutes = max(1, min(int(ttl_minutes), 7 * 24 * 60))

    payload = _read_json(_path(project), {"grants": []})
    grants = _prune(list(payload.get("grants") or []))
    entry = {
        "action": action,
        "granted_at": _now().isoformat(),
        "expires_at": (_now() + timedelta(minutes=ttl_minutes)).isoformat(),
        "uses_remaining": uses,
        "note": str(note)[:300],
        "actor": str(actor)[:80],
    }
    grants.append(entry)
    _write_json(_path(project), {"grants": grants})
    from .audit import audit
    audit(project, "approval.grant", target=action, outcome="granted",
          actor=actor, details={"uses": uses, "ttl_minutes": ttl_minutes, "note": entry["note"]})
    return {"granted": True, "grant": entry}


def list_grants(project_path: str | Path) -> dict[str, Any]:
    project = Path(project_path).expanduser().resolve()
    payload = _read_json(_path(project), {"grants": []})
    grants = _prune(list(payload.get("grants") or []))
    return {"project_path": str(project), "policy": load_policy(project), "grants": grants,
            "count": len(grants)}


def revoke(project_path: str | Path, action: str, actor: str = "cli") -> dict[str, Any]:
    project = Path(project_path).expanduser().resolve()
    payload = _read_json(_path(project), {"grants": []})
    grants = [item for item in payload.get("grants") or [] if item.get("action") != action]
    _write_json(_path(project), {"grants": grants})
    from .audit import audit
    audit(project, "approval.revoke", target=action, outcome="revoked", actor=actor)
    return {"revoked": True, "action": action}


def consume(project_path: str | Path, action: str, actor: str = "cli") -> dict[str, Any]:
    """Consume one use of a grant. Returns ``{"allowed": bool, "reason": str}``."""
    project = Path(project_path).expanduser().resolve()
    if not project.is_dir():
        raise NotADirectoryError(str(project))

    payload = _read_json(_path(project), {"grants": []})
    grants = _prune(list(payload.get("grants") or []))
    index = next((i for i, item in enumerate(grants) if item.get("action") == action), None)
    if index is None:
        return {
            "allowed": False,
            "reason": (
                f"No valid approval for '{action}'. Ask an operator to run: "
                f"runfix approvals \"{project}\" --grant {action}"
            ),
        }
    entry = grants[index]
    entry["uses_remaining"] = int(entry.get("uses_remaining") or 0) - 1
    grants = _prune(grants)
    _write_json(_path(project), {"grants": grants})
    from .audit import audit
    audit(project, "approval.consume", target=action, outcome="consumed", actor=actor,
          details={"uses_remaining": max(0, entry["uses_remaining"])})
    return {"allowed": True, "reason": "approval consumed", "grant": entry}
