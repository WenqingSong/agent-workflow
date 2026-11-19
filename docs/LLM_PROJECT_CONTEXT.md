# Workflow V5.0.0 — LLM 项目上下文（单文件完整分析）

> **文档用途**：把整个仓库压缩成一份可被 LLM / AI Agent 单次加载、直接用于推理与改动的上下文。README 面向人类读者；本文面向需要理解「规则如何被机器执行」的模型。
>
> **快照基线**：`.agent/VERSION = 5.0.0`。本文描述**工作树中的源码事实**，不绑定某个 Commit，也不声称任何 Release 已发布。
>
> **命名提示**：Go Module 名 `workflow-v2-engine`、State Schema v2/v3 都是历史技术标识，与 Workflow Major Version（当前 V5）不是同一概念。历史 V3.x 发布说明与任务证据保留为历史记录。
>
> **给模型的阅读建议**：
>
> 1. 先读 §1–§3 建立整体模型；
> 2. 要执行某个角色，读 §4、§10、§11；
> 3. 要判断 Gate 为什么 FAIL，读 §5–§9；
> 4. 要改代码或文档，读 §18–§20；
> 5. 不要从 `.agent/tasks/` 下的历史任务反推当前规则；那里只是历史证据。

---

## 0. 三十秒摘要

- 这是一个 **source-vendored（源码分发包）协作框架**，不是模型调用器、不是调度器、不是代码合并机器人。
- 它用 **Git Commit + `.agent/tasks/<task>/` 文件**作为跨 Session 唯一可信事实源；聊天上下文不是事实源。
- 七个角色按三条路线工作：`NORMAL`（5 个执行角色）、`COMPLEX`（6 个）、`ARCHITECT`（7 个，且不经过 Cleaner）。
- 五个只读 Gate（`coder-start` / `cleaner-start` / `owner-gate-start` / `delivery-start` / `merge-ready`）由 Go 程序 `workflow-check` 计算，输出 `PASS` / `FAIL` / `ERROR`。
- 每个审批 / 审查 / 验收都绑定 **不可变 Commit SHA**；代码变化会让旧结论机械失效，而不是在状态文件里保存 `STALE`。
- 人类 Owner 保留四类决定权：任务与分支边界、Contract、核心逻辑接受、最终集成。
- Engine 只校验可观察事实。它**不能**认证 Agent 身份、Session 独立性、账本真实性或 Prompt 遵守程度。

---

## 1. 项目是什么 / 不是什么

### 1.1 是什么

| 维度 | 事实 |
| --- | --- |
| 类型 | 可复制到其他 Git 仓库的源码包（Source Distribution） |
| 核心组成 | 角色 Prompt、协作规范、任务状态/证据文件、五个 Gate、Go 校验器、循环熔断协议、安装/升级工具、发布工程 |
| 运行依赖 | Git；构建校验器需要 Go 1.23+；安装/升级工具需要 Python 3.10+ |
| 版本 | 源码版本 `5.0.0`；发布 Tag 为 `v5.0.0`，由手工发布流程创建 |
| 安装位置 | 宿主仓库的 `.agent/` 命名空间 |

### 1.2 不是什么

- **不是 Orchestrator**：不会自动派发 Agent、不会调用模型、不会替人开新 Session。
- **不是 Git 写代理**：`workflow-check` 不 stage / commit / push / merge，也不拦截这些命令。
- **不是身份认证系统**：无法证明某个 Session 真的是 Cleaner、无法证明审查真的发生过。
- **不是业务构建系统**：不运行宿主业务测试，不修改宿主 CI。
- **不是自动合并工具**：`merge-ready` 只表示机器条件满足，最终合并由 Owner 执行。

---

## 2. 事实源与权威顺序

### 2.1 事实源优先级

当文档与实现冲突时，按以下顺序取事实：

1. **当前真实代码与测试**：`.agent/engine/internal/workflow/*.go`、`.agent/engine/cmd/workflow-check/*.go`、各 `*_test.go`；
2. **当前生效的角色 Prompt 与规范**：`.agent/specs/*`、`.agent/roles/*`；
3. **架构设计文档**：`docs/design/agent-workflow.md`（解释 WHY，不覆盖代码）；
4. **历史任务证据**：`.agent/tasks/*`（只作历史证据，不是现行语义来源）。

发布相关事实以 `scripts/release/release.py`、`.cnb.yml`、`PACKAGE_MANIFEST.md` 为准。平台发布状态以 CNB Release、附件 SHA256 与 Git Tag 核验为准，不以文档自述为准。

> 注意区分两种「优先级」：上表是**事实真伪**的优先级；角色执行时的**指令优先级**另见 `WorkflowAgentDirective.md` §7（Owner 当前指令 > Task AC > Contract/Design > 宿主 `AGENTS.md` > Workflow 规范）。行为优先级不能用来否认实现事实。

### 2.2 跨 Session 事实从哪来

| 事实 | 唯一来源 |
| --- | --- |
| 任务定义、AC、Scope、Review Baseline | `.agent/tasks/<task>/task.md` |
| 当前工作流决策状态 | `.agent/tasks/<task>/state.yaml` |
| 路线 | `state.yaml.route`（必须与 `task.md` 的 `## Complexity` 一致） |
| 审查对象 | `state.yaml.review.target`（并以 Gate 输出复核） |
| 复审对象 | `findings.md` 中 `Status: OPEN` 的编号 |
| 失败原因与退回对象 | `findings.md` / `owner-decision.md` / `delivery.md` / `loop-ledger.md` |
| 当前 Commit 与远端事实 | `git rev-parse HEAD`、`git ls-remote` |
| 宿主业务规范 | 宿主根 `AGENTS.md`（可选；不存在时不创建、不阻断） |

**禁止**把上一角色的聊天叙述、历史 `HANDOFF_PROMPT`、任务编号顺序当作事实。

---

## 3. 心智模型：三层 + 三个闭环

### 3.1 三层

| 层 | 内容 | 位置 | 稳定性 |
| --- | --- | --- | --- |
| Workflow Core | 角色、权限、Evidence、Gate、Handoff、完成条件、Git 安全模型、Registry 概念 | `.agent/engine/`、`.agent/specs/`、`.agent/roles/` | 跨项目稳定 |
| Machine Configuration | `integration_branch` 与 resource kind → Registry 路径 | 宿主 `.agent/workflow.yaml` | 项目间变化；不由分发包提供 |
| Project Policy | 业务规范、构建命令、目录约定、宿主 `AGENTS.md` | 宿主仓库 | 宿主所有；Engine 不解析 |

关键推论：**分发包不包含 `workflow.yaml`**。未完成 Adoption 的仓库不能直接运行 Gate 示例。

### 3.2 三个闭环

1. **证据闭环**：实现 → Evidence Commit → Metadata Commit（写入 SHA）→ 审查/验收绑定该 SHA → 后续实质变化使其失效。
2. **出站闭环**：角色完成职责 → 显式 stage → commit → push → 远端核验 → 下一入口 Gate PASS → 才能输出交接。
3. **停机闭环**：同一根因两轮往返 → DANGER → `blocked.active=true` → 五个 Gate 全部拒绝 → 只有 Owner 授权才能恢复。

---

## 4. 路线与角色

### 4.1 三条路线

