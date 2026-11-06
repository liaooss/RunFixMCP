"""Project understanding for research-code repositories.

Produces a structured, evidence-carrying description of how a repository is meant to
be installed and run. Everything here is read-only: no module of the target project is
imported and no command of the target project is executed.

The output deliberately separates *evidence* (what a file actually says) from
*inference* (what RunFix concluded), so a report can show why it believes something.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..common import redact
from ..scanner import scan_project

README_NAMES = ("README.md", "README.rst", "README.txt", "readme.md", "README.MD", "Readme.md")

CONFIG_DIRS = ("configs", "config", "conf", "cfg", "options", "experiments", "scripts/config")
CONFIG_SUFFIXES = {".yaml", ".yml", ".json", ".toml"}

ENTRYPOINT_PRIORITY = {
    "train.py": 100,
    "main.py": 95,
    "run.py": 90,
    "run_train.py": 88,
    "finetune.py": 85,
    "train_model.py": 84,
    "pretrain.py": 82,
    "evaluate.py": 70,
    "eval.py": 70,
    "test.py": 65,
    "inference.py": 60,
    "predict.py": 60,
    "demo.py": 50,
    "app.py": 45,
    "server.py": 40,
}

SHELL_PROMPT = re.compile(r"^\s*(?:\$|>|PS[^>]*>|#)\s+")
FENCE = re.compile(r"```([A-Za-z0-9_+-]*)\n(.*?)```", re.DOTALL)
RUN_COMMAND = re.compile(
    r"^(?:python3?|torchrun|accelerate\s+launch|deepspeed|bash|sh|conda\s+run)\s+\S"
)
INSTALL_COMMAND = re.compile(
    r"^(?:pip3?\s+install|python3?\s+-m\s+pip\s+install|conda\s+(?:env\s+create|install)|"
    r"uv\s+(?:pip\s+)?install|poetry\s+install|git\s+clone)"
)
CUDA_TAG = re.compile(r"cu(\d{2,3})", re.IGNORECASE)
PATH_LIKE = re.compile(r"[\\/]|\.(?:csv|tsv|json|h5|hdf5|npy|npz|pt|pth|ckpt|txt|png|jpg|zarr|parquet)$", re.IGNORECASE)

SEED_KEYS = ("seed", "random_seed", "manual_seed", "rand_seed", "numpy_seed", "torch_seed")
DATASET_KEYS = re.compile(
    r"(?:data|dataset|corpus|image_dir|dataset_dir)", re.IGNORECASE
)
# Paths the project *writes* are not datasets: a missing output directory is normal.
OUTPUT_KEYS = re.compile(
    r"(?:out|output|log|save|ckpt|checkpoint|result|weight|exp|run)_?(?:dir|path|root|file)?$",
    re.IGNORECASE,
)
TRAINING_KEYS = ("epochs", "epoch", "max_epochs", "num_epochs", "batch_size", "lr",
                 "learning_rate", "weight_decay", "optimizer", "model", "backbone")
METRIC_WORDS = ("accuracy", "acc", "f1", "precision", "recall", "auc", "rmse", "mae",
                "mse", "psnr", "ssim", "bleu", "rouge", "loss", "perplexity", "map", "iou")


def _read_text(path: Path, limit: int = 400_000) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _clean_command(line: str) -> str:
    return SHELL_PROMPT.sub("", line.strip()).strip()


def _parse_readme(text: str) -> dict[str, Any]:
    headings = [line.strip() for line in text.splitlines()
                if re.match(r"^#{1,6}\s+\S", line.strip())]
    blocks = [match.group(2) for match in FENCE.finditer(text)]
    install: list[str] = []
    run: list[str] = []
    for block in blocks:
        for raw in block.splitlines():
            line = _clean_command(raw)
            if not line or line.startswith("#"):
                continue
            if INSTALL_COMMAND.match(line):
                install.append(line)
            elif RUN_COMMAND.match(line):
                run.append(line)
    return {
        "headings": headings[:80],
        "install_commands": sorted(set(install))[:40],
        "run_commands": sorted(set(run))[:40],
        "code_block_count": len(blocks),
    }


def _load_structured(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Load a config file. Returns (mapping, parser-name) or (None, error)."""
    text = _read_text(path, 300_000)
    if not text.strip():
        return None, "empty"
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            value = json.loads(text)
        except ValueError as exc:
            return None, f"json: {exc}"
        return (value if isinstance(value, dict) else None), "json"
    if suffix == ".toml":
        try:
            import tomllib  # Python 3.11+
        except ImportError:  # pragma: no cover - Python 3.10
            return None, "toml parser unavailable"
        try:
            value = tomllib.loads(text)
        except Exception as exc:  # tomllib raises TOMLDecodeError
            return None, f"toml: {exc}"
        return (value if isinstance(value, dict) else None), "toml"
    try:
        import yaml  # optional
    except ImportError:
        return _scan_flat_keys(text), "flat-key-scan"
    try:
        value = yaml.safe_load(text)
    except Exception as exc:
        return _scan_flat_keys(text), f"flat-key-scan (yaml failed: {exc})"
    return (value if isinstance(value, dict) else None), "yaml"


