from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import __version__
from ..analyzer import analyze_error
from ..audit import read_audit
from ..diagnostics import diagnose_environment
from ..scanner import scan_project
from ..validation import validate_project

STATE_DIR_NAME = ".runfix-web"

HTML = """
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RunFix Console</title><style>
*{box-sizing:border-box}
body{margin:0;background:#f3f5f9;color:#182235;font:15px Segoe UI,Microsoft YaHei,Arial}
header{background:#111827;color:#fff;padding:18px max(20px,calc((100% - 1280px)/2))}
header h1{margin:0 0 4px;font-size:22px}header p{margin:0;color:#cbd5e1;font-size:13px}
.layout{max-width:1280px;margin:18px auto;padding:0 16px;display:grid;grid-template-columns:330px 1fr;gap:16px}
section{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:16px}
h2{font-size:15px;margin:0 0 10px}
label{display:block;margin:10px 0 4px;color:#475569;font-size:12px}
input,textarea,select{width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:8px;font:inherit;font-size:13px}
textarea{min-height:90px}
button{margin:8px 6px 0 0;padding:8px 12px;border:0;border-radius:8px;background:#2563eb;color:#fff;cursor:pointer;font-size:13px}
button.alt{background:#e2e8f0;color:#172033}button.warn{background:#b91c1c}
pre{white-space:pre-wrap;word-break:break-word;background:#0b1220;color:#dbeafe;padding:12px;border-radius:10px;font-size:12px;max-height:420px;overflow:auto;min-height:120px}
.project{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:8px;border:1px solid #e2e8f0;border-radius:8px;margin-bottom:6px;cursor:pointer;font-size:12px}
.project.active{border-color:#2563eb;background:#eff6ff}
.project small{color:#64748b;display:block}
.badge{font-size:11px;padding:2px 6px;border-radius:999px;background:#e2e8f0;color:#334155}
.badge.ok{background:#dcfce7;color:#166534}.badge.bad{background:#fee2e2;color:#991b1b}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
small.hint{color:#64748b;line-height:1.5;display:block;margin-top:6px}
@media(max-width:900px){.layout{grid-template-columns:1fr}.grid2{grid-template-columns:1fr}}
</style></head><body>
<header><h1>RunFix Console</h1><p>多项目 · 诊断 · 复现 · 审计 · v<span id="ver"></span></p></header>
<div class="layout">
<section>
  <h2>项目</h2>
  <div id="projects"></div>
  <label>添加项目（绝对路径）</label>
  <input id="newproject" placeholder="D:\\research\\paper-code">
  <button onclick="addProject()">添加</button>
  <label>Agent 目标</label>
  <input id="goal" value="诊断并修复这个科研项目，使其能够通过测试。">
  <label>验证命令（可选）</label><input id="command" placeholder="python train.py">
  <label>Python 解释器（可选）</label><input id="python" placeholder="留空使用 RunFix 当前解释器">
  <label><input type="checkbox" id="allow_code" style="width:auto"> 允许执行项目测试/命令</label>
  <label><input type="checkbox" id="allow_write" style="width:auto"> 允许应用已检查的补丁</label>
  <label><input type="checkbox" id="allow_install" style="width:auto"> 允许安装到项目隔离环境</label>
  <label><input type="checkbox" id="use_llm" style="width:auto"> 启用 LLM 规划</label>
  <div>
    <button onclick="act('agent')">运行 Agent</button>
    <button class="alt" onclick="act('scan')">扫描</button>
    <button class="alt" onclick="act('diagnose')">环境诊断</button>
    <button class="alt" onclick="act('validate')">分层验证</button>
  </div>
  <label>Traceback</label><textarea id="traceback" placeholder="粘贴完整 Python Traceback"></textarea>
  <button class="alt" onclick="act('analyze')">分析错误</button>
</section>
<section>
  <h2 id="current">未选择项目</h2>
  <div class="grid2">
    <div><h2>任务</h2><pre id="tasks">—</pre></div>
    <div><h2>审计（最近 20 条）</h2><pre id="audit">—</pre></div>
  </div>
  <h2>结果</h2>
  <pre id="out">等待操作……</pre>
</section>
</div>
<script>
const el=id=>document.getElementById(id);
let current=null;
el('ver').textContent='%VERSION%';
function esc(v){return String(v??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
async function api(path,opts){const r=await fetch(path,opts);return await r.json();}
async function loadProjects(){
  const data=await api('/api/projects');
  const box=el('projects');box.innerHTML='';
  for(const p of data.projects){
    const div=document.createElement('div');
    div.className='project'+(current===p.path?' active':'');
    div.innerHTML=`<div><b>${esc(p.name)}</b><small>${esc(p.path)}</small></div>
      <span class="badge ${p.exists?'ok':'bad'}">${p.exists?(p.tasks||0)+' 任务':'缺失'}</span>`;
    div.onclick=()=>selectProject(p.path);
    box.appendChild(div);
  }
  if(!data.projects.length){box.innerHTML='<small class="hint">还没有项目。用下面的输入框添加。</small>';}
}
async function selectProject(path){current=path;el('current').textContent='项目：'+path;
  await refreshPanels();await loadProjects();}
async function refreshPanels(){
  if(!current){return;}
  const [tasks,audit]=await Promise.all([api('/api/tasks?project='+encodeURIComponent(current)),
                                         api('/api/audit?project='+encodeURIComponent(current))]);
  el('tasks').textContent=(tasks.tasks||[]).map(t=>`${t.status.padEnd(12)} ${t.label||t.task_id}  rc=${t.returncode}`).join('\\n')||'（无任务）';
  el('audit').textContent=(audit.entries||[]).map(e=>`${e.ts.slice(0,19)} ${e.action.padEnd(18)} ${e.outcome}  ${e.target}`).join('\\n')||'（无记录）';
}
async function addProject(){
  const path=el('newproject').value.trim();if(!path){return;}
  const data=await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
  if(data.error){el('out').textContent=data.error;return;}
  el('newproject').value='';await loadProjects();
}
async function act(name){
  if(!current){el('out').textContent='请先选择项目';return;}
  el('out').textContent='处理中……';
  const body={project_path:current,python_executable:el('python').value.trim()||null,
    command:el('command').value.trim()||null,traceback:el('traceback').value,
    goal:el('goal').value.trim(),allow_project_code:el('allow_code').checked,
    allow_write:el('allow_write').checked,allow_install:el('allow_install').checked,
    use_llm:el('use_llm').checked};
  try{const r=await fetch('/api/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    el('out').textContent=JSON.stringify(await r.json(),null,2);await refreshPanels();}
  catch(e){el('out').textContent='请求失败：'+e;}
}
loadProjects();
</script></body></html>
"""


