"""Render benchmark results as Markdown, always including what was not measured."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..common import stamp
from .suite import SUITE, SUITE_DESCRIPTION

NOT_MEASURED = {
    "environment_build_success_rate": (
        "Requires installing packages (network) and, for conda tasks, a working conda. "
        "Not measured on this offline suite."
    ),
    "repair_success_rate": (
        "Requires either authorized package installation or an LLM-authored patch. "
        "Synthetic-v1 measures the *proposed action*, not whether the repair was applied."
    ),
    "test_pass_rate_after_repair": "Depends on repair being applied; see repair_success_rate.",
    "experiment_reproduction_rate": (
        "Requires a curated corpus of real research repositories with published results. "
        "Synthetic fixtures have no paper to reproduce."
    ),
    "real_world_root_cause_accuracy": (
        "Synthetic-v1 tasks are constructed by us; accuracy on published repositories is "
        "unknown until a curated corpus is evaluated."
    ),
}


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join([" --- "] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|") for cell in row) + " |")
    return lines


def _metric_line(name: str, value: Any) -> str:
    return f"- **{name}**: `{value}`"


def render_suite_markdown(run: dict[str, Any]) -> str:
    metrics = run.get("metrics") or {}
    lines: list[str] = ["# RunFix benchmark report", ""]
    lines += [
        f"- **Suite:** `{run.get('suite')}`",
        f"- **Variant:** `{run.get('variant')}`"
        + (f" (ablations: {', '.join(run.get('ablations') or [])})" if run.get("ablations") else ""),
        f"- **Started:** {run.get('started_at')}",
        f"- **Wall time:** {run.get('wall_seconds')} s",
        "",
        SUITE_DESCRIPTION,
        "",
        "## Metrics",
        "",
    ]
    lines += [
        _metric_line("tasks", metrics.get("task_count")),
        _metric_line("top-1 root-cause accuracy", metrics.get("top1_accuracy")),
        _metric_line("top-3 root-cause accuracy", metrics.get("top3_accuracy")),
        _metric_line("proposed-action accuracy", metrics.get("action_accuracy")),
        _metric_line("detection rate (non-control tasks)", metrics.get("detection_rate")),
        _metric_line("control abstention rate (control tasks)", metrics.get("control_abstention_rate")),
        _metric_line("unclassified rate (all tasks)", metrics.get("unclassified_rate")),
        _metric_line("mean top confidence", metrics.get("mean_top_confidence")),
        _metric_line("mean diagnosis latency (s)", metrics.get("mean_latency_seconds")),
        _metric_line("files modified by diagnosis", metrics.get("files_modified_by_diagnosis")),
    ]
    per_category = metrics.get("per_category_accuracy") or {}
    if per_category:
        lines += ["", "Per-category top-1 accuracy:", ""]
        lines += [f"- `{name}`: `{value}`" for name, value in per_category.items()]
    lines.append("")

    tasks = run.get("tasks") or []
    if tasks:
        lines += ["## Per-task results", ""]
        rows = []
        for item in tasks:
            mark = "yes" if item["top1_hit"] else "**no**"
            rows.append([
                f"`{item['task_id']}`",
                f"`{item['expected_kind']}`",
                f"`{item['top_kind']}`",
                mark,
                item.get("top_confidence"),
                item.get("elapsed_seconds"),
            ])
        lines += _table(["Task", "Expected", "Top candidate", "Hit", "Confidence", "Seconds"], rows)
        lines.append("")

        misses = [item for item in tasks if not item["top1_hit"]]
        if misses:
            lines += ["### Misses", ""]
            for item in misses:
                lines.append(
                    f"- `{item['task_id']}` expected `{item['expected_kind']}` but ranked "
                    f"{', '.join('`' + kind + '`' for kind in item['ranked_kinds'][:4]) or '(nothing)'}"
                )
            lines.append("")

        modified = [item for item in tasks if item["files_modified_by_diagnosis"]]
        lines.append(
            f"Diagnosis modified files in {len(modified)} of {len(tasks)} tasks "
            f"(expected: 0 — diagnosis is read-only)."
        )
        lines.append("")

    lines += ["## Not measured", ""]
    lines += [f"- **{name}**: {reason}" for name, reason in NOT_MEASURED.items()]
    lines.append("")
    lines += [
        "> These numbers describe a synthetic suite that RunFix's authors constructed. They are "
        "reproducible and they guard against regressions, but they are not evidence of accuracy "
        "on published research repositories.",
        "",
    ]
    return "\n".join(lines)


def render_ablation_markdown(study: dict[str, Any]) -> str:
    lines: list[str] = ["# RunFix ablation study", ""]
    lines += [
        f"- **Suite:** `{study.get('suite')}`",
        f"- **Baseline:** `{study.get('baseline')}`",
        f"- **Generated:** {datetime.now(timezone.utc).isoformat()}",
        "",
        "Each variant runs the same tasks with one contribution disabled.",
        "",
        "## Comparison",
        "",
    ]
    rows = []
    for item in study.get("comparison") or []:
        rows.append([
            f"`{item['variant']}`",
            ", ".join(item["ablations"]) or "(none)",
            item["top1_accuracy"],
            item["top3_accuracy"],
            item["action_accuracy"],
            item["delta_top1_vs_full"],
            item["delta_action_vs_full"],
        ])
    lines += _table(
        ["Variant", "Disabled", "Top-1", "Top-3", "Action", "Δ top-1", "Δ action"], rows
    )
    lines.append("")

    baseline = study.get("baseline")
    for item in study.get("comparison") or []:
        if item["variant"] == baseline:
            continue
        delta = item["delta_top1_vs_full"]
        if delta < 0:
            lines.append(
                f"- Removing `{', '.join(item['ablations'])}` lowers top-1 accuracy by "
                f"{abs(delta):.2%} — that contribution carries real signal."
            )
        elif delta > 0:
            lines.append(
                f"- Removing `{', '.join(item['ablations'])}` *raises* top-1 accuracy by "
                f"{delta:.2%} — that contribution is currently counter-productive on this suite."
            )
        else:
            lines.append(
                f"- Removing `{', '.join(item['ablations'])}` does not change top-1 accuracy on "
                "this suite; the suite does not yet discriminate it."
            )
    lines.append("")
    lines += [f"{name}: {reason}" for name, reason in NOT_MEASURED.items()]
    lines.append("")
    return "\n".join(lines)


def write_benchmark_report(result: dict[str, Any], directory: str | Path | None = None) -> Path:
    """Write JSON plus Markdown for a suite run or an ablation study."""
    target = Path(directory) if directory else Path.cwd() / ".runfix-bench" / (
        stamp() + "-" + os.urandom(2).hex()
    )
    target.mkdir(parents=True, exist_ok=True)
    if "comparison" in result:
        (target / "ablation.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        (target / "ablation.md").write_text(render_ablation_markdown(result), encoding="utf-8")
    else:
        (target / "benchmark.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        (target / "benchmark.md").write_text(render_suite_markdown(result), encoding="utf-8")
    return target
