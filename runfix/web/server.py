from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from pathlib import Path
from ..scanner import scan_project
from ..diagnostics import diagnose_environment
from ..validation import validate_project
from ..analyzer import analyze_error
from .. import __version__

HTML = """
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RunFix</title><style>
body{margin:0;background:#f3f5f9;color:#182235;font:15px Segoe UI,Microsoft YaHei,Arial}
header{background:#111827;color:white;padding:24px max(20px,calc((100% - 1120px)/2))}
header h1{margin:0 0 5px}header p{margin:0;color:#cbd5e1}
main{max-width:1120px;margin:24px auto;padding:0 16px;display:grid;grid-template-columns:340px 1fr;gap:18px}
section{background:white;border:1px solid #e2e8f0;border-radius:14px;padding:18px}
h2{font-size:17px;margin:0 0 12px}label{display:block;margin:12px 0 5px;color:#475569;font-size:13px}
input,textarea{width:100%;box-sizing:border-box;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font:inherit;font-size:13px}
textarea{min-height:130px}button{margin:8px 6px 0 0;padding:10px 13px;border:0;border-radius:8px;background:#2563eb;color:white;cursor:pointer}
button.alt{background:#e2e8f0;color:#172033}pre{white-space:pre-wrap;word-break:break-word;background:#0b1220;color:#dbeafe;padding:14px;border-radius:10px;min-height:360px;max-height:70vh;overflow:auto;font-size:12px}
small{color:#64748b;line-height:1.5}@media(max-width:800px){main{grid-template-columns:1fr}}
</style></head><body><header><h1>RunFix</h1><p>科研代码自主运行诊断 · Agent规划执行 · 反馈式安全修复</p></header>
<main><section><h2>项目与任务</h2>
<label>项目绝对路径</label><input id="project" placeholder="例如 D:\\research\\my-project">
<label>Python 解释器（可选）</label><input id="python" placeholder="留空则使用 RunFix 当前 Python">
<label>Agent 任务目标</label><input id="goal" value="分析并修复这个科研代码项目，使其能够通过项目测试。">
<label>验证命令（可选）</label><input id="command" placeholder="例如 python train.py">
<label><input type="checkbox" id="allow_code" style="width:auto"> 允许 Agent 执行项目测试/命令（仅限可信代码）</label>
<label><input type="checkbox" id="allow_write" style="width:auto"> 允许 Agent 应用已检查的代码补丁</label>
<label><input type="checkbox" id="allow_install" style="width:auto"> 允许安装 requirements.txt 到项目独立虚拟环境</label>
<label><input type="checkbox" id="use_llm" style="width:auto"> 启用 LLM 规划与补丁建议（需配置 API）</label>
<small>代码执行和文件修改均需要明确授权。Agent 不具备任意 shell 工具。</small><p>
<button onclick="act('agent')">启动自主 Agent</button>
<button class="alt" onclick="act('scan')">扫描项目</button><button class="alt" onclick="act('diagnose')">环境诊断</button>
<button class="alt" onclick="act('validate')">分层验证</button></p>
<label>Traceback / 错误日志</label><textarea id="traceback" placeholder="粘贴完整 Python Traceback……"></textarea>
<button class="alt" onclick="act('analyze')">分析错误</button>
<p><small>Agent 会自主规划并调用白名单工具，记录每一步决策和结果。仅监听本机地址。</small></p>
</section><section><h2>诊断结果与证据</h2><pre id="out">等待操作……</pre></section></main>
<script>
const el=id=>document.getElementById(id);
async function act(name){
 const project=el('project').value.trim();if(!project){el('out').textContent='请先填写项目目录';return;}
 el('out').textContent='处理中……';
 const body={project_path:project,python_executable:el('python').value.trim()||null,
 command:el('command').value.trim()||null,traceback:el('traceback').value,
 goal:el('goal').value.trim(),allow_project_code:el('allow_code').checked,
 allow_write:el('allow_write').checked,allow_install:el('allow_install').checked,
 use_llm:el('use_llm').checked};
 try{const r=await fetch('/api/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const d=await r.json();el('out').textContent=JSON.stringify(d,null,2);}
 catch(e){el('out').textContent='请求失败：'+e;}
}
</script></body></html>
"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send_json(self,status,data):
        raw=json.dumps(data,ensure_ascii=False,indent=2).encode()
        self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(raw)));self.send_header("X-Content-Type-Options","nosniff")
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/":
            raw=HTML.encode();self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
        elif path=="/api/health": self.send_json(200,{"status":"ok","service":"RunFix","version":__version__})
        else:self.send_json(404,{"error":"Not found"})
    def do_POST(self):
        try:
            size=int(self.headers.get("Content-Length","0"))
            if size<1 or size>2_000_000:self.send_json(413,{"error":"Request must be 1 byte to 2 MB"});return
            data=json.loads(self.rfile.read(size).decode())
            root=Path(data.get("project_path","")).expanduser().resolve()
            if not root.is_dir():self.send_json(400,{"error":"Project directory not found"});return
            path=urlparse(self.path).path
            if path=="/api/agent":
                if data.get("allow_write") and not data.get("allow_project_code"):
                    self.send_json(400,{"error":"Allow project-code execution before allowing patch application."});return
                if data.get("allow_install") and not data.get("allow_project_code"):
                    self.send_json(400,{"error":"Allow project-code execution before allowing dependency installation."});return
                from ..agent.core import run_agent
                command=data.get("command")
                if command:
                    import shlex
                    command=shlex.split(command,posix=(__import__("os").name!="nt"))
                result=run_agent(root,data.get("goal") or "Diagnose and repair this research project.",
                    data.get("python_executable"),max_steps=12,
                    allow_project_code=bool(data.get("allow_project_code")),
                    allow_write=bool(data.get("allow_write")),
                    use_llm=bool(data.get("use_llm")),command=command,timeout=300,
                    allow_install=bool(data.get("allow_install")))
            elif path=="/api/scan": result=scan_project(root)
            elif path=="/api/diagnose": result={"scan":scan_project(root),"environment":diagnose_environment(data.get("python_executable"))}
            elif path=="/api/validate":
                cmd=data.get("command")
                # Web MVP intentionally supports only simple space-separated commands; use CLI for quoted args.
                result=validate_project(root,data.get("python_executable"),cmd.split() if cmd else None)
            elif path=="/api/analyze":
                if not data.get("traceback","").strip():self.send_json(400,{"error":"Paste a traceback first"});return
                result=analyze_error(root,data["traceback"],False)
            else:self.send_json(404,{"error":"Unknown endpoint"});return
            self.send_json(200,result)
        except Exception as e:self.send_json(500,{"error":f"{type(e).__name__}: {e}"})

def serve(host="127.0.0.1",port=8765):
    if host not in {"127.0.0.1","localhost","::1"}:
        raise ValueError("For safety, RunFix web MVP only binds to loopback.")
    server=ThreadingHTTPServer((host,port),Handler)
    print(f"RunFix web UI: http://{host}:{port}");print("Press Ctrl+C to stop.")
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
