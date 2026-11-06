# Web console and multi-project management (1.0.0)

```powershell
runfix web --host 127.0.0.1 --port 8765 --state .\.runfix-web
```

The console binds to loopback only. It keeps a project registry in
`<state>/projects.json` (default `./.runfix-web/`) and reads each project's own
`.runfix/` data. **The registry stores paths only; nothing is copied into it.**

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Dashboard (projects, tasks, audit, actions) |
| GET | `/api/health` | Version and state directory |
| GET | `/api/projects` | Registered projects with a per-project summary |
| POST | `/api/projects` | Add a project (`{"path": "D:\\research\\paper"}`) |
| POST | `/api/projects/remove` | Remove a project from the registry |
| GET | `/api/tasks?project=P&limit=N` | Task records read from `<project>/.runfix/tasks` |
| GET | `/api/audit?project=P&limit=N` | Audit entries (read-only) |
| GET | `/api/reports?project=P` | Latest agent report and reproduction report |
| POST | `/api/scan` `/api/diagnose` `/api/validate` `/api/analyze` `/api/agent` | Actions on the selected project |

Per-project summary fields: `path`, `name`, `exists`, `tasks`, `last_task`,
`last_report`, `reproduction`, `audit_entries`.

**Read-only by default.** The GET endpoints only read files; they never mutate task metadata,
never run the agent and never write into the project. The POST action endpoints keep the same
permission rules as the CLI: patch application requires `allow_project_code` plus
`allow_write`, and installation requires `allow_project_code` plus `allow_install`.

## Security posture

- Loopback binding is enforced (`0.0.0.0` is rejected with an error).
- Requests are capped at 2 MB; responses are capped per endpoint (tasks 100, audit 200).
- Project paths must be existing directories; the registry stores the resolved absolute path.
- Secrets in task logs are redacted by the task manager, and audit/report details by the same
  redaction rules.
- There is no authentication: the console is a local operator tool, not a network service. Run
  it on a machine you control and never port-forward it.

## Verified behaviour (this machine, 1.0.0)

| Check | Result |
| --- | --- |
| `GET /api/health` | reports the package version and state directory |
| `POST /api/projects` | registers a project; repeating it is idempotent |
| `GET /api/projects` | returns tasks, audit count and the latest reproduction verdict |
| `GET /api/audit?project=…` | returned the 3 entries produced by `reproduce`, `agent` and `approvals` |
| `GET /api/tasks?project=…` | reads task metadata without touching it |
| `GET /` | serves the dashboard (6.2 KB) |
| `POST /api/projects` with a missing directory | `400` |
| binding to `0.0.0.0` | rejected |

These were exercised over real HTTP against a running server, not against the handler
functions directly.
