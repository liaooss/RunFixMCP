"""Regression tests for the C0-C5 evidence audit and pytest-style traceback context (0.6.1)."""

import pytest

from runfix.analyzer import _contexts, analyze_error, classify
from runfix.validation import audit_stages, validate_project


def test_audit_accepts_well_formed_stages():
    stages = [
        {"id": "C0", "status": "passed", "evidence": {"count": 1}},
        {"id": "C1", "status": "skipped", "evidence": {"reason": "not requested"}},
        {"id": "C2", "status": "warning", "evidence": {"detail": "cpu only"}},
    ]
    assert audit_stages(stages) == []


def test_audit_rejects_a_passed_stage_without_evidence():
    problems = audit_stages([{"id": "C0", "status": "passed", "evidence": None}])
    assert problems == ["C0 reported passed without structured evidence"]


def test_audit_rejects_an_empty_evidence_mapping():
    problems = audit_stages([{"id": "C1", "status": "failed", "evidence": {}}])
    assert problems == ["C1 reported failed without structured evidence"]


def test_audit_rejects_an_unknown_status():
    problems = audit_stages([{"id": "C2", "status": "maybe", "evidence": {"x": 1}}])
    assert problems == ["C2 reported the unknown status 'maybe'"]


def test_validate_project_evidence_audit_reports_success(tmp_path):
    (tmp_path / "main.py").write_text("value = 1\n", encoding="utf-8")
    result = validate_project(tmp_path)
    audit_stage = next(stage for stage in result["stages"] if stage["id"] == "C5")
    assert audit_stage["status"] == "passed"
    assert audit_stage["evidence"]["audit_errors"] == []
    assert audit_stage["evidence"]["checked_stage_count"] >= 5


def test_pytest_style_traceback_yields_source_context(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    source = tests_dir / "test_model.py"
    source.write_text(
        "from model import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    (tmp_path / "model.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    traceback_text = (
        "=================================== FAILURES ===================================\n"
        "__________________________________ test_add ___________________________________\n\n"
        "    def test_add():\n"
        ">       assert add(2, 3) == 5\n"
        "E       assert -1 == 5\n\n"
        f"{source}:4: AssertionError\n"
        "=========================== short test summary info ===========================\n"
        "FAILED tests/test_model.py::test_add - assert -1 == 5\n"
    )
    contexts = _contexts(tmp_path, traceback_text)
    assert contexts, "a pytest-style traceback must produce source context"
    assert contexts[0]["file"] == "tests/test_model.py"
    assert contexts[0]["line"] == 4

    analysis = analyze_error(tmp_path, traceback_text)
    assert analysis["category"] == "test_assertion_failure"
    assert analysis["evidence"]["source_context"]


def test_cpython_frame_still_produces_context(tmp_path):
    source = tmp_path / "train.py"
    source.write_text("import torch\nraise RuntimeError('boom')\n", encoding="utf-8")
    traceback_text = (
        'Traceback (most recent call last):\n'
        f'  File "{source}", line 2, in <module>\n'
        "    raise RuntimeError('boom')\n"
        "RuntimeError: boom\n"
    )
    contexts = _contexts(tmp_path, traceback_text)
    assert contexts[0]["file"] == "train.py"
    assert contexts[0]["function"] == "<module>"


def test_frames_outside_the_project_are_ignored(tmp_path):
    outside = tmp_path.parent / "outside_module.py"
    traceback_text = f'  File "{outside}", line 3, in run\nValueError: x\n'
    assert _contexts(tmp_path, traceback_text) == []


def test_assertion_classification_prefers_the_specific_rule():
    result = classify("E       assert -1 == 5\nAssertionError")
    assert result["category"] == "test_assertion_failure"
    assert result["confidence"] >= 0.7
