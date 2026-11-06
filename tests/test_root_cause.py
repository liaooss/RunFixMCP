"""Tests for the evidence graph and root-cause ranking (0.8.0)."""

from __future__ import annotations

import pytest

from runfix.diagnosis.evidence import build_evidence_graph
from runfix.diagnosis.rootcause import rank_root_causes

MISSING_TORCH = (
    "Traceback (most recent call last):\n"
    '  File "{project}/train.py", line 3, in <module>\n'
    "    import torch\n"
    "ModuleNotFoundError: No module named 'torch'\n"
)


def _fake_environment(**overrides):
    base = {
        "python_executable": "C:/Python311/python.exe",
        "python_version": "3.10.12",
        "platform": "Windows-Test",
        "packages": {},
        "torch": {"installed": False, "version": None, "cuda_available": None,
                  "cuda_version": None, "gpu_count": None, "error": None},
        "nvidia_smi": {"available": False, "summary": ""},
        "issues": [],
    }
    base.update(overrides)
    return base


@pytest.fixture
def stubbed(monkeypatch):
    """Replace subprocess-backed probes with deterministic fakes."""
    state = {"environment": _fake_environment(), "installed": {}}

    monkeypatch.setattr("runfix.diagnosis.evidence.diagnose_environment",
                        lambda *a, **k: state["environment"])
    monkeypatch.setattr("runfix.diagnosis.evidence.probe_package_versions",
                        lambda python, names, **k: {n: state["installed"].get(n) for n in names})
    return state


