# Research experiment reproduction (0.7.0)

`runfix reproduce` answers one question honestly: **how far did this repository get, and
what evidence supports that?**

It never treats "the process exited with code 0" as a reproduction.

## What it does

```text
repository ──► understanding ──► plan ──► (authorized) run ──► evidence ──► verdict
```

1. **Understanding** (`runfix/repro/understanding.py`, read-only)
   - README: headings, documented install commands, documented run commands.
   - Entrypoints: ranked `train.py` / `main.py` / … with the evidence for each score
     (README reference, `__main__` guard, `argparse`, distributed code).
   - Configuration: `configs/*.yaml|yml|json|toml`, parsed with PyYAML when available and
     with a flat-key scanner otherwise (so no hard dependency is introduced).
   - Seeds, training parameters (`epochs`, `batch_size`, `lr`, …).
   - Dataset references: path-like config values, resolved and checked for existence.
     Output directories are explicitly *not* treated as datasets.
   - Device requirements: `.cuda()`, `torch.cuda`, `DistributedDataParallel`, README GPU mentions.
   - Declared CUDA wheel tag (`cu118` in a requirements file or a
     `download.pytorch.org/whl/cuXXX` index URL).

2. **Plan** (`runfix/repro/plan.py`)
   - Python requirement vs. the selected interpreter.
   - Manifest selection (`environment.yml` → conda, `requirements.txt` / `pyproject.toml` → pip).
   - CUDA compatibility: the declared wheel tag compared with the driver's maximum CUDA
     version. The verdict is `supported`, `unsupported` or `unknown` — never a guess.
   - Dataset readiness; missing data is reported, never downloaded.
   - Every step carries `requires_authorization`.

3. **Run** (`runfix/repro/runner.py`, only with explicit authorization)
   - Executes one argv list with `shell=False`, a timeout, and a capture log under
     `<project>/.runfix/repro/<run-id>/`.
   - Records: exit code, duration, command, git commit/branch/dirty, dependency-manifest
     SHA-256, interpreter, framework versions, GPU names, and every file created during the run.

4. **Metrics** (`runfix/repro/metrics.py`)
   - Recognises `key=value` / `key: value` lines, JSON lines, and percentages; maps common
     aliases (`acc` → `accuracy`, `f1_score` → `f1`) so an expectation can be compared.
   - Every observation keeps the source line number and text as evidence.

5. **Verdict** (`runfix/repro/report.py`)

| Rung | Requires |
| --- | --- |
| `inspected` | the repository was read |
| `plan_ready` | an environment/run plan exists |
| `executed` | the command ran (even if it failed or timed out) |
| `completed` | exit 0 **and** positive completion evidence: a metric series or a completion marker |
| `metrics_recorded` | final metric values were parsed from the output |
| `metrics_matched` | declared `--expect name=value` targets were satisfied |
| `reproduced` | `metrics_matched` **and** the repository had no uncommitted changes |

A run that exits 0 and prints nothing is reported as `process_exited` / `executed` with the
explicit reason that a zero exit code is not evidence. `metrics_matched` and above require
the caller to declare target values; without them RunFix stops at `metrics_recorded`.

## CLI

```powershell
# 1. Understand and plan only. Never executes project code.
runfix reproduce "D:\research\paper-code" --plan-only

# 2. Run the README-documented command (requires authorization) and report.
runfix reproduce "D:\research\paper-code" --allow-execution --timeout 7200

# 3. Run an explicit command and compare with the paper's reported metric.
runfix reproduce "D:\research\paper-code" --allow-execution `
    --command python train.py --config configs/base.yaml `
    --expect accuracy=0.812 --expect f1=0.774

# 4. Build the isolated environment first (implies dependency installation), then run.
runfix reproduce "D:\research\paper-code" --build-environment --allow-execution
```

The exit code is `0` only for `metrics_matched` or `reproduced` (and for `--plan-only`), so
the command can gate CI.

## MCP tools

| Tool | Gate |
| --- | --- |
| `plan_reproduction` | read-only; runs only interpreter/GPU probes |
| `run_reproduction` | `--enable-execution` + `confirm_execution=true`; `--enable-install` + `confirm_install=true` when `build_environment=true` |

## Output

```text
<project>/.runfix/repro/
  <run-id>/
    run.json      # command, status, exit code, fingerprint, metrics, artifacts
    run.log       # redacted combined output
  reports/<stamp>/
    reproduction_report.json
    reproduction_report.md
```

The Markdown report contains the verdict ladder, the evidence for each plan step, the
execution fingerprint, the final metrics, the comparison table, the created artifacts and an
explicit limitations section.

## What this does **not** claim

- A completed run is not a reproduced paper. Only `metrics_matched` / `reproduced` are.
- Metrics are read from stdout/stderr; a project that only writes to a file or a tracking
  server is not captured unless it also prints them (use `--completion-regex` to at least
  establish completion).
- RunFix never downloads datasets, weights or licensed assets. Missing data is reported.
- A smoke configuration that reproduces is not evidence that the full-scale experiment does.
- Exact numeric equality with a paper is not required by default: `--expect` uses an
  absolute tolerance of 0.02 for values in [0, 1] and a relative tolerance of 2% otherwise.
