# 判定依据：六步流程，每一步凭什么下结论

本文回答两个问题：**RunFix 每一步用的是什么证据？到哪一步可以说什么、不能说什么？**

核心原则只有三条：

1. **每一条结论都要能指回它的证据。** 报告里的置信度、状态、判定，都可以用同一份代码复算。
2. **证据不足时降级，而不是补一句猜测。** 最低一级叫 `inspected`，最高一级叫 `reproduced`，中间每一级都有明确的进入条件。
3. **退出码为 0 不等于实验成功，跑完不等于复现。** 这是整个判定体系的设计前提。

---

## 第一步：理解项目

**目标**：在不运行任何项目代码的前提下，回答“这个仓库想让我怎么装、怎么跑”。

| 证据 | 提取规则 | 产出字段 |
| --- | --- | --- |
| README | 依次匹配 `README.md` / `README.rst` / `README.txt` / `readme.md` / `README.MD` / `Readme.md`；解析标题、代码块内的安装与运行命令（会剥掉 `$`、`>`、`PS>`、`#` 提示符） | `readme.install_commands`、`readme.run_commands`、`readme.headings` |
| 入口脚本 | 文件名基础分 + README 提及 +40 + 有 `__main__` 守卫 +20 + 用 argparse +15 + 含分布式训练 +10 | `entrypoints[]`（含 `score` 与逐条 `evidence`） |
| 配置 | 扫描 `configs/ conf/ config/ cfg/ options/ experiments/` 与根目录 `config.*`；`.yaml/.yml/.json/.toml`；深度 ≤ 4、单文件 ≤ 300 KB、最多 60 个 | `configs[]`、`seeds[]`、`training_parameters{}` |
| 配置解析 | `json` / `tomllib` / 可选 PyYAML；**没有 PyYAML 时**退化为平铺键扫描（缩进 ≤ 2 的 `key: value`） | 每个配置的 `parser` 字段会写明用了哪种 |
| 数据集 | 键名匹配 `data/dataset/corpus/image_dir/dataset_dir`，排除输出类键（`out/output/log/save/ckpt/checkpoint/result/weight/exp`），值必须像路径，解析后检查是否存在 | `datasets[]`、`missing_datasets[]` |
| 设备需求 | 代码中出现 `.cuda()`、`torch.cuda`、`device='cuda'`、`DistributedDataParallel`，或 README 提到 CUDA/GPU | `device.gpu_code_detected`、`device.evidence[]` |
| CUDA 版本线索 | 依赖与 README 中的 `cu118`/`cu121` 形式标签、`download.pytorch.org/whl/cuXXX` 索引地址 | `environment.cuda_tags` |
| Python 要求 | `pyproject.toml` 的 `requires-python`，或 `environment.yml` 的 `- python=3.x` | `environment.python_requirement` |

**会明确降级的情况**：没有 README → `readme.warning`；没有识别出入口 → `warnings` 里写明“必须显式提供命令”；没有依赖清单 → `warnings` 里写明；配置里的数据集路径不存在 → 进入 `missing_datasets`，且**不会**自动下载。

> 这一步只读文件，不 import 目标模块、不执行目标命令。基准测试用“诊断前后全量文件 SHA-256 比对”来证明这一点（`files_modified_by_diagnosis` 必须为 0）。

---

## 第二步：诊断环境

**目标**：回答“用这个解释器，这个项目跑得起来吗”。

用一个子进程探针（不改环境、不装包）采集：Python 版本与路径、平台、8 个常见科学计算包的版本、`torch` 是否可导入及其 `cuda_available` / `cuda_version` / GPU 数量与名称、`nvidia-smi` 是否可用及其输出。

| 判定项 | 依据 | 结论 |
| --- | --- | --- |
| Python 兼容 | 把解释器版本与 `python_requirement` 按数字元组比较 | `satisfied` = `true` / `false` / `null`（无法判断） |
| CUDA 兼容 | 声明的轮子标签（`cu121` → 12.1）与驱动报告的 `CUDA Version: X.Y` 比较：驱动 ≥ 所需 → **supported**；驱动 < 所需 → **unsupported**；任一缺失 → **unknown** | `cuda.verdict` + `cuda.reason` |
| 环境清单 | 固定优先级：`environment.yml` → `environment.yaml` → `requirements.txt` → `pyproject.toml` | `steps[environment].manager/manifest` |
| 数据集 | 第一步的 `missing_datasets` 是否为空 | `ready` / `missing` / `unknown` |
| GPU 风险 | 代码需要 CUDA 但解释器看不到 GPU → `at_risk`，并给出提示 | `steps[gpu].warning` |

