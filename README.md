# RunFix 0.8.0 — Autonomous Research-Code Agent + MCP Server

**AI-powered Research Runtime Diagnosis, Evidence-based Root-Cause Analysis, Safe Repair and Experiment Reproduction Agent**

RunFix diagnoses Python / PyTorch research projects, connects environment evidence with source-code errors, ranks root causes with cited evidence, applies and rolls back minimal patches, plans and runs a reproduction attempt with an evidence ladder, and can execute under Docker/WSL isolation.

> **Status:** 0.8.0 adds evidence-based depth diagnosis (dependency resolution, evidence graph, ranked root causes) and Docker/WSL isolation on top of the 0.7.0 reproduction agent. See [docs/CURRENT_STATE_AUDIT.md](docs/CURRENT_STATE_AUDIT.md), [docs/DIAGNOSIS.md](docs/DIAGNOSIS.md) and [docs/REPRODUCTION.md](docs/REPRODUCTION.md).

## MVP scope

- Windows 10/11 + Anaconda/venv as the entry point; Python 3.10+.
- CPU and single-GPU validation. Multi-GPU/DDP is deferred.
- An agent loop with a planner, allow-listed tool registry, observations, action history, stop conditions and evidence-backed reports.
- Project scanning, Python/PyTorch/CUDA diagnosis, traceback analysis, optional LLM planning/patch proposals, guarded patch application, C0-C5 evidence validation, and a local web UI.
- A local MCP Server (stdio) with 23 tools for diagnostics, gated environment creation, persistent background experiment tasks, log retrieval, patch validation, Agent orchestration and experiment reproduction.
- Optional adapters for separately installed Env-Doctor, Repo2Run, and ResearchEnvBench.
- Upstream source repositories are not bundled in this package.

## Windows quick start (Anaconda / PowerShell)

Recommended: create a dedicated Conda environment inside the RunFix project directory.

```powershell
# Open Anaconda Prompt or a PowerShell session where conda is initialized
Set-ExecutionPolicy -Scope Process Bypass
.\\scripts\\install_conda.ps1
.\\scripts\\start_conda.ps1
```

The Conda environment is created at `RunFix\\.conda_env`. It does not modify an existing named Conda environment.

Alternative: use a standard Python 3.11 virtual environment:

```powershell
.\\scripts\\install_windows.ps1
.\\scripts\\start_windows.ps1
```

The web interface opens at `http://127.0.0.1:8765`.

For CLI commands in the Conda environment:

```powershell
.\\.conda_env\\python.exe -m runfix --help
.\\.conda_env\\python.exe -m runfix scan "D:\\path\\to\\research-project"
.\\.conda_env\\python.exe -m runfix agent "D:\\path\\to\\research-project"
```

RunFix can also use any existing Conda interpreter through `--python "D:\\path\\to\\env\\python.exe"`.

## Autonomous Agent mode

RunFix now has an actual plan-act-observe loop. The planner chooses from an allow-listed tool registry; every call and result is recorded in `agent_state.json`.

```powershell
# Inspection only: does not execute the repository's tests or training scripts
runfix agent "D:\path\to\research-project" --python "D:\path\to\env\python.exe"

# Allow execution of the repository's discovered pytest suite
runfix agent "D:\path\to\research-project" --python "D:\path\to\env\python.exe" --allow-project-code

# Allow tests plus a user-approved patch to be applied and re-tested
runfix agent "D:\path\to\research-project" --python "D:\path\to\env\python.exe" --allow-project-code --allow-write --use-llm

# Run a specific command only when explicitly supplied and authorized
runfix agent "D:\path\to\research-project" --allow-project-code --command -- python train.py --config configs/base.yaml
```

The local Web UI has the same controls: **allow project-code execution**, **allow patch application**, **allow requirements installation into an isolated project venv**, and **enable LLM planning/proposals**. `--allow-write` requires `--allow-project-code` so a patch can be verified after application, and `--allow-install` likewise requires it so the agent can re-test after installation. The Web UI has no dedicated patch endpoint; patches reach the filesystem only through an authorized agent run.

For LLM planning and patch proposals, configure `RUNFIX_LLM_API_KEY`, `RUNFIX_LLM_BASE_URL`, and `RUNFIX_LLM_MODEL` as described below, then select `--use-llm`. Without an API key RunFix still runs its deterministic tool-agent workflow, but it does not perform LLM reasoning or generate AI patches.

