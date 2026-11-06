"""Regression tests for the report artifact formats (0.6.1)."""

import json

from runfix.common import save_report, to_markdown


def _sample_report():
    return {
        "run_id": "run-1",
        "project_path": r"C:\research\paper-code",
        "overall_status": "warning",
        "summary": "The project was inspected.",
        "stages": [{"id": "C0", "status": "passed", "details": "ok"}],
        "analysis": {
            "category": "missing_dependency_or_import_path",
            "confidence": 0.78,
            "root_cause": "torch is missing",
            "recommended_actions": ["Install torch", "Re-run the tests"],
        },
        "agent_mode": True,
        "goal": "Diagnose",
        "outcome": "diagnosed",
        "stop_reason": "planner_finished",
        "repair_rounds_used": 1,
        "tool_history": [
            {"step": 1, "tool": "scan_project", "status": "completed", "rationale": "structure"}
        ],
        "permissions": {"allow_project_code": True, "allow_write": False, "use_llm": False},
    }


def test_markdown_report_uses_real_newlines():
    markdown = to_markdown(_sample_report())
    assert "\\n" not in markdown, "report.md must not contain a literal backslash-n"
    assert markdown.count("\n") > 10
    assert markdown.startswith("# RunFix report\n")
    assert "\n## Root-cause analysis\n" in markdown
    assert "\n### Tool history\n" in markdown


def test_saved_report_markdown_is_a_real_document(tmp_path):
    report = _sample_report()
    output = save_report(tmp_path, report, "log line\n")
    text = (output / "report.md").read_text(encoding="utf-8")
    assert text.count("\n") > 10
    assert "\\n" not in text
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[0] == "# RunFix report"
    assert any(line.startswith("- **C0 / passed**") for line in lines)

    payload = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert payload["run_id"] == report["run_id"]
    assert (output / "execution.log").read_text(encoding="utf-8") == "log line\n"


def test_pytest_output_concatenation_keeps_real_newlines(tmp_path):
    """The agent used to join stdout and stderr with a literal backslash-n."""
    import importlib.util
    import sys

    import pytest

    from runfix.agent.tools import ToolBox

    if importlib.util.find_spec("pytest") is None:
        pytest.skip("pytest is required to exercise the test runner")

    box = ToolBox(tmp_path, sys.executable, {"allow_project_code": True, "timeout": 60})
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    result = box.execute("run_tests", {"observations": {}})
    assert result["passed"] is True
    assert "\\n" not in box.latest_log