| 路线 | 目标 | `contract.status` | 角色序列 |
| --- | --- | --- | --- |
| `NORMAL` | 目标清楚、边界有限，不需要独立 Contract | `NOT_REQUIRED` | TaskBuilder → Coder → Cleaner → OwnerGate → Deliverer → Owner |
| `COMPLEX` | 需要独立的需求/设计/Contract 审批 | `PENDING` → `APPROVED` | TaskBuilder → Analyst → Coder → Cleaner → OwnerGate → Deliverer → Owner |
| `ARCHITECT` | 交付结构本身（边界、接口、骨架、结构测试），不是完整业务功能 | `PENDING` → `APPROVED` | TaskBuilder → Analyst → Architect → OwnerGate（V1 结构验证 → V2 Owner 决策）→ Deliverer → Owner |

`ARCHITECT` 的关键差异：

- **不经过 Cleaner**；结构验证由 OwnerGate 的 V1 阶段在**新 Session** 中执行，入口 Gate 仍是 `cleaner-start`；
- V1 产出 `findings.md` / `core-logic.md` / `review.status=CLEAN`（`target` 保持 D1）；
- V2 在同一 OwnerGate Session 中请求 Owner 决策，产出 `owner-decision.md` / `owner.status=ACCEPTED`；
- **不新增 Gate**，五个 Gate 的名称与语义不变；
- 需要完整业务功能、DB Migration、未批准 Schema、新外部依赖或构建/部署模型变化时，不属于 `ARCHITECT`，必须回交 Analyst / Owner 决定是否切换 `COMPLEX`。

### 4.2 角色职责

| 角色 | 回答的问题 | 主要产物 | 入口 Gate | 出口 Gate |
| --- | --- | --- | --- | --- |
| TaskBuilder | 交付什么、怎样算完成、走哪条路线 | `task.md`、初始 `state.yaml` | 无专用入口（自动基线 + 预检） | NORMAL 自检 `coder-start`；COMPLEX/ARCHITECT 只做同步自检 |
| Analyst | 有哪些约束、方案、必须保持的性质 | `contract.md`、`docs/design/*`、`state.contract` | 无（TaskBuilder 直接交接） | `coder-start` |
| Architect | 结构应当怎样成立 | `architecture.md`、结构骨架、结构测试、`state.review` | `coder-start` | `cleaner-start` |
| Coder | 怎样在既定范围内实现 | 业务代码、migration、测试、`state.review` | `coder-start` | `cleaner-start` |
| Cleaner | 完整变更是否满足任务、测试是否可信 | `findings.md`、`core-logic.md`、`state.review` | `cleaner-start` | `owner-gate-start` |
| OwnerGate | 核心机制是什么；ARCHITECT 下先验证结构 | `owner-decision.md`、`state.owner`；ARCHITECT V1 另产出审查证据 | `owner-gate-start`（V1 为 `cleaner-start`） | `delivery-start` |
| Deliverer | 能否在目标环境真实运行 | `delivery.md`、`state.delivery`（三 SHA） | `delivery-start` | `merge-ready` |
| Owner（人类） | 任务边界、Contract、核心逻辑、最终集成 | 决策，不是 Artifact | — | — |

Owner 不是第八个 Agent。Agent 不得自行生成 `ACCEPTED`，也不得替 Owner 合并。

### 4.3 Artifact 所有权（硬规则）

| Artifact | 写者 | 说明 |
| --- | --- | --- |
| `task.md` | TaskBuilder | 不在 Review-neutral 白名单；任何修改都会使旧 `CLEAN` 失效 |
| `state.yaml` | 各角色按职责写自己的字段 | 唯一机器事实源 |
| `contract.md` | Analyst | 内容变更需重新批准 |
| `architecture.md` | Architect | 必须在 D1 内；D1 后修改即实质变化 |
| `findings.md` | Cleaner（ARCHITECT 下 OwnerGate V1） | Finding 只能由审查方创建/关闭 |
| `core-logic.md` | Cleaner（ARCHITECT 下 OwnerGate V1） | Owner 核心验证卡 |
| `owner-decision.md` | OwnerGate | 含 `Return To` |
| `delivery.md` | Deliverer | 三 SHA 绑定 |
| `loop-ledger.md` | 发起请求/退回/熔断的角色 | append-only 事件流 |
| `danger-report.md` | 触发 DANGER 的角色 | 停机证据 |

发现其他角色的 Artifact dirty 时：不得 `git add -A`、不得顺手提交、不得丢弃；只 stage 自己的路径，或 `BLOCKED`。

---

## 5. `state.yaml` 数据模型

### 5.1 真实结构（Schema 3）

```yaml
schema_version: 3
route: NORMAL | COMPLEX | ARCHITECT   # 可选；缺失 = 历史任务，新任务必须写
task_id: <task-slug>

contract:
  status: PENDING | APPROVED | REJECTED | NOT_REQUIRED
  target: <full-40-hex | "">

resources:
  reservations:
    <resource-kind>:
      - <opaque-value>

review:
  status: NOT_REQUESTED | PENDING | CLEAN | CHANGES_REQUIRED
  target: <full-40-hex | "">

owner:
  status: PENDING | ACCEPTED | REJECTED | NOT_REQUIRED
  review_target: <full-40-hex | "">

delivery:
  status: NOT_RUN | PASS | FAIL | BLOCKED
  review_target: <full-40-hex | "">
  feature_head: <full-40-hex | "">
  develop_base: <full-40-hex | "">

blocked:
  active: false
  by: ""
  reason: ""
```

### 5.2 设计约束

- **不保存** `phase`、`STALE`、`DONE`、`branch`、`HEAD`、`working_tree`、`changed_files`、`NEXT_ROLE`、`OWNER_ACTION`。
- 生命周期由 Gate 与 Owner 控制；有效性由 Git 事实动态推导。
- `resources.reservations` 只声明「需要什么」，**不自证满足**；权威事实在集成分支的 Registry。
- 每个 reservation value 对 Core 都是不透明非空字符串；格式正确性属于 Project Policy。
- `schema_version` 只接受 `3` 与 `2`；`2` 仅用于历史任务读取兼容，运行时归一化为 generic reservations，**不写回磁盘**；其他版本 → `ERROR`（exit 2）。
- 解析是 strict 的：未知顶层字段、未知 `resources` 子键、v2/v3 字段混用都 fail closed。
- `task_id` 必须非空；Registry 中的 owner 与 `task_id` 做精确字符串比较。

---

## 6. Evidence Snapshot 与两阶段提交

### 6.1 为什么需要

Commit 在创建前无法知道自己的 SHA。因此不能让 Evidence Commit 自己写入「我的 SHA」。解决办法是两阶段：

| 阶段 | 符号 | 内容 | 后续引用 |
| --- | --- | --- | --- |
| Contract 内容 | A1 | `contract.md` + 必要 Design | `state.contract.target = A1`（A2 写入） |
| Contract 元数据 | A2 | 只写 `state.yaml` | — |
| 实现内容 | C1 | 业务代码 + 测试 | `state.review.target = C1`（C2 写入） |
| 实现元数据 | C2 | 只写 `state.yaml` | — |
| 结构内容 | D1 | 骨架 + `architecture.md` + 结构测试 | `state.review.target = D1`（D2 写入） |
| 结构元数据 | D2 | 只写 `state.yaml` | — |

审查方审的是 **C1 / D1**，不是「开始审查时的 HEAD」，也不是审查方自己的 commit。

### 6.2 Evidence 字段写法

- 新证据必须写**完整 40 位十六进制 SHA**。
- 不接受 `HEAD`、`HEAD~1`、分支名、Tag、`origin/<branch>` 等可移动引用。
- 为兼容历史，Engine 接受 7～40 位十六进制短 SHA，但会：形态校验 → 唯一解析 → 前缀一致性校验 → 返回完整 SHA；绑定比较统一用完整 SHA。
- 短 SHA 兼容**不是**新任务可以使用短 SHA 的许可。

