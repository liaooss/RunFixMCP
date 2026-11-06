"""Optional container/VM isolation for project execution.

RunFix never pretends a sandbox exists. Each backend is probed, its command is built from
explicit arguments, and if the backend is missing the caller gets a clear error instead of a
silently unsandboxed run.

Supported backends:

* ``docker`` — ``docker run --rm -v <project>:/workspace -w /workspace <image> <argv>``
* ``wsl``    — ``wsl -d <distro> --cd <project> -- <argv>``
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .common import redact, resolve_python, run

DEFAULT_DOCKER_IMAGE = "python:3.11-slim"
BACKENDS = ("none", "docker", "wsl")
DERIVED_STOP = ("docker", "wsl")


def _find_executable(name: str) -> str | None:
    located = shutil.which(name)
    if not located:
        return None
    # A Windows .cmd/.bat shim cannot be started with shell=False.
    if os.name == "nt" and Path(located).suffix.lower() in {".cmd", ".bat"}:
        return None
    return str(Path(located).resolve())


def windows_to_wsl_path(value: str | Path) -> str:
    """Convert ``E:\\a\\b`` to ``/mnt/e/a/b``; leave already-POSIX paths alone."""
    text = str(value)
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2).replace("\\", "/")
        return f"/mnt/{drive}/{rest}".rstrip("/")
    return text.replace("\\", "/")


def probe_backends_fast() -> dict[str, bool]:
    """Cheap availability check (no subprocess), suitable for a capabilities response."""
    return {
        "docker": bool(_find_executable("docker")),
        "wsl": bool(_find_executable("wsl")),
    }


def detect_backends() -> dict[str, Any]:
    """Report which isolation backends this machine can actually provide."""
    docker_exe = _find_executable("docker")
    wsl_exe = _find_executable("wsl")
    docker_version = None
    if docker_exe:
        result = run([docker_exe, "--version"], timeout=15)
        docker_version = (result.get("stdout") or "").strip() or None

    distros: list[str] = []
    wsl_ready = False
    if wsl_exe:
        listed = subprocess.run([wsl_exe, "-l", "-q"], capture_output=True, text=True,
                                timeout=25, shell=False, encoding="utf-8", errors="replace")
        if listed.returncode == 0:
            distros = [
                line.strip().replace("\x00", "").strip()
                for line in (listed.stdout or "").splitlines()
                if line.strip().replace("\x00", "").strip()
            ]
        wsl_ready = bool(distros)

    return {
        "docker": {
            "available": bool(docker_exe),
            "executable": docker_exe,
            "version": docker_version,
            "hint": None if docker_exe else (
                "Docker was not found. Install Docker Desktop (or expose docker.exe on PATH) to use "
                "--sandbox docker."
            ),
        },
        "wsl": {
            "available": bool(wsl_exe and wsl_ready),
            "executable": wsl_exe,
            "distros": distros,
            "hint": None if (wsl_exe and wsl_ready) else (
                "WSL was not found, or no distribution is installed. Run 'wsl --install -d Ubuntu' "
                "to use --sandbox wsl."
            ),
        },
    }


def build_docker_command(project_path: str | Path,
                         argv: list[str],
                         image: str = DEFAULT_DOCKER_IMAGE,
                         docker_executable: str = "docker",
                         network: str = "none",
                         extra_mounts: list[tuple[str, str]] | None = None) -> list[str]:
    """Build an isolated ``docker run`` invocation for one command."""
    if not argv:
        raise ValueError("A command is required to build a sandboxed run.")
    if not image or not str(image).strip():
        raise ValueError("A Docker image is required (for example python:3.11-slim).")
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    mount = f"{str(root).replace(os.sep, '/')}:/workspace"
    command = [
        docker_executable, "run", "--rm", "-i",
        "-v", mount,
        "-w", "/workspace",
        "--network", network,
        "-e", "PYTHONUNBUFFERED=1",
    ]
    for host, container in extra_mounts or []:
        command.extend(["-v", f"{str(host).replace(os.sep, '/')}:{container}"])
    command.append(str(image))
    command.extend(str(item) for item in argv)
    return command


def build_wsl_command(project_path: str | Path,
                      argv: list[str],
                      distro: str | None = None,
                      wsl_executable: str = "wsl",
                      wsl_path: str | None = None) -> list[str]:
    """Build an isolated ``wsl --cd <project> -- <argv>`` invocation."""
    if not argv:
        raise ValueError("A command is required to build a sandboxed run.")
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    target = wsl_path or windows_to_wsl_path(root)
    command = [wsl_executable]
    if distro:
        command.extend(["-d", distro])
    command.extend(["--cd", target, "--"])
    command.extend(str(item) for item in argv)
    return command


def _translate_argv(argv: list[str], backend: str, project_root: Path) -> list[str]:
    """Inside the sandbox the project is at /workspace; host paths must be rewritten."""
    if backend != "docker":
        return list(argv)
    translated: list[str] = []
    root_text = str(project_root)
    for item in argv:
        if item.startswith(root_text):
            remainder = item[len(root_text):].replace("\\", "/")
            translated.append("/workspace" + remainder)
        else:
            translated.append(item)
    return translated


def build_sandbox_command(project_path: str | Path,
                          argv: list[str],
                          backend: str = "docker",
                          image: str = DEFAULT_DOCKER_IMAGE,
                          distro: str | None = None,
                          docker_executable: str = "docker",
                          wsl_executable: str = "wsl",
                          wsl_path: str | None = None) -> dict[str, Any]:
    """Return the sandboxed command together with the isolation facts for the report."""
    root = Path(project_path).expanduser().resolve()
    if backend == "docker":
        command = build_docker_command(root, _translate_argv(argv, "docker", root), image=image,
                                       docker_executable=docker_executable)
        return {"backend": "docker", "command": command, "image": image,
                "network": "none", "mount": f"{root}:/workspace"}
    if backend == "wsl":
        command = build_wsl_command(root, argv, distro=distro, wsl_executable=wsl_executable,
                                    wsl_path=wsl_path)
        return {"backend": "wsl", "command": command, "distro": distro,
                "wsl_path": wsl_path or windows_to_wsl_path(root)}
    raise ValueError(f"Unknown sandbox backend {backend!r}; expected one of none, docker, wsl.")


def ensure_backend(backend: str, docker_executable: str = "docker",
                   wsl_executable: str = "wsl") -> dict[str, Any]:
    """Verify a backend is usable, or raise with an actionable message."""
    if backend == "none":
        return {"backend": "none", "available": True}
    if backend not in DERIVED_STOP:
        raise ValueError(f"Unknown sandbox backend {backend!r}; expected one of none, docker, wsl.")

    found = _find_executable(docker_executable if backend == "docker" else wsl_executable)
    if not found:
        name = "docker" if backend == "docker" else "wsl"
        raise RuntimeError(
            f"--sandbox {backend} was requested but {name} is not available on this machine. "
            "RunFix refuses to silently run the command unsandboxed."
        )

    if backend == "wsl":
        listed = subprocess.run([found, "-l", "-q"], capture_output=True, text=True,
                                timeout=25, shell=False, encoding="utf-8", errors="replace")
        distros = [line.strip().replace("\x00", "").strip()
                   for line in (listed.stdout or "").splitlines()]
        distros = [item for item in distros if item]
        if not distros:
            raise RuntimeError(
                "--sandbox wsl was requested but no WSL distribution is installed. "
                "Run 'wsl --install -d Ubuntu' first, or use --sandbox docker/none."
            )
        return {"backend": "wsl", "available": True, "executable": found, "distros": distros}

    version = run([found, "--version"], timeout=15)
    if version.get("returncode") != 0:
        raise RuntimeError(
            "Docker was found but is not responding to 'docker --version'. Start Docker Desktop "
            "and retry, or use --sandbox none."
        )
    return {"backend": "docker", "available": True, "executable": found,
            "version": (version.get("stdout") or "").strip()}


def run_sandboxed(project_path: str | Path,
                  argv: list[str],
                  backend: str = "docker",
                  image: str = DEFAULT_DOCKER_IMAGE,
                  distro: str | None = None,
                  timeout: int = 1800,
                  python_executable: str | None = None) -> dict[str, Any]:
    """Execute one command inside the requested sandbox and capture its output."""
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    if backend == "none":
        python = resolve_python(python_executable)
        command = list(argv)
        if Path(command[0]).name.lower() in {"python", "python.exe", "py"}:
            command[0] = python
        result = run(command, cwd=root, timeout=timeout)
        return {"sandbox": {"backend": "none"}, **result}

    info = ensure_backend(backend)
    plan = build_sandbox_command(
        root, argv, backend=backend, image=image, distro=distro,
        docker_executable=info.get("executable", "docker"),
        wsl_executable=info.get("executable", "wsl"),
    )
    result = run(plan["command"], cwd=root, timeout=timeout)
    return {"sandbox": {**plan, "executable": info.get("executable"), "version": info.get("version")},
            **result}


def summarize_sandbox(sandbox: dict[str, Any] | None) -> str:
    if not sandbox:
        return "no sandbox (host execution)"
    backend = sandbox.get("backend", "none")
    if backend == "none":
        return "no sandbox (host execution)"
    if backend == "docker":
        return f"docker image {sandbox.get('image')} (network {sandbox.get('network')})"
    if backend == "wsl":
        return f"wsl distro {sandbox.get('distro') or 'default'} at {sandbox.get('wsl_path')}"
    return redact(str(backend))
