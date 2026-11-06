"""Regression tests for MCP SDK error translation and contract checks (0.6.1)."""

import sys
import types

import pytest

from runfix.mcp.server import (
    EXPECTED_TOOL_FAILURES,
    _load_sdk_tool_error,
    _translate_tool_failure,
)
from runfix.mcp import smoke as smoke_module
from runfix.mcp.smoke import _is_expected_install_denial


class FakeToolError(Exception):
    pass


def test_permission_denial_is_translated_to_a_tool_error():
    translated = _translate_tool_failure(PermissionError("Project-code execution is disabled."), FakeToolError)
    assert isinstance(translated, FakeToolError)
    assert "Project-code execution is disabled." in str(translated)


def test_unexpected_defect_is_not_translated():
    original = KeyError("bug")
    assert _translate_tool_failure(original, FakeToolError) is original


def test_translation_is_a_no_op_without_an_sdk():
    original = PermissionError("denied")
    assert _translate_tool_failure(original, None) is original


def test_every_anticipated_failure_is_translated():
    for failure in EXPECTED_TOOL_FAILURES:
        translated = _translate_tool_failure(failure("boom"), FakeToolError)
        assert isinstance(translated, FakeToolError)


def test_guarded_tool_raises_the_sdk_tool_error(tmp_path, monkeypatch):
    """The wrapper registered by create_server must surface the denial message."""
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
    monkeypatch.setattr("runfix.mcp.server._load_sdk_tool_error", lambda: FakeToolError)

    server = create_server(MCPPolicy(allowed_roots=[tmp_path], enable_execution=False))
    with pytest.raises(FakeToolError) as error:
        server.tools["run_project_command"](
            str(tmp_path), ["python", "--version"], confirm_execution=True
        )
    assert "Project-code execution is disabled" in str(error.value)


def test_load_sdk_tool_error_returns_none_for_a_stub_sdk(monkeypatch):
    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    monkeypatch.setitem(sys.modules, "mcp", mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.server", server_mod)
    assert _load_sdk_tool_error() is None


def test_install_denial_is_recognised_separately():
    class Result:
        is_error = True

        def __init__(self, text):
            self.content = [types.SimpleNamespace(text=text)]

    assert _is_expected_install_denial(
        Result("PermissionError: Dependency installation is disabled by the MCP server.")
    )
    assert not _is_expected_install_denial(
        Result("PermissionError: Project-code execution is disabled by the MCP server.")
    )


def test_smoke_expected_tools_match_the_documented_contract():
    from runfix.mcp.smoke import EXPECTED_TOOLS

    assert len(EXPECTED_TOOLS) == 22
    assert {"apply_patch", "rollback_patch", "get_patch_transaction"} <= EXPECTED_TOOLS
    assert {"start_task", "get_task_status", "stop_task", "read_task_log", "list_tasks"} <= EXPECTED_TOOLS


def test_smoke_denial_detection_accepts_translated_messages():
    class Result:
        is_error = True
        content = [types.SimpleNamespace(
            text="PermissionError: Project-code execution is disabled by the MCP server."
        )]

    assert smoke_module._is_expected_execution_denial(Result())
    assert smoke_module._is_expected_execution_denial(
        RuntimeError("Error executing tool x: PermissionError: Project-code execution is disabled")
    )
