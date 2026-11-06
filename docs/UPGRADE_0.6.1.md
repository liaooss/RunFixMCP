# Upgrading to RunFix 0.6.1

0.6.1 is a stability release on top of the 0.6.0 transaction model. There is no
on-disk migration: `.runfix/patch_transactions`, `.runfix/tasks`, `.runfix/runs` and
`.runfix/environments` from 0.6.0 are read as-is.

## 1. Upgrade the environment

```powershell
# Virtual-environment install
.\scripts\upgrade_windows.ps1

# Conda install
.\scripts\upgrade_windows.ps1 -Conda
```

Both scripts print the version before and after, then re-run the MCP stdio self-check.
They do not touch project data under `<project>\.runfix`.

Manual equivalent:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade -e ".[dev,mcp]"
.\.venv\Scripts\python.exe -m runfix --version     # must print: RunFix 0.6.1
```

## 2. Verify the upgrade

```powershell
# 1. The test suite must report zero failures.
.\.venv\Scripts\python.exe -m pytest -q

# 2. Real stdio handshake plus every permission gate.
.\.venv\Scripts\python.exe -m runfix.mcp.smoke

# 3. All 20 tools over the MCP protocol, on a throwaway project.
.\.venv\Scripts\python.exe -m runfix.mcp.smoke --full

# 4. One-shot environment diagnosis.
.\scripts\diagnose_windows.ps1
```

## 3. What changed that you will notice

| Area | 0.6.0 behaviour | 0.6.1 behaviour |
| --- | --- | --- |
| MCP permission denial | Client saw only `Error executing tool <name>` under SDK v2 | Client receives the real reason, e.g. `PermissionError: Project-code execution is disabled by the MCP server...` |
| `report.md` | One physical line containing literal `\n` | Real Markdown |
| Conda environment build on Windows | Reported `created: false` even on success | Reports the real interpreter and `created: true` |
| pytest failures | `unclassified_runtime_error`, no source excerpt | `test_assertion_failure` with the failing file/line excerpt |
| Interrupted patch | Transaction stuck in `applying`, rollback refused | Restored automatically, or flagged `interrupted_needs_review` |
| Concurrent patching | No serialisation | Per-project thread + OS file lock |
| Task after server restart | `ended_unobserved`, `returncode: null` | `completed`/`failed` with the true `returncode` and `exit_code_source` |
| `/api/health` | Reported `0.5.0` | Reports the package version |

## 4. New commands

```powershell
runfix patch-recover "D:\research\paper-code"   # resolve transactions left by a crash
runfix diagnose "D:\research\paper-code"         # environment probe + saved report
.\scripts\diagnose_windows.ps1                   # full installation + MCP self-diagnosis
python -m runfix.mcp.smoke --full                # 20/20 tool protocol check
```

## 5. Rollback to 0.6.0

Install the previous version and keep your project data:

```powershell
.\.venv\Scripts\python.exe -m pip install "runfix-agent==0.6.0"
```

Existing transactions stay valid: 0.6.0 reads the same `transaction.json` schema. If
0.6.1 marked a transaction `recovered_from_interrupt` or `interrupted_needs_review`,
0.6.0 will not act on it (it only rolls back `applied`); inspect the file manually.