### Agent architecture

1. **Planner** — optional LLM chooses the next action from currently available tools; deterministic policy is the fallback.
2. **Tool registry** — project scan, environment diagnosis, static validation, test execution, traceback analysis, patch validation, patch application and post-patch verification.
3. **Observation state** — records evidence, tool history, latest failure and patch outcome.
4. **Safety policy** — no arbitrary shell tool; project code and writes require explicit flags; patch paths are checked and original files backed up.
5. **Feedback loop** — after an authorized patch, tests are run again; the result determines whether RunFix can report verified success.
6. **MCP adapter** — exposes the core and Agent tools to external MCP-compatible clients; the host Agent remains responsible for its own model-level planning.

A run is reported as `verified` only when an authorized runtime check passes. A static scan alone is never described as a successful reproduction.


## Research experiment reproduction (0.7.0)

```powershell
# 1. Read the repository and plan only — executes nothing.
runfix reproduce "D:\research\paper-code" --plan-only

# 2. Run the command the README documents, then report what actually happened.
runfix reproduce "D:\research\paper-code" --allow-execution --timeout 7200

# 3. Run an explicit command and compare with the numbers the paper reports.
runfix reproduce "D:\research\paper-code" --allow-execution `
    --command python train.py --config configs/base.yaml `
    --expect accuracy=0.812 --expect f1=0.774

# 4. Build the isolated environment first (implies dependency installation), then run.
runfix reproduce "D:\research\paper-code" --build-environment --allow-execution
```

The exit code is `0` only for `metrics_matched` / `reproduced` (and for `--plan-only`), so it
can gate CI. Every attempt is written to `<project>/.runfix/repro/` as a JSON and Markdown
report containing the verdict ladder, the environment/run plan, the run fingerprint, the
final metrics, the comparison table and the files the run created.

**A zero exit code is not a reproduction.** `completed` requires a metric series or a
completion marker; `metrics_matched` requires target values you declared; `reproduced`
additionally requires a clean git tree. Full details in
[docs/REPRODUCTION.md](docs/REPRODUCTION.md).

## Upgrade from an earlier release

See [0.6.1](docs/UPGRADE_0.6.1.md), [0.6.0](docs/UPGRADE_0.6.0.md) or [0.5.0](docs/UPGRADE_0.5.0.md) upgrade instructions. All of them preserve your existing `.conda_env` and `.runfix` folders.

## RunFix 0.8.0 highlights (depth diagnosis and isolation)

- **Evidence graph.** `runfix diagnose-root-cause` links the exception, traceback frames, source files, imports, declared requirements, installed versions and candidate test files into one graph with typed edges (`failed_with`, `raised_in`, `imports`, `declares`, `resolved_to`, `tested_by`, …).
- **Ranked root causes with citations.** Each candidate carries a deterministic confidence, the facts that raised it, the facts that would contradict it, and one concrete action. `module_declared_missing` scores 0.95 when the manifest, the environment probe and the classifier all agree.
- **Dependency-constraint analysis.** A self-contained PEP 440 subset (`==`, `!=`, `>=`, `<=`, `>`, `<`, `~=`, `==X.Y.*`) compares declared constraints with the installed interpreter, detects manifest disagreements and Python-version mismatches, and proposes install / choose-manifest / select-interpreter resolutions.
- **The agent uses the ranking.** `analyze_failure` now attaches the ranked candidates, and the offline policy decides from the top candidate's action instead of the coarse category, stopping with `needs_interpreter_selection`, `needs_data_configuration`, `needs_cuda_wheel_selection`, `needs_gpu_host`, `needs_manifest_decision`, …
- **Docker/WSL isolation.** `--sandbox docker|wsl` runs the reproduction command inside a container or a WSL distribution, with the network disabled and the project mounted at `/workspace`. If the backend is missing, RunFix refuses to run unsandboxed instead of silently degrading.
- **New CLI**: `runfix diagnose-root-cause`, `runfix sandbox-info`; **new MCP tool**: `explain_root_causes` (23 tools total).

## RunFix 0.7.0 highlights (experiment reproduction)

