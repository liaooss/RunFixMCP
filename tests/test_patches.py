import subprocess

from runfix.patches import (
    apply_patch,
    get_patch_transaction,
    rollback_patch,
    validate_patch,
)


def _diff(path, before, after):
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"-{before}\n"
        f"+{after}\n"
    )


def test_reject_traversal(tmp_path):
    patch = (
        "diff --git a/../../x.py b/../../x.py\n"
        "--- a/../../x.py\n"
        "+++ b/../../x.py\n"
        "@@ -1 +1 @@\n-a\n+b\n"
    )
    result = validate_patch(tmp_path, patch)
    assert not result["valid"]
    assert "Unsafe" in result["reason"]


def test_reject_binary(tmp_path):
    assert not validate_patch(tmp_path, "GIT binary patch\n")["valid"]


def test_ast_validation_rejects_syntax_error_without_modifying_project(tmp_path):
    source = tmp_path / "model.py"
    source.write_text("value = 1\n", encoding="utf-8")
    patch = _diff("model.py", "value = 1", "def broken(:")
    result = validate_patch(tmp_path, patch)
    assert result["valid"] is False
    assert result["ast_validation"]["status"] == "failed"
    assert source.read_text(encoding="utf-8") == "value = 1\n"


def test_valid_patch_can_be_rolled_back_transactionally(tmp_path):
    source = tmp_path / "model.py"
    original = "value = 1\n"
    source.write_text(original, encoding="utf-8")
    patch = _diff("model.py", "value = 1", "value = 2")

    applied = apply_patch(tmp_path, patch, confirmed=True)
    assert applied["applied"] is True
    assert source.read_text(encoding="utf-8") == "value = 2\n"
    assert len(applied["transaction_id"]) == 32

    transaction = get_patch_transaction(tmp_path, applied["transaction_id"])
    assert transaction["status"] == "applied"
    assert transaction["files"][0]["sha256_before"]
    assert transaction["files"][0]["sha256_after"]

    rolled = rollback_patch(tmp_path, applied["transaction_id"], confirmed=True)
    assert rolled["rolled_back"] is True
    assert source.read_text(encoding="utf-8") == original

    repeated = rollback_patch(tmp_path, applied["transaction_id"], confirmed=True)
    assert repeated["rolled_back"] is True
    assert repeated["already_rolled_back"] is True


def test_rollback_refuses_to_overwrite_independent_changes(tmp_path):
    source = tmp_path / "model.py"
    source.write_text("value = 1\n", encoding="utf-8")
    applied = apply_patch(
        tmp_path, _diff("model.py", "value = 1", "value = 2"), confirmed=True
    )
    assert applied["applied"]
    source.write_text("value = 3\n", encoding="utf-8")

    result = rollback_patch(tmp_path, applied["transaction_id"], confirmed=True)
    assert result["rolled_back"] is False
    assert result["conflicts"][0]["path"] == "model.py"
    assert source.read_text(encoding="utf-8") == "value = 3\n"


def test_new_file_patch_is_removed_by_rollback(tmp_path):
    patch = (
        "diff --git a/new_module.py b/new_module.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/new_module.py\n"
        "@@ -0,0 +1 @@\n"
        "+value = 42\n"
    )
    result = apply_patch(tmp_path, patch, confirmed=True)
    assert result["applied"]
    assert (tmp_path / "new_module.py").read_text(encoding="utf-8") == "value = 42\n"
    rolled = rollback_patch(tmp_path, result["transaction_id"], confirmed=True)
    assert rolled["rolled_back"]
    assert not (tmp_path / "new_module.py").exists()


def test_patch_requires_explicit_confirmation(tmp_path):
    source = tmp_path / "model.py"
    source.write_text("value = 1\n", encoding="utf-8")
    result = apply_patch(
        tmp_path, _diff("model.py", "value = 1", "value = 2"), confirmed=False
    )
    assert result["applied"] is False
    assert source.read_text(encoding="utf-8") == "value = 1\n"


def test_reject_symlink_patch_mode(tmp_path):
    patch = (
        "diff --git a/link.py b/link.py\n"
        "new file mode 120000\n"
        "--- /dev/null\n"
        "+++ b/link.py\n"
        "@@ -0,0 +1 @@\n"
        "+outside.py\n"
    )
    result = validate_patch(tmp_path, patch)
    assert result["valid"] is False
    assert "Symlink" in result["reason"]


def test_rollback_preflights_all_backups_before_restoring_any_file(tmp_path):
    import pytest

    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("a = 1\n", encoding="utf-8")
    second.write_text("b = 1\n", encoding="utf-8")
    patch = (
        "diff --git a/first.py b/first.py\n"
        "--- a/first.py\n+++ b/first.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n"
        "diff --git a/second.py b/second.py\n"
        "--- a/second.py\n+++ b/second.py\n@@ -1 +1 @@\n-b = 1\n+b = 2\n"
    )
    applied = apply_patch(tmp_path, patch, confirmed=True)
    assert applied["applied"]
    metadata = get_patch_transaction(tmp_path, applied["transaction_id"])
    backup = tmp_path / ".runfix" / "patch_transactions" / applied["transaction_id"] / metadata["files"][1]["backup_path"]
    backup.unlink()

    with pytest.raises(FileNotFoundError):
        rollback_patch(tmp_path, applied["transaction_id"], confirmed=True)
    assert first.read_text(encoding="utf-8") == "a = 2\n"
    assert second.read_text(encoding="utf-8") == "b = 2\n"


def test_rollback_refuses_symlink_replacement(tmp_path):
    import os

    import pytest

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
    folder = tmp_path / "src"
    folder.mkdir()
    source = folder / "model.py"
    source.write_text("value = 1\n", encoding="utf-8")
    patch = (
        "diff --git a/src/model.py b/src/model.py\n"
        "--- a/src/model.py\n+++ b/src/model.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    )
    applied = apply_patch(tmp_path, patch, confirmed=True)
    assert applied["applied"]
    outside = tmp_path / "outside.py"
    outside.write_text("outside = True\n", encoding="utf-8")
    source.unlink()
    source.symlink_to(outside)

    result = rollback_patch(tmp_path, applied["transaction_id"], confirmed=True)
    assert result["rolled_back"] is False
    assert result["conflicts"]
    assert outside.read_text(encoding="utf-8") == "outside = True\n"
