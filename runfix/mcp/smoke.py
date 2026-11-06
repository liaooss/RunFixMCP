from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from .. import __version__

EXPECTED_TOOLS = {
    "get_server_capabilities",
    "scan_project",
    "diagnose_environment",
    "validate_project",
    "analyze_failure",
    "get_source_context",
    "validate_patch",
    "apply_patch",
    "rollback_patch",
    "get_patch_transaction",
    "run_tests",
    "run_project_command",
    "run_agent",
    "get_latest_report",
    "create_isolated_environment",
    "start_task", "get_task_status", "stop_task", "read_task_log", "list_tasks",
    "plan_reproduction", "run_reproduction", "explain_root_causes",
}


def _load_sdk():
    """Load the installed MCP SDK and return its major API style."""
    try:
        from mcp import Client, StdioServerParameters
        return 2, Client, StdioServerParameters
    except ImportError:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            return 1, (ClientSession, stdio_client), StdioServerParameters
        except ImportError as exc:
            raise RuntimeError(
                'MCP SDK is missing. Install it with: '
                'python -m pip install -e ".[mcp]"'
            ) from exc


def _structured_payload(result: Any) -> dict[str, Any]:
    for attr in ("structured_content", "structuredContent"):
        value = getattr(result, attr, None)
        if isinstance(value, dict):
            return value

    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value

    raise RuntimeError(
        "The MCP tool returned no structured JSON object. "
        f"Result type: {type(result).__name__}"
    )


def _is_tool_error(result: Any) -> bool:
    return bool(
        getattr(result, "is_error", False)
        or getattr(result, "isError", False)
    )


def _is_expected_execution_denial(value: Any) -> bool:
    """Recognize the explicit server-side policy denial across SDK result styles."""
    if isinstance(value, BaseException):
        message = str(value)
    else:
        message = " ".join(
            getattr(block, "text", "") or ""
            for block in (getattr(value, "content", []) or [])
        )
        if not _is_tool_error(value):
            return False
    lowered = message.lower()
    return (
        "project-code execution is disabled" in lowered
        or "confirm_execution=true" in lowered
        or "execution permission" in lowered
    )


def _is_expected_install_denial(value: Any) -> bool:
    """Recognize the dependency-install gate, which is checked after the execution gate."""
    if isinstance(value, BaseException):
        message = str(value)
    else:
        message = " ".join(
            getattr(block, "text", "") or ""
            for block in (getattr(value, "content", []) or [])
        )
        if not _is_tool_error(value):
            return False
    return "dependency installation is disabled" in message.lower()


def _bootstrap_arguments(project: Path, *flags: str) -> list[str]:
    """Child argv that can import this source checkout even before installation."""
    repo_root = Path(__file__).resolve().parents[2]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{str(repo_root)!r});"
        "runpy.run_module('runfix.mcp.server',run_name='__main__')"
    )
    return ["-c", bootstrap, "--allowed-root", str(project), *flags]


async def _call(client: Any, name: str, arguments: dict[str, Any]) -> Any:
    """Call a tool and return the raw result object, never raising for tool errors."""
    try:
        return await client.call_tool(name, arguments=arguments)
    except Exception as exc:  # transport-level or SDK-level wrapper
        return exc


