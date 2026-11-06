"""Ranked root-cause candidates with explicit confidence and cited evidence.

Every candidate is generated from a deterministic rule over the evidence graph, the
dependency analysis and the environment probe. No large language model is involved, so a
candidate can always explain itself: it lists the facts that raised it and the facts that
would contradict it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .dependencies import analyse_dependencies, declared_requirements
from .evidence import build_evidence_graph

MISSING_MODULE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")
UNEXPECTED_KWARG = re.compile(r"unexpected keyword argument ['\"]([^'\"]+)['\"]")
ATTRIBUTE_MISSING = re.compile(r"module ['\"]([^'\"]+)['\"] has no attribute ['\"]([^'\"]+)['\"]")
CUDA_TAG = re.compile(r"\bcu(\d{2,3})\b", re.IGNORECASE)

BASE_CONFIDENCE = {
    "version_constraint_unsatisfied": 0.88,
    "module_declared_missing": 0.86,
    "module_missing_undeclared": 0.80,
    "python_incompatible": 0.85,
    "cuda_driver_mismatch": 0.82,
    "cuda_unavailable": 0.74,
    "manifest_disagreement": 0.66,
    "declared_package_not_installed": 0.58,
    "dataset_missing": 0.78,
    "api_signature_mismatch": 0.62,
    "test_assertion_failure": 0.70,
    "unclassified_error": 0.30,
}

CATEGORY_ALIGNMENT = {
    "missing_dependency_or_import_path": {"module_declared_missing", "module_missing_undeclared"},
    "import_or_binary_compatibility": {"version_constraint_unsatisfied", "manifest_disagreement"},
    "cuda_torch_compatibility": {"cuda_driver_mismatch", "cuda_unavailable"},
    "missing_file_or_wrong_path": {"dataset_missing"},
    "api_signature_mismatch": {"api_signature_mismatch"},
    "test_assertion_failure": {"test_assertion_failure"},
}

ACTION_BY_KIND = {
    "module_declared_missing": "install_package",
    "module_missing_undeclared": "declare_and_install",
    "declared_package_not_installed": "install_package",
    "version_constraint_unsatisfied": "install_constraint",
    "manifest_disagreement": "choose_manifest",
    "python_incompatible": "select_interpreter",
    "cuda_driver_mismatch": "select_cuda_wheel",
    "cuda_unavailable": "run_on_cpu_or_gpu_host",
    "dataset_missing": "configure_data",
    "api_signature_mismatch": "patch_source",
    "test_assertion_failure": "patch_source",
    "unclassified_error": "collect_more_evidence",
}


def _clamp(value: float) -> float:
    return max(0.05, min(0.98, round(value, 3)))


def _candidate(kind: str, statement: str, evidence: list[dict[str, Any]],
               action: dict[str, Any], *, category: str | None = None,
               contradicting: list[dict[str, Any]] | None = None,
               corroboration: float = 0.0) -> dict[str, Any]:
    confidence = BASE_CONFIDENCE.get(kind, 0.3)
    confidence += min(0.09, 0.03 * max(0, len(evidence) - 1))
    confidence += corroboration
    if contradicting:
        confidence -= 0.12 * len(contradicting)
    return {
        "kind": kind,
        "statement": statement,
        "confidence": _clamp(confidence),
        "category": category,
        "evidence": evidence,
        "contradicting": contradicting or [],
        "action": {**action, "kind": action.get("kind") or ACTION_BY_KIND.get(kind, "manual")},
    }


def _manifest_evidence(declared: list[dict[str, Any]], package: str) -> list[dict[str, Any]]:
    return [
        {"source": f"{item['manifest']}", "detail": item["raw"]}
        for item in declared if item["canonical"] == package
    ]


def _cuda_candidates(root: Path, environment: dict[str, Any],
                     declared_text: str) -> list[dict[str, Any]]:
    from ..repro.plan import assess_cuda_compatibility

    tags = sorted({f"cu{match}" for match in CUDA_TAG.findall(declared_text)})
    nvidia = (environment.get("nvidia_smi") or {})
    assessment = assess_cuda_compatibility({"cuda_tags": tags, "torch_index_url_tag": None},
                                           nvidia.get("summary", "") if nvidia.get("available") else "")
    torch = environment.get("torch") or {}
    candidates: list[dict[str, Any]] = []

    if assessment["verdict"] == "unsupported":
        candidates.append(_candidate(
            "cuda_driver_mismatch",
            f"The project declares a CUDA {assessment.get('required_cuda')} PyTorch build, but the "
            f"installed driver supports CUDA {assessment.get('driver_cuda')}.",
            [
                {"source": "requirements/README", "detail": ", ".join(assessment["declared_tags"])},
                {"source": "nvidia-smi", "detail": f"CUDA Version: {assessment.get('driver_cuda')}"},
            ],
            {"kind": "select_cuda_wheel",
             "description": "Install a PyTorch wheel built for the driver's CUDA version (or a CPU wheel), "
                            "or update the driver outside RunFix.",
             "command": None},
            corroboration=0.05,
        ))

    if torch.get("installed") and torch.get("cuda_available") is False and tags:
        candidates.append(_candidate(
            "cuda_unavailable",
            "The project expects CUDA, but the selected interpreter cannot see a GPU.",
            [
                {"source": "environment probe", "detail": f"torch {torch.get('version')} reports cuda_available=False"},
                {"source": "project", "detail": "CUDA wheel tags declared: " + ", ".join(tags)},
            ],
            {"kind": "run_on_cpu_or_gpu_host",
             "description": "Run on a machine with a visible GPU, or reduce the configuration to a CPU/GPU-free path.",
             "command": None},
        ))
    return candidates


def _module_candidates(analysis: dict[str, Any] | None, declared: list[dict[str, Any]],
                       installed: dict[str, str | None],
                       dependency_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    message = str((analysis or {}).get("evidence", {}).get("traceback_tail", "")) or ""
    covered_packages: set[str] = set()
    match = MISSING_MODULE.search(message)
    if match:
        module = match.group(1)
        root_module = module.split(".")[0]
        canonical = re.sub(r"[-_.]+", "-", root_module.lower())
        declared_names = {item["canonical"] for item in declared}
        from ..diagnostics import canonical_distribution
        distribution = canonical_distribution(root_module)
        is_declared = canonical in declared_names or distribution in declared_names
        installed_version = installed.get(distribution) or installed.get(canonical)

        if is_declared and installed_version is None:
            specifier = next((item["specifier"] for item in declared
                              if item["canonical"] in {canonical, distribution}), "")
            covered_packages.add(canonical)
            covered_packages.add(distribution)
            candidates.append(_candidate(
                "module_declared_missing",
                f"'{root_module}' is declared by the project but is not installed in the selected interpreter.",
                _manifest_evidence(declared, canonical) + [
                    {"source": "traceback", "detail": f"No module named '{module}'"},
                ],
                {"kind": "install_package",
                 "description": f"Install the declared package into the isolated environment: {root_module}{specifier}.",
                 "command": ["pip", "install", f"{root_module}{specifier}"],
                 "package": root_module},
                category="missing_dependency_or_import_path",
            ))
        elif not is_declared:
            candidates.append(_candidate(
                "module_missing_undeclared",
                f"'{root_module}' is imported by the project but no manifest declares it and it is not installed.",
                [
                    {"source": "traceback", "detail": f"No module named '{module}'"},
                    {"source": "manifests", "detail": "no matching declaration was found"},
                ],
                {"kind": "declare_and_install",
                 "description": f"Add '{root_module}' to the project's manifest and install it in the isolated environment.",
                 "command": ["pip", "install", root_module],
                 "package": root_module},
                category="missing_dependency_or_import_path",
            ))

    for finding in dependency_analysis.get("findings", []):
        if finding["kind"] == "not_installed":
            if finding["package"] in covered_packages:
                continue
            candidates.append(_candidate(
                "declared_package_not_installed",
                f"{finding['package']} is declared but not installed in the selected interpreter; "
                "any import of it will fail.",
                finding["evidence"] + [
                    {"source": "environment probe", "detail": "distribution is not installed"},
                ],
                {"kind": "install_package",
                 "description": f"Install {finding['package']}{finding['specifier']} into the isolated environment.",
                 "command": ["pip", "install", f"{finding['package']}{finding['specifier']}"],
                 "package": finding["package"]},
            ))
        elif finding["kind"] == "version_unsatisfied":
            candidates.append(_candidate(
                "version_constraint_unsatisfied",
                f"{finding['package']}=={finding['installed']} violates the declared constraint {finding['specifier']}.",
                finding["evidence"] + [
                    {"source": "environment probe", "detail": f"installed {finding['package']}=={finding['installed']}"},
                ],
                {"kind": "install_constraint",
                 "description": f"Install {finding['package']}{finding['specifier']} inside the isolated environment.",
                 "command": ["pip", "install", f"{finding['package']}{finding['specifier']}"],
                 "package": finding["package"]},
            ))
        elif finding["kind"] == "manifest_disagreement":
            candidates.append(_candidate(
                "manifest_disagreement",
                f"{finding['package']} is declared with conflicting constraints: {', '.join(finding['specifiers'])}.",
                finding["evidence"],
                {"kind": "choose_manifest",
                 "description": "Decide which manifest is authoritative and align the other one by hand.",
                 "command": None},
            ))
        elif finding["kind"] == "python_incompatible":
            candidates.append(_candidate(
                "python_incompatible",
                finding["detail"],
                finding["evidence"] + [
                    {"source": "environment probe", "detail": f"python {finding['installed']}"},
                ],
                {"kind": "select_interpreter",
                 "description": "Build the isolated environment with an interpreter that satisfies the requirement.",
                 "command": None},
            ))
    return candidates


def _misc_candidates(analysis: dict[str, Any] | None,
                     understanding: dict[str, Any] | None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    traceback_tail = str((analysis or {}).get("evidence", {}).get("traceback_tail", ""))
    category = (analysis or {}).get("category")

    if UNEXPECTED_KWARG.search(traceback_tail):
        name = UNEXPECTED_KWARG.search(traceback_tail).group(1)
        candidates.append(_candidate(
            "api_signature_mismatch",
            f"A call site passes '{name}', which the installed library version does not accept.",
            [
                {"source": "traceback", "detail": f"unexpected keyword argument '{name}'"},
                {"source": "analysis", "detail": f"category {category}"},
            ],
            {"kind": "patch_source",
             "description": f"Adjust the call site in the project source, or pin the library version that accepts '{name}'.",
             "command": None},
            category=category,
        ))

    if ATTRIBUTE_MISSING.search(traceback_tail):
        match = ATTRIBUTE_MISSING.search(traceback_tail)
        candidates.append(_candidate(
            "api_signature_mismatch",
            f"'{match.group(1)}' has no attribute '{match.group(2)}' in the installed version.",
            [{"source": "traceback", "detail": match.group(0)[:200]}],
            {"kind": "patch_source",
             "description": "Pin the library version that provides the attribute, or update the code to the installed API.",
             "command": None},
            category=category,
        ))

    if understanding:
        missing = understanding.get("missing_datasets") or []
        if missing:
            candidates.append(_candidate(
                "dataset_missing",
                f"{len(missing)} configured dataset path(s) do not exist on this machine.",
                [{"source": item["config"], "detail": f"{item['key']} = {item['value']}"} for item in missing[:5]],
                {"kind": "configure_data",
                 "description": "Provide or download the dataset (authorization required) or point the configuration at an existing copy.",
                 "command": None},
                category="missing_file_or_wrong_path",
            ))

    if category == "test_assertion_failure":
        contexts = (analysis or {}).get("evidence", {}).get("source_context") or []
        evidence = [{"source": f"{item['file']}:{item['line']}", "detail": (item.get("excerpt") or "")[:300]}
                    for item in contexts[:3]]
        candidates.append(_candidate(
            "test_assertion_failure",
            "A test assertion failed; the fault is either in the implementation or in the test's expectation.",
            evidence or [{"source": "traceback", "detail": "AssertionError"}],
            {"kind": "patch_source",
             "description": "Compare the assertion with the documented behaviour and make the smallest change that satisfies it.",
             "command": None},
            category=category,
        ))
    return candidates


def rank_root_causes(project_path: str | Path,
                     traceback_text: str | None = None,
                     python_executable: str | None = None,
                     include_probe: bool = True,
                     include_understanding: bool = False,
                     max_candidates: int = 8) -> dict[str, Any]:
    """Return ranked, evidence-backed root-cause candidates for a project or a failure."""
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    bundle = build_evidence_graph(root, traceback_text, python_executable, include_probe)
    environment = bundle["environment"]
    declared = bundle["declared_requirements"]
    installed = bundle["installed_versions"]
    analysis = bundle["analysis"]

    dependency_analysis = analyse_dependencies(
        root, installed, environment.get("python_version") if environment else None
    )

    understanding = None
    if include_understanding:
        from ..repro.understanding import understand_project
        understanding = understand_project(root)

    declared_text = ""
    for name in ("requirements.txt", "environment.yml", "environment.yaml", "pyproject.toml"):
        path = root / name
        if path.is_file():
            try:
                declared_text += "\n" + path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

    candidates = []
    candidates += _module_candidates(analysis, declared, installed, dependency_analysis)
    candidates += _misc_candidates(analysis, understanding)
    candidates += _cuda_candidates(root, environment, declared_text)

    category = (analysis or {}).get("category")
    aligned = CATEGORY_ALIGNMENT.get(category, set())
    for index, candidate in enumerate(candidates, start=1):
        candidate["id"] = f"rc{index}"
        corroboration = 0.06 if candidate["kind"] in aligned else 0.0
        if corroboration:
            candidate["confidence"] = _clamp(candidate["confidence"] + corroboration)
            candidate["evidence"].append({
                "source": "analysis",
                "detail": f"the traceback classifier independently reported '{category}'",
            })

    if not candidates:
        candidates.append(_candidate(
            "unclassified_error",
            "No rule produced a high-confidence cause; more evidence (full traceback, interpreter, "
            "manifest and configuration) is required.",
            [{"source": "analysis", "detail": category or "no traceback supplied"}],
            {"kind": "collect_more_evidence",
             "description": "Supply the complete traceback and the exact interpreter used for the failing run.",
             "command": None},
            category=category,
        ))

    for candidate in candidates:
        candidate["score"] = round(
            candidate["confidence"] + 0.01 * min(5, len(candidate["evidence"])), 4
        )
    candidates.sort(key=lambda item: (-item["score"], item["kind"]))
    for index, candidate in enumerate(candidates, start=1):
        candidate["rank"] = index
        candidate["id"] = f"rc{index}"
    candidates = candidates[: max(1, min(int(max_candidates), 20))]

    return {
        "project_path": str(root),
        "traceback_supplied": bool(traceback_text),
        "analysis": None if analysis is None else {
            "category": analysis.get("category"),
            "confidence": analysis.get("confidence"),
            "exception": analysis.get("exception"),
            "message": analysis.get("message"),
        },
        "candidates": candidates,
        "top": candidates[0] if candidates else None,
        "dependency_analysis": dependency_analysis,
        "environment": {
            "python_executable": environment.get("python_executable"),
            "python_version": environment.get("python_version"),
            "torch": (environment.get("torch") or {}).get("version"),
            "cuda_available": (environment.get("torch") or {}).get("cuda_available"),
        },
        "evidence_graph": bundle["graph"],
        "notes": [
            "Confidence is a deterministic score over the cited evidence, not a learned probability.",
            "A candidate describes a plausible cause; it is not a proof. Apply its action only if the evidence applies to your case.",
        ],
    }
