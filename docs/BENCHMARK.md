# Benchmark and ablation harness (0.9.0)

```powershell
runfix bench                                   # run the suite, write JSON + Markdown
runfix bench --list                            # show every task and its ground truth
runfix bench --task version_unsatisfied        # one task
runfix bench --ablations                       # four variants, compared against the full system
runfix bench --out .\bench-0.9.0               # choose the output directory
```

Every run writes `benchmark.json` + `benchmark.md` (or `ablation.json` + `ablation.md`) into the
output directory, which defaults to `./.runfix-bench/<stamp>/`.

## What the suite is — and is not

`runfix/bench/suite.py` defines **`synthetic-v1`**: eleven small repositories, each with one
injected defect, a fixed environment description (Python version, installed package versions,
driver CUDA version) and a recorded expected root cause plus expected action. Each task is
materialised into a fresh temporary directory per run and deleted afterwards.

It is **not** a curated corpus of published research repositories. It exists because it is
reproducible offline, runs in two seconds, and catches regressions in the diagnosis rules.
Accuracy on real papers is unknown until a curated corpus is evaluated — the report states
that on every run.

| Task | Category | Expected root cause | Expected action |
| --- | --- | --- | --- |
| `missing_declared_dependency` | dependency | `module_declared_missing` | `install_package` |
| `missing_undeclared_dependency` | dependency | `module_missing_undeclared` | `declare_and_install` |
| `version_unsatisfied` | dependency | `version_constraint_unsatisfied` | `install_constraint` |
| `manifest_disagreement` | dependency | `manifest_disagreement` | `choose_manifest` |
| `declared_not_installed_no_traceback` | dependency | `declared_package_not_installed` | `install_package` |
| `python_incompatible` | environment | `python_incompatible` | `select_interpreter` |
| `missing_dataset` | data | `dataset_missing` | `configure_data` |
| `test_assertion_failure` | source | `test_assertion_failure` | `patch_source` |
| `syntax_error` | source | `syntax_error` | `patch_source` |
| `cuda_tag_mismatch` | gpu | `cuda_driver_mismatch` | `select_cuda_wheel` |
| `control_no_signal` | control | `unclassified_error` | `collect_more_evidence` |

The control task matters: a system that always finds *something* is dangerous. Correct
behaviour there is abstention, so the report separates **detection rate** (defect tasks) from
**control abstention rate**.

## Measured metrics

| Metric | Definition |
| --- | --- |
| `top1_accuracy` | expected root-cause kind is ranked first |
| `top3_accuracy` | expected kind appears in the first three |
| `action_accuracy` | the top candidate proposes the expected action |
| `detection_rate` | non-control tasks whose top candidate is not `unclassified_error` |
| `control_abstention_rate` | control tasks whose top candidate *is* `unclassified_error` |
| `mean_top_confidence` | average confidence of the top candidate |
| `mean_latency_seconds` | wall-clock diagnosis time per task |
| `files_modified_by_diagnosis` | files whose SHA-256 changed during diagnosis (must be 0) |
| `per_category_accuracy` | top-1 accuracy grouped by category |

## Results on this machine (0.9.0)

`runfix bench`, 11 tasks, wall time ≈ 2.0 s:

| Metric | Value |
| --- | --- |
| top-1 accuracy | **1.0** |
| top-3 accuracy | **1.0** |
| action accuracy | **1.0** |
| detection rate (10 defect tasks) | **1.0** |
| control abstention rate (1 control task) | **1.0** |
| mean top confidence | 0.7845 |
| mean latency | 0.179 s / task |
| files modified by diagnosis | **0** |

Per-category top-1: control 1.0, data 1.0, dependency 1.0, environment 1.0, gpu 1.0, source 1.0.

A perfect score on a suite we wrote is expected, not impressive. Its value is as a regression
guard: any rule change that breaks one of these eleven construction patterns shows up
immediately.

## Ablation study

`runfix bench --ablations` runs the same tasks with one contribution disabled:

| Variant | Disabled | Top-1 | Δ top-1 vs full |
| --- | --- | --- | --- |
| `full` | — | 1.0 | 0 |
| `no_dependency_analysis` | manifest/version findings | 0.6364 | **−0.3636** |
| `no_classifier_agreement` | the classifier's corroboration bonus | 1.0 | 0 |
| `no_score_ranking` | score-based ordering (insertion order instead) | 0.9091 | **−0.0909** |

Reading:

- Dependency analysis carries real signal: without it, four tasks fall back to a weaker or
  unclassified candidate.
- Score-based ranking matters for at least one task; insertion order is not good enough.
- Classifier corroboration changes no verdict on this suite. That is reported honestly rather
  than removed: it only shifts confidence, and confidence is not what top-1 measures. A suite
  that discriminates it would need tasks where two candidates are otherwise tied.

## Not measured (and why)

| Metric | Why it is missing |
| --- | --- |
| `environment_build_success_rate` | requires installing packages (network) and a working conda |
| `repair_success_rate` | requires authorized installs or LLM-authored patches |
| `test_pass_rate_after_repair` | depends on a repair actually being applied |
| `experiment_reproduction_rate` | needs a curated corpus of real repositories with published results |
| `real_world_root_cause_accuracy` | synthetic fixtures are authored by us |

These are printed in every report. The benchmark never reports a number it did not measure.

## Properties the harness guarantees

- **Deterministic**: each task supplies its own environment description, so results do not
  depend on the host's installed packages or GPU.
- **Isolated**: each task runs in its own temporary directory, created and deleted per run.
- **Read-only check**: file hashes are taken before and after diagnosis; the report proves that
  diagnosis changed nothing.
- **Composable**: `--task` restricts the run, `--json` emits machine-readable output for CI.