async def _exercise_gated_client(client: Any, project: Path) -> dict[str, Any]:
    """Read-only server: verify discovery, version and every permission gate."""
    listing = await client.list_tools()
    tool_names = {item.name for item in listing.tools}
    missing = sorted(EXPECTED_TOOLS - tool_names)
    unexpected = sorted(tool_names - EXPECTED_TOOLS)
    if missing or unexpected:
        raise RuntimeError(
            "MCP tool discovery does not match the documented contract; "
            f"missing={missing}, unexpected={unexpected}"
        )

    capabilities_result = await client.call_tool("get_server_capabilities", arguments={})
    capabilities = _structured_payload(capabilities_result)

    if capabilities.get("version") != __version__:
        raise RuntimeError(
            "Unexpected RunFix server version: "
            f"{capabilities.get('version')!r}; expected {__version__!r}."
        )

    if set(capabilities.get("tools", [])) != EXPECTED_TOOLS:
        raise RuntimeError("get_server_capabilities returned an incomplete tool list.")

    scan_result = await client.call_tool(
        "scan_project", arguments={"project_path": str(project)}
    )
    scan = _structured_payload(scan_result)
    if scan.get("python_file_count", 0) < 1 or scan.get("file_count", 0) < 2:
        raise RuntimeError(f"Project scan returned unexpected results: {scan}")

    tasks_result = await client.call_tool(
        "list_tasks", arguments={"project_path": str(project), "limit": 5}
    )
    tasks = _structured_payload(tasks_result)
    if tasks.get("count") != 0 or tasks.get("tasks") != []:
        raise RuntimeError(f"New temporary project should have no tasks: {tasks}")

    # The server is intentionally started without --enable-execution.
    # Even an explicit per-call confirmation must not bypass that server gate.
    gate_result = await _call(client, "run_project_command", {
        "project_path": str(project),
        "command": [sys.executable, "-c", "print('RUNFIX_MCP_EXECUTION_GATE_BYPASSED')"],
        "confirm_execution": True,
    })
    if not _is_expected_execution_denial(gate_result):
        raise RuntimeError(
            "The execution permission gate did not return the expected policy denial. "
            f"Unexpected result: {gate_result!r}"
        )

    for tool_name, arguments in (
        ("start_task", {
            "project_path": str(project),
            "command": [sys.executable, "-c", "print('SHOULD_NOT_RUN')"],
            "confirm_execution": True,
        }),
        ("create_isolated_environment", {
            "project_path": str(project),
            "confirm_execution": True,
            "confirm_install": True,
        }),
        ("run_agent", {
            "project_path": str(project),
            "goal": "Attempt to bypass the execution gate",
            "allow_project_code": True,
            "confirm_execution": True,
        }),
    ):
        result = await _call(client, tool_name, arguments)
        if not _is_expected_execution_denial(result):
            raise RuntimeError(
                f"{tool_name} did not return the expected server-side permission denial: {result!r}"
            )

    return {
        "status": "PASS",
        "mode": "gated",
        "sdk_api": capabilities.get("sdk_api", "unknown"),
        "server_version": capabilities["version"],
        "discovered_tool_count": len(tool_names),
        "scan_file_count": scan["file_count"],
        "scan_python_file_count": scan["python_file_count"],
        "task_listing": "PASS",
        "execution_gate": "BLOCKED_AS_EXPECTED",
        "background_task_gate": "BLOCKED_AS_EXPECTED",
        "agent_escalation_gate": "BLOCKED_AS_EXPECTED",
        "environment_install_gate": "BLOCKED_AS_EXPECTED",
    }


def _require(payload: Any, tool: str, *keys: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"{tool} returned no structured object: {payload!r}")
    for key in keys:
        if key not in payload:
            raise RuntimeError(f"{tool} response is missing {key!r}: {payload!r}")
    return payload


