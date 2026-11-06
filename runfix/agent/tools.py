from __future__ import annotations
from pathlib import Path
from typing import Any
from ..scanner import scan_project
from ..diagnostics import diagnose_environment
from ..analyzer import analyze_error
from ..patches import validate_patch, apply_patch, rollback_patch
from ..common import resolve_python, run
from ..environments import detect_environment_manifest, build_isolated_environment
from ..test_selection import select_tests

def has_tests(root: Path) -> bool:
    ignored={".git",".venv","venv","env",".runfix","__pycache__","node_modules","site-packages"}
    try:
        for base, dirs, files in __import__("os").walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in ignored]
            if any(name.startswith("test_") and name.endswith(".py") or
                   name.endswith("_test.py") for name in files):
                return True
    except OSError:
        return False
    return False

def _pytest(root: Path, python: str, timeout: int,
            selected_tests: list[str] | None = None) -> dict[str, Any]:
    args=[python,"-m","pytest","-q","--disable-warnings"]
    if selected_tests:
        args.extend(selected_tests)
    result=run(args,cwd=root,timeout=timeout)
    return {"command":args,**result,
            "passed":result["returncode"]==0 and not result["timed_out"]}

class ToolBox:
    """Allow-listed tools. The planner cannot execute arbitrary shell or Python snippets."""
    def __init__(self, root: Path, python: str, options: dict[str, Any]):
        self.root=root
        self.python=resolve_python(python)
        self.options=options
        self.latest_log=""
        self.patch_text=""
        self.patch_check=None
        self.patch_applied=False
        self.repair_round=0
        self.max_repair_rounds=2
        self.apply_attempted_round=-1
        self.verified_round=-1
        self.environment_created=False
        self.environment_setup_attempted=False
        self.patch_transaction_id: str | None = None
        self.patch_rolled_back=False
        self.repair_attempts=0

    def available(self, state: dict[str, Any]) -> list[str]:
        obs=state.get("observations",{})
        tools=[]
        if obs.get("scan") is None: tools.append("scan_project")
        if obs.get("environment") is None: tools.append("diagnose_environment")
        if obs.get("static_validation") is None: tools.append("static_validation")

        runtime=obs.get("post_patch_runtime") or obs.get("runtime")
        runtime_failed=bool(runtime and (runtime.get("returncode") not in (0,None) or runtime.get("timed_out")))
        if obs.get("runtime") is None and self.options.get("allow_project_code"):
            if has_tests(self.root): tools.append("run_tests")
            elif self.options.get("command"): tools.append("run_user_command")

        can_analyze = (
            runtime_failed
            and obs.get("analysis") is None
            and self.repair_round + 1 < self.max_repair_rounds
            and not self.patch_applied
        )
        if can_analyze: tools.append("analyze_failure")
        analysis=obs.get("analysis") or {}
        if (analysis.get("category")=="missing_dependency_or_import_path" and
                self.options.get("allow_install") and detect_environment_manifest(self.root)
                and not self.environment_created and not self.environment_setup_attempted):
            tools.append("create_isolated_environment")

        if self.patch_text and obs.get("patch_check") is None:
            tools.append("validate_patch")
        if (self.patch_check and self.patch_check.get("valid") and self.options.get("allow_write")
                and self.apply_attempted_round != self.repair_round):
            tools.append("apply_patch")
        if (self.patch_applied and self.options.get("allow_project_code")
                and self.verified_round != self.repair_round):
            if has_tests(self.root) or self.options.get("command"):
                tools.append("verify_after_patch")

        base_done=all(obs.get(k) is not None for k in ("scan","environment","static_validation"))
        runtime_required=bool(self.options.get("allow_project_code") and
                              (has_tests(self.root) or self.options.get("command")))
        runtime_done=obs.get("runtime") is not None
        latest=obs.get("post_patch_runtime") or obs.get("runtime")
        failed=bool(latest and (latest.get("returncode") not in (0,None) or latest.get("timed_out")))
        analysis_done=(
            not failed
            or obs.get("analysis") is not None
            or self.repair_round + 1 >= self.max_repair_rounds
        )
        patch_check_done=(not self.patch_text) or obs.get("patch_check") is not None
        patch_apply_done=(not (self.patch_check and self.patch_check.get("valid") and self.options.get("allow_write"))
                          or self.apply_attempted_round==self.repair_round)
        verify_done=(not self.patch_applied) or self.verified_round==self.repair_round
        install_pending=bool(analysis.get("category")=="missing_dependency_or_import_path" and
            self.options.get("allow_install") and detect_environment_manifest(self.root) and
            not self.environment_created and not self.environment_setup_attempted)
        if (base_done and (not runtime_required or runtime_done) and analysis_done and
                patch_check_done and patch_apply_done and verify_done and not install_pending):
            tools.append("finish")
        return tools

    def execute(self, name: str, state: dict[str, Any]) -> dict[str, Any]:
        if name=="scan_project":
            return scan_project(self.root)
        if name=="diagnose_environment":
            return diagnose_environment(self.python)
        if name=="static_validation":
            scan=scan_project(self.root)
            errors=scan.get("syntax_errors",[])
            return {"overall_status":"failed" if errors else "passed",
                    "summary":f"{len(errors)} Python syntax error(s) across {scan.get('python_file_count',0)} Python files.",
                    "syntax_errors":errors[:200],"python_file_count":scan.get("python_file_count",0),
                    "framework_hints":scan.get("framework_hints",[])}
        if name=="run_tests":
            if not self.options.get("allow_project_code"):
                raise PermissionError("Project-code execution was not authorized.")
            result=_pytest(self.root,self.python,self.options.get("timeout",300))
            self.latest_log=(result.get("stdout","")+"\n"+result.get("stderr",""))[-16000:]
            return result
        if name=="run_user_command":
            if not self.options.get("allow_project_code"):
                raise PermissionError("Project-code execution was not authorized.")
            command=self.options.get("command")
            if not command: raise ValueError("No user command was supplied.")
            args=list(command)
            if args and args[0].lower() in {"python","python.exe","py"}: args[0]=self.python
            result=run(args,cwd=self.root,timeout=self.options.get("timeout",300))
            result["command"]=args
            result["passed"]=result["returncode"]==0 and not result["timed_out"]
            self.latest_log=(result.get("stdout","")+"\n"+result.get("stderr",""))[-16000:]
            return result
        if name=="create_isolated_environment":
            if not self.options.get("allow_install"):
                raise PermissionError("Dependency installation was not authorized.")
            self.environment_setup_attempted=True
            result=build_isolated_environment(
                self.root,
                python_executable=self.python,
                timeout=self.options.get("install_timeout",1800),
            )
            self.environment_created=bool(result.get("created"))
            if self.environment_created and result.get("python_executable"):
                self.python=str(Path(result["python_executable"]).resolve())
            result["note"] = (
                result.get("note", "") +
                " RunFix did not modify the base interpreter or system CUDA drivers."
            ).strip()
            return result
        if name=="analyze_failure":
            if not self.latest_log: raise ValueError("No runtime failure log is available.")
            self.repair_attempts += 1
            if self.patch_applied:
                self.repair_round += 1
                self.patch_applied=False
            elif self.patch_rolled_back:
                self.repair_round += 1
                self.patch_rolled_back=False
            self.patch_check=None
            self.patch_text=""
            analysis=analyze_error(self.root,self.latest_log,use_llm=bool(self.options.get("use_llm")))
            llm=analysis.get("llm_analysis") or {}
            self.patch_text=llm.get("patch","") if isinstance(llm,dict) else ""
            return analysis
        if name=="validate_patch":
            if not self.patch_text: raise ValueError("No patch was proposed.")
            self.patch_check=validate_patch(self.root,self.patch_text)
            return self.patch_check
        if name=="apply_patch":
            if not self.options.get("allow_write"):
                raise PermissionError("File modification was not authorized.")
            if not self.patch_check or not self.patch_check.get("valid"):
                raise ValueError("Patch has not passed validation.")
            self.apply_attempted_round=self.repair_round
            result=apply_patch(self.root,self.patch_text,confirmed=True)
            self.patch_applied=bool(result.get("applied"))
            self.patch_transaction_id=result.get("transaction_id") if self.patch_applied else None
            return result
        if name=="verify_after_patch":
            if not self.patch_applied: raise ValueError("No patch was applied.")
            if has_tests(self.root):
                selection=select_tests(self.root,self.latest_log)
                if selection.get("selected"):
                    targeted=_pytest(
                        self.root,self.python,self.options.get("timeout",300),
                        selected_tests=selection["selected"],
                    )
                    if targeted.get("passed"):
                        full_suite=_pytest(self.root,self.python,self.options.get("timeout",300))
                        result={
                            "passed":full_suite.get("passed",False),
                            "strategy":"targeted_then_full_suite",
                            "selection":selection,
                            "targeted_tests":targeted,
                            "full_suite":full_suite,
                            "returncode":full_suite.get("returncode"),
                            "timed_out":full_suite.get("timed_out",False),
                            "stdout":targeted.get("stdout","")+"\n"+full_suite.get("stdout",""),
                            "stderr":targeted.get("stderr","")+"\n"+full_suite.get("stderr",""),
                        }
                    else:
                        result={
                            "passed":False,
                            "strategy":"targeted_tests_failed",
                            "selection":selection,
                            "targeted_tests":targeted,
                            "returncode":targeted.get("returncode"),
                            "timed_out":targeted.get("timed_out",False),
                            "stdout":targeted.get("stdout",""),
                            "stderr":targeted.get("stderr",""),
                        }
                else:
                    full_suite=_pytest(self.root,self.python,self.options.get("timeout",300))
                    result={
                        **full_suite,
                        "strategy":"full_suite_fallback",
                        "selection":selection,
                        "full_suite":full_suite,
                    }
            else:
                command=self.options.get("command")
                if not command: raise ValueError("No tests or explicit command available for post-patch verification.")
                args=list(command)
                if args and args[0].lower() in {"python","python.exe","py"}: args[0]=self.python
                raw=run(args,cwd=self.root,timeout=self.options.get("timeout",300))
                result={
                    "command":args,**raw,
                    "passed":raw["returncode"]==0 and not raw["timed_out"],
                    "strategy":"explicit_user_command",
                }
            self.latest_log=(result.get("stdout","")+"\n"+result.get("stderr",""))[-16000:]
            self.verified_round=self.repair_round
            if not result.get("passed") and self.patch_transaction_id:
                rollback=rollback_patch(
                    self.root,self.patch_transaction_id,confirmed=True
                )
                result["rollback"]=rollback
                if rollback.get("rolled_back"):
                    self.patch_applied=False
                    self.patch_rolled_back=True
            return result
        if name=="finish":
            return {"finished":True,"reason":"Planner selected completion; all required authorized stages have been considered."}
        raise ValueError(f"Tool is not allow-listed: {name}")
