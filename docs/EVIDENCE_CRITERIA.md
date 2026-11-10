# How RunFix decides: the criteria behind every step

[中文版](EVIDENCE_CRITERIA.zh-CN.md)

This document answers one question: **what evidence does each stage use, and what may RunFix
claim at the end of it?**

Three principles hold throughout:

1. **Every conclusion can be traced to evidence.** Statuses, confidences and verdicts are
   computed by code you can re-run.
2. **When evidence is missing, RunFix downgrades.** The ladder starts at `inspected` and ends at
   `reproduced`; each rung has an entry condition.
3. **A zero exit code is not a reproduction.** That assumption shapes the whole verdict model.

---

## Step 1 — Understand the project

**Question:** how does this repository want to be installed and run?

| Evidence | Rule | Output |
| --- | --- | --- |
| README | First match of `README.md` / `.rst` / `.txt` / `readme.md` / `README.MD` / `Readme.md`; headings, plus install/run commands inside fenced blocks (after stripping `$`, `>`, `PS>`, `#` prompts) | `readme.install_commands`, `readme.run_commands` |
| Entrypoints | Name base score **plus** README mention +40, `__main__` guard +20, argparse +15, distributed-training code +10 | `entrypoints[]` with `score` and per-item `evidence` |
| Configs | `configs/ conf/ config/ cfg/ options/ experiments/` and root `config.*`; YAML/JSON/TOML; depth ≤ 4, ≤ 300 KB, ≤ 60 files | `configs[]`, `seeds[]`, `training_parameters{}` |
| Config parsing | `json` / `tomllib` / optional PyYAML; **without PyYAML** it degrades to a flat `key: value` scan | each config records which `parser` was used |
| Datasets | keys matching `data/dataset/corpus/image_dir/dataset_dir`, excluding output-like keys (`out/output/log/save/ckpt/checkpoint/result/weight/exp`); value must look like a path; resolved and checked for existence | `datasets[]`, `missing_datasets[]` |
| Device needs | `.cuda()`, `torch.cuda`, `device='cuda'`, `DistributedDataParallel`, or a README mention of CUDA/GPU | `device.gpu_code_detected` |
| CUDA hints | `cu118`/`cu121` style tags and `download.pytorch.org/whl/cuXXX` index URLs | `environment.cuda_tags` |
| Python | `requires-python`, or `- python=3.x` in `environment.yml` | `environment.python_requirement` |

**Explicit downgrades:** no README → warning; no entrypoint → warning saying an explicit command
is required; no manifest → warning; missing dataset paths are reported **and never downloaded**.

Step 1 is read-only. The benchmark proves it by hashing every file before and after diagnosis
(`files_modified_by_diagnosis` must be 0).

---

## Step 2 — Diagnose the environment

**Question:** with *this* interpreter, can the project run?

A subprocess probe (no install, no mutation) collects the Python version and path, the platform,
eight common scientific packages, `torch` importability with `cuda_available` / `cuda_version` /
device count and names, and `nvidia-smi` availability and output.

| Decision | Criterion | Result |
| --- | --- | --- |
| Python compatibility | interpreter version compared as a numeric tuple against `python_requirement` | `satisfied` = true / false / null |
| CUDA compatibility | declared wheel tag (`cu121` → 12.1) vs the driver's `CUDA Version: X.Y`: driver ≥ required → **supported**; driver < required → **unsupported**; either missing → **unknown** | `cuda.verdict` + `cuda.reason` |
| Manifest selection | fixed priority `environment.yml` → `environment.yaml` → `requirements.txt` → `pyproject.toml` | `steps[environment].manager` |
| Datasets | `missing_datasets` empty or not | `ready` / `missing` / `unknown` |
| GPU risk | code needs CUDA but the interpreter cannot see a GPU → `at_risk` with a warning | `steps[gpu].warning` |

**Deliberate choice:** no CUDA guesswork. Without a declared wheel tag or a driver version the
verdict is `unknown` with a reason, not "probably fine".

Every step carries `requires_authorization`; the top level summarises it as
`install_environment`, `run_project_code`, `download_data`.

---

## Step 3 — Locate the root cause

### 3.1 The evidence graph

Nodes: `project`, `git_revision`, `environment`, `framework`, `dependency` (declared constraint),
`installed_package` (actual version), `module`, `source_file`, `test_file`, `exception`.