**关键取舍**：CUDA 兼容性**不做猜测**。没有声明轮子标签，或拿不到驱动版本，结论就是 `unknown` 并说明原因，而不是“大概兼容”。

输出结构里每一步都带 `requires_authorization`，`requires_authorization` 顶层汇总为三项：装环境、跑代码、下载数据。

---

## 第三步：定位根因

**目标**：把“报错了”变成“最可能是什么原因、凭什么、下一步做什么”。

### 3.1 先建证据图

节点：`project`、`git_revision`、`environment`、`framework`、`dependency`（声明的约束）、`installed_package`（实际安装的版本）、`module`（import）、`source_file`、`test_file`、`exception`。

边：`failed_with`（项目→异常）、`raised_in`（异常→文件，带行号与函数）、`imports`（文件→模块）、`references`、`declares`（项目→依赖约束）、`resolved_to`（约束→实际版本）、`tested_by`（源文件→测试文件）、`has_revision`、`runs_on`、`provides`。

### 3.2 依赖判定

把清单里声明的约束与探针拿到的实际版本逐条比对（自实现的 PEP 440 子集：`==` `!=` `>=` `<=` `>` `<` `~=` `===` `==X.Y.*`）：

| 结论 | 触发条件 | 建议动作 |
| --- | --- | --- |
| `not_installed` | 声明了，但该解释器里没有 | 安装声明的版本 |
| `version_unsatisfied` | 装了，但落在约束外 | 安装声明范围 |
| `manifest_disagreement` | 两份清单对同一包给出不同约束 | 由人决定以哪份为准（RunFix 不猜） |
| `python_incompatible` | `requires-python` 与解释器不符 | 换解释器重建环境 |
| `version_unparsable` | 版本串无法解析 | 仅报告，不判定 |

### 3.3 根因候选（11 类）与置信度

| 候选 | 触发证据 | 建议动作 |
| --- | --- | --- |
| `module_declared_missing` | 报错点名某模块，清单声明了它，但未安装 | `install_package` |
| `module_missing_undeclared` | 报错点名某模块，任何清单都没声明 | `declare_and_install` |
| `declared_package_not_installed` | 无报错，但清单里有包没装 | `install_package` |
| `version_constraint_unsatisfied` | 安装版本违反约束 | `install_constraint` |
| `manifest_disagreement` | 两份清单冲突 | `choose_manifest` |
| `python_incompatible` | Python 版本不符 | `select_interpreter` |
| `cuda_driver_mismatch` | 声明 `cuXXX` 高于驱动支持 | `select_cuda_wheel` |
| `cuda_unavailable` | 代码要 CUDA，但看不到 GPU | `run_on_cpu_or_gpu_host` |
| `dataset_missing` | 配置里的数据路径不存在 | `configure_data` |
| `api_signature_mismatch` | `unexpected keyword argument` / `has no attribute` | `patch_source` |
| `syntax_error` | 语法错误 | `patch_source` |
| `test_assertion_failure` | 断言失败且有项目内调用帧 | `patch_source` |
| `unclassified_error` | 以上都不成立 | `collect_more_evidence` |

**置信度公式**（确定性，可复算）：

```
confidence = 基础分(按候选类型, 0.30–0.90)
           + min(0.09, 0.03 × (证据条数 − 1))
           + 一致性加分（traceback 分类器给出同一类时 +0.06；CUDA 驱动判定一致时 +0.05）
           − 0.12 × 反证条数
           裁剪到 [0.05, 0.98]
```

**实算示例**（演示项目的真实输出）：`module_declared_missing` 基础分 0.86，证据 2 条（清单 1 条 + traceback 1 条）→ `+0.03`，分类器一致 → `+0.06`，合计 **0.95**。

**排序**：`score = confidence + 0.01 × min(5, 证据条数)`，降序。

**什么时候不给结论**：没有任何候选成立时返回 `unclassified_error`（置信度 0.30）并列出建议补采的证据，而不是编一个高置信度的猜测。基准集中专门有一个**无缺陷对照任务**，要求的正确行为就是弃权。

---

## 第四步：受控修复

**目标**：只做有证据、可回滚、可审计的修改。

### 4.1 三道门禁（缺一不可）

| 层 | 依据 |
| --- | --- |
| 服务端开关 | `--enable-write` / `--enable-execution` / `--enable-install`（MCP），或 CLI 的 `--allow-write` / `--allow-project-code` / `--allow-install` |
| 单次调用确认 | `confirm_write=true` / `confirm_execution=true` / `confirm_install=true` |
| 审批（可选） | `--require-approval` 时，还要消费一张未过期、还有剩余次数的一次性授权 |

