# RunFix 0.6.0 现状审计报告

> 审计对象：`E:\Projects\RunFixMCP\RunFix`（基线版本 0.6.0）
> 审计角色：项目主程 / 架构师 / 测试负责人
> 审计时间：2026-11-06
> 审计方式：逐文件阅读全部源码与测试、在真实 Windows 环境执行测试套件、启动真实 MCP STDIO 服务端并完成协议握手、在真实失败项目上运行 Agent 并检查其行为。

本报告只记录**实际读到、实际跑出**的结论。所有无法在本机验证的项目都明确标注为“未验证”，不做推断性描述。

---

## 0. 审计环境与可复现命令

| 项目 | 值 |
| --- | --- |
| 操作系统 | Windows（`E:\` 卷，Anaconda 安装于 `E:\tool\miniconda`） |
| GPU | NVIDIA GeForce RTX 3070，8 GB，驱动 595.97 |
| Git | 2.56.0.windows.2（`E:\tool\Git\cmd\git.exe`） |
| Conda | 26.9.3（存在插件与 TOS 缓存写入问题，见 §4.9） |
| 本次测试用解释器 | Python 3.11.17（conda-forge），独立 venv：`E:\Projects\RunFixMCP\.venvs\runfix-dev` |
| MCP SDK | `mcp==2.3.0`（SDK v2，`mcp.server.MCPServer`） |
| pytest | 9.2.0 |
| RunFix 运行时依赖 | 无（`pyproject.toml:12` `dependencies = []`） |

复现测试套件：

```powershell
$env:PYTHONPATH="E:\Projects\RunFixMCP\RunFix"
E:\Projects\RunFixMCP\.venvs\runfix-dev\Scripts\python.exe -m pytest -q
```

复现 MCP 握手：

```powershell
$env:PYTHONPATH="E:\Projects\RunFixMCP\RunFix"
E:\Projects\RunFixMCP\.venvs\runfix-dev\Scripts\python.exe -m runfix.mcp.smoke
```

> 沙箱说明：本会话的文件沙箱是 AppContainer 形态，`tempfile.mkdtemp()` / `os.mkdir(..., 0o700)` 会写出“仅所有者”DACL，而沙箱令牌中的用户 SID 是 deny-only，导致 pip / venv / pytest 的临时目录写入失败。审计期间用一个**不进入项目、不参与交付**的 `E:\Projects\RunFixMCP\.sandbox\sitecustomize.py` 让 `os.mkdir` 忽略 Windows 上的 POSIX mode（这本身就是普通 Windows 上的行为）。该 shim 不修改 RunFix 任何代码，也不影响测试对 RunFix 逻辑的覆盖；但在用户机器的真实环境中不需要它。同理，MCP 握手需要父子进程匿名管道，普通沙箱下会 `WinError 5`，握手与 pip 安装是在放开限制的单次命令中完成的。

---

## 1. 当前项目架构（以代码为准）

```
runfix/
  common.py          # 子进程执行、脱敏、报告保存与 Markdown 渲染
  scanner.py         # 目录遍历 + AST 解析：语言、入口、清单、导入、语法错误、git 信息
  diagnostics.py     # 子进程探针：Python/PyTorch/CUDA/nvidia-smi/env-doctor
  analyzer.py        # Traceback 规则分类 + 源码上下文 + 可选 LLM 增强
  validation.py      # C0–C5 分层验证与证据一致性审计
  patches.py         # 补丁路径校验、AST 隔离校验、事务化应用/回滚
  environments.py    # environment.yml / requirements.txt / pyproject.toml → .runfix/environments
  task_manager.py    # 后台任务：启动、状态、停止、日志、列表、实验指纹
  test_selection.py  # Traceback → 测试文件静态导入关系评分
  llm.py             # OpenAI 兼容 Chat Completions 调用（分析 + 补丁建议）
  cli.py / __main__.py
  agent/
    core.py          # Plan–Act–Observe 循环、停止条件、结果判定、报告落盘
    brain.py         # 确定性策略（heuristic_next）+ LLM 规划器（llm_next）
    tools.py         # ToolBox：白名单工具 + available() 可用性判定 + 权限检查
  mcp/
    server.py        # MCPPolicy + 20 个工具注册 + stdio 启动
    smoke.py         # 真实 stdio 子进程端到端检查
  web/server.py      # 回环地址上的单页 Web UI + /api/*
  integrations/      # repo2run / env_doctor / research_envbench 适配