### 6.3 Neutral Tail（精确路径白名单）

`review.target..feature HEAD` 之间只允许当前任务根目录下的这些文件：

```text
.agent/tasks/<task>/findings.md
.agent/tasks/<task>/core-logic.md
.agent/tasks/<task>/owner-decision.md
.agent/tasks/<task>/delivery.md
.agent/tasks/<task>/state.yaml
.agent/tasks/<task>/loop-ledger.md
.agent/tasks/<task>/danger-report.md
```

`delivery.feature_head..remote feature HEAD` 之间允许更窄的一组：

```text
.agent/tasks/<task>/delivery.md
.agent/tasks/<task>/state.yaml
.agent/tasks/<task>/loop-ledger.md
.agent/tasks/<task>/danger-report.md
```

判定规则：

- **task-scoped + exact path**：`path.Dir(p)` 必须精确等于 `.agent/tasks/<task>`，不接受子目录。
- **default deny**：不在白名单内的一切路径都是 substantive change。
- **禁用 rename detection**：使用 `git diff --no-renames --name-only`；rename 会暴露为「源路径删除 + 目标路径新增」，避免生产代码被折叠进白名单路径。
- `architecture.md` 不在任何 Tail 中：它必须与结构实现同处 D1。
- 其他任务、其他目录的同名文件一律 substantive。

### 6.4 核心不变量

| 编号 | 不变量 |
| --- | --- |
| INV-1 | 只有 Owner 能变更共享集成分支；Agent 只有两个窄例外：Analyst 的 Registry-only develop commit、TaskBuilder 的当前任务初始分支创建 |
| INV-2 | Coder 开始前 Contract 必须有效，且每个共享资源都由集成分支 Registry 授权 |
| INV-3 | Cleaner 只能对明确的不可变 Commit 给 `CLEAN`，不能对工作区给 `CLEAN` |
| INV-4 | 任何 substantive change 使旧 `CLEAN` 机械失效；不保存 `STALE` |
| INV-5 | Owner `ACCEPTED` 必须绑定当前 `review.target` |
| INV-6 | Delivery `PASS` 必须绑定 `review.target` + `feature_head` + 精确 `develop_base` |
| INV-7 | Contract / Review / Delivery 证据必须绑定不可变快照；只允许该阶段的 neutral tail |

---

## 7. 五个 Gate 的精确算法

`workflow-check gate <name> <task>` 的公共前缀：

```text
1. 读取 <repo-root>/.agent/tasks/<task>/state.yaml
   - 读取失败 / 解析失败 / schema 不支持 → ERROR(exit 2)
2. schemaValidation
   - task_id 为空、枚举非法 → FAIL(exit 1)
3. blocked.active == true → FAIL(exit 1)
4. routeValid（route 为空则跳过）
5. 各 Gate 专属检查
```

### 7.1 `coder-start`

```text
handoffReady()
  → contractValid()
    → resourcesAuthorized()
```

含义：现在可以把实现交给 Coder（ARCHITECT 下是 Architect，语义不变）。

- `contractValid`：`NOT_REQUIRED` 直接通过；`APPROVED` 要求 `target` 可解析、是当前 feature HEAD 的祖先、且 target 中的 `contract.md` 与工作树当前内容完全一致；`PENDING` / `REJECTED` 失败。
- `resourcesAuthorized`：无 reservations 直接通过；否则逐个 kind 读取集成分支 Registry。

### 7.2 `cleaner-start`

```text
review.status == PENDING
  → review.target 非空
  → handoffReady()
  → featureHead()
  → reviewTailValid()
```

含义：Review Request 已稳定落 Git，可以开始独立审查。

- `reviewTailValid` 要求 `review.target` 可解析、是当前 feature HEAD 的祖先或同一 Commit、且 tail 仅 neutral。
- 此 Gate **不检查** Contract / Resources。

### 7.3 `owner-gate-start`

```text
handoffReady()
  → featureHead()
  → cleanValidity()
```

含义：可以围绕一个仍然有效的 `CLEAN` 快照请求 Owner 决策。

- `cleanValidity` = `review.status == CLEAN` + `reviewTailValid`。
- `PASS` **不等于** Owner 已接受。

### 7.4 `delivery-start`

```text
contractValid()
  → resourcesAuthorized()
  → featureHead()
  → cleanValidity()
  → owner.status == ACCEPTED
  → resolve(review.target) 与 resolve(owner.review_target)，完整 SHA 必须相等
  → handoffReady()
```

含义：Deliverer 可以验收一个已被 CLEAN、Owner 明确接受、且已同步的候选。

### 7.5 `merge-ready`

```text
contractValid()
  → resourcesAuthorized()
  → featureHead()
  → cleanValidity()
  → ownerAcceptanceValid()
  → delivery.status == PASS
  → resolve(review.target) 与 resolve(delivery.review_target)，完整 SHA 必须相等
  → delivery.feature_head 非空
  → delivery.develop_base 非空
  → currentRemoteFeatureHead() 与 developHead()
  → deliveryFreshness()
  → handoffReady()
```

`deliveryFreshness`：

1. `delivery.feature_head` 是当前 `origin/<current-branch>` HEAD 的祖先；
2. `feature_head..remote HEAD` 只允许 delivery-neutral；
3. 当前 `origin/<integration_branch>` 的 HEAD 必须与 `delivery.develop_base` **完整 SHA 相等**。

`merge-ready` **不代表** Agent 可以合并；它是最后一个 Agent Gate，之后交给 Owner。

### 7.6 `handoffReady`

```text
非 detached HEAD
working tree clean（git status --porcelain 为空）
origin/<current-branch> 存在
local HEAD == origin/<current-branch>
```

- detached HEAD / 远端分支不存在 → **ERROR**（exit 2），不是 FAIL。
- dirty 或 local != remote → **FAIL**（exit 1）。

### 7.7 输出与退出码

```text
PASS     gate=<name>
FAIL     gate=<name> task=<task> check=<invariant> actual=<...> expected=<...> reason=<...>
ERROR    gate=<name> <error>
```

| exit | 含义 |
| --- | --- |
| 0 | PASS |
| 1 | 不变量 / Gate FAIL |
| 2 | 输入 / Schema / 运行级 ERROR |

常见 ERROR：`state.yaml` 不存在、YAML 解析失败、不支持的 schema、配置缺失、Registry 读取失败、detached HEAD、Git 命令失败。

---

## 8. 路线不变量（R-1 ~ R-4）

| 编号 | 检查 | 失败形态 |
| --- | --- | --- |
| R-1 | `route` ∈ {`NORMAL`,`COMPLEX`,`ARCHITECT`}；`workflow-check route` 默认要求存在 | `route enum` / `Route Authority (R-1)` |
| R-2 | `NORMAL` 必须是 `contract.status=NOT_REQUIRED`；`COMPLEX` / `ARCHITECT` 不得为 `NOT_REQUIRED` | `Route Contract (R-2)` |
| R-3 | `state.route` 必须等于 `task.md` 中 `## Complexity` 的唯一声明 | `Route Task Document (R-3)` |
| R-4a | `contract.target` 所在 Commit 的 `state.yaml` 记录的 route 必须仍是当前 route | `Route Contract Evidence (R-4a)` |
| R-4b | `ARCHITECT` 的 `review.target` 必须包含 `architecture.md`；其他路线的 `review.target` 自身不得改写/引入它 | `Route Review Evidence (R-4b)` |

`## Complexity` 解析规则（fail-closed）：

