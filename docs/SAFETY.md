# Safety model

- Scanning does not import project modules.
- Default validation does not run a training/application entrypoint.
- Explicit commands run without a shell and have a timeout.
- Repo2Run requires `--confirm-external-build`.
- Web UI binds only to loopback and has no dedicated patch-apply endpoint; a patch can only reach disk through an explicitly authorized agent run from that page.
- Patch paths must be project-relative; `.git`, binary patches, traversal and unsupported extensions are rejected.
- Existing files are backed up before patch application.
- LLM calls are opt-in; credentials are read from environment variables.
- Dependency installation is disabled by default. With explicit execution/install authorization, RunFix creates a separate environment under `<project>/.runfix/environments` from a supported Conda or pip manifest; the selected/base interpreter and system packages are not modified.
- RunFix never changes NVIDIA drivers, elevates privileges or modifies global PATH.

- Background task commands are explicit argv lists (`shell=False`), restricted to allowed project roots, and require execution authorization. Task IDs are validated; task storage rejects symlink escapes; task logs are redacted when read and after observed completion.

- Patch transactions store pre-patch snapshots and before/after hashes under `.runfix/patch_transactions`. Rollback is refused if any target file changed independently after the patch, preventing RunFix from overwriting later user edits.
- Patch application and rollback are serialised per project by a thread lock plus an OS file lock, so two RunFix processes cannot interleave on the same files.
- A transaction interrupted mid-apply (`status: "applying"`) is never silently trusted: RunFix restores it only when the on-disk hash still matches the recorded pre- or post-patch hash, and otherwise marks it `interrupted_needs_review` and leaves the file untouched. `runfix patch-recover` runs the same check on demand and exits non-zero when a transaction needs a human decision.
- Pruning removes only transactions that can no longer be rolled back (`rolled_back`, `apply_failed`); applied transactions are kept so rollback stays possible.
- Python patch syntax is checked in a temporary isolated tree before project files are changed. This is a syntax check, not a proof of runtime correctness.
- If post-patch verification fails, the Agent attempts rollback only for its own recorded transaction. If the transaction has conflicts, the Agent reports that rollback was not safe.
