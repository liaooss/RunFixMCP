# Changelog

## 0.6.1 — stability, real MCP v2 verification and crash-safe repair

Verified on Windows 11 + Anaconda + Python 3.11 against `mcp==2.3.0`. The full test
suite and the two MCP protocol checks were executed on this release; see
`docs/CURRENT_STATE_AUDIT.md` for the audit that preceded it.

### Fixed

- **MCP SDK v2 hid every permission denial.** v2 reports any exception except its own
  `ToolError` as a bare `Error executing tool <name>` crash, so a client could not tell
  "not authorized" from "server broken". RunFix now translates anticipated failures
  (denied permission, rejected argument, missing file) into the SDK's tool-error type,
  which keeps the original message and avoids a false ERROR-level traceback.
- **`report.md` was one line of text.** `to_markdown` joined its lines with a literal
  backslash-n; the same defect affected agent log concatenation (`tools.py`, `brain.py`)
  and several test fixtures that therefore wrote invalid TOML/YAML/Python files.
- **Conda environments always reported failure on Windows.** `build_isolated_environment`
  resolved the interpreter path before creating the environment, so a successful
  `environment.yml` build returned `created: false` / `python_executable: null`. The path
  is now re-resolved after creation.
- **`/api/health` reported the wrong version** (0.5.0) instead of the package version.
- **C5 "evidence audit" could never fail**, because every stage was constructed with an
  evidence argument. It is now a real check (`validation.audit_stages`) that fails a stage
  reporting `passed`/`failed`/`warning` without a non-empty evidence mapping.
- **pytest-style tracebacks produced no source context** and fell into
  `unclassified_runtime_error`. `File "...", line N` and `path.py:N:` frames are both
  parsed now, and assertion failures get their own category (`test_assertion_failure`).
- **Three tests were written for POSIX venv layouts** (`bin/python`) and failed on Windows.
  They now build the interpreter location for the current platform.
- **`runfix.cli` had no `__main__` guard**, so `python -m runfix.cli` exited silently.
- Install scripts no longer depend on a non-existent `py -3.11` launcher, and they verify
  the installation instead of only installing.

### Added

- **Patch transaction locking.** Apply and rollback are serialised by a per-project thread
  lock plus an OS file lock (`msvcrt` / `flock`).
- **Interrupted-transaction recovery.** A transaction left in `applying` by a crash is
  restored when the file still matches a recorded hash, and flagged
  `interrupted_needs_review` when it does not. Exposed as `runfix patch-recover` and run
  automatically before every apply/rollback.
- **Transaction retention.** Finished, no-longer-rollbackable transactions are pruned
  beyond 200 entries; applied transactions are never pruned.
- **Task exit-code recovery across restarts.** Each background task now runs under a
  supervisor that records the real exit code to `process_result.json`, so a restarted
  server reports `completed`/`failed` with the true `returncode` and an
  `exit_code_source` field, instead of an unobservable `ended_unobserved`.
- **`runfix-mcp-check --full`.** Starts a fully-permitted server on a throwaway project and
  calls all 20 tools over the MCP protocol, including a patch → rollback cycle and the
  whole task lifecycle.
- **MCP smoke test now asserts equality** between discovered and documented tools, and
  derives the expected version from `runfix.__version__` instead of a literal.
- **`runfix patch-recover`, `scripts/upgrade_windows.ps1`, `scripts/diagnose_windows.ps1`.**
- **`docs/CURRENT_STATE_AUDIT.md`** — the evidence-based audit of 0.6.0.
- 40 new regression tests (`test_report_format`, `test_patch_transactions`,
  `test_task_recovery`, `test_mcp_errors`, `test_diagnostics_quality`).

### Changed

- `build_isolated_environment` refuses to delete an existing environment directory that
  contains files RunFix did not create.
- `README.md`, `docs/MCP_SERVER.md`, `docs/ARCHITECTURE.md`, `docs/AGENT_DESIGN.md` and
  `docs/SAFETY.md` were corrected where they disagreed with the code (backup location,
  `--allow-install` requirements, Web patch claims, Env-Doctor invocation, C5 semantics).


## 0.6.0 — transactional repair and targeted verification

- Added isolated-tree AST validation for Python patches.
- Added durable patch transactions with before/after file hashes and backups.
- Added conflict-aware rollback and idempotent transaction status.
- Added automatic Agent rollback when post-patch verification fails.
- Added traceback-guided pytest selection through static import analysis.
- Added `rollback_patch` and `get_patch_transaction` MCP tools (20 total).
- Expanded tests for syntax rejection, rollback conflicts, new-file rollback, Agent rollback and test selection.


## 0.5.0 — experiment task management and environment automation

- Added persistent background task management: start, status, stop, list and bounded log retrieval.
- Added experiment fingerprints covering command, Git revision, dependency manifest hashes and interpreter.
- Added isolated environment creation from Conda `environment.yml`, pip `requirements.txt`, and `pyproject.toml`.
- Added manifest-fingerprint environment reuse and separate environments for changed manifests.
- Exposed six new MCP tools (18 total) with explicit execution/install permission gates.
- Expanded tests for process lifecycle, path safety, environment detection, environment reuse and MCP permissions.


## 0.4.0 — MCP end-to-end verification

- Added a real stdio subprocess smoke-test client for MCP SDK v2 and v1 (`runfix-mcp-check`).
- The smoke test discovers all 12 tools, calls server capabilities and scans a temporary project.
- Added a protocol-level negative test proving that per-call confirmation cannot bypass the server execution gate.
- Added host-visible MCP instructions covering allowed roots, untrusted repository content, permission gates and evidence-backed repair claims.
- Updated Codex configuration guidance with startup/tool timeouts and CLI registration steps.
- Added explicit MCP server identity and version reporting.


## 0.3.0 — MCP Server integration

- Added a local stdio MCP Server with 12 tools for project inspection, diagnosis, validation, source context, patching, execution and Agent orchestration.
- Added allowed-root filesystem boundaries.
- Added server-side and per-call permission gates for execution, writes and dependency installation.
- Added Codex configuration example and MCP integration documentation.
- Added MCP policy and tool-registration tests.


## 0.2.0 — Autonomous Agent foundation

- Added plan–act–observe agent loop with an LLM planner and deterministic fallback.
- Added allow-listed tool registry and stateful execution trace.
- Added opt-in pytest and explicit user-command execution.
- Added opt-in isolated requirements installation into `.runfix/agent_env`.
- Added guarded patch validation, explicit write permission and post-patch verification.
- Added up to two bounded patch rounds.
- Added Web UI controls for code execution, patch application, dependency installation and LLM use.
- Added persistent `agent_state.json` execution record.

## 0.1.0 — Diagnostic MVP

- Initial project scanner, environment diagnostics, traceback analysis, patch checks, staged validation and local Web UI.
