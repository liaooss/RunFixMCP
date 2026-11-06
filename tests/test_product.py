"""Tests for the 1.0.0 product surface: audit trail, checkpoints, approvals and the console."""

from __future__ import annotations

import json
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from runfix import approvals
from runfix.agent.core import run_agent
from runfix.audit import PRIVILEGED_ACTIONS, audit, read_audit, verify_chain
from runfix.checkpoint import list_checkpoints, load_checkpoint, save_checkpoint
from runfix.patches import apply_patch


# --------------------------------------------------------------------------- audit


def test_audit_chain_starts_at_zero_and_verifies(tmp_path):
    first = audit(tmp_path, "task.start", target="t1", details={"command": "python train.py"})
    second = audit(tmp_path, "task.stop", target="t1", outcome="stopped")
    assert first["recorded"] and second["recorded"]
    assert first["entry"]["seq"] == 1
    assert first["entry"]["prev_hash"] == "0" * 64
    assert second["entry"]["prev_hash"] == first["entry"]["hash"]
    verification = verify_chain(tmp_path)
    assert verification["ok"] is True
    assert verification["checked"] == 2


def test_audit_detects_a_modified_entry(tmp_path):
    audit(tmp_path, "patch.apply", target="model.py")
    audit(tmp_path, "patch.rollback", target="tx1")
    audit_file = next((tmp_path / ".runfix" / "audit").glob("audit-*.jsonl"))
    lines = audit_file.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    entry["outcome"] = "tampered"
    lines[0] = json.dumps(entry)
    audit_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = verify_chain(tmp_path)
    assert result["ok"] is False
    assert "hash mismatch" in result["reason"]


def test_audit_detects_a_removed_entry(tmp_path):
    for index in range(3):
        audit(tmp_path, "task.start", target=f"t{index}")
    audit_file = next((tmp_path / ".runfix" / "audit").glob("audit-*.jsonl"))
    lines = audit_file.read_text(encoding="utf-8").splitlines()
    audit_file.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")

    result = verify_chain(tmp_path)
    assert result["ok"] is False
    assert "prev_hash" in result["reason"] or "sequence" in result["reason"]


def test_audit_redacts_secrets(tmp_path):
    audit(tmp_path, "reproduce.run", details={"env": "API_KEY=sk-12345678901234567890"})
    entries = read_audit(tmp_path)
    assert "12345678901234567890" not in json.dumps(entries)
    assert "REDACTED" in json.dumps(entries)


def test_audit_filters_and_limits(tmp_path):
    for index in range(5):
        audit(tmp_path, "patch.apply", target=f"f{index}")
    audit(tmp_path, "task.stop", target="t")
    assert len(read_audit(tmp_path, limit=2)) == 2
    assert [item["target"] for item in read_audit(tmp_path, action="task.stop")] == ["t"]


