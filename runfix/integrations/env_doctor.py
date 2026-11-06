from ..diagnostics import find_env_doctor
from ..common import run

def inspect(python: str):
    cli=find_env_doctor(python)
    if not cli: return {"available":False,"install_hint":f'"{python}" -m pip install env-doctor'}
    r=run([cli,"--version"],timeout=10)
    return {"available":True,"cli":cli,"version_output":r["stdout"] or r["stderr"]}