def _scan_flat_keys(text: str) -> dict[str, Any]:
    """Fallback for YAML without PyYAML: capture top-level ``key: value`` pairs."""
    result: dict[str, Any] = {}
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent > 2:
            continue
        match = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*:\s*(.+?)\s*$", line)
        if not match:
            continue
        key, raw = match.group(1), match.group(2)
        if raw in {"", "|", ">", "{}", "[]"}:
            continue
        if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
            raw = raw[1:-1]
        result[key] = raw
    return result


def _config_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for name in CONFIG_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix.lower() in CONFIG_SUFFIXES and path.is_file() and not path.is_symlink():
                if len(path.relative_to(root).parts) <= 4 and path.stat().st_size <= 300_000:
                    found.append(path)
            if len(found) >= 60:
                return found
    for name in ("config.yaml", "config.yml", "config.json", "defaults.yaml", "params.yaml"):
        path = root / name
        if path.is_file() and not path.is_symlink():
            found.append(path)
    return found[:60]


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, Any]]:
    if depth > 4:
        return []
    items: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            items.extend(_flatten(child, f"{prefix}.{key}" if prefix else str(key), depth + 1))
    elif isinstance(value, list) and len(value) <= 20:
        for index, child in enumerate(value):
            items.extend(_flatten(child, f"{prefix}[{index}]", depth + 1))
    else:
        items.append((prefix, value))
    return items


