"""Metric extraction from run logs.

Research code reports results in whatever format its author chose, so this parser is
deliberately conservative: it recognises three common shapes and records the exact line
it came from, so a report can show the evidence rather than a bare number.

* key/value on one line:      ``epoch 3/10 loss=0.421 acc=0.883``
* colon/equals summary lines: ``Best Accuracy: 91.23%`` or ``F1 = 0.874``
* JSON lines:                 ``{"epoch": 2, "loss": 0.5, "accuracy": 0.81}``
"""

from __future__ import annotations

import json
import re
from typing import Any

NUMBER = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
PAIR = re.compile(rf"\b([A-Za-z][A-Za-z0-9_ .\-]{{0,40}}?)\s*[:=]\s*({NUMBER})\s?(%?)")
EPOCH = re.compile(r"(?:\[\s*)?epoch[\s\[\]=:]*(\d+)\s*(?:/\s*(\d+))?", re.IGNORECASE)
STEP = re.compile(r"(?:\[\s*)?(?:step|iter|iteration)[\s\[\]=:]*(\d+)\s*(?:/\s*(\d+))?", re.IGNORECASE)

IGNORED_KEYS = {
    "line", "step", "steps", "iter", "iteration", "epoch", "epochs",
    "time", "elapsed", "sec", "seconds", "ms", "pid", "gpu", "device",
}

# Research code abbreviates the same quantity in many ways; map the common ones onto a
# canonical name so a declared expectation can be compared with what the log actually says.
ALIASES = {
    "acc": "accuracy",
    "top1": "accuracy",
    "top_1": "accuracy",
    "top1_acc": "accuracy",
    "f1score": "f1",
    "f1_score": "f1",
    "roc_auc": "auc",
    "auc_roc": "auc",
    "val_acc": "val_accuracy",
    "test_acc": "test_accuracy",
    "train_acc": "train_accuracy",
    "mse_loss": "mse",
    "rmse_loss": "rmse",
}


def _normalize_key(raw: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")
    return ALIASES.get(key, key)


def _value(raw: str, percent: str) -> tuple[float, bool]:
    value = float(raw)
    if percent:
        return value / 100.0, True
    return value, False


def _json_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return None
    try:
        value = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    metrics: dict[str, float] = {}
    epoch = None
    step = None
    for key, item in value.items():
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            continue
        normalized = _normalize_key(str(key))
        if normalized in {"epoch", "epochs"}:
            epoch = int(item)
            continue
        if normalized in {"step", "steps", "iter", "iteration"}:
            step = int(item)
            continue
        if normalized in IGNORED_KEYS:
            continue
        metrics[normalized] = float(item)
    if not metrics and epoch is None and step is None:
        return None
    return {"metrics": metrics, "epoch": epoch, "step": step}


def extract_metrics(log_text: str, max_points: int = 5000) -> dict[str, Any]:
    """Extract metric observations with their source lines."""
    points: list[dict[str, Any]] = []
    series: dict[str, list[dict[str, Any]]] = {}
    last_epoch: int | None = None
    last_step: int | None = None

    for line_number, line in enumerate((log_text or "").splitlines(), start=1):
        if not line.strip():
            continue
        epoch_match = EPOCH.search(line)
        step_match = STEP.search(line)
        if epoch_match:
            last_epoch = int(epoch_match.group(1))
        if step_match:
            last_step = int(step_match.group(1))

        payload = _json_line(line)
        if payload is None:
            metrics: dict[str, float] = {}
            for match in PAIR.finditer(line):
                raw_key = match.group(1).strip()
                key = _normalize_key(raw_key)
                if not key or key in IGNORED_KEYS:
                    continue
                if key.isdigit():
                    continue
                value, _ = _value(match.group(2), match.group(3))
                metrics[key] = value
            payload = {"metrics": metrics, "epoch": last_epoch, "step": last_step}
            if not metrics:
                continue
        else:
            if payload["epoch"] is None:
                payload["epoch"] = last_epoch
            if payload["step"] is None:
                payload["step"] = last_step
            last_epoch = payload["epoch"]
            last_step = payload["step"]

        index = len(points) + 1
        point = {
            "index": index,
            "line": line_number,
            "epoch": payload["epoch"],
            "step": payload["step"],
            "metrics": payload["metrics"],
            "evidence": line.strip()[:300],
        }
        points.append(point)
        for key, value in payload["metrics"].items():
            series.setdefault(key, []).append(
                {"index": index, "epoch": payload["epoch"], "step": payload["step"], "value": value}
            )
        if len(points) >= max_points:
            break

    final: dict[str, float] = {}
    for key, observations in series.items():
        if observations:
            final[key] = observations[-1]["value"]

    return {
        "points": points,
        "point_count": len(points),
        "series": series,
        "metric_keys": sorted(series),
        "final": final,
        "first_observation_line": points[0]["line"] if points else None,
        "last_observation_line": points[-1]["line"] if points else None,
    }


def compare_metrics(observed: dict[str, float],
                    expected: dict[str, float],
                    tolerance: float = 0.02) -> dict[str, Any]:
    """Compare observed final metrics against user-declared expectations.

    ``tolerance`` is absolute for values in [0, 1] and relative otherwise.
    """
    comparisons = []
    for key, target in expected.items():
        normalized = _normalize_key(key)
        value = observed.get(normalized)
        if value is None:
            comparisons.append({
                "metric": normalized,
                "expected": target,
                "observed": None,
                "status": "missing",
            })
            continue
        allowed = tolerance if abs(target) <= 1 else abs(target) * tolerance
        delta = abs(value - target)
        comparisons.append({
            "metric": normalized,
            "expected": target,
            "observed": value,
            "delta": round(delta, 6),
            "allowed_delta": round(allowed, 6),
            "status": "match" if delta <= allowed else "mismatch",
        })
    return {
        "tolerance": tolerance,
        "comparisons": comparisons,
        "all_match": bool(comparisons) and all(item["status"] == "match" for item in comparisons),
        "any_missing": any(item["status"] == "missing" for item in comparisons),
    }
