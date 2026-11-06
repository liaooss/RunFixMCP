from __future__ import annotations
import os, re
from pathlib import Path
from typing import Any
from .scanner import scan_project
from .common import redact
from .llm import analyze_with_llm

FRAME = re.compile(r'File ["\'](.+?)["\'], line (\d+), in ([^\n]+)')
# pytest's short summary form: "tests/test_model.py:12: AssertionError"
PYTEST_FRAME = re.compile(r'(?m)^([A-Za-z]:[\\/][^:\n]+\.py|[^\s:][^:\n]*\.py):(\d+):')
ERR = re.compile(r"(?m)^([A-Za-z_][\w.]*Error|Exception):\s*(.*)$")

def classify(text: str) -> dict[str, Any]:
    low = text.lower()
    m = ERR.search(text)
    exc = m.group(1) if m else "Unknown"
    msg = m.group(2).strip() if m else (text.strip().splitlines()[-1] if text.strip() else "No traceback supplied.")
    rules = [
      (("no module named","modulenotfounderror"),"missing_dependency_or_import_path",
       "Python cannot resolve a referenced module. It may be an uninstalled dependency or an incorrect local-package import path.",
       ["Check the selected interpreter and declared dependencies.","If the module is local, inspect the project root, src-layout and package structure."],.78),
      (("cuda out of memory",), "gpu_out_of_memory",
       "The GPU ran out of available memory during the operation.",
       ["Record GPU memory use and batch size.","Try a smaller batch size or gradient accumulation.","Check other processes using the GPU."],.86),
      (("torch.cuda","cuda driver","cudnn","no kernel image"),"cuda_torch_compatibility",
       "The failure may involve driver, CUDA runtime/toolkit, PyTorch build, cuDNN or GPU architecture compatibility.",
       ["Run RunFix diagnose with the exact interpreter.","Compare driver support with the CUDA version bundled in PyTorch.","Check GPU compute-capability support in the installed wheel."],.76),
      (("size mismatch","mat1 and mat2 shapes","expected input"),"tensor_shape_mismatch",
       "Tensor dimensions do not match the operation or model's expected input shape.",
       ["Inspect tensor shapes before the failing operation.","Trace batch, channel, sequence and feature dimensions.","Check preprocessing against model configuration."],.81),
      (("unexpected keyword argument",),"api_signature_mismatch",
       "A call supplied a keyword argument not accepted by the installed function or library version.",
       ["Inspect the installed package version and function signature.","Pin a compatible dependency or minimally update the call site."],.82),
      (("filenotfounderror","no such file or directory","cannot find the path"),"missing_file_or_wrong_path",
       "A required file or directory was not found; a relative path may be resolved from an unexpected working directory.",
       ["Check the resolved path and current working directory.","Verify data/config files exist.","Prefer paths anchored to the project or config directory."],.77),
      (("certificate verify failed","ssl","tls"),"network_tls_certificate",
       "A TLS certificate or network transport error interrupted a package/API request.",
       ["Check system time and certificate trust.","Check proxy and mirror settings.","Do not permanently disable TLS verification."],.78),
      (("permissionerror","access is denied"),"permission_or_file_lock",
       "The process could not access a file or directory because of permissions or a file lock.",
       ["Check permissions and file locks.","Avoid protected system directories.","Use a project-local output directory."],.72),
      (("syntaxerror","indentationerror"),"python_syntax",
       "The Python parser found invalid syntax or indentation.",
       ["Inspect the exact file and line.","Check indentation, brackets and quotes.","Run compile checks before training."],.91),
      (("importerror",),"import_or_binary_compatibility",
       "An import failed, possibly because of an incompatible binary wheel, missing symbol or package-version conflict.",
       ["Capture full import error and package versions.","Check Python ABI and platform compatibility.","Reinstall only the affected package after confirming a compatible wheel."],.65),
      (("typeerror",),"type_or_interface_mismatch",
       "A function received an incompatible type or interface.",
       ["Inspect the call site and runtime argument types.","Check for API changes after library upgrades.","Make only a narrowly evidenced change."],.67),
      (("assertionerror","assertion failed"),"test_assertion_failure",
       "A test assertion failed: observed behaviour differs from the behaviour the test requires.",
       ["Read the failing assertion and the actual value reported by pytest.",
        "Decide from the project's documented behaviour whether the test or the implementation is wrong.",
        "Prefer the smallest change that satisfies the documented behaviour instead of deleting the assertion."],.74),
    ]
    for keys, category, root, actions, confidence in rules:
        if any(k in low for k in keys):
            return {"category":category,"confidence":confidence,"exception":exc,"message":msg,
                    "root_cause":root,"recommended_actions":actions,
                    "verification_steps":["Re-run the original failing command in the same interpreter and working directory.",
                                          "Run RunFix staged validation and inspect its evidence report."]}
    return {"category":"unclassified_runtime_error","confidence":.35,"exception":exc,"message":msg,
            "root_cause":"The traceback does not match a high-confidence built-in rule; more execution context is needed.",
            "recommended_actions":["Provide the complete traceback, including chained exceptions.",
                                   "Use the same interpreter and working directory as the failed run.",
                                   "Collect relevant source lines and package versions."],
            "verification_steps":["Re-run the failing command and compare the new traceback."]}

def _frames(text: str) -> list[tuple[str, int, str]]:
    """Collect (raw path, line, function) frames from CPython and pytest traceback styles."""
    found: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int]] = set()
    for match in FRAME.finditer(text):
        raw, line_text, function = match.groups()
        line = int(line_text)
        if (raw, line) in seen:
            continue
        seen.add((raw, line))
        found.append((raw, line, function.strip()))
    for match in PYTEST_FRAME.finditer(text):
        raw, line_text = match.group(1), match.group(2)
        line = int(line_text)
        if (raw, line) in seen:
            continue
        seen.add((raw, line))
        found.append((raw, line, ""))
    return found


def _contexts(project: Path, text: str):
    result=[]
    for raw, line, function in _frames(text):
        p=Path(raw)
        if not p.is_absolute(): p=(project/p).resolve()
        else: p=p.resolve()
        try: p.relative_to(project)
        except ValueError: continue
        if not p.is_file(): continue
        try: lines=p.read_text(encoding="utf-8",errors="replace").splitlines()
        except OSError: continue
        a,b=max(1,line-5),min(len(lines),line+5)
        result.append({"file":p.relative_to(project).as_posix(),"line":line,"function":function,
                       "excerpt":"\n".join(f"{i}: {lines[i-1]}" for i in range(a,b+1))})
        if len(result)>=10: break
    return result

def analyze_error(project_path: str | Path, traceback_text: str, use_llm: bool=False):
    project=Path(project_path).expanduser().resolve()
    if not project.is_dir(): raise NotADirectoryError(str(project))
    scan=scan_project(project); result=classify(traceback_text); contexts=_contexts(project,traceback_text)
    result["evidence"]={"traceback_tail":redact(traceback_text[-6000:]),"source_context":contexts,
        "framework_hints":scan.get("framework_hints"),"dependency_manifests":scan.get("dependency_manifests")}
    if use_llm:
        llm=analyze_with_llm({"languages":scan.get("languages"),"framework_hints":scan.get("framework_hints"),
            "entrypoint_candidates":scan.get("entrypoint_candidates")},traceback_text,contexts)
        result["llm_analysis"]=llm
        if llm and not llm.get("llm_error"):
            for key in ("category","confidence","root_cause","explanation","recommended_actions","verification_steps","patch"):
                if key in llm: result[key]=llm[key]
    return result
