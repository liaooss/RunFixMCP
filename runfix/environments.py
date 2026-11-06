from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable

from .common import redact, resolve_python, run


def detect_environment_manifest(project_path: str | Path) -> dict[str, Any] | None:
    """Return the preferred supported environment manifest for a project."""
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    candidates = [
        ("conda", "environment.yml"),
        ("conda", "environment.yaml"),
        ("pip", "requirements.txt"),
        ("pip", "pyproject.toml"),
    ]
    for manager, filename in candidates:
        path = root / filename
        if path.is_file() and not path.is_symlink():
            return {"manager": manager, "manifest": filename, "path": str(path)}
    return None


def _find_conda() -> str | None:
    candidates = []
    configured = os.environ.get("CONDA_EXE")
    if configured:
        candidates.append(configured)
    located = shutil.which("conda")
    if located:
        candidates.append(located)

    for item in candidates:
        path = Path(item).expanduser()
        if not path.is_file():
            continue
        # A Windows .bat/.cmd file cannot be reliably launched with shell=False.
        if os.name == "nt" and path.suffix.lower() in {".bat", ".cmd"}:
            continue
        return str(path.resolve())
    return None


ENVIRONMENT_MARKER = ".runfix-environment.json"


def _environment_python(env_dir: Path) -> Path:
    """Return the interpreter path for a conda prefix or a venv prefix.

    The first existing candidate wins; when the environment does not exist yet the
    canonical location for the current platform is returned so callers can use this
    as a pre-creation probe. Callers must re-resolve this path after creating the
    environment: a Windows venv puts its interpreter in ``Scripts/python.exe`` while
    a conda prefix puts it at ``python.exe``, and the two layouts are indistinguishable
    before the environment exists.
    """
    if os.name == "nt":
        candidates = [env_dir / "python.exe", env_dir / "Scripts" / "python.exe"]
    else:
        candidates = [env_dir / "bin" / "python"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _is_disposable_environment(env_dir: Path) -> bool:
    """Only an empty directory or a RunFix/venv/conda prefix may be deleted and rebuilt."""
    if not env_dir.exists():
        return True
    if env_dir.is_symlink() or not env_dir.is_dir():
        return False
    markers = {ENVIRONMENT_MARKER, "pyvenv.cfg", "conda-meta"}
    try:
        entries = {item.name for item in env_dir.iterdir()}
    except OSError:
        return False
    if not entries:
        return True
    return bool(entries & markers)


def _fingerprint(root: Path, manifest: Path) -> str:
    digest = hashlib.sha256()
    for path in [manifest, root / "poetry.lock", root / "uv.lock", root / "requirements.txt"]:
        if path.is_file() and not path.is_symlink():
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()[:16]


def build_isolated_environment(
    project_path: str | Path,
    python_executable: str | None = None,
    timeout: int = 1800,
    runner: Callable[..., dict[str, Any]] = run,
    conda_executable: str | None = None,
) -> dict[str, Any]:
    """Build (or reuse) a project-local environment and record the action in the audit trail."""
    root = Path(project_path).expanduser().resolve()
    result = _build_isolated_environment(
        root, python_executable=python_executable, timeout=timeout,
        runner=runner, conda_executable=conda_executable,
    )
    from .audit import audit as _audit
    _audit(
        root, "environment.build",
        target=str(result.get("environment") or result.get("manifest") or ""),
        outcome=("reused" if result.get("reused") else
                 "created" if result.get("created") else "failed"),
        actor="runfix.environments",
        details={
            "manager": result.get("manager"),
            "manifest": result.get("manifest"),
            "fingerprint": result.get("fingerprint"),
            "python_executable": result.get("python_executable"),
            "reason": result.get("reason"),
        },
    )
    return result


def _build_isolated_environment(
    project_path: str | Path,
    python_executable: str | None = None,
    timeout: int = 1800,
    runner: Callable[..., dict[str, Any]] = run,
    conda_executable: str | None = None,
) -> dict[str, Any]:
    """Build an isolated environment from environment.yml, requirements.txt or pyproject.toml.

    This function never installs into the selected/base interpreter. Every target is
    placed beneath <project>/.runfix/environments/.
    """
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    detected = detect_environment_manifest(root)
    if detected is None:
        return {
            "created": False,
            "reason": "No supported environment manifest found.",
            "supported_manifests": ["environment.yml", "environment.yaml", "requirements.txt", "pyproject.toml"],
        }

    manager = detected["manager"]
    manifest = Path(detected["path"])
    fingerprint = _fingerprint(root, manifest)
    env_dir = root / ".runfix" / "environments" / f"{manager}-{fingerprint}"
    resolved_env_dir = env_dir.resolve()
    try:
        resolved_env_dir.relative_to(root)
    except ValueError as exc:
        raise PermissionError("Environment directory resolves outside the project.") from exc

    python_path = _environment_python(env_dir)
    if python_path.is_file():
        return {
            "created": True,
            "reused": True,
            "manager": manager,
            "manifest": detected["manifest"],
            "fingerprint": fingerprint,
            "environment": str(env_dir),
            "python_executable": str(python_path.resolve()),
            "note": "An isolated environment with the same manifest fingerprint already exists.",
        }

    env_dir.parent.mkdir(parents=True, exist_ok=True)
    if env_dir.exists():
        if not _is_disposable_environment(env_dir):
            return {
                "created": False,
                "manager": manager,
                "manifest": detected["manifest"],
                "fingerprint": fingerprint,
                "environment": str(env_dir),
                "reason": "The target environment directory already contains files RunFix did not create; refusing to delete it.",
                "hint": "Move or rename that directory, or remove it manually once you have confirmed it is not needed.",
            }
        shutil.rmtree(env_dir)

    timeout = max(60, min(int(timeout), 7200))
    base_python = resolve_python(python_executable)

    if manager == "conda":
        conda = conda_executable or _find_conda()
        if not conda:
            return {
                "created": False,
                "manager": "conda",
                "manifest": detected["manifest"],
                "fingerprint": fingerprint,
                "reason": "environment.yml was found, but a conda executable was not located.",
                "hint": "Activate Anaconda/Miniconda so CONDA_EXE is set, or add conda.exe to PATH. RunFix will not silently reinterpret Conda packages as pip packages.",
            }
        args = [
            conda, "env", "create", "--prefix", str(env_dir),
            "--file", str(manifest), "--yes",
        ]
        install = runner(args, cwd=root, timeout=timeout)
    elif detected["manifest"] == "requirements.txt":
        created = runner(
            [base_python, "-m", "venv", str(env_dir)],
            cwd=root,
            timeout=min(timeout, 300),
        )
        if created.get("returncode") != 0 or created.get("timed_out"):
            return {
                "created": False, "manager": "pip", "manifest": detected["manifest"],
                "fingerprint": fingerprint, "environment": str(env_dir),
                "stage": "venv_create", "install": created,
            }
        python_path = _environment_python(env_dir)
        install = runner(
            [str(python_path), "-m", "pip", "install", "--disable-pip-version-check",
             "-r", str(manifest)],
            cwd=root,
            timeout=timeout,
        )
    else:
        created = runner(
            [base_python, "-m", "venv", str(env_dir)],
            cwd=root,
            timeout=min(timeout, 300),
        )
        if created.get("returncode") != 0 or created.get("timed_out"):
            return {
                "created": False, "manager": "pip", "manifest": detected["manifest"],
                "fingerprint": fingerprint, "environment": str(env_dir),
                "stage": "venv_create", "install": created,
            }
        python_path = _environment_python(env_dir)
        install = runner(
            [str(python_path), "-m", "pip", "install", "--disable-pip-version-check", "."],
            cwd=root,
            timeout=timeout,
        )

    # Re-resolve after creation: conda prefixes use python.exe while venvs use
    # Scripts/python.exe on Windows, and that layout is only observable once the
    # environment exists.
    python_path = _environment_python(env_dir)
    ready = python_path.is_file() and install.get("returncode") == 0 and not install.get("timed_out")
    if ready:
        try:
            (env_dir / ENVIRONMENT_MARKER).write_text(
                json.dumps({
                    "manager": manager,
                    "manifest": detected["manifest"],
                    "fingerprint": fingerprint,
                    "python_executable": str(python_path.resolve()),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
    return {
        "created": ready,
        "reused": False,
        "manager": manager,
        "manifest": detected["manifest"],
        "fingerprint": fingerprint,
        "environment": str(env_dir),
        "python_executable": str(python_path.resolve()) if python_path.exists() else None,
        "install": install,
        "note": (
            "Dependencies were installed only into a project-local environment."
            if ready else "Environment creation did not complete successfully; inspect the install output."
        ),
    }
