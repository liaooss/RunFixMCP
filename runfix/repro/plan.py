"""Turn project understanding into an explicit, reviewable reproduction plan.

The plan never claims more than its evidence supports. In particular the CUDA/PyTorch
compatibility field is derived from a declared wheel tag (``cu118`` and friends) and the
driver's reported maximum CUDA version; when either is missing the verdict is
``unknown`` rather than a guess.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..diagnostics import diagnose_environment
from ..environments import detect_environment_manifest
from .understanding import understand_project

DRIVER_CUDA = re.compile(r"CUDA Version:\s*(\d+)\.(\d+)")
TORCH_CUDA_TAG = re.compile(r"^cu(\d{2,3})$")


def _parse_driver_cuda(nvidia_summary: str) -> tuple[int, int] | None:
    match = DRIVER_CUDA.search(nvidia_summary or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _tag_version(tag: str | None) -> tuple[int, int] | None:
    """Parse a PyTorch wheel CUDA tag: cu92 -> 9.2, cu118 -> 11.8, cu121 -> 12.1."""
    if not tag:
        return None
    match = TORCH_CUDA_TAG.match(tag)
    if not match:
        return None
    digits = match.group(1)
    if len(digits) == 2:
        return int(digits[0]), int(digits[1])
    return int(digits[:2]), int(digits[2])


def assess_cuda_compatibility(environment: dict[str, Any],
                              nvidia_summary: str) -> dict[str, Any]:
    """Compare a declared PyTorch CUDA wheel tag with the driver's CUDA support."""
    driver = _parse_driver_cuda(nvidia_summary)
    declared_tags = list(environment.get("cuda_tags") or [])
    index_tag = environment.get("torch_index_url_tag")
    if index_tag and index_tag.startswith("cu"):
        declared_tags.append(index_tag)
    declared_tags = sorted(set(tag for tag in declared_tags if tag))

    if not declared_tags:
        return {
            "declared_tags": [],
            "driver_cuda": ".".join(str(part) for part in driver) if driver else None,
            "verdict": "unknown",
            "reason": "No CUDA wheel tag (for example cu121) is declared by the project, so no compatibility claim is possible.",
        }
    if driver is None:
        return {
            "declared_tags": declared_tags,
            "driver_cuda": None,
            "verdict": "unknown",
            "reason": "No NVIDIA driver information is available, so the declared CUDA build cannot be checked.",
        }

    required = _tag_version(declared_tags[0])
    if required is None:
        return {
            "declared_tags": declared_tags,
            "driver_cuda": f"{driver[0]}.{driver[1]}",
            "verdict": "unknown",
            "reason": "The declared CUDA tag could not be parsed as a version.",
        }
    if driver >= required:
        verdict = "supported"
        reason = (
            f"Driver supports CUDA {driver[0]}.{driver[1]}, which satisfies the declared "
            f"PyTorch wheel tag {declared_tags[0]}."
        )
    else:
        verdict = "unsupported"
        reason = (
            f"Driver supports CUDA {driver[0]}.{driver[1]} but the project declares "
            f"{declared_tags[0]}; install would need a newer driver or a CPU/older-CUDA wheel."
        )
    return {
        "declared_tags": declared_tags,
        "driver_cuda": f"{driver[0]}.{driver[1]}",
        "required_cuda": f"{required[0]}.{required[1]}",
        "verdict": verdict,
        "reason": reason,
    }


def _python_step(python_executable: str | None, understanding: dict[str, Any]) -> dict[str, Any]:
    requirement = understanding["environment"].get("python_requirement")
    probe = diagnose_environment(python_executable)
    reported = probe.get("python_version")
    satisfied = None
    if requirement and reported:
        try:
            satisfied = tuple(int(part) for part in reported.split(".")[:2]) >= tuple(
                int(part) for part in requirement.split(".")[:2]
            )
        except ValueError:
            satisfied = None
    return {
        "id": "python",
        "status": "ok" if satisfied is not False else "mismatch",
        "required": requirement,
        "selected_executable": probe.get("python_executable"),
        "selected_version": reported,
        "satisfied": satisfied,
        "requires_authorization": False,
    }


