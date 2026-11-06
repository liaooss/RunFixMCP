"""Regression tests for background-task exit-code recovery across a server restart (0.6.1)."""

import sys
import time

import pytest

from runfix import task_manager
from runfix.task_manager import TaskManager


def _wait_for_record(project, task_id, timeout=20.0):
    path = project / ".runfix" / "tasks" / task_id / "process_result.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.is_file():
            return path
        time.sleep(0.05)
    return None


def _wait_for_terminal(manager, project, task_id, timeout=20.0):
    deadline = time.time() + timeout
    latest = None
    while time.time() < deadline:
        latest = manager.status(project, task_id)
        if latest["status"] not in {"running", "starting", "stop_requested"}:
            return latest
        time.sleep(0.05)
    return latest


def test_supervisor_records_the_exit_code(tmp_path):
    manager = TaskManager()
    result = manager.start(
        tmp_path,
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        max_runtime_seconds=60,
    )
    assert _wait_for_record(tmp_path, result["task_id"]) is not None
    status = _wait_for_terminal(manager, tmp_path, result["task_id"])
    assert status["status"] == "failed"
    assert status["returncode"] == 3


def test_exit_code_is_recovered_after_a_server_restart(tmp_path):
    manager = TaskManager()
    result = task_manager.TaskManager().start(
        tmp_path,
        [sys.executable, "-c", "import time; time.sleep(0.4); import sys; sys.exit(7)"],
        max_runtime_seconds=60,
    )
    task_id = result["task_id"]

    # Simulate a server restart: the in-memory process table is lost while the task runs.
    with task_manager._LOCK:
        task_manager._PROCESSES.pop(task_id, None)

    assert _wait_for_record(tmp_path, task_id) is not None
    deadline = time.time() + 20
    while time.time() < deadline:
        if not task_manager._process_alive(int(result["pid"])):
            break
        time.sleep(0.05)

    status = manager.status(tmp_path, task_id)
    assert status["status"] == "failed"
    assert status["returncode"] == 7
    assert status["exit_code_source"] == "supervisor_record_after_restart"
    assert "recovered from the supervisor record" in status["note"]


def test_completed_task_reports_the_exit_code_source(tmp_path):
    manager = TaskManager()
    result = manager.start(
        tmp_path, [sys.executable, "-c", "print('ok')"], max_runtime_seconds=60
    )
    status = _wait_for_terminal(manager, tmp_path, result["task_id"])
    assert status["status"] == "completed"
    assert status["returncode"] == 0
    assert status["exit_code_source"] in {"live_process"}


def test_task_without_a_supervisor_record_is_reported_as_unobserved(tmp_path):
    manager = TaskManager()
    result = manager.start(
        tmp_path,
        [sys.executable, "-c", "import time; time.sleep(0.3)"],
        max_runtime_seconds=60,
    )
    task_id = result["task_id"]
    with task_manager._LOCK:
        task_manager._PROCESSES.pop(task_id, None)

    deadline = time.time() + 20
    while time.time() < deadline:
        if not task_manager._process_alive(int(result["pid"])):
            break
        time.sleep(0.05)

    # Remove the supervisor record to model a hard kill of the whole process tree.
    (tmp_path / ".runfix" / "tasks" / task_id / "process_result.json").unlink(missing_ok=True)
    (tmp_path / ".runfix" / "tasks" / task_id / "process_result.json.tmp").unlink(missing_ok=True)

    status = manager.status(tmp_path, task_id)
    assert status["status"] == "ended_unobserved"
    assert status["returncode"] is None
    assert status["exit_code_source"] is None


def test_log_capture_still_reaches_the_task_log(tmp_path):
    manager = TaskManager()
    result = manager.start(
        tmp_path,
        [sys.executable, "-c", "print('SUPERVISOR_STDOUT_OK')"],
        max_runtime_seconds=60,
    )
    _wait_for_terminal(manager, tmp_path, result["task_id"])
    log = manager.read_log(tmp_path, result["task_id"])["log"]
    assert "SUPERVISOR_STDOUT_OK" in log


def test_stop_terminates_the_supervised_process_tree(tmp_path):
    manager = TaskManager()
    result = manager.start(
        tmp_path,
        [sys.executable, "-c", "import time; print('READY', flush=True); time.sleep(60)"],
        max_runtime_seconds=60,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        if "READY" in manager.read_log(tmp_path, result["task_id"])["log"]:
            break
        time.sleep(0.05)

    stopped = manager.stop(tmp_path, result["task_id"])
    assert stopped["stopped"] is True
    assert stopped["task"]["status"] == "stopped"
