from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .brain import heuristic_next, llm_next
from .tools import ToolBox
from ..common import save_report, redact, resolve_python

def _brief(value: Any, limit: int=5000):
    try: text=json.dumps(value,ensure_ascii=False,default=str)
    except Exception: text=str(value)
    return text[:limit]

def run_agent(project_path: str|Path, goal: str="Diagnose and repair this research project so it can run.",
              python_executable: str|None=None, max_steps: int=12, allow_project_code: bool=False,
              allow_write: bool=False, use_llm: bool=False, command: list[str]|None=None,
              timeout: int=300, allow_install: bool=False, install_timeout: int=1800) -> dict[str, Any]:
    root=Path(project_path).expanduser().resolve()
    if not root.is_dir(): raise NotADirectoryError(str(root))
    if allow_write and not allow_project_code:
        raise ValueError("--allow-write requires --allow-project-code so the agent can verify changes.")
    if allow_install and not allow_project_code:
        raise ValueError("--allow-install requires --allow-project-code so the agent can diagnose and re-test after installation.")
    max_steps=max(3,min(int(max_steps),20))
    python=resolve_python(python_executable)
    options={"allow_project_code":allow_project_code,"allow_write":allow_write,
             "use_llm":use_llm,"command":command,"timeout":max(30,min(int(timeout),1800)),
             "allow_install":allow_install,"install_timeout":max(60,min(int(install_timeout),3600))}
    box=ToolBox(root,python,options)
    state={"goal":goal,"project_path":str(root),"python_executable":python,
           "observations":{},"tool_counts":{},"tool_history":[],"last_error":""}
    events=[]
    stop_reason="max_steps_reached"
    planner_mode="llm_with_heuristic_fallback" if use_llm else "heuristic_tool_agent"

    for step in range(1,max_steps+1):
        available=box.available(state)
        decision=llm_next(state,available) if use_llm else None
        if decision is None:
            decision=heuristic_next(state,available)
            decision.setdefault("planner","heuristic_fallback" if use_llm else "heuristic")
        tool=decision.get("tool")
        if tool not in available:
            decision=heuristic_next(state,available)
            tool=decision["tool"]
            decision["planner"]="safety_fallback"

        event={"step":step,"planner":decision.get("planner"),"tool":tool,
               "rationale":decision.get("rationale","")}
        if decision.get("strategy"):
            event["strategy"]=decision["strategy"]
        try:
            result=box.execute(tool,state)
            state["tool_counts"][tool]=state["tool_counts"].get(tool,0)+1

            if tool=="scan_project":
                state["observations"]["scan"]=result
            elif tool=="diagnose_environment":
                state["observations"]["environment"]=result
            elif tool=="static_validation":
                state["observations"]["static_validation"]=result
            elif tool in {"run_tests","run_user_command"}:
                state["observations"]["runtime"]=result
            elif tool=="create_isolated_environment":
                state["observations"]["environment_setup"]=result
                if result.get("created"):
                    state["python_executable"]=box.python
                    state["observations"]["environment"]=None
                    state["observations"]["previous_runtime"]=state["observations"].get("runtime")
                    state["observations"]["runtime"]=None
                    state["observations"]["analysis"]=None
            elif tool=="analyze_failure":
                if state["observations"].get("analysis"):
                    state["observations"]["previous_analysis"]=state["observations"]["analysis"]
                state["observations"]["analysis"]=result
                state["last_error"]=box.latest_log
                state["observations"]["patch_check"]=None
                state["observations"]["patch_apply"]=None
            elif tool=="validate_patch":
                state["observations"]["patch_check"]=result
            elif tool=="apply_patch":
                state["observations"]["patch_apply"]=result
                if result.get("applied"):
                    state["observations"]["post_patch_runtime"]=None
            elif tool=="verify_after_patch":
                state["observations"]["post_patch_runtime"]=result
                if result.get("rollback"):
                    state["observations"]["patch_rollback"]=result["rollback"]
                if not result.get("passed") and box.repair_round+1 < box.max_repair_rounds:
                    state["observations"]["analysis"]=None
                    state["observations"]["patch_check"]=None
                    state["observations"]["patch_apply"]=None

            event["status"]="completed"
            event["result_summary"]=_brief(result,3500)
            state["tool_history"].append({"step":step,"tool":tool,"status":"completed",
                                          "summary":_brief(result,1800)})
            if tool=="finish":
                stop_reason=decision.get("stop_reason") or "planner_finished"
                events.append(event)
                break
        except Exception as exc:
            event["status"]="error"
            event["error"]=f"{type(exc).__name__}: {redact(str(exc))}"
            state["tool_history"].append({"step":step,"tool":tool,"status":"error","summary":event["error"]})
            state["tool_counts"][tool]=state["tool_counts"].get(tool,0)+1
        events.append(event)
    else:
        stop_reason="max_steps_reached"

    runtime=state["observations"].get("post_patch_runtime") or state["observations"].get("runtime")
    analysis=state["observations"].get("analysis")
    patch=state["observations"].get("patch_apply")
    rollback=state["observations"].get("patch_rollback")
    if runtime and runtime.get("passed"):
        outcome="verified"
    elif rollback and rollback.get("rolled_back"):
        outcome="rolled_back"
    elif patch and patch.get("applied"):
        outcome="patched_not_verified"
    elif analysis:
        outcome="diagnosed"
    else:
        outcome="inspection_only"

    summary={
      "verified":"The authorized runtime check passed after the agent's execution workflow.",
      "patched_not_verified":"A patch was applied, but successful runtime verification is not established; rollback was not completed.",
      "rolled_back":"Post-patch verification failed. RunFix restored the pre-patch files from the transaction backup.",
      "diagnosed":"The project was inspected and a runtime failure was analyzed; a successful repair is not established.",
      "inspection_only":"The agent completed available inspection steps; no successful runtime check was authorized or available."
    }[outcome]
    python=box.python
    report={"project_path":str(root),"goal":goal,"python_executable":python,
      "agent_mode":planner_mode,"outcome":outcome,"stop_reason":stop_reason,
      "overall_status":"passed" if outcome=="verified" else ("warning" if outcome in {"diagnosed","patched_not_verified","rolled_back"} else "inspection_only"),
      "summary":summary,"permissions":{"allow_project_code":allow_project_code,"allow_write":allow_write,
      "use_llm":use_llm,"allow_install":allow_install,"command_supplied":bool(command)},
      "tool_history":events,"observations":state["observations"],
      "patch_transaction_id":box.patch_transaction_id,
      "patch_rollback":rollback,
      "repair_rounds_used":box.repair_attempts,
      "limitations":["The planner can only invoke allow-listed tools; arbitrary shell execution is not exposed as an agent tool.",
        "Project-code execution and file modification require explicit user authorization.",
        "A successful test suite does not prove scientific correctness or exact reproduction of a paper.",
        "Dependency installation is optional, requires explicit permission, supports Conda environment.yml, pip requirements.txt and pyproject.toml, and remains inside .runfix/environments.",
        "The agent does not modify system drivers, global PATH, or the user's existing Conda environment.",
        "Patch verification uses targeted tests when a static traceback-to-test relationship is found, then runs the full suite; otherwise it falls back to the full suite.",
        "A failed post-patch verification triggers rollback when the transaction files have not been modified independently.",
        "The offline policy re-plans from the failure category and the last verification result, but it cannot generate code patches; patch text comes from the LLM planner."]}
    log="\n\n".join(f"[step {e['step']}] {e['tool']} ({e['status']})\n{e.get('result_summary',e.get('error',''))}" for e in events)
    out=save_report(root,report,log)
    report["report_dir"]=str(out)
    (out/"agent_state.json").write_text(json.dumps({"state":state,"events":events},ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    return report
