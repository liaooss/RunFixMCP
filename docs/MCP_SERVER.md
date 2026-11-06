
# RunFix MCP Server

RunFix 0.8.0 exposes project inspection, isolated environment setup, persistent background experiment management, evidence-based root-cause diagnosis and experiment reproduction through the Model Context Protocol (MCP). The first transport is **stdio**, intended for local clients such as Codex and Claude Desktop. RunFix prefers the current MCP Python SDK v2 `MCPServer` API and retains a v1 `FastMCP` compatibility fallback; the v2 path is verified against `mcp==2.3.0`.

## 1. Install

With the project's Conda environment activated:

```powershell
python -m pip install -e ".[mcp]"
```

The current SDK v2 requires Python 3.11 or newer. On Python 3.10, pip resolves a compatible v1 SDK release; RunFix supports both API styles.

The repository's Anaconda installer installs the MCP extra by default:

```powershell
.\scripts\install_conda.ps1
```

## 2. Start manually

Safe default: only inspect projects under the allowed root. Project-code execution, file writes and dependency installation are disabled.

```powershell
python -m runfix.mcp.server --allowed-root "D:\research"
```

Equivalent CLI:

```powershell
runfix mcp --allowed-root "D:\research"
```

Multiple roots can be added by repeating `--allowed-root`.

### Permission gates

The server uses two layers of authorization: a server-startup capability gate and a per-tool-call confirmation argument.

| Capability | Server flag | Per-call argument |
|---|---|---|
| Read / scan / diagnose | Enabled by default | Not required |
| Execute pytest or a project command | `--enable-execution` | `confirm_execution=true` |
| Apply a code patch | `--enable-write` | `confirm_write=true` |
| Install requirements in an isolated RunFix environment | `--enable-install` plus `--enable-execution` | `confirm_install=true` plus `confirm_execution=true` |

Example for a trusted repository where tests may be run, but code must not be modified:

```powershell
python -m runfix.mcp.server --allowed-root "D:\research" --enable-execution
```

Dependency installation also requires the execution gate because RunFix invokes Conda or pip and then verifies the environment. Enable installation only for trusted projects with a supported `environment.yml`, `requirements.txt`, or `pyproject.toml` manifest.

Enable patch application only when you explicitly want the MCP client to be able to modify project files:

```powershell
python -m runfix.mcp.server --allowed-root "D:\research" --enable-execution --enable-write
```

Do not enable execution or writes for untrusted repositories. `run_project_command` uses an argv list and `shell=False`, but the requested executable still runs with the MCP server user's permissions.

## 3. Verify the actual stdio connection

After installing the MCP extra, run the included smoke test from the RunFix project root:

```powershell
python -m pip install -e ".[mcp]"
python .\scripts\check_mcp_connection.py
# or, after installation: runfix-mcp-check
```

The smoke test launches the RunFix MCP server as a child process and uses the official MCP Python SDK client to:

1. Connect over the real `stdio` transport.
2. Assert that the discovered tool set **equals** the documented 23 tools (an extra or missing tool fails the check).
3. Call `get_server_capabilities` and check that the reported version equals `runfix.__version__`.
4. Call `scan_project` and `list_tasks` against a temporary, harmless test project.
5. Attempt a project command, background task launch, `create_isolated_environment` and an escalating `run_agent` call with per-call confirmation while the server gates are disabled; all must be rejected with the original denial message.

The test supports MCP SDK v2 (`mcp.server.MCPServer`) and the v1 maintenance line. It does not modify your research projects, install dependencies or run a training script.

Add `--full` to start a second, fully-permitted server on the same throwaway project and call **every** tool over the protocol: scan, diagnose, validate, analyse, source context, patch validate/apply/transaction/rollback, tests, command execution, environment creation, the whole task lifecycle (`start_task` → `get_task_status` → `read_task_log` → `list_tasks` → `stop_task`), `run_agent` and `get_latest_report`. This is the check that proves the tool surface actually works end to end, not just that it registers.

```powershell
python -m runfix.mcp.smoke --full
```

