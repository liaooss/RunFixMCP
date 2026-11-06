# Changelog

## 1.0.0 — product surface: audit trail, checkpoint resume, approvals, multi-project console

Everything below was exercised on this machine; the verification table at the end of this
entry lists what was actually run.

### Added

- **Tamper-evident audit trail** (`runfix/audit.py`): every privileged action
  (`patch.apply/rollback/recover`, `environment.build`, `task.start/stop`, `agent.run`,
  `reproduce.run`, approval grants/revocations) is appended to
  `<project>/.runfix/audit/audit-YYYY-MM.jsonl` with a SHA-256 hash chain and redacted
  details. `runfix audit <project> --verify` recomputes the chain and exits non-zero on any
  modification, removal or reordering.
- **Durable agent checkpoints and resume** (`runfix/checkpoint.py`): a checkpoint is written
  after every completed step; `runfix agent <project> --resume <run_key>` continues from the
  next step instead of replaying side effects. `runfix runs <project>` lists them.
  Resuming re-uses the permissions the run was started with, so a resume can never escalate.
- **Explicit approvals** (`runfix/approvals.py`): `runfix approvals <project> --grant
  patch.apply --uses 2 --ttl-minutes 30` issues an expiring, one-shot grant;
  `--require patch.apply` records the project policy; `--revoke` withdraws it. The MCP server
  honours the policy when started with `--require-approval`, consuming a grant before
  `apply_patch`, `rollback_patch`, `start_task`, `stop_task`, `create_isolated_environment`,
  `run_agent` or `run_reproduction` runs. Approvals only *narrow* what the permission flags
  already allow.
- **Multi-project web console** (`runfix/web/server.py`): a project registry plus
  `/api/projects`, `/api/tasks`, `/api/audit`, `/api/reports` and a dashboard that shows each
  project's tasks, latest report, reproduction verdict and audit count. GET endpoints are
  strictly read-only; the existing action endpoints keep their permission rules.
- `docs/AUDIT_LOG.md`, `docs/WEB_UI.md`.

### Changed

- `runfix web` gained `--state` (registry directory, default `./.runfix-web`).
- `runfix agent` gained `--resume`, and every agent report now carries `checkpoint_id`.
- `runfix mcp` gained `--require-approval`.
- The MCP tool wrapper consumes approvals before invoking a privileged tool and reports the
  denial as a tool error with the exact command needed to grant it.

### Verified on this machine

| Check | Result |
| --- | --- |
| `python -m pytest -q` | **249 passed, 2 skipped** |
| `python -m runfix.mcp.smoke --full` | **23/23 tools**, exit 0 |
| Real audited actions (`reproduce`, `agent`, `approvals --grant`) | 3 entries written |
| `runfix audit --verify` | `{"ok": true, "checked": 3}` |
| Web console over real HTTP | health/projects/tasks/audit/dashboard all returned; 0.0.0.0 rejected |

### Still not verified

- Docker/WSL execution (no Docker daemon, no WSL distribution on this host).
- LLM planning and LLM-authored patches (no API key).
- Accuracy on real published repositories (the benchmark suite is self-authored).
- The web console in a real browser: only HTTP-level checks were performed, not visual ones.


## 0.9.0 — reproducible benchmark and ablation study

RunFix can now measure its own diagnosis instead of asserting it works.

### Added

- **`runfix/bench`**: a reproducible diagnosis benchmark (`synthetic-v1`) of eleven real
  small repositories with injected defects, fixed environment descriptions and recorded
  ground truth, plus an ablation harness. Exposed as `runfix bench`
  (`--list`, `--task`, `--ablations`, `--out`, `--json`).
- **Metrics that separate correctness from caution**: `top1_accuracy`, `top3_accuracy`,
  `action_accuracy`, `detection_rate` (defect tasks) and `control_abstention_rate` (the
  control task must stay `unclassified_error`), `mean_top_confidence`,
  `mean_latency_seconds`, `per_category_accuracy`, and
  `files_modified_by_diagnosis` — verified by hashing every file before and after diagnosis.