```

**分层评价**：交接文档要求的 7 层边界（Agent / 项目理解 / 环境诊断 / 故障分析 / 修复 / 实验复现 / MCP 与交互）在前 6 层已有对应模块；**第 6 层“实验复现层”完全缺失**（下文 §3.3）。

**数据流**：
`项目路径 → scanner → (diagnostics, validation) → 失败日志 → analyzer → (test_selection) → patches → 报告 .runfix/runs/<id>/`
Agent 在 `core.py:36` 的循环里反复调用 `ToolBox.available()` 取候选工具，执行后写回 `state["observations"]`，由 `available()` 决定下一步可见的工具集合。

**关键设计约束**（已正确实现）：
- 工具白名单（`tools.py:112` `execute()` 末尾对未知名字抛错）。
- 三层权限：CLI 参数 / MCP 服务端开关 / 单次调用 confirm（`server.py:63-91`）。
- 工具参数不能提升服务端策略（`server.py:352-369`）。
- 文件修改必须走事务（`patches.py:294`），回滚前校验 `sha256_after`（`patches.py:460-482`）。

---

## 2. 已实现功能（逐项证据）

| 能力 | 位置 | 状态 |
| --- | --- | --- |
| 项目结构扫描、语言/入口/清单识别 | `scanner.py:23-83` | 已实现，有测试 |
| 语法错误判定 | `scanner.py:42-44` | 已实现，有测试 |
| Python/PyTorch/CUDA/GPU 探测 | `diagnostics.py:7-66` | 已实现，实测可用（RTX 3070 可见） |
| Traceback 规则分类（11 类 + 兜底） | `analyzer.py:17-63` | 已实现，有测试 |
| 源码上下文提取 | `analyzer.py:65-81` | **仅支持 `File "...", line N` 格式**（见 §4.11） |
| LLM 根因分析与补丁建议 | `llm.py:6-35` | 已实现；无 key 时返回 `llm_error`，未实测 |
| C0–C5 分层验证 | `validation.py:26-107` | 已实现；C5 存在逻辑缺陷（§4.8） |
| 补丁路径/扩展名/符号链接校验 | `patches.py:51-77` | 已实现，有测试 |
| 隔离临时树 AST 校验 | `patches.py:80-152` | 已实现，有测试 |
| 补丁事务、前后 SHA-256、备份 | `patches.py:294-392` | 已实现，有测试 |
| 冲突感知回滚（拒绝覆盖独立修改） | `patches.py:434-494` | 已实现，有测试 |
| Agent Plan–Act–Observe 循环 | `agent/core.py:36-105` | 已实现；确定性路径为固定序列（§4.4） |
| LLM 规划器 + 安全回退 | `agent/brain.py:34-98`, `core.py:38-46` | 已实现；未实测（无 API key） |
| 补丁失败自动回滚 | `agent/tools.py:242-249` | 已实现，有测试 |
| Traceback→测试选择 | `test_selection.py:53-146` | 已实现，有测试 |
| 后台任务生命周期 | `task_manager.py:245-550` | 已实现，有测试 |
| 实验指纹（命令/git/清单哈希/解释器） | `task_manager.py:345-357` | 已实现，有测试 |
| 三种清单的隔离环境构建 | `environments.py:68-189` | venv 路径可用；**conda 路径在 Windows 恒为失败**（§4.2） |
| 环境指纹复用 | `environments.py:101-112` | 逻辑正确，但 Windows 测试失败（§4.5） |
| MCP stdio 服务端 + 20 工具 | `mcp/server.py:147-389` | **真实握手已通过**（SDK 2.3.0） |
| MCP 权限拒绝 | `mcp/server.py:63-91` | v1 语义正确；**v2 下拒绝原因被吞掉**（§4.1） |
| Web UI（回环） | `web/server.py:59-117` | 已实现；版本号错误（§4.7） |
| CLI 全部子命令 | `cli.py:15-105` | 已实现 |

---

## 3. 真实测试与验证结果

### 3.1 单元/集成测试（本机实测）

```
4 failed, 61 passed, 3 skipped in 7.51s
```

- **与交接文档“67 passed / 1 skipped”不一致。** 实际总数 68 项，其中 61 通过、4 失败、3 跳过。
- 4 个失败：`test_requirements_builds_project_local_venv`、`test_pyproject_builds_project_local_venv_and_installs_project`、`test_matching_environment_fingerprint_is_reused`、`test_conda_manifest_uses_conda_and_returns_environment_python`。
- 3 个跳过：2 个符号链接测试（Windows 需要特权）+ 1 个 MCP STDIO 端到端（当时未装 SDK）。

失败原因见 §4.2 与 §4.5。**结论：0.6.0 的测试套件在 Windows 上从未全绿过。**

### 3.2 MCP STDIO 真实握手（本机实测）

在安装真实 SDK `mcp==2.3.0` 后执行 `python -m runfix.mcp.smoke`：

- ✅ 服务端子进程启动成功，`stdio` 传输建立。
- ✅ `list_tools` 返回工具集合，20 个工具全部被发现。
- ✅ `get_server_capabilities` 返回 `version=0.6.0`、`sdk_api=MCPServer v2`、工具清单一致。
- ✅ `scan_project` 对临时项目返回正确文件统计。
- ✅ `list_tasks` 返回空列表。
- ❌ **权限闸门断言失败**：`run_project_command` 抛出 `PermissionError` 后，SDK v2 将其视为“崩溃”，客户端只收到
  `mcp.server.mcpserver.exceptions.UnexpectedToolError: Error executing tool run_project_command`，
  原始拒绝文案 `Project-code execution is disabled by the MCP server.` 没有到达客户端。
  于是 `smoke.py:148-158` 的 `_is_expected_execution_denial()` 判定失败，整个握手测试以失败退出。

这是**真实存在的兼容性缺陷**，不是测试写法问题：任何 Codex / Claude 客户端在 v2 下调用被门禁的工具，都只会看到一句 “Error executing tool xxx”，无法区分“权限被拒”和“工具内部崩溃”。

### 3.3 Agent 行为实测

用一个真实会失败的临时项目（`model.py` 的 `add()` 返回 `a - b`，`tests/test_model.py` 断言 `add(2,3)==5`）运行：

```powershell
python -m runfix agent <proj> --allow-project-code --max-steps 12
```

实测工具序列：

```
 1 heuristic  scan_project          completed
 2 heuristic  diagnose_environment  completed
 3 heuristic  static_validation     completed
 4 heuristic  run_tests             completed
 5 heuristic  analyze_failure       completed
 6 heuristic  finish                completed
