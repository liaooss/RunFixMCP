# RunFix Agent Design (Agent subsystem)

## Agent definition

RunFix uses a plan–act–observe loop rather than a fixed button-to-function workflow.

- **Goal:** diagnose and, when explicitly authorized, repair a research-code project.
- **Planner:** an optional OpenAI-compatible LLM selects one next action from the currently available allow-listed tools. A deterministic planner is the offline fallback.
- **Offline policy (0.7.0):** the fallback in `runfix/agent/policy.py` reads the observations — failure category, patch state, previous analysis, last verification — and stops with a machine-readable reason (`needs_install_permission`, `needs_configuration_change`, `needs_write_permission_or_patch`, `no_evidence_backed_repair`, `verified_baseline`, …) instead of walking a fixed tool list.
- **Tools:** scan, environment probe, static validation, pytest execution, explicit user command, traceback/source analysis, patch validation, patch application and post-patch verification.
- **Memory:** in-run observations, tool counts, decision rationale, strategy, results and failure log are persisted to `agent_state.json`.
- **Feedback:** after a patch is applied, the agent runs the authorized test suite or user command again and reports the observed result.
- **Termination:** planner finish, no authorized actions left, or max-step limit — each recorded as a distinct `stop_reason`.

## Permission tiers

| Capability | Default | Permission |
|---|---|---|
| Read project files and scan | Allowed | None |
| Probe selected Python environment | Allowed | None |
| Static validation | Allowed | None |
| Execute repository tests | Disabled | `--allow-project-code` |
| Execute user-supplied command | Disabled | `--allow-project-code` plus `--command` |
| Apply a generated patch | Disabled | `--allow-write` and `--allow-project-code` |
| Build an isolated environment | Disabled | `--allow-install` **and** `--allow-project-code` (installation alone is refused so the agent can re-test afterwards); Conda `environment.yml`, pip `requirements.txt`, or `pyproject.toml` under `.runfix/environments` |
| Change system drivers / global PATH | Not implemented | Never automatic |

## Why it is an Agent

The planner is not allowed to execute arbitrary code. It receives a changing list of valid next actions, chooses a tool, observes the result, updates its state, and chooses again. Tool execution is separate from planning and every action is auditable.

## Current limitations

- LLM planning and patch generation require a configured OpenAI-compatible API.
- **The offline policy is adaptive but not generative.** It now re-plans from the observed failure category and stops with an explicit reason (fixed in 0.7.0), but it still cannot author a patch: patch text comes only from the LLM planner.
- Optional dependency setup supports Conda `environment.yml`, pip `requirements.txt`, and `pyproject.toml`; it creates a manifest-specific environment beneath `.runfix/environments` after explicit permission. Driver changes are not supported.
- Multi-round patch refinement is bounded to at most two guarded patch-and-verify rounds.
- Running pytest executes repository code; users must only authorize trusted repositories.
- Passing tests does not prove scientific correctness or exact reproduction of a paper.