def _dataset_references(root: Path, pairs: list[tuple[str, str, Any]]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for relative, key, value in pairs:
        if not isinstance(value, str) or not value.strip():
            continue
        leaf = key.rsplit(".", 1)[-1]
        if OUTPUT_KEYS.search(leaf):
            continue
        if not DATASET_KEYS.search(leaf):
            continue
        if not PATH_LIKE.search(value):
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = (root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        exists = candidate.exists()
        references.append({
            "config": relative,
            "key": key,
            "value": redact(value)[:300],
            "resolved_path": str(candidate),
            "inside_project": _is_inside(candidate, root),
            "exists": exists,
        })
    return references


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _seeds(pairs: list[tuple[str, str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for relative, key, value in pairs:
        leaf = key.rsplit(".", 1)[-1].lower()
        if leaf in SEED_KEYS or leaf.endswith("_seed"):
            found.append({"source": relative, "key": key, "value": value})
    return found


def _training_parameters(pairs: list[tuple[str, str, Any]]) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for relative, key, value in pairs:
        leaf = key.rsplit(".", 1)[-1].lower()
        if leaf in TRAINING_KEYS and isinstance(value, (int, float, str, bool)):
            parameters.setdefault(leaf, {"value": value, "source": relative, "key": key})
    return parameters


def _entrypoints(root: Path, scan: dict[str, Any], run_commands: list[str]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    mentioned = " ".join(run_commands)
    for relative in scan.get("entrypoint_candidates", []):
        name = Path(relative).name.lower()
        score = ENTRYPOINT_PRIORITY.get(name, 30)
        evidence: list[str] = [f"file name is a common research entrypoint ({name})"]
        if relative.replace("\\", "/") in mentioned or name in mentioned:
            score += 40
            evidence.append("a README run command references this file")
        text = _read_text(root / relative, 200_000)
        if 'if __name__ == "__main__"' in text or "__name__=='__main__'" in text.replace(" ", ""):
            score += 20
            evidence.append("has a __main__ guard")
        if "argparse" in text:
            score += 15
            evidence.append("parses command-line arguments with argparse")
        if "torchrun" in text or "DistributedDataParallel" in text:
            score += 10
            evidence.append("contains distributed-training code")
        if not text:
            evidence.append("file could not be read (size limit or encoding)")
        ranked.append({"path": relative, "score": score, "evidence": evidence})
    ranked.sort(key=lambda item: (-item["score"], item["path"]))
    return ranked[:10]


def _run_command_candidates(entrypoints: list[dict[str, Any]], readme_runs: list[str],
                            root: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for command in readme_runs:
        candidates.append({
            "argv": command.split(),
            "raw": command,
            "source": "README",
            "confidence": "documented",
            "needs_review": True,
        })
    for item in entrypoints[:3]:
        relative = item["path"]
        if any(relative in candidate["raw"] for candidate in candidates):
            continue
        candidates.append({
            "argv": ["python", relative],
            "raw": f"python {relative}",
            "source": "entrypoint-inference",
            "confidence": "inferred",
            "needs_review": True,
            "note": "No arguments were supplied; a research entrypoint usually needs a config path.",
        })
    return candidates[:10]


def _environment_requirements(root: Path, readme_text: str, scan: dict[str, Any]) -> dict[str, Any]:
    manifests = list(scan.get("dependency_manifests", []))
    combined = readme_text
    for name in ("requirements.txt", "environment.yml", "environment.yaml", "pyproject.toml"):
        path = root / name
        if path.is_file():
            combined += "\n" + _read_text(path, 200_000)

    python_requirement = None
    match = re.search(r"python\s*(?:=|>=|==)\s*['\"]?(\d+(?:\.\d+){0,2})", combined, re.IGNORECASE)
    if match:
        python_requirement = match.group(1)

    frameworks = []
    for key, label in (("torch", "PyTorch"), ("tensorflow", "TensorFlow"), ("jax", "JAX"),
                       ("lightning", "PyTorch Lightning"), ("transformers", "Transformers"),
                       ("sklearn", "scikit-learn"), ("xgboost", "XGBoost")):
        if re.search(rf"\b{key}\b", combined, re.IGNORECASE):
            frameworks.append(label)

    torch_pins = sorted(set(re.findall(r"\btorch\s*==\s*([0-9][0-9A-Za-z.\-+]*)", combined)))
    cuda_tags = sorted({f"cu{match}" for match in CUDA_TAG.findall(combined)})
    torch_index = None
    index_match = re.search(r"download\.pytorch\.org/whl/(cu\d+|cpu)", combined, re.IGNORECASE)
    if index_match:
        torch_index = index_match.group(1).lower()

    return {
        "manifests": manifests,
        "python_requirement": python_requirement,
        "frameworks": frameworks,
        "torch_pins": torch_pins,
        "cuda_tags": cuda_tags,
        "torch_index_url_tag": torch_index,
    }


def _device_requirements(root: Path, scan: dict[str, Any], readme_text: str) -> dict[str, Any]:
    evidence: list[str] = []
    signals = 0
    for relative in scan.get("entrypoint_candidates", [])[:20]:
        text = _read_text(root / relative, 200_000)
        if not text:
            continue
        for pattern, label in (
            (r"\.cuda\(\)", "calls .cuda()"),
            (r"torch\.cuda", "uses torch.cuda"),
            (r"device\s*=\s*['\"]cuda", "requests a cuda device"),
            (r"DistributedDataParallel", "uses distributed training"),
        ):
            if re.search(pattern, text):
                signals += 1
                evidence.append(f"{relative}: {label}")
    if re.search(r"\b(?:nvidia-smi|cuda|gpu)\b", readme_text, re.IGNORECASE):
        signals += 1
        evidence.append("README mentions CUDA/GPU")
    return {
        "gpu_code_detected": signals > 0,
        "signal_count": signals,
        "evidence": evidence[:20],
    }


def _expected_metrics(text: str) -> list[str]:
    found: set[str] = set()
    lowered = text.lower()
    for word in METRIC_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            found.add(word)
    return sorted(found)


def understand_project(project_path: str | Path) -> dict[str, Any]:
    """Describe how a research repository is meant to be installed and run."""
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    scan = scan_project(root)
    readme_path = next((root / name for name in README_NAMES if (root / name).is_file()), None)
    readme_text = _read_text(readme_path, 400_000) if readme_path else ""
    readme = _parse_readme(readme_text)
    readme["path"] = readme_path.name if readme_path else None
    if readme_path is None:
        readme["warning"] = "No README file was found; run commands must be supplied manually."

    pairs: list[tuple[str, str, Any]] = []
    configs: list[dict[str, Any]] = []
    for path in _config_files(root):
        relative = path.relative_to(root).as_posix()
        mapping, parser_name = _load_structured(path)
        entry = {"path": relative, "parser": parser_name, "keys": 0}
        if mapping is None:
            entry["error"] = parser_name
            configs.append(entry)
            continue
        flat = _flatten(mapping)
        entry["keys"] = len(flat)
        configs.append(entry)
        for key, value in flat:
            pairs.append((relative, key, value))

    entrypoints = _entrypoints(root, scan, readme["run_commands"])
    datasets = _dataset_references(root, pairs)
    environment = _environment_requirements(root, readme_text, scan)

    warnings: list[str] = []
    if not scan.get("dependency_manifests"):
        warnings.append("No dependency manifest (requirements.txt / environment.yml / pyproject.toml) was found.")
    if not entrypoints:
        warnings.append("No conventional entrypoint was identified; supply an explicit command.")
    missing = [item for item in datasets if not item["exists"]]
    if missing:
        warnings.append(
            f"{len(missing)} dataset path(s) referenced by configuration do not exist on this machine."
        )
    if environment["frameworks"] and not scan.get("dependency_manifests"):
        warnings.append("Frameworks are mentioned but no manifest pins them.")

    return {
        "project_path": str(root),
        "readme": readme,
        "entrypoints": entrypoints,
        "configs": configs,
        "config_count": len(configs),
        "environment": environment,
        "device": _device_requirements(root, scan, readme_text),
        "datasets": datasets,
        "missing_datasets": [item for item in datasets if not item["exists"]],
        "seeds": _seeds(pairs),
        "training_parameters": _training_parameters(pairs),
        "expected_metrics": _expected_metrics(readme_text),
        "run_command_candidates": _run_command_candidates(entrypoints, readme["run_commands"], root),
        "scan_summary": {
            "file_count": scan.get("file_count"),
            "python_file_count": scan.get("python_file_count"),
            "languages": scan.get("languages"),
            "framework_hints": scan.get("framework_hints"),
            "syntax_errors": scan.get("syntax_errors", [])[:20],
            "git": scan.get("git"),
        },
        "warnings": warnings,
    }
