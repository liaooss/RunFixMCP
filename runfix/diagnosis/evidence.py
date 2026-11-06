"""A small, serialisable evidence graph that links a failure to its environment.

Nodes carry the fact; edges carry the relation and the evidence for it. The graph is what
lets a root-cause candidate cite *why* it is ranked where it is, instead of asserting a
diagnosis.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..diagnostics import canonical_distribution, diagnose_environment, probe_package_versions
from ..scanner import scan_project
from ..analyzer import analyze_error
from ..test_selection import select_tests
from .dependencies import declared_requirements, python_requirement as project_python_requirement

MAX_NODES = 800


class EvidenceGraph:
    """Add-only graph with deterministic identifiers."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []

    def node(self, kind: str, label: str, **attributes: Any) -> str:
        node_id = f"{kind}:{label}"
        if node_id not in self.nodes:
            if len(self.nodes) >= MAX_NODES:
                return node_id
            self.nodes[node_id] = {"id": node_id, "kind": kind, "label": label, "attributes": attributes}
        else:
            self.nodes[node_id]["attributes"].update(attributes)
        return node_id

    def edge(self, source: str, target: str, relation: str, **attributes: Any) -> None:
        if source not in self.nodes or target not in self.nodes:
            return
        self.edges.append({"source": source, "target": target, "relation": relation, "attributes": attributes})

    def to_dict(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for node in self.nodes.values():
            kinds[node["kind"]] = kinds.get(node["kind"], 0) + 1
        relations: dict[str, int] = {}
        for edge in self.edges:
            relations[edge["relation"]] = relations.get(edge["relation"], 0) + 1
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "node_kinds": kinds,
            "relations": relations,
            "nodes": list(self.nodes.values()),
            "edges": self.edges,
        }


def _module_matches(module: str, package: str) -> bool:
    module_key = canonical_distribution(module)
    package_key = canonical_distribution(package)
    return module_key == package_key or module_key.startswith(package_key + "-") or package_key.startswith(module_key + "-")


def build_evidence_graph(project_path: str | Path,
                         traceback_text: str | None = None,
                         python_executable: str | None = None,
                         include_probe: bool = True,
                         include_tests: bool = True) -> dict[str, Any]:
    """Assemble nodes and edges for one project, optionally around a concrete failure."""
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    scan = scan_project(root)
    declared = declared_requirements(root)
    python_requirement = project_python_requirement(root)
    graph = EvidenceGraph()

    project_node = graph.node("project", "project", path=str(root),
                              languages=scan.get("languages", []),
                              python_files=scan.get("python_file_count"))
    git = scan.get("git") or {}
    if git.get("commit"):
        graph.node("git_revision", str(git.get("commit"))[:12], branch=git.get("branch"),
                   origin=git.get("origin"))
        graph.edge(project_node, f"git_revision:{str(git.get('commit'))[:12]}", "has_revision")

    environment: dict[str, Any] = {}
    if include_probe:
        environment = diagnose_environment(python_executable, use_env_doctor=False)
        env_id = graph.node(
            "environment", "interpreter",
            executable=environment.get("python_executable"),
            version=environment.get("python_version"),
            platform=environment.get("platform"),
            python_requirement=python_requirement,
        )
        graph.edge(project_node, env_id, "runs_on")
        torch = environment.get("torch") or {}
        if torch.get("installed"):
            torch_id = graph.node("framework", "torch", version=torch.get("version"),
                                  cuda=torch.get("cuda_available"), cuda_version=torch.get("cuda_version"),
                                  gpus=torch.get("gpu_count"))
            graph.edge(env_id, torch_id, "provides")
        elif torch.get("error"):
            graph.node("framework", "torch", installed=False, error=torch.get("error"))

    # Declared dependencies and their installed counterparts.
    package_names = [item["name"] for item in declared]
    installed = probe_package_versions(python_executable, package_names) if (include_probe and package_names) else {}
    dependency_ids: dict[str, str] = {}
    for item in declared:
        canonical = item["canonical"]
        node_id = graph.node(
            "dependency", f"{canonical}{item['specifier']}",
            package=canonical, specifier=item["specifier"], manifest=item["manifest"], raw=item["raw"],
        )
        graph.edge(project_node, node_id, "declares")
        dependency_ids.setdefault(canonical, node_id)

    for canonical in sorted({item["canonical"] for item in declared}):
        version = installed.get(canonical)
        installed_id = graph.node("installed_package", canonical, version=version)
        graph.edge(dependency_ids[canonical], installed_id, "resolved_to", version=version)

    # Imports actually present in the source tree.
    import_nodes: dict[str, str] = {}
    for item in scan.get("imports", [])[:1500]:
        module = item.get("module")
        if not module:
            continue
        if module not in import_nodes:
            import_nodes[module] = graph.node("module", module)
        file_id = graph.node("source_file", item.get("file", ""))
        graph.edge(file_id, import_nodes[module], "imports", line=item.get("line"))
        for canonical, dependency_id in dependency_ids.items():
            if _module_matches(module, canonical):
                graph.edge(import_nodes[module], dependency_id, "declared_as")

    analysis = None
    if traceback_text:
        analysis = analyze_error(root, traceback_text, use_llm=False)
        exception_id = graph.node(
            "exception", str(analysis.get("exception")),
            category=analysis.get("category"), confidence=analysis.get("confidence"),
            message=str(analysis.get("message"))[:500],
        )
        graph.edge(project_node, exception_id, "failed_with")
        for frame in (analysis.get("evidence") or {}).get("source_context", []):
            file_id = graph.node("source_file", frame["file"])
            graph.edge(exception_id, file_id, "raised_in", line=frame.get("line"),
                       function=frame.get("function"))
            for module, module_id in import_nodes.items():
                if frame["file"] and module and module in (frame.get("excerpt") or ""):
                    graph.edge(file_id, module_id, "references")

    if include_tests:
        selection = select_tests(root, traceback_text or "")
        for candidate in selection.get("candidates", [])[:12]:
            test_id = graph.node("test_file", candidate["path"], score=candidate["score"])
            graph.edge(project_node, test_id, "has_test")
            for evidence in candidate.get("evidence", []):
                graph.edge(test_id, project_node, "test_selected_because", reason=evidence[:200])
            for source in selection.get("source_files", []):
                file_id = graph.node("source_file", source)
                graph.edge(file_id, test_id, "tested_by")

    return {
        "graph": graph.to_dict(),
        "environment": environment,
        "python_requirement": python_requirement,
        "analysis": analysis,
        "installed_versions": installed,
        "declared_requirements": declared,
        "scan_summary": {
            "file_count": scan.get("file_count"),
            "python_file_count": scan.get("python_file_count"),
            "frameworks": scan.get("framework_hints"),
        },
    }


def nodes_of_kind(graph: dict[str, Any], kind: str) -> Iterable[dict[str, Any]]:
    for node in graph.get("nodes", []):
        if node.get("kind") == kind:
            yield node


def edges_from(graph: dict[str, Any], node_id: str, relation: str | None = None) -> list[dict[str, Any]]:
    return [
        edge for edge in graph.get("edges", [])
        if edge.get("source") == node_id and (relation is None or edge.get("relation") == relation)
    ]
