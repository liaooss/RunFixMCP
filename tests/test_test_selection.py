from runfix.test_selection import select_tests


def test_selects_test_file_importing_traceback_module(tmp_path):
    package = tmp_path / "models"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    source = package / "network.py"
    source.write_text("class Net: pass\n", encoding="utf-8")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_network.py").write_text(
        "from models.network import Net\n\ndef test_net(): assert Net is not None\n",
        encoding="utf-8",
    )
    (tests / "test_data.py").write_text(
        "def test_data(): assert True\n", encoding="utf-8"
    )

    traceback = f'  File "{source}", line 1, in forward\nRuntimeError: boom'
    result = select_tests(tmp_path, traceback)
    assert result["selected"] == ["tests/test_network.py"]
    assert result["fallback_required"] is False


def test_falls_back_to_full_suite_when_no_test_dependency_is_found(tmp_path):
    source = tmp_path / "orphan.py"
    source.write_text("def run(): pass\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_misc.py").write_text(
        "def test_misc(): assert True\n", encoding="utf-8"
    )

    traceback = f'  File "{source}", line 1, in run\nValueError: boom'
    result = select_tests(tmp_path, traceback)
    assert result["selected"] == []
    assert result["fallback_required"] is True


def test_traceback_that_mentions_test_file_selects_it_directly(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    target = tests / "test_shapes.py"
    target.write_text("def test_shape(): assert True\n", encoding="utf-8")
    traceback = f'  File "{target}", line 1, in test_shape\nAssertionError'
    result = select_tests(tmp_path, traceback)
    assert "tests/test_shapes.py" in result["selected"]