outcome: diagnosed | stop: planner_finished | planner: heuristic_tool_agent
```

- 失败被 `analyze_failure` 归为 `unclassified_runtime_error`（置信度 0.35），`source_context` 为空。
- 之后直接 `finish`，`outcome=diagnosed`，`patch_transaction_id=null`。
- 原因：**确定性策略完全没有“根据观察结果重规划”的能力**，且 `patch_text` 只在 `tools.py:170-172` 由 LLM 输出赋值——**没有 LLM 时 Agent 永远不可能产生任何补丁**。

### 3.4 未验证项（明确声明）

| 项目 | 原因 |
| --- | --- |
| LLM 规划器 / LLM 补丁 | 无 API key，未执行 |
| Codex / Claude Desktop 实际接入 | 本机未安装这些客户端 |
| Conda 环境真实创建 | 本机 conda 存在插件与 TOS 缓存故障（§4.9），`conda env create` 无法完成 |
| Wheel 构建 / 独立 venv 安装 | 0.6.1 中补做（本次审计未跑 `python -m build`） |
| Docker / WSL 沙箱 | 尚未实现 |
| `env-doctor` 集成 | 未安装该可选依赖 |

---

## 4. 已发现的缺陷（按严重度）

### 4.1 【严重】MCP SDK v2 下权限拒绝语义丢失

- 位置：`mcp/server.py:63-91`（各 `require_*` 抛 `PermissionError`）、`mcp/smoke.py:78-94`。
- 现象：SDK 2.x 中只有 `ToolError` / `ResourceError` 会被包装成 `is_error=True` 且保留文案；其它异常一律视为崩溃，客户端只看到 `Error executing tool <name>`。
- 影响：客户端无法区分权限拒绝与内部错误；服务端每次拒绝都会打印 ERROR 级 traceback（噪音）；`smoke.py` 端到端测试必然失败。
- 证据：SDK 源码 `mcp/server/mcpserver/tools/base.py:160-213`；本机实测 traceback 见 §3.2。

### 4.2 【严重】conda 环境创建在 Windows 上恒报失败

- 位置：`environments.py:101` 先算 `python_path = _environment_python(env_dir)`，`:121-136` conda 分支执行 `runner()` 后**不重新计算** `python_path`，`:175` 用旧值判断 `ready`。
- 机制：`_environment_python()`（`:51-54`）在 `env_dir/python.exe` 不存在时回退到 `env_dir/Scripts/python.exe`。环境尚未创建时两个都不存在，于是 `python_path` 被固定成 `.../Scripts/python.exe`（Windows venv 布局），而 conda 环境真正的解释器是 `env_dir/python.exe`。
- 影响：`environment.yml` 项目即使 conda 创建成功，RunFix 也返回 `created=False`、`python_executable=None`，Agent 不会切换到新环境，后续修复全部作用在错误解释器上。
- 证据：`tests/test_environments.py::test_conda_manifest_uses_conda_and_returns_environment_python` 实测失败。

### 4.3 【高】生成的 `report.md` 是单行文本，换行符是字面量 `\n`

- 位置：`common.py:88` `return "\\n".join(lines)`。
- 实测：报告的物理行数为 1，内容形如 `# RunFix report\n\n- Run: ...`，`\n` 是反斜杠加 n 两个字符。
- 同类问题：`agent/tools.py:207`、`agent/tools.py:240`（测试输出拼接）、`agent/brain.py:63`（LLM 上下文拼接）。
- 影响：Markdown 报告在任何渲染器里都是一整段不可读的文本；下游 `get_latest_report` 返回的 markdown 同样不可用。

### 4.4 【高】Agent 的确定性策略是固定序列，不具备重规划能力

- 位置：`agent/brain.py:5-32`（`heuristic_next` 是一串 `if not calls.get(...)` 的线性检查）、`agent/core.py:36-46`。
- 证据：§3.3 实测 6 步固定序列；失败未被修复就直接 `finish`。
- 结构性缺陷：`heuristic_next` 只读 `tool_counts`，**不读 `observations` 的内容**，因此无法根据“失败类型”改变下一步；`available()`（`tools.py:53-110`）虽然按观察结果裁剪工具，但这是“可用性裁剪”，不是“策略选择”。
- 次生缺陷：`patch_text` 只来自 LLM（`tools.py:171-172`），确定性路径**没有任何补丁生成器**，所以“确定性策略回退”在功能上等于“只诊断不修复”。

### 4.5 【中】3 个测试按 POSIX 目录布局编写，在 Windows 上必然失败

- 位置：`tests/test_environments.py:54-58, 77-81, 92-96` 创建 `<env>/bin/python`；
  而被测代码 `environments.py:51-54` 在 Windows 上查找 `<env>/python.exe` 或 `<env>/Scripts/python.exe`。