- 只认第一个 `## Complexity` 章节；
- 只认该章节内、以路线关键字开头、且整行只出现一个路线关键字的行；
- 保留模板占位（如 `NORMAL / COMPLEX / ARCHITECT`）视为未声明；
- 章节缺失、无唯一声明、多路线并列 → 未声明 → 拒绝。

Engine **不推断**缺失 `route` 的历史任务属于哪条路线；历史任务只能用 `--allow-legacy` 跳过 `route` 子命令的严格检查。

---

## 9. Generic Shared Resource Authority

### 9.1 数据流

```text
state.yaml.resources.reservations.<kind>
        ↓ 按 kind 查配置
.agent/workflow.yaml: resources.<kind>.registry
        ↓ 读取远端集成分支上的文件
origin/<integration_branch>:<registry-path>
        ↓ 解析 Markdown 表
row = Value / Owner / Status
```

### 9.2 配置要求

```yaml
schema_version: 1
git:
  integration_branch: develop
resources:
  migration_version:
    registry: .agent/registry/migrations.md
  error_code_domain:
    registry: .agent/registry/error-codes.md
```

- `schema_version` 必须为 `1`。
- `git.integration_branch` 必填；Engine 构造 `origin/<branch>`。
- `resources` 是任意 map；kind 必须匹配 `^[a-z][a-z0-9_]*$`。
- Registry 路径必须非空、相对路径、规范化后严格位于 `.agent/registry/` 下。
- 不同 kind 不得声明相同 Registry 路径。
- 配置任何损坏 → `ERROR`（exit 2），不得误报为「resource not reserved」。

### 9.3 Registry 解析与授权

- 跳过表头与分隔行；少于 4 列的 row 跳过。
- `Value` = 第 1 列；`Owner` = 倒数第 3 列；`Status` = 倒数第 2 列。
- 有效状态只有 `RESERVED` / `ACTIVE`。
- 每个声明的 value 必须：存在、Owner 精确等于 `state.task_id`、状态有效。
- Feature 分支自己写 `RESERVED` 不算授权。
- 并发的串行化依靠 Git 提交顺序，无外部锁；先提交到集成分支者胜。

---

## 10. 独立 Session 启动与阶段推导

### 10.1 启动串

```text
以 <ROLE> 角色执行任务：
.agent/tasks/<task-slug>/
```

可选第三行 `NOTE: <≤2 行>`，只允许写无法持久化的启动前提。

角色按五步执行：

1. 读规范链（Directive、自身 Role Prompt、协作规范、职责边界、路线、熔断、交接契约、Git Lifecycle）；
2. 读 Task 目录内全部现存 Artifact；`task.md` / `state.yaml` 缺失为 `BLOCKED`，其余缺失不阻断；
3. 推导 stage、`mode`、入口 Gate；
4. 输出开工声明（理解证据，不是 Gate 结论）；
5. 运行入口 Gate；`PASS` 后进入职责，`FAIL` 时按原因 `BLOCKED` / `STOP`。

### 10.2 开工声明

```text
## 开工声明
ROLE: <CLEANER>
TASK: <task-slug>
STAGE: <REVIEW_PENDING>
TARGET: <review.target 完整 SHA，或 NONE>
ENTRY_GATE: <cleaner-start = PASS | none>
```

推导冲突、`route` 缺失、Artifact 自相矛盾 → `BLOCKED`，不猜测、不擅自改角色。

### 10.3 Stage Derivation Table（要点）

| route | contract | review | owner | delivery | stage | actor | 入口 Gate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NORMAL | NOT_REQUIRED | NOT_REQUESTED | PENDING | NOT_RUN | IMPLEMENTATION_PENDING | CODER | coder-start |
| COMPLEX / ARCHITECT | PENDING / REJECTED | NOT_REQUESTED | PENDING | NOT_RUN | CONTRACT_PENDING | ANALYST | none |
| COMPLEX | APPROVED | NOT_REQUESTED | PENDING | NOT_RUN | IMPLEMENTATION_PENDING | CODER | coder-start |
| ARCHITECT | APPROVED | NOT_REQUESTED | PENDING | NOT_RUN | STRUCTURE_PENDING | ARCHITECT | coder-start |
| 任意 | 任意 | PENDING | PENDING | NOT_RUN | REVIEW_PENDING | CLEANER（ARCHITECT → OWNER_GATE V1） | cleaner-start |
| 任意 | 任意 | CHANGES_REQUIRED | 任意 | NOT_RUN | REPAIR_PENDING | CODER（ARCHITECT → ARCHITECT） | none |
| 任意 | 任意 | CLEAN | PENDING | NOT_RUN | DECISION_PENDING | OWNER_GATE | owner-gate-start |
| 任意 | 任意 | CLEAN | ACCEPTED | NOT_RUN | DELIVERY_PENDING | DELIVERER | delivery-start |
| 任意 | 任意 | CLEAN | ACCEPTED | PASS | INTEGRATION_PENDING | OWNER | merge-ready |
| 任意 | 任意 | CLEAN | REJECTED | NOT_RUN | RETURN_PENDING | 读 `owner-decision.md` 的 `Return To`；缺失 → BLOCKED | none |
| 任意 | 任意 | 任意 | 任意 | 任意 | BLOCKED（`blocked.active=true`） | NONE | 任意 Gate 均 FAIL |

`RETURN_PENDING` 是唯一不可机器推导的行。

### 10.4 `mode` 推导

| Role | 推导 |
| --- | --- |
| Analyst | `contract.status=PENDING` 且有 Owner 确认文本 → `record_owner_decision`；`APPROVED` 且 Owner 要求重审 → `reconsideration`；否则 `analysis` |
| Architect | `review.status=CHANGES_REQUIRED` → `structural_repair`；否则 `architecture_delivery` |
| Coder | `review.status=CHANGES_REQUIRED` 或 `owner.status=REJECTED` 或 `delivery.status=FAIL` → `finding_fix`；否则 `implementation` |
| Cleaner | `review.status=PENDING` → `initial_review`；`CHANGES_REQUIRED` → `re_review` |
| Deliverer | `delivery.status=NOT_RUN` → `milestone_verification`；`FAIL` → `re_verification` |
| OwnerGate | `review.status=PENDING` 且 `route=ARCHITECT` → V1；`review.status=CLEAN` → V2 |
| TaskBuilder | 输入含 `task_path` → `owner_update`；否则 `create` |

`finding_ids` 不是输入；复审对象固定为 `findings.md` 中 `Status: OPEN` 的全部编号。`findings.md` 缺失或全部 `CLOSED` 时 `BLOCKED`（`INPUT_INCOMPLETE`）。

---

## 11. 出站交接契约（V5.0）

### 11.1 输出位置与字段

必须是角色最终回答的**最后一个章节**，之后不得再有任何文字：

```text
## 出站交接

RESULT: <本角色终态结果码>
NEXT_ROLE: <ANALYST | ARCHITECT | CODER | CLEANER | OWNER_GATE | DELIVERER | TASK_BUILDER | OWNER | NONE>
TASK_PATH: .agent/tasks/<task-slug>/
OWNER_ACTION: <NONE | CONTINUE | DECIDE: ... | DO: ... | STOP>
```

可选 `NOTE:` 最多 2 行，且只能写无法持久化的启动前提。

### 11.2 `OWNER_ACTION` 五种语义

