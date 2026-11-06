"""Tests for research-project understanding, environment planning and metric parsing."""

from __future__ import annotations

import sys

import pytest

from runfix.repro.metrics import compare_metrics, extract_metrics
from runfix.repro.plan import assess_cuda_compatibility, build_environment_plan
from runfix.repro.understanding import understand_project


def test_readme_is_parsed_for_install_and_run_commands(paper_project):
    result = understand_project(paper_project)
    readme = result["readme"]
    assert readme["path"] == "README.md"
    assert any("pip install -r requirements.txt" in command for command in readme["install_commands"])
    assert any("python train.py" in command for command in readme["run_commands"])
    assert "# Demo Paper: Shape-Aware Forecasting" in readme["headings"]


def test_entrypoint_is_ranked_and_documented(paper_project):
    result = understand_project(paper_project)
    assert result["entrypoints"], "an entrypoint must be identified"
    best = result["entrypoints"][0]
    assert best["path"] == "train.py"
    assert any("README" in item for item in best["evidence"])
    assert any("argparse" in item for item in best["evidence"])


def test_configuration_is_parsed_without_yaml_dependency(paper_project):
    result = understand_project(paper_project)
    configs = {item["path"]: item for item in result["configs"]}
    assert "configs/base.yaml" in configs
    assert configs["configs/base.yaml"]["keys"] >= 5
    seeds = {item["key"]: item["value"] for item in result["seeds"]}
    assert seeds.get("seed") == "42" or seeds.get("seed") == 42
    assert "epochs" in result["training_parameters"]
    assert result["training_parameters"]["batch_size"]["value"] in (32, "32")


def test_missing_dataset_reference_is_reported(paper_project):
    result = understand_project(paper_project)
    assert result["datasets"], "the configured data_path must be detected"
    entry = result["datasets"][0]
    assert entry["key"] == "data_path"
    assert entry["exists"] is False
    assert result["missing_datasets"]
    assert any("dataset" in warning.lower() for warning in result["warnings"])


def test_existing_dataset_is_not_reported_as_missing(paper_project):
    data = paper_project / "data"
    data.mkdir()
    (data / "train.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    result = understand_project(paper_project)
    assert result["datasets"][0]["exists"] is True
    assert result["missing_datasets"] == []


def test_expected_metrics_and_frameworks(paper_project):
    result = understand_project(paper_project)
    assert "accuracy" in result["expected_metrics"]
    assert "f1" in result["expected_metrics"]
    assert "PyTorch" not in result["environment"]["frameworks"]


def test_plan_reports_no_manifest_for_a_bare_project(bare_project):
    plan = build_environment_plan(bare_project, include_probe=False)
    environment = next(step for step in plan["steps"] if step["id"] == "environment")
    assert environment["status"] == "missing_manifest"
    assert plan["requires_authorization"]["install_environment"] is False
    assert any("manifest" in warning.lower() for warning in plan["warnings"])


def test_plan_selects_the_declared_manifest(paper_project):
    plan = build_environment_plan(paper_project, include_probe=False)
    environment = next(step for step in plan["steps"] if step["id"] == "environment")
    assert environment["status"] == "ok"
    assert environment["manager"] == "pip"
    assert environment["manifest"] == "requirements.txt"
    assert plan["requires_authorization"]["install_environment"] is True


def test_plan_makes_no_cuda_claim_without_a_declared_wheel_tag(paper_project):
    plan = build_environment_plan(paper_project, include_probe=False)
    assert plan["cuda"]["verdict"] == "unknown"
    reason = plan["cuda"]["reason"].lower()
    assert "skipped" in reason or "declared" in reason or "tag" in reason


def test_cuda_assessment_marks_an_older_driver_as_unsupported():
    result = assess_cuda_compatibility(
        {"cuda_tags": ["cu121"], "torch_index_url_tag": None},
        "| NVIDIA-SMI 535.00  Driver Version: 535.00  CUDA Version: 11.8 |",
    )
    assert result["verdict"] == "unsupported"
    assert result["required_cuda"] == "12.1"
    assert result["driver_cuda"] == "11.8"


def test_cuda_assessment_accepts_a_newer_driver_for_an_older_wheel():
    result = assess_cuda_compatibility(
        {"cuda_tags": ["cu121"], "torch_index_url_tag": None},
        "CUDA Version: 12.2",
    )
    assert result["verdict"] == "supported"


def test_cuda_assessment_accepts_a_sufficient_driver():
    result = assess_cuda_compatibility(
        {"cuda_tags": ["cu118"], "torch_index_url_tag": None},
        "CUDA Version: 12.4",
    )
    assert result["verdict"] == "supported"


def test_cuda_assessment_without_driver_information_is_unknown():
    result = assess_cuda_compatibility({"cuda_tags": ["cu121"], "torch_index_url_tag": None}, "")
    assert result["verdict"] == "unknown"


def test_plan_selects_the_readme_command(paper_project):
    plan = build_environment_plan(paper_project, include_probe=False)
    entrypoint = next(step for step in plan["steps"] if step["id"] == "entrypoint")
    assert entrypoint["status"] == "ready"
    assert entrypoint["selected"]["argv"][:2] == ["python", "train.py"]


def test_metrics_are_extracted_with_evidence_lines():
    log = "\n".join([
        "epoch 1/3 loss=1.0000 acc=0.6000",
        "epoch 2/3 loss=0.5000 acc=0.7000",
        "final test accuracy: 0.8000",
        '{"epoch": 3, "loss": 0.3333, "accuracy": 0.8100}',
    ])
    result = extract_metrics(log)
    assert result["point_count"] == 4
    assert result["series"]["loss"][0]["value"] == 1.0
    assert result["final"]["loss"] == pytest.approx(0.3333)
    assert result["final"]["accuracy"] == pytest.approx(0.81)
    assert result["points"][0]["evidence"].startswith("epoch 1/3")


def test_metric_percentages_are_normalised():
    result = extract_metrics("Best Accuracy: 91.23%")
    assert result["final"]["best_accuracy"] == pytest.approx(0.9123)


def test_metric_parsing_ignores_timestamps_and_epoch_counters():
    result = extract_metrics("epoch 2/10 elapsed=12.5s pid=4242")
    assert result["points"] == []


def test_compare_metrics_detects_match_and_mismatch():
    matched = compare_metrics({"accuracy": 0.81}, {"accuracy": 0.80}, tolerance=0.02)
    assert matched["all_match"] is True
    mismatched = compare_metrics({"accuracy": 0.55}, {"accuracy": 0.80}, tolerance=0.02)
    assert mismatched["all_match"] is False
    missing = compare_metrics({}, {"accuracy": 0.80})
    assert missing["any_missing"] is True