- **Ablation variants**: `no_dependency_analysis`, `no_classifier_agreement`,
  `no_score_ranking`, each run over the same tasks and reported as a delta against `full`.
- **Injection points for deterministic evaluation**: `rank_root_causes(...,
  environment_override=, installed_override=, ablate=...)` so a task can describe an
  environment without touching the host.
- **A new root-cause kind found by building the benchmark**: `python_syntax` →
  `syntax_error` (`patch_source`). Before 0.9.0 a `SyntaxError` fell through to
  `unclassified_error`, which the suite caught immediately.
- `docs/BENCHMARK.md`.

### Results on this machine (real run, not projected)

`runfix bench`, 11 tasks, wall time ≈ 2.0 s:

| Metric | Value |
| --- | --- |
| top-1 / top-3 root-cause accuracy | 1.0 / 1.0 |
| proposed-action accuracy | 1.0 |
| detection rate (10 defect tasks) | 1.0 |
| control abstention rate (1 control task) | 1.0 |
| mean top confidence | 0.7845 |
| mean latency | 0.179 s per task |
| files modified by diagnosis | 0 |

`runfix bench --ablations`:

| Variant | Top-1 | Δ vs full |
| --- | --- | --- |
| `full` | 1.0 | — |
| `no_dependency_analysis` | 0.6364 | −0.3636 |
| `no_classifier_agreement` | 1.0 | 0.0 |
| `no_score_ranking` | 0.9091 | −0.0909 |

A perfect score on a suite we authored is a regression guard, not evidence of real-world
accuracy; the report says so on every run, and the unmeasured metrics (environment build
rate, repair rate, reproduction rate, real-world accuracy) are printed with their reasons.

### Not measured (stated, not claimed)

- Environment build success, repair success, post-repair test pass rate and experiment
  reproduction rate: they need network installs, LLM-authored patches or a curated corpus.
- Docker/WSL execution remains unverified on this host (no Docker daemon, no WSL distro).

### Not changed

- No new MCP tools in this release: benchmarking is a developer/evaluation workflow, not an
  agent capability, so the MCP surface stays at 23 tools.


## 0.8.0 — evidence-based diagnosis, dependency resolution and isolation

RunFix now diagnoses with an evidence graph and ranked, citable root causes, and can execute a
reproduction attempt inside Docker or WSL.

### Added

- **Evidence graph** (`runfix/diagnosis/evidence.py`): typed nodes (project, git revision,
  environment, framework, dependency, installed package, module, source file, test file,
  exception) and relations (`failed_with`, `raised_in`, `imports`, `references`, `declares`,
  `resolved_to`, `tested_by`, …), built from static analysis plus one interpreter probe.
- **Dependency analysis** (`runfix/diagnosis/dependencies.py`): a self-contained PEP 440
  subset (`==`, `!=`, `>=`, `<=`, `>`, `<`, `~=`, `===`, `==X.Y.*`), parsing of
  `requirements.txt` / `environment.yml` (conda pins + `pip:` section) / `pyproject.toml`,
  and findings for `not_installed`, `version_unsatisfied`, `manifest_disagreement`,
  `python_incompatible` and `version_unparsable` with proposed resolutions.
- **Ranked root causes** (`runfix/diagnosis/rootcause.py`): 11 candidate kinds, each with a
  deterministic confidence, the evidence that raised it, contradicting facts, and one
  concrete action. No LLM involved.
- **`runfix diagnose-root-cause`** and the MCP tool `explain_root_causes` (23 tools total).
- **The offline agent uses the ranking**: `analyze_failure` attaches the candidates and the
  policy decides from the top candidate's action, with new stop reasons
  `needs_interpreter_selection`, `needs_data_configuration`, `needs_cuda_wheel_selection`,
  `needs_gpu_host`, `needs_manifest_decision`, `needs_human_decision`.
- **Docker/WSL isolation** (`runfix/sandbox.py`): `runfix sandbox-info`, and
  `--sandbox none|docker|wsl` on `runfix reproduce` and `run_reproduction`. Docker runs with
  `--network none`, the project mounted at `/workspace`, and project paths rewritten. A
  missing backend raises instead of silently running unsandboxed.
