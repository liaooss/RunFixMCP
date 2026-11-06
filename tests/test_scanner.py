from runfix.scanner import scan_project

def test_scan_python_project(tmp_path):
    (tmp_path/"requirements.txt").write_text("torch\nnumpy>=1.20\n")
    (tmp_path/"train.py").write_text("import torch\n")
    r=scan_project(tmp_path)
    assert "Python" in r["languages"]
    assert "train.py" in r["entrypoint_candidates"]
    assert r["import_count"]==1

def test_scan_syntax_error(tmp_path):
    (tmp_path/"bad.py").write_text("def f(:\n pass\n")
    assert scan_project(tmp_path)["syntax_errors"]
