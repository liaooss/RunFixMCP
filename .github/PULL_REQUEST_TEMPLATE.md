# Pull request

## What this changes

<!-- One paragraph. Link the issue it closes, if any. -->

Closes #

## Why

<!-- The concrete failure this fixes, or the capability gap it fills. -->

## Evidence

- [ ] `python -m pytest -q` passes locally (paste the summary line)
- [ ] `python -m runfix.mcp.smoke` passes (protocol-level gates)
- [ ] `python -m runfix.mcp.smoke --full` passes, or explain why it was not run
- [ ] `python -m runfix bench` still reports top-1 accuracy 1.0, or the delta is explained
- [ ] A regression test covers the change (bug fixes) / a benchmark task covers it (diagnosis)

```text
# paste the observed output of the checks you ran
```

## What is **not** verified

<!--
Every PR states its boundaries. Examples: "not tested on Linux", "no Docker on this machine",
"LLM path untested, no API key". This is not a weakness — it is the project's rule.
-->

## Checklist

- [ ] `README.md` updated if user-facing behaviour changed
- [ ] The relevant `docs/*.md` updated
- [ ] `CHANGELOG.md` entry added under the current unreleased/next version
- [ ] Version bumped in `pyproject.toml` and `runfix/__init__.py` (releases only)
- [ ] No new runtime dependency added without justification
- [ ] No secrets, tokens or private repository content in the diff or screenshots
- [ ] Honesty sections ("What RunFix does not claim", "Not measured") left accurate