| 取值 | 语义 | 开发者动作 |
| --- | --- | --- |
| `NONE` | 正常交接 / 失败交接，事实已落盘 | 开新 Session：`以 <NEXT_ROLE> 角色执行任务：<TASK_PATH>` |
| `CONTINUE` | 同 Session 阶段推进或本角色继续 | 无需操作 |
| `DECIDE: <最小问题>` | 等待 Owner 决策，Session 保持 | 回答问题 |
| `DO: <动作>` | 需要 Owner 先完成 Agent 不能做的操作 | 执行后重跑入口 Gate |
| `STOP` | `BLOCKED` / `DANGER` / Owner 终止 | 按 `NOTE` 的解除条件处理 |

### 11.3 交接块不包含

- AC / Scope / 不变量复述；
- 完整 40 位 SHA；
- 文件清单、实现步骤；
- 「下一步先读 X」或必读清单；
- owned artifact 修改规则。

事实分别在 `task.md`、`state.yaml`/Git、Role Prompt、Directive §4 中。

### 11.4 失败必须先落盘

| 失败来源 | 载体 | 必含 |
| --- | --- | --- |
| Cleaner `CHANGES_REQUIRED` | `findings.md` | Finding 编号、Severity、Trigger、Actual、Expected、Required Fix Boundary |
| OwnerGate `REJECTED` | `owner-decision.md` | 决定、`Return To`、理由、不可修改范围 |
| Deliverer `FAIL_*` | `delivery.md` | 检查项、实际、期望、证据 |
| 其他角色失败 | `loop-ledger.md` | `handoff_target`、`receiver_action`、根因、证据 Commit |
| 两轮熔断 | `loop-ledger.md` + `danger-report.md` | 两轮证据、状态、最小待决问题、允许的恢复方式 |

未落盘并 push 的失败原因，不得发出失败交接；只能 `STOP`（`PERSISTENCE_FAILURE`）。

### 11.5 特殊终态

| 终态 | 字段 |
| --- | --- |
| DANGER | `RESULT=DANGER`、`NEXT_ROLE=NONE`、`OWNER_ACTION=STOP`；`blocked.active=true` |
| BLOCKED | `OWNER_ACTION=STOP`；`NEXT_ROLE` 只能是 OWNER / NONE / 责任角色 |
| Owner 终止 | `RESULT=STOPPED`、`NEXT_ROLE=NONE`、`OWNER_ACTION=STOP`；**不得**写 `blocked.active=true` |

终止 ≠ 阻塞：把终止写成 blocked 会让五个 Gate 永久拒绝该任务。

---

## 12. Git Lifecycle

### 12.1 状态机

```text
WORKING
  → DELIVERABLE_VERIFIED
  → COMMITTED
  → PUSHED
  → REMOTE_VERIFIED
  → GATED
  → HANDOFFED
```

不可跳过、不可颠倒。`GATED` 之前不得输出正常交接。

### 12.2 合法 Commit 类型（只有六种）

| 类型 | 内容 | 关键约束 |
| --- | --- | --- |
| `BOOTSTRAP` | Task 初始 Artifact | 只用 pathspec 提交当前任务目录；保留他人修改 |
| `EVIDENCE` | A1 / C1 / D1 内容 | 不含 `state.yaml`；不含其他角色 artifact；验证已真实执行 |
| `METADATA` | **仅** `state.yaml` | 引用完整 SHA；字段转移与角色一致 |
| `ROLE_ARTIFACT` | 本角色产物 + 对应 state 字段 | 不改 `review.target`；不写他人字段 |
| `FORCED_EVIDENCE` | `loop-ledger.md`、`danger-report.md`、`state.yaml` blocked 字段 | append-only；DANGER 不交接 |
| `REGISTRY_ONLY` | 配置已声明的 Registry 文件 | 只 push 集成分支；diff 只含 Registry |

`PROGRESS`（临时保存）禁止。已 push 的 Commit 不得 amend / rebase / force。

### 12.3 允许与禁止的 Git 操作

允许：

```text
fetch / status / diff / log / show / rev-parse
switch 到已存在且属于当前任务的分支
pull --ff-only 当前 feature
显式 add <path>
commit
push 当前 feature
```

禁止：

```text
force / force-with-lease
reset --hard
clean -fd
rebase 已发布历史
amend 已发布 commit
删除/重命名远程分支
git add -A / git add . / git add -u
push 共享集成分支（Analyst Registry-only 例外）
创建分支（TaskBuilder 当前任务初始分支例外）
```

远端事实必须用 `git ls-remote` 核验，不能只看本地 `origin/<branch>` tracking ref。

### 12.4 三种「完成」不能混淆

| 概念 | 含义 | 可交接 |
| --- | --- | --- |
| `ROLE_TASK_COMPLETE` | 本角色职责完成 | 否 |
| `STAGE_DELIVERABLE_VERIFIED` | 阶段交付成立（A1/C1/D1、CLEAN、ACCEPT、PASS） | 否 |
| `SESSION_COMPLETE` | 落盘 + commit + push + 远端核验 + 入口 Gate PASS | 是 |

---

## 13. 循环熔断（DANGER）

### 13.1 预算

- 计数键：`(task_id, loop_key)`。
- `loop_key` = 问题类别 + 稳定语义根因。
- 每个 segment 默认允许 **2 次 `REQUEST`**。
- 第 3 次发起前必须 DANGER；DANGER 不消耗轮次。
- `CLOSE` 只关闭当前 loop，不重置计数。
- 只有 Owner 明确授权才能 `RESET`（开启新 segment 并记录预算）。

### 13.2 Write-ahead 记账

发起第 1/2 轮前，必须：

1. 读取完整 `loop-ledger.md`、`danger-report.md`、`state.blocked`；
2. 确定 `loop_key`；
3. 检查 segment 与预算；
4. 追加 `REQUEST` 事件；
5. 只 stage `loop-ledger.md`（以及本次确实同时变更的 `state.yaml`），不得混入业务代码、测试、Contract 或 Design；
6. commit + push 当前 feature；
7. 核验远端确实包含新事件；
8. 以上全部通过，才能发出请求 / 退回 / Contract 修订。

记账失败 → 禁止继续发问，输出 `RESULT=BLOCKED`。已发出但未记账 → 补记并标 `UNRECORDED_REQUEST`，保守计入轮次。

### 13.3 账本事件

只允许：`REQUEST` / `DANGER` / `CLOSE` / `RESET`。

必填字段包含：`task_id`、`event_id`、`event_type`、`loop_key`、`category`、`segment`、`round`、`budget`、`role`、`evidence_commit`、`ledger_base`、`anchor`、`root_cause`、`request_summary`、`handoff_target`、`receiver_action`、`premise_change`、`owner_decision`、`outcome`、`timestamp`、`seq`。

`loop-ledger.md` 在 Review-neutral 与 Delivery-neutral 两条白名单中；`danger-report.md` 同理。

### 13.4 DANGER 原子停机

```text
账本写入 DANGER
  → danger-report.md 写全
  → state.blocked.active=true
  → commit（只含三类文件）
  → push
  → 远端核验
```

之后：不得运行下游 Gate，不得要求下游继续。持久化失败 → `RESULT=BLOCKED`（`PERSISTENCE_FAILURE`），不得假装 DANGER 已记录。

### 13.5 能力边界

Engine 只保证：

1. `blocked.active=true` 时五个 Gate 全部 FAIL；
2. 两个证据文件在当前 Task 根目录内属于 neutral tail；
3. 其他路径仍是 substantive。

Engine **不能**保证：账本 append-only、轮次真实、根因归类正确、DANGER 报告未编造、跨 Session 一定重新读取账本。

---

## 14. TaskBuilder 自主初始化

### 14.1 输入