@pytest.fixture
def project(tmp_path):
    (tmp_path / "train.py").write_text("import torch\nimport numpy\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("torch>=2.0\nnumpy>=1.20\n", encoding="utf-8")
    return tmp_path


def test_evidence_graph_links_failure_to_source(project, stubbed):
    stubbed["environment"] = _fake_environment()
    stubbed["installed"] = {"torch": None, "numpy": "1.24.0"}
    traceback_text = MISSING_TORCH.format(project=str(project).replace("\\", "/"))
    bundle = build_evidence_graph(project, traceback_text, include_probe=True)
    graph = bundle["graph"]
    relations = graph["relations"]
    assert relations.get("failed_with") == 1
    assert relations.get("raised_in") == 1
    assert relations.get("imports", 0) >= 2
    assert relations.get("declares", 0) >= 2
    kinds = graph["node_kinds"]
    assert kinds.get("exception") == 1
    assert kinds.get("dependency", 0) >= 2
    assert kinds.get("installed_package", 0) >= 2


def test_declared_missing_module_is_the_top_candidate(project, stubbed):
    stubbed["installed"] = {"torch": None, "numpy": "1.24.0"}
    traceback_text = MISSING_TORCH.format(project=str(project).replace("\\", "/"))
    result = rank_root_causes(project, traceback_text, include_probe=True)
    top = result["top"]
    assert top["kind"] == "module_declared_missing"
    assert top["action"]["kind"] == "install_package"
    assert top["confidence"] >= 0.9
    sources = {item["source"] for item in top["evidence"]}
    assert "requirements.txt" in sources
    assert "traceback" in sources
    assert top["rank"] == 1


def test_undeclared_missing_module_asks_for_a_declaration(tmp_path, stubbed):
    (tmp_path / "train.py").write_text("import bitsandbytes\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("numpy>=1.20\n", encoding="utf-8")
    traceback_text = MISSING_TORCH.format(project=str(tmp_path).replace("\\", "/"))
    result = rank_root_causes(tmp_path, traceback_text, include_probe=True)
    top = result["top"]
    assert top["kind"] == "module_missing_undeclared"
    assert top["action"]["kind"] == "declare_and_install"


def test_version_conflict_is_ranked_with_its_constraint(tmp_path, stubbed):
    (tmp_path / "requirements.txt").write_text("torch==2.0.0\n", encoding="utf-8")
    stubbed["installed"] = {"torch": "2.1.0"}
    result = rank_root_causes(tmp_path, None, include_probe=True)
    kinds = [item["kind"] for item in result["candidates"]]
    assert "version_constraint_unsatisfied" in kinds
    candidate = next(item for item in result["candidates"]
                     if item["kind"] == "version_constraint_unsatisfied")
    assert candidate["action"]["command"] == ["pip", "install", "torch==2.0.0"]
    assert candidate["confidence"] >= 0.85


def test_python_incompatibility_is_reported(tmp_path, stubbed):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nrequires-python=">=3.11"\n', encoding="utf-8"
    )
    result = rank_root_causes(tmp_path, None, include_probe=True)
    kinds = [item["kind"] for item in result["candidates"]]
    assert "python_incompatible" in kinds
    candidate = next(item for item in result["candidates"] if item["kind"] == "python_incompatible")
    assert candidate["action"]["kind"] == "select_interpreter"


def test_cuda_driver_mismatch_is_detected(tmp_path, stubbed):
    (tmp_path / "requirements.txt").write_text(
        "--extra-index-url https://download.pytorch.org/whl/cu121\ntorch==2.1.0\n", encoding="utf-8"
    )
    stubbed["installed"] = {"torch": None}
    stubbed["environment"] = _fake_environment(
        nvidia_smi={"available": True, "summary": "CUDA Version: 11.8"},
        torch={"installed": False, "version": None, "cuda_available": False,
               "cuda_version": None, "gpu_count": 1, "error": None},
    )
    result = rank_root_causes(tmp_path, None, include_probe=True)
    kinds = [item["kind"] for item in result["candidates"]]
    assert "cuda_driver_mismatch" in kinds
    candidate = next(item for item in result["candidates"] if item["kind"] == "cuda_driver_mismatch")
    assert candidate["action"]["kind"] == "select_cuda_wheel"
    assert "12.1" in candidate["statement"]


def test_missing_dataset_is_reported_when_understanding_is_included(tmp_path, stubbed):
    (tmp_path / "train.py").write_text("print('train')\n", encoding="utf-8")
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "base.yaml").write_text("data_path: data/train.csv\n", encoding="utf-8")
    result = rank_root_causes(tmp_path, None, include_probe=True, include_understanding=True)
    kinds = [item["kind"] for item in result["candidates"]]
    assert "dataset_missing" in kinds
    candidate = next(item for item in result["candidates"] if item["kind"] == "dataset_missing")
    assert candidate["action"]["kind"] == "configure_data"


def test_no_signal_falls_back_to_an_honest_unclassified_candidate(tmp_path, stubbed):
    (tmp_path / "train.py").write_text("print('train')\n", encoding="utf-8")
    result = rank_root_causes(tmp_path, None, include_probe=True)
    assert result["top"]["kind"] == "unclassified_error"
    assert result["top"]["action"]["kind"] == "collect_more_evidence"
    assert result["top"]["confidence"] <= 0.4


def test_candidates_are_sorted_by_score(project, stubbed):
    stubbed["installed"] = {"torch": None, "numpy": "1.10.0"}
    result = rank_root_causes(project, None, include_probe=True)
    scores = [item["score"] for item in result["candidates"]]
    assert scores == sorted(scores, reverse=True)
    assert [item["rank"] for item in result["candidates"]] == list(
        range(1, len(result["candidates"]) + 1)
    )


def test_max_candidates_is_respected(project, stubbed):
    stubbed["installed"] = {"torch": None, "numpy": "1.10.0"}
    result = rank_root_causes(project, None, include_probe=True, max_candidates=1)
    assert len(result["candidates"]) == 1


def test_analysis_exposes_the_graph_summary(tmp_path, stubbed):
    (tmp_path / "train.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("numpy>=1.20\n", encoding="utf-8")
    result = rank_root_causes(tmp_path, None, include_probe=True)
    assert result["evidence_graph"]["node_count"] >= 3
    assert "relations" in result["evidence_graph"]
    assert result["notes"]