Edges: `failed_with`, `raised_in` (with line and function), `imports`, `references`, `declares`,
`resolved_to`, `tested_by`, `has_revision`, `runs_on`, `provides`.

### 3.2 Dependency findings

Declared constraints are compared with the installed versions using a self-contained PEP 440
subset (`==` `!=` `>=` `<=` `>` `<` `~=` `===` `==X.Y.*`):

| Finding | Trigger | Proposed resolution |
| --- | --- | --- |
| `not_installed` | declared, absent in this interpreter | install the declared constraint |
| `version_unsatisfied` | installed but outside the range | install the declared range |
| `manifest_disagreement` | two manifests disagree | a human chooses the authoritative one |
| `python_incompatible` | `requires-python` not satisfied | rebuild with a matching interpreter |
| `version_unparsable` | version string unparseable | reported only, not judged |

### 3.3 Candidates and confidence

Thirteen candidate kinds — `module_declared_missing`, `module_missing_undeclared`,
`declared_package_not_installed`, `version_constraint_unsatisfied`, `manifest_disagreement`,
`python_incompatible`, `cuda_driver_mismatch`, `cuda_unavailable`, `dataset_missing`,
`api_signature_mismatch`, `syntax_error`, `test_assertion_failure`, `unclassified_error` — each
carrying a statement, cited evidence, contradicting facts and one action.

```
confidence = base(kind, 0.30–0.90)
           + min(0.09, 0.03 × (evidence_count − 1))
           + corroboration (traceback classifier agrees: +0.06; CUDA driver agrees: +0.05)
           − 0.12 × contradicting_count
           clamped to [0.05, 0.98]

rank score = confidence + 0.01 × min(5, evidence_count)
```

**Worked example** (real output): `module_declared_missing` = 0.86 base + 0.03 (2 pieces of
evidence) + 0.06 (classifier agrees) = **0.95**.

**When it refuses:** if no rule fires, the result is `unclassified_error` at 0.30 with a request
for more evidence. The benchmark contains a defect-free control task whose correct outcome is
exactly that abstention.

---

## Step 4 — Controlled repair

### 4.1 Three gates

1. server-side flag (`--enable-write` / `--enable-execution` / `--enable-install`);
2. per-call confirmation (`confirm_write` / …);
3. optional approval (`--require-approval`): an unexpired, one-shot grant.

### 4.2 Seven patch checks (all must pass)

relative path inside the project; no `.git`/`.runfix`; extension allow-list; per-segment symlink
check; reject binary / submodule / symlink-mode patches; `git apply --check` succeeds; Python
files parse with `ast.parse` **in an isolated temporary tree**.

### 4.3 Transaction and rollback

| Scenario | Criterion | Result |
| --- | --- | --- |
| Apply | transaction id, backups, before/after SHA-256, project-wide cross-process lock | `applied` |
| Rollback | **every** target's current hash equals the recorded `sha256_after` | only then restore; otherwise refuse and list conflicts |
| After a crash (`applying`) | current hash == `sha256_after` → restore; == `sha256_before` → leave; anything else → someone edited it | `recovered_from_interrupt` / `interrupted_needs_review` |

Applying a patch is **not** repairing. Repair success is decided in step 5.

### 4.4 When the agent acts and when it stops

The policy reads the top-ranked candidate's action:

| Top action | Behaviour |
| --- | --- |
| `install_package` / `install_constraint` / `declare_and_install` | build the isolated env if authorized, else stop at `needs_install_permission` |
| `patch_source` | validate → apply → verify if write is authorized, else `needs_write_permission_or_patch` |
| `select_interpreter` / `configure_data` / `select_cuda_wheel` / `run_on_cpu_or_gpu_host` / `choose_manifest` | stop with the matching reason and hand the decision back |
| `collect_more_evidence` | stop at `needs_more_evidence` |

Each decision records `rationale` and `strategy` (e.g. `root_cause:test_assertion_failure`).

---

## Step 5 — Run and collect evidence

Explicit argv, `shell=False`, timeout, combined output to `.runfix/repro/<run-id>/run.log`.

**Test selection** (a prioritisation heuristic, not coverage): +100 if the test file appears in
the traceback, +15 if it references the source path, +10 if it statically imports the failing
module, +5 for a matching basename. Related tests run first; on success the full suite runs. No
relationship → full suite directly.