async def _exercise_full_client(client: Any, project: Path) -> dict[str, Any]:
    """Fully-permitted server: call every documented tool once over the protocol."""
    listing = await client.list_tools()
    tool_names = {item.name for item in listing.tools}
    if tool_names != EXPECTED_TOOLS:
        raise RuntimeError(
            "MCP tool discovery does not match the documented contract; "
            f"missing={sorted(EXPECTED_TOOLS - tool_names)}, "
            f"unexpected={sorted(tool_names - EXPECTED_TOOLS)}"
        )

    exercised: list[str] = []

    async def invoke(name: str, arguments: dict[str, Any], *keys: str) -> dict[str, Any]:
        result = await _call(client, name, arguments)
        payload = _structured_payload(result) if not isinstance(result, BaseException) else None
        if payload is None:
            raise RuntimeError(f"{name} failed over the protocol: {result!r}")
        exercised.append(name)
        return _require(payload, name, *keys)

    capabilities = await invoke("get_server_capabilities", {}, "version", "tools", "permissions")
    if capabilities["version"] != __version__:
        raise RuntimeError(f"Version mismatch: {capabilities['version']!r} != {__version__!r}")
    if set(capabilities["tools"]) != EXPECTED_TOOLS:
        raise RuntimeError("get_server_capabilities advertised an unexpected tool set.")
    if not all(capabilities["permissions"].values()):
        raise RuntimeError(f"Full-mode server did not enable every gate: {capabilities['permissions']}")

    scan = await invoke("scan_project", {"project_path": str(project)},
                        "file_count", "python_file_count")
    await invoke("diagnose_environment", {"python_executable": sys.executable},
                 "python_version")
    await invoke("validate_project", {"project_path": str(project)}, "stages", "overall_status")
    await invoke("analyze_failure", {
        "project_path": str(project),
        "traceback_text": "ModuleNotFoundError: No module named 'torch'",
    }, "category", "confidence")
    await invoke("get_source_context", {
        "project_path": str(project), "file_path": "main.py", "line_number": 1,
    }, "excerpt")

    patch = (
        "diff --git a/main.py b/main.py\n"
        "--- a/main.py\n"
        "+++ b/main.py\n"
        "@@ -1 +1 @@\n"
        "-print('hello')\n"
        "+print('hello from runfix')\n"
    )
    await invoke("validate_patch", {"project_path": str(project), "patch_text": patch}, "valid")
    applied = await invoke("apply_patch", {
        "project_path": str(project), "patch_text": patch, "confirm_write": True,
    }, "applied", "transaction_id")
    if applied["applied"] is not True:
        raise RuntimeError(f"apply_patch did not apply the patch: {applied!r}")
    await invoke("get_patch_transaction", {
        "project_path": str(project), "transaction_id": applied["transaction_id"],
    }, "status", "files")
    await invoke("rollback_patch", {
        "project_path": str(project),
        "transaction_id": applied["transaction_id"],
        "confirm_write": True,
    }, "rolled_back")

    await invoke("run_tests", {"project_path": str(project), "confirm_execution": True}, "passed")
    await invoke("run_project_command", {
        "project_path": str(project),
        "command": [sys.executable, "-c", "print('RUNFIX_COMMAND_OK')"],
        "confirm_execution": True,
    }, "returncode", "passed")
    await invoke("create_isolated_environment", {
        "project_path": str(project),
        "confirm_execution": True,
        "confirm_install": True,
    }, "created")

    task = await invoke("start_task", {
        "project_path": str(project),
        "command": [sys.executable, "-c", "print('RUNFIX_TASK_OK')"],
        "confirm_execution": True,
    }, "task_id", "status")
    task_id = task["task_id"]
    for _ in range(50):
        status = await invoke("get_task_status", {
            "project_path": str(project), "task_id": task_id,
        }, "status")
        if status["status"] not in {"running", "starting", "stop_requested"}:
            break
        await asyncio.sleep(0.1)
    await invoke("read_task_log", {"project_path": str(project), "task_id": task_id}, "log")
    listed = await invoke("list_tasks", {"project_path": str(project), "limit": 10}, "count")
    if listed["count"] < 1:
        raise RuntimeError("list_tasks did not return the task that was just started.")
    await invoke("stop_task", {
        "project_path": str(project), "task_id": task_id, "confirm_execution": True,
    }, "task", "stopped")

    agent_report = await invoke("run_agent", {
        "project_path": str(project),
        "goal": "Inspect this project without executing it",
    }, "outcome", "tool_history")
    if agent_report["outcome"] not in {"inspection_only", "diagnosed"}:
        raise RuntimeError(f"Unexpected agent outcome in full mode: {agent_report['outcome']!r}")
    await invoke("plan_reproduction", {"project_path": str(project)}, "steps", "cuda")
    await invoke("explain_root_causes", {
        "project_path": str(project),
        "traceback_text": "ModuleNotFoundError: No module named 'definitely_not_installed_pkg'",
    }, "candidates", "top")
    reproduction = await invoke("run_reproduction", {
        "project_path": str(project),
        "command": [sys.executable, "-c", "print('epoch 1/1 loss=0.42 acc=0.91'); print('final test accuracy: 0.91')"],
        "confirm_execution": True,
    }, "verdict", "run")
    if reproduction["verdict"] not in {"metrics_recorded", "completed", "metrics_matched", "reproduced"}:
        raise RuntimeError(f"run_reproduction produced an unexpected verdict: {reproduction['verdict']!r}")
    report = await invoke("get_latest_report", {"project_path": str(project)}, "found")
    if report["found"] is not True:
        raise RuntimeError("get_latest_report did not find the report written by run_agent.")

    missing = sorted(EXPECTED_TOOLS - set(exercised))
    if missing:
        raise RuntimeError(f"These tools were never exercised: {missing}")

    return {
        "status": "PASS",
        "mode": "full",
        "server_version": capabilities["version"],
        "discovered_tool_count": len(tool_names),
        "exercised_tool_count": len(set(exercised)),
        "exercised_tools": sorted(set(exercised)),
        "scan_file_count": scan["file_count"],
        "patch_cycle": "applied-and-rolled-back",
        "agent_outcome": agent_report["outcome"],
        "reproduction_verdict": reproduction["verdict"],
    }


