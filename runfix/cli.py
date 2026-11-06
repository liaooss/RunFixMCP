from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from . import __version__
from .common import project_dir, save_report
from .scanner import scan_project
from .diagnostics import diagnose_environment
from .analyzer import analyze_error
from .validation import validate_project
from .patches import validate_patch, apply_patch
from .integrations.env_doctor import inspect as inspect_envdoctor
from .integrations.repo2run import inspect as inspect_repo2run, build as build_repo2run
from .integrations.researchenvbench import inspect as inspect_bench, export_report

def parser():
    p=argparse.ArgumentParser(prog="runfix",description="Research-code runtime diagnosis and safe repair.")
    p.add_argument("--version",action="version",version=f"RunFix {__version__}")
    sub=p.add_subparsers(dest="action",required=True)
    x=sub.add_parser("scan"); x.add_argument("project")
    x=sub.add_parser("diagnose"); x.add_argument("project"); x.add_argument("--python",dest="py")
    x=sub.add_parser("validate"); x.add_argument("project"); x.add_argument("--python",dest="py")
    x.add_argument("--timeout",type=int,default=180); x.add_argument("--include-gpu",action="store_true")
    x.add_argument("--command",nargs=argparse.REMAINDER)
    x=sub.add_parser("analyze"); x.add_argument("project")
    g=x.add_mutually_exclusive_group(required=True); g.add_argument("--traceback-file"); g.add_argument("--traceback")
    x.add_argument("--use-llm",action="store_true")
    for name in ("patch-check","patch-apply"):
        x=sub.add_parser(name); x.add_argument("project"); x.add_argument("patch_file")
        if name=="patch-apply": x.add_argument("--confirm",action="store_true")
    x=sub.add_parser("patch-recover",help="Recover patch transactions a previous process left half-applied")
    x.add_argument("project")
    x=sub.add_parser("integrations"); x.add_argument("--python",dest="py",default=sys.executable)
    x.add_argument("--repo2run-path"); x.add_argument("--researchenvbench-path")
    x=sub.add_parser("repo2run-build"); x.add_argument("project"); x.add_argument("--repo2run-path",required=True)
    x.add_argument("--llm-model",default=os.getenv("RUNFIX_LLM_MODEL","gpt-4o-2024-05-13"))
    x.add_argument("--confirm-external-build",action="store_true"); x.add_argument("--timeout",type=int,default=7200)
    x=sub.add_parser("agent",help="Run the autonomous research-code agent loop")
    x.add_argument("project"); x.add_argument("--goal",default="Diagnose and repair this research project so it can run.")
    x.add_argument("--python",dest="py"); x.add_argument("--max-steps",type=int,default=12)
    x.add_argument("--timeout",type=int,default=300); x.add_argument("--allow-project-code",action="store_true")
    x.add_argument("--allow-write",action="store_true"); x.add_argument("--use-llm",action="store_true")
    x.add_argument("--allow-install",action="store_true",help="Allow requirements.txt installation into a project-local venv")
    x.add_argument("--command",nargs=argparse.REMAINDER)
    x=sub.add_parser("web"); x.add_argument("--host",default="127.0.0.1"); x.add_argument("--port",type=int,default=8765)
    x=sub.add_parser("mcp",help="Start the RunFix MCP server over stdio")
    x.add_argument("--allowed-root",action="append",default=[],help="Allowed project root; repeat to add more.")
    x.add_argument("--enable-execution",action="store_true",help="Allow project test/command execution after per-call confirmation.")
    x.add_argument("--enable-write",action="store_true",help="Allow patch application after per-call confirmation.")
    x.add_argument("--enable-install",action="store_true",help="Allow project-local dependency installation after per-call confirmation.")
    x.add_argument("--transport",choices=["stdio"],default="stdio")
    return p

def main(argv=None):
    a=parser().parse_args(argv)
    try:
        if a.action=="scan":
            result=scan_project(project_dir(a.project)); print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
        if a.action=="diagnose":
            root=project_dir(a.project); scan=scan_project(root); env=diagnose_environment(a.py)
            report={"project_path":str(root),"scan":scan,"diagnosis":env,"summary":"Project scan and environment probe completed.",
                    "overall_status":"warning" if env.get("issues") else "passed"}
            out=save_report(root,report,env.get("nvidia_smi",{}).get("summary","")); report["report_dir"]=str(out)
            print(json.dumps(report,ensure_ascii=False,indent=2)); return 0
        if a.action=="validate":
            root=project_dir(a.project); cmd=a.command or None
            if cmd and cmd[0]=="--": cmd=cmd[1:]
            result=validate_project(root,a.py,cmd,a.timeout,a.include_gpu)
            result["researchenvbench_export"]=export_report(result)
            out=save_report(root,result); result["report_dir"]=str(out)
            print(json.dumps(result,ensure_ascii=False,indent=2)); return 1 if result["overall_status"]=="failed" else 0
        if a.action=="analyze":
            root=project_dir(a.project)
            text=Path(a.traceback_file).read_text(encoding="utf-8",errors="replace") if a.traceback_file else a.traceback
            result=analyze_error(root,text,a.use_llm)
            report={"project_path":str(root),"analysis":result,"summary":result["root_cause"],"overall_status":"diagnosed"}
            out=save_report(root,report,text); result["report_dir"]=str(out)
            print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
        if a.action in {"patch-check","patch-apply"}:
            root=project_dir(a.project); text=Path(a.patch_file).read_text(encoding="utf-8")
            result=validate_patch(root,text) if a.action=="patch-check" else apply_patch(root,text,a.confirm)
            print(json.dumps(result,ensure_ascii=False,indent=2)); return 0 if result.get("valid",result.get("applied",False)) else 1
        if a.action=="patch-recover":
            from .patches import recover_interrupted_transactions
            result=recover_interrupted_transactions(project_dir(a.project))
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 1 if result.get("needs_review") else 0
        if a.action=="integrations":
            result={"env_doctor":inspect_envdoctor(a.py),
              "repo2run":inspect_repo2run(a.repo2run_path) if a.repo2run_path else {"available":False,"hint":"Pass --repo2run-path."},
              "researchenvbench":inspect_bench(a.researchenvbench_path) if a.researchenvbench_path else {"available":False,"hint":"Pass --researchenvbench-path."}}
            print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
        if a.action=="repo2run-build":
            result=build_repo2run(a.project,a.repo2run_path,a.llm_model,a.confirm_external_build,a.timeout)
            print(json.dumps(result,ensure_ascii=False,indent=2)); return 0 if result.get("returncode")==0 else 1
        if a.action=="agent":
            from .agent.core import run_agent
            root=project_dir(a.project); cmd=a.command or None
            if cmd and cmd[0]=="--": cmd=cmd[1:]
            result=run_agent(root,a.goal,a.py,a.max_steps,a.allow_project_code,a.allow_write,a.use_llm,
                             cmd,a.timeout,a.allow_install)
            print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
            return 1 if result["outcome"] in {"diagnosed","patched_not_verified"} else 0
        if a.action=="web":
            from .web.server import serve
            serve(a.host,a.port); return 0
        if a.action=="mcp":
            from .mcp.server import serve
            serve(a.allowed_root or None,a.enable_execution,a.enable_write,a.enable_install,a.transport)
            return 0
    except Exception as e:
        print(f"RunFix error: {type(e).__name__}: {e}",file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
