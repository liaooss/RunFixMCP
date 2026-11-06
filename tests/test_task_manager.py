import sys
import time

import pytest

from runfix.task_manager import TaskManager


def _wait_for_terminal(manager, project, task_id, timeout=5):
    deadline = time.time() + timeout
    latest = None
    while time.time() < deadline:
        latest = manager.status(project, task_id)
        if latest["status"] not in {"running", "starting", "stop_requested"}:
            return latest
        time.sleep(0.05)
    return latest


def test_background_task_records_experiment_fingerprint_and_log(tmp_path):
    manager = TaskManager()
    result = manager.start(
        tmp_path,
        [sys.executable, "-c", "print('RUNFIX_TASK_OK')"],
        max_runtime_seconds=60,
        label="unit test",
    )
    assert result["status"] == "running"
    assert len(result["experiment_fingerprint"]) == 64
    assert result["git"]["commit"] is None or isinstance(result["git"]["commit"], str)

    status = _wait_for_terminal(manager, tmp_path, result["task_id"])
    assert status["status"] == "completed"
    assert status["returncode"] == 0

    log = manager.read_log(tmp_path, result["task_id"])
    assert "RUNFIX_TASK_OK" in log["log"]
    assert log["log_size_bytes"] > 0


def test_background_task_can_be_stopped(tmp_path):
    manager = TaskManager()
    result = manager.start(
        tmp_path,
        [sys.executable, "-c", "import time; print('READY', flush=True); time.sleep(60)"],
        max_runtime_seconds=60,
    )
    stopped = manager.stop(tmp_path, result["task_id"])
    assert stopped["stopped"] is True
    assert stopped["task"]["status"] == "stopped"


def test_list_tasks_returns_recent_tasks(tmp_path):
    manager = TaskManager()
    result = manager.start(
        tmp_path,
        [sys.executable, "-c", "print('done')"],
        max_runtime_seconds=60,
    )
    _wait_for_terminal(manager, tmp_path, result["task_id"])
    listing = manager.list_tasks(tmp_path)
    assert listing["count"] == 1
    assert listing["tasks"][0]["task_id"] == result["task_id"]


def test_task_id_path_traversal_is_rejected(tmp_path):
    manager = TaskManager()
    with pytest.raises(ValueError):
        manager.status(tmp_path, "../task.json")


def test_background_task_rejects_shell_string(tmp_path):
    manager = TaskManager()
    with pytest.raises(ValueError):
        manager.start(tmp_path, "python train.py")


def test_task_storage_rejects_symlinked_runfix_directory(tmp_path):
    def _can_create_symlink() -> bool:
        target = tmp_path / "symlink_probe_target"
        target.write_text("x", encoding="utf-8")
        link = tmp_path / "symlink_probe_link"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            return False
        finally:
            link.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
        return True

    if not _can_create_symlink():
        pytest.skip("This Windows account cannot create symlinks (no SeCreateSymbolicLinkPrivilege).")
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / ".runfix").symlink_to(outside, target_is_directory=True)
    manager = TaskManager()
    with pytest.raises(PermissionError):
        manager.start(project, [sys.executable, "-c", "print(1)"])
    assert list(outside.iterdir()) == []


def test_completed_task_log_redacts_api_key(tmp_path):
    manager = TaskManager()
    result = manager.start(
        tmp_path,
        [sys.executable, "-c", "print('API_KEY=sk-12345678901234567890')"],
        max_runtime_seconds=60,
    )
    status = _wait_for_terminal(manager, tmp_path, result["task_id"])
    assert status["status"] == "completed"
    log = manager.read_log(tmp_path, result["task_id"])["log"]
    assert "12345678901234567890" not in log
    assert "[REDACTED]" in log or "[REDACTED_API_KEY]" in log