def _state_dir(value: str | None = None) -> Path:
    path = Path(value).expanduser().resolve() if value else (Path.cwd() / STATE_DIR_NAME)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _registry_path(state: Path) -> Path:
    return state / "projects.json"


def load_registry(state: Path) -> list[str]:
    path = _registry_path(state)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    projects = payload.get("projects") if isinstance(payload, dict) else None
    return [str(item) for item in projects or [] if isinstance(item, str)]


def save_registry(state: Path, projects: list[str]) -> None:
    path = _registry_path(state)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"projects": projects}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _latest(directory: Path, filename: str) -> Path | None:
    if not directory.is_dir():
        return None
    candidates = [item / filename for item in directory.iterdir() if (item / filename).is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def summarize_project(path: str) -> dict:
    root = Path(path)
    summary = {"path": str(root), "name": root.name or str(root), "exists": root.is_dir(),
               "tasks": 0, "last_task": None, "last_report": None, "reproduction": None,
               "audit_entries": 0}
    if not summary["exists"]:
        return summary
    state = root / ".runfix"
    tasks_dir = state / "tasks"
    if tasks_dir.is_dir():
        records = []
        for item in tasks_dir.iterdir():
            record = _read_json(item / "task.json") if item.is_dir() else None
            if record:
                records.append(record)
        records.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        summary["tasks"] = len(records)
        if records:
            summary["last_task"] = {
                "task_id": records[0].get("task_id"),
                "status": records[0].get("status"),
                "label": records[0].get("label"),
                "returncode": records[0].get("returncode"),
                "created_at": records[0].get("created_at"),
            }
    report_path = _latest(state / "runs", "report.json")
    if report_path:
        report = _read_json(report_path) or {}
        summary["last_report"] = {
            "run_id": report.get("run_id"),
            "overall_status": report.get("overall_status"),
            "outcome": report.get("outcome"),
            "created_at": report.get("created_at"),
        }
    repro_path = _latest(state / "repro" / "reports", "reproduction_report.json")
    if repro_path:
        repro = _read_json(repro_path) or {}
        summary["reproduction"] = {
            "verdict": repro.get("verdict"),
            "created_at": repro.get("created_at"),
        }
    try:
        summary["audit_entries"] = len(read_audit(root, limit=5000))
    except Exception:
        summary["audit_entries"] = 0
    return summary


def list_tasks(path: str, limit: int = 20) -> dict:
    root = Path(path)
    tasks_dir = root / ".runfix" / "tasks"
    items = []
    if tasks_dir.is_dir():
        for item in tasks_dir.iterdir():
            record = _read_json(item / "task.json") if item.is_dir() else None
            if not record:
                continue
            log_path = item / "execution.log"
            items.append({
                "task_id": record.get("task_id"),
                "label": record.get("label"),
                "status": record.get("status"),
                "returncode": record.get("returncode"),
                "created_at": record.get("created_at"),
                "completed_at": record.get("completed_at"),
                "command": record.get("command"),
                "log_size_bytes": log_path.stat().st_size if log_path.is_file() else 0,
                "fingerprint": record.get("experiment_fingerprint"),
            })
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"project_path": str(root), "count": len(items[:limit]), "tasks": items[:limit]}


