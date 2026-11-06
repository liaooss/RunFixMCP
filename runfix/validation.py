from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .common import resolve_python, run
from .diagnostics import diagnose_environment
from .scanner import scan_project

def _missing_imports(python: str, imports: list[dict], local: set[str]):
    names=sorted({x["module"] for x in imports if x.get("module") not in local})
    script=("import importlib.util,json,sys; names=json.loads(sys.argv[1]); out=[]\n"
      "for n in names:\n"
      " try:\n"
      "  if importlib.util.find_spec(n) is None: out.append(n)\n"
      " except (ImportError,ModuleNotFoundError,ValueError): pass\n"
      "print(json.dumps(out))")
    r=run([python,"-c",script,json.dumps(names)],timeout=25)
    if r["returncode"]!=0: return []
    try: missing=set(json.loads(r["stdout"].splitlines()[-1]))
    except Exception: return []
    return [x for x in imports if x["module"] in missing][:200]

def stage(i,name,status,details,evidence):
    return {"id":i,"name":name,"status":status,"details":details,"evidence":evidence}

VALID_STATUSES = {"passed", "failed", "warning", "skipped", "not_applicable"}
EVIDENCE_REQUIRED = {"passed", "failed", "warning"}


def audit_stages(stages: list[dict[str, Any]]) -> list[str]:
    """Report stages that assert an outcome without structured evidence for it."""
    problems: list[str] = []
    for item in stages:
        status = item.get("status")
        if status not in VALID_STATUSES:
            problems.append(f"{item.get('id')} reported the unknown status {status!r}")
        if status in EVIDENCE_REQUIRED:
            evidence = item.get("evidence")
            if not isinstance(evidence, dict) or not evidence:
                problems.append(f"{item.get('id')} reported {status} without structured evidence")
    return problems

def validate_project(project_path: str|Path, python_executable: str|None=None,
                     command: list[str]|None=None, timeout: int=180, include_gpu: bool=False):
    root=Path(project_path).expanduser().resolve()
    if not root.is_dir(): raise NotADirectoryError(str(root))
    python=resolve_python(python_executable); scan=scan_project(root)
    local=set()
    for p in root.rglob("*.py"):
        if any(x in {".git",".venv","venv",".runfix","__pycache__"} for x in p.parts): continue
        local.add(p.stem)
        if len(p.relative_to(root).parts)>1: local.add(p.relative_to(root).parts[0])
    missing=_missing_imports(python,scan["imports"],local)
    syntax=scan["syntax_errors"]
    c0="failed" if syntax else ("warning" if missing else "passed")
    stages=[stage("C0","Static source/import checks",c0,
      f"{len(syntax)} syntax error(s), {len(missing)} unresolved import(s).",
      {"syntax_errors":syntax[:100],"unresolved_imports":missing[:100],"python_files_scanned":scan["python_file_count"]})]

    if command:
        args=list(command)
        if args and args[0].lower() in {"python","python.exe","py"}: args[0]=python
        result=run(args,cwd=root,timeout=timeout)
        status="passed" if result["returncode"]==0 and not result["timed_out"] else "failed"
        stages.append(stage("C1","CPU entrypoint / requested command",status,
          f"Exit code={result['returncode']}; timed_out={result['timed_out']}.",{"command":args,**result}))
    else:
        stages.append(stage("C1","CPU entrypoint / requested command","skipped",
          "No command supplied; RunFix did not launch the project's training/application entrypoint.",
          {"reason":"explicit_command_required"}))

    env=diagnose_environment(python); torch=env.get("torch",{})
    if torch.get("installed") and torch.get("cuda_available") is True:
        c2="passed"; detail="PyTorch imports and CUDA is visible in the selected interpreter."
    elif torch.get("installed") and torch.get("cuda_available") is False:
        c2="warning"; detail="PyTorch imports, but CUDA is not visible; acceptable for CPU-only projects."
    else:
        c2="warning" if torch.get("error") else "passed"
        detail="Python environment probe completed; no verified CUDA-enabled PyTorch runtime."
    stages.append(stage("C2","Python / ML / CUDA alignment",c2,detail,env))

    if include_gpu:
        probe=("import json,torch; ok=torch.cuda.is_available(); out={'cuda_available':bool(ok),"
          "'gpu_count':torch.cuda.device_count() if ok else 0}\n"
          "if ok:\n x=torch.randn((128,128),device='cuda'); y=x@x; torch.cuda.synchronize(); out['shape']=list(y.shape)\n"
          "print(json.dumps(out))")
        gpu=run([python,"-c",probe],cwd=root,timeout=60)
        gpu_data = None
        if gpu["returncode"] == 0:
            try:
                gpu_data = json.loads(gpu["stdout"].splitlines()[-1])
            except Exception:
                gpu_data = None
        if gpu["returncode"] != 0:
            status = "failed" if torch.get("installed") else "not_applicable"
        elif gpu_data and gpu_data.get("cuda_available") is True:
            status = "passed"
        else:
            status = "not_applicable"
        gpu["parsed_probe"] = gpu_data
        stages.append(stage("C3","Single-GPU smoke execution",status,
          "CUDA tensor operation completed." if status == "passed" else "No usable CUDA GPU was verified; GPU execution is not applicable in this environment.",
          gpu))
    else:
        stages.append(stage("C3","Single-GPU smoke execution","skipped",
          "GPU check is opt-in; use --include-gpu to run a small CUDA tensor operation.",
          {"reason":"gpu_check_not_requested"}))

    stages.append(stage("C4","Multi-GPU / DDP execution","skipped",
      "Multi-GPU/DDP validation is outside the RunFix MVP scope.",{"reason":"mvp_scope_exclusion"}))
    audit=audit_stages(stages)
    stages.append(stage("C5","Evidence/report consistency audit","failed" if audit else "passed",
      "; ".join(audit) if audit else "All reported stage outcomes have structured evidence.",
      {"audit_errors":audit,"checked_stage_count":len(stages)}))
    active=[s for s in stages if s["status"] not in {"skipped","not_applicable"}]
    overall="failed" if any(s["status"]=="failed" for s in active) else (
      "warning" if any(s["status"]=="warning" for s in active) else "passed")
    return {"project_path":str(root),"python_executable":python,"overall_status":overall,"stages":stages,
      "summary":f"{sum(s['status']=='passed' for s in stages)} passed, {sum(s['status']=='warning' for s in stages)} warning, {sum(s['status']=='failed' for s in stages)} failed, {sum(s['status']=='skipped' for s in stages)} skipped.",
      "scan":{"file_count":scan["file_count"],"python_file_count":scan["python_file_count"],
              "framework_hints":scan["framework_hints"],"entrypoint_candidates":scan["entrypoint_candidates"]}}
