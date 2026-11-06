from pathlib import Path

STAGES={"C0":"static missing-import check","C1":"CPU entrypoint run","C2":"CUDA alignment",
        "C3":"single-GPU execution","C4":"multi-GPU/DDP execution","C5":"report hallucination audit"}

def inspect(path):
    root=Path(path).expanduser().resolve()
    expected=["host_orchestrator.py","m2/run_one_job.py","tools/env_setup_runner/report_schema.json"]
    found=[x for x in expected if (root/x).is_file()] if root.is_dir() else []
    return {"available":len(found)==len(expected),"path":str(root),"found":found,
            "missing":[x for x in expected if x not in found],"stages":STAGES,
            "note":"RunFix provides a local C0-C5 evidence workflow; it does not launch the upstream 44-repository benchmark."}

def export_report(report):
    return {"schema":"runfix-researchenvbench-adapter-v1","project_path":report.get("project_path"),
      "overall_status":report.get("overall_status"),
      "runtime_stages":{s["id"]:{"status":s["status"],"details":s["details"],"evidence":s["evidence"]}
                        for s in report.get("stages",[])},
      "upstream_benchmark_executed":False}
