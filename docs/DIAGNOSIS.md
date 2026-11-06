# Evidence-based diagnosis (0.8.0)

RunFix does not guess a cause. It builds an evidence graph, compares declared dependency
constraints with the interpreter it actually inspected, and ranks candidates that each cite
the facts behind them.

```powershell
runfix diagnose-root-cause "D:\research\paper-code" --traceback-file .\error.log
runfix diagnose-root-cause "D:\research\paper-code" --python "D:\envs\paper\python.exe" --include-understanding
```

Available over MCP as `explain_root_causes` (read-only).

## 1. The evidence graph (`runfix/diagnosis/evidence.py`)

Nodes are facts; edges are relations that carry their own evidence.

| Node kind | Example |
| --- | --- |
| `project`, `git_revision`, `environment`, `framework` | the repository, its commit, the inspected interpreter, torch |
| `dependency`, `installed_package` | `numpy>=1.20` declared in `requirements.txt`; `numpy` installed as `1.24.0` |
| `module`, `source_file`, `test_file`, `exception` | `import numpy` seen at `train.py:3`; `ModuleNotFoundError` |

| Edge | Meaning |
| --- | --- |
| `failed_with`, `raised_in` | the exception and the project-local frames it was raised in |
| `imports`, `references` | a file imports / references a module |
| `declares`, `resolved_to` | the project declares a requirement; that requirement resolved to an installed version |
| `tested_by`, `has_test`, `test_selected_because` | traceback-to-test relationships and why a test was selected |
| `runs_on`, `provides`, `has_revision` | environment and version-control facts |

Everything is derived from static analysis plus one interpreter probe. No target module is
imported and no project command is executed.

## 2. Dependency analysis (`runfix/diagnosis/dependencies.py`)

RunFix ships with no runtime dependencies, so the module implements the PEP 440 subset that
research manifests use: `==`, `!=`, `>=`, `<=`, `>`, `<`, `~=`, `===` and `==X.Y.*`.
Anything it cannot parse is reported as *unknown*, never as satisfied.

It reads `requirements.txt`, `environment.yml` (conda pins and the `pip:` section) and
`pyproject.toml`, then compares them with the versions `importlib.metadata` reports for the
**selected** interpreter. Findings:

| Kind | Meaning | Proposed resolution |
| --- | --- | --- |
| `not_installed` | declared but absent | install the declared constraint |
| `version_unsatisfied` | installed but outside the declared range | install the declared range |
| `manifest_disagreement` | two manifests disagree | choose the authoritative manifest (RunFix will not guess) |
| `python_incompatible` | `requires-python` not satisfied | build the environment with a matching interpreter |
| `version_unparsable` | constraint or version not parseable | reported as informational |

## 3. Ranked candidates (`runfix/diagnosis/rootcause.py`)

Each candidate contains:

```json
{
  "id": "rc1",
  "kind": "module_declared_missing",
  "statement": "'numpy' is declared by the project but is not installed in the selected interpreter.",
  "confidence": 0.95,
  "rank": 1,
  "evidence": [
    {"source": "requirements.txt", "detail": "numpy>=1.20"},
    {"source": "traceback", "detail": "No module named 'numpy'"},
    {"source": "analysis", "detail": "the traceback classifier independently reported 'missing_dependency_or_import_path'"}
  ],
  "contradicting": [],
  "action": {"kind": "install_package", "description": "...", "command": ["pip", "install", "numpy>=1.20"]}
}
```

**Confidence is deterministic**: a base score per kind, plus 0.03 per additional piece of
evidence (capped at +0.09), plus 0.06 when the traceback classifier independently agrees,
minus 0.12 per contradicting fact, clamped to [0.05, 0.98]. It is a *score over cited
evidence*, not a learned probability, and the report says so.

Candidate kinds:

| Kind | Typical trigger | Action |
| --- | --- | --- |
| `module_declared_missing` | `No module named X`, X declared, not installed | `install_package` |
| `module_missing_undeclared` | `No module named X`, X not declared anywhere | `declare_and_install` |
| `version_constraint_unsatisfied` | installed version outside the declared range | `install_constraint` |
| `declared_package_not_installed` | declared and absent, no traceback | `install_package` |
| `manifest_disagreement` | two manifests disagree | `choose_manifest` |
| `python_incompatible` | `requires-python` not satisfied | `select_interpreter` |
| `cuda_driver_mismatch` | declared `cuXXX` newer than the driver's CUDA | `select_cuda_wheel` |
| `cuda_unavailable` | CUDA code, no visible GPU | `run_on_cpu_or_gpu_host` |
| `dataset_missing` | configured data path does not exist | `configure_data` |
| `api_signature_mismatch` | `unexpected keyword argument` | `patch_source` |
| `test_assertion_failure` | `AssertionError` with a project frame | `patch_source` |
| `unclassified_error` | nothing matched | `collect_more_evidence` |

## 4. How the agent uses it

`analyze_failure` attaches the ranked candidates to the analysis, and the offline policy in
`runfix/agent/policy.py` decides from **the top candidate's action**, not from the coarse
category:

| Top action | Agent behaviour |
| --- | --- |
| `install_package` / `install_constraint` / `declare_and_install` | build the isolated environment if authorized, otherwise stop with `needs_install_permission` |
| `patch_source` | validate → apply → verify if write is authorized, otherwise `needs_write_permission_or_patch` |
| `select_interpreter` | stop with `needs_interpreter_selection` |
| `configure_data` | stop with `needs_data_configuration` |
| `select_cuda_wheel` | stop with `needs_cuda_wheel_selection` |
| `run_on_cpu_or_gpu_host` | stop with `needs_gpu_host` |
| `choose_manifest` | stop with `needs_manifest_decision` |
| `collect_more_evidence` | stop with `needs_more_evidence` |

The step records `strategy=root_cause:<kind>`, so the report shows which candidate drove the
action.

## 5. Honest boundaries

- A candidate is a hypothesis with cited evidence, not a proof. RunFix never applies a
  candidate's `pip install` automatically; it builds the project's declared environment.
- RunFix still cannot author code patches. `patch_source` candidates describe what kind of
  change is needed; the patch text comes from the LLM planner.
- The evidence graph is bounded (800 nodes, 60 config files, 1500 import statements); a very
  large repository is summarised rather than fully expanded.
- `probe_package_versions` inspects only the interpreter RunFix is told to use. Passing the
  wrong `--python` yields confidently wrong conclusions — the report prints the interpreter
  it inspected for exactly this reason.
