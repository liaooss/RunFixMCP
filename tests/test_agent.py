from runfix.agent.core import run_agent

def fake_environment(_python):
    return {"python_version":"3.11.0","packages":{},"torch":{"installed":False},
            "issues":[],"nvidia_smi":{"available":False}}

def test_agent_inspection_mode_does_not_run_project_code(tmp_path, monkeypatch):
    import runfix.agent.tools as tools
    monkeypatch.setattr(tools, "diagnose_environment", fake_environment)
    (tmp_path/"main.py").write_text("raise RuntimeError('should not execute')\n")
    result=run_agent(tmp_path,max_steps=8,allow_project_code=False)
    assert result["outcome"]=="inspection_only"
    assert not any(x["tool"] in {"run_tests","run_user_command","apply_patch"} for x in result["tool_history"])
    assert (tmp_path/".runfix"/"runs").exists()

def test_agent_requires_code_permission_for_writes(tmp_path):
    try:
        run_agent(tmp_path,allow_write=True,allow_project_code=False)
    except ValueError as exc:
        assert "requires" in str(exc)
    else:
        assert False, "write permission must require project-code execution permission"

def test_agent_runs_project_tests_only_after_permission(tmp_path, monkeypatch):
    import runfix.agent.tools as tools
    monkeypatch.setattr(tools, "diagnose_environment", fake_environment)
    tests=tmp_path/"tests"
    tests.mkdir()
    (tests/"test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    result=run_agent(tmp_path,max_steps=10,allow_project_code=True)
    assert result["outcome"]=="verified"
    assert any(x["tool"]=="run_tests" and x["status"]=="completed" for x in result["tool_history"])


def test_agent_demo_has_expected_tool_order(tmp_path, monkeypatch):
    import runfix.agent.tools as tools
    monkeypatch.setattr(tools, "diagnose_environment", fake_environment)
    (tmp_path/"tests").mkdir()
    (tmp_path/"tests"/"test_ok.py").write_text("def test_ok():\n    assert True\n")
    result=run_agent(tmp_path,max_steps=10,allow_project_code=True)
    names=[x["tool"] for x in result["tool_history"]]
    assert names.index("scan_project") < names.index("diagnose_environment")
    assert names.index("diagnose_environment") < names.index("static_validation")
    assert names.index("static_validation") < names.index("run_tests")
    assert names[-1]=="finish"


def test_agent_requires_code_permission_for_dependency_install(tmp_path):
    try:
        run_agent(tmp_path,allow_install=True,allow_project_code=False)
    except ValueError as exc:
        assert "requires" in str(exc)
    else:
        assert False, "dependency installation must require project-code permission"


def test_agent_offers_environment_setup_for_pyproject(tmp_path):
    import sys
    from runfix.agent.tools import ToolBox

    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools']\n", encoding="utf-8"
    )
    box = ToolBox(tmp_path, sys.executable, {
        "allow_project_code": True,
        "allow_install": True,
        "allow_write": False,
        "timeout": 30,
    })
    state = {
        "observations": {
            "analysis": {"category": "missing_dependency_or_import_path"}
        }
    }
    assert "create_isolated_environment" in box.available(state)