- `docs/DIAGNOSIS.md`.

### Changed

- `runfix reproduce` records `sandbox`, `launch_command` and `interpreter_resolution` in the
  run metadata, and the Markdown report prints the sandbox in the execution section.
- The agent's `analyze_failure` result now carries `root_causes`, `dependency_analysis` and
  an `evidence_graph_summary`.
- `get_server_capabilities` reports `isolation_backends` (cheap existence check only).

### Verified

- `python -m pytest -q` → **201 passed, 2 skipped**.
- `python -m runfix.mcp.smoke --full` → **23/23 tools exercised over the real MCP protocol**,
  exit 0.
- Root-cause CLI on a real project: `module_declared_missing`, confidence 0.95, citing
  `requirements.txt` + the traceback + the classifier, with graph
  `failed_with/raised_in/imports/declares/resolved_to` edges present.

### Not verified (stated, not claimed)

- A real Docker container or WSL distribution was never executed: neither is available on
  this machine. The command construction, refusal semantics and pipeline wiring are tested;
  `runfix sandbox-info` reports both backends unavailable here.
- The LLM planner and LLM-authored patches remain unverified (no API key).


## 0.7.0 — research experiment reproduction agent

RunFix can now read a research repository, plan its environment, run it under explicit
authorization, and report how far it actually got — with an evidence ladder that refuses to
call an exit code a reproduction.

### Added

- **`runfix reproduce`**: understand → plan → run → evidence → verdict, written to
  `<project>/.runfix/repro/` as `reproduction_report.json` and `.md`.
- **Project understanding** (`runfix/repro/understanding.py`): README install/run commands,
  ranked entrypoints with per-score evidence, config parsing with a PyYAML-free fallback,
  seeds and training parameters, dataset references checked for existence, device
  requirements, and declared CUDA wheel tags.
- **Environment planning** (`runfix/repro/plan.py`): manifest selection, Python requirement
  check, dataset readiness, GPU visibility, and a CUDA compatibility verdict derived from the
  declared wheel tag versus the driver's maximum CUDA version. Unknown evidence yields
  `unknown`, never a guess.
- **Reproduction runs** (`runfix/repro/runner.py`): one authorized argv command with a
  timeout, captured log, and a fingerprint (git commit/branch/dirty, dependency-manifest
  SHA-256, interpreter, framework versions, GPU names) plus every file the run created.
- **Metric extraction** (`runfix/repro/metrics.py`): `key=value`, `key: value`, JSON lines and
  percentages, with alias normalisation (`acc` → `accuracy`) and the source line as evidence.
- **Verdict ladder** (`runfix/repro/report.py`): `inspected` → `plan_ready` → `executed` →
  `completed` → `metrics_recorded` → `metrics_matched` → `reproduced`. `completed` requires
  positive completion evidence; `metrics_matched` and above require caller-declared targets;
  `reproduced` also requires a clean git tree.
- **Observation-driven agent policy** (`runfix/agent/policy.py`): the offline planner now
  reads the failure category, patch state and last verification result instead of walking a
  fixed tool list, and stops with a machine-readable reason (`needs_install_permission`,
  `needs_configuration_change`, `needs_write_permission_or_patch`, `no_evidence_backed_repair`,
  `verified_baseline`, …).
- **MCP tools `plan_reproduction` and `run_reproduction`** (22 tools total), with the
  execution and install gates.
- `docs/REPRODUCTION.md`.

### Changed

- `get_server_capabilities` now derives the advertised tool list from the tools that were
  actually registered, so the advertised and registered sets cannot drift.
- The agent records a per-step `strategy` and a `stop_reason`, and `policy` as the planner
  name when the offline policy made the decision.

### Tests

- 46 new tests covering understanding, planning, CUDA assessment, metric parsing, the
  verdict ladder, run evidence collection and the policy. Suite: 151 passed, 2 skipped
  (the two symlink tests skip only when the account cannot create symlinks).


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