- 这解释了交接文档“67 passed”与实际“61 passed / 4 failed”的差异——套件在 Windows 上从未全绿。

### 4.6 【中】MCP 冒烟测试的断言强度不足，且版本号硬编码

- `smoke.py:100-102` 只断言 `EXPECTED_TOOLS ⊆ 实际工具`，多注册一个工具不会被发现；`smoke.py:109` 硬编码期望 `"0.6.0"`，版本升级必然要手工改代码。
- `MCP_SERVER.md:78` 声称“发现全部 20 个已注册工具”，与实现不符。

### 4.7 【中】Web `/api/health` 版本号漂移

- `web/server.py:71` 返回 `"version":"0.5.0"`，而 `__init__.py:2` 为 `0.6.0`。无测试覆盖。

### 4.8 【中】C5“证据一致性审计”永远不会失败

- `validation.py:23-24` 的 `stage()` 要求 `evidence` 是必填位置参数，因此 `validation.py:96` 的 `s.get("evidence") is None` 永远为假。
- C5 实际只检查“状态字符串是否合法”，与 README 描述的“检查每个结论是否有结构化证据”不符。

### 4.9 【中】补丁事务缺少并发控制与崩溃恢复

- 位置：`patches.py:294-392`、`patches.py:446-458`。
- 问题 1：validate → backup → apply 之间没有文件锁。两个并发 `apply_patch` 可以对同一文件交错执行（TOCTOU）。
- 问题 2：进程在 `git apply` 之后、写回 `status="applied"` 之前崩溃，事务会永久停在 `"applying"`；此时 `rollback_patch` 因 `status != "applied"` 拒绝回滚，备份文件成为孤儿，用户文件处于未知状态。
- 问题 3：`.runfix/patch_transactions/` 无保留策略，长期使用会无限增长。

### 4.10 【中】后台任务在服务重启后拿不到退出码

- 位置：`task_manager.py:388-401`。重启后 `_PROCESSES` 为空，只能通过 PID 身份判断“还活着 / 已结束”，已结束的任务被标记为 `ended_unobserved`，`returncode` 永久为 `null`。
- 影响：`ROADMAP.md:44` 自己列出的 “Full task recovery with exit-code capture after MCP server restart” 仍未完成；实验管理场景下无法判断训练是成功结束还是崩溃。

### 4.11 【中】pytest 风格 Traceback 无法提取源码上下文

- `analyzer.py:9` 的 `FRAME` 只匹配 `File "...", line N, in f`。pytest 断言失败输出的是 `tests/test_model.py:4: AssertionError`，实测 `source_context` 为空数组。
- 影响：最常见的科研项目失败形态（测试断言失败）拿不到任何源码证据，根因分析退化为正则匹配。

### 4.12 【低】文档与代码不一致（子代理逐条比对结果）

| 文档 | 声称 | 实际 |
| --- | --- | --- |
| `README.md:168,224-226` | 备份在 `.runfix\backups\...` | 在 `.runfix/patch_transactions/<txid>/backups/...`（`patches.py:308-322`） |
| `AGENT_DESIGN.md:24`, `README.md:237` | 安装只需 `--allow-install` | 还需 `--allow-project-code`（`core.py:22-23`，`server.py:358-361`） |
| `README.md:67` vs `README.md:241` | Web UI 可应用补丁 / 不暴露补丁 | 可经 `run_agent` 间接应用（`web/server.py:31,50,94`） |
| `README.md:139-144` | `runfix integrations` 会调 `env-doctor check` | 只调 `--version`（`integrations/env_doctor.py:5-8`） |
| `README.md:179` | C5 校验结构化证据 | 见 §4.8 |
| `UPGRADE_0.5.0.md:40` | `--version` 应报 0.5.0 | 现报 0.6.0 |
| `MCP_SERVER.md:99,108`、`codex-mcp-config.example.toml:2,11` | 路径写死 `E:\Projects\RunFix` | 本仓库位于 `E:\Projects\RunFixMCP\RunFix` |

已实现但完全无文档：`runfix diagnose` 子命令（`cli.py:20,56-61`，而 `analyzer.py:26` 还建议用户去跑它）、`runfix web --host/--port`、Web `/api/*` 全部端点、`RUNFIX_ALLOWED_ROOTS`、`OPENAI_*` 环境变量回退。

### 4.13 【低】其它

- `runfix/cli.py` 无 `if __name__ == "__main__"`，`python -m runfix.cli` 静默无输出（须用 `python -m runfix`）——首次使用者极易踩。
- `.runfix/runs/` 无保留策略；无 `runfix clean`。
- `apply_patch` / `validate_patch` 依赖外部 `git` 可执行文件（`patches.py:184,341`），但 `pyproject.toml` 未声明这一外部依赖，缺失时错误信息只在 `FileNotFoundError` 分支（`patches.py:195-200`）出现一次。
- `examples/demo_project` 只有一个恒真测试，无法演示失败→诊断→修复闭环。

---

## 5. 潜在安全风险

