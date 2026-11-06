from __future__ import annotations
import json, os, shutil
from pathlib import Path
from typing import Any
from .common import resolve_python, run

PROBE = """
import importlib.metadata as md, json, platform, sys
out={"python_executable":sys.executable,"python_version":platform.python_version(),
     "platform":platform.platform(),"packages":{},
     "torch":{"installed":False,"version":None,"cuda_available":None,"cuda_version":None,"gpu_count":None,"error":None}}
for n in ("numpy","scipy","pandas","sklearn","torch","tensorflow","jax","transformers"):
 try: out["packages"][n]=md.version(n)
 except md.PackageNotFoundError: out["packages"][n]=None
try:
 import torch
 out["torch"].update({"installed":True,"version":torch.__version__,
  "cuda_available":bool(torch.cuda.is_available()),"cuda_version":torch.version.cuda,
  "gpu_count":torch.cuda.device_count()})
 if torch.cuda.is_available(): out["torch"]["gpu_names"]=[torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
except Exception as e: out["torch"]["error"]=type(e).__name__+": "+str(e)
print(json.dumps(out))
"""

def _json_output(text: str):
    decoder = json.JSONDecoder()
    for i, c in enumerate(text):
        if c == "{":
            try:
                value, _ = decoder.raw_decode(text[i:])
                if isinstance(value, dict): return value
            except Exception: pass
    return None

def find_env_doctor(python: str):
    p = Path(python)
    candidates = [
        str(p.parent / ("env-doctor.exe" if os.name == "nt" else "env-doctor")),
        str(p.parent / "Scripts" / "env-doctor.exe"),
        str(p.parent / "bin" / "env-doctor"),
        shutil.which("env-doctor"),
    ]
    return next((x for x in candidates if x and Path(x).is_file()), None)

def diagnose_environment(python_executable: str | None = None, use_env_doctor: bool = True) -> dict[str, Any]:
    python = resolve_python(python_executable)
    probe = run([python, "-c", PROBE], timeout=40)
    info = _json_output(probe["stdout"]) or {"python_executable": python, "probe_error": probe["stderr"]}
    nvidia = run(["nvidia-smi"], timeout=10)
    info["nvidia_smi"] = {"available": nvidia["returncode"] == 0,
                          "summary": (nvidia["stdout"] or nvidia["stderr"])[:4000]}
    info["issues"] = []
    torch = info.get("torch", {})
    if torch.get("error"):
        info["issues"].append({"severity":"warning","category":"torch_import","message":torch["error"]})
    cli = find_env_doctor(python) if use_env_doctor else None
    if cli:
        result = run([cli, "check", "--format", "json"], timeout=50)
        info["env_doctor"] = {"available":True,"cli":cli,"returncode":result["returncode"],
            "data":_json_output(result["stdout"]),"stdout":result["stdout"][:6000],
            "stderr":result["stderr"][:2000]}
    else:
        info["env_doctor"] = {"available":False,
            "install_hint": f'"{python}" -m pip install env-doctor'}
    info["probe_returncode"] = probe["returncode"]
    return info
