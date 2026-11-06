"""End-to-end reproduction pipeline and verdict tests (0.7.0)."""

from __future__ import annotations

import json
import sys

import pytest

from runfix.repro.pipeline import reproduce
from runfix.repro.report import build_reproduction_report, derive_verdict, render_reproduction_markdown
from runfix.repro.runner import run_reproduction


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_plan_only_never_executes_and_reports_the_plan(paper_project):
    result = reproduce(paper_project, plan_only=True)
    assert result["verdict"] == "plan_ready"
    assert result["run"] is None
    assert "Plan-only" in result["execution"]["blocked_reason"]
    report_dir = paper_project / ".runfix" / "repro" / "reports"
    assert report_dir.is_dir()
    markdown = next(report_dir.glob("*/reproduction_report.md")).read_text(encoding="utf-8")
    assert "# RunFix reproduction report" in markdown
    assert "## Reproduction plan" in markdown


def test_unauthorized_reproduction_stops_before_running(paper_project):
    result = reproduce(paper_project, command=_python("print('should not run')"), allow_execute=False)
    assert result["run"] is None
    assert result["verdict"] == "plan_ready"


def test_authorized_run_with_metrics_is_not_yet_a_reproduction(paper_project):
    result = reproduce(paper_project, allow_execute=True)
    run = result["run"]
    assert run is not None
    assert run["status"] == "completed"
    assert run["metrics"]["final"]["accuracy"] == pytest.approx(0.8)
    assert result["verdict"] == "metrics_recorded"
    assert any("No expected metric values" in reason for reason in result["verdict_reasons"])


def test_declared_expectations_can_produce_a_reproduction_verdict(paper_project):
    result = reproduce(paper_project, allow_execute=True, expected_metrics={"accuracy": 0.8})
    assert result["verdict"] == "reproduced"
    comparison = result["run"]["metric_comparison"]
    assert comparison["all_match"] is True


def test_a_mismatched_metric_blocks_the_reproduction_claim(paper_project):
    result = reproduce(paper_project, allow_execute=True, expected_metrics={"accuracy": 0.95})
    assert result["verdict"] == "metrics_recorded"
    assert any("accuracy" in reason for reason in result["verdict_reasons"])


def test_zero_exit_without_evidence_is_not_completion(paper_project):
    result = reproduce(paper_project, command=_python("print('nothing useful')"), allow_execute=True)
    assert result["run"]["status"] == "process_exited"
    assert result["verdict"] == "executed"
    assert any("zero exit code" in reason.lower() for reason in result["verdict_reasons"])


def test_failing_run_is_reported_with_its_exit_code(paper_project):
    result = reproduce(paper_project, command=_python("import sys; sys.exit(4)"), allow_execute=True)
    assert result["run"]["status"] == "failed"
    assert result["run"]["returncode"] == 4
    assert result["verdict"] == "executed"
    assert any("code 4" in reason for reason in result["verdict_reasons"])


def test_completion_regex_is_honoured(paper_project):
    result = reproduce(
        paper_project,
        command=_python("print('ALL DONE FOR THIS EXPERIMENT')"),
        allow_execute=True,
        completion_regex=r"ALL DONE",
    )
    assert result["run"]["status"] == "completed"
    assert result["run"]["completion_evidence"]["completion_regex_match"] == "ALL DONE"
    assert result["verdict"] == "completed"


def test_timeout_is_clamped_to_a_safe_minimum(paper_project):
    record = run_reproduction(
        paper_project,
        command=_python("print('training complete')"),
        timeout=1,
    )
    assert record["timeout_seconds"] == 30


def test_artifacts_created_by_the_run_are_recorded(paper_project):
    code = (
        "import os; os.makedirs('outputs', exist_ok=True);"
        "open('outputs/metrics.json','w').write('{\"accuracy\": 0.8}');"
        "print('final test accuracy: 0.8'); print('training complete')"
    )
    result = reproduce(paper_project, command=_python(code), allow_execute=True)
    paths = {item["path"] for item in result["run"]["artifacts"]}
    assert "outputs/metrics.json" in paths


def test_fingerprint_records_git_and_hardware(paper_project):
    result = reproduce(paper_project, command=_python("print('training complete')"), allow_execute=True)
    fingerprint = result["run"]["fingerprint"]
    assert "dependency_manifests" in fingerprint
    assert "requirements.txt" in fingerprint["dependency_manifests"]
    hardware = result["run"]["hardware"]
    assert hardware["python_version"]
    assert hardware["python_executable"]


def test_run_directory_contains_the_log_and_metadata(paper_project):
    result = reproduce(paper_project, command=_python("print('training complete')"), allow_execute=True)
    run_path = paper_project / result["run"]["log"]["path"]
    assert run_path.is_file()
    assert "training complete" in run_path.read_text(encoding="utf-8")
    metadata = json.loads((run_path.parent / "run.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "completed"


def test_report_markdown_documents_the_verdict_ladder(paper_project):
    result = reproduce(paper_project, plan_only=True)
    markdown = render_reproduction_markdown(result)
    assert "Verdict ladder" in markdown
    assert "| reproduced | no |" in markdown
    assert "Limitations" in markdown


def test_dirty_repository_blocks_the_reproduction_rung():
    plan = {"steps": [], "blockers": []}
    run = {
        "status": "completed",
        "returncode": 0,
        "metrics": {"point_count": 3, "final": {"accuracy": 0.8}},
        "metric_comparison": {"all_match": True},
        "fingerprint": {"git": {"dirty": True}},
    }
    verdict, reasons = derive_verdict(plan, run)
    assert verdict == "metrics_matched"
    assert any("uncommitted" in reason for reason in reasons)


def test_clean_repository_with_matching_metrics_reproduces():
    plan = {"steps": [], "blockers": []}
    run = {
        "status": "completed",
        "returncode": 0,
        "metrics": {"point_count": 3, "final": {"accuracy": 0.8}},
        "metric_comparison": {"all_match": True},
        "fingerprint": {"git": {"dirty": False, "commit": "abc123"}},
    }
    verdict, reasons = derive_verdict(plan, run)
    assert verdict == "reproduced"
    assert reasons == []


def test_project_without_version_control_cannot_claim_reproduction():
    plan = {"steps": [], "blockers": []}
    run = {
        "status": "completed",
        "returncode": 0,
        "metrics": {"point_count": 3, "final": {"accuracy": 0.8}},
        "metric_comparison": {"all_match": True},
        "fingerprint": {"git": {"dirty": False, "commit": None}},
    }
    verdict, reasons = derive_verdict(plan, run)
    assert verdict == "metrics_matched"
    assert any("not under version control" in reason for reason in reasons)


def test_report_without_plan_or_run_is_only_inspected(tmp_path):
    report = build_reproduction_report(tmp_path)
    assert report["verdict"] == "inspected"
    assert report["verdict_reasons"]
