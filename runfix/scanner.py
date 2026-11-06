from __future__ import annotations
import ast, os, re, subprocess
from pathlib import Path
from typing import Any

SKIP = {".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
        ".mypy_cache", ".tox", "node_modules", "site-packages", "dist", "build", ".runfix"}
ENTRY = {"main.py", "train.py", "run.py", "app.py", "server.py", "inference.py"}

def _git(root: Path, *args: str):
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
            text=True, timeout=5, shell=False, encoding="utf-8", errors="replace")
        return p.stdout.strip() if p.returncode == 0 else None
    except Exception: return None

def _read(path: Path) -> str:
    try:
        if path.stat().st_size > 500_000: return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError: return ""

def scan_project(project_path: str | Path) -> dict[str, Any]:
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir(): raise NotADirectoryError(str(root))
    paths = []
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP and not (Path(base)/d).is_symlink()]
        for name in names:
            p = Path(base)/name
            if not p.is_symlink():
                paths.append(p)
            if len(paths) >= 12000: break
        if len(paths) >= 12000: break

    pyfiles = [p for p in paths if p.suffix == ".py"]
    syntax, imports = [], []
    for p in pyfiles:
        text = _read(p)
        if not text: continue
        rel = str(p.relative_to(root))
        try: tree = ast.parse(text, filename=rel)
        except SyntaxError as e:
            syntax.append({"file": rel, "line": e.lineno, "message": e.msg}); continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend({"module": a.name.split(".")[0], "file": rel, "line": node.lineno} for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append({"module": node.module.split(".")[0], "file": rel, "line": node.lineno})

    names = {p.name.lower() for p in paths}
    languages = []
    if pyfiles or names.intersection({"pyproject.toml", "requirements.txt", "setup.py"}): languages.append("Python")
    if "package.json" in names: languages.append("JavaScript/TypeScript")
    if "cargo.toml" in names: languages.append("Rust")
    if "go.mod" in names: languages.append("Go")

    manifest_names = {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
                      "environment.yml", "environment.yaml", "poetry.lock", "uv.lock", "pipfile"}
    manifests = [str(p.relative_to(root)) for p in paths if p.name.lower() in manifest_names]
    metadata_text = "\n".join(_read(p) for p in paths if p.name.lower() in manifest_names)
    hints = []
    for key, label in [("torch","PyTorch"),("tensorflow","TensorFlow"),("jax","JAX"),
                       ("transformers","Hugging Face Transformers"),("lightning","PyTorch Lightning"),
                       ("fastapi","FastAPI"),("scikit-learn","scikit-learn")]:
        if key in metadata_text.lower(): hints.append(label)

    reqs = {}
    for p in paths:
        if p.name.lower().startswith("requirements") and p.suffix.lower() == ".txt":
            reqs[str(p.relative_to(root))] = [x.strip() for x in _read(p).splitlines()
                if x.strip() and not x.strip().startswith(("#", "-"))][:300]

    return {
        "project_path": str(root), "file_count": len(paths), "python_file_count": len(pyfiles),
        "languages": languages, "framework_hints": sorted(set(hints)),
        "entrypoint_candidates": sorted((str(p.relative_to(root)) for p in pyfiles if p.name.lower() in ENTRY))[:40],
        "dependency_manifests": manifests, "requirements": reqs,
        "import_count": len(imports), "imports": imports[:2000], "syntax_errors": syntax[:200],
        "git": {"origin": _git(root,"remote","get-url","origin"),
                "commit": _git(root,"rev-parse","HEAD"),
                "branch": _git(root,"branch","--show-current")},
    }
