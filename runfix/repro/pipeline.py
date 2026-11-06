"""End-to-end reproduction orchestration: understand, plan, run (when authorized), report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..environments import build_isolated_environment
from .plan import build_environment_plan
from .report import build_reproduction_report, write_reproduction_report
from .runner import latest_reproduction_runs, run_reproduction


def _select_command(plan: dict[str, Any], explicit: list[str] | None) -> tuple[list[str] | None, str]:
    if explicit:
        return list(explicit), "explicit"
    candidates = (plan.get("understanding") or {}).get("run_command_candidates") or []
    for candidate in candidates:
        if candidate.get("confidence") == "documented":
            return list(candidate["argv"]), f"README:{candidate.get('raw')}"
    return None, "none"


def reproduce(project_path: str | Path,
              command: list[str] | None = None,
              python_executable: str | None = None,
              timeout: int = 1800,
              allow_execute: bool = False,
              plan_only: bool = False,
              expected_metrics: dict[str, float] | None = None,
              completion_regex: str | None = None,
              build_environment: bool = False,
              install_timeout: int = 1800,
              sandbox: str = "none",
              sandbox_image: str | None = None,
              sandbox_distro: str | None = None) -> dict[str, Any]:
    """Produce a reproduction report, executing only what the caller authorized."""
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    sandbox = (sandbox or "none").lower()
    if sandbox not in {"none", "docker", "wsl"}:
        raise ValueError(f"Unknown sandbox backend {sandbox!r}; expected none, docker or wsl.")
    if sandbox != "none":
        from ..sandbox import ensure_backend
        # Fail before planning if the requested isolation cannot actually be provided.
        ensure_backend(sandbox)

    plan = build_environment_plan(root, python_executable)
    argv, command_source = _select_command(plan, command)
    plan["selected_command"] = argv
    plan["selected_command_source"] = command_source

    execution: dict[str, Any] = {
        "authorized": bool(allow_execute),
        "planned_command": argv,
        "command_source": command_source,
        "sandbox": sandbox,
    }

    environment_result = None
    effective_python = python_executable
    if build_environment and not plan_only:
        environment_result = build_isolated_environment(
            root, python_executable=python_executable, timeout=install_timeout
        )
        if environment_result.get("created") and environment_result.get("python_executable"):
            effective_python = environment_result["python_executable"]
            plan = build_environment_plan(root, effective_python)
            plan["selected_command"] = argv
            plan["selected_command_source"] = command_source
        else:
            execution["environment_error"] = environment_result.get("reason") or environment_result.get("note")

    run = None
    if plan_only:
        execution["blocked_reason"] = "Plan-only mode requested; project code was not executed."
    elif allow_execute and argv:
        run = run_reproduction(
            root,
            command=argv,
            python_executable=effective_python,
            timeout=timeout,
            expected_metrics=expected_metrics,
            completion_regex=completion_regex,
            sandbox=sandbox,
            sandbox_image=sandbox_image,
            sandbox_distro=sandbox_distro,
        )
    elif allow_execute and not argv:
        execution["blocked_reason"] = (
            "No documented run command was found and none was supplied; RunFix does not invent "
            "a training invocation."
        )
    else:
        execution["blocked_reason"] = "Project-code execution was not authorized."

    previous = latest_reproduction_runs(root, limit=5)
    if run is not None:
        previous = [item for item in previous if item.get("run_id") != run.get("run_id")]

    report = build_reproduction_report(root, plan=plan, run=run, extra_runs=previous)
    report["execution"] = execution
    report["environment"] = environment_result
    report["expected_metrics"] = expected_metrics
    report["project"] = report.get("project") or {}
    report["project"]["run_command_candidates"] = plan.get("understanding", {}).get("run_command_candidates")
    directory = write_reproduction_report(root, report)
    report["report_dir"] = str(directory)
    report["report_json"] = str(directory / "reproduction_report.json")
    report["report_markdown"] = str(directory / "reproduction_report.md")

    from ..audit import audit as _audit
    _audit(root, "reproduce.run", target=" ".join(argv or [])[:400],
           outcome=report["verdict"], actor="runfix.reproduction",
           details={"run_status": (run or {}).get("status"),
                    "elapsed_seconds": (run or {}).get("duration_seconds"),
                    "sandbox": sandbox, "expected_metrics": expected_metrics,
                    "report": report["report_json"]})
    return report
