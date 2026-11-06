"""Build and render the reproduction report.

The verdict is a ladder, and the report always states which rung was reached and what
evidence is missing for the next one. ``reproduced`` is never derived from an exit code:
it requires an explicit user-declared expectation that the observed metrics satisfy.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..common import stamp

VERDICT_ORDER = (
    "inspected",
    "plan_ready",
    "executed",
    "completed",
    "metrics_recorded",
    "metrics_matched",
    "reproduced",
)

VERDICT_MEANING = {
    "inspected": "The repository was read and described; nothing was executed.",
    "plan_ready": "An environment/run plan exists and its blockers are known.",
    "executed": "The command ran, but the run did not reach a demonstrable terminal state.",
    "process_exited": "The process exited with code 0 but produced no completion evidence; this is NOT a reproduction.",
    "completed": "The run exited successfully and produced completion evidence (markers or a metric series).",
    "metrics_recorded": "Final metrics were recorded from the run output.",
    "metrics_matched": "Recorded metrics satisfy the expectations the user declared.",
    "reproduced": "Metrics matched the declared expectations on a clean, fingerprinted run.",
}


def _higher_of(current: str, candidate: str) -> str:
    try:
        return candidate if VERDICT_ORDER.index(candidate) > VERDICT_ORDER.index(current) else current
    except ValueError:
        return current


def derive_verdict(plan: dict[str, Any] | None,
                   run: dict[str, Any] | None) -> tuple[str, list[str]]:
    """Return the highest justified rung plus the reasons the next rung was not reached."""
    verdict = "inspected"
    reasons: list[str] = []

    if plan is not None:
        verdict = _higher_of(verdict, "plan_ready")

    if run is None:
        reasons.append("No command was executed, so no runtime evidence exists.")
        return verdict, reasons

    status = run.get("status")
    if status == "failed_to_start":
        reasons.append(f"The command could not be started: {run.get('start_error')}")
        return verdict, reasons
    if status == "timed_out":
        verdict = _higher_of(verdict, "executed")
        reasons.append(f"The run exceeded its {run.get('timeout_seconds')}s timeout and was killed.")
        return verdict, reasons
    if status == "failed":
        verdict = _higher_of(verdict, "executed")
        reasons.append(f"The process exited with code {run.get('returncode')}.")
        return verdict, reasons
    if status == "process_exited":
        verdict = _higher_of(verdict, "executed")
        reasons.append(
            "The process exited with code 0 but printed no completion marker and no metric series. "
            "A zero exit code alone is not evidence that the experiment finished."
        )
        return verdict, reasons

    verdict = _higher_of(verdict, "completed")
    metrics = run.get("metrics") or {}
    if metrics.get("point_count", 0) > 0 and metrics.get("final"):
        verdict = _higher_of(verdict, "metrics_recorded")
    else:
        reasons.append("The run completed but no numeric metric could be parsed from its output.")

    comparison = run.get("metric_comparison")
    if comparison:
        if comparison.get("all_match"):
            verdict = _higher_of(verdict, "metrics_matched")
        elif comparison.get("any_missing"):
            reasons.append("Some expected metrics were not present in the run output.")
        else:
            mismatches = [item for item in comparison.get("comparisons", []) if item.get("status") == "mismatch"]
            for item in mismatches:
                reasons.append(
                    f"{item['metric']}: expected {item['expected']}, observed {item['observed']} "
                    f"(allowed delta {item.get('allowed_delta')})."
                )
    else:
        reasons.append(
            "No expected metric values were declared, so the result cannot be compared with the "
            "paper. Supply --expect metric=value to allow a reproduction claim."
        )

    fingerprint = run.get("fingerprint") or {}
    git = fingerprint.get("git") or {}
    dirty = git.get("dirty")
    commit = git.get("commit")
    if verdict == "metrics_matched":
        if dirty:
            reasons.append(
                "The repository had uncommitted changes during the run, so the result cannot be "
                "tied to a reproducible commit."
            )
        elif not commit:
            reasons.append(
                "The project is not under version control (no git commit was recorded), so the "
                "result cannot be tied to a specific revision."
            )
        else:
            verdict = _higher_of(verdict, "reproduced")

    return verdict, reasons


def build_reproduction_report(project_path: str | Path,
                              plan: dict[str, Any] | None = None,
                              run: dict[str, Any] | None = None,
                              extra_runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    verdict, reasons = derive_verdict(plan, run)
    understanding = (plan or {}).get("understanding")

    report: dict[str, Any] = {
        "report_type": "runfix-reproduction",
        "report_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_path": str(root),
        "verdict": verdict,
        "verdict_meaning": VERDICT_MEANING.get(verdict, verdict),
        "verdict_reasons": reasons,
        "ladder": [
            {
                "rung": rung,
                "reached": VERDICT_ORDER.index(rung) <= VERDICT_ORDER.index(verdict),
                "meaning": VERDICT_MEANING.get(rung, ""),
            }
            for rung in VERDICT_ORDER
        ],
        "plan": None if plan is None else {
            "blockers": plan.get("blockers"),
            "cuda": plan.get("cuda"),
            "gpu": plan.get("gpu"),
            "steps": plan.get("steps"),
            "requires_authorization": plan.get("requires_authorization"),
            "warnings": plan.get("warnings"),
        },
        "project": None if understanding is None else {
            "readme": understanding.get("readme"),
            "entrypoints": understanding.get("entrypoints"),
            "configs": understanding.get("configs"),
            "environment": understanding.get("environment"),
            "device": understanding.get("device"),
            "datasets": understanding.get("datasets"),
            "seeds": understanding.get("seeds"),
            "training_parameters": understanding.get("training_parameters"),
            "expected_metrics": understanding.get("expected_metrics"),
            "run_command_candidates": understanding.get("run_command_candidates"),
        },
        "run": run,
        "previous_runs": extra_runs or [],
        "limitations": [
            "A completed run is not the same as a reproduced result; reproduction requires "
            "declared target metrics that the run satisfies.",
            "Metrics are parsed from stdout/stderr text, so a project that writes metrics only "
            "to a file or a tracking server will not be captured unless it also prints them.",
            "RunFix never downloads datasets, model weights or licensed assets without explicit "
            "authorization, and reports their absence instead.",
            "Passing a smoke configuration is not evidence that the full-scale experiment reproduces.",
        ],
    }
    return report


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join([" --- "] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|") for cell in row) + " |")
    return lines


def render_reproduction_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = ["# RunFix reproduction report", ""]
    lines += [
        f"- **Project:** `{report.get('project_path')}`",
        f"- **Created:** {report.get('created_at')}",
        f"- **Verdict:** **{report.get('verdict')}**",
        f"- Meaning: {report.get('verdict_meaning')}",
        "",
    ]

    lines += ["## Verdict ladder", ""]
    lines += _table(
        ["Rung", "Reached", "Meaning"],
        [[item["rung"], "yes" if item["reached"] else "no", item["meaning"]] for item in report.get("ladder", [])],
    )
    lines.append("")

    reasons = report.get("verdict_reasons") or []
    if reasons:
        lines += ["### Why the verdict is not higher", ""]
        lines += [f"- {reason}" for reason in reasons]
        lines.append("")

    project = report.get("project")
    if project:
        lines += ["## What the repository says", ""]
        readme = project.get("readme") or {}
        lines.append(f"- README: `{readme.get('path')}`" if readme.get("path") else "- README: **not found**")
        if readme.get("install_commands"):
            lines += ["", "Documented install commands:", ""]
            lines += [f"- `{command}`" for command in readme["install_commands"][:10]]
        if readme.get("run_commands"):
            lines += ["", "Documented run commands:", ""]
            lines += [f"- `{command}`" for command in readme["run_commands"][:10]]
        entrypoints = project.get("entrypoints") or []
        if entrypoints:
            lines += ["", "Ranked entrypoints:", ""]
            lines += _table(
                ["Path", "Score", "Evidence"],
                [[f"`{item['path']}`", item["score"], "; ".join(item["evidence"])] for item in entrypoints[:5]],
            )
        environment = project.get("environment") or {}
        lines += ["", "Environment evidence:", ""]
        lines += [
            f"- Manifests: {', '.join(environment.get('manifests') or ['(none found)'])}",
            f"- Frameworks: {', '.join(environment.get('frameworks') or ['(none detected)'])}",
            f"- Python requirement: {environment.get('python_requirement') or '(not declared)'}",
            f"- CUDA wheel tags: {', '.join(environment.get('cuda_tags') or ['(none declared)'])}",
        ]
        datasets = project.get("datasets") or []
        if datasets:
            lines += ["", "Dataset references:", ""]
            lines += _table(
                ["Config key", "Value", "Exists"],
                [[f"`{item['key']}`", f"`{item['value']}`", "yes" if item["exists"] else "**no**"]
                 for item in datasets[:10]],
            )
        seeds = project.get("seeds") or []
        if seeds:
            lines += ["", "Seeds declared in configuration: " +
                      ", ".join(f"`{item['key']}={item['value']}`" for item in seeds[:6])]
        lines.append("")

    plan = report.get("plan")
    if plan:
        lines += ["## Reproduction plan", ""]
        steps = plan.get("steps") or []
        lines += _table(
            ["Step", "Status", "Needs authorization", "Detail"],
            [[step.get("id"), step.get("status"),
              "yes" if step.get("requires_authorization") else "no",
              step.get("warning") or step.get("detail") or step.get("manifest") or ""]
             for step in steps],
        )
        cuda = plan.get("cuda") or {}
        gpu = plan.get("gpu") or {}
        lines += ["", f"- CUDA compatibility: **{cuda.get('verdict')}** — {cuda.get('reason')}",
                  f"- GPU visible to the selected interpreter: `{gpu.get('available')}`"]
        if plan.get("blockers"):
            lines.append(f"- Blockers: {', '.join(plan['blockers'])}")
        lines.append("")

    run = report.get("run")
    if run:
        lines += ["## Execution", ""]
        lines += [
            f"- Command: `{' '.join(run.get('command') or [])}`",
            f"- Status: **{run.get('status')}** (exit code `{run.get('returncode')}`)",
            f"- Duration: {run.get('duration_seconds')} s",
            f"- Log: `{(run.get('log') or {}).get('path')}` ({((run.get('log') or {}).get('size_bytes'))} bytes)",
        ]
        fingerprint = (run.get("fingerprint") or {})
        git = fingerprint.get("git") or {}
        hardware = run.get("hardware") or {}
        lines += [
            f"- Git commit: `{git.get('commit')}` (dirty: `{git.get('dirty')}`)",
            f"- Python: `{hardware.get('python_version')}` at `{hardware.get('python_executable')}`",
            f"- PyTorch: `{hardware.get('torch_version')}` (CUDA `{hardware.get('torch_cuda')}`)",
            f"- GPUs: {', '.join(hardware.get('gpu_names') or []) or '(none reported)'}",
        ]
        evidence = run.get("completion_evidence") or {}
        lines += ["", "Completion evidence:", ""]
        lines += [
            f"- Metric series found: `{evidence.get('metric_series_count')}`",
            f"- Completion markers: {', '.join(evidence.get('markers') or []) or '(none)'}",
            f"- Caller completion regex: `{evidence.get('completion_regex_match')}`",
        ]

        metrics = run.get("metrics") or {}
        final = metrics.get("final") or {}
        if final:
            lines += ["", "### Final metrics", ""]
            lines += _table(["Metric", "Value"],
                            [[key, value] for key, value in sorted(final.items())])

        comparison = run.get("metric_comparison")
        if comparison:
            lines += ["", "### Comparison with declared expectations", ""]
            lines += _table(
                ["Metric", "Expected", "Observed", "Delta", "Allowed", "Status"],
                [[item["metric"], item["expected"], item.get("observed"),
                  item.get("delta", "-"), item.get("allowed_delta", "-"), item["status"]]
                 for item in comparison.get("comparisons", [])],
            )

        artifacts = run.get("artifacts") or []
        if artifacts:
            lines += ["", f"### Artifacts created during the run ({len(artifacts)})", ""]
            lines += _table(["Path", "Size (bytes)"],
                            [[f"`{item['path']}`", item["size_bytes"]] for item in artifacts[:20]])
        lines.append("")

    limitations = report.get("limitations") or []
    if limitations:
        lines += ["## Limitations", ""]
        lines += [f"- {item}" for item in limitations]
        lines.append("")

    return "\n".join(lines)


def write_reproduction_report(project_path: str | Path,
                              report: dict[str, Any],
                              directory: Path | None = None) -> Path:
    root = Path(project_path).expanduser().resolve()
    if directory is None:
        directory = root / ".runfix" / "repro" / "reports" / (stamp() + "-" + os.urandom(3).hex())
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reproduction_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (directory / "reproduction_report.md").write_text(
        render_reproduction_markdown(report), encoding="utf-8"
    )
    return directory