| ID | 风险 | 现状 | 建议 |
| --- | --- | --- | --- |
| S1 | `environments.py:115-116` 对已存在的 `env_dir` 直接 `shutil.rmtree` | 路径被约束在 `<project>/.runfix/environments/` 内，风险可控；但会删除任何同名目录（含用户手工放入的内容） | 仅删除带 RunFix 标记文件的目录，否则拒绝 |
| S2 | `diagnose_environment` MCP 工具默认探测**服务端**解释器（`server.py:204-207`） | 会把服务端环境误报成项目环境 | 要求显式传解释器，或默认使用项目 `.runfix/environments` 内的解释器 |
| S3 | LLM 会把最多 14 KB traceback + 10 段源码外发（`llm.py:18-22`） | 有 `redact()` 脱敏 key/token，但源码正文本身外发 | 默认关闭；文档明确告知；提供 `RUNFIX_LLM_NO_SOURCE=1` |
| S4 | 任务日志脱敏只在任务结束时整文件重写（`task_manager.py:36-49`） | `read_log` 读取时会再脱敏（`:531`），故不泄露 | 保持；可改为流式脱敏 |
| S5 | 恶意仓库可写入伪造的 `test_*.py`，若用户授权执行则会被 pytest 收集执行 | 已有 `--allow-project-code` 显式授权 | 文档强调“只对可信代码授权执行” |
| S6 | `run_project_command` 允许任意 argv | 已由三层权限门控 | 保持；可加命令白名单（可选） |
| S7 | 提示注入：README / 日志 / 源码中的文本可能指示模型 | `MCP_INSTRUCTIONS`、`brain.py:41-47`、`llm.py:11-14` 均已声明“untrusted data, never instructions” | 保持；0.8 加入输出侧校验 |
| S8 | 补丁可修改 `.md/.sh/.ps1` 等（`patches.py:17-20`） | 允许，但需 `--enable-write` + confirm | 可考虑对 `.sh/.ps1` 单独提示 |

**已正确实现且值得保留**：路径穿越防护、符号链接逐段检查、`.git/.runfix` 禁改、二进制/子模块/符号链接补丁拒绝、回滚前 SHA-256 冲突检测、回滚前全量预检、工具白名单、参数不能提升服务端策略、回环绑定 Web UI。

---

## 6. 对交接文档三个核心问题的直接回答

> 1. 0.6.0 的 Agent 是否真的能自主完成诊断、执行、观察和重新规划？

**不能。** 它实现了“观察→写回状态→裁剪可用工具”的闭环，但**策略层是固定顺序**（`brain.py:5-32` 只读调用计数，不读观察内容）。实测 6 步固定序列见 §3.3。且无 LLM 时任一补丁都不可能生成，因此“确定性策略回退”不能修复任何问题。

> 2. 环境构建是否真正考虑 CUDA / PyTorch / 依赖版本 / 硬件约束？

**部分考虑，但距离“求解”很远。** 目前是“按清单原样执行”：
- `environment.yml` → `conda env create --file`（`environments.py:132-136`）；
- `requirements.txt` → venv + `pip install -r`（`:137-155`）；
- `pyproject.toml` → venv + `pip install .`（`:156-173`）。

没有任何版本冲突求解、CUDA 与 PyTorch 兼容性校验、GPU 可用性前置检查、失败细分（无 wheel / 编译失败 / 版本冲突）。而且 conda 分支在 Windows 上恒报失败（§4.2）。

> 3. 实验是否真正复现成功，而不是 `train.py` 没报错就算成功？

**尚未具备任何实验复现能力。** 没有：README 解析、入口与参数识别、数据集路径检查、指标采集、随机种子/硬件记录、复现报告。现有“验证”只到“进程退出码为 0”（`validation.py:47`、`tools.py:139`）。这正是 0.7.0 要解决的问题。

---

## 7. 下一阶段开发建议

按“先稳定、后扩展”的顺序，**不要直接开始 0.7.0**。

### 0.6.1 —— 稳定性与真实部署（先做）

必须完成：

1. 修复 §4.1（MCP v2 的错误语义），使权限拒绝在 v1 / v2 下都返回 `is_error=True` 且携带原始文案。
2. 修复 §4.2（conda 环境创建的 `python_path` 时序 bug），并补 Windows 真实 conda 验证。
3. 修复 §4.3（字面量 `\n`），并加回归测试断言 `report.md` 行数 > 10。
4. 修复 §4.5（3 个 POSIX 测试）→ 测试按平台布局构造，套件在 Windows 全绿。
5. 修复 §4.6（冒烟测试改为“集合相等 + 版本派生自 `__version__`”）、§4.7（health 版本）。
6. 修复 §4.8（C5 改为真实校验：每个非 skipped 阶段必须有证据键且非空）。
7. 修复 §4.11（支持 pytest 风格 Traceback 的源码上下文提取）。
8. 补 §4.9：补丁事务加跨进程文件锁 + 启动时“孤儿 `applying` 事务”恢复 + 事务保留策略。
9. 补 §4.10：任务退出码跨重启恢复（监督者进程落盘 exit code）。
10. 一键安装/升级脚本改为可真实执行（当前 `install_windows.ps1` 依赖不存在的 `py -3.11`）。
11. 回归测试：新增上述每一项的测试，目标 `0 failed`。

### 0.7.0 之后

