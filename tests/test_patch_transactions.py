"""Regression tests for patch-transaction concurrency, crash recovery and retention (0.6.1)."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from runfix.patches import (
    _prune_transactions,
    _transaction_root,
    apply_patch,
    get_patch_transaction,
    recover_interrupted_transactions,
    rollback_patch,
)


def _diff(path: str, before: str, after: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"-{before}\n"
        f"+{after}\n"
    )


def _force_interrupted(project, transaction_id: str) -> None:
    """Simulate a crash right after git apply but before the status was written."""
    path = project / ".runfix" / "patch_transactions" / transaction_id / "transaction.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["status"] = "applying"
    path.write_text(json.dumps(metadata), encoding="utf-8")


def test_concurrent_applications_are_serialised(tmp_path):
    files = {}
    for index in range(6):
        name = f"module_{index}.py"
        (tmp_path / name).write_text("value = 1\n", encoding="utf-8")
        files[name] = _diff(name, "value = 1", "value = 2")

    errors: list[BaseException] = []

    def worker(name: str) -> None:
        try:
            result = apply_patch(tmp_path, files[name], confirmed=True)
            assert result["applied"] is True, result
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(worker, list(files)))

    assert not errors
    for name in files:
        assert (tmp_path / name).read_text(encoding="utf-8") == "value = 2\n"

    transactions = [
        item for item in (tmp_path / ".runfix" / "patch_transactions").iterdir()
        if item.is_dir()
    ]
    assert len(transactions) == len(files)


def test_transaction_lock_is_exclusive_across_threads(tmp_path):
    from runfix.patches import _transaction_lock

    order: list[str] = []
    barrier = threading.Barrier(2)

    def critical(name: str) -> None:
        barrier.wait()
        with _transaction_lock(tmp_path):
            order.append(f"{name}-enter")
            threading.Event().wait(0.05)
            order.append(f"{name}-exit")

    threads = [threading.Thread(target=critical, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert order in (
        ["a-enter", "a-exit", "b-enter", "b-exit"],
        ["b-enter", "b-exit", "a-enter", "a-exit"],
    )


def test_interrupted_application_is_restored(tmp_path):
    source = tmp_path / "model.py"
    source.write_text("value = 1\n", encoding="utf-8")
    applied = apply_patch(tmp_path, _diff("model.py", "value = 1", "value = 2"), confirmed=True)
    assert applied["applied"] is True
    assert source.read_text(encoding="utf-8") == "value = 2\n"
    _force_interrupted(tmp_path, applied["transaction_id"])

    result = recover_interrupted_transactions(tmp_path)
    assert [item["transaction_id"] for item in result["recovered"]] == [applied["transaction_id"]]
    assert result["needs_review"] == []
    assert source.read_text(encoding="utf-8") == "value = 1\n"
    assert get_patch_transaction(tmp_path, applied["transaction_id"])["status"] == "recovered_from_interrupt"


def test_interrupted_application_without_changes_needs_no_restore(tmp_path):
    source = tmp_path / "model.py"
    source.write_text("value = 1\n", encoding="utf-8")
    applied = apply_patch(tmp_path, _diff("model.py", "value = 1", "value = 2"), confirmed=True)
    _force_interrupted(tmp_path, applied["transaction_id"])
    # The crash happened before git touched the file, so it still holds the pre-patch hash.
    source.write_text("value = 1\n", encoding="utf-8")

    result = recover_interrupted_transactions(tmp_path)
    assert result["needs_review"] == []
    assert source.read_text(encoding="utf-8") == "value = 1\n"
    assert get_patch_transaction(tmp_path, applied["transaction_id"])["status"] == "recovered_from_interrupt"


def test_interrupted_application_with_foreign_edit_is_left_for_review(tmp_path):
    source = tmp_path / "model.py"
    source.write_text("value = 1\n", encoding="utf-8")
    applied = apply_patch(tmp_path, _diff("model.py", "value = 1", "value = 2"), confirmed=True)
    _force_interrupted(tmp_path, applied["transaction_id"])
    source.write_text("value = 99\n", encoding="utf-8")

    result = recover_interrupted_transactions(tmp_path)
    assert result["recovered"] == []
    assert result["needs_review"][0]["transaction_id"] == applied["transaction_id"]
    assert source.read_text(encoding="utf-8") == "value = 99\n"
    assert get_patch_transaction(tmp_path, applied["transaction_id"])["status"] == "interrupted_needs_review"


def test_apply_recovers_interrupted_transactions_before_starting(tmp_path):
    first = tmp_path / "a.py"
    first.write_text("a = 1\n", encoding="utf-8")
    applied = apply_patch(tmp_path, _diff("a.py", "a = 1", "a = 2"), confirmed=True)
    _force_interrupted(tmp_path, applied["transaction_id"])

    second = tmp_path / "b.py"
    second.write_text("b = 1\n", encoding="utf-8")
    result = apply_patch(tmp_path, _diff("b.py", "b = 1", "b = 2"), confirmed=True)
    assert result["applied"] is True
    assert result["recovery"]["recovered"][0]["transaction_id"] == applied["transaction_id"]
    assert first.read_text(encoding="utf-8") == "a = 1\n"
    assert second.read_text(encoding="utf-8") == "b = 2\n"


def test_prune_keeps_applied_transactions_and_drops_rolled_back_ones(tmp_path):
    import os

    names = []
    for index in range(4):
        name = f"file_{index}.py"
        (tmp_path / name).write_text("v = 1\n", encoding="utf-8")
        names.append(name)

    rolled_ids = []
    for name in names[:2]:
        transaction_id = apply_patch(tmp_path, _diff(name, "v = 1", "v = 2"), confirmed=True)["transaction_id"]
        assert rollback_patch(tmp_path, transaction_id, confirmed=True)["rolled_back"] is True
        rolled_ids.append(transaction_id)

    applied_ids = []
    for name in names[2:]:
        applied_ids.append(apply_patch(tmp_path, _diff(name, "v = 1", "v = 2"), confirmed=True)["transaction_id"])

    base = _transaction_root(tmp_path)
    # Make the rolled-back transactions the oldest so they are the prune candidates.
    for offset, transaction_id in enumerate(rolled_ids):
        os.utime(base / transaction_id, (1_600_000_000 + offset, 1_600_000_000 + offset))

    pruned = _prune_transactions(base, keep=2)
    assert sorted(pruned) == sorted(rolled_ids)

    remaining = {item.name for item in base.iterdir() if item.is_dir()}
    assert set(applied_ids).issubset(remaining)
    for transaction_id in rolled_ids:
        assert transaction_id not in remaining


def test_rollback_after_recovery_refuses_and_reports_status(tmp_path):
    source = tmp_path / "model.py"
    source.write_text("value = 1\n", encoding="utf-8")
    applied = apply_patch(tmp_path, _diff("model.py", "value = 1", "value = 2"), confirmed=True)
    _force_interrupted(tmp_path, applied["transaction_id"])
    recover_interrupted_transactions(tmp_path)

    result = rollback_patch(tmp_path, applied["transaction_id"], confirmed=True)
    assert result["rolled_back"] is False
    assert "recovered_from_interrupt" in result["reason"]
    assert source.read_text(encoding="utf-8") == "value = 1\n"


def test_unreadable_transaction_metadata_is_tolerated_during_recovery(tmp_path):
    base = _transaction_root(tmp_path)
    broken = base / ("a" * 32)
    broken.mkdir()
    (broken / "transaction.json").write_text("{not json", encoding="utf-8")

    result = recover_interrupted_transactions(tmp_path)
    assert result["recovered"] == []
    assert result["needs_review"] == []


@pytest.mark.parametrize("status", ["applied", "rolled_back", "apply_failed"])
def test_recovery_ignores_finished_transactions(tmp_path, status):
    source = tmp_path / "model.py"
    source.write_text("value = 1\n", encoding="utf-8")
    applied = apply_patch(tmp_path, _diff("model.py", "value = 1", "value = 2"), confirmed=True)
    path = tmp_path / ".runfix" / "patch_transactions" / applied["transaction_id"] / "transaction.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["status"] = status
    path.write_text(json.dumps(metadata), encoding="utf-8")

    result = recover_interrupted_transactions(tmp_path)
    assert result["recovered"] == []
    assert source.read_text(encoding="utf-8") == "value = 2\n"
