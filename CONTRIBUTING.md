# Contributing to RunFix

Thanks for taking the time to contribute. RunFix exists to make claims about research code
that someone else can check, and the same standard applies to changes to RunFix itself.

## The one rule

**No claim without evidence.** A pull request that says "improves diagnosis" without a test, a
benchmark delta or a recorded command is not reviewable and will be asked for one. This rule is
why the repository contains `docs/CURRENT_STATE_AUDIT.md` and `runfix bench`.

Concretely, a change should be able to answer:

1. What was wrong or missing? (an issue, a failing test, a benchmark miss)
2. What changed? (code, docs, version)
3. How do I reproduce your verification? (commands + observed output)
4. What did you *not* verify? (say it explicitly)

## Development setup

```powershell
git clone https://github.com/liaooss/RunFixMCP.git
cd RunFixMCP
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1      # creates .venv, installs, runs the MCP self-check
```

Manual equivalent:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,mcp]"
```

RunFix has **no runtime dependencies**; `mcp` and `pytest` are optional extras. Please keep it
that way unless there is a strong reason — it is what lets the CLI run on a bare interpreter
inside a research environment.

## Before you open a pull request

```powershell
.\.venv\Scripts\python.exe -m pytest -q                # must be 0 failures
.\.venv\Scripts\python.exe -m runfix.mcp.smoke          # real stdio handshake + gates
.\.venv\Scripts\python.exe -m runfix.mcp.smoke --full   # all 23 tools, may take a minute
.\.venv\Scripts\python.exe -m runfix bench              # diagnosis benchmark, must stay 1.0
```

If your change touches diagnosis, include the `--ablations` output too.

Two tests skip on accounts without `SeCreateSymbolicLinkPrivilege`; that is expected, not a
failure.

## Project conventions

- **Python 3.10+**, standard library first. New optional features go behind an extra or a
  runtime check, never a hard import at module load.
- **Tests live next to the behaviour they pin.** A bug fix without a regression test is
  incomplete.
- **Every privileged operation is auditable.** If you add one, call `runfix.audit.audit(...)`
  with a stable action name, and cover it in `tests/test_product.py`.
- **Read-only stays read-only.** Endpoints and tools that claim to inspect must not write. The
  benchmark verifies this by hashing every file before and after diagnosis.
- **Never widen a permission silently.** A resumed run inherits the permissions it started
  with; approvals can only narrow what the flags already allow.
- **Commit messages** follow `Area: imperative summary` in the subject and explain the *why* in
  the body. Releases bump `pyproject.toml`, `runfix/__init__.py`, `CHANGELOG.md` and
  `docs/ROADMAP.md` together.

## Documentation

Documentation is part of the deliverable, not an afterthought. If behaviour changes:

- `README.md` and `README.zh-CN.md` — user-facing description
- `docs/` — the relevant design document
- `CHANGELOG.md` — the "Fixed / Added / Changed" entry
- `docs/ROADMAP.md` — tick what is done, add what remains

Keep the honesty sections ("What RunFix does not claim", "Not measured") accurate. Removing a
limitation is welcome **only** when it has actually been verified.

## Reporting a bug

Use the bug report template. A useful report contains:

1. `runfix --version`, OS, Python version, and the output of `scripts\diagnose_windows.ps1`
2. The exact command you ran
3. The observed output and the expected output
4. Whether the affected path was inside `.runfix/` (reports, tasks, audit)

Please redact API keys and private repository contents.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).

## Licence

By contributing you agree that your contribution is licensed under the [MIT Licence](LICENSE).