Owner 只需给任务目标或任务清单。Slug、分支、基线、创建授权都不是必填；Owner 显式给出时是硬约束。

### 14.2 基线（TB-BASE-1）

- 默认基线 = **当前检出分支 + 当前 HEAD**。
- 不擅自切换到 develop / main。
- 不用远端 SHA 替换当前 HEAD。
- 只记录 `AHEAD` / `BEHIND` / `DIVERGED`，不自动 pull / merge / rebase / reset。
- detached HEAD → `BASE_UNRESOLVED`，报告候选与 reflog，由 Owner 指定。

### 14.3 命名与分支（TB-NAME-1 / TB-BRANCH-1）

- 从目标生成 kebab-case slug，分支为 `task/<slug>`。
- 检查本地、远端、任务目录与 ref 合法性；冲突时先改名，不覆盖。
- 授权范围仅限：创建当前任务初始分支、写初始 Artifact、用 pathspec 提交任务目录、普通 push 该分支、核验远端。
- 不延伸到：删除/重命名分支、Merge、Rebase、Force、push 共享分支、修改其他任务。

### 14.4 保护既有修改

- 任务目录外的既有修改（含已暂存）保留并隔离。
- `git commit -- .agent/tasks/<task>` 只提交任务目录。
- 无法 clean exit → `BOOTSTRAP_HELD`，不得为了交接而提交、stash、reset、clean 他人修改。

### 14.5 依赖（TB-DEP-1）

| 类型 | 含义 | 上游未交付时 |
| --- | --- | --- |
| Blocking | Goal / AC 必须使用上游已审查通过的结论 | 不初始化、不交接；`BLOCKED` + `UPSTREAM_NOT_DELIVERED` |
| Reference-only | 上游只提供参考 | 继续初始化；不引用未审查结论 |
| Parallel | 无交付依赖 | 正常初始化 |

依赖只写入 `task.md`，不升级 state schema。

### 14.6 结果码（Prompt 层）

`BOOTSTRAP_COMPLETE` / `BOOTSTRAP_HELD` / `BASE_UNRESOLVED` / `OWNER_INPUT_REQUIRED` / `BOOTSTRAP_FAILED` 等是 Prompt 输出标识，不进入 `state.yaml`，不改变 Gate。

---

## 15. 分发、安装与升级

### 15.1 文件分类

| 分类 | 内容 | 进分发包 | 安装到宿主 |
| --- | --- | --- | --- |
| `managed` | `.agent/roles/**`、`.agent/specs/**`、`.agent/templates/**`、`.agent/engine/**`、`.agent/adoption/**`（tests 除外）、`.agent/README.md`、`.agent/VERSION`、`.agent/bin/.gitignore` | 是 | 是；升级参与 B/L/N |
| `package_only` | 根 `README.md`、`PACKAGE_MANIFEST.md`、`.cnb.yml`、`docs/design/agent-workflow.md`、`docs/LLM_PROJECT_CONTEXT.md`、`docs/releases/**`、`scripts/release/**`、`.agent/adoption/tests/**` | 是 | 否 |
| `generated` | `.agent/workflow.yaml`、`.agent/upstream.yaml`、`.agent/bin/workflow-check` | 否 | 仅由成功事务生成/更新 |
| `protected` | 根 `AGENTS.md`、宿主 README / CI / Design、`.agent/workflow.yaml`、`.agent/tasks/**`、`.agent/registry/**`、业务文件、已有证据 | 否 | 永不覆盖/删除 |

新增 tracked 文件必须先在 `.agent/adoption/workflow_adoption.py` 的 `PACKAGE_ONLY_FILES` / `MANAGED_*` 与 `PACKAGE_MANIFEST.md` 归类，否则 `dist` 会以 `SOURCE_UNCLASSIFIED` 失败。

### 15.2 Source Distribution

```bash
.agent/adoption/workflow-adoption dist --source . --output dist/
```

产物：

```text
dist/agent-workflow-src-<commit12>.tar.gz
dist/agent-workflow-src-<commit12>.tar.gz.sha256
```

构建要求：

- 源仓库必须干净，包括无未跟踪文件；
- 只接受显式分类的 tracked 文件；
- 宿主数据（`workflow.yaml` / `upstream.yaml` / registry / binary / `AGENTS.md`）fail closed；
- 框架仓库自身的 tracked `.agent/tasks/**` 被排除，但不再使构建失败；
- 拒绝绝对路径、`..` 穿越、符号链接、硬链接、设备/FIFO；
- 归档确定性：固定 mtime、uid/gid、uname/gname 与排序。

`MANIFEST.json` 包含：`format`、`kind`、`framework_version`、`source.repository/commit/tree`、`compatibility.config_schema=1`、`compatibility.state_schema=[2,3]`、每个成员的 `path/sha256/mode/install`、`install.managed/package_only/generated/protected`、`excluded`、`release.tag=""`、`release.url=""`。

### 15.3 安装

```bash
.agent/adoption/workflow-adoption install \
  --target /path/to/host \
  --package dist/agent-workflow-src-<commit12>.tar.gz \
  --distribution-sha256 <sha256> \
  --source-commit <full-40-hex> \
  --integration-branch <owner-confirmed-branch> \
  --owner <owner> --owner-authorized --execute
```

- 默认 Dry-run；`--execute` 才写入。
- 只写 `managed`；`package_only` 不落宿主。
- 不创建/覆盖/删除根 `AGENTS.md`；不覆盖宿主 README / CI / Design。
- 生成真实 `workflow.yaml`、`upstream.yaml`、`workflow-check`。
- `resources` 默认 `{}`；只有 `--resource KIND=PATH` 才创建空表。
- 写入前备份到 `.git/workflow-adoption/backups/<txn>/`，报告写到 `.git/workflow-adoption/reports/<txn>.json`；失败自动回滚。
- 退出码：`0` 成功/Dry-run，`1` 失败，`2` 前置/来源/完整性错误，`3` CONFLICT，`4` BASE_UNKNOWN，`5` 未授权。

### 15.4 升级（B/L/N）

- **B**：上次安装的上游内容；来自 `.agent/upstream.yaml` 指向的精确快照，不能只用 Commit SHA 代替内容。
- **L**：当前宿主内容。
- **N**：新分发包内容。

| 条件 | 处理 |
| --- | --- |
| L=B，N 已变化 | 可计划更新为 N |
| L=N | 无需修改 |
| B=N，L 有改动 | 保留 L |
| B/L/N 三者不同 | `CONFLICT`，保留 L，等待 Owner |
| B 不可用且 L 与 N 不同 | `BASE_UNKNOWN`，不自动覆盖 |
| 上游删除文件，L=B | 需 `--allow-deletes` 才可删除 |
| 上游删除文件，L 已修改 | 保留 L，标记冲突 |
| 文件是宿主保护对象 | 保护优先 |

B 快照缺失或损坏时，只能恢复快照或按 B 重新安装，**不得把远端 HEAD 当作上次安装版本**。冲突、B 缺失、删除未授权 → 整次 fail closed，不做部分写入。

### 15.5 Binary 来源

- 默认从分发包内同一 Commit 的 Engine 构建，注入 `build_source=source`。
- 使用发布资产时注入 `build_source=release`。
- 外部 binary 必须同时提供 `--workflow-check-sha256` 与匹配的 `--workflow-check-commit`，并在替换前通过真实 Gate。
- 未注入的本地开发构建为 `framework_version=dev`、`source_commit=unknown`、`build_source=dev`。

---

## 16. 发布工程

### 16.1 资产矩阵