def test_audit_is_recorded_by_patch_operations(tmp_path):
    source = tmp_path / "model.py"
    source.write_text("value = 1\n", encoding="utf-8")
    patch = (
        "diff --git a/model.py b/model.py\n--- a/model.py\n+++ b/model.py\n"
        "@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    )
    applied = apply_patch(tmp_path, patch, confirmed=True)
    actions = [item["action"] for item in read_audit(tmp_path)]
    assert "patch.apply" in actions
    assert applied["applied"] is True
    assert "patch.apply" in PRIVILEGED_ACTIONS


def test_audit_reports_storage_failure_without_raising(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("runfix.audit._audit_root", boom)
    result = audit(tmp_path, "task.start")
    assert result["recorded"] is False
    assert "audit warning" in result["error"].lower()


# ---------------------------------------------------------------------- checkpoints


def test_checkpoint_roundtrip_and_listing(tmp_path):
    save_checkpoint(tmp_path, "run-abc", {"status": "running", "step": 2, "goal": "g"})
    loaded = load_checkpoint(tmp_path, "run-abc")
    assert loaded["step"] == 2
    assert loaded["run_id"] == "run-abc"
    listing = list_checkpoints(tmp_path)
    assert listing[0]["run_id"] == "run-abc"
    assert listing[0]["status"] == "running"


def test_checkpoint_rejects_unsafe_run_ids(tmp_path):
    with pytest.raises(ValueError):
        save_checkpoint(tmp_path, "../escape", {})
    with pytest.raises(ValueError):
        load_checkpoint(tmp_path, "a" * 100)


def test_agent_writes_a_finished_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "runfix.agent.tools.diagnose_environment",
        lambda _python: {"python_version": "3.11.0", "packages": {}, "torch": {"installed": False},
                         "issues": [], "nvidia_smi": {"available": False}},
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_agent(tmp_path, max_steps=8, allow_project_code=True)
    checkpoint_id = result["checkpoint_id"]
    record = load_checkpoint(tmp_path, checkpoint_id)
    assert record["status"] == "finished"
    assert record["state"]["tool_counts"]["run_tests"] == 1
    assert record["report"]["outcome"] == "verified"


def test_agent_resumes_from_a_checkpoint_without_replaying_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "runfix.agent.tools.diagnose_environment",
        lambda _python: {"python_version": "3.11.0", "packages": {}, "torch": {"installed": False},
                         "issues": [], "nvidia_smi": {"available": False}},
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    observations = {
        "scan": {"file_count": 2, "python_file_count": 1},
        "environment": {"python_version": "3.11.0", "torch": {"installed": False}},
        "static_validation": {"overall_status": "passed"},
        "runtime": None,
    }
    state = {
        "goal": "resume test",
        "project_path": str(tmp_path),
        "python_executable": sys.executable,
        "observations": observations,
        "tool_counts": {"scan_project": 1, "diagnose_environment": 1, "static_validation": 1},
        "tool_history": [{"step": 1, "tool": "scan_project", "status": "completed", "summary": ""},
                         {"step": 2, "tool": "diagnose_environment", "status": "completed", "summary": ""},
                         {"step": 3, "tool": "static_validation", "status": "completed", "summary": ""}],
        "last_error": "",
    }
    events = [{"step": 1, "tool": "scan_project", "status": "completed", "rationale": "r"},
              {"step": 2, "tool": "diagnose_environment", "status": "completed", "rationale": "r"},
              {"step": 3, "tool": "static_validation", "status": "completed", "rationale": "r"}]
    save_checkpoint(tmp_path, "crashed-run", {
        "run_key": "crashed-run", "goal": "resume test", "project_path": str(tmp_path),
        "python_executable": sys.executable,
        "options": {"allow_project_code": True, "allow_write": False, "allow_install": False,
                    "use_llm": False, "command": None, "timeout": 60, "install_timeout": 60,
                    "python": sys.executable},
        "planner_mode": "heuristic_tool_agent", "step": 3, "status": "running",
        "events": events, "state": state,
        "box": {"python": sys.executable, "repair_round": 0},
    })

    result = run_agent(tmp_path, max_steps=8, resume_run_id="crashed-run")
    assert result["outcome"] == "verified"
    tools = [event["tool"] for event in result["tool_history"]]
    assert tools[:3] == ["scan_project", "diagnose_environment", "static_validation"]
    assert tools.count("scan_project") == 1, "a resumed run must not replay completed steps"
    assert "run_tests" in tools
    assert result["checkpoint_id"] == "crashed-run"


def test_resume_of_an_unknown_run_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_agent(tmp_path, resume_run_id="does-not-exist")


def test_resume_cannot_escalate_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "runfix.agent.tools.diagnose_environment",
        lambda _python: {"python_version": "3.11.0", "packages": {}, "torch": {"installed": False},
                         "issues": [], "nvidia_smi": {"available": False}},
    )
    save_checkpoint(tmp_path, "limited", {
        "run_key": "limited", "goal": "g", "project_path": str(tmp_path),
        "python_executable": sys.executable,
        "options": {"allow_project_code": False, "allow_write": False, "allow_install": False,
                    "use_llm": False, "command": None, "timeout": 60, "install_timeout": 60,
                    "python": sys.executable},
        "step": 0, "status": "running", "events": [], "state": {
            "goal": "g", "project_path": str(tmp_path), "python_executable": sys.executable,
            "observations": {}, "tool_counts": {}, "tool_history": [], "last_error": ""},
        "box": {"python": sys.executable},
    })
    result = run_agent(tmp_path, max_steps=6, allow_project_code=True, resume_run_id="limited")
    assert result["permissions"]["allow_project_code"] is False
    assert all(event["tool"] not in {"run_tests", "apply_patch"} for event in result["tool_history"])


# ------------------------------------------------------------------------ approvals


def test_grant_and_consume_decrements_uses(tmp_path):
    approvals.grant(tmp_path, "patch.apply", uses=2, note="reviewed")
    first = approvals.consume(tmp_path, "patch.apply")
    second = approvals.consume(tmp_path, "patch.apply")
    third = approvals.consume(tmp_path, "patch.apply")
    assert first["allowed"] and second["allowed"]
    assert third["allowed"] is False
    assert "No valid approval" in third["reason"]


def test_expired_grant_is_refused(tmp_path):
    approvals.grant(tmp_path, "task.start", uses=1, ttl_minutes=1)
    path = tmp_path / ".runfix" / "approvals.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["grants"][0]["expires_at"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert approvals.consume(tmp_path, "task.start")["allowed"] is False


def test_revoke_removes_the_grant(tmp_path):
    approvals.grant(tmp_path, "agent.run", uses=3)
    approvals.revoke(tmp_path, "agent.run")
    assert approvals.consume(tmp_path, "agent.run")["allowed"] is False


def test_policy_marks_which_actions_need_approval(tmp_path):
    approvals.save_policy(tmp_path, ["patch.apply", "reproduce.run"])
    assert approvals.requires_approval(tmp_path, "patch.apply") is True
    assert approvals.requires_approval(tmp_path, "task.start") is False
    with pytest.raises(ValueError):
        approvals.save_policy(tmp_path, ["nope"])
    with pytest.raises(ValueError):
        approvals.grant(tmp_path, "nope")


def test_approval_actions_are_audited(tmp_path):
    approvals.grant(tmp_path, "patch.apply")
    approvals.consume(tmp_path, "patch.apply")
    actions = [item["action"] for item in read_audit(tmp_path)]
    assert "approval.grant" in actions
    assert "approval.consume" in actions
    assert verify_chain(tmp_path)["ok"] is True


def test_mcp_requires_an_approval_when_enabled(tmp_path, monkeypatch):
    from runfix.mcp.server import MCPPolicy, create_server

    class FakeFastMCP:
        def __init__(self, name, **kwargs):
            self.name = name
            self.instructions = kwargs.get("instructions")
            self.tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn

            return register

    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    server_mod.MCPServer = FakeFastMCP
    monkeypatch.setitem(sys.modules, "mcp", mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.server", server_mod)

    project = tmp_path / "repo"
    project.mkdir()
    source = project / "model.py"
    source.write_text("value = 1\n", encoding="utf-8")
    patch = (
        "diff --git a/model.py b/model.py\n--- a/model.py\n+++ b/model.py\n"
        "@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    )
    server = create_server(MCPPolicy(allowed_roots=[tmp_path], enable_write=True,
                                     require_approval=True))

    with pytest.raises(PermissionError) as error:
        server.tools["apply_patch"](str(project), patch, confirm_write=True)
    assert "No valid approval" in str(error.value)
    assert source.read_text(encoding="utf-8") == "value = 1\n"

    approvals.grant(project, "patch.apply")
    applied = server.tools["apply_patch"](str(project), patch, confirm_write=True)
    assert applied["applied"] is True
    assert source.read_text(encoding="utf-8") == "value = 2\n"


# ----------------------------------------------------------------------------- web


@pytest.fixture
def web_server(tmp_path):
    from runfix.web import server as web

    state = tmp_path / "state"
    state.mkdir()
    web.Handler.state = state
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", state
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url: str):
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(url: str, payload: dict):
    data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_web_health_reports_the_package_version(web_server):
    base, state = web_server
    payload = _get(base + "/api/health")
    from runfix import __version__
    assert payload["version"] == __version__
    assert payload["state_dir"] == str(state)


def test_web_registers_and_lists_projects(web_server, tmp_path):
    base, state = web_server
    project = tmp_path / "paper"
    project.mkdir()
    assert _post(base + "/api/projects", {"path": str(project)})["count"] == 1
    listed = _get(base + "/api/projects")
    assert listed["count"] == 1
    assert listed["projects"][0]["path"] == str(project.resolve())
    assert (state / "projects.json").is_file()

    # Registering again is idempotent.
    assert _post(base + "/api/projects", {"path": str(project)})["count"] == 1
    assert _post(base + "/api/projects/remove", {"path": str(project)})["count"] == 0


def test_web_rejects_a_missing_project_directory(web_server, tmp_path):
    base, _ = web_server
    with pytest.raises(urllib.error.HTTPError) as error:
        _post(base + "/api/projects", {"path": str(tmp_path / "nope")})
    assert error.value.code == 400


def test_web_reports_tasks_and_audit_for_a_project(web_server, tmp_path):
    base, _ = web_server
    project = tmp_path / "paper"
    tasks = project / ".runfix" / "tasks" / ("a" * 32)
    tasks.mkdir(parents=True)
    (tasks / "task.json").write_text(json.dumps({
        "task_id": "a" * 32, "status": "completed", "returncode": 0,
        "created_at": "2026-01-01T00:00:00+00:00", "label": "seed 1",
        "command": ["python", "train.py"],
    }), encoding="utf-8")
    audit(project, "agent.run", target="run-1", outcome="verified")

    listed = _get(f"{base}/api/tasks?project={project}")
    assert listed["count"] == 1
    assert listed["tasks"][0]["label"] == "seed 1"

    entries = _get(f"{base}/api/audit?project={project}")
    assert entries["count"] >= 1
    assert entries["entries"][-1]["action"] == "agent.run"

    summary = _get(base + "/api/projects")  # empty registry, still valid
    assert summary["count"] == 0


def test_web_project_summary_counts_tasks_and_audit(web_server, tmp_path):
    base, _ = web_server
    project = tmp_path / "paper"
    task_dir = project / ".runfix" / "tasks" / ("b" * 32)
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(json.dumps({
        "task_id": "b" * 32, "status": "running", "created_at": "2026-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    audit(project, "task.start", target="b" * 32)
    _post(base + "/api/projects", {"path": str(project)})

    project_summary = _get(base + "/api/projects")["projects"][0]
    assert project_summary["tasks"] == 1
    assert project_summary["last_task"]["status"] == "running"
    assert project_summary["audit_entries"] == 1


def test_web_only_binds_to_loopback(tmp_path):
    from runfix.web.server import serve

    with pytest.raises(ValueError):
        serve("0.0.0.0", 8765, str(tmp_path))


def test_web_rejects_non_json_payloads(web_server):
    base, _ = web_server
    request = urllib.request.Request(base + "/api/projects", data=b"{not json",
                                     method="POST", headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=10)
    assert error.value.code == 500
