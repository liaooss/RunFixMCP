from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

_FRAME = re.compile(r'File ["\'](.+?)["\'], line \d+(?:, in [^\n]+)?')
_PYTEST_PATH = re.compile(r'(?P<path>(?:[A-Za-z]:[\\/])?[^ \t\r\n:]+\.py)::')


def _module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_traceback_path(root: Path, raw: str) -> Path | None:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() and resolved.suffix == ".py" else None


def _imports_from_test(root: Path, test_file: Path) -> set[str]:
    try:
        tree = ast.parse(test_file.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, UnicodeError):
        return set()
    imports: set[str] = set()
    package = _module_name(root, test_file).split(".")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                keep = max(0, len(package) - node.level + 1)
                base = ".".join(package[:keep] + ([base] if base else []))
            if base:
                imports.add(base)
                imports.update(f"{base}.{alias.name}" for alias in node.names)
    return imports


def select_tests(
    project_path: str | Path,
    traceback_text: str,
    max_candidates: int = 8,
) -> dict[str, Any]:
    """Select likely related pytest files using traceback frames and static import edges.

    This is a prioritization hint, not a proof of test coverage. Callers should still
    run the full suite after a targeted pass when full-suite verification is required.
    """
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    max_candidates = max(1, min(int(max_candidates), 20))

    source_paths: list[Path] = []
    for match in _FRAME.finditer(traceback_text or ""):
        path = _resolve_traceback_path(root, match.group(1))
        if path is not None and path not in source_paths:
            source_paths.append(path)

    # Pytest's short traceback format: tests/test_x.py::test_name
    for match in _PYTEST_PATH.finditer(traceback_text or ""):
        path = _resolve_traceback_path(root, match.group("path"))
        if path is not None and path not in source_paths:
            source_paths.append(path)

    tests = []
    ignored = {".git", ".venv", "venv", "env", ".runfix", "__pycache__", "node_modules", "site-packages"}
    for base, dirs, files in __import__("os").walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in ignored]
        for name in files:
            if (name.startswith("test_") and name.endswith(".py")) or name.endswith("_test.py"):
                tests.append(Path(base) / name)

    source_modules = {_module_name(root, p) for p in source_paths if p not in tests}
    source_basenames = {p.stem for p in source_paths}
    scored: list[dict[str, Any]] = []
    for test in tests:
        relative = test.relative_to(root).as_posix()
        score = 0
        evidence: list[str] = []
        if test in source_paths:
            score += 100
            evidence.append("test file appears directly in traceback")
        imports = _imports_from_test(root, test)
        for module in source_modules:
            if any(
                item == module
                or item.startswith(module + ".")
                or module.startswith(item + ".")
                for item in imports
            ):
                score += 10
                evidence.append(f"imports traceback module {module}")
        for basename in source_basenames:
            if basename and basename in imports:
                score += 5
                evidence.append(f"imports module with basename {basename}")
        try:
            source_text = test.read_text(encoding="utf-8", errors="replace")
        except OSError:
            source_text = ""
        for source in source_paths:
            relative_source = source.relative_to(root).as_posix()
            if relative_source in source_text:
                score += 15
                evidence.append(f"references source path {relative_source}")
        if score:
            scored.append({
                "path": relative,
                "score": score,
                "evidence": sorted(set(evidence)),
            })

    scored.sort(key=lambda item: (-item["score"], item["path"]))
    selected = scored[:max_candidates]
    if selected:
        return {
            "selected": [item["path"] for item in selected],
            "candidates": selected,
            "source_files": [p.relative_to(root).as_posix() for p in source_paths],
            "fallback_required": False,
            "strategy": "traceback_frames_and_static_test_imports",
            "note": "Targeted selection is heuristic; a full-suite run is still required for complete verification.",
        }
    return {
        "selected": [],
        "candidates": [],
        "source_files": [p.relative_to(root).as_posix() for p in source_paths],
        "fallback_required": True,
        "strategy": "full_suite_fallback",
        "note": "No high-confidence test-to-source relationship was found; run the full test suite.",
    }