| 平台 | GOOS | GOARCH | 文件名 |
| --- | --- | --- | --- |
| Linux x86-64 | `linux` | `amd64` | `workflow-check_5.0.0_linux_amd64` |
| Linux ARM64 | `linux` | `arm64` | `workflow-check_5.0.0_linux_arm64` |
| macOS Apple Silicon | `darwin` | `arm64` | `workflow-check_5.0.0_darwin_arm64` |
| Windows x86-64 | `windows` | `amd64` | `workflow-check_5.0.0_windows_amd64.exe` |

固定构建参数：

```text
CGO_ENABLED=0
GOWORK=off
GOFLAGS=-mod=readonly
go build -trimpath -buildvcs=false -mod=readonly \
  -ldflags "-s -w -X main.version=<VERSION> -X main.commit=<HEAD> -X main.buildSource=release"
```

不注入构建时间，保证可复现。

### 16.2 九项正式附件

业务资产 7 项：

```text
4 个平台 Binary
agent-workflow-src-5.0.0.tar.gz
agent-workflow-src-5.0.0.tar.gz.sha256
agent-workflow-release-notes-5.0.0.md
```

校验元数据 2 项：

```text
SHA256SUMS
version.json
```

- `SHA256SUMS` 覆盖 7 项业务资产，不覆盖 `version.json`；
- `version.json` 记录 7 项摘要与 `SHA256SUMS` 自身摘要，自排除；
- 无哈希环。
- 验证等级：`runtime`（仅 Linux x86-64 真实执行 `version`）、`static`（交叉编译 + 文件头/架构/buildinfo）、`content`、`derived`。

### 16.3 CNB 流水线

`.cnb.yml` 中的发布配置针对精确 Tag `v5.0.0`；当前发布与镜像流水线已由 Owner 注释停用，V5.0.0 使用手工发布流程。以下为原流水线阶段，保留供未来恢复时参考：

```text
toolchain → tag-guard → release-context → engine-verification → adoption-e2e
→ build-assets → verify-assets-local → snapshot-expected → check-pipeline-coverage
→ create-pre-release (preRelease=true, latest=false)
→ upload 9 → download 9 → verify-uploaded → promote-gate-pre
→ promote-ga (overlying=true) → reupload 9 → redownload 9 → verify-after-promote
→ promote-gate-post → mark-latest → redownload 9 → verify-after-latest
→ promote-gate-latest → release-verification-report
```

失败语义：任一阶段非零退出，后续不执行；平台侧最多停留在 pre-release；未通过附件核对不得 promote；未通过 promote 后核对不得标记 `latest`。

`.cnb.yml` 的 `main` push 任务把 main 单向镜像到 GitHub，并在推送后回读远端 ref 逐字比对；GitHub 是只读影子，CNB 是唯一写入方。

### 16.4 发布脚本

```bash
python3 scripts/release/release.py guard --repo . --tag v5.0.0
python3 scripts/release/release.py repository --repo . --tag v5.0.0 --json
python3 scripts/release/release.py build --repo . --output dist --tag v5.0.0
python3 scripts/release/release.py verify-assets --dir dist --repo . \
  --version 5.0.0 --tag v5.0.0 --commit <40-hex>
python3 scripts/release/release.py snapshot --assets dist --out dist/expected
python3 scripts/release/release.py verify-uploaded --assets dist/expected --downloads .
python3 scripts/release/release.py promote-gate --stage pre-promote ...
python3 scripts/release/release.py check-pipeline --config .cnb.yml --dir dist
python3 scripts/release/release.py repro --repo . --tag v5.0.0
python3 scripts/release/release.py report --dir dist --reports dist/reports --require-all
```

退出码：`0` 通过，`1` 校验失败/阻塞，`2` 输入/前置条件错误。

---

## 17. 仓库文件地图

| 路径 | 作用 |
| --- | --- |
| `.agent/VERSION` | 源码内容版本（当前 `5.0.0`） |
| `.agent/README.md` | Runtime Namespace 索引与常用命令 |
| `.agent/roles/*.md` | 七个角色 Prompt（各自职责、输入、Git 出站、交接出口） |
| `.agent/specs/WorkflowAgentDirective.md` | Workflow 唯一入口；激活、必读链、启动协议、阶段推导 |
| `.agent/specs/AgentCollaborationSpecification.md` | 协作规范：事实源、Artifact 所有权、路由、Gate、Git 权限 |
| `.agent/specs/Five-AgentResponsibilityBoundary.md` | 角色职责边界 |
| `.agent/specs/WorkflowRouteSelection.md` | 路线判定（TB-ROUTE-1/2）与 R-1~R-4 |
| `.agent/specs/WorkflowHandoffContract.md` | 四字段交接、失败落盘、覆盖矩阵 |
| `.agent/specs/WorkflowGitLifecycle.md` | Stage/Commit/Push/Gate/HANDOFF 时机与完成条件 |
| `.agent/specs/WorkflowLoopCircuitBreaker.md` | 两轮预算、账本、DANGER、Owner 恢复 |
| `.agent/specs/WorkflowAdoption.md` | 首次接入、安装、B/L/N 升级规范 |
| `.agent/specs/tools/git-lifecycle-check` | 只读 Git 自检：`baseline` / `preflight` / `commit-check` / `push-check` / `verify-remote` / `handoff-check` |
| `.agent/templates/` | Adoption 工作单、workflow.yaml、upstream.yaml、空 Registry 模板 |
| `.agent/adoption/` | `dist` / `inspect` / `install` / `upgrade` / `verify` / `restore` 工具与 E2E 测试 |
| `.agent/engine/` | 独立 Go Module：`workflow-check` 与测试 |
| `.agent/engine/internal/workflow/gates.go` | 五个 Gate 及其共享判定 |
| `.agent/engine/internal/workflow/route.go` | R-1~R-4 与 `## Complexity` 解析 |
| `.agent/engine/internal/workflow/paths.go` | Review/Delivery neutral 白名单 |
| `.agent/engine/internal/workflow/state.go` / `state_load.go` | State Schema 与 v2 兼容 |
| `.agent/engine/internal/workflow/config.go` | `workflow.yaml` 严格解析 |
| `.agent/engine/internal/workflow/registry.go` | Registry 表格解析与资源授权 |
| `.agent/engine/internal/workflow/git.go` | 只读 Git 适配器（含 `--no-renames`） |
| `.agent/tasks/<task>/` | 任务运行时证据（宿主数据；框架仓库自托管时也不进分发包） |
| `.agent/workflow.yaml` | 宿主机器配置（不在分发包中） |
| `.agent/upstream.yaml` | 宿主安装来源与 B 快照（只由成功事务写） |
| `.cnb.yml` | 发布流水线与 main 镜像 |
| `scripts/release/release.py` | 发布工程 CLI |
| `docs/design/agent-workflow.md` | 架构设计（WHY） |
| `docs/releases/` | 各版本 Release Notes |
| `PACKAGE_MANIFEST.md` | 分发契约与文件分类 |
| `README.md` | 人类入口 |
| `docs/LLM_PROJECT_CONTEXT.md` | 本文 |

---

## 18. 验证命令

在仓库根目录：

```bash
GOWORK=off go -C .agent/engine test -count=1 ./...
GOWORK=off go -C .agent/engine vet ./...
GOWORK=off go -C .agent/engine build ./...
GOWORK=off go -C .agent/engine test -race -count=1 ./...

python3 .agent/adoption/tests/test_workflow_adoption.py -v
python3 .agent/adoption/tests/test_git_lifecycle.py -v
python3 .agent/adoption/tests/test_taskbuilder_autonomy.py -v
python3 .agent/adoption/tests/test_handoff_contract.py -v
python3 .agent/adoption/tests/test_cold_start.py -v
python3 .agent/adoption/tests/test_route_acceptance.py -v

python3 scripts/release/tests/test_release_scripts.py -v
git diff --check
```