### 4.2 补丁的七道闸（全部通过才落盘）

1. 路径必须是项目内相对路径，禁止 `..`、绝对路径、盘符前缀；
2. 禁止改 `.git` 与 `.runfix`；
3. 扩展名白名单（`.py .pyi .toml .txt .yaml .yml .json .ini .cfg .md .rst .sh .ps1`）；
4. 逐段检查符号链接，拒绝穿越；
5. 拒绝二进制补丁、子模块补丁、符号链接模式补丁；
6. `git apply --check` 必须成功；
7. 在**隔离临时树**里应用后，改动过的 Python 文件必须能 `ast.parse`。

### 4.3 事务与回滚的判定

| 场景 | 判定依据 | 结果 |
| --- | --- | --- |
| 应用 | 事务 ID + 原文件备份 + 前后 SHA-256 + 项目级跨进程锁 | `applied` |
| 回滚 | **所有**目标文件的当前哈希都必须等于记录里的 `sha256_after` | 全等才回滚，否则拒绝并列出冲突文件 |
| 崩溃后（`applying`） | 当前哈希 == `sha256_after` → 已应用，恢复备份；== `sha256_before` → 未落地，保持不动；其它 → 有人改过 | `recovered_from_interrupt` / `interrupted_needs_review` |

**修复是否"成功"不在这里判定**——应用成功只代表文件改了，必须过第五步。

### 4.4 Agent 什么时候修、什么时候停

策略读的是**排序第一的根因的动作**，不是固定流程：

| 排名第一的根因动作 | Agent 行为 |
| --- | --- |
| `install_package` / `install_constraint` / `declare_and_install` | 有安装权限 → 建隔离环境；没有 → 停在 `needs_install_permission` |
| `patch_source` | 有写权限 → 校验→应用→验证；没有 → 停在 `needs_write_permission_or_patch` |
| `select_interpreter` / `configure_data` / `select_cuda_wheel` / `run_on_cpu_or_gpu_host` / `choose_manifest` | 停在对应原因（`needs_interpreter_selection` 等），交给人决策 |
| `collect_more_evidence` | 停在 `needs_more_evidence` |

每个决策都记 `rationale` 与 `strategy`（如 `root_cause:test_assertion_failure`），所以报告能解释"为什么走了这一步"。

---

## 第五步：运行验证

**目标**：拿到"这次执行到底发生了什么"的客观记录。

### 5.1 执行规则

显式 argv 列表、`shell=False`、带超时、stdout/stderr 合并写入 `.runfix/repro/<run-id>/run.log`。

### 5.2 测试选择的是"先跑哪些"

| 打分 | 依据 |
| --- | --- |
| +100 | 测试文件本身出现在 traceback 里 |
| +15 | 测试源码里引用了报错源文件的相对路径 |
| +10 | 测试文件静态 import 了 traceback 中的模块 |
| +5 | 测试文件 import 了同名 basename |

先跑相关测试，通过后再跑**全量**；若没有任何关联证据，直接跑全量。这是优先级启发式，不是覆盖率证明——报告里也这么写。

### 5.3 运行状态机（五态，互斥）

| 状态 | 依据 |
| --- | --- |
| `failed_to_start` | 进程启动即抛错 |
| `timed_out` | 超过超时被杀 |
| `failed` | 退出码 ≠ 0 |
| `completed` | 退出码 = 0 **且**有完成证据 |
| `process_exited` | 退出码 = 0 **但没有**任何完成证据 |

**完成证据**（三者之一即可）：

1. 解析到至少 1 个指标观测点；
2. 输出里出现完成标记（`training complete`、`training finished`、`all epochs`、`final results`、`final test`、`best model saved`、`saved checkpoint`、`evaluation results`、`test results`、`experiment finished`、`reproduction complete` 等 13 个）；
3. 调用方提供了 `--completion-regex` 且匹配成功。

### 5.4 同时记录的可追溯信息

git commit / branch / origin / 是否 dirty（排除 `.runfix` 与 `__pycache__`）、依赖清单 SHA-256、解释器路径与版本、torch 版本与 CUDA、GPU 名称、运行期间新建或修改的文件（产物）。

---

## 第六步：判定是否复现

**这是"判定是否复现"的正式答案。** 结论是一个**七级阶梯**，取"当前证据能支撑的最高一级"，并记录为什么没有更高。

| 级别 | 进入条件（缺一不可） |
| --- | --- |
| `inspected` | 读过仓库 |
| `plan_ready` | 有环境/运行计划 |
| `executed` | 命令真的跑过（超时、失败、无证据也算） |
| `completed` | 退出码 0 **且**有完成证据（指标序列 / 完成标记 / 完成正则） |
| `metrics_recorded` | 从输出里解析出最终指标 |
| `metrics_matched` | 调用方声明的目标值**全部**匹配 |
| `reproduced` | 指标匹配 **且** 运行时工作区干净 **且** 有 git commit |

