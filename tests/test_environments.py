import os
from pathlib import Path

from runfix.environments import (
    _fingerprint,
    _is_disposable_environment,
    _environment_python,
    build_isolated_environment,
    detect_environment_manifest,
)


def fake_venv_prefix(env: Path) -> Path:
    """Create the interpreter location a real venv would use on this platform."""
    if os.name == "nt":
        directory = env / "Scripts"
        interpreter = directory / "python.exe"
    else:
        directory = env / "bin"
        interpreter = directory / "python"
    directory.mkdir(parents=True, exist_ok=True)
    interpreter.write_text("", encoding="utf-8")
    return interpreter


def _success(args, cwd=None, timeout=60):
    return {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False}


def test_conda_manifest_is_preferred_over_requirements(tmp_path):
    (tmp_path / "environment.yml").write_text("name: paper\ndependencies:\n  - python=3.11\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    detected = detect_environment_manifest(tmp_path)
    assert detected["manager"] == "conda"
    assert detected["manifest"] == "environment.yml"


def test_no_supported_manifest_is_reported(tmp_path):
    assert detect_environment_manifest(tmp_path) is None
    result = build_isolated_environment(tmp_path)
    assert result["created"] is False
    assert "No supported environment manifest" in result["reason"]


def test_conda_manifest_does_not_silently_fall_back_to_pip(tmp_path):
    (tmp_path / "environment.yml").write_text("dependencies:\n  - python=3.11\n", encoding="utf-8")
    calls = []

    def runner(*args, **kwargs):
        calls.append(args)
        return _success(args[0])

    result = build_isolated_environment(
        tmp_path,
        runner=runner,
        conda_executable="",
    )
    assert result["created"] is False
    assert result["manager"] == "conda"
    assert "conda executable" in result["reason"]
    assert calls == []


def test_requirements_builds_project_local_venv(tmp_path):
    (tmp_path / "requirements.txt").write_text("numpy>=1.20\n", encoding="utf-8")
    calls = []

    def fake_runner(args, cwd=None, timeout=60):
        calls.append(list(args))
        if args[1:3] == ["-m", "venv"]:
            fake_venv_prefix(Path(args[-1]))
        return _success(args)

    result = build_isolated_environment(tmp_path, runner=fake_runner)
    assert result["created"] is True
    assert result["manager"] == "pip"
    assert result["manifest"] == "requirements.txt"
    assert Path(result["python_executable"]).is_file()
    assert calls[1][-2:] == ["-r", str(tmp_path / "requirements.txt")]


def test_pyproject_builds_project_local_venv_and_installs_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools']\nbuild-backend='setuptools.build_meta'\n",
        encoding="utf-8",
    )
    calls = []

    def fake_runner(args, cwd=None, timeout=60):
        calls.append(list(args))
        if args[1:3] == ["-m", "venv"]:
            fake_venv_prefix(Path(args[-1]))
        return _success(args)

    result = build_isolated_environment(tmp_path, runner=fake_runner)
    assert result["created"] is True
    assert result["manifest"] == "pyproject.toml"
    assert calls[1][-1] == "."


def test_matching_environment_fingerprint_is_reused(tmp_path):
    (tmp_path / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    manifest = tmp_path / "requirements.txt"
    fingerprint = _fingerprint(tmp_path, manifest)
    env = tmp_path / ".runfix" / "environments" / f"pip-{fingerprint}"
    python = fake_venv_prefix(env)

    def should_not_run(*args, **kwargs):
        raise AssertionError("A matching existing environment should be reused.")

    result = build_isolated_environment(tmp_path, runner=should_not_run)
    assert result["created"] is True
    assert result["reused"] is True
    assert Path(result["python_executable"]) == python.resolve()


def test_conda_manifest_uses_conda_and_returns_environment_python(tmp_path):
    (tmp_path / "environment.yml").write_text(
        "name: paper\ndependencies:\n  - python=3.11\n", encoding="utf-8"
    )
    conda = tmp_path / "conda.exe"
    conda.write_text("", encoding="utf-8")
    calls = []

    def fake_runner(args, cwd=None, timeout=60):
        calls.append(list(args))
        prefix = Path(args[args.index("--prefix") + 1])
        prefix.mkdir(parents=True, exist_ok=True)
        if __import__("os").name == "nt":
            (prefix / "python.exe").write_text("", encoding="utf-8")
        else:
            (prefix / "bin").mkdir(exist_ok=True)
            (prefix / "bin" / "python").write_text("", encoding="utf-8")
        return _success(args)

    result = build_isolated_environment(
        tmp_path, runner=fake_runner, conda_executable=str(conda)
    )
    assert result["created"] is True
    assert result["manager"] == "conda"
    assert calls[0][0] == str(conda)
    assert "env" in calls[0] and "create" in calls[0]
    assert Path(result["python_executable"]).is_file()