> **Why the denial message matters.** MCP SDK v2 treats any exception other than its own `ToolError` as a server crash and reports only `Error executing tool <name>`. RunFix therefore translates its anticipated failures (denied permission, rejected argument, missing file) into the SDK's tool-error type, so a client can tell "you are not authorized" from "the server broke".

This verifies the RunFix server process and MCP protocol. Codex host registration is a separate final check; after configuring Codex, run `codex mcp list` and use `/mcp` in the Codex TUI to inspect the connected server.

## 4. Codex configuration (Windows)

Codex reads local MCP server configuration from:

```text
%USERPROFILE%\.codex\config.toml
```

Add this TOML block. Replace the RunFix path and the allowed research-project root with the actual paths on your computer.

```toml
[mcp_servers.runfix]
command = "E:\\Projects\\RunFix\\.conda_env\\python.exe"
args = [
  "-m",
  "runfix.mcp.server",
  "--transport",
  "stdio",
  "--allowed-root",
  "D:\\research"
]
cwd = "E:\\Projects\\RunFix"
startup_timeout_sec = 30
tool_timeout_sec = 120
```

For a trusted project where Codex may run tests, add `"--enable-execution"` to the `args` array. To permit patch application, also add `"--enable-write"`. Keep these flags disabled unless the workflow needs them.

Alternatively, register it directly through the Codex CLI (replace the two paths):

```powershell
codex mcp add runfix -- "E:\Projects\RunFix\.conda_env\python.exe" -m runfix.mcp.server --transport stdio --allowed-root "D:\research"
```

After saving the file or adding the server, restart or reload Codex, then verify the server is listed with:

```powershell
codex mcp list
```

The Codex CLI and IDE extension share the MCP configuration. The same stdio command can also be registered with other MCP hosts, but their configuration file formats differ.

## 5. Isolated environment setup and background tasks

### Create an isolated environment

The `create_isolated_environment` tool recognizes, in priority order, `environment.yml` / `environment.yaml`, `requirements.txt`, and `pyproject.toml`:

- Conda manifests create an environment under `<project>/.runfix/environments/` using the Conda executable. If Conda is unavailable, RunFix reports that explicitly; it does not reinterpret Conda dependencies as pip requirements.
- `requirements.txt` creates a project-local venv and installs the listed requirements.
- `pyproject.toml` creates a project-local venv and installs the local project with pip.
- The manifest fingerprint is part of the environment directory name, so a changed manifest gets a separate environment.

Environment creation is potentially networked and executes package build/install steps. It requires both server flags `--enable-execution --enable-install` and per-call `confirm_execution=true`, `confirm_install=true`. It never installs into the selected/base interpreter or changes system CUDA drivers.

### Manage long-running experiments

The task tools are designed for training scripts that may take much longer than a normal MCP request:

1. `start_task` launches an explicit argument list in the background and returns a task ID immediately.
2. `get_task_status` polls the process and returns its state, exit code when observable, and experiment fingerprint.
3. `read_task_log` returns a bounded, redacted tail of the combined stdout/stderr log.
4. `list_tasks` lists recent tasks for the project.
5. `stop_task` terminates the task process (and its process group/tree where supported).

Starting and stopping code require `--enable-execution` plus per-call `confirm_execution=true`. The command is passed as an argv list with `shell=False`; RunFix does not accept shell command strings. Task metadata and logs are stored under `<project>/.runfix/tasks/`. The experiment fingerprint records the command, Git commit/branch, dependency-manifest hashes, and selected interpreter. It is an audit fingerprint, not proof of deterministic reproduction. If the MCP server restarts, a task's exit code may be unavailable; RunFix reports that as `ended_unobserved` or `status_unknown` rather than inventing a result.

## 6. Available MCP tools