**Run status** (mutually exclusive):

| Status | Criterion |
| --- | --- |
| `failed_to_start` | the process could not start |
| `timed_out` | killed after the timeout |
| `failed` | non-zero exit code |
| `completed` | exit code 0 **and** positive completion evidence |
| `process_exited` | exit code 0 **and none** of that evidence |

**Completion evidence** (any one): at least one parsed metric observation; one of the thirteen
completion markers (`training complete`, `all epochs`, `final results`, …); or a match for the
caller's `--completion-regex`.

Also recorded: git commit/branch/origin/dirty (ignoring `.runfix` and `__pycache__`),
dependency-manifest SHA-256, interpreter, torch/CUDA versions, GPU names, and every file created
or modified during the run.

---

## Step 6 — Decide whether it reproduced

The verdict is the highest rung the evidence supports, plus the reason it is not higher.

| Rung | Entry condition (all must hold) |
| --- | --- |
| `inspected` | the repository was read |
| `plan_ready` | an environment/run plan exists |
| `executed` | the command ran (timeout and failure count) |
| `completed` | exit code 0 **and** completion evidence |
| `metrics_recorded` | final metrics were parsed |
| `metrics_matched` | **every** declared target matched |
| `reproduced` | matched **and** the tree was clean during the run **and** a git commit exists |

**Metric comparison:** names are normalised through an alias table (`acc → accuracy`); tolerance
is **absolute 0.02** for targets with |value| ≤ 1 and **relative 2%** otherwise; each target is
`match`, `mismatch` or `missing` (absent from the output — reported separately).

**Decision tree** (mirrors `derive_verdict`):

```
verdict = inspected
if plan:  verdict = plan_ready
if no run: return verdict
switch run.status:
  failed_to_start -> stay plan_ready, attach the start error
  timed_out       -> executed, attach the timeout
  failed          -> executed, attach the exit code
  process_exited  -> executed, state that a zero exit code is not evidence
  completed       -> completed
if metrics exist:            verdict = metrics_recorded
if targets declared and all match: verdict = metrics_matched
else if no targets:          stay metrics_recorded ("cannot be compared with the paper")
if verdict == metrics_matched:
    dirty   -> stay, "uncommitted changes: cannot be tied to a commit"
    no commit -> stay, "not under version control"
    else    -> verdict = reproduced
```

**Four real outcomes from this repository:**

| Situation | Verdict | Reason in the report |
| --- | --- | --- |
| `--expect accuracy=0.8 --expect f1=0.74`, both matched | `reproduced` | — |
| no targets declared | `metrics_recorded` | "No expected metric values were declared…" |
| `python -c "print('done')"` exits 0 | `executed` | "A zero exit code alone is not evidence…" |
| `--expect accuracy=0.99`, observed 0.80 | `metrics_recorded` | "expected 0.99, observed 0.8 (allowed delta 0.02)" |

**`reproduced` means:** on this machine, with this interpreter and this commit, the documented
command ran, the parsed metrics met the declared targets, and the tree was clean. **It does not
mean** the paper is correct, the method beats its baselines, or the result is statistically
significant. Those are in `limitations`, not implied away.

---

## Known weaknesses

1. **Markers are heuristics.** A script that writes files and prints nothing is `process_exited`;
   use `--completion-regex` or print one metric line.
2. **Metrics come from stdout/stderr only.** TensorBoard/W&B-only projects are not captured.
3. **The alias table is finite.** Unusual metric names show up as `missing`, not `mismatch`.
4. **Targets are human input.** RunFix does not read the paper PDF.
5. **One run is not repeatability.** No multi-seed variance analysis yet (see the roadmap).
6. **A clean git tree buys traceability, not correctness.**

---

## Check it yourself

```powershell
runfix reproduce "D:\research\paper-code" --plan-only            # steps 1–2, no execution
runfix diagnose-root-cause "D:\research\paper-code" --traceback-file .\error.log   # step 3
runfix patch-check "D:\research\paper-code" .\candidate.patch    # step 4 gates
runfix reproduce "D:\research\paper-code" --allow-execution --expect accuracy=0.8  # steps 5–6
runfix audit "D:\research\paper-code" --verify                   # every privileged action
```

`runfix bench --ablations` quantifies step 3: removing dependency analysis drops top-1 accuracy
from 1.0 to 0.6364.
