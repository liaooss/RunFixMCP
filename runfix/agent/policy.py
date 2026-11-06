"""Observation-driven decision policy for the offline agent.

The 0.6.x fallback walked a fixed tool list and only looked at how many times each tool
had run, so the same sequence was produced no matter what the run actually reported.
This policy reads the observations instead: the failure category, whether a patch is
pending, which repair round we are in, and what the last verification said. Every
decision carries a rationale and, when the agent stops, a machine-readable
``stop_reason`` that the report keeps.
"""

from __future__ import annotations

from typing import Any

# Categories the code-repair path can act on without guessing the diagnosis.
PATCHABLE_CATEGORIES = {
    "python_syntax",
    "missing_file_or_wrong_path",
    "api_signature_mismatch",
    "type_or_interface_mismatch",
    "tensor_shape_mismatch",
    "test_assertion_failure",
    "permission_or_file_lock",
}

# Categories that need an environment decision, not a source patch.
ENVIRONMENT_CATEGORIES = {
    "missing_dependency_or_import_path",
    "import_or_binary_compatibility",
    "cuda_torch_compatibility",
}

# Categories where RunFix has no safe automatic action and should stop with advice.
ADVISORY_CATEGORIES = {
    "gpu_out_of_memory": "needs_configuration_change",
    "network_tls_certificate": "needs_network_configuration",
    "unclassified_runtime_error": "needs_more_evidence",
}


def _runtime(observations: dict[str, Any]) -> dict[str, Any] | None:
    return observations.get("post_patch_runtime") or observations.get("runtime")


def _failed(runtime: dict[str, Any] | None) -> bool:
    return bool(
        runtime
        and (runtime.get("returncode") not in (0, None) or runtime.get("timed_out"))
    )


def _finish(rationale: str, stop_reason: str) -> dict[str, Any]:
    return {"tool": "finish", "rationale": rationale, "stop_reason": stop_reason, "planner": "policy"}


def decide(state: dict[str, Any], available: list[str]) -> dict[str, Any]:
    """Choose the next tool from the currently available set, using the evidence."""
    observations = state.get("observations", {})
    analysis = observations.get("analysis") or {}
    category = analysis.get("category")
    runtime = _runtime(observations)
    failed = _failed(runtime)

    def choose(tool: str, rationale: str, **extra: Any) -> dict[str, Any]:
        decision = {"tool": tool, "rationale": rationale, "planner": "policy"}
        decision.update(extra)
        return decision

    # Discovery first: nothing can be judged before the project and interpreter are known.
    if "scan_project" in available:
        return choose("scan_project", "Establish the repository structure and declared dependencies first.")
    if "diagnose_environment" in available:
        return choose("diagnose_environment",
                      "Collect interpreter, framework and CUDA evidence before judging any failure.")
    if "static_validation" in available:
        return choose("static_validation",
                      "Check syntax and unresolved imports so a static defect is not mistaken for a runtime one.")

    if ("run_tests" in available or "run_user_command" in available) and runtime is None:
        if "run_tests" in available:
            return choose("run_tests", "Execution is authorized; run the repository test suite to obtain runtime evidence.")
        return choose("run_user_command", "Execution is authorized; run the exact command the user supplied.")

    if not runtime:
        if "finish" in available:
            return _finish(
                "No runtime evidence can be produced because execution was not authorized.",
                "no_execution_authorized",
            )
        if available:
            return choose(available[0], "Continue with the next authorized inspection step.")
        return _finish("No further authorized action is available.", "no_action_available")

    if not failed:
        if observations.get("patch_apply") or observations.get("post_patch_runtime"):
            return _finish(
                "The authorized runtime check passed after the repair attempt.",
                "verified_after_repair",
            )
        return _finish("The authorized runtime check passed.", "verified_baseline")

    # The run failed. Re-plan from the diagnosis rather than from a fixed list.
    if "analyze_failure" in available:
        previous = observations.get("previous_analysis") or {}
        if previous.get("category"):
            return choose(
                "analyze_failure",
                f"The previous repair did not resolve a {previous['category']} failure; re-analyze "
                "the new log instead of repeating the same patch.",
                strategy=f"reanalyze_after_{previous['category']}",
            )
        return choose("analyze_failure",
                      "A runtime failure exists; classify it from the traceback and source context before acting.")

    if category in ENVIRONMENT_CATEGORIES:
        if "create_isolated_environment" in available:
            return choose(
                "create_isolated_environment",
                f"The failure is classified as {category}; build the isolated environment from the "
                "declared manifest because installation was authorized.",
                strategy="environment_repair",
            )
        return _finish(
            f"The failure is classified as {category}, which requires building an isolated "
            "environment, but dependency installation was not authorized.",
            "needs_install_permission",
        )

    if category in ADVISORY_CATEGORIES:
        return _finish(
            f"The failure is classified as {category}. RunFix has no evidence-backed automatic "
            "action for it; the recommended actions are in the analysis.",
            ADVISORY_CATEGORIES[category],
        )

    if category in PATCHABLE_CATEGORIES:
        if "validate_patch" in available:
            return choose("validate_patch",
                          f"The failure is classified as {category}; validate the proposed minimal patch "
                          "before touching any file.",
                          strategy=f"patch_for_{category}")
        if "apply_patch" in available:
            return choose("apply_patch",
                          "The patch passed validation and write permission was granted; apply it as a transaction.")
        if "verify_after_patch" in available:
            return choose("verify_after_patch",
                          "Re-run the selected tests to decide whether the patch actually fixed the failure.")
        return _finish(
            f"The failure is classified as {category} and needs a source patch, but no validated patch "
            "or write permission is available.",
            "needs_write_permission_or_patch",
        )

    if "create_isolated_environment" in available:
        return choose(
            "create_isolated_environment",
            "No high-confidence diagnosis exists yet; the most reversible remaining action is to build "
            "the environment declared by the project.",
            strategy="install_without_diagnosis",
        )
    if "finish" in available:
        return _finish(
            f"The runtime failed and the diagnosis is '{category or 'unavailable'}'. RunFix will not "
            "guess a repair without evidence.",
            "no_evidence_backed_repair",
        )
    return choose(available[0], "Continue with the next authorized step.") if available else \
        _finish("No further authorized action is available.", "no_action_available")


def summarize_decision(decision: dict[str, Any]) -> str:
    """Compact one-line description used in reports."""
    planner = decision.get("planner", "policy")
    tool = decision.get("tool")
    strategy = decision.get("strategy")
    suffix = f" [{strategy}]" if strategy else ""
    return f"{planner}:{tool}{suffix} — {decision.get('rationale', '')}"
