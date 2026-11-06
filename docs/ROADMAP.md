# Roadmap

## 0.1.0 — diagnostic MVP
- [x] Project scan, environment probe, traceback classifier
- [x] Guarded patch validation and report generation
- [x] Local web UI and upstream adapters

## 0.2.0 — autonomous agent foundation
- [x] Plan–act–observe loop
- [x] Allow-listed tool registry
- [x] LLM planner with deterministic fallback
- [x] Explicit project-code and write permissions
- [x] Test execution and post-patch verification
- [x] Persistent tool history and agent state
- [x] Web UI controls for agent permissions
- [x] Opt-in isolated requirements.txt setup in a project-local venv
- [x] Bounded patch-and-retest loop (up to two patch rounds)

## 0.3.0 — MCP integration
- [x] Local stdio MCP server and 12 tools
- [x] Allowed-root path boundary
- [x] Server-side and per-call permission gates
- [x] Codex configuration example and integration guide

## 0.4.0 — MCP reliability and end-to-end verification
- [x] Real stdio subprocess smoke-test client
- [x] SDK v2 support with SDK v1 compatibility
- [x] Tool discovery and capability-call verification
- [x] Temporary-project scan through the MCP protocol
- [x] Negative execution-gate test
- [x] Host-visible server instructions
- [ ] Codex UI/CLI connection verification on a user Windows machine

## 0.5.0 — experiment operations
- [x] Persistent background task start/status/stop/list/log tools
- [x] Process-group/tree termination where supported
- [x] Experiment fingerprints for Git revision, command, interpreter and manifest hashes
- [x] Isolated Conda environment creation from `environment.yml`
- [x] Isolated venv creation from `requirements.txt`
- [x] Isolated venv + local install from `pyproject.toml`
- [x] Manifest-fingerprint environment reuse
- [x] MCP execution/install permission gates for the new operations
- [ ] Pause/resume across Windows and POSIX
- [x] Full task recovery with exit-code capture after MCP server restart (0.6.1)

## 0.6.0 — transactional repair and targeted verification
- [x] AST-aware patch validation in an isolated temporary tree
- [x] Durable patch transaction IDs and pre/post SHA-256 snapshots
- [x] Conflict-aware rollback that refuses to overwrite independent edits
- [x] Agent automatic rollback after failed post-patch verification
- [x] Traceback-to-test selection using static Python import relationships
- [x] Targeted tests first, followed by the full suite; safe full-suite fallback
- [x] MCP rollback and patch-transaction inspection tools
- [ ] Dependency conflict solver and lockfile-aware repair
- [ ] Docker/WSL sandbox execution
- [ ] Multi-source evidence graph and confidence-calibrated root-cause ranking
- [ ] Causal dependency-to-runtime fault localization
- [ ] Adaptive repair policy evaluated on benchmark tasks
- [ ] Benchmark against Repo2Run, Env-Doctor and ResearchEnvBench-related baselines

## 0.6.1 — stability, real MCP verification and crash-safe repair
- [x] Audit every module, test and document; publish `docs/CURRENT_STATE_AUDIT.md`
- [x] Real MCP stdio handshake and permission-gate verification against `mcp==2.3.0`
- [x] Translate RunFix's anticipated failures into the SDK's tool-error type (v2 compat)
- [x] `runfix-mcp-check --full`: call all 20 tools over the MCP protocol
- [x] Exact tool-set and version assertions in the MCP smoke test
- [x] Cross-process lock for patch transactions
- [x] Recovery of `applying` transactions after a crash, with conflict review
- [x] Transaction retention that never prunes a rollbackable transaction
- [x] Background task exit-code recovery across a server restart (supervisor record)
- [x] Fix the Windows conda interpreter-resolution defect
- [x] Fix the literal `\n` defect in reports, agent logs and test fixtures
- [x] Make C5 a real evidence audit
- [x] Parse pytest-style tracebacks; add the `test_assertion_failure` category
- [x] Make the test suite green on Windows (was 4 failed / 61 passed)
- [x] Wheel build, clean-venv install and CLI version verification
- [x] One-shot install / upgrade / diagnose scripts
- [ ] Codex / Claude Desktop host registration on a user machine (requires the host client)

## 0.7.0 — research experiment reproduction agent
- [x] **Adaptive offline planner**: the policy reads observations, not just call counts
- [x] Repository understanding: README, configs, entrypoints and experiment parameters
- [x] Environment plan: Python/PyTorch/CUDA compatibility and hardware requirements
- [x] Dataset discovery with missing-path reporting and no unauthorized downloads
- [x] Run, collect logs/metrics/artifacts, record seeds, git revision and hardware
- [x] Reproduction report with an evidence ladder that never equates exit code 0 with success
- [x] MCP tools `plan_reproduction` / `run_reproduction` (22 tools)
- [ ] Automatic environment construction inside the reproduction flow (currently opt-in via `--build-environment`)
- [ ] Training-curve comparison against paper figures
- [ ] Multi-run averaging with variance reporting

## 0.8.0 — deep diagnosis and adaptive repair
- [x] Multi-source evidence graph (traceback × environment × dependencies × source × tests)
- [x] Root-cause candidate ranking with deterministic confidence and cited evidence
- [x] Dependency-constraint analysis (PEP 440 subset) with proposed resolutions
- [x] Repair strategy selection driven by the top ranked candidate
- [x] Docker/WSL isolation for reproduction runs, with refusal instead of silent degradation
- [x] MCP tool `explain_root_causes` (23 tools)
- [ ] Multi-round repair loop that *re-selects* a different candidate after a failed attempt
- [ ] Log-signal extraction from run output into the evidence graph
- [ ] Real container execution verified on a host with Docker
- [ ] Causal dependency-to-runtime fault localization across packages

## 0.9.0 — benchmark and evaluation
- [x] Reproducible benchmark suite (`synthetic-v1`, 11 tasks with recorded ground truth)
- [x] Metrics: top-1/top-3 accuracy, action accuracy, detection vs control abstention, latency, unauthorized-modification count
- [x] Ablation study over dependency analysis, classifier corroboration and score ranking
- [x] Read-only verification by hashing every project file before and after diagnosis
- [x] Benchmark CLI (`runfix bench`) with JSON/Markdown output
- [ ] A curated corpus of real research repositories (requires network access and licensing review)
- [ ] Environment-build and repair success rates (require authorized installs)
- [ ] Comparison against Repo2Run / Env-Doctor / ResearchEnvBench-style baselines on the same corpus
- [ ] Reproduction success rate with published target metrics

## 1.0.0 — productisation
- [ ] Stable CLI, MCP server and Web UI with multi-project task management
- [ ] Long-task checkpoint recovery across restarts (beyond exit-code recovery)
- [ ] Permission approval workflow and full operation audit trail
- [ ] Complete documentation, packaging and installation/upgrade verification on a clean machine