构建并运行 Gate（需在已完成 Adoption 的宿主仓库）：

```bash
GOWORK=off go -C .agent/engine build -o ../bin/workflow-check ./cmd/workflow-check
.agent/bin/workflow-check gate cleaner-start .agent/tasks/<task>
.agent/bin/workflow-check route .agent/tasks/<task>
.agent/bin/workflow-check version --json
```

---

## 19. 能力边界：机器保证 vs SPEC_ONLY

### 19.1 Engine 真正能保证

- `state.yaml` 的 schema 与枚举；
- `blocked.active=true` 时五个 Gate 全部 FAIL；
- Evidence 字段形态、唯一解析、祖先关系、完整 SHA 绑定比较；
- Review/Delivery Tail 的精确白名单（default deny，禁用 rename detection）；
- Contract 内容与批准快照一致；
- Owner Acceptance 绑定当前 CLEAN；
- Delivery 三 SHA 与 develop 基线新鲜度；
- 路线与 Contract / task.md / 历史证据的自洽；
- `handoffReady` 的 clean + local == remote；
- Registry 授权（远端集成分支上的真实行）。

### 19.2 只能由 Prompt / Session / 人工保证

- Session 是否真的独立；
- Git 作者是否真的是该角色；
- Owner 是否真的批准；
- 测试、Gate、构建是否真的执行；
- 账本是否 append-only、轮次是否真实；
- DANGER 报告是否诚实；
- 结构验证是否真的由非 Architect 执行；
- 路由判定质量；
- Git 写约束（Engine 不拦截 `git commit` / `git push`）。

任何文档或回答都不得把 SPEC_ONLY 说成「已获机器认证」。

### 19.3 已声明的验证缺口

- 跨项目 Adoption 的完整真实项目试用尚未执行（DEFERRED BY OWNER）。
- 安装/升级真实 E2E 只覆盖 Linux；macOS 与 Windows 原生未验证。
- 发布 Binary 四平台交叉编译，只有 Linux x86-64 在构建环境真实运行。
- `v5.0.0` Tag/Release/资产是否完成，以 CNB Release 与附件核验为准，文档不预先声称 GA。
- V5.0 启动串、推导表、四字段交接是规范层约束；Engine 不解析交接块。
- 真实 LLM 冷启动验收需人工在独立 Session 执行并记录。

---

## 20. 常见误读

| 误读 | 事实 |
| --- | --- |
| 「五个 Gate = 五个阶段状态机」 | Gate 是**下一角色的入口检查**，不是持久 phase；`state.yaml` 不保存 phase |
| 「`CLEAN` 表示 Cleaner 完成了」 | `CLEAN` 只是审查结论；还必须落盘、commit、push、跑 `owner-gate-start` |
| 「`merge-ready` 表示可以自动合并」 | 它只表示机器条件满足；合并仍是 Owner Authority |
| 「`owner-gate-start` PASS = Owner 已接受」 | 不等于；它只证明 CLEAN 与 Git 收尾有效 |
| 「`blocked` 只是提示」 | `blocked.active=true` 时五个 Gate 一律 FAIL |
| 「路线可以由 Engine 推断」 | Engine 只校验已记录事实，不推断缺失 route |
| 「`task.md` 更新后 CLEAN 仍有效」 | `task.md` 不在 neutral 白名单，修改即失效 |
| 「短 SHA 也可以作为新证据」 | Engine 兼容历史，但新证据必须完整 40 位 |
| 「DANGER 是 Engine 限流」 | 它是协议 + 账本 + `blocked` 的组合；Engine 不解析账本 |
| 「`architecture.md` 可以放在 D1 之后补」 | 它必须与结构实现同处 D1；之后修改即实质变化 |
| 「`workflow.yaml` 会随分发包安装」 | 它是 `generated`，由成功 install/upgrade 生成 |
| 「Gate PASS 等于 Agent 身份可信」 | Engine 不认证身份、Session 或作者 |

---

## 21. 变更影响清单

修改仓库前，按影响面同步：

| 改动 | 必须同步 |
| --- | --- |
| 新增 tracked 文件 | `.agent/adoption/workflow_adoption.py` 分类 + `PACKAGE_MANIFEST.md`，否则 `dist` 失败 |
| 新增/删除 Spec | `scripts/release/release.py` 的 `REQUIRED_SPECS` / `REQUIRED_DIST_PATHS` + `PACKAGE_MANIFEST.md` |
| 新增/删除角色 | `REQUIRED_ROLES` + 七个 Role Prompt 的矩阵 + `PACKAGE_MANIFEST.md` |
| 修改 Gate 逻辑 | `.agent/engine/internal/workflow/*` 测试 + `docs/design/agent-workflow.md` §12 + 相关 Spec |
| 修改 Tail 白名单 | `paths.go` + design §9 + GitLifecycle + Handoff Contract 的失败落盘表 |
| 修改 `state.yaml` schema | `state.go` / `state_load.go` + design §6 + `PACKAGE_MANIFEST.md` 的 `state_schema` + Adoption 兼容性 |
| 修改路线规则 | `route.go` + `WorkflowRouteSelection.md` + design §32 + Role Prompt |
| 修改交接字段 | `WorkflowHandoffContract.md` + 七个 Role Prompt 的 `# handoff-exits` + 静态测试 |
| 修改版本号 | `.agent/VERSION`、`PACKAGE_MANIFEST.md`、`.cnb.yml` 的 Tag/附件名、`docs/releases/<version>.md` |
| 修改发布资产集合 | `scripts/release/release.py` 的矩阵/必需清单 + `.cnb.yml` 的上传/下载/校验顺序 + `PACKAGE_MANIFEST.md` |

改完后至少运行 §18 的 Engine 测试、Adoption 测试、发布脚本测试与 `git diff --check`。

---

## 22. 术语表

| 术语 | 含义 |
| --- | --- |
| Owner | 人类决策者与共享仓库控制者；不是 Agent |
| Role Session | 以某个角色职责运行的独立 Agent 会话 |
| Evidence Commit | 被审批/审查/验收绑定的不可变 Commit（A1/C1/D1） |
| Metadata Commit | 只写 `state.yaml`、引用 Evidence SHA 的提交（A2/C2/D2） |
| Neutral Tail | Evidence 之后允许的、不使结论失效的精确路径变更 |
| `handoffReady` | 非 detached、clean、远端存在、local == remote |
| Route | `NORMAL` / `COMPLEX` / `ARCHITECT` |
| R-1~R-4 | 路线事实不变量 |
| INV-1~INV-7 | 核心不变量 |
| Registry | 集成分支上的共享资源分配表 |
| B/L/N | Base（上次上游）/ Local（宿主当前）/ New（新包） |
| DANGER | 两轮预算耗尽后的停机协议 |
| SPEC_ONLY | 只能由 Prompt、Session 隔离或人工保证，Engine 不认证的约束 |
| Source Distribution | 从干净 Commit 构建的可校验源码包 |

---

## 23. 一句话结论

这个项目的本质是：**把「谁在什么证据上做了什么决定」变成 Git 可复核的事实，把「现在能不能交给下一个角色」变成只读 Gate，把「什么时候必须停下来问人」变成可追溯的停机协议。** 理解这一点，就能正确判断任何改动应该放在 Core、Machine Config、Project Policy 还是 SPEC_ONLY 层。
