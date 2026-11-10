# Documentation index

Start with the README ([English](../README.md) · [中文](../README.zh-CN.md)). This page maps
the rest.

## For first-time readers

| Document | Read it if you want to… |
| --- | --- |
| [PROJECT_BRIEF.zh-CN.md](PROJECT_BRIEF.zh-CN.md) | 用中文快速了解：背景、定位、架构、版本演进、验证结果、能力边界（推荐先读） |
| [EVIDENCE_CRITERIA.md](EVIDENCE_CRITERIA.md) · [中文](EVIDENCE_CRITERIA.zh-CN.md) | see **what each of the six steps actually checks** and what the reproduction verdict is based on |
| [ARCHITECTURE.md](ARCHITECTURE.md) | see the layers, the data flow and how the pieces connect |
| [CURRENT_STATE_AUDIT.md](CURRENT_STATE_AUDIT.md) | read the evidence-based audit the project was rebuilt from — every defect with a file:line reference |
| [ROADMAP.md](ROADMAP.md) | see what is done and what is explicitly still open |

## Using RunFix

| Document | Contents |
| --- | --- |
| [DIAGNOSIS.md](DIAGNOSIS.md) | evidence graph, dependency-constraint analysis, root-cause kinds and the confidence formula |
| [REPRODUCTION.md](REPRODUCTION.md) | the seven-rung evidence ladder, CLI usage, report layout, what it refuses to claim |
| [BENCHMARK.md](BENCHMARK.md) | the `synthetic-v1` suite, metric definitions, ablation results, unmeasured metrics |
| [MCP_SERVER.md](MCP_SERVER.md) | the 23 tools, their gates, the smoke test, Codex configuration |
| [WEB_UI.md](WEB_UI.md) | the multi-project console, its endpoints and security posture |
| [AUDIT_LOG.md](AUDIT_LOG.md) | the hash-chained audit trail and how to verify it |
| [SAFETY.md](SAFETY.md) | permission model, rollback rules, approval semantics |

## For maintainers

| Document | Contents |
| --- | --- |
| [AGENT_DESIGN.md](AGENT_DESIGN.md) | planner, policy, tool registry, stop reasons, limits |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | setup, the "no claim without evidence" rule, PR checklist |
| [../SECURITY.md](../SECURITY.md) | threat model, reporting, known limitations |
| [UPGRADE_0.6.1.md](UPGRADE_0.6.1.md) · [UPGRADE_0.6.0.md](UPGRADE_0.6.0.md) · [UPGRADE_0.5.0.md](UPGRADE_0.5.0.md) | historical upgrade notes |
| [codex-mcp-config.example.toml](codex-mcp-config.example.toml) | a ready-to-edit MCP client configuration |

## Conventions used throughout

- **A claim without a command is not a claim.** Every documented result names the command that
  produced it.
- **"Not verified" sections are load-bearing.** They list what the project has *not* proven, with
  the reason (missing hardware, missing credentials, missing corpus).
- **Confidence is deterministic.** The formula is in DIAGNOSIS.md and can be recomputed from the
  cited evidence; it is not a model score.
- **Read-only means read-only.** Endpoints and tools documented as inspecting are verified by
  the benchmark's file-hash comparison.