def latest_reports(path: str) -> dict:
    root = Path(path)
    state = root / ".runfix"
    result: dict = {"project_path": str(root), "report": None, "reproduction": None}
    report_path = _latest(state / "runs", "report.json")
    if report_path:
        result["report"] = _read_json(report_path)
    repro_path = _latest(state / "repro" / "reports", "reproduction_report.json")
    if repro_path:
        result["reproduction"] = _read_json(repro_path)
    return result


class Handler(BaseHTTPRequestHandler):
    state: Path = Path.cwd() / STATE_DIR_NAME

    def log_message(self, *args):
        pass

    def send_json(self, status, data):
        raw = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _query(self):
        parsed = urlparse(self.path)
        return parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}

    def do_GET(self):
        path, query = self._query()
        if path == "/":
            raw = HTML.replace("%VERSION%", __version__).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/api/health":
            self.send_json(200, {"status": "ok", "service": "RunFix", "version": __version__,
                                 "state_dir": str(self.state)})
            return
        if path == "/api/projects":
            projects = [summarize_project(item) for item in load_registry(self.state)]
            self.send_json(200, {"state_dir": str(self.state), "count": len(projects),
                                 "projects": projects})
            return
        if path in {"/api/tasks", "/api/audit", "/api/reports"}:
            project = query.get("project") or ""
            root = Path(project).expanduser().resolve()
            if not root.is_dir():
                self.send_json(400, {"error": "project must be an existing directory"})
                return
            try:
                if path == "/api/tasks":
                    limit = max(1, min(int(query.get("limit", 20)), 100))
                    self.send_json(200, list_tasks(str(root), limit))
                elif path == "/api/audit":
                    limit = max(1, min(int(query.get("limit", 20)), 200))
                    entries = read_audit(root, limit=limit)
                    self.send_json(200, {"project_path": str(root),
                                         "count": len(entries), "entries": entries})
                else:
                    self.send_json(200, latest_reports(str(root)))
            except Exception as exc:
                self.send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return
        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 2_000_000:
                self.send_json(413, {"error": "Request must be 1 byte to 2 MB"})
                return
            data = json.loads(self.rfile.read(size).decode())
            path, _ = self._query()
            if path == "/api/projects":
                target = Path(str(data.get("path", ""))).expanduser().resolve()
                if not target.is_dir():
                    self.send_json(400, {"error": "project path must be an existing directory"})
                    return
                projects = load_registry(self.state)
                if str(target) not in projects:
                    projects.append(str(target))
                    save_registry(self.state, projects)
                self.send_json(200, {"added": str(target), "count": len(projects)})
                return
            if path == "/api/projects/remove":
                target = str(Path(str(data.get("path", ""))).expanduser().resolve())
                projects = [item for item in load_registry(self.state) if item != target]
                save_registry(self.state, projects)
                self.send_json(200, {"removed": target, "count": len(projects)})
                return

            root = Path(data.get("project_path", "")).expanduser().resolve()
            if not root.is_dir():
                self.send_json(400, {"error": "Project directory not found"})
                return
            if path == "/api/agent":
                if data.get("allow_write") and not data.get("allow_project_code"):
                    self.send_json(400, {"error": "Allow project-code execution before allowing patch application."})
                    return
                if data.get("allow_install") and not data.get("allow_project_code"):
                    self.send_json(400, {"error": "Allow project-code execution before allowing dependency installation."})
                    return
                from ..agent.core import run_agent
                command = data.get("command")
                if command:
                    import shlex
                    command = shlex.split(command, posix=(__import__("os").name != "nt"))
                result = run_agent(root, data.get("goal") or "Diagnose and repair this research project.",
                                   data.get("python_executable"), max_steps=12,
                                   allow_project_code=bool(data.get("allow_project_code")),
                                   allow_write=bool(data.get("allow_write")),
                                   use_llm=bool(data.get("use_llm")), command=command, timeout=300,
                                   allow_install=bool(data.get("allow_install")))
            elif path == "/api/scan":
                result = scan_project(root)
            elif path == "/api/diagnose":
                result = {"scan": scan_project(root),
                          "environment": diagnose_environment(data.get("python_executable"))}
            elif path == "/api/validate":
                cmd = data.get("command")
                result = validate_project(root, data.get("python_executable"),
                                          cmd.split() if cmd else None)
            elif path == "/api/analyze":
                if not data.get("traceback", "").strip():
                    self.send_json(400, {"error": "Paste a traceback first"})
                    return
                result = analyze_error(root, data["traceback"], False)
            else:
                self.send_json(404, {"error": "Unknown endpoint"})
                return
            self.send_json(200, result)
        except Exception as exc:
            self.send_json(500, {"error": f"{type(exc).__name__}: {exc}"})


def serve(host="127.0.0.1", port=8765, state_dir: str | None = None):
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("For safety, RunFix web MVP only binds to loopback.")
    state = _state_dir(state_dir)
    Handler.state = state
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"RunFix web UI: http://{host}:{port}")
    print(f"Project registry: {_registry_path(state)}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
