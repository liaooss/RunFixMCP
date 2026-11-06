"""Run the benchmark suite and compute metrics from the recorded ground truth."""

from __future__ import annotations

import hashlib
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..diagnosis.rootcause import rank_root_causes
from .suite import SUITE, BenchTask, get_tasks, materialize_task, task_traceback

ABLATIONS = {
    "full": frozenset(),
    "no_dependency_analysis": frozenset({"dependencies"}),
    "no_classifier_agreement": frozenset({"classifier"}),
    "no_score_ranking": frozenset({"ranking"}),
}


def _snapshot(root: Path) -> dict[str, str]:
    state: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            relative = path.relative_to(root).as_posix()
            state[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return state


def _changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    changed = []
    for path in sorted(set(before) | set(after)):
        if before.get(path) != after.get(path):
            changed.append(path)
    return changed


def run_task(task: BenchTask, ablate: set[str] | frozenset[str] = frozenset(),
             keep: bool = False) -> dict[str, Any]:
    """Materialise one task, diagnose it, and compare with the recorded ground truth."""
    with tempfile.TemporaryDirectory(prefix=f"runfix-bench-{task.id}-") as temporary:
        root = materialize_task(task, temporary)
        before = _snapshot(root)
        traceback_text = task_traceback(task, root)
        started = time.perf_counter()
        error = None
        result: dict[str, Any] = {}
        try:
            result = rank_root_causes(
                root,
                traceback_text,
                include_probe=False,
                include_understanding=task.include_understanding,
                environment_override=task.environment,
                installed_override=task.installed,
                ablate=ablate,
            )
        except Exception as exc:  # pragma: no cover - defensive
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - started
        after = _snapshot(root)

    candidates = result.get("candidates") or []
    kinds = [item["kind"] for item in candidates]
    top = candidates[0] if candidates else None
    modified = _changed_files(before, after)

    return {
        "task_id": task.id,
        "category": task.category,
        "description": task.description,
        "expected_kind": task.expected_kind,
        "expected_action": task.expected_action,
        "top_kind": top["kind"] if top else None,
        "top_action": (top or {}).get("action", {}).get("kind"),
        "top_confidence": (top or {}).get("confidence"),
        "ranked_kinds": kinds,
        "candidate_count": len(candidates),
        "top1_hit": bool(top) and top["kind"] == task.expected_kind,
        "top3_hit": task.expected_kind in kinds[:3],
        "action_hit": bool(top) and (top.get("action") or {}).get("kind") == task.expected_action,
        "detected": bool(top) and top["kind"] != "unclassified_error",
        "elapsed_seconds": round(elapsed, 4),
        "files_modified_by_diagnosis": modified,
        "error": error,
        "ablations": sorted(ablate),
        "project_files": sorted(task.files),
    }


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the metrics from per-task records. Never hides a miss."""
    total = len(records)
    if not total:
        return {"task_count": 0}

    def rate(key: str) -> float:
        return round(sum(1 for item in records if item.get(key)) / total, 4)

    controls = [item for item in records if item["category"] == "control"]
    non_control = [item for item in records if item["category"] != "control"]
    confidences = [item["top_confidence"] for item in records if item.get("top_confidence") is not None]
    latencies = [item["elapsed_seconds"] for item in records]

    def sub_rate(items: list[dict[str, Any]], key: str) -> float | None:
        return round(sum(1 for item in items if item.get(key)) / len(items), 4) if items else None

    # A control task is *supposed* to stay unclassified, so the two rates are reported
    # separately instead of being averaged into one misleading "detection rate".
    detected_non_control = round(
        sum(1 for item in non_control if item["detected"]) / max(1, len(non_control)), 4
    )
    control_abstention = round(
        sum(1 for item in controls if item["top_kind"] == "unclassified_error")
        / max(1, len(controls)), 4
    )

    return {
        "task_count": total,
        "control_task_count": len(controls),
        "top1_accuracy": rate("top1_hit"),
        "top3_accuracy": rate("top3_hit"),
        "action_accuracy": rate("action_hit"),
        "detection_rate": detected_non_control,
        "control_abstention_rate": control_abstention if controls else None,
        "unclassified_rate": round(
            sum(1 for item in records if item["top_kind"] == "unclassified_error") / total, 4
        ),
        "mean_top_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
        "mean_latency_seconds": round(sum(latencies) / len(latencies), 4),
        "total_latency_seconds": round(sum(latencies), 4),
        "tasks_with_errors": sum(1 for item in records if item["error"]),
        "files_modified_by_diagnosis": sum(len(item["files_modified_by_diagnosis"]) for item in records),
        "unauthorized_modification_count": sum(
            len(item["files_modified_by_diagnosis"]) for item in records
        ),
        "per_category_accuracy": {
            category: sub_rate([item for item in records if item["category"] == category], "top1_hit")
            for category in sorted({item["category"] for item in records})
        },
    }


def run_suite(task_ids: list[str] | None = None,
              ablate: set[str] | frozenset[str] = frozenset(),
              suite: str = SUITE) -> dict[str, Any]:
    """Run every task of the suite under one configuration."""
    started = datetime.now(timezone.utc)
    records = [run_task(task, ablate=ablate) for task in get_tasks(task_ids)]
    finished = datetime.now(timezone.utc)
    return {
        "suite": suite,
        "ablations": sorted(ablate),
        "variant": next((name for name, value in ABLATIONS.items() if value == frozenset(ablate)), "custom"),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "wall_seconds": round((finished - started).total_seconds(), 3),
        "metrics": aggregate(records),
        "tasks": records,
    }


def run_ablations(task_ids: list[str] | None = None,
                  suite: str = SUITE) -> dict[str, Any]:
    """Run the suite once per ablation variant and compare them."""
    variants = []
    for name, ablate in ABLATIONS.items():
        run = run_suite(task_ids=task_ids, ablate=ablate, suite=suite)
        run["variant"] = name
        variants.append(run)

    baseline = next((item for item in variants if item["variant"] == "full"), variants[0])
    comparison = []
    for run in variants:
        comparison.append({
            "variant": run["variant"],
            "ablations": run["ablations"],
            "top1_accuracy": run["metrics"]["top1_accuracy"],
            "top3_accuracy": run["metrics"]["top3_accuracy"],
            "action_accuracy": run["metrics"]["action_accuracy"],
            "mean_top_confidence": run["metrics"]["mean_top_confidence"],
            "delta_top1_vs_full": round(
                run["metrics"]["top1_accuracy"] - baseline["metrics"]["top1_accuracy"], 4
            ),
            "delta_action_vs_full": round(
                run["metrics"]["action_accuracy"] - baseline["metrics"]["action_accuracy"], 4
            ),
        })
    return {
        "suite": suite,
        "variants": variants,
        "comparison": comparison,
        "baseline": baseline["variant"],
    }
