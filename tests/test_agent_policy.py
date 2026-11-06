"""Tests for the observation-driven offline agent policy (0.7.0)."""

from __future__ import annotations

import sys

import pytest

from runfix.agent import policy
from runfix.agent.core import run_agent


def _state(**observations):
    return {"observations": observations, "tool_counts": {}, "tool_history": []}


def test_discovery_steps_come_first():
    decision = policy.decide(_state(), [
        "scan_project", "diagnose_environment", "static_validation", "finish",
    ])
    assert decision["tool"] == "scan_project"


def test_missing_dependency_with_install_permission_builds_the_environment():
    state = _state(
        scan={}, environment={}, static_validation={},
        runtime={"returncode": 1, "timed_out": False},
        analysis={"category": "missing_dependency_or_import_path"},
    )
    decision = policy.decide(state, ["create_isolated_environment", "finish"])
    assert decision["tool"] == "create_isolated_environment"
    assert decision["strategy"] == "environment_repair"


def test_missing_dependency_without_install_permission_stops_and_says_why():
    state = _state(
        scan={}, environment={}, static_validation={},
        runtime={"returncode": 1, "timed_out": False},
        analysis={"category": "missing_dependency_or_import_path"},
    )
    decision = policy.decide(state, ["finish"])
    assert decision["tool"] == "finish"
    assert decision["stop_reason"] == "needs_install_permission"
    assert "not authorized" in decision["rationale"]


def test_gpu_out_of_memory_finishes_with_a_configuration_recommendation():
    state = _state(
        scan={}, environment={}, static_validation={},
        runtime={"returncode": 1, "timed_out": False},
        analysis={"category": "gpu_out_of_memory"},
    )
    decision = policy.decide(state, ["finish"])
    assert decision["tool"] == "finish"
    assert decision["stop_reason"] == "needs_configuration_change"


def test_patchable_failure_prefers_patch_validation():
    state = _state(
        scan={}, environment={}, static_validation={},
        runtime={"returncode": 1, "timed_out": False},
        analysis={"category": "python_syntax"},
    )
    decision = policy.decide(state, ["validate_patch", "finish"])
    assert decision["tool"] == "validate_patch"
    assert decision["strategy"] == "patch_for_python_syntax"


def test_patchable_failure_without_write_permission_reports_it():
    state = _state(
        scan={}, environment={}, static_validation={},
        runtime={"returncode": 1, "timed_out": False},
        analysis={"category": "test_assertion_failure"},
    )
    decision = policy.decide(state, ["finish"])
    assert decision["tool"] == "finish"
    assert decision["stop_reason"] == "needs_write_permission_or_patch"


def test_passing_runtime_finishes_as_verified():
    state = _state(
        scan={}, environment={}, static_validation={},
        runtime={"returncode": 0, "timed_out": False},
    )
    decision = policy.decide(state, ["finish"])
    assert decision["tool"] == "finish"
    assert decision["stop_reason"] == "verified_baseline"


def test_repeated_failure_reanalyses_instead_of_repeating_the_patch():
    state = _state(
        scan={}, environment={}, static_validation={},
        runtime={"returncode": 1, "timed_out": False},
        analysis=None,
        previous_analysis={"category": "test_assertion_failure"},
    )
    decision = policy.decide(state, ["analyze_failure", "validate_patch", "finish"])
    assert decision["tool"] == "analyze_failure"
    assert "re-analyze" in decision["rationale"]


def test_unclassified_failure_never_guesses_a_repair():
    state = _state(
        scan={}, environment={}, static_validation={},
        runtime={"returncode": 1, "timed_out": False},
        analysis={"category": "unclassified_runtime_error"},
    )
    decision = policy.decide(state, ["finish"])
    assert decision["tool"] == "finish"
    assert decision["stop_reason"] == "needs_more_evidence"


def test_summarize_decision_is_compact():
    text = policy.summarize_decision({
        "planner": "policy", "tool": "analyze_failure",
        "rationale": "because", "strategy": "x",
    })
    assert text.startswith("policy:analyze_failure [x]")


def test_agent_on_a_passing_project_verifies(tmp_path, monkeypatch):
    import runfix.agent.tools as tools

    monkeypatch.setattr(
        tools, "diagnose_environment",
        lambda _python: {"python_version": "3.11.0", "packages": {}, "torch": {"installed": False},
                         "issues": [], "nvidia_smi": {"available": False}},
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_agent(tmp_path, max_steps=10, allow_project_code=True)
    assert result["outcome"] == "verified"
    assert result["stop_reason"] == "verified_baseline"
    names = [event["tool"] for event in result["tool_history"]]
    assert names[0] == "scan_project"
    assert "run_tests" in names


def test_agent_stops_with_a_reason_instead_of_a_fixed_sequence(tmp_path, monkeypatch):
    """A failing suite without install permission must stop and explain, not loop."""
    import runfix.agent.tools as tools

    monkeypatch.setattr(
        tools, "diagnose_environment",
        lambda _python: {"python_version": "3.11.0", "packages": {}, "torch": {"installed": False},
                         "issues": [], "nvidia_smi": {"available": False}},
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_fail.py").write_text(
        "def test_fail():\n    assert 1 == 2\n", encoding="utf-8"
    )
    result = run_agent(tmp_path, max_steps=10, allow_project_code=True)
    assert result["stop_reason"] in {
        "no_evidence_backed_repair", "needs_write_permission_or_patch",
        "needs_more_evidence", "needs_install_permission",
    }
    analysis = result["observations"].get("analysis") or {}
    assert analysis.get("category") == "test_assertion_failure"
