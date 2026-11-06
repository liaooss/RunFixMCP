"""Tests for Docker/WSL isolation support (0.8.0).

The host used for development has no Docker daemon and no WSL distribution, so these tests
verify command construction, refusal semantics and pipeline wiring — not a real container.
That limitation is stated in the documentation rather than papered over.
"""

from __future__ import annotations

import pytest

from runfix import sandbox
from runfix.repro.pipeline import reproduce
from runfix.sandbox import (
    build_docker_command,
    build_sandbox_command,
    build_wsl_command,
    detect_backends,
    ensure_backend,
    probe_backends_fast,
    summarize_sandbox,
    windows_to_wsl_path,
)


def test_windows_paths_convert_to_wsl_paths():
    assert windows_to_wsl_path(r"E:\Projects\RunFix") == "/mnt/e/Projects/RunFix"
    assert windows_to_wsl_path("E:/Projects/RunFix") == "/mnt/e/Projects/RunFix"
    assert windows_to_wsl_path("/home/user/code") == "/home/user/code"


def test_docker_command_shape(tmp_path):
    command = build_docker_command(tmp_path, ["python", "train.py"], image="pytorch/pytorch:2.1")
    assert command[0] == "docker"
    assert command[1:4] == ["run", "--rm", "-i"]
    assert "-v" in command
    assert f"{str(tmp_path).replace(chr(92), '/')}:/workspace" in command
    assert command[command.index("-w") + 1] == "/workspace"
    assert "--network" in command and "none" in command
    assert "pytorch/pytorch:2.1" in command
    assert command[-2:] == ["python", "train.py"]


def test_docker_command_rejects_an_empty_image(tmp_path):
    with pytest.raises(ValueError):
        build_docker_command(tmp_path, ["python"], image="")


def test_docker_command_rejects_an_empty_command(tmp_path):
    with pytest.raises(ValueError):
        build_docker_command(tmp_path, [], image="python:3.11-slim")


def test_wsl_command_shape(tmp_path):
    command = build_wsl_command(tmp_path, ["python", "train.py"], distro="Ubuntu")
    assert command[0] == "wsl"
    assert command[1:3] == ["-d", "Ubuntu"]
    assert "--cd" in command
    assert command[-1] == "train.py"
    assert command[command.index("--cd") + 1].startswith("/mnt/") or command[command.index("--cd") + 1].startswith("/")


def test_project_paths_are_rewritten_for_docker(tmp_path):
    plan = build_sandbox_command(
        tmp_path, ["python", str(tmp_path / "train.py")], backend="docker", image="python:3.11-slim"
    )
    assert plan["backend"] == "docker"
    assert "/workspace/train.py" in plan["command"]
    assert all(not str(item).startswith(str(tmp_path)) for item in plan["command"])


def test_unknown_backend_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        build_sandbox_command(tmp_path, ["python"], backend="podman")
    with pytest.raises(ValueError):
        ensure_backend("podman")


def test_none_backend_is_always_available():
    assert ensure_backend("none")["available"] is True


def test_missing_docker_is_reported_instead_of_running_unsandboxed(monkeypatch):
    monkeypatch.setattr(sandbox, "_find_executable", lambda name: None)
    with pytest.raises(RuntimeError) as error:
        ensure_backend("docker")
    assert "refuses to silently run" in str(error.value)


def test_missing_wsl_is_reported(monkeypatch):
    monkeypatch.setattr(sandbox, "_find_executable", lambda name: None)
    with pytest.raises(RuntimeError) as error:
        ensure_backend("wsl")
    assert "wsl" in str(error.value).lower()


def test_fast_probe_reports_absence(monkeypatch):
    monkeypatch.setattr(sandbox, "_find_executable", lambda name: None)
    assert probe_backends_fast() == {"docker": False, "wsl": False}


def test_detect_backends_marks_unavailable_backends_with_a_hint(monkeypatch):
    monkeypatch.setattr(sandbox, "_find_executable", lambda name: None)
    detected = detect_backends()
    assert detected["docker"]["available"] is False
    assert detected["docker"]["hint"]
    assert detected["wsl"]["available"] is False
    assert detected["wsl"]["hint"]


def test_summarize_sandbox_is_explicit():
    assert summarize_sandbox(None) == "no sandbox (host execution)"
    assert summarize_sandbox({"backend": "none"}) == "no sandbox (host execution)"
    assert "pytorch" in summarize_sandbox({"backend": "docker", "image": "pytorch:2.1", "network": "none"})
    assert "Ubuntu" in summarize_sandbox({"backend": "wsl", "distro": "Ubuntu", "wsl_path": "/mnt/e/x"})


def test_run_reproduction_rejects_an_unknown_backend(tmp_path):
    from runfix.repro.runner import run_reproduction

    with pytest.raises(ValueError):
        run_reproduction(tmp_path, command=["python", "-c", "print(1)"], sandbox="podman")


def test_reproduce_fails_fast_when_the_sandbox_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "_find_executable", lambda name: None)
    (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as error:
        reproduce(tmp_path, plan_only=True, sandbox="docker")
    assert "docker" in str(error.value).lower()


def test_host_execution_still_records_a_none_sandbox(paper_project):
    result = reproduce(paper_project, allow_execute=True)
    assert result["run"]["sandbox"]["backend"] == "none"
    assert result["run"]["interpreter_resolution"] == "host"
    assert result["execution"]["sandbox"] == "none"
