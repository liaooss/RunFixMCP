from runfix.validation import validate_project

def test_no_entrypoint_runs_by_default(tmp_path):
    (tmp_path/"main.py").write_text("raise RuntimeError('must not execute')\n")
    r=validate_project(tmp_path)
    assert next(s for s in r["stages"] if s["id"]=="C1")["status"]=="skipped"
    assert next(s for s in r["stages"] if s["id"]=="C5")