- **`runfix reproduce`** reads a research repository, plans its environment, runs it when authorized, and writes a `reproduction_report.json` / `.md` that states how far it got and why it stopped there.
- **An evidence ladder, not an exit code.** `inspected → plan_ready → executed → completed → metrics_recorded → metrics_matched → reproduced`. `completed` requires a metric series or a completion marker; `metrics_matched` requires caller-declared targets; `reproduced` additionally requires a clean git tree.
- **Real CUDA reasoning.** A declared PyTorch wheel tag (`cu118`, …) is compared with the driver's maximum CUDA version; without both, the verdict is `unknown` rather than a guess.
- **Dataset awareness without silent downloads.** Path-like config values are resolved and checked; missing data is reported and never fetched.
- **Full run fingerprint.** Git commit/branch/dirty flag, dependency-manifest SHA-256 values, interpreter and framework versions, GPU names, and every file the run created.
- **The offline agent now re-plans.** `runfix/agent/policy.py` decides from the failure category, patch state and last verification, and stops with a reason such as `needs_install_permission` instead of walking a fixed tool list.
- **MCP grew two gated tools**: `plan_reproduction` (read-only) and `run_reproduction` (execution + install gates).

## RunFix 0.6.1 highlights (stability)

- **MCP SDK v2 support is real, not nominal.** Permission denials now reach the client as tool errors carrying the original reason instead of a bare `Error executing tool ...` crash. Verified against `mcp==2.3.0`.
- **The MCP self-check exercises the whole contract.** `runfix-mcp-check --full` starts a fully-permitted server on a throwaway project and calls all 23 tools over the protocol, including a full patch → rollback cycle.
- **Patch transactions are concurrency-safe.** A per-project cross-process lock serialises validation, backup and apply; a transaction left in `applying` by a crash is restored (or flagged for review) on the next RunFix operation, and `runfix patch-recover` does it explicitly.
- **Background tasks recover their exit code after a server restart.** Each task runs under a small supervisor that writes the real exit code to disk, so a restarted server reports `completed`/`failed` with the true `returncode` instead of `ended_unobserved`.
- **Conda environments are reported correctly on Windows.** `build_isolated_environment` re-resolves the interpreter location after creation, so a successful `environment.yml` build no longer returns `created: false`.
- **Reports are real Markdown.** `report.md` previously joined its lines with a literal backslash-n.
- **pytest-style tracebacks now yield source context and their own failure category** (`test_assertion_failure`), instead of `unclassified_runtime_error` with no code excerpt.

## RunFix 0.6.0 highlights

- Background research runs: start a command without blocking the MCP call, inspect status, read bounded logs, list recent tasks, and stop a task.
- Experiment fingerprints: record the launch command, Git commit/branch, dependency-manifest SHA-256 values, selected interpreter, and a stable run fingerprint.
- Manifest-aware environment setup: isolated environments from `environment.yml` (Conda), `requirements.txt` (venv + pip), or `pyproject.toml` (venv + local project install).
- Environment reuse: a matching manifest fingerprint reuses the project-local environment; changed manifests create a separate environment.
- Safety gates: environment installation and background code execution require server-side capability flags and per-call confirmation.

## MCP Server (0.6.1)

Install the MCP SDK extra:

```powershell
python -m pip install -e ".[mcp]"
```

Start a local stdio server, restricted to a workspace:

```powershell
python -m runfix.mcp.server --allowed-root "D:\research"
# or: runfix mcp --allowed-root "D:\research"
```

Verify the actual stdio connection. The default run starts a temporary project, asserts the discovered tool set **equals** the documented 23 tools, checks the reported version, scans the project, lists tasks, and proves that execution / background-task / agent-escalation / installation gates cannot be bypassed by a per-call confirmation:

```powershell
python .\scripts\check_mcp_connection.py
# or, after installation: runfix-mcp-check
```

Add `--full` to also start a fully-permitted server on the same throwaway project and call **all 23 tools** over the protocol (scan, diagnose, validate, analyse, source context, patch validate/apply/rollback/transaction, tests, command, environment, task lifecycle, agent run, reproduction plan/run, report):

```powershell
python -m runfix.mcp.smoke --full
```

The server exposes 23 tools, including `create_isolated_environment`, background task management, `rollback_patch`, `get_patch_transaction`, and the reproduction pair `plan_reproduction` / `run_reproduction`.

**Security:** project paths are restricted to configured allowed roots. Execution, writes and package installation are disabled by default. Enable them at server startup (`--enable-execution`, `--enable-write`, `--enable-install`) and also require explicit per-call confirmation. See [docs/MCP_SERVER.md](docs/MCP_SERVER.md) for Codex configuration and examples.

### Background research runs through MCP

