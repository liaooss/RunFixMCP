from runfix.analyzer import analyze_error

def test_missing_module(tmp_path):
    (tmp_path/"main.py").write_text("import torch\n")
    r=analyze_error(tmp_path,"ModuleNotFoundError: No module named 'torch'")
    assert r["category"]=="missing_dependency_or_import_path"

def test_tensor_shape(tmp_path):
    r=analyze_error(tmp_path,"RuntimeError: mat1 and mat2 shapes cannot be multiplied")
    assert r["category"]=="tensor_shape_mismatch"