def build_environment_plan(project_path: str | Path,
                           python_executable: str | None = None,
                           include_probe: bool = True) -> dict[str, Any]:
    """Build an explicit plan without install/run: what must happen, and why."""
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    understanding = understand_project(root)
    manifest = detect_environment_manifest(root)

    python_step = (
        _python_step(python_executable, understanding)
        if include_probe else {
            "id": "python",
            "status": "not_probed",
            "required": understanding["environment"].get("python_requirement"),
            "selected_executable": python_executable,
            "selected_version": None,
            "satisfied": None,
            "requires_authorization": False,
        }
    )

    gpu: dict[str, Any] = {"available": None, "nvidia_smi": None, "devices": []}
    cuda: dict[str, Any] = {
        "declared_tags": understanding["environment"].get("cuda_tags", []),
        "driver_cuda": None,
        "verdict": "unknown",
        "reason": "The environment probe was skipped, so no CUDA compatibility claim is made.",
    }
    if include_probe:
        probe = diagnose_environment(python_step.get("selected_executable"))
        torch = probe.get("torch") or {}
        nvidia = probe.get("nvidia_smi") or {}
        gpu = {
            "available": bool(torch.get("cuda_available")),
            "torch_version": torch.get("version"),
            "torch_cuda_version": torch.get("cuda_version"),
            "gpu_count": torch.get("gpu_count"),
            "devices": torch.get("gpu_names") or [],
            "nvidia_smi": bool(nvidia.get("available")),
            "torch_error": torch.get("error"),
        }
        cuda = assess_cuda_compatibility(understanding["environment"], nvidia.get("summary", ""))

    device = understanding["device"]
    gpu_step = {
        "id": "gpu",
        "status": "ok",
        "required_by_code": device["gpu_code_detected"],
        "available": gpu.get("available"),
        "requires_authorization": False,
    }
    if device["gpu_code_detected"] and gpu.get("available") is False:
        gpu_step["status"] = "at_risk"
        gpu_step["warning"] = (
            "The project references CUDA, but the selected interpreter cannot see a GPU. "
            "The run may fall back to CPU or fail; a smaller configuration may be required."
        )
    if cuda.get("verdict") == "unsupported":
        gpu_step["status"] = "at_risk"
        gpu_step["warning"] = cuda["reason"]

    environment_step = {
        "id": "environment",
        "status": "ok" if manifest else "missing_manifest",
        "manager": manifest["manager"] if manifest else None,
        "manifest": manifest["manifest"] if manifest else None,
        "supported_manifests": ["environment.yml", "environment.yaml", "requirements.txt", "pyproject.toml"],
        "requires_authorization": bool(manifest),
    }
    if manifest is None:
        environment_step["detail"] = (
            "No supported manifest was found. RunFix will not guess packages; supply a manifest "
            "or install the environment yourself."
        )

    missing = understanding["missing_datasets"]
    dataset_step = {
        "id": "datasets",
        "status": "ready" if understanding["datasets"] and not missing else (
            "missing" if missing else "unknown"
        ),
        "referenced": len(understanding["datasets"]),
        "missing": [item["value"] for item in missing],
        "requires_authorization": bool(missing),
        "detail": (
            "Dataset paths referenced by configuration are missing. RunFix never downloads "
            "datasets or model weights without explicit authorization."
            if missing else
            "No missing dataset path was detected from configuration."
        ),
    }

    commands = understanding["run_command_candidates"]
    entrypoint_step = {
        "id": "entrypoint",
        "status": "ready" if commands else "missing",
        "selected": commands[0] if commands else None,
        "candidates": commands,
        "requires_authorization": bool(commands),
    }

    steps = [python_step, environment_step, dataset_step, gpu_step, entrypoint_step]
    blockers = [step["id"] for step in steps if step["status"] in {"missing_manifest", "missing"}]
    blockers = [item for item in blockers if item != "environment" or manifest is None]

    return {
        "project_path": str(root),
        "understanding_summary": {
            "readme": understanding["readme"].get("path"),
            "entrypoints": [item["path"] for item in understanding["entrypoints"][:3]],
            "configs": understanding["config_count"],
            "seeds": understanding["seeds"][:5],
            "expected_metrics": understanding["expected_metrics"],
            "frameworks": understanding["environment"]["frameworks"],
        },
        "steps": steps,
        "cuda": cuda,
        "gpu": gpu,
        "blockers": blockers,
        "requires_authorization": {
            "install_environment": bool(manifest),
            "run_project_code": bool(commands),
            "download_data": bool(missing),
        },
        "warnings": understanding["warnings"] + ([cuda["reason"]] if cuda["verdict"] == "unknown" else []),
        "understanding": understanding,
    }