保持交接文档顺序：0.7.0 实验复现（`reproduce/` 新包：仓库理解 → 参数识别 → 环境求解 → 运行 → 指标采集 → 报告），0.8.0 证据图与自适应修复，0.9.0 评测，1.0.0 产品化。

但 0.7.0 之前必须先解决 §4.4：**Agent 需要一个真正读取观察结果的策略层**（状态机/条件规则 + 可解释决策），否则实验复现的“失败→调整→重试”仍然落空。

---

## 8. 审计结论

- 0.6.0 的**安全骨架是合格的**：权限三层、路径约束、事务化补丁、冲突感知回滚都真实存在且有测试。
- 但**“可验证”这件事本身没有做到位**：交接文档声称的测试结果在 Windows 上不成立，MCP 真实握手在 v2 下失败，conda 环境创建在 Windows 上恒返回失败，报告输出格式损坏。
- 项目当前处于“**能诊断、能受控执行、能安全回滚，但还不会自主修复，也完全没有实验复现能力**”的阶段。
- 因此本审计的结论是：**先完成 0.6.1，把“已验证”变成事实，再进入 0.7.0。**

（本报告的所有结论都可以用 §0 的命令复现。）

---

## 9. 0.6.1 修复结果（审计后回填）

本节在 0.6.1 实现完成后回填，记录每一项缺陷的处置状态与验证方式。

| 编号 | 缺陷 | 状态 | 修复位置 | 回归验证 |
| --- | --- | --- | --- | --- |
| §4.1 | MCP v2 权限拒绝语义丢失 | **已修复** | `mcp/server.py` `_translate_tool_failure` / `_load_sdk_tool_error` / `tool()` | `tests/test_mcp_errors.py`；真机 `runfix-mcp-check` 与 `--full` |
| §4.2 | conda 环境创建恒报失败 | **已修复** | `environments.py` 创建后重新解析 `python_path` | `tests/test_environments.py`（7 passed） |
| §4.3 | 字面量 `\n` | **已修复** | `common.py`、`agent/tools.py`、`agent/brain.py` 及测试夹具 | `tests/test_report_format.py` |
| §4.4 | Agent 固定序列、无重规划 | **未修复（转入 0.7.0 首要任务）** | — | 审计 §3.3 记录在案 |
| §4.5 | 3 个 POSIX 测试在 Windows 必失败 | **已修复** | `tests/test_environments.py` `fake_venv_prefix` | 全量套件 0 failed |
| §4.6 | 冒烟测试子集断言 + 版本硬编码 | **已修复** | `mcp/smoke.py` 集合相等 + `__version__` | `tests/test_mcp_errors.py::test_smoke_expected_tools_match_the_documented_contract` |
| §4.7 | `/api/health` 版本漂移 | **已修复** | `web/server.py` 使用 `__version__` | 人工核对 + 版本单点来源 |
| §4.8 | C5 永不失败 | **已修复** | `validation.py` `audit_stages` | `tests/test_diagnostics_quality.py` |
| §4.9 | 事务并发与崩溃恢复 | **已修复** | `patches.py` `_transaction_lock` / `_recover_interrupted_locked` / `_prune_transactions` | `tests/test_patch_transactions.py` |
| §4.10 | 任务退出码跨重启丢失 | **已修复** | `task_manager.py` supervisor + `process_result.json` | `tests/test_task_recovery.py` |
| §4.11 | pytest 风格 Traceback 无上下文 | **已修复** | `analyzer.py` `_frames` / `PYTEST_FRAME` / `test_assertion_failure` | `tests/test_diagnostics_quality.py` |
| §4.12 | 文档与代码不一致 | **已修复** | `README.md`、`MCP_SERVER.md`、`ARCHITECTURE.md`、`AGENT_DESIGN.md`、`SAFETY.md`、`ROADMAP.md` | 逐条对照源代码 |
| §4.13 | CLI/脚本/保留策略杂项 | **已修复** | `cli.py` `__main__` 守卫 + `patch-recover`；`scripts/*.ps1` 重写 | 手动执行 + wheel 安装验证 |
| S1 | 环境目录 rmtree 无防护 | **已修复** | `environments.py` `_is_disposable_environment` | `tests/test_environments.py` |
| S2 | `diagnose_environment` 默认探测服务端解释器 | **未修复（转 0.7.0）** | — | 审计 §5 记录在案 |

### 0.6.1 的实测验证结果

| 验证项 | 结果 |
| --- | --- |
| `pytest -q`（Windows，Python 3.11，安装了 mcp 的 venv） | **105 passed, 2 skipped, 0 failed** |
| MCP 门禁模式 `runfix-mcp-check` | PASS：工具集合精确匹配、版本匹配、4 类门禁全部按预期拒绝并回传原始原因 |
| MCP 全功能模式 `--full` | PASS：20/20 工具在真实协议下调用成功，含 apply → rollback 全周期 |
| Wheel 构建 | PASS：`runfix_agent-0.6.1-py3-none-any.whl`（67 KB） |
| 干净 venv 安装 wheel | PASS：`runfix --version` → `RunFix 0.6.1`；核心模块导入正常 |
| `scripts\\install_windows.ps1` 一键安装 | PASS（真实执行，Python 3.14.8 + `mcp==2.3.0`）：创建 venv、安装、版本自检、MCP 自检全部通过，退出码 0 |
| 全部 PowerShell 脚本 | 语法解析通过；`install_windows.ps1 -DryRun` 能正确识别解释器并打印计划 |

