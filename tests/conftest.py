"""Shared fixtures: a synthetic repository that behaves like a small research project."""

from __future__ import annotations

import textwrap

import pytest

README = """\
# Demo Paper: Shape-Aware Forecasting

Official implementation of "Shape-Aware Forecasting" (demo).

## Installation

```bash
pip install -r requirements.txt
```

## Training

```bash
python train.py --config configs/base.yaml
```

## Results

The paper reports **Accuracy** and **F1** on the test split.
"""

TRAIN = textwrap.dedent(
    """\
    import argparse


    def main():
        parser = argparse.ArgumentParser()
        parser.add_argument("--config", default="configs/base.yaml")
        parser.add_argument("--seed", type=int, default=42)
        args = parser.parse_args()
        for epoch in range(1, 4):
            print(f"epoch {epoch}/3 loss={1.0 / epoch:.4f} acc={0.5 + epoch / 10:.4f}")
        print("final test accuracy: 0.8000")
        print("final test f1: 0.7400")
        print("training complete")


    if __name__ == "__main__":
        main()
    """
)

CONFIG = """\
seed: 42
epochs: 3
batch_size: 32
learning_rate: 0.001
data_path: data/train.csv
output_dir: outputs/run1
"""

SMOKE_TEST = textwrap.dedent(
    """\
    def test_importable():
        assert True
    """
)


@pytest.fixture
def paper_project(tmp_path):
    """A tiny but realistic research repository (no deep-learning dependency needed)."""
    (tmp_path / "README.md").write_text(README, encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("numpy>=1.20\n", encoding="utf-8")
    (tmp_path / "train.py").write_text(TRAIN, encoding="utf-8")
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "base.yaml").write_text(CONFIG, encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_smoke.py").write_text(SMOKE_TEST, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".runfix/\n__pycache__/\noutputs/\n", encoding="utf-8")
    _git_init(tmp_path)
    return tmp_path


def _git_init(root) -> bool:
    """Make the fixture a committed repository, like real research code on GitHub."""
    import subprocess

    commands = [
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.name=RunFix Test", "-c", "user.email=test@localhost",
         "commit", "-q", "-m", "fixture"],
    ]
    try:
        for command in commands:
            result = subprocess.run(command, cwd=str(root), capture_output=True,
                                    text=True, timeout=30, shell=False)
            if result.returncode != 0:
                return False
    except Exception:
        return False
    return True


@pytest.fixture
def bare_project(tmp_path):
    """A repository with no manifest, no README and no conventional entrypoint."""
    (tmp_path / "scratch.py").write_text("value = 1\n", encoding="utf-8")
    return tmp_path
