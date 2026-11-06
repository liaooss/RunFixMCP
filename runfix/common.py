from __future__ import annotations
import json, os, re, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

def redact(text: str) -> str:
    for pattern, replacement in [
        (r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._\-]+", r"\1[REDACTED]"),
        (r"\bsk-[A-Za-z0-9_\-]{12,}\b", "[REDACTED_API_KEY]"),
        (r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", "[REDACTED_TOKEN]"),
    ]:
        text = re.sub(pattern, replacement, text)
    return text

def resolve_python(value: str | None = None) -> str:
    candidate = value or sys.executable
    found = shutil.which(candidate) if not Path(candidate).exists() else candidate
    if not found or not Path(found).is_file():
        raise FileNotFoundError(f"Python interpreter not found: {candidate}")
    return str(Path(found).expanduser().resolve())

def run(args: list[str], cwd: Path | None = None, timeout: int = 30, input_text: str | None = None) -> dict[str, Any]:
    try:
        p = subprocess.run(args, cwd=str(cwd) if cwd else None, timeout=timeout,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            input=input_text, shell=False)
        return {"returncode": p.returncode, "stdout": redact(p.stdout or ""),
                "stderr": redact(p.stderr or ""), "timed_out": False}
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""; err = e.stderr or ""
        if isinstance(out, bytes): out = out.decode("utf-8", "replace")
        if isinstance(err, bytes): err = err.decode("utf-8", "replace")
        return {"returncode": None, "stdout": redact(out), "stderr": redact(err), "timed_out": True}
    except OSError as e:
        return {"returncode": 127, "stdout": "", "stderr": str(e), "timed_out": False}

def project_dir(value: str | Path) -> Path:
    p = Path(value).expanduser().resolve()
    if not p.is_dir(): raise NotADirectoryError(str(p))
    return p

def save_report(project: Path, report: dict[str, Any], log: str = "") -> Path:
    run_id = stamp() + "-" + os.urandom(3).hex()
    out = project / ".runfix" / "runs" / run_id
    out.mkdir(parents=True, exist_ok=True)
    report["run_id"] = run_id
    report["created_at"] = datetime.now(timezone.utc).isoformat()
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(to_markdown(report), encoding="utf-8")
    (out / "execution.log").write_text(redact(log), encoding="utf-8")
    return out

def to_markdown(report: dict[str, Any]) -> str:
    lines = ["# RunFix report", "", f"- Run: `{report.get('run_id','pending')}`",
             f"- Project: `{report.get('project_path','')}`",
             f"- Overall: **{report.get('overall_status','unknown')}**", "",
             "## Summary", "", report.get("summary", ""), ""]
    for stage in report.get("stages", []):
        lines.append(f"- **{stage.get('id')} / {stage.get('status')}** — {stage.get('details','')}")
    analysis = report.get("analysis")
    if analysis:
        lines += ["", "## Root-cause analysis", "", f"- Category: `{analysis.get('category')}`",
                  f"- Confidence: `{analysis.get('confidence')}`", "", analysis.get("root_cause", ""), "",
                  "### Recommended actions", ""]
        lines += [f"- {item}" for item in analysis.get("recommended_actions", [])]
    if report.get("agent_mode"):
        lines += ["", "## Agent execution", "", f"- Goal: {report.get('goal','')}",
                  f"- Outcome: `{report.get('outcome')}`", f"- Stop reason: `{report.get('stop_reason')}`",
                  f"- Repair attempts: `{report.get('repair_rounds_used', 0)}`"]
        if report.get("patch_transaction_id"):
            lines.append(f"- Patch transaction: `{report.get('patch_transaction_id')}`")
        rollback = report.get("patch_rollback")
        if rollback:
            lines.append(f"- Patch rollback: `{rollback.get('rolled_back', False)}`")
        lines += ["", "### Tool history", ""]
        for event in report.get("tool_history", []):
            lines.append(f"- Step {event.get('step')}: **{event.get('tool')}** — {event.get('status')} — {event.get('rationale','')}")
        permissions = report.get("permissions", {})
        lines += ["", "### Permissions", "",
                  f"- Project-code execution: `{permissions.get('allow_project_code')}`",
                  f"- File modification: `{permissions.get('allow_write')}`",
                  f"- LLM enabled: `{permissions.get('use_llm')}`"]
    return "\n".join(lines)