| Tool | Purpose | Default permission |
|---|---|---|
| `get_server_capabilities` | Show version, allowed roots and permission gates | Read |
| `scan_project` | Scan source files, entrypoints and dependency manifests | Read |
| `diagnose_environment` | Inspect a selected Python interpreter and ML/CUDA packages | Interpreter probe |
| `validate_project` | Static/import/environment validation without running the project entrypoint | Read + interpreter probe |
| `analyze_failure` | Analyze a supplied traceback and collect evidence | Read |
| `get_source_context` | Read a bounded source excerpt, with basic secret redaction | Read |
| `validate_patch` | Validate paths, `git apply --check`, and changed Python AST syntax in a temporary tree | Read |
| `apply_patch` | Apply a validated diff and create a durable patch transaction | Write gate |
| `rollback_patch` | Roll back a transaction only if files still match their post-patch hashes | Write gate |
| `get_patch_transaction` | Inspect transaction status and before/after file hashes | Read |
| `run_tests` | Discover and run pytest tests | Execution gate |
| `run_project_command` | Run an explicit argv command in the project directory | Execution gate |
| `create_isolated_environment` | Build a project-local Conda/venv from a supported manifest | Execution + install gates |
| `start_task` | Launch a long-running experiment without blocking the MCP call | Execution gate |
| `get_task_status` | Poll a task and retrieve its experiment fingerprint | Read |
| `stop_task` | Stop a background task and its process tree | Execution gate |
| `read_task_log` | Read a bounded, redacted log tail | Read |
| `list_tasks` | List recent background tasks | Read |
| `run_agent` | Start the full RunFix plan–act–observe repair workflow | Per-capability gates |
| `plan_reproduction` | Read a research repository and return an environment/run plan with CUDA, GPU, dataset and entrypoint evidence | Read + interpreter probe |
| `run_reproduction` | Run one authorized reproduction attempt and return its evidence-backed report | Execution gate; install gate when `build_environment=true` |
| `explain_root_causes` | Rank evidence-backed root-cause candidates from an evidence graph plus dependency analysis | Read + interpreter probe |
| `get_latest_report` | Read the latest RunFix report | Read |

## Patch transactions and rollback

`validate_patch` performs path validation, `git apply --check`, and isolated AST parsing for changed Python files. `apply_patch` returns a `transaction_id` and stores snapshots under `<project>/.runfix/patch_transactions/`.

`rollback_patch` requires the write permission gate and `confirm_write=true`. It compares each current file hash with the post-patch hash. If a file has been edited since the transaction, rollback is refused for the entire transaction. `get_patch_transaction` is read-only and exposes the transaction status and hashes.

The Agent also attempts rollback automatically after failed post-patch verification. If rollback detects independent edits, it reports the conflict and does not overwrite them.

## 7. Example prompts for an MCP client

- "Use RunFix to scan this repository and summarize its entrypoints, dependencies and likely runtime risks."
- "Analyze this traceback against the repository source and identify the most likely root cause."
- "Run the project's pytest suite through RunFix and summarize the failing tests." (requires execution gate)
- "Ask RunFix to diagnose and repair this trusted project. Do not modify files unless I explicitly approve a patch." (run_agent; write gate remains off)
- "Start `python train.py --config configs/base.yaml` as a background task, then report its task ID and experiment fingerprint." (requires execution gate)
- "Check task `<task_id>` and show the latest 20 KB of its log." (read-only)
- "Prepare an isolated environment from this repository's environment.yml." (requires execution + install gates and per-call confirmation)

## 8. LLM configuration

MCP does not itself require a model API key. The MCP client (for example Codex) supplies the reasoning model. If you want RunFix's own optional LLM-assisted traceback analysis or inner planner, configure `RUNFIX_LLM_API_KEY`, `RUNFIX_LLM_BASE_URL`, and `RUNFIX_LLM_MODEL` in the MCP server process environment.

## 9. Scope and limitations

- 0.6.1 supports local stdio transport. Streamable HTTP is a later milestone.
- Project paths must be inside one of the configured allowed roots.
- Isolated environment creation supports `environment.yml` / `environment.yaml` with Conda, `requirements.txt` with venv + pip, and `pyproject.toml` with venv + local project installation. It requires explicit execution and installation permissions.
- `diagnose_environment` executes the selected Python interpreter to inspect it. Use trusted interpreter paths.
- MCP exposes RunFix capabilities; it does not replace the host Agent's planning or model.
- Background task exit codes may be unavailable if the MCP server restarts while a child process is running; RunFix reports that uncertainty rather than guessing. Pause/resume and complete exit-code recovery are not yet implemented.
- Passing tests is evidence of runtime health, not proof of scientific correctness or exact paper reproduction.
