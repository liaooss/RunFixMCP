# Audit trail (1.0.0)

Every privileged RunFix action is appended to a tamper-evident log inside the project:

```text
<project>/.runfix/audit/audit-YYYY-MM.jsonl
```

```powershell
runfix audit "D:\research\paper-code" --limit 20
runfix audit "D:\research\paper-code" --filter-action patch.apply
runfix audit "D:\research\paper-code" --verify
```

## What is recorded

| Action | Recorded by |
| --- | --- |
| `patch.apply` / `patch.rollback` / `patch.recover` | `runfix/patches.py` |
| `environment.build` | `runfix/environments.py` |
| `task.start` / `task.stop` | `runfix/task_manager.py` |
| `agent.run` | `runfix/agent/core.py` |
| `reproduce.run` | `runfix/repro/pipeline.py` |
| `approval.grant` / `approval.revoke` / `approval.consume` | `runfix/approvals.py` |

Each entry carries:

```json
{
  "schema": 1,
  "seq": 7,
  "ts": "2026-11-06T10:58:57.123456+00:00",
  "actor": "runfix.patches",
  "action": "patch.apply",
  "target": "model.py",
  "outcome": "applied",
  "details": {"transaction_id": "…", "paths": ["model.py"]},
  "prev_hash": "…",
  "hash": "…"
}
```

`details` values are redacted with the same rules as reports (`API_KEY=…`, bearer tokens,
`sk-…` style keys, GitHub tokens) and truncated to 4 KB.

## Why it is tamper-evident

`hash` is `sha256(canonical_json(entry without hash))`, and `prev_hash` is the previous entry's
hash (64 zeros for the first entry). Editing, reordering or deleting an entry breaks the chain:

```powershell
runfix audit "D:\research\paper-code" --verify
# {"ok": true, "checked": 3, "files": ["audit-2026-11.jsonl"], "last_hash": "…"}
```

Exit code is `0` when the chain verifies and `1` when it does not, so CI can gate on it. The
verifier reports the first failure with its file, sequence number and reason (`hash mismatch`,
`prev_hash mismatch`, `sequence gap`).

Files rotate by month; the chain continues across files because each entry stores the previous
hash regardless of which file it lives in.

## Guarantees and limits

- **Auditing never blocks work.** If the log cannot be written, RunFix prints a warning to
  stderr and continues; `audit()` returns `{"recorded": false, "error": …}`. Losing an audit
  entry must not lose a repair.
- **Ordering.** Entries are appended under a process-wide lock. Concurrent *processes* are not
  serialised by the audit log — the patch transaction lock covers the operations where order
  matters.
- **Actor names** are self-reported labels (`runfix.patches`, `mcp`, `cli`), not authenticated
  identities. The trail records what happened on this machine, not who was at the keyboard.
- **Retention** is unbounded by design; rotate or archive the directory yourself if a project
  runs for years.
