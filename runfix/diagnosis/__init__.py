"""Evidence-based diagnosis: dependency analysis, evidence graph and root-cause ranking."""

from .dependencies import (
    analyse_dependencies,
    declared_requirements,
    parse_requirements,
    parse_environment_yml,
    parse_pyproject,
    python_requirement,
    satisfies,
    compare_versions,
    parse_version,
)
from .evidence import build_evidence_graph, EvidenceGraph
from .rootcause import rank_root_causes

__all__ = [
    "analyse_dependencies",
    "declared_requirements",
    "parse_requirements",
    "parse_environment_yml",
    "parse_pyproject",
    "python_requirement",
    "satisfies",
    "compare_versions",
    "parse_version",
    "build_evidence_graph",
    "EvidenceGraph",
    "rank_root_causes",
]