### 指标匹配的判定

- 归一化指标名（`acc → accuracy`、`f1_score → f1` 等别名表），避免"名字不同就被判缺失"；
- 容差：目标值绝对值 ≤ 1 时用**绝对容差 0.02**；否则用**相对容差 2%**；
- 每个目标值三种结果：`match` / `mismatch` / `missing`（输出里没有这个指标）；
- `all_match` 才升到 `metrics_matched`。

### 判定树（伪代码，对应 `derive_verdict`）

```
verdict = inspected
if plan:      verdict = plan_ready
if no run:    return verdict          # 不执行就没有运行证据
match run.status:
  failed_to_start → 停在 plan_ready，附启动错误
  timed_out       → executed，附超时秒数
  failed          → executed，附退出码
  process_exited  → executed，并明确写"零退出码不是证据"
  completed       → completed
if metrics 有点且有 final:        verdict = metrics_recorded
if 有目标值 and all_match:        verdict = metrics_matched
else if 有点但没目标值:           停在 metrics_recorded，写明"未声明目标值，无法与论文比较"
if verdict == metrics_matched:
    if git dirty:    停在 metrics_matched，写明"有未提交改动，无法绑定到某个提交"
    elif 无 commit:  停在 metrics_matched，写明"项目不在版本控制下"
    else:            verdict = reproduced
```

### 四种真实结果的对照（本仓库实测）

| 场景 | 判定 | 报告里的理由 |
| --- | --- | --- |
| `--expect accuracy=0.8 --expect f1=0.74`，实测一致 | `reproduced` | 无（达到最高级） |
| 不声明目标值 | `metrics_recorded` | "未声明目标值，无法与论文比较" |
| `python -c "print('done')"`，退出码 0 | `executed` | "零退出码本身不是实验完成的证据" |
| `--expect accuracy=0.99`，实测 0.80 | `metrics_recorded` | "accuracy: expected 0.99, observed 0.8（允许误差 0.02）" |

### `reproduced` 到底意味着什么

**意味着**：这个人在这个解释器、这个提交、这套依赖版本下，跑通了文档里的命令；输出里解析出的指标满足他声明的目标值；工作区没有未提交改动。

**不意味着**：论文结论正确、方法优于基线、结果在别的机器上可重复、统计上显著。报告把这些写进 `limitations`，不隐藏在措辞里。

---

## 已知弱点（如实列出）

1. **完成标记是启发式的。** 只写文件、不打印任何东西的项目会被判为 `process_exited`；此时应用 `--completion-regex` 或让脚本打印一行指标。
2. **指标只从 stdout/stderr 解析。** 只写 TensorBoard/W&B 的项目采集不到指标（可用 `--completion-regex` 至少确认完成）。
3. **别名表是有限的。** 冷门指标名可能匹配不上，表现为 `missing` 而不是 `mismatch`——报告会区分这两者。
4. **目标值必须由人给出。** RunFix 不读论文 PDF，不替你决定“这篇论文的 accuracy 应该是多少”。
5. **一次运行不等于可重复性。** 没有多种子、多次运行的方差分析；这一点明确列在路线图里。
6. **干净 git 只保证可追溯，不保证正确。** 它解决的是"结果能否绑定到某个提交"，不是"结果是否科学正确"。

---

## 如何自行核查上述每一条

```powershell
# 1. 理解项目（只读）
runfix reproduce "D:\research\paper-code" --plan-only

# 2. 环境与 CUDA 判定（三态）
runfix reproduce "D:\research\paper-code" --plan-only   # 看 plan.cuda.verdict 与 reason

# 3. 根因与置信度（可复算）
runfix diagnose-root-cause "D:\research\paper-code" --traceback-file .\error.log

# 4. 补丁闸门与回滚
runfix patch-check "D:\research\paper-code" .\candidate.patch
runfix patch-apply "D:\research\paper-code" .\candidate.patch --confirm
runfix patch-recover "D:\research\paper-code"

# 5-6. 运行与判定（含证据阶梯与理由）
runfix reproduce "D:\research\paper-code" --allow-execution --expect accuracy=0.8

# 审计：每一步特权操作都有哈希链记录
runfix audit "D:\research\paper-code" --verify
```

此外，`runfix bench` 用 11 个带标准答案的任务量化第三步的准确率，并用消融实验检验每条证据的贡献（去掉依赖分析：top-1 从 1.0 降到 0.6364）。
