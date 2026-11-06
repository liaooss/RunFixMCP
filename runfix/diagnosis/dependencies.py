"""Dependency-constraint analysis for research repositories.

RunFix ships with no runtime dependencies, so this module implements the small subset of
PEP 440 that research manifests actually use (``==``, ``!=``, ``>=``, ``<=``, ``>``, ``<``,
``~=``, ``===`` and ``==X.Y.*``). Anything it cannot parse is reported as ``unknown``
instead of being silently treated as satisfied.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[^\]]*\])?"
    r"\s*(?P<specifier>(?:(?:===|==|!=|~=|>=|<=|>|<)\s*[0-9A-Za-z.*+-]+\s*,?\s*)*)"
    r"\s*(?:;.*)?$"
)
SPEC_PART = re.compile(r"(===|==|!=|~=|>=|<=|>|<)\s*([0-9A-Za-z.*+-]+)")
CONDITION = re.compile(r"\bpython_version\b\s*(?:>|<|==|>=|<=|!=)\s*['\"]?(\d+\.\d+)")

MANIFESTS = ("requirements.txt", "environment.yml", "environment.yaml", "pyproject.toml")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def parse_version(value: str) -> tuple[int, ...] | None:
    """Numeric release tuple, ignoring pre/post/local suffixes (documented simplification)."""
    if value is None:
        return None
    match = re.match(r"^\s*[vV]?(\d+(?:\.\d+)*)", str(value))
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _pad(value: tuple[int, ...], size: int) -> tuple[int, ...]:
    return value + (0,) * max(0, size - len(value))


def compare_versions(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    size = max(len(left), len(right))
    a, b = _pad(left, size), _pad(right, size)
    return (a > b) - (a < b)


def satisfies(version: str | None, specifier: str) -> bool | None:
    """Whether an installed version satisfies a specifier. ``None`` means "cannot tell"."""
    if not specifier or not specifier.strip():
        return True
    if version is None:
        return False
    installed = parse_version(version)
    if installed is None:
        return None
    for op, raw in SPEC_PART.findall(specifier):
        raw = raw.strip()
        if op in {"==", "==="} and raw.endswith(".*"):
            prefix = parse_version(raw[:-2])
            if prefix is None:
                return None
            if _pad(installed, len(prefix))[: len(prefix)] != prefix:
                return False
            continue
        target = parse_version(raw)
        if target is None:
            return None
        if op == "~=":
            # PEP 440: ~=1.4 means >=1.4, <2.0; ~=1.4.2 means >=1.4.2, <1.5.
            if compare_versions(installed, target) < 0:
                return False
            upper = list(target[:-1])
            upper[-1] += 1
            if compare_versions(installed, tuple(upper)) >= 0:
                return False
            continue
        comparison = compare_versions(installed, target)
        if op in {"==", "==="} and comparison != 0:
            return False
        if op == "!=" and comparison == 0:
            return False
        if op == ">=" and comparison < 0:
            return False
        if op == "<=" and comparison > 0:
            return False
        if op == ">" and comparison <= 0:
            return False
        if op == "<" and comparison >= 0:
            return False
    return True


def parse_requirements(text: str) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-", "git+", "http://", "https://")):
            continue
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()  # pip allows trailing comments
        if not line:
            continue
        match = REQUIREMENT.match(line)
        if not match:
            continue
        requirements.append({
            "name": match.group("name"),
            "canonical": _canonical(match.group("name")),
            "specifier": (match.group("specifier") or "").strip(),
            "raw": line,
            "marker": (CONDITION.search(line).group(0) if CONDITION.search(line) else None),
        })
    return requirements


def parse_environment_yml(text: str) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    in_pip = False
    for raw in (text or "").splitlines():
        stripped = raw.strip()
        if stripped.startswith("- pip:"):
            in_pip = True
            continue
        if in_pip:
            if stripped.startswith("- ") and not stripped.startswith("- pip"):
                entry = stripped[2:].strip()
                if entry and not entry.startswith("#"):
                    match = REQUIREMENT.match(entry)
                    if match:
                        requirements.append({
                            "name": match.group("name"),
                            "canonical": _canonical(match.group("name")),
                            "specifier": (match.group("specifier") or "").strip(),
                            "raw": entry,
                            "source": "pip-section",
                        })
                continue
            in_pip = False
        if stripped.startswith("- ") and ":" not in stripped:
            entry = stripped[2:].strip()
            if not entry or entry.startswith("#"):
                continue
            pinned = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*=\s*([0-9][0-9A-Za-z.*]*)$", entry)
            if pinned:
                name = pinned.group(1)
                specifier = "==" + pinned.group(2)
            else:
                match = REQUIREMENT.match(entry)
                if not match:
                    continue
                name = match.group("name")
                specifier = (match.group("specifier") or "").strip()
            requirements.append({
                "name": name,
                "canonical": _canonical(name),
                "specifier": specifier,
                "raw": entry,
                "source": "conda",
            })
    return requirements


def parse_pyproject(text: str) -> list[dict[str, Any]]:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.10
        return []
    try:
        data = tomllib.loads(text)
    except Exception:
        return []
    project = data.get("project") if isinstance(data, dict) else None
    if not isinstance(project, dict):
        return []
    requirements: list[dict[str, Any]] = []
    for item in project.get("dependencies") or []:
        if not isinstance(item, str):
            continue
        match = REQUIREMENT.match(item)
        if not match:
            continue
        requirements.append({
            "name": match.group("name"),
            "canonical": _canonical(match.group("name")),
            "specifier": (match.group("specifier") or "").strip(),
            "raw": item,
        })
    return requirements


def parse_manifest(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    name = path.name.lower()
    if name.startswith("requirements"):
        return parse_requirements(text)
    if name.startswith("environment") and name.endswith((".yml", ".yaml")):
        return parse_environment_yml(text)
    if name == "pyproject.toml":
        return parse_pyproject(text)
    return []


def declared_requirements(root: Path) -> list[dict[str, Any]]:
    """Every requirement declared by any supported manifest, with its source file."""
    declared: list[dict[str, Any]] = []
    for name in MANIFESTS:
        path = root / name
        if path.is_file() and not path.is_symlink():
            for item in parse_manifest(path):
                item = dict(item)
                item["manifest"] = name
                declared.append(item)
    return declared


def python_requirement(root: Path) -> str | None:
    pyproject = root / "pyproject.toml"
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        tomllib = None
    if tomllib is not None and pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            value = (data.get("project") or {}).get("requires-python")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except Exception:
            pass
    for name in ("environment.yml", "environment.yaml"):
        path = root / name
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^\s*-\s*python\s*=\s*([0-9][0-9A-Za-z.*]*)\s*$", raw)
            if match:
                return "==" + match.group(1)
    return None


def analyse_dependencies(root: Path,
                         installed: dict[str, str | None],
                         python_version: str | None = None) -> dict[str, Any]:
    """Compare declared requirements with what is actually installed."""
    declared = declared_requirements(root)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in declared:
        by_name.setdefault(item["canonical"], []).append(item)

    findings: list[dict[str, Any]] = []
    for canonical, items in sorted(by_name.items()):
        specs = {item["specifier"] for item in items if item["specifier"]}
        version = installed.get(canonical)
        evidence = [{"manifest": item["manifest"], "raw": item["raw"]} for item in items]

        if len(specs) > 1:
            findings.append({
                "kind": "manifest_disagreement",
                "package": canonical,
                "specifiers": sorted(specs),
                "installed": version,
                "evidence": evidence,
                "severity": "warning",
                "detail": "The same package is declared with different constraints in different manifests.",
            })
            continue

        specifier = next(iter(specs), "")
        if not specifier:
            continue
        verdict = satisfies(version, specifier)
        if verdict is True:
            continue
        if version is None:
            findings.append({
                "kind": "not_installed",
                "package": canonical,
                "specifier": specifier,
                "installed": None,
                "evidence": evidence,
                "severity": "error",
                "detail": f"{canonical} is declared ({specifier}) but not importable in the selected interpreter.",
            })
        elif verdict is False:
            findings.append({
                "kind": "version_unsatisfied",
                "package": canonical,
                "specifier": specifier,
                "installed": version,
                "evidence": evidence,
                "severity": "error",
                "detail": f"{canonical}=={version} does not satisfy the declared constraint {specifier}.",
            })
        else:
            findings.append({
                "kind": "version_unparsable",
                "package": canonical,
                "specifier": specifier,
                "installed": version,
                "evidence": evidence,
                "severity": "info",
                "detail": "The installed or declared version could not be parsed, so satisfaction is unknown.",
            })

    python_constraint = python_requirement(root)
    python_satisfied = None
    if python_constraint and python_version:
        python_satisfied = satisfies(python_version, python_constraint)
        if python_satisfied is False:
            findings.append({
                "kind": "python_incompatible",
                "package": "python",
                "specifier": python_constraint,
                "installed": python_version,
                "evidence": [{"manifest": "requires-python", "raw": python_constraint}],
                "severity": "error",
                "detail": f"The interpreter is Python {python_version}, but the project requires {python_constraint}.",
            })

    resolutions = []
    for finding in findings:
        if finding["kind"] == "version_unsatisfied":
            resolutions.append({
                "finding": finding["kind"],
                "package": finding["package"],
                "action": "install_constraint",
                "command": ["pip", "install", f"{finding['package']}{finding['specifier']}"],
                "rationale": "Install the version range the project declares instead of the version currently present.",
                "risk": "may change unrelated packages in the environment",
            })
        elif finding["kind"] == "not_installed":
            resolutions.append({
                "finding": finding["kind"],
                "package": finding["package"],
                "action": "install_constraint",
                "command": ["pip", "install", f"{finding['package']}{finding['specifier']}"],
                "rationale": "Install the declared package into the isolated environment.",
                "risk": "installs a package the project declares; network access required",
            })
        elif finding["kind"] == "manifest_disagreement":
            resolutions.append({
                "finding": finding["kind"],
                "package": finding["package"],
                "action": "choose_manifest",
                "command": None,
                "rationale": "Pick the manifest the project actually uses (conda for environment.yml) and delete or align the other constraint by hand.",
                "risk": "RunFix will not guess which manifest is authoritative",
            })
        elif finding["kind"] == "python_incompatible":
            resolutions.append({
                "finding": finding["kind"],
                "package": "python",
                "action": "select_interpreter",
                "command": None,
                "rationale": "Create the isolated environment with a matching interpreter instead of changing the system Python.",
                "risk": "requires rebuilding the environment",
            })

    return {
        "manifests": sorted({item["manifest"] for item in declared}),
        "declared_count": len(declared),
        "declared": declared,
        "installed_subset": {name: installed.get(name) for name in by_name},
        "python_requirement": python_constraint,
        "python_version": python_version,
        "python_satisfied": python_satisfied,
        "findings": findings,
        "resolutions": resolutions,
        "counts": {
            "error": sum(1 for item in findings if item["severity"] == "error"),
            "warning": sum(1 for item in findings if item["severity"] == "warning"),
            "info": sum(1 for item in findings if item["severity"] == "info"),
        },
    }
