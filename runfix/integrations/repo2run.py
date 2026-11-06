from __future__ import annotations
import os, re, shutil
from pathlib import Path
from ..common import run, stamp

def inspect(path):
    p=Path(path).expanduser()
    entry=p/"build_agent"/"main.py"
    return {"available":entry.is_file(),"path":str(p.resolve()),
      "entrypoint":str(entry.resolve()) if entry.is_file() else None,
      "platform_note":"Upstream Repo2Run uses Linux shell commands; use Linux/WSL2, not native Windows Python."}

def identity(project: Path):
    r=run(["git","-C",str(project),"remote","get-url","origin"],timeout=8)
    s=run(["git","-C",str(project),"rev-parse","HEAD"],timeout=8)
    if r["returncode"] or s["returncode"]: return None
    m=re.search(r"github\.com[:/]([^/]+)/([^/.]+)(?:\.git)?$",r["stdout"].strip())
    return (m.group(1)+"/"+m.group(2),s["stdout"].strip()) if m else None

def build(project_path, repo2run_path, llm_model, confirmed=False, timeout=7200):
    if not confirmed: return {"started":False,"reason":"Explicit --confirm-external-build is required."}
    project=Path(project_path).expanduser().resolve(); repo=Path(repo2run_path).expanduser()
    info=inspect(repo)
    if not info["available"]: return {"started":False,"reason":"Repo2Run build_agent/main.py not found."}
    ident=identity(project)
    if not ident: return {"started":False,"reason":"A GitHub origin remote and current commit SHA are required."}
    name,sha=ident; workspace=project/".runfix"/"repo2run_runs"/stamp(); workspace.mkdir(parents=True,exist_ok=True)
    entry=repo/"build_agent"/"main.py"
    if os.name=="nt":
        wsl=shutil.which("wsl.exe") or shutil.which("wsl")
        if not wsl: return {"started":False,"reason":"WSL2 was not found; Repo2Run requires Linux/WSL2."}
        e=run([wsl,"wslpath","-a",str(entry.resolve())],timeout=10)
        w=run([wsl,"wslpath","-a",str(workspace.resolve())],timeout=10)
        if e["returncode"] or w["returncode"]: return {"started":False,"reason":"Could not convert paths using wslpath."}
        cmd=[wsl,"--exec","python3",e["stdout"].strip(),"--full_name",name,"--sha",sha,
             "--root_path",w["stdout"].strip(),"--llm",llm_model]
    else:
        py=shutil.which("python3") or shutil.which("python")
        if not py: return {"started":False,"reason":"python3 not found."}
        cmd=[py,str(entry),"--full_name",name,"--sha",sha,"--root_path",str(workspace),"--llm",llm_model]
    result=run(cmd,cwd=repo,timeout=timeout)
    return {"started":True,"github_repository":name,"commit":sha,"workspace":str(workspace),"command":cmd,**result}