For a long training job, ask the connected MCP host to call `start_task` with an argument list, for example:

```json
{
  "project_path": "D:\\research\\paper-code",
  "command": ["python", "train.py", "--config", "configs/base.yaml"],
  "label": "paper reproduction",
  "max_runtime_seconds": 86400,
  "confirm_execution": true
}
```

The tool returns a task ID immediately. Use `get_task_status` to poll it, `read_task_log` to inspect output, and `stop_task` to terminate it. Starting/stopping requires the MCP server to be launched with `--enable-execution`.

## Env-Doctor (optional)

```powershell
python -m pip install env-doctor          # or: python -m pip install -e ".[envdoctor]"
runfix diagnose "D:\path\to\project" --python "D:\path\to\conda\envs\ml\python.exe"
```

`runfix diagnose` calls `env-doctor check --format json` when the CLI is found next to the selected interpreter (or on `PATH`), and falls back to RunFix's built-in probes when it is absent. `runfix integrations --python ...` only reports whether Env-Doctor, Repo2Run and ResearchEnvBench are present; it does not run their checks.

## LLM-assisted analysis (optional, explicit opt-in)

The heuristic analyzer works without an API key. To enable an OpenAI-compatible endpoint:

```powershell
$env:RUNFIX_LLM_API_KEY = "YOUR_API_KEY"
$env:RUNFIX_LLM_BASE_URL = "https://api.openai.com/v1"
$env:RUNFIX_LLM_MODEL = "gpt-4.1-mini"
runfix analyze "D:\path\to\project" --traceback-file ".\error.log" --use-llm
```

Use the provider's own base URL and model for compatible services. RunFix does not send source code to an LLM unless `--use-llm` is explicitly selected. API keys are read from environment variables and are never written into reports.

## Review and apply a patch

If analysis returns a unified diff, save it to a patch file and inspect it:

```powershell
runfix patch-check "D:\path\to\project" ".\candidate.patch"
runfix patch-apply "D:\path\to\project" ".\candidate.patch" --confirm
```

RunFix rejects path traversal, `.git` modifications, binary patches, and unsupported file extensions. Every patch is applied as a durable transaction: the original bytes are copied to `<project>\.runfix\patch_transactions\<transaction-id>\backups\<relative-path>` together with pre/post SHA-256 hashes, so it can be rolled back later. Inspect and roll back with:

```powershell
runfix patch-check "D:\path\to\project" ".\candidate.patch"     # validate only
runfix patch-apply "D:\path\to\project" ".\candidate.patch" --confirm
runfix patch-recover "D:\path\to\project"                        # repair a transaction interrupted by a crash
```

No AI patch is applied without explicit confirmation. Rollback refuses to overwrite a file that changed after the patch was applied.

## C0-C5 staged validation

| Stage | Meaning | MVP behavior |
|---|---|---|
| C0 | Static import/source checks | Runs automatically; does not import project modules |
| C1 | CPU entrypoint run | Skipped unless an explicit `--command` is supplied |
| C2 | Python / ML / CUDA alignment | Runs environment diagnostics |
| C3 | Single-GPU execution | Opt-in with `--include-gpu` |
| C4 | Multi-GPU / DDP | Deferred in MVP |
| C5 | Evidence/report consistency audit | Fails when a stage reports `passed`/`failed`/`warning` without a non-empty evidence mapping, or reports an unknown status |

To run a specific command, append it after `--command`:

```powershell
runfix validate "D:\path\to\project" --python "D:\env\python.exe" --command python train.py
```

Only run commands from repositories you trust. Commands execute without `shell=True`, in the project directory, with a timeout. Default validation does not launch training.

## Repo2Run adapter (Linux / WSL2 only)

Repo2Run's current upstream implementation uses Linux shell commands and Docker. It is not invoked automatically from native Windows Python.

Inside WSL2/Linux:

```bash
git clone https://github.com/bytedance/Repo2Run.git ~/third_party/Repo2Run
runfix repo2run-build /path/to/local/github-clone \
  --repo2run-path ~/third_party/Repo2Run \
  --confirm-external-build \
  --llm-model YOUR_MODEL
```

Requirements: a GitHub `origin` remote and commit SHA, Docker, Repo2Run dependencies, and its own LLM credentials. RunFix uses a fresh workspace below `<project>/.runfix/repo2run_runs/`. Repo2Run is archived upstream, so this adapter is optional.

