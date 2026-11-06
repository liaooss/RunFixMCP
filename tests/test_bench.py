"""Tests for the reproducible diagnosis benchmark and its ablation study (0.9.0)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runfix.agent.policy import ROOT_CAUSE_STOP_REASONS
from runfix.bench import (
    ABLATIONS,
    SUITE,
    TASKS,
    aggregate,
    get_tasks,
    materialize_task,
    render_ablation_markdown,
    render_suite_markdown,
    run_ablations,
    run_suite,
    run_task,
    task_traceback,
    write_benchmark_report,
)
from runfix.bench.suite import TASKS_BY_ID
from runfix.diagnosis.rootcause import ACTION_BY_KIND, BASE_CONFIDENCE


def test_suite_ids_are_unique_and_complete():
    ids = [task.id for task in TASKS]
    assert len(ids) == len(set(ids)) >= 10
    assert SUITE == "synthetic-v1"


def test_every_expected_kind_and_action_is_a_real_kind():
    for task in TASKS:
        assert task.expected_kind in BASE_CONFIDENCE, task.id
        assert task.expected_action in ACTION_BY_KIND.values(), task.id


def test_suite_contains_a_control_task():
    controls = [task for task in TASKS if task.category == "control"]
    assert len(controls) == 1
    assert controls[0].expected_kind == "unclassified_error"
    assert controls[0].expected_action == "collect_more_evidence"


def test_get_tasks_filters_and_rejects_unknown_ids():
    assert len(get_tasks()) == len(TASKS)
    assert [task.id for task in get_tasks(["version_unsatisfied"])] == ["version_unsatisfied"]
    with pytest.raises(KeyError):
        get_tasks(["does-not-exist"])


def test_materialize_task_writes_files_and_refuses_a_used_directory(tmp_path):
    task = TASKS_BY_ID["version_unsatisfied"]
    root = materialize_task(task, tmp_path)
    for relative in task.files:
        assert (root / relative).is_file()
    with pytest.raises(FileExistsError):
        materialize_task(task, tmp_path)


def test_traceback_template_uses_the_materialized_path(tmp_path):
    task = TASKS_BY_ID["missing_declared_dependency"]
    root = materialize_task(task, tmp_path)
    text = task_traceback(task, root)
    assert str(root).replace("\\", "/") in text
    assert "ModuleNotFoundError" in text


def test_task_without_traceback_returns_none():
    assert task_traceback(TASKS_BY_ID["version_unsatisfied"], ".") is None


def test_run_task_hits_the_expected_root_cause():
    record = run_task(TASKS_BY_ID["missing_declared_dependency"])
    assert record["top_kind"] == "module_declared_missing"
    assert record["top_action"] == "install_package"
    assert record["top1_hit"] is True
    assert record["action_hit"] is True
    assert record["files_modified_by_diagnosis"] == []
    assert record["error"] is None


def test_run_task_control_abstains():
    record = run_task(TASKS_BY_ID["control_no_signal"])
    assert record["top_kind"] == "unclassified_error"
    assert record["detected"] is False
    assert record["top1_hit"] is True  # expected kind *is* unclassified_error


def test_run_task_records_ablation_labels():
    record = run_task(TASKS_BY_ID["version_unsatisfied"], ablate=frozenset({"dependencies"}))
    assert record["ablations"] == ["dependencies"]


def _record(**overrides):
    base = {
        "task_id": "t",
        "category": "dependency",
        "top_kind": "module_declared_missing",
        "top_confidence": 0.9,
        "top1_hit": True,
        "top3_hit": True,
        "action_hit": True,
        "detected": True,
        "elapsed_seconds": 0.1,
        "files_modified_by_diagnosis": [],
        "error": None,
    }
    base.update(overrides)
    return base


def test_aggregate_separates_detection_from_control_abstention():
    records = [
        _record(),
        _record(task_id="c", category="control", top_kind="unclassified_error",
                detected=False, top1_hit=True, action_hit=True),
    ]
    metrics = aggregate(records)
    assert metrics["task_count"] == 2
    assert metrics["control_task_count"] == 1
    assert metrics["detection_rate"] == 1.0
    assert metrics["control_abstention_rate"] == 1.0
    assert metrics["unclassified_rate"] == 0.5
    assert metrics["per_category_accuracy"] == {"control": 1.0, "dependency": 1.0}


def test_aggregate_counts_misses_and_modifications():
    records = [
        _record(),
        _record(task_id="m", top_kind="unclassified_error", top1_hit=False,
                top3_hit=False, action_hit=False, detected=False,
                files_modified_by_diagnosis=["train.py"]),
    ]
    metrics = aggregate(records)
    assert metrics["top1_accuracy"] == 0.5
    assert metrics["files_modified_by_diagnosis"] == 1
    assert metrics["unauthorized_modification_count"] == 1


def test_aggregate_of_nothing_is_explicit():
    assert aggregate([]) == {"task_count": 0}


def test_run_suite_is_serialisable_and_consistent():
    run = run_suite(task_ids=["missing_declared_dependency", "version_unsatisfied"])
    assert run["suite"] == SUITE
    assert run["variant"] == "full"
    assert run["metrics"]["task_count"] == 2
    assert run["metrics"]["top1_accuracy"] == 1.0
    json.dumps(run)  # must be JSON-serialisable
    assert len(run["tasks"]) == 2


def test_ablation_study_reports_a_delta_for_every_variant():
    study = run_ablations(task_ids=["version_unsatisfied", "manifest_disagreement"])
    assert study["baseline"] == "full"
    names = [item["variant"] for item in study["comparison"]]
    assert names == list(ABLATIONS)
    for item in study["comparison"]:
        assert "delta_top1_vs_full" in item
    full = next(item for item in study["comparison"] if item["variant"] == "full")
    assert full["delta_top1_vs_full"] == 0.0
    no_deps = next(item for item in study["comparison"] if item["variant"] == "no_dependency_analysis")
    assert no_deps["top1_accuracy"] < full["top1_accuracy"]


def test_suite_markdown_documents_metrics_and_limits():
    run = run_suite(task_ids=["missing_declared_dependency"])
    markdown = render_suite_markdown(run)
    assert "# RunFix benchmark report" in markdown
    assert "top-1 root-cause accuracy" in markdown
    assert "## Not measured" in markdown
    assert "synthetic" in markdown.lower()
    assert "not evidence of accuracy" in markdown


def test_ablation_markdown_explains_each_variant():
    study = run_ablations(task_ids=["version_unsatisfied"])
    markdown = render_ablation_markdown(study)
    assert "# RunFix ablation study" in markdown
    for variant in ABLATIONS:
        assert f"`{variant}`" in markdown


def test_write_benchmark_report_writes_both_formats(tmp_path):
    run = run_suite(task_ids=["missing_declared_dependency"])
    directory = write_benchmark_report(run, tmp_path / "suite")
    assert (directory / "benchmark.json").is_file()
    assert (directory / "benchmark.md").is_file()
    payload = json.loads((directory / "benchmark.json").read_text(encoding="utf-8"))
    assert payload["metrics"]["task_count"] == 1


def test_write_ablation_report_uses_its_own_filenames(tmp_path):
    study = run_ablations(task_ids=["version_unsatisfied"])
    directory = write_benchmark_report(study, tmp_path / "abl")
    assert (directory / "ablation.json").is_file()
    assert (directory / "ablation.md").is_file()


def test_cli_runs_a_single_task(tmp_path, capsys):
    from runfix.cli import main

    code = main(["bench", "--task", "missing_declared_dependency", "--out", str(tmp_path / "out")])
    assert code == 0
    output = capsys.readouterr().out
    assert "RunFix benchmark report" in output
    assert (tmp_path / "out" / "benchmark.md").is_file()


def test_cli_lists_tasks(tmp_path, capsys):
    from runfix.cli import main

    assert main(["bench", "--list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == len(TASKS)
    assert payload[0]["id"]


def test_cli_rejects_an_unknown_suite(tmp_path):
    from runfix.cli import main

    assert main(["bench", "--suite", "imaginary"]) == 2