---

## 10. 0.6.1 测试基线（回填）

> 由 0.6.1 交付时在相同环境重跑得出，命令见 §0。

```
python -m pytest -q   →   105 passed, 2 skipped
```

2 个跳过项：两个符号链接测试——本机 Windows 账户没有 `SeCreateSymbolicLinkPrivilege`，测试已改为“能建符号链接就真跑，不能才跳过”，因此在开发者模式或提权环境下会自动执行，而不是无条件跳过。

对比 0.6.0 的 `4 failed, 61 passed, 3 skipped`：4 个失败全部修复，新增 42 个回归测试，MCP 端到端测试从跳过变为通过。

**尚未验证、不得声称已验证的事项**：LLM 规划器与 LLM 补丁生成（无 API key）、Codex / Claude Desktop 宿主机注册（本机未安装客户端）、真实 conda 环境创建（本机 conda 插件与 TOS 缓存故障，见 §0 与 §3.4）、Docker/WSL 沙箱、实验复现能力。

---

## 11. 0.7.0 增量（回填）

0.7.0 在 0.6.1 基础上交付“科研实验自动复现”，并同时关闭审计 §4.4 与 §5/S2 中的两项遗留。

### 11.1 关闭的遗留项

| 编号 | 缺陷 | 处置 |
| --- | --- | --- |
| §4.4 | Agent 固定序列、无重规划 | **已修复**：新增 `runfix/agent/policy.py`，按失败类别、补丁状态、上一次验证结果决策，并输出 `stop_reason`（`needs_install_permission` / `needs_configuration_change` / `needs_write_permission_or_patch` / `no_evidence_backed_repair` / `verified_baseline` 等）。仍不能生成补丁——补丁文本只来自 LLM。 |
| §5 S2 | 环境诊断默认探测服务端解释器 | **部分修复**：`plan_reproduction` 与复现流程显式使用“所选解释器”，并在报告中写明 `python_executable` 与推断来源；`diagnose_environment` MCP 工具保持原语义（显式传参优先）。 |

### 11.2 新增能力

| 能力 | 实现 | 验证 |
| --- | --- | --- |
| 项目理解（README/入口/配置/种子/数据集/设备/CUDA 标签） | `runfix/repro/understanding.py` | `tests/test_repro_understanding.py` |
| 环境与兼容性计划（含 CUDA 轮子标签 vs 驱动） | `runfix/repro/plan.py` | 同上（supported / unsupported / unknown 三态） |
| 受控执行 + 指纹 + 产物收集 | `runfix/repro/runner.py` | `tests/test_repro_pipeline.py` |
| 指标提取与别名归一 | `runfix/repro/metrics.py` | 同上 |
| 证据阶梯与报告 | `runfix/repro/report.py` | 同上（含“退出码 0 不等于完成”专项用例） |
| CLI `runfix reproduce` | `runfix/cli.py` | 手动 + 测试 |
| MCP `plan_reproduction` / `run_reproduction`（共 22 工具） | `runfix/mcp/server.py` | `runfix.mcp.smoke --full` → 22/22 |

### 11.3 0.7.0 测试基线

```
python -m pytest -q                → 152 passed, 2 skipped
python -m runfix.mcp.smoke --full  → 22/22 tools exercised, exit 0
```

端到端演示（真实运行，非模拟）：对一个带 git 提交、README、配置与产物的演示论文项目执行
`runfix reproduce`，四种判决均按设计出现：

| 场景 | 结果 |
| --- | --- |
| `--expect accuracy=0.8 --expect f1=0.74`（与运行结果一致） | `reproduced`（退出码 0，产物 `outputs/base/metrics.json` 被收集） |
| 不声明期望值 | `metrics_recorded`，退出码 1，原因写明“无法与论文比较” |
| `--command python -c "print('done')"`（退出码 0 但无任何证据） | `executed` / `process_exited`，原因写明“零退出码不是证据” |
| `--expect accuracy=0.99`（与实测 0.80 不符） | `metrics_recorded`，原因给出 expected/observed/allowed delta |

### 11.4 仍然存在的问题（0.8.0 及以后）

- 复现流程本身不会自动构建环境（需显式 `--build-environment`），也没有“失败→调整→重试”的自动回路。
- 指标比较是“数值 vs 用户声明”，尚不能与论文图表/多随机种子方差对比。
- 根因分析仍是“规则 + 静态证据”，没有多源证据图与置信度排序（0.8.0）。
- 依赖冲突求解、Docker/WSL 隔离、基准评测（0.9.0）均未开始。
- LLM 规划器与补丁生成仍未在真实 API 下验证。

---

## 12. 0.8.0 增量（回填）

### 12.1 新增能力

