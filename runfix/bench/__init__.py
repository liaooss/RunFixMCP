"""Reproducible benchmark and ablation harness for RunFix's diagnosis."""

from .suite import SUITE, SUITE_DESCRIPTION, TASKS, BenchTask, get_tasks, materialize_task, task_traceback
from .runner import ABLATIONS, aggregate, run_ablations, run_suite, run_task
from .report import NOT_MEASURED, render_ablation_markdown, render_suite_markdown, write_benchmark_report

__all__ = [
    "SUITE",
    "SUITE_DESCRIPTION",
    "TASKS",
    "BenchTask",
    "get_tasks",
    "materialize_task",
    "task_traceback",
    "ABLATIONS",
    "aggregate",
    "run_task",
    "run_suite",
    "run_ablations",
    "NOT_MEASURED",
    "render_suite_markdown",
    "render_ablation_markdown",
    "write_benchmark_report",
]