## ResearchEnvBench adapter

RunFix adapts the C0-C5 evidence workflow and can inspect an upstream checkout:

```powershell
runfix integrations --researchenvbench-path "D:\path\to\ResearchEnvBench"
```

It does not silently run the full 44-repository benchmark. Full paper reproduction requires the upstream Docker image, pinned manifests, backend credentials, Linux/NVIDIA setup, and substantial disk.

## Reports

```text
<project>/
  .runfix/
    runs/<timestamp-id>/
      report.json
      report.md          # real Markdown
      execution.log
      agent_state.json   # agent runs only
    patch_transactions/<transaction-id>/
      transaction.json   # status, pre/post SHA-256, conflicts
      backups/<relative/path/to/changed_file.py>
    environments/<manager>-<fingerprint>/
    tasks/<task-id>/
      task.json
      execution.log
      process_result.json   # written by the task supervisor
```

## Command reference

| Command | Purpose |
| --- | --- |
| `runfix scan <project>` | Structure, entrypoints, manifests, imports, syntax errors, git state |
| `runfix diagnose <project> [--python P]` | Environment probe (Python, PyTorch, CUDA, GPU, Env-Doctor) and a saved report |
| `runfix analyze <project> (--traceback-file F \| --traceback TEXT) [--use-llm]` | Root-cause classification with CPython **and** pytest traceback frames |
| `runfix validate <project> [--python P] [--include-gpu] [--command ...]` | C0–C5 staged validation with saved evidence |
| `runfix patch-check / patch-apply / patch-recover <project> ...` | Validate, apply and recover transactional patches |
| `runfix agent <project> [flags]` | Plan–act–observe agent (`--goal`, `--max-steps`, `--timeout`) |
| `runfix diagnose-root-cause <project> [--traceback-file F] [--python P]` | Evidence graph and ranked root-cause candidates |
| `runfix reproduce <project> [--plan-only] [--allow-execution] [--expect N=V] [--sandbox docker\|wsl]` | Understand, plan, run when authorized, and report reproducibility |
| `runfix sandbox-info` | Which isolation backends (Docker / WSL) this machine provides |
| `runfix web [--host H] [--port N]` | Loopback-only Web UI (`/api/health`, `/api/scan`, `/api/diagnose`, `/api/validate`, `/api/analyze`, `/api/agent`) |
| `runfix mcp [--allowed-root R ...] [--enable-execution] [--enable-write] [--enable-install]` | stdio MCP server |
| `runfix integrations [--python P] [--repo2run-path R] [--researchenvbench-path R]` | Report which optional upstream tools are present |

## Tests

```powershell
python -m pip install -e ".[dev,mcp]"
python -m pytest -q                                  # unit + integration suite
python -m runfix.mcp.smoke                           # real stdio handshake and permission gates
python -m runfix.mcp.smoke --full                    # all 23 tools over the MCP protocol
```

The MCP end-to-end test is skipped automatically when the optional `mcp` extra is not installed. `python -m pytest -q` must report zero failures for a release to be considered verified; see [docs/CURRENT_STATE_AUDIT.md](docs/CURRENT_STATE_AUDIT.md) for the audit that established that requirement.

## Safety boundaries

- Dependency installation is opt-in (`--allow-install` **plus** `--allow-project-code`, or the MCP execution + install gates). It builds a project-local Conda/venv from supported manifests and never installs into the selected/base interpreter; no driver installation, system PATH changes, or privilege elevation.
- No training entrypoint is run unless the user supplies a command.
- No LLM request unless explicitly enabled.
- No automatic patch application: every patch needs an explicit confirmation, and every application is a reversible transaction.
- The Web UI binds to loopback only and exposes no dedicated patch endpoint; patches reach disk only through an authorized agent run.
- RunFix is an assistant, not a guarantee that every repository can be reproduced.

## Upstream attribution

- [Repo2Run](https://github.com/bytedance/Repo2Run) — Apache-2.0; environment-building workflow. Upstream source is not redistributed here.
- [ResearchEnvBench](https://github.com/No-518/ResearchEnvBench) — MIT; benchmark orchestration and C0-C5 runtime-verification concepts. Benchmark data/scripts are not redistributed here.
- [Env-Doctor](https://github.com/mitulgarg/env-doctor) — MIT; optional local GPU/CUDA/Python diagnosis via CLI. Package is installed separately.

See `THIRD_PARTY_NOTICES.md`.
