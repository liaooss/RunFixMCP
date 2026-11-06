from types import SimpleNamespace

from runfix.mcp.smoke import (
    EXPECTED_TOOLS,
    _is_tool_error,
    _is_expected_execution_denial,
    _structured_payload,
)


def test_smoke_test_covers_all_registered_mcp_tools():
    assert len(EXPECTED_TOOLS) == 20
    assert "get_server_capabilities" in EXPECTED_TOOLS
    assert "run_agent" in EXPECTED_TOOLS
    assert "get_latest_report" in EXPECTED_TOOLS
    assert "rollback_patch" in EXPECTED_TOOLS
    assert "get_patch_transaction" in EXPECTED_TOOLS
    assert "create_isolated_environment" in EXPECTED_TOOLS
    assert "start_task" in EXPECTED_TOOLS
    assert "get_task_status" in EXPECTED_TOOLS
    assert "stop_task" in EXPECTED_TOOLS
    assert "read_task_log" in EXPECTED_TOOLS
    assert "list_tasks" in EXPECTED_TOOLS


def test_structured_payload_supports_sdk_v2_field_name():
    result = SimpleNamespace(
        structured_content={"version": "0.6.0", "sdk_api": "MCPServer v2"},
        content=[],
    )
    assert _structured_payload(result)["version"] == "0.6.0"


def test_structured_payload_supports_sdk_v1_field_name():
    result = SimpleNamespace(
        structuredContent={"version": "0.6.0", "sdk_api": "FastMCP v1"},
        content=[],
    )
    assert _structured_payload(result)["sdk_api"] == "FastMCP v1"


def test_structured_payload_supports_json_text_fallback():
    result = SimpleNamespace(
        structured_content=None,
        structuredContent=None,
        content=[SimpleNamespace(text='{"file_count": 2, "python_file_count": 1}')],
    )
    assert _structured_payload(result)["file_count"] == 2


def test_error_flag_supports_both_sdk_generations():
    assert _is_tool_error(SimpleNamespace(is_error=True))
    assert _is_tool_error(SimpleNamespace(isError=True))
    assert not _is_tool_error(SimpleNamespace(is_error=False, isError=False))


def test_mcp_stdio_end_to_end_when_sdk_is_installed():
    import importlib.util
    import subprocess
    import sys
    from pathlib import Path
    import pytest

    if importlib.util.find_spec("mcp") is None:
        pytest.skip("Install the optional MCP SDK extra to run the stdio protocol test.")

    project_root = Path(__file__).resolve().parents[1]
    script = project_root / "scripts" / "check_mcp_connection.py"
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )


def test_execution_denial_recognizes_sdk_result_and_exception_styles():
    result = SimpleNamespace(
        is_error=True,
        content=[SimpleNamespace(text="Project-code execution is disabled by the MCP server.")],
    )
    assert _is_expected_execution_denial(result)
    assert _is_expected_execution_denial(PermissionError("Project-code execution is disabled"))
    assert not _is_expected_execution_denial(RuntimeError("unrelated transport failure"))


def test_expected_tool_set_contains_task_and_environment_tools():
    assert {
        "create_isolated_environment",
        "start_task",
        "get_task_status",
        "stop_task",
        "read_task_log",
        "list_tasks",
    }.issubset(EXPECTED_TOOLS)