async def _with_v2(Client: Any, StdioServerParameters: Any, command: str,
                   args: list[str], project: Path, full: bool):
    params = StdioServerParameters(command=command, args=args)
    async with Client(params) as client:
        if full:
            return await _exercise_full_client(client, project)
        return await _exercise_gated_client(client, project)


async def _with_v1(ClientSession: Any, stdio_client: Any, StdioServerParameters: Any,
                   command: str, args: list[str], project: Path, full: bool):
    params = StdioServerParameters(command=command, args=args)
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as client:
            await client.initialize()
            if full:
                return await _exercise_full_client(client, project)
            return await _exercise_gated_client(client, project)


def _run_one(project: Path, flags: list[str], full: bool) -> dict[str, Any]:
    sdk_major, client_api, params_type = _load_sdk()
    child_args = _bootstrap_arguments(project, *flags)
    if sdk_major == 2:
        return asyncio.run(_with_v2(client_api, params_type, sys.executable, child_args, project, full))
    ClientSession, stdio_client = client_api
    return asyncio.run(
        _with_v1(ClientSession, stdio_client, params_type, sys.executable, child_args, project, full)
    )


def run_smoke_test(full: bool = False) -> dict[str, Any]:
    """Start the real server over stdio and verify the documented MCP contract."""
    with tempfile.TemporaryDirectory(prefix="runfix_mcp_smoke_") as temp:
        project = Path(temp).resolve()
        (project / "main.py").write_text("print('hello')\n", encoding="utf-8")
        (project / "requirements.txt").write_text("# intentionally empty\n", encoding="utf-8")
        tests_dir = project / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_smoke.py").write_text(
            "def test_smoke():\n    assert True\n", encoding="utf-8"
        )

        gated = _run_one(project, [], full=False)
        if not full:
            return gated

        full_result = _run_one(
            project,
            ["--enable-execution", "--enable-write", "--enable-install"],
            full=True,
        )
        return {"gated": gated, "full": full_result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="End-to-end stdio connection test for the RunFix MCP server."
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Also start a fully-permitted server and call all 20 tools over the protocol.",
    )
    args = parser.parse_args(argv)
    try:
        result = run_smoke_test(full=args.full)
    except Exception as exc:
        print(f"[FAIL] RunFix MCP connection test: {type(exc).__name__}: {exc}")
        return 1

    if not args.full:
        print("[PASS] RunFix MCP server started over stdio.")
        print(f"[PASS] SDK API: {result['sdk_api']}")
        print(f"[PASS] Server version: {result['server_version']}")
        print(f"[PASS] Discovered tools: {result['discovered_tool_count']} (exact contract match)")
        print(
            "[PASS] scan_project returned "
            f"{result['scan_file_count']} files "
            f"({result['scan_python_file_count']} Python files)."
        )
        print("[PASS] list_tasks returned an empty task list.")
        print("[PASS] Execution, background-task, agent-escalation and install gates blocked unauthorized calls.")
        print("\nStructured result:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    gated, full = result["gated"], result["full"]
    print("[PASS] Gated server: tool discovery, version and all permission gates verified.")
    print(
        f"[PASS] Full server: {full['exercised_tool_count']}/{full['discovered_tool_count']} "
        "tools exercised over the MCP protocol."
    )
    print(f"[PASS] Patch cycle over MCP: {full['patch_cycle']}.")
    print(f"[PASS] run_agent over MCP produced outcome: {full['agent_outcome']}.")
    print("\nStructured result:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