| 能力 | 实现 | 验证 |
| --- | --- | --- |
| 多源证据图（异常/帧/源码/导入/依赖/已安装版本/测试） | `runfix/diagnosis/evidence.py` | `tests/test_root_cause.py`（断言 `failed_with` / `raised_in` / `imports` / `declares` 边） |
| 依赖约束分析（PEP 440 子集，无运行时依赖） | `runfix/diagnosis/dependencies.py` | `tests/test_dependencies.py`（含 `~=1.4`、`==X.Y.*`、conda pin、pip 段、清单冲突、Python 版本不兼容） |
| 根因候选排序 + 置信度 + 证据引用 | `runfix/diagnosis/rootcause.py` | `tests/test_root_cause.py`（11 类候选、排序、上限、无信号兜底） |
| Agent 采用排序结果决策 | `agent/policy.py`、`agent/tools.py` | `tests/test_agent_policy.py` |
| Docker / WSL 隔离执行 | `runfix/sandbox.py` | `tests/test_sandbox.py`（命令构造、路径改写、缺失即拒绝） |
| CLI `diagnose-root-cause` / `sandbox-info` | `runfix/cli.py` | 手动真实运行 |
| MCP `explain_root_causes`（共 23 工具） | `runfix/mcp/server.py` | `runfix.mcp.smoke --full` → 23/23 |

### 12.2 真实运行证据

对一个含 git 提交、README、manifest 的演示项目，给定真实 `ModuleNotFoundError` 日志：

```
top kind   : module_declared_missing  conf=0.95  rank=1
statement  : 'numpy' is declared by the project but is not installed in the selected interpreter.
action     : install_package -> pip install numpy>=1.20
evidence   : requirements.txt, traceback, analysis(独立分类器一致)
graph      : 11 nodes / 12 edges
relations  : failed_with=1 raised_in=1 imports=3 declares=1 resolved_to=1 has_revision=1 runs_on=1 references=3
```

### 12.3 0.8.0 测试基线

```
python -m pytest -q                → 201 passed, 2 skipped
python -m runfix.mcp.smoke --full  → 23/23 tools exercised, exit 0
```

### 12.4 明确未验证

- **真实 Docker 容器与真实 WSL 发行版从未执行**：本机 `docker` 不存在、`wsl` 无发行版，
  `runfix sandbox-info` 如实报告两者不可用。已验证的是命令构造、路径改写、缺失即拒绝、
  以及复现流程的接线；**不能**声称容器内执行已验证。
- 多轮修复中“换一个候选重试”尚未实现（当前一轮分析只采纳 top 候选）。
- 日志信号（运行输出中的线索）尚未进入证据图。

---

## 13. 0.9.0 增量（回填）

### 13.1 新增能力

| 能力 | 实现 | 验证 |
| --- | --- | --- |
| 可复现诊断基准集（11 个任务，含 1 个对照组） | `runfix/bench/suite.py` | `tests/test_bench.py`（22 项） |
| 指标计算（top-1/top-3/action/detection/control abstention/延迟/文件改动） | `runfix/bench/runner.py` | 同上 |
| 消融实验（依赖分析 / 分类器一致 / 评分排序） | `runfix/bench/runner.py` `run_ablations` | 同上 |
| 只读性验证（诊断前后全量文件哈希对比） | `runfix/bench/runner.py` `_snapshot` | 报告断言 0 个文件被修改 |
| CLI `runfix bench` | `runfix/cli.py` | 手动真实运行 + CLI 级测试 |
| 确定性评测注入点 | `rank_root_causes(environment_override=, installed_override=, ablate=)` | 基准任务不依赖宿主环境 |

### 13.2 基准发现并修复的真实缺陷

构建基准时发现：`python_syntax` 类别没有任何候选生成器，`SyntaxError` 会落到
`unclassified_error`。0.9.0 新增 `syntax_error` 候选（置信度 0.90，动作 `patch_source`），
并已由 `syntax_error` 任务覆盖。

### 13.3 本机实测数字

`runfix bench`（11 个任务，墙钟约 2.0 s）：

| 指标 | 值 |
| --- | --- |
| top-1 / top-3 根因准确率 | 1.0 / 1.0 |
| 建议动作准确率 | 1.0 |
| 检出率（10 个缺陷任务） | 1.0 |
| 对照组弃权率（1 个无缺陷任务） | 1.0 |
| 平均 top 置信度 | 0.7845 |
| 平均诊断耗时 | 0.179 s / 任务 |
| 诊断修改的文件数 | 0 |

`runfix bench --ablations`：

| 变体 | Top-1 | Δ vs full |
| --- | --- | --- |
| `full` | 1.0 | — |
| `no_dependency_analysis` | 0.6364 | **−0.3636** |
| `no_classifier_agreement` | 1.0 | 0.0 |
| `no_score_ranking` | 0.9091 | **−0.0909** |

### 13.4 0.9.0 测试基线

```
python -m pytest -q                → 223 passed, 2 skipped
python -m runfix.mcp.smoke --full  → 23/23 tools exercised, exit 0（本版本未新增 MCP 工具）
```

### 13.5 明确未测量（不宣称）

- 环境构建成功率、修复成功率、修复后测试通过率、实验复现成功率：需要联网安装、LLM 生成补丁
  或真实论文语料，本机均不具备。
- 真实科研仓库上的根因准确率：`synthetic-v1` 的任务由本项目自行构造，**不能**外推为真实世界精度。
- 与 Repo2Run / Env-Doctor / ResearchEnvBench 的对比实验：需要统一语料与硬件条件，尚未开展。
- Docker/WSL 实际执行仍未验证（本机无 Docker 守护进程、无 WSL 发行版）。
