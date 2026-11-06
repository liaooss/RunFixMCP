# Upgrade to RunFix 0.5.0 (Windows / Anaconda)

This upgrade preserves your existing Conda environment and project-local `.runfix` reports/tasks.

## 1. Back up the current installation

Close Codex first. Copy `E:\Projects\RunFix` to a backup directory.

Do not delete these existing folders when replacing source files:

- `.conda_env` — your RunFix Conda environment
- `.runfix` — reports, task logs and project-local environments

The 0.5.0 source ZIP does not include either folder.

## 2. Replace the source files

Extract `RunFix_0.5.0_Agent_MCP.zip` to a temporary directory. Copy the contents of its `RunFix` folder into `E:\Projects\RunFix`, allowing source files to be overwritten. Keep the existing `.conda_env` and `.runfix` folders.

## 3. Update the package inside the existing Conda environment

Open Anaconda Prompt or PowerShell where Conda is initialized:

```powershell
cd E:\Projects\RunFix

.\.conda_env\python.exe -m pip install -e ".[dev,mcp]"
```

This updates RunFix in editable mode and ensures the MCP and test extras are installed. It does not recreate `.conda_env`.

## 4. Run local checks

```powershell
.\.conda_env\python.exe -m runfix --version
.\.conda_env\python.exe -m pytest -q
.\.conda_env\python.exe .\scripts\check_mcp_connection.py
```

The version command should report `RunFix 0.5.0` at the time of this upgrade (a later release reports its own version). The MCP connection check uses the official SDK client and tests the actual stdio subprocess connection. If the optional MCP SDK is absent, install the extra from step 3.

## 5. Refresh the Codex MCP configuration

Your existing `config.toml` entry can keep the same command and paths. To enable long-running tasks, add `--enable-execution` to the server `args`.

To allow the Agent to create isolated environments from project manifests, also add `--enable-install`.

Example:

```toml
[mcp_servers.runfix]
command = "E:\\Projects\\RunFix\\.conda_env\\python.exe"
args = [
  "-m",
  "runfix.mcp.server",
  "--transport",
  "stdio",
  "--allowed-root",
  "D:\\research",
  "--enable-execution",
  "--enable-install"
]
cwd = "E:\\Projects\\RunFix"
startup_timeout_sec = 30
tool_timeout_sec = 120
```

Only enable these flags when the configured allowed root contains repositories you trust. Each tool call still needs its own `confirm_execution=true` and/or `confirm_install=true` argument. File writes remain disabled unless `--enable-write` is also added.

After saving the configuration, restart Codex and check:

```powershell
codex mcp list
```

## 6. Try a safe first request

Start with a read-only request:

> Use RunFix to scan this repository, list its dependency manifests, inspect the selected Python/CUDA environment, and report risks. Do not install packages, execute project code, or modify files.

For a background training job, explicitly ask Codex to use `start_task` with an argv list, then use `get_task_status` and `read_task_log`. Avoid passing a shell command string.
