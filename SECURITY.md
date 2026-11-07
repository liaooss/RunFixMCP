# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.0.x | yes |
| 0.9.x and earlier | no (upgrade to 1.0.x) |

## Reporting a vulnerability

**Do not open a public issue.** Open a private security advisory on the repository
(<https://github.com/liaooss/RunFixMCP/security/advisories/new>) or contact the maintainer
through GitHub. Please include:

- the RunFix version, OS and Python version;
- the exact command or MCP tool call;
- a minimal reproduction, if one can be shared safely;
- the impact you believe it has.

You can expect an acknowledgement within a few days. This is a single-maintainer project without
a paid on-call rotation — please plan accordingly.

## What RunFix is designed to defend against

RunFix executes code from repositories it did not write, so the threat model assumes the
repository, its README, its logs and its code comments are **untrusted data**.

| Threat | Mitigation |
| --- | --- |
| Prompt injection through README / logs / source comments | Repository content is labelled untrusted in the LLM system prompts and in the MCP instructions; the planner cannot invent tools |
| Path traversal out of the project | Patch paths, task IDs, run IDs and source reads are normalised and resolved, then checked to stay inside the project |
| Symlink escape | Every path segment of a patch target is checked; symlinked `.runfix` directories are refused |
| Overwriting user edits | Rollback refuses when a file no longer matches the recorded post-patch hash |
| Destroying `.git` | Patches to `.git` and `.runfix` are rejected; binary patches and symlink/submodule modes are rejected |
| Unauthorised execution or installation | Three layers: server-side capability flag, per-call confirmation, optional one-shot approval |
| Silent privilege escalation on resume | A resumed run reuses the permissions recorded at start |
| Tampering with the record | Privileged actions are appended to a SHA-256 hash chain; `runfix audit --verify` detects edits, removals and reordering |
| Deleting a non-RunFix environment | `build_isolated_environment` only deletes directories it can prove it created |
| Secret leakage into reports | API keys and tokens are redacted before reports, logs, audit details and LLM requests |

## Known limitations (not vulnerabilities)

- **The MCP server and the web console bind to loopback only.** They are local operator tools,
  not network services. Never port-forward them or expose them behind a proxy.
- **The web console has no authentication.** Anyone with local access to the port can act with
  the permissions the server was started with.
- **Audit actor names are self-reported** (`cli`, `mcp`, `runfix.patches`), not authenticated
  identities.
- **Running project code is inherently unsafe.** `--allow-project-code` and
  `--enable-execution` exist for a reason; only authorize repositories you trust.
- **Docker/WSL execution isolates the process, not the data.** A container still mounts the
  project read-write at `/workspace`.
- **The LLM is optional and off by default.** Enabling it sends tracebacks and source excerpts
  to the configured endpoint; secrets are redacted but source text is not.
