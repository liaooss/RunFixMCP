# Upgrade RunFix 0.5.0 to 0.6.0 (Windows / Anaconda)

This update keeps your existing Conda environment, MCP configuration and RunFix reports.

## 1. Back up the installation

Close Codex and copy `E:\Projects\RunFix` to a backup directory.

Do not delete:

- `.conda_env`
- `.runfix`
- Your existing Codex `config.toml`

## 2. Replace the source files

Extract the RunFix 0.6.0 ZIP. Copy the contents of its `RunFix` folder into `E:\Projects\RunFix`, overwriting source files. Preserve the existing `.conda_env` and `.runfix` directories.

## 3. Update the package in the existing environment

```powershell
cd E:\Projects\RunFix
.\.conda_env\python.exe -m pip install -e ".[dev,mcp]"
```

## 4. Run the checks

```powershell
.\.conda_env\python.exe -m runfix --version
.\.conda_env\python.exe -m pytest -q
.\.conda_env\python.exe .\scripts\check_mcp_connection.py
```

The version should report `RunFix 0.6.0` (a newer release reports its own version). There are 20 MCP tools; `python -m pytest -q` runs the test suite. The real stdio check requires the optional MCP SDK.

## 5. MCP configuration

The existing 0.5.0 MCP configuration can remain unchanged. The two new tools `rollback_patch` and `get_patch_transaction` are registered automatically after the package update.

`rollback_patch` requires the existing write gate (`--enable-write`) and a per-call `confirm_write=true`. `get_patch_transaction` is read-only.

## 6. First safe request

Ask Codex:

> Use RunFix 0.6.0 to inspect this repository and explain how patch validation, transaction backups, rollback conflicts and traceback-guided test selection work. Do not modify any files or run project code.
