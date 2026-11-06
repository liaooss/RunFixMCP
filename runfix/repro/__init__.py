"""Research-experiment reproduction: project understanding, planning, execution, reporting."""

from .understanding import understand_project
from .plan import build_environment_plan, assess_cuda_compatibility
from .metrics import extract_metrics, compare_metrics
from .runner import run_reproduction, latest_reproduction_runs
from .report import build_reproduction_report, write_reproduction_report, derive_verdict, render_reproduction_markdown
from .pipeline import reproduce

__all__ = [
    "understand_project",
    "build_environment_plan",
    "assess_cuda_compatibility",
    "extract_metrics",
    "compare_metrics",
    "run_reproduction",
    "latest_reproduction_runs",
    "build_reproduction_report",
    "write_reproduction_report",
    "derive_verdict",
    "render_reproduction_markdown",
    "reproduce",
]
