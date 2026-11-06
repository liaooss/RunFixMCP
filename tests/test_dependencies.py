"""Tests for dependency-constraint analysis (0.8.0)."""

from __future__ import annotations

import pytest

from runfix.diagnosis.dependencies import (
    analyse_dependencies,
    compare_versions,
    declared_requirements,
    parse_environment_yml,
    parse_pyproject,
    parse_requirements,
    parse_version,
    python_requirement,
    satisfies,
)


@pytest.mark.parametrize("version,specifier,expected", [
    ("2.1.0", ">=2.0", True),
    ("1.9.0", ">=2.0", False),
    ("2.0.0", "==2.0", True),
    ("2.0.1", "==2.0", False),
    ("2.0.5", "==2.0.*", True),
    ("2.1.0", "==2.0.*", False),
    ("1.7.0", "~=1.4,>=1.4.2", True),
    ("2.0.0", "~=1.4", False),
    ("1.4.0", "!=1.4.0", False),
    ("1.5.0", "", True),
    ("abc", ">=1.0", None),
])
def test_specifier_satisfaction(version, specifier, expected):
    assert satisfies(version, specifier) is expected


def test_missing_version_never_satisfies_a_constraint():
    assert satisfies(None, ">=1.0") is False


def test_version_parsing_and_comparison():
    assert parse_version("1.2.3rc1") == (1, 2, 3)
    assert parse_version("v2.0") == (2, 0)
    assert compare_versions((1, 2), (1, 2, 0)) == 0
    assert compare_versions((1, 10), (1, 9)) == 1


def test_parse_requirements_skips_comments_and_urls():
    text = """
# a comment
numpy>=1.20
torch==2.1.0  # inline comment is part of the line
-r other.txt
git+https://github.com/x/y.git
scikit-learn[alldeps]>=1.0; python_version >= "3.9"
"""
    parsed = {item["canonical"]: item for item in parse_requirements(text)}
    assert "numpy" in parsed and parsed["numpy"]["specifier"] == ">=1.20"
    assert "torch" in parsed
    assert "scikit-learn" in parsed
    assert "other.txt" not in parsed


def test_parse_environment_yml_handles_conda_and_pip_sections():
    text = """
name: paper
dependencies:
  - python=3.10
  - pytorch=2.1
  - numpy>=1.21
  - pip:
    - transformers==4.35.0
    - datasets>=2.0
"""
    parsed = parse_environment_yml(text)
    by_name = {item["canonical"]: item for item in parsed}
    assert by_name["python"]["specifier"] == "==3.10"
    assert by_name["pytorch"]["specifier"] == "==2.1"
    assert by_name["numpy"]["specifier"] == ">=1.21"
    assert by_name["transformers"]["specifier"] == "==4.35.0"
    assert by_name["datasets"]["source"] == "pip-section"


def test_parse_pyproject_reads_project_dependencies():
    text = """
[project]
name = "x"
requires-python = ">=3.10"
dependencies = ["numpy>=1.20", "torch==2.1.0"]
"""
    parsed = {item["canonical"]: item for item in parse_pyproject(text)}
    assert parsed["numpy"]["specifier"] == ">=1.20"
    assert parsed["torch"]["specifier"] == "==2.1.0"


def test_python_requirement_from_pyproject_and_conda(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nrequires-python=">=3.10"\n', encoding="utf-8"
    )
    assert python_requirement(tmp_path) == ">=3.10"

    (tmp_path / "pyproject.toml").unlink()
    (tmp_path / "environment.yml").write_text("dependencies:\n  - python=3.9\n", encoding="utf-8")
    assert python_requirement(tmp_path) == "==3.9"


def test_declared_requirements_records_the_manifest(tmp_path):
    (tmp_path / "requirements.txt").write_text("numpy>=1.20\n", encoding="utf-8")
    (tmp_path / "environment.yml").write_text("dependencies:\n  - scipy=1.11\n", encoding="utf-8")
    declared = declared_requirements(tmp_path)
    by_name = {item["canonical"]: item["manifest"] for item in declared}
    assert by_name["numpy"] == "requirements.txt"
    assert by_name["scipy"] == "environment.yml"


def test_analysis_reports_missing_and_unsatisfied(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "numpy>=1.20\nscipy==1.10.0\nmissingpkg>=0.1\n", encoding="utf-8"
    )
    result = analyse_dependencies(tmp_path, {"numpy": "1.24.0", "scipy": "1.11.0"},
                                  python_version="3.10.12")
    kinds = {(item["kind"], item["package"]) for item in result["findings"]}
    assert ("version_unsatisfied", "scipy") in kinds
    assert ("not_installed", "missingpkg") in kinds
    assert ("not_installed", "numpy") not in kinds
    assert result["counts"]["error"] == 2
    assert any(item["action"] == "install_constraint" for item in result["resolutions"])


def test_analysis_reports_manifest_disagreement(tmp_path):
    (tmp_path / "requirements.txt").write_text("numpy>=1.20\n", encoding="utf-8")
    (tmp_path / "environment.yml").write_text("dependencies:\n  - numpy=1.19\n", encoding="utf-8")
    result = analyse_dependencies(tmp_path, {"numpy": "1.24.0"})
    findings = [item for item in result["findings"] if item["kind"] == "manifest_disagreement"]
    assert findings and findings[0]["package"] == "numpy"
    assert set(findings[0]["specifiers"]) == {">=1.20", "==1.19"}
    assert any(item["action"] == "choose_manifest" for item in result["resolutions"])


def test_analysis_reports_python_incompatibility(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nrequires-python=">=3.11"\n', encoding="utf-8"
    )
    result = analyse_dependencies(tmp_path, {}, python_version="3.9.18")
    kinds = [item["kind"] for item in result["findings"]]
    assert "python_incompatible" in kinds
    assert result["python_satisfied"] is False


def test_analysis_is_satisfied_for_a_matching_environment(tmp_path):
    (tmp_path / "requirements.txt").write_text("numpy>=1.20\n", encoding="utf-8")
    result = analyse_dependencies(tmp_path, {"numpy": "1.26.0"})
    assert result["findings"] == []
    assert result["counts"]["error"] == 0
