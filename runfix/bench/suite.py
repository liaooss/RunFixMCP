"""A self-contained, reproducible benchmark suite for RunFix's diagnosis.

Why synthetic? The evaluation must run on an offline machine, deterministically, with no
GPU, no Docker and no network. Each task is a *real* small repository with a *real* defect
and a recorded ground truth, materialised into a fresh directory per run. That makes the
numbers reproducible, but it also means they are **not** a substitute for a curated corpus
of published research repositories — the report says so explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUITE = "synthetic-v1"
SUITE_DESCRIPTION = (
    "Self-contained diagnosis tasks. Each task is a small repository with one injected "
    "defect, a fixed environment description and a recorded expected root cause and action."
)


@dataclass(frozen=True)
class BenchTask:
    id: str
    category: str
    description: str
    expected_kind: str
    expected_action: str
    files: dict[str, str] = field(default_factory=dict)
    traceback_template: str | None = None
    environment: dict[str, Any] | None = None
    installed: dict[str, str | None] | None = None
    include_understanding: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "description": self.description,
            "expected_kind": self.expected_kind,
            "expected_action": self.expected_action,
            "has_traceback": self.traceback_template is not None,
            "include_understanding": self.include_understanding,
            "environment": self.environment is not None,
        }


def _env(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "python_executable": "benchmark://python",
        "python_version": "3.10.12",
        "platform": "Benchmark",
        "packages": {},
        "torch": {"installed": False, "version": None, "cuda_available": None,
                  "cuda_version": None, "gpu_count": None, "error": None},
        "nvidia_smi": {"available": False, "summary": ""},
        "issues": [],
    }
    base.update(overrides)
    return base


TASKS: tuple[BenchTask, ...] = (
    BenchTask(
        id="missing_declared_dependency",
        category="dependency",
        description="A declared package is not installed in the selected interpreter.",
        expected_kind="module_declared_missing",
        expected_action="install_package",
        files={
            "requirements.txt": "numpy>=1.20\n",
            "train.py": "import numpy\n\nprint(numpy.__version__)\n",
        },
        traceback_template=(
            'Traceback (most recent call last):\n'
            '  File "{project}/train.py", line 1, in <module>\n'
            "    import numpy\n"
            "ModuleNotFoundError: No module named 'numpy'\n"
        ),
        environment=_env(),
        installed={"numpy": None},
    ),
    BenchTask(
        id="missing_undeclared_dependency",
        category="dependency",
        description="A package is imported but no manifest declares it.",
        expected_kind="module_missing_undeclared",
        expected_action="declare_and_install",
        files={
            "requirements.txt": "numpy>=1.20\n",
            "train.py": "import bitsandbytes\n\nprint(bitsandbytes.__version__)\n",
        },
        traceback_template=(
            'Traceback (most recent call last):\n'
            '  File "{project}/train.py", line 1, in <module>\n'
            "    import bitsandbytes\n"
            "ModuleNotFoundError: No module named 'bitsandbytes'\n"
        ),
        environment=_env(),
        installed={"numpy": "1.24.0"},
    ),
    BenchTask(
        id="version_unsatisfied",
        category="dependency",
        description="The installed version violates the declared constraint, without any traceback.",
        expected_kind="version_constraint_unsatisfied",
        expected_action="install_constraint",
        files={
            "requirements.txt": "torch==2.0.0\n",
            "train.py": "import torch\n\nprint(torch.__version__)\n",
        },
        environment=_env(),
        installed={"torch": "2.1.0"},
    ),
    BenchTask(
        id="manifest_disagreement",
        category="dependency",
        description="requirements.txt and environment.yml pin different versions.",
        expected_kind="manifest_disagreement",
        expected_action="choose_manifest",
        files={
            "requirements.txt": "numpy>=1.20\n",
            "environment.yml": "name: bench\ndependencies:\n  - numpy=1.19\n",
            "train.py": "import numpy\n",
        },
        environment=_env(),
        installed={"numpy": "1.24.0"},
    ),
    BenchTask(
        id="python_incompatible",
        category="environment",
        description="requires-python excludes the selected interpreter.",
        expected_kind="python_incompatible",
        expected_action="select_interpreter",
        files={
            "pyproject.toml": '[project]\nname = "bench"\nrequires-python = ">=3.11"\n',
            "train.py": "print('train')\n",
        },
        environment=_env(),
    ),
    BenchTask(
        id="declared_not_installed_no_traceback",
        category="dependency",
        description="A declared package is absent and no traceback was supplied yet.",
        expected_kind="declared_package_not_installed",
        expected_action="install_package",
        files={
            "requirements.txt": "scipy>=1.10\n",
            "train.py": "import scipy\n",
        },
        environment=_env(),
        installed={"scipy": None},
    ),
    BenchTask(
        id="missing_dataset",
        category="data",
        description="A configured dataset path does not exist on this machine.",
        expected_kind="dataset_missing",
        expected_action="configure_data",
        files={
            "train.py": "print('train')\n",
            "configs/base.yaml": "seed: 42\ndata_path: data/train.csv\n",
        },
        environment=_env(),
        include_understanding=True,
    ),
    BenchTask(
        id="test_assertion_failure",
        category="source",
        description="A unit test fails against the implementation.",
        expected_kind="test_assertion_failure",
        expected_action="patch_source",
        files={
            "model.py": "def add(a, b):\n    return a - b\n",
            "tests/test_model.py": (
                "from model import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
            ),
        },
        traceback_template=(
            "=================================== FAILURES ===================================\n"
            "__________________________________ test_add ___________________________________\n\n"
            "    def test_add():\n"
            ">       assert add(2, 3) == 5\n"
            "E       assert -1 == 5\n\n"
            "{project}/tests/test_model.py:5: AssertionError\n"
        ),
        environment=_env(),
    ),
    BenchTask(
        id="syntax_error",
        category="source",
        description="A module does not parse, so nothing else can run.",
        expected_kind="syntax_error",
        expected_action="patch_source",
        files={"broken.py": "def f(:\n    pass\n"},
        traceback_template=(
            'Traceback (most recent call last):\n'
            '  File "{project}/broken.py", line 1, in <module>\n'
            "    def f(:\n"
            "        ^\n"
            "SyntaxError: invalid syntax\n"
        ),
        environment=_env(),
    ),
    BenchTask(
        id="cuda_tag_mismatch",
        category="gpu",
        description="The project declares a CUDA wheel newer than the installed driver supports.",
        expected_kind="cuda_driver_mismatch",
        expected_action="select_cuda_wheel",
        files={
            "requirements.txt": (
                "--extra-index-url https://download.pytorch.org/whl/cu121\n"
                "torch==2.1.0\n"
            ),
            "train.py": "import torch\n",
        },
        environment=_env(
            nvidia_smi={"available": True, "summary": "CUDA Version: 11.8"},
            torch={"installed": False, "version": None, "cuda_available": False,
                   "cuda_version": None, "gpu_count": 1, "error": None},
        ),
        installed={"torch": None},
    ),
    BenchTask(
        id="control_no_signal",
        category="control",
        description="A project with no diagnosable defect; the system must not invent one.",
        expected_kind="unclassified_error",
        expected_action="collect_more_evidence",
        files={"train.py": "print('train')\n"},
        traceback_template=(
            'Traceback (most recent call last):\n'
            "RuntimeError: something unrelated happened\n"
        ),
        environment=_env(),
    ),
)

TASKS_BY_ID = {task.id: task for task in TASKS}


def get_tasks(task_ids: list[str] | None = None) -> list[BenchTask]:
    if not task_ids:
        return list(TASKS)
    missing = [item for item in task_ids if item not in TASKS_BY_ID]
    if missing:
        raise KeyError(f"Unknown benchmark task(s): {', '.join(missing)}")
    return [TASKS_BY_ID[item] for item in task_ids]


def materialize_task(task: BenchTask, workdir: str | Path) -> Path:
    """Write a task's repository into ``workdir`` and return its root."""
    root = Path(workdir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Benchmark work directory is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in task.files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def task_traceback(task: BenchTask, project_root: str | Path) -> str | None:
    if not task.traceback_template:
        return None
    flattened = str(Path(project_root).resolve()).replace("\\", "/")
    return task.traceback_template.format(project=flattened)
