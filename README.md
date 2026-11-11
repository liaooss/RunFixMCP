# RunFix

**面向科研代码的自主运行诊断、环境修复与实验复现 Agent。**

[![version](https://img.shields.io/badge/version-1.0.0-blue.svg)](CHANGELOG.md)
[![python](https://img.shields.io/badge/python-3.10%2B-informational.svg)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![tests](https://img.shields.io/badge/tests-249%20passed%20%2F%202%20skipped-brightgreen.svg)](.github/workflows/ci.yml)
[![mcp](https://img.shields.io/badge/MCP-23%20tools-purple.svg)](docs/MCP_SERVER.md)

> **退出码为 0，不等于实验复现成功。**
> 这是整个项目的设计前提：RunFix 的每一条结论都必须能指回它采集到的证据，
> 证据不足时它会降级并说明缺什么，而不是补一句猜测。

---

## 目录

- [一、项目简介](#一项目简介)
- [二、目标与非目标](#二目标与非目标)
- [三、核心能力](#三核心能力)
- [四、六步工作流与判定依据](#四六步工作流与判定依据)
- [五、实际效果](#五实际效果)
- [六、系统架构与代码结构](#六系统架构与代码结构)
- [七、安装与快速开始](#七安装与快速开始)
- [八、验证结果](#八验证结果)
- [九、版本演进与完成度对照](#九版本演进与完成度对照)
- [十、能力边界](#十能力边界)
- [十一、文档索引](#十一文档索引)
- [十二、贡献、安全与许可](#十二贡献安全与许可)

---

## 一、项目简介

### 1.1 一句话

**RunFix 接收一个科研代码仓库，自主完成「理解项目 → 诊断环境 → 定位根因 → 受控修复 → 运行验证 → 判定是否复现」的闭环，
并对每一步给出可核查的证据。**

### 1.2 要解决的问题：三个断层

科研人员复现一篇论文的代码时，通常不是卡在一个点上，而是连续卡在三个断层的交界处：

| 断层 | 典型症状 | 现有工具的局限 |
| --- | --- | --- |
| **环境断层** | conda/pip 装完仍然报错；CUDA 与 PyTorch 版本不匹配 | 依赖安装器只管装，不判断“装完能不能跑” |
| **代码断层** | `ModuleNotFoundError`、张量形状不匹配、库 API 变更 | 报错解释器只解释文本，不结合依赖清单与已安装版本 |
| **结果断层** | `train.py` 退出码 0，但没人知道实验是否真的完成、指标是否合理 | 评测框架通常只跑一次，把“没报错”当成成功 |

只解决其中一环的工具，会在另外两环失效。RunFix 的判断是：**这三件事必须在同一条工作流里解决**，
否则每一步的结论都无法被下一步验证。

### 1.3 定位与差异化

RunFix 不是一个“更好用的依赖安装器”，也不是“更聪明的报错解释器”，而是一个**有决策、执行、观察、
修复和验证能力的 Agent**：

```
项目理解 → 环境诊断 → 根因定位 → 方案规划 → 受控执行 → 测试验证 → 结果反馈 → 自适应调整
```

参考了三个方向，并在它们止步的地方继续：

| 参考方向 | 借鉴 | RunFix 的增量 |
| --- | --- | --- |
| Repo2Run | 科研仓库到可运行环境的构建思路 | 增加运行失败后的诊断、修复与验证闭环 |
| Env-Doctor | Python 环境与依赖问题识别 | 把环境问题与真实日志、源码、测试结果关联成**证据图** |
| ResearchEnvBench | 科研环境构建与复现能力的评测思路 | 建立自己的可复现基准与**消融实验** |

差异化可以概括为一句：

> **RunFix = 项目理解 + 环境诊断 + Agent 决策 + 安全修复 + 实验复现 + 可验证评测。**

### 1.4 项目基本信息

| 项目 | 内容 |
| --- | --- |
| 名称 | RunFix（Python 包名 `runfix-agent`） |
| 版本 | 1.0.0 |
| 语言 / 运行环境 | Python 3.10+；Windows 10/11 + Anaconda 或 venv（主目标平台），Linux 亦可 |
| 核心接口 | CLI（19 个子命令）、MCP Server（stdio，23 个工具）、本地 Web 控制台 |
| 运行时依赖 | **无**（`mcp`、`pytest` 为可选 extras）——这是刻意的，保证能在科研环境中用一个裸解释器跑起来 |
| 代码规模 | `runfix/` 47 个 Python 文件 / 约 8.3k 行；`tests/` 24 个测试文件 / 约 2.9k 行 |
| 文档 | `docs/` 下 17 份 Markdown + 1 份 MCP 配置示例 |
| 许可 | MIT |
| 仓库 | <https://github.com/liaooss/RunFixMCP> |
| 项目性质 | 开源课程作业：在原有 0.6.0 代码基线上完成审计、稳定化、功能扩展到 1.0.0，并逐项验证 |

---

## 二、目标与非目标

### 2.1 目标

1. **可追溯的诊断**：每个根因候选都列出支持事实与反证，置信度可由代码复算。
2. **可回滚的修复**：任何文件修改都是事务，失败可自动回滚，崩溃后可恢复。
3. **可判定的复现**：用七级证据阶梯代替“退出码 0”，并写明为什么没到下一级。
4. **可审计的执行**：特权操作进入 SHA-256 哈希链，权限只能收紧不能放宽。

### 2.2 非目标（明确不做）

- 不是通用依赖安装器，也不替代 conda/pip。
- 不修改系统驱动、不改全局 `PATH`、不提权。
- 不下载数据集 / 模型权重 / 许可资源，除非用户显式授权。
- 不在没有证据时宣称“已修复”或“已复现”。

---

## 三、核心能力

| 能力 | 命令 | 说明 |
| --- | --- | --- |
| 项目扫描 | `runfix scan` | 结构、入口、依赖清单、import、语法错误、git 状态 |
| 环境诊断 | `runfix diagnose` | Python / PyTorch / CUDA / GPU / nvidia-smi |
| 分层验证 | `runfix validate` | C0–C5：静态检查 → 入口运行 → 框架对齐 → 单卡 → 多卡 → 证据审计 |
| 错误分析 | `runfix analyze` | 支持 CPython 与 pytest 两种 traceback 风格，附源码上下文 |
| **证据化根因** | `runfix diagnose-root-cause` | 证据图 + 13 类候选 + 可复算置信度 |
| 补丁校验 / 应用 / 恢复 | `runfix patch-check` / `patch-apply` / `patch-recover` | 七道闸 + 事务 + 回滚 + 崩溃恢复 |
| 自主 Agent | `runfix agent [--resume]` | Plan–Act–Observe，按根因排序决策，支持断点恢复 |
| **实验复现** | `runfix reproduce` | 理解 → 计划 → 执行 → 指标 → 七级判定 |
| 隔离执行 | `runfix reproduce --sandbox docker\|wsl` | 容器 / WSL 内运行，后端缺失则拒绝而非降级 |
| MCP Server | `runfix mcp` | 23 个工具，三层权限门禁，可对接 Codex/Claude 类客户端 |
| 多项目控制台 | `runfix web` | 项目注册表 + 任务/审计/报告视图 |
| 操作审计 | `runfix audit` | SHA-256 哈希链，`--verify` 可检出篡改/删除/重排 |
| 权限审批 | `runfix approvals` | 一次性、带有效期、可撤销 |
| 断点列表 | `runfix runs` | 查看 / 恢复历史 Agent 运行 |
| 自评测 | `runfix bench` | 11 个任务的基准 + 4 组消融 |

---

## 四、六步工作流与判定依据

这是本文档的核心部分：**每一步读什么、按什么规则判、判不出来怎么办。**
完整版（含公式推导、判定树伪代码、已知弱点）见 [docs/EVIDENCE_CRITERIA.zh-CN.md](docs/EVIDENCE_CRITERIA.zh-CN.md)。

### 4.1 第一步：理解项目（只读，不 import、不执行）

| 证据 | 判定规则 | 产出 |
| --- | --- | --- |
| README | 按优先级匹配 `README.md/.rst/.txt`；只从代码块中提取 `pip/conda/python/torchrun` 开头的行，并剥掉 `$`、`>`、`PS>` 提示符 | 安装命令、运行命令、标题结构 |
| 入口脚本 | 文件名基础分 **+40**（README 提及）**+20**（`__main__` 守卫）**+15**（argparse）**+10**（分布式代码），取前 10 名，每项带逐条证据 | `entrypoints[]` |
| 配置文件 | 扫 `configs/ config/ conf/ cfg/ options/ experiments/` 与根目录；深度 ≤ 4、单文件 ≤ 300 KB、最多 60 个 | `configs[]`、`seeds[]`、训练参数 |
| 配置解析 | `json` / `tomllib` / 可选 PyYAML；**无 PyYAML 时退化为平铺键扫描**（缩进 ≤ 2 的 `key: value`） | 每个配置记录用了哪个解析器 |
| 数据集 | 键名匹配 `data/dataset/corpus/image_dir/dataset_dir`，排除 `out/output/log/save/ckpt/checkpoint/result/weight` 等输出键；值为路径时解析为绝对路径并检查存在性 | `datasets[]`、`missing_datasets[]` |
| 设备需求 | 代码出现 `.cuda()` / `torch.cuda` / DDP，或 README 提到 GPU | `device.gpu_code_detected` |
| CUDA 线索 | 依赖与 README 中的 `cu118`/`cu121` 标签、`download.pytorch.org/whl/cuXXX` 地址 | `environment.cuda_tags` |
| Python 要求 | `requires-python` 或 `environment.yml` 的 `- python=3.x` | `environment.python_requirement` |

**判不出来时**：没有 README、没有入口、没有依赖清单都写进 `warnings`；数据集缺失写进 `missing_datasets` 且**不会自动下载**。

> 这一步只读文件。基准测试用“诊断前后全量文件 SHA-256 比对”证明未改动任何文件（`files_modified_by_diagnosis` 必须为 0）。

### 4.2 第二步：诊断环境（用一个探针子进程，不装包）

探针采集：Python 版本与路径、平台、8 个常见科学计算包版本、`torch` 是否可导入及其
`cuda_available` / `cuda_version` / GPU 数量与名称、`nvidia-smi` 是否可用及输出。

| 判定项 | 依据 | 结论 |
| --- | --- | --- |
| Python 兼容 | 解释器版本与 `requires-python` 按数字元组比较 | `true` / `false` / `null`（无法判断） |
| CUDA 兼容 | 声明的轮子标签（`cu121` → 12.1）与驱动报告的 `CUDA Version: X.Y` 比较 | 驱动 ≥ 所需 → `supported`；驱动 < 所需 → `unsupported`；**缺任一输入 → `unknown`** |
| 环境清单 | 固定优先级 `environment.yml` → `environment.yaml` → `requirements.txt` → `pyproject.toml` | `manager` / `manifest` |
| 数据集 | 第一步的 `missing_datasets` 是否为空 | `ready` / `missing` / `unknown` |
| GPU 风险 | 代码需要 CUDA，但探针看不到 GPU | `at_risk` + 提示 |

**关键取舍**：CUDA 兼容性不做猜测。没有声明轮子标签、或拿不到驱动版本，答案就是“无法判定”并说明原因。

### 4.3 第三步：定位根因（证据图 + 可复算置信度）

先把异常、调用帧、源码文件、import、声明约束、实际版本、候选测试连成**证据图**：

- 节点：`project`、`git_revision`、`environment`、`framework`、`dependency`、`installed_package`、`module`、`source_file`、`test_file`、`exception`
- 边：`failed_with`、`raised_in`（带行号与函数）、`imports`、`references`、`declares`、`resolved_to`、`tested_by`、`has_revision`、`runs_on`、`provides`

再生成 **13 类根因候选**，每类都带“陈述 + 支持证据 + 反证 + 一个具体动作”：

| 候选 | 触发 | 建议动作 |
| --- | --- | --- |
| `module_declared_missing` | 报错点名某模块，清单声明了但它没装 | `install_package` |
| `module_missing_undeclared` | 报错点名某模块，任何清单都没声明 | `declare_and_install` |
| `declared_package_not_installed` | 无报错，但清单里有包没装 | `install_package` |
| `version_constraint_unsatisfied` | 安装版本落在约束外 | `install_constraint` |
| `manifest_disagreement` | 两份清单对同一包约束冲突 | `choose_manifest`（交人决定） |
| `python_incompatible` | `requires-python` 与解释器不符 | `select_interpreter` |
| `cuda_driver_mismatch` | 声明 `cuXXX` 高于驱动支持 | `select_cuda_wheel` |
| `cuda_unavailable` | 代码要 CUDA 但看不到 GPU | `run_on_cpu_or_gpu_host` |
| `dataset_missing` | 配置里的数据路径不存在 | `configure_data` |
| `api_signature_mismatch` | `unexpected keyword argument` / `has no attribute` | `patch_source` |
| `syntax_error` | 语法错误 | `patch_source` |
| `test_assertion_failure` | 断言失败且有项目内调用帧 | `patch_source` |
| `unclassified_error` | 以上都不成立 | `collect_more_evidence` |

**置信度公式（确定性、可复算）**：

```
置信度 = 基础分(按候选类型, 0.30–0.90)
       + min(0.09, 0.03 × (证据条数 − 1))
       + 一致加分（traceback 分类器给出同一类 +0.06；CUDA 驱动判定一致 +0.05）
       − 0.12 × 反证条数
       裁剪到 [0.05, 0.98]

排序分 = 置信度 + 0.01 × min(5, 证据条数)
```

**实算示例**（演示项目的真实输出）：`module_declared_missing` = 基础 0.86 + 0.03（2 条证据）+ 0.06（分类器一致）= **0.95**。

**什么时候不给结论**：没有任何规则命中时返回 `unclassified_error`（0.30）并列出建议补采的证据。
基准集里专门有一个**无缺陷对照任务**，要求的正确行为就是弃权——只会“总能找出点问题”的系统并不可信。

### 4.4 第四步：受控修复（只做有证据、可回滚、可审计的修改）

**三道门禁（缺一不可）**：服务端开关（`--enable-write` 等）→ 单次调用确认（`confirm_write`）→
可选的一次性、带有效期审批（`--require-approval`）。

**七道补丁闸（全部通过才落盘）**：

1. 项目内相对路径，禁止 `..`、绝对路径、盘符；
2. 禁止修改 `.git` 与 `.runfix`；
3. 扩展名白名单；
4. 逐段符号链接检查，拒绝穿越；
5. 拒绝二进制补丁、子模块补丁、符号链接模式补丁；
6. `git apply --check` 必须成功；
7. 在**隔离临时树**里应用后，改动的 Python 文件必须能 `ast.parse`。

**事务与回滚判定**：

| 场景 | 判定依据 | 结果 |
| --- | --- | --- |
| 应用 | 事务 ID + 原文件备份 + 前后 SHA-256 + 项目级跨进程锁 | `applied` |
| 回滚 | **所有**目标文件当前哈希必须等于记录里的 `sha256_after` | 全等才回滚，否则拒绝并列出冲突 |
| 崩溃后（`applying`） | 哈希 == `sha256_after` → 恢复；== `sha256_before` → 不动；其它 → 有人改过 | `recovered_from_interrupt` / `interrupted_needs_review` |

**应用成功不等于修复成功**——必须过第五步。

**Agent 何时修、何时停**：策略读的是排名第一的根因动作，而不是固定流程。

| 排名第一的动作 | 行为 |
| --- | --- |
| `install_package` / `install_constraint` / `declare_and_install` | 有安装权限 → 建隔离环境；没有 → 停在 `needs_install_permission` |
| `patch_source` | 有写权限 → 校验 → 应用 → 验证；没有 → 停在 `needs_write_permission_or_patch` |
| `select_interpreter` / `configure_data` / `select_cuda_wheel` / `run_on_cpu_or_gpu_host` / `choose_manifest` | 停在对应原因，交人决策 |
| `collect_more_evidence` | 停在 `needs_more_evidence` |

### 4.5 第五步：运行验证（拿到客观记录）

显式 argv、`shell=False`、超时、stdout/stderr 合并写入 `.runfix/repro/<run-id>/run.log`。

**测试选择**（先跑哪些）：+100 测试文件本身出现在 traceback；+15 测试源码引用了报错源文件的相对路径；
+10 静态 import 了 traceback 中的模块；+5 同名 basename。先跑相关测试，通过后再跑**全量**；
无法确定关联就直接跑全量。

**运行状态机（五态，互斥）**：

| 状态 | 依据 |
| --- | --- |
| `failed_to_start` | 进程启动即失败 |
| `timed_out` | 超过超时被杀 |
| `failed` | 退出码 ≠ 0 |
| `completed` | 退出码 0 **且**有完成证据 |
| `process_exited` | 退出码 0 **但没有任何**完成证据 |

**完成证据**（三者之一即可）：解析到 ≥1 个指标观测点；命中 13 个完成标记之一
（`training complete`、`training finished`、`all epochs`、`final results`、`final test`、
`best model saved`、`saved checkpoint`、`evaluation results`、`test results`、`experiment finished`、
`reproduction complete` 等）；或匹配调用方提供的 `--completion-regex`。

同时记录：git commit / branch / dirty、依赖清单 SHA-256、解释器与 torch/CUDA 版本、GPU 名称、
运行期间新建或修改的文件（产物）。

### 4.6 第六步：判定是否复现（七级证据阶梯）

**这是“是否复现”的正式判定**：取当前证据能支撑的**最高一级**，并写明为什么没有更高。

| 级别 | 进入条件（缺一不可） |
| --- | --- |
| `inspected` | 读过仓库 |
| `plan_ready` | 有环境 / 运行计划 |
| `executed` | 命令真的跑过（超时、失败也算） |
| `completed` | 退出码 0 **且**有完成证据 |
| `metrics_recorded` | 从输出解析出最终指标 |
| `metrics_matched` | 声明的目标值**全部**匹配 |
| `reproduced` | 指标匹配 **且** 运行期间工作区干净 **且** 有 git commit |

**指标比较规则**：名称按别名表归一（`acc → accuracy`、`f1_score → f1` 等）；
目标值 |v| ≤ 1 用**绝对容差 0.02**，否则用**相对容差 2%**；每个目标值为
`match` / `mismatch` / `missing`（输出里没有该指标）三态，**全部 match** 才升级。

**判定树**（对应 `derive_verdict`）：

```
没执行                       → plan_ready
启动失败                     → 停在 plan_ready，附启动错误
超时 / 失败 / 零退出码无输出   → executed，并写明具体原因
退出码 0 且有完成证据         → completed
解析到指标                   → metrics_recorded
未声明目标值                 → 停在 metrics_recorded，写明"无法与论文比较"
目标值全部匹配               → metrics_matched
工作区脏 / 无 git 提交        → 停在 metrics_matched，写明原因
否则                        → reproduced
```

**`reproduced` 的准确含义**：

- **是**：在这台机器、这个提交、这套依赖版本下，文档里的命令跑通了，解析出的指标满足你声明的目标值，工作区没有未提交改动。
- **不是**：论文结论正确、方法优于基线、结果在别的机器上可重复、统计显著。这些写入报告的 `limitations`，不用措辞掩盖。

---

## 五、实际效果

以下都是本仓库真实运行的输出，不是示意图。

**① 诊断**：项目声明了 `numpy` 但没安装——

```text
$ runfix diagnose-root-cause . --traceback-file error.log
top kind   : module_declared_missing      conf=0.95  rank=1
statement  : 'numpy' is declared by the project but is not installed in the selected interpreter.
action     : install_package -> pip install numpy>=1.20
evidence   : requirements.txt, traceback, analysis（独立分类器一致）
graph      : 11 nodes / 12 edges
```

**② 复现**：同一个项目，声明论文里的指标——

```text
$ runfix reproduce . --allow-execution --expect accuracy=0.8 --expect f1=0.74
verdict      : reproduced
run status   : completed (rc=0, 0.53s)
comparison   : accuracy match, f1 match
artifacts    : outputs/base/metrics.json
git commit   : 4ea2158 dirty=False
```

**③ 证据不足时明确降级**：

| 情况 | 判定 | 报告里的理由 |
| --- | --- | --- |
| 不声明目标指标 | `metrics_recorded` | “未声明目标值，无法与论文比较” |
| 进程退出码 0 但没有任何输出 | `executed` / `process_exited` | “零退出码本身不是实验完成的证据” |
| 声明 `accuracy=0.99`，实测 `0.80` | `metrics_recorded` | “expected 0.99, observed 0.8（允许误差 0.02）” |
| 工作区有未提交改动 | `metrics_matched` | “结果无法绑定到某个提交” |

---

## 六、系统架构与代码结构

### 6.1 分层结构

```
┌──────────────────────────── 交互层 ────────────────────────────┐
│  CLI（19 子命令）   MCP Server（stdio, 23 工具）   Web 控制台    │
└───────────────┬───────────────────────────┬────────────────────┘
                │                           │
┌───────────────▼───────────┐  ┌────────────▼────────────────────┐
│      Agent 层              │  │        复现层 (repro)            │
│  policy(策略) brain(LLM)   │  │ understand → plan → run →        │
│  tools(白名单) core(循环)  │  │ metrics → report(证据阶梯)       │
└───────────────┬───────────┘  └────────────┬────────────────────┘
                │                           │
┌───────────────▼───────────────────────────▼────────────────────┐
│                      诊断层 (diagnosis)                         │
│  evidence(证据图)  dependencies(PEP 440 子集)  rootcause(排序)  │
└───────────────┬────────────────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────────────────┐
│  基础能力层                                                     │
│  scanner  diagnostics  analyzer  validation  patches            │
│  environments  task_manager  sandbox  test_selection            │
└───────────────┬────────────────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────────────────┐
│  产品支撑层                                                     │
│  audit(哈希链)  approvals(一次性授权)  checkpoint(断点)  bench   │
└─────────────────────────────────────────────────────────────────┘
```

数据流：`项目路径 → scanner/diagnostics → 失败日志 → analyzer → diagnosis(证据图+根因) → 动作(安装/补丁/运行) → 报告 + 审计`

### 6.2 目录结构

```
runfix/
  agent/          决策与执行：policy(策略) brain(LLM) tools(白名单) core(循环)
  diagnosis/      证据图、依赖求解、根因排序
  repro/          项目理解、环境计划、执行、指标、报告
  bench/          基准集、指标、消融、报告
  mcp/            MCP Server（23 工具）与端到端冒烟
  web/            多项目控制台
  audit.py        哈希链审计
  approvals.py    一次性授权
  checkpoint.py   断点恢复
  patches.py      事务式补丁
  sandbox.py      Docker/WSL 隔离
  scanner.py diagnostics.py analyzer.py validation.py environments.py task_manager.py
docs/             17 份设计 / 使用 / 审计文档
tests/            24 个测试文件
scripts/          一键安装、升级、诊断
```

---

## 七、安装与快速开始

### 7.1 安装

Windows 10/11 + Python 3.10+（Anaconda 或普通 Python）。脚本**不会**装进你启动它的那个解释器。

```powershell
# 虚拟环境方案（.venv/）
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1

# 或 Conda 前缀环境（.conda_env/，不动你的 base 环境）
.\scripts\install_conda.ps1

# 一键体检：Python / git / conda / GPU / MCP 握手
.\scripts\diagnose_windows.ps1
```

安装脚本最后会跑一次真实的 MCP stdio 自检，所以“安装成功”是验证过的成功。手动等价步骤：

```powershell
python -m pip install -e ".[dev,mcp]"
python -m runfix --version        # RunFix 1.0.0
python -m pytest -q               # 249 passed, 2 skipped
python -m runfix.mcp.smoke --full # 23/23 工具走真实协议
```

### 7.2 快速开始

```powershell
# 1. 只看不跑：读 README、识别入口/配置/数据集/CUDA 要求，输出环境与运行计划
runfix reproduce "D:\research\paper-code" --plan-only

# 2. 拿真实报错做证据化诊断
runfix diagnose-root-cause "D:\research\paper-code" --traceback-file .\error.log

# 3. 在明确授权下让 Agent 自主工作（可断点恢复）
runfix agent "D:\research\paper-code" --allow-project-code --allow-write --use-llm
runfix agent "D:\research\paper-code" --resume <run_key>

# 4. 跑文档里的实验，并与论文指标对比
runfix reproduce "D:\research\paper-code" --allow-execution --expect accuracy=0.812

# 5. 接入 MCP 客户端（Codex / Claude Desktop 等）
runfix mcp --allowed-root "D:\research" --enable-execution --enable-write

# 6. 多项目管理与审计
runfix web --state .\.runfix-web
runfix audit "D:\research\paper-code" --verify
```

---

## 八、验证结果

全部为本机实测（Windows 11、Python 3.11、`mcp==2.3.0`）。

| 检查项 | 结果 |
| --- | --- |
| 测试套件 | **249 passed, 2 skipped** |
| MCP 协议 | **23/23 工具**真实 stdio 调用，exit 0 |
| 可复现基准 | 11 个任务，top-1 准确率 **1.0**，诊断修改文件数 **0** |
| 消融：去掉依赖分析 | top-1 1.0 → 0.6364（**−36.4 个百分点**） |
| 消融：去掉评分排序 | top-1 1.0 → 0.9091（**−9.1 个百分点**） |
| 审计链 | 真实三类操作后 `{"ok": true, "checked": 3}` |
| 打包 | wheel 在干净 venv 安装，`runfix --version` → 1.0.0 |
| 一键安装 | 真实执行（Python 3.14 + mcp 2.3.0），退出码 0 |
| Web 控制台 | 真实 HTTP 调用 health/projects/tasks/audit/首页全部通过 |

2 个跳过项需要 Windows 符号链接特权（`SeCreateSymbolicLinkPrivilege`），在具备该权限的账户上会自动执行。

**自己验证一遍**：

```powershell
git clone https://github.com/liaooss/RunFixMCP.git
cd RunFixMCP
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m runfix.mcp.smoke --full
.\.venv\Scripts\python.exe -m runfix bench --ablations
```

三步分别验证：单元/集成正确性、协议级可用性、自身诊断能力的量化与消融。

---

## 九、版本演进与完成度对照

### 9.1 版本演进

| 版本 | 主题 | 关键交付 |
| --- | --- | --- |
| 0.1–0.6 | 既有基线（接手前） | 扫描、诊断、Agent 循环、MCP Server、任务管理、事务补丁 |
| **0.6.1** | 稳定性与真实部署 | 修复 13 项缺陷：MCP v2 权限语义、Windows conda 解释器、报告换行、C5 假审计、pytest traceback、事务并发与崩溃恢复、任务退出码跨重启 |
| **0.7.0** | 科研实验自动复现 | `repro` 全链路 + 七级证据阶梯；Agent 改为观察驱动策略 |
| **0.8.0** | 深度诊断与隔离 | 证据图、PEP 440 依赖求解、13 类根因排序、Docker/WSL 隔离 |
| **0.9.0** | 基准与消融 | `synthetic-v1` 11 任务、指标、消融；基准自己抓出 `SyntaxError` 未归类的缺陷 |
| **1.0.0** | 产品化 | 哈希链审计、断点恢复、一次性审批、多项目控制台、wheel 安装验证 |

### 9.2 任务书完成度对照

| 任务书要求 | 状态 | 证据 |
| --- | --- | --- |
| ① 全面审计源码，输出 `docs/CURRENT_STATE_AUDIT.md` | ✅ 完成 | 40 KB 审计报告；实测推翻交接文档“67 passed”，实为 4 failed / 61 passed / 3 skipped |
| ② 0.6.1 稳定性（真实环境、MCP 握手、任务恢复、事务并发、回归测试） | ✅ 完成 | 13 项缺陷全修；MCP 真实握手通过；105 passed |
| ③ 0.7.0 科研实验自动复现 Agent | ✅ 完成 | `runfix reproduce` 全链路；四种判决实测（reproduced / metrics_recorded / executed / mismatch） |
| ④ 0.8.0 深度诊断与自适应修复 | ✅ 完成 | 证据图 + 13 类根因 + 依赖求解；Agent 按根因动作决策 |
| ⑤ 0.8.0 Docker/WSL 隔离 | ⚠️ 代码完成，容器内执行未验证 | 命令构造、路径改写、缺失即拒绝已测；本机无 Docker 守护进程、无 WSL 发行版 |
| ⑥ 0.9.0 基准评测 | ⚠️ 部分完成 | 自建 `synthetic-v1` + 消融已完成；**与参考项目的对比实验需要统一真实语料，未做** |
| ⑦ 1.0.0 产品化（审计、审批、断点、多项目） | ✅ 完成 | 审计链 `--verify` 通过；断点恢复不重放副作用；审批一次性；控制台真实 HTTP 验证 |
| ⑧ 完整文档与测试 | ✅ 完成 | 17 份文档；249 个测试；中英判定依据文档 |

---

## 十、能力边界

### 10.1 明确未验证（不做宣称）

| 项目 | 原因 |
| --- | --- |
| 真实 Docker 容器 / WSL 发行版内执行 | 开发机无守护进程、无发行版；`runfix sandbox-info` 如实报告两者不可用 |
| LLM 规划与 LLM 生成补丁 | 无 API key；离线策略已验证，LLM 路径未实测 |
| 真实已发表论文仓库上的准确率 | 基准集为自建，满分是回归护栏，不是真实世界精度 |
| 浏览器级 UI 与可访问性 | 只做了 HTTP 层验证，未在浏览器中人工点检 |
| 与 Repo2Run / Env-Doctor / ResearchEnvBench 的对比实验 | 需要统一真实语料与硬件条件 |

### 10.2 方法论边界

- 完成标记是启发式的：只写文件、不打印任何东西的项目会被判为 `process_exited`，此时用 `--completion-regex` 兜底。
- 指标只从 stdout/stderr 解析：只写 TensorBoard/W&B 的项目采集不到指标。
- 别名表有限：冷门指标名会表现为 `missing` 而不是 `mismatch`。
- 目标值必须由人给出：RunFix 不读论文 PDF，不替你决定“这篇论文的 accuracy 应该是多少”。
- 一次运行不等于可重复性：没有多种子方差分析。
- 干净 git 只保证可追溯，不保证科学正确。

---

## 十一、文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/INDEX.md](docs/INDEX.md) | 文档总索引 |
| [docs/EVIDENCE_CRITERIA.zh-CN.md](docs/EVIDENCE_CRITERIA.zh-CN.md) | **六步判定依据完整版**：规则、公式、判定树、已知弱点 |
| [docs/PROJECT_BRIEF.zh-CN.md](docs/PROJECT_BRIEF.zh-CN.md) | 项目简介详细版（背景、架构、版本、验证、边界） |
| [docs/CURRENT_STATE_AUDIT.md](docs/CURRENT_STATE_AUDIT.md) | 接手时的逐项审计与缺陷清单（含 file:line） |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 分层结构、数据流、产品面 |
| [docs/DIAGNOSIS.md](docs/DIAGNOSIS.md) | 证据图、依赖求解、置信度模型 |
| [docs/REPRODUCTION.md](docs/REPRODUCTION.md) | 七级证据阶梯与每一级要求 |
| [docs/BENCHMARK.md](docs/BENCHMARK.md) | 评测集、指标、消融、未测量项 |
| [docs/SAFETY.md](docs/SAFETY.md) | 权限模型与失败处理 |
| [docs/AUDIT_LOG.md](docs/AUDIT_LOG.md) | 防篡改审计 |
| [docs/MCP_SERVER.md](docs/MCP_SERVER.md) | 23 个工具、门禁、Codex 配置 |
| [docs/WEB_UI.md](docs/WEB_UI.md) | 多项目控制台 |
| [docs/AGENT_DESIGN.md](docs/AGENT_DESIGN.md) | 规划器、策略、工具注册、停止原因 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 已完成与仍未完成的路线 |
| [CHANGELOG.md](CHANGELOG.md) | 各版本变更记录 |

---

## 十二、贡献、安全与许可

- **贡献**：见 [CONTRIBUTING.md](CONTRIBUTING.md)。本项目的核心规则是“无证据不下结论”，
  PR 需要说明改了什么、如何复现验证、以及**没有**验证什么。
- **安全**：见 [SECURITY.md](SECURITY.md)。安全问题请走私下渠道，不要开公开 Issue。
- **行为准则**：见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- **许可**：MIT，见 [LICENSE](LICENSE)；第三方组件与思路来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- **引用**：见 [CITATION.cff](CITATION.cff)。

---

*RunFix 是一个助手。它缩短“仓库跑不起来”到“实验复现成功”之间的距离，并且如实说明这段距离还有哪一段没有走完。*