def test_agent_uses_isolated_environment_builder(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    from runfix.agent.tools import ToolBox

    (tmp_path / "environment.yml").write_text("dependencies:\n  - python=3.11\n", encoding="utf-8")
    box = ToolBox(tmp_path, sys.executable, {
        "allow_project_code": True,
        "allow_install": True,
        "install_timeout": 120,
    })
    monkeypatch.setattr(
        "runfix.agent.tools.build_isolated_environment",
        lambda *args, **kwargs: {
            "created": True,
            "python_executable": sys.executable,
            "manager": "conda",
            "manifest": "environment.yml",
            "fingerprint": "abc123",
        },
    )
    result = box.execute("create_isolated_environment", {"observations": {}})
    assert result["created"] is True
    assert box.environment_created is True
    assert box.python == str(Path(sys.executable).resolve())


def test_agent_toolbox_rolls_back_patch_after_failed_targeted_test(tmp_path, monkeypatch):
    import sys
    import runfix.agent.tools as tools
    from runfix.agent.tools import ToolBox

    source = tmp_path / "model.py"
    source.write_text("value = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_model.py").write_text(
        "from model import value\ndef test_value(): assert value == 1\n",
        encoding="utf-8",
    )
    patch = (
        "diff --git a/model.py b/model.py\n"
        "--- a/model.py\n"
        "+++ b/model.py\n"
        "@@ -1 +1 @@\n"
        "-value = 1\n"
        "+value = 2\n"
    )
    box = ToolBox(tmp_path, sys.executable, {
        "allow_project_code": True,
        "allow_write": True,
        "timeout": 30,
    })
    box.latest_log = f'  File "{source}", line 1, in train\nRuntimeError: original failure'
    box.patch_text = patch
    checked = box.execute("validate_patch", {"observations": {}})
    assert checked["valid"] is True
    applied = box.execute("apply_patch", {"observations": {}})
    assert applied["applied"] is True
    assert source.read_text(encoding="utf-8") == "value = 2\n"

    calls = []
    def fail_targeted(root, python, timeout, selected_tests=None):
        calls.append(selected_tests)
        return {
            "passed": False,
            "returncode": 1,
            "timed_out": False,
            "stdout": "FAILED tests/test_model.py",
            "stderr": "AssertionError",
            "command": [python, "-m", "pytest", *(selected_tests or [])],
        }
    monkeypatch.setattr(tools, "_pytest", fail_targeted)

    result = box.execute("verify_after_patch", {"observations": {}})
    assert result["passed"] is False
    assert result["strategy"] == "targeted_tests_failed"
    assert result["selection"]["selected"] == ["tests/test_model.py"]
    assert result["rollback"]["rolled_back"] is True
    assert source.read_text(encoding="utf-8") == "value = 1\n"
    assert box.patch_applied is False
    assert box.patch_rolled_back is True
    assert box.repair_round == 0


def test_agent_advances_repair_round_after_rollback(tmp_path):
    import sys
    from runfix.agent.tools import ToolBox

    box = ToolBox(tmp_path, sys.executable, {
        "allow_project_code": True,
        "allow_write": True,
    })
    box.latest_log = "RuntimeError: failed after patch"
    box.patch_rolled_back = True
    result = box.execute("analyze_failure", {"observations": {}})
    assert box.repair_round == 1
    assert box.patch_rolled_back is False
    assert result["category"] == "unclassified_runtime_error"


def test_agent_runs_full_suite_after_targeted_tests_pass_and_rolls_back_on_full_failure(tmp_path, monkeypatch):
    import sys
    import runfix.agent.tools as tools
    from runfix.agent.tools import ToolBox

    source = tmp_path / "model.py"
    source.write_text("value = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_model.py").write_text(
        "from model import value\ndef test_value(): assert value == 1\n",
        encoding="utf-8",
    )
    patch = (
        "diff --git a/model.py b/model.py\n"
        "--- a/model.py\n"
        "+++ b/model.py\n"
        "@@ -1 +1 @@\n"
        "-value = 1\n"
        "+value = 2\n"
    )
    box = ToolBox(tmp_path, sys.executable, {
        "allow_project_code": True, "allow_write": True, "timeout": 30
    })
    box.latest_log = f'  File "{source}", line 1, in train\nRuntimeError: original failure'
    box.patch_text = patch
    assert box.execute("validate_patch", {})["valid"]
    assert box.execute("apply_patch", {})["applied"]

    calls = []
    def fake_pytest(root, python, timeout, selected_tests=None):
        calls.append(selected_tests)
        if selected_tests:
            return {"passed": True, "returncode": 0, "timed_out": False,
                    "stdout": "1 passed", "stderr": "", "command": [python, "-m", "pytest", *selected_tests]}
        return {"passed": False, "returncode": 1, "timed_out": False,
                "stdout": "full suite failed", "stderr": "AssertionError",
                "command": [python, "-m", "pytest"]}
    monkeypatch.setattr(tools, "_pytest", fake_pytest)

    result = box.execute("verify_after_patch", {})
    assert calls == [["tests/test_model.py"], None]
    assert result["strategy"] == "targeted_then_full_suite"
    assert result["passed"] is False
    assert result["rollback"]["rolled_back"] is True
    assert source.read_text(encoding="utf-8") == "value = 1\n"
