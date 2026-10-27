# Agent Workflow V3 — 核心架构设计

> **Workflow V3 Baseline**（`.agent/VERSION` = `3.1.0`）
>
> 当前 Workflow 协作 / 工程架构 major version 为 **V3**。Baseline 包含：independent-session role workflow（`NORMAL` / `COMPLEX` 六角色，`ARCHITECT` 增加 Architect 且不经过 Cleaner，见 §32）、Evidence Snapshot、machine gates、Generic Shared Resources、strict YAML、independent Workflow Engine module、Adoption mechanism。
>
> 已知验证边界：Cross-project Adoption Dogfood **NOT PERFORMED**（DEFERRED BY OWNER）。portability architecture 已静态与内部验证，但尚未在第二个真实项目验证跨项目 Adoption。
>
> 命名约定：正文中「Workflow V2」若指当前架构名称，已被 V3 取代；若指 V1→V2 历史演进、legacy reference 或 state schema v2/v3，则保留历史含义，不随本次 Release Marking 改写。`module workflow-v2-engine` 为 historical technical identifier，本轮不改。

本文是 Workflow V3 的**唯一核心架构 Design 文档**：解释「为什么这样设计」「整个系统如何运行」「各概念之间的关系」「为什么需要这些 Gate / Git / Evidence 规则」。

本文定位是「**为什么**」。`.agent/specs/*`（Workflow Agent Directive、协作规范、职责边界）与 `.agent/roles/*`（各角色 Prompt）负责「**具体怎么做**」，引用本文，不重复定义。

事实源优先级：

1. 当前真实代码 / Validator / Tests（`.agent/engine/internal/workflow/*`、`.agent/engine/cmd/workflow-check/*`）；
2. 当前现行 Agent Prompt / 协作规范（`.agent/specs/*` 与 `.agent/roles/*`）；
3. 本文（`docs/design/agent-workflow.md`）；
4. 历史 Task Artifact（`.agent/tasks/*`，仅作历史证据，不作为现行语义来源）。

---

## 1. 设计目标

Workflow V2 不是抽象的设计稿，而是针对真实执行中暴露的缺陷演进出来的结果。它要解决的是一组具体问题：

| # | 真实问题 | V2 的应对 |
|---|---|---|
| 1 | 多 Agent 长上下文污染：一个 Session 越滚越长，后续角色被前面角色的推理历史干扰 | 每个角色独立 Agent Session，新 Session 只读取持久事实 |
| 2 | 自审偏差：同一个 Agent 自己写代码再自己 Review | Coder 与 Cleaner 分离为两个独立 Session |
| 3 | Owner 被迫记住「下一个角色是谁、流程走到哪」 | 状态机 + Gate 机械推导，Owner 不再承担流程记忆 |
| 4 | Owner 被迫承担机械 Git / Registry 操作 | Analyst Registry-only authority 接管 Registry 落盘，Owner 只做决策 |
| 5 | Conversation 状态和 Git 实际状态不一致 | 以 Git + Repository Artifacts 为跨 Session 唯一事实源 |
| 6 | Dirty Worktree 导致角色之间事实不稳定 | Handoff 前强制 clean exit（`handoffReady`） |
| 7 | Review Target 漂移 | Evidence Snapshot 绑定 immutable commit |
| 8 | Shared Resource collision（resource kind 撞车，如 migration version / 错误码域） | 共享 develop Registry + 只读 Validator 机械授权（Generic Reservation） |
| 9 | Agent 越权修改 shared develop | INV-1 权限边界 + 唯一 Registry-only 例外 |
| 10 | 「业务结果完成」与「Role Session 完成」混淆 | 通用 Completion Contract（业务结果 ≠ Session 完成） |

### 核心原则

- **Conversation 是临时上下文**。聊天历史不承担跨 Session 事实权威。
- **Git + Repository Artifacts 是跨 Session 的持久事实源**。`state.yaml` 是唯一机器权威源，Evidence Artifact（`contract.md` / `findings.md` / `core-logic.md` / `owner-decision.md` / `delivery.md`）是「为什么」。
- **Handoff 是导航信息，不是另一个事实数据库**。它只携带最小必要导航与事实，下一 Session 仍需重新读取 Git 与 Artifact。

---

## 2. 角色模型

`NORMAL` / `COMPLEX`（六个角色）：

```text
TaskBuilder → Analyst → Coder → Cleaner → OwnerGate → Deliverer → Owner Integration
```

`ARCHITECT`（七个角色，不经过 Cleaner）：

```text
TaskBuilder → Analyst → Architect → OwnerGate（V1 结构验证 → V2 Owner 决策）→ Deliverer → Owner Integration
```

Owner 不是序列中的第八个 Agent，而是整个 Workflow 的 **Decision Authority + Shared Repository Authority**（人类控制面）。`ARCHITECT` 是 P05 引入的第三条正式路线，其设计见 §32。

### 为什么每个角色使用独立 Agent Session

- **降低上下文污染**：每个 Session 只看到本角色职责所需的事实，不带入前一角色的推理链。
- **降低自我确认偏差**：Coder 的实现与 Cleaner 的审查分属不同上下文，审查才能真正独立。
- **强化职责边界**：Session 边界即职责边界，角色完成自己阶段后停止，不顺手接管下一角色。
- **角色之间只依赖持久事实**：新 Session 必须重新读取 Git、Task artifacts、Contract、Design、Registry，禁止依赖「上一个 Agent 聊天里已经说过了」。

### 各角色职责

| 角色 | 回答的问题 | 输入 | 主要职责 | 负责的 Artifact | Authority | 明确禁止 | 完成条件 | 下一角色 |
|---|---|---|---|---|---|---|---|---|
| **TaskBuilder** | 交付什么、怎样判断完成 | Owner 需求 | 把需求整理成可执行、可验收的 `task.md` | `task.md`、`state.yaml` 初始结构 | 定义任务边界与路由；受控初始分支创建（§3 例外，P03 定义细则） | 写生产代码；删除/重命名 branch；受控例外之外的分支操作；替 Owner/Analyst 作关键决定 | `task.md` + 初始 `state.yaml` + 明确路由 | Analyst（`COMPLEX` / `ARCHITECT`）或 Coder（`NORMAL`） |
| **Analyst** | 有哪些约束、可行方案、必须保持什么性质 | `task.md` | 调查约束、出方案、写 Contract、派生资源、沉淀长期 Design | `contract.md`、`docs/design/*`、`state.contract`、`state.resources` | **唯一** Registry-only develop mutation authority | 改生产代码/测试；批准自己的推荐；改 shared develop 其他内容 | Contract APPROVED + Registry Reservation 落地 + 两段式提交 | Coder（`NORMAL`/`COMPLEX`）或 Architect（`ARCHITECT`） |
| **Architect**（`ARCHITECT`） | 结构应当怎样成立 | `task.md` + APPROVED `contract.md` | 交付模块边界 / 接口骨架 / 结构测试与 `architecture.md` | `architecture.md`、结构骨架、结构测试、`state.review`（PENDING） | 在 Contract 限定的结构范围内实现 | 完整业务功能、DB Migration、未批准 Schema、新外部依赖、未批准构建/部署变化；写 `CLEAN`/`owner`/`delivery` | D1 结构快照 + D2 Review Metadata | OwnerGate（V1 结构验证） |
| **Coder** | 怎样在既定范围内完成实现 | `task.md` + APPROVED `contract.md` | 实现 AC、写测试、发起 Review Request | 业务代码、migration、tests、`state.review`（PENDING） | 在 feature 内实现 | 自行改 Contract/Task；写 `CLEAN`/`owner`/`delivery`；自行推断资源编号 | 实现 + 两段式提交（C1 + C2） | Cleaner |
| **Cleaner** | 完整变更是否满足任务、测试是否可信 | `task.md` + Contract + C1 | 独立审查、写 Finding、整理核心逻辑、给出 CLEAN | `findings.md`、`core-logic.md`、`state.review`（CLEAN） | 审查结论 | 默认不改生产代码/测试；写 `owner` 状态；替 Owner 最终接受 | CLEAN + artifacts commit/push + `owner-gate-start` PASS | OwnerGate |
| **OwnerGate** | 核心机制是什么、请求 Owner 决策并持久化；`ARCHITECT` 下先独立验证结构 | CLEAN review（`ARCHITECT`：D2 Review Request） | 提炼 1~3 个核心机制、解释给 Owner、持久化 Owner 决定；`ARCHITECT` V1 独立结构验证 | `owner-decision.md`、`state.owner`；`ARCHITECT` V1：`findings.md`、`core-logic.md`、`state.review` | File Writer（**不是** Decision Authority）；`ARCHITECT` V1 验证责任 | 改代码/Contract/结构产物；自行生成 `ACCEPTED` | Owner 决定持久化 + `delivery-start` PASS | Deliverer |
| **Deliverer** | 能否在目标环境真实运行 | CLEAN + Owner ACCEPTED | 里程碑运行验证，记录三个不可变 SHA | `delivery.md`、`state.delivery` | 验证 | merge/push develop；修生产代码；替 Owner 接受 | delivery PASS + `merge-ready` PASS | Owner（集成决策） |

Owner 保留四类真正需要人判断的职责：Task / Branch Boundary、Contract Decision、Core Logic Decision、Shared Integration Decision。

---

## 3. Owner 与 Agent 的权限边界

这是 Workflow V2 的核心之一。权限边界用「**谁控制什么**」而非「谁更聪明」来划分。

### Owner 控制区

1. **Task / Feature Branch Lifecycle**：创建 feature branch、删除 branch、重命名 branch、branch boundary 决策。
   - **受控初始分支例外**（已批准的 P00-R1 契约，V3.1）：TaskBuilder 在任务初始化时可以在受控条件下创建该任务自己的初始 feature branch。它不是通用分支权限，触发条件、允许的命令序列、拒绝条件与证据要求由 P03 在 `.agent/roles/TaskBuilderPrompt.md` 与 `.agent/specs/AgentCollaborationSpecification.md` 中定义。
   - 本节只同步设计语义，不复制 P03 的规则文本，也不把例外扩大为「Agent 可自由创建 / 删除 / 重命名分支」。除此之外，分支生命周期仍是 Owner Authority。两处文本的一致性由集成检查点核对。
2. **Shared Integration**：feature → develop、merge 时机、merge 顺序、merge / squash 策略、最终 integration 决策。

### Agent Autonomous Zone

默认在 Owner 已准备好的 feature branch 内工作。Agent 可以在自己的 Authority 内：

- 阅读代码；
- 修改代码；
- 测试；
- 显式 stage；
- commit；
- push 当前 feature。

但**不能**因为「工作需要」就自行改变 Shared Integration Boundary。共享主线与分支生命周期始终是 Owner Authority。

---

## 4. Analyst Registry-only Develop Authority

这是 `product-view-count-v1` dogfood 发现的第一个真实缺陷，必须作为正式设计写清楚。

### 原问题

Analyst 负责读 Registry、判断资源、计算 migration / error-code domain、形成 Resource Proposal，但最后却必须让 Owner 手工修改 Registry。这导致 Owner 再次沦为机械操作员，违背了「Owner 只做决策」的目标。

### 最终规则

Analyst 是唯一被允许进行 shared-develop mutation 的 Agent 例外。但这不是普通 develop 权限，而是**只允许**修改两个白名单文件：

```text
.agent/registry/migrations.md
.agent/registry/error-codes.md
```

> 这两个文件路径由机器配置 `.agent/workflow.yaml` 声明（见 §29 Machine Configuration）；本文中的两个路径是当前 my-shop 的默认值。

形成 **Registry-only commit**。流程：

```text
git fetch
→ 读取最新 origin/develop Registry
→ 重新计算 next resource
→ 修改 Registry
→ 显式 stage
→ 检查 staged / commit diff
→ commit
→ 正常 push develop
```

### 强约束

- push 前必须确认 diff **只含** Registry 白名单文件，出现任何其他路径 → **STOP**。
- push 因 non-fast-forward 被拒 → **禁止** force / force-with-lease / rewrite shared history；必须 fetch → 重新读取 Registry → 重新计算资源 → 重新形成 Reservation → 再正常 push。

### 语义定性

这是「**共享资源竞争协议**」，不是「Analyst 拥有 develop 开发权限」。TaskBuilder / Coder / Cleaner / OwnerGate / Deliverer 仍没有普通 develop push authority。

---

## 5. Artifact Ownership

一个角色只拥有自己负责的 Artifact。**一个角色不能顺手把另一个角色的未提交 Artifact 一起提交**。

| 所有者 | 拥有的 Artifact |
|---|---|
| TaskBuilder | `task.md`、task initialization / task metadata |
| Analyst | `contract.md`、业务设计文档（`docs/design/*`）、resource proposal / contract 状态 |
| Architect | `architecture.md`、结构骨架、结构测试（仅 `ARCHITECT`；结构验证阶段只读） |
| Coder | 业务代码、migration、tests、实现相关文件 |
| Cleaner | `findings.md`、`core-logic.md`、review 状态（`NORMAL` / `COMPLEX`） |
| OwnerGate | `owner-decision.md`、owner acceptance/rejection 状态；`ARCHITECT` V1 的 `findings.md` / `core-logic.md` / review 状态 |
| Deliverer | `delivery.md`、delivery 状态 |
| Owner | branch lifecycle、final integration |
| shared Registry | Analyst Registry-only exception |

如果发现其他角色 owned artifact 是 dirty，**不要** `git add -A` / `git add .`，而是 STOP / 报告 / 让正确角色处理。

---

## 6. Workflow V2 State Model

### 为什么删除 V1 的 global phase

V1 使用 13 值 `phase` 状态机 + `STALE` 持久状态 + Transition Authority。这套设计的问题在于：

- `phase` 是「谁能行动」的持久事实，但它可以被一次文件编辑伪造，不构成可信的历史。
- `STALE` 是一个持久状态，但「审查是否还有效」本质上是**由 Git 事实动态推导**的结果，持久化它只会引入「状态与事实不同步」的新错误。
- 阶段推进的 Authority 表在 Prompt 层难以强制，V1 Validator 无法证明历史 transition 是否合法。

V2 改为：**不保存 `phase`、`STALE`、`DONE`**。生命周期由 Owner 与 Gate 控制；状态有效性由 immutable Evidence Snapshot + Neutral Tail 机械推导。

`state.yaml` 只保存 Git 无法推导的**工作流决策事实**。明确不保存：`phase`、`STALE`、`DONE`、`branch`、`HEAD`、`working_tree`、`changed_files`、`NEXT_ROLE`、`NEXT_ACTION`。

### 当前真实 `state.yaml` 数据模型

```yaml
schema_version: 3
route: NORMAL | COMPLEX | ARCHITECT        # 可选：缺失表示历史任务（Legacy），新任务必须写
task_id: <task-slug>

contract:
  status: PENDING | APPROVED | REJECTED | NOT_REQUIRED
  target: <evidence-commit-sha | "">

resources:
  reservations:
    <resource-kind>:
      - <opaque-value>

review:
  status: NOT_REQUESTED | PENDING | CLEAN | CHANGES_REQUIRED
  target: <evidence-commit-sha | "">

owner:
  status: PENDING | ACCEPTED | REJECTED | NOT_REQUIRED
  review_target: <review-target-sha | "">

delivery:
  status: NOT_RUN | PASS | FAIL | BLOCKED
  review_target: <sha | "">
  feature_head: <sha | "">
  develop_base: <sha | "">

blocked:
  active: false
  by: ""
  reason: ""
```

要点：

- `resources.reservations` 只声明「需要什么」，不自证满足；真正 Authority 永远读取 shared integration branch 上的 Registry（当前 my-shop 为 `origin/develop:.agent/registry/*`，由 `.agent/workflow.yaml` 声明，见 §29）。
- `resources.reservations.<resource-kind>` 是 **Generic Shared Resource Reservation**：Workflow Core 只认识 resource kind → opaque value 列表，不预置任何具体资源类型（migration / error code 等只是当前 my-shop Project Config 声明的 kind）。
- 每个 reservation value 对 Workflow Core 都是 opaque non-empty string，具体格式正确性属于 Project Policy。
- `schema_version: 3` 是新任务当前 schema；`schema_version: 2` 仅为历史任务做 Legacy Read Compatibility（读取后 runtime normalize，不写回磁盘）。其它 schema version 调用 Gate → `ERROR`（exit 2）。
- `route` 是 V3.2.0 的可选决策事实（`NORMAL` / `COMPLEX` / `ARCHITECT`），由 TaskBuilder 写入并与 `task.md` 的 `## Complexity` 一致；缺失表示历史任务，Engine 不推断其路线。不变量与 Engine 检查见 `.agent/specs/WorkflowRouteSelection.md`（R-1 ~ R-4）。

---

## 7. Evidence Snapshot

这是 Workflow V2 最核心的设计之一。

### 为什么「当前 HEAD」不能直接等价于「被批准 / 被审查的对象」

工作区与分支 HEAD 是**移动的**：Coder 提交 C1 后又提交 metadata，Cleaner 开始时 HEAD 可能又前进了。如果「审查对象」=「Cleaner 开始时的 HEAD」，那么 Owner / Cleaner / Deliverer 讨论的就不是同一个不可变对象，任何后续 commit 都会让结论失去锚点。

### Evidence Snapshot 的概念

一个审批 / Review 必须绑定一个**不可变 Git commit**。例如：

```text
Analyst：
  A1 = Contract Evidence Commit
  A2 = Metadata / State Commit
  contract.target = A1

Coder：
  C1 = Implementation Evidence Commit
  C2 = Metadata / State Commit
  review.target = C1

Architect（ARCHITECT）：
  D1 = 结构实现快照（结构骨架 + architecture.md + 结构测试）
  D2 = Metadata / State Commit
  review.target = D1
```

Cleaner 审查的是 **C1**，而不是「Cleaner 开始工作时当前 HEAD」。`ARCHITECT` 下由 OwnerGate 的 V1 阶段审查 **D1**（§32），D1 之后只允许 Review-neutral Tail。这样 Owner / 审查方 / Deliverer 讨论的是同一个不可变对象。

三种 Evidence Binding：

| Binding | 绑定对象 | 语义 |
|---|---|---|
| Contract（INV-2） | `contract.target` = A1 | Owner 最终批准的 `contract.md` Evidence Commit |
| Review（INV-3 / INV-4） | `review.target` = C1 或 D1 | 审查方真正审查的 immutable Evidence Commit：`NORMAL` / `COMPLEX` 为 Cleaner 审查的 C1；`ARCHITECT` 为 OwnerGate V1 验证的 D1 |
| Delivery（INV-6） | `delivery.review_target` / `feature_head` / `develop_base` | 被验证的业务实现 / 实际参与集成的 feature snapshot / 验证时的 develop 基线 |

### Evidence 字段的写入形态（F-03）

新证据一律写入**完整 40 位 Commit SHA**。`contract.target`、`review.target`、`owner.review_target`、`delivery.review_target` / `feature_head` / `develop_base` 都不得写入 `HEAD`、`HEAD~1`、分支名、Tag、`origin/<branch>` 或其他可移动 Git Ref。

Engine 为保护历史兼容仍接受合法的 7～40 位十六进制短 SHA（`resolveImmutableCommit`：形态校验 → 唯一解析 → 前缀一致性 → 返回完整 SHA）。这条兼容不构成新任务使用短 SHA 的许可；所有引用必须先解析并核验真实身份，绑定比较统一使用完整 SHA，避免历史短 SHA 与完整 SHA 因字面值不同被误判为不同绑定。历史任务中已存在的可移动引用属于 Adoption / 升级迁移问题，不在 Engine 内放宽。

---

## 8. Evidence Snapshot 为什么需要两阶段 Commit

一个实际的机制约束：**Commit 本身不能在创建之前知道自己的 SHA**。因此不能试图让 Evidence Commit 自己写入「我的 SHA 是 xxx」。

解决办法是**两阶段提交**：

```text
Evidence Commit（内容）
      ↓
Metadata Commit（只写 state.yaml，引用 Evidence Commit 的 SHA）
```

- Analyst：A1（contract 内容）→ A2（只写 `state.contract.status=APPROVED`、`state.contract.target=A1`）。
- Coder：C1（业务代码 + 测试）→ C2（只写 `state.review.status=PENDING`、`state.review.target=C1`）。
- Architect（`ARCHITECT`）：D1（结构骨架 + `architecture.md` + 结构测试）→ D2（只写 `state.review.status=PENDING`、`state.review.target=D1`）。

Metadata Commit 是「事后引用」Evidence Commit 的 SHA，因此 SHA 可以精确落盘。Cleaner 审 C1，不是 C2；`ARCHITECT` 下 OwnerGate V1 审 D1，不是 D2。

`A1` / `C1` / `D1` 在这里是 Evidence Commit 的符号名；写入 `state.yaml` 时必须是该 commit 的**完整 40 位 SHA**，不能是 `HEAD`、分支名、Tag 或短 SHA（见 §7 Evidence 字段的写入形态）。

---

## 9. Neutral Tail

Evidence Snapshot 之后可以存在**有限**的 metadata commit，但必须严格限制。

核心规则：**task-scoped、exact path matching、default deny**。白名单必须精确限定在 `.agent/tasks/<current-task>/` 下，禁止 basename-only 宽松匹配。

### Review-neutral tail（`review.target` 之后允许）

```text
.agent/tasks/<task>/state.yaml
.agent/tasks/<task>/findings.md
.agent/tasks/<task>/core-logic.md
.agent/tasks/<task>/owner-decision.md
.agent/tasks/<task>/delivery.md
.agent/tasks/<task>/loop-ledger.md
.agent/tasks/<task>/danger-report.md
```

### Delivery-neutral tail（`feature_head` 之后允许，更窄）

```text
.agent/tasks/<task>/state.yaml
.agent/tasks/<task>/delivery.md
.agent/tasks/<task>/loop-ledger.md
.agent/tasks/<task>/danger-report.md
```

`loop-ledger.md` 与 `danger-report.md` 是 V3.1 循环熔断的证据文件（§31）。它们进入两条 Tail 的原因不同：Review Tail 允许 CLEAN 之后继续记录循环，Delivery Tail 允许 Delivery PASS 之后记录 DANGER 与 Owner 授权的恢复动作。两者都是**精确文件名 + 当前 Task 根目录**豁免，不扩大到其他目录、其他 Task 或本 Task 子目录。

`architecture.md` **不在**两条 Tail 的白名单内。它是 `ARCHITECT` 的结构事实，必须与结构骨架、结构测试一起进入 D1；D1 之后修改它属于 substantive change，旧 CLEAN 立即失效。这是有意的：结构说明与结构实现必须在同一个不可变快照里，否则无法证明被审查的结构就是被描述的结构。

### 判定逻辑（以真实代码为准）

- `isReviewSubstantial(task, path)` = `!isReviewNeutral(task, path)`，即 default-deny：路径不在「本 task 目录 + 白名单文件名」内，一律判 substantive。
- 其他目录、其他 task 的同名文件（如 `.agent/tasks/other/state.yaml`、`docs/design/state.yaml`、`.agent/registry/state.yaml`）一律 substantive。
- `.go`、`.sql`、业务测试修改、业务设计修改都属于 substantive change。一旦出现，旧 `review.target` 的 CLEAN 就不再有效（INV-4，机械失效，不保存 STALE）。
- **Git Diff 禁用 rename detection**：review tail 与 delivery tail 的判定都使用 `git diff --no-renames --name-only`。Git 默认把 `src/keep.go → .agent/tasks/<task>/findings.md` 折叠成单一目标路径，会让生产代码「消失」在 Tail 中；禁用后同一次 rename 同时暴露源路径删除与目标路径新增，default-deny 会分别拒绝。该选项只让变更路径集合更完整，不会漏报。
- **Review Target 的祖先验证**：`review.target` 必须解析为不可变 commit，并且是当前 feature HEAD 的祖先或同一 commit。Tree Diff 为空只说明两棵树相同，不能证明 target 属于当前 feature 历史；被历史改写孤儿化的 target 即使树完全一致也必须判 CLEAN 无效。`cleaner-start`、`owner-gate-start`、`delivery-start`、`merge-ready` 复用同一实现，四个 Gate 的语义一致。

---

## 10. 核心不变量（INV-1 ~ INV-7）

- **INV-1 Shared Branch Authority**：Only Owner mutates shared develop（branch 生命周期与最终 feature→develop integration）。Agent 不删除或重命名 task branch、不最终 merge、不 push develop。两个**窄例外**：Analyst 的 Registry-only develop commit（仅 `.agent/workflow.yaml` 声明的 Registry 文件，当前 my-shop 为 `.agent/registry/migrations.md` 与 `.agent/registry/error-codes.md`）；TaskBuilder 的受控初始分支创建（§3，细则由 P03 定义）。受控例外不构成通用分支权限。
- **INV-2 Implement Authorization**：Coder 开始前 Contract valid + 每个 Shared Resource Reservation 由 `origin/<integration-branch>` 上对应的 Registry 授权（value 存在、owner 与当前 Task 一致、status 为 RESERVED/ACTIVE）。
- **INV-3 Immutable Review**：Cleaner 只能 CLEAN 明确 immutable commit，不能 CLEAN working tree。
- **INV-4 Review Freshness**：任何 substantive change 使旧 CLEAN 自动失效；不保存 STALE。
- **INV-5 Owner Binding**：Owner ACCEPTED 必须绑定当前 CLEAN review target（`owner.review_target == review.target`）。
- **INV-6 Delivery Binding**：Delivery PASS 必须绑定 current review target + exact develop base；develop 前进后旧 PASS 不再 Merge Ready。
- **INV-7 Evidence Snapshot Integrity**：Contract Approval、Cleaner Review、Delivery Verification 都必须绑定明确 immutable Evidence Snapshot；Evidence Snapshot 之后只允许该阶段定义的 neutral metadata tail，任何超出白名单的变化都会使对应验证结果失效。

---

## 11. workflow-check 的职责

`.agent/engine/cmd/workflow-check` 是**只读 Validator**，与业务二进制 `my-shop` 分离。Workflow Engine 是独立 Go Module（module `workflow-v2-engine`，位于 `.agent/engine/`），不依赖宿主业务 Go Module、GoFrame、MySQL / Redis 或任何业务包；只依赖 Git 仓库与 Go toolchain。

它只做：读取事实 → 判断 Gate → 输出 `PASS / FAIL / ERROR`。

它**不能**：

- 修改 YAML / 修改 Registry / reserve resource；
- 创建 branch / stage / commit / push / merge；
- 修改业务代码；
- 替 Owner 做业务决定。

### 当前真实 CLI

```text
workflow-check gate <coder-start|cleaner-start|owner-gate-start|delivery-start|merge-ready> <task> [--root DIR]
workflow-check route <task> [--allow-legacy] [--root DIR]
workflow-check version [--json]
```

`route` 是路线事实自检（R-1 ~ R-4），**不是 Gate**：不产生 Handoff 授权，也不改变 Gate 语义。默认 strict，历史任务必须显式 `--allow-legacy`。

integration branch 与 Registry 路径来自 `.agent/workflow.yaml`（见 §29 Machine Configuration），不再由 CLI 写死；本地 develop 分支不是权威。

### 构建与运行（独立 Engine Module）

Workflow Engine 是 source-vendored nested Go Module，与业务 Module 彻底独立，不通过 `go.work` / `replace` 关联：

```text
# 构建本地 binary（进入 Git ignore，不进入 Git）
go -C .agent/engine build -o ../bin/workflow-check ./cmd/workflow-check

# 运行（正式 Gate 机器入口优先 direct binary，保留 0/1/2 exit code）
.agent/bin/workflow-check gate <gate> <task>
.agent/bin/workflow-check route <task>

# 测试 / 静态检查（Engine 与 Business 分开运行）
go -C .agent/engine test ./...
go -C .agent/engine test -race ./...
go -C .agent/engine vet ./...
go -C .agent/engine build ./...
```

Repository Root 始终通过 `git rev-parse --show-toplevel` 解析，与 Engine Module Root（`.agent/engine/`）无关；从 repo 根、`.agent/`、`.agent/engine/` 任一 cwd 运行 binary 都应解析到同一个 Repository Root，并读取 `<repo-root>/.agent/workflow.yaml`。

### exit code

| code | 含义 |
|---|---|
| 0 | PASS |
| 1 | Invariant / Gate FAIL |
| 2 | Input / Runtime ERROR（含对非 V2 task 调用 gate） |

Validator 不代理任何 git 写操作；Agent 的 git 写约束属于协议 / Prompt 层，shared develop 的机器级保护交给仓库 branch protection / 外部 Git 控制能力。

---

## 12. 五个 Gate

Gate 是 Handoff 前的机械出站检查。每个 Gate 回答一个「现在能否交给下一角色」的问题。

| Gate | 为什么存在 | 关键前置 |
|---|---|---|
| `coder-start` | 保证实现者开始前 Contract / Resource 已合法（`ARCHITECT` 下实现者是 Architect，语义不变） | `blocked.active=false` + `handoffReady` + Contract 有效 + Resources 授权 |
| `cleaner-start` | 保证审查方拿到的是稳定 Implementation Evidence（`ARCHITECT` 下由 OwnerGate V1 消费） | `blocked.active=false` + `review.status=PENDING` + target 真实且在 feature 历史 + `handoffReady` + review tail 仅 neutral |
| `owner-gate-start` | 保证审查方已完成 CLEAN 持久化和 Git 收尾（`NORMAL` / `COMPLEX` 为 Cleaner，`ARCHITECT` 为 OwnerGate V1） | `blocked.active=false` + `handoffReady` + `review.status=CLEAN` + cleanValidity PASS |
| `delivery-start` | 保证 Owner 已绑定当前 CLEAN review | `blocked.active=false` + Contract/Resources 有效 + cleanValidity + `owner.status=ACCEPTED`（`NOT_REQUIRED` 不构成接受事实）+ `owner.review_target==review.target` + `handoffReady` |
| `merge-ready` | 只表示 feature 已满足交给 Owner 集成的机械条件，**不代表 Agent 可以自行 merge** | `blocked.active=false` + 上述全部 + `delivery.status=PASS` + delivery 三个 SHA 绑定 + develop/feature 新鲜度 + `handoffReady` |

**`blocked` 对五个 Gate 一律生效**：每个 Gate 在 schema 校验之后、其他昂贵检查之前，先执行同一条 `blocked.active` 拦截，`Actual` / `Expected` 完全一致，只有 Reason 描述各自被禁止的动作。因此 DANGER 停机不需要新增 Gate，也无法靠换 Gate 绕过：`blocked.active=true` 时没有任何一个入口能 PASS。

### 12.1 路线级 Gate 顺序

Gate 由当前角色在出站时运行，是**下一角色的入口检查**。

```text
NORMAL：
TaskBuilder（Commit + Push + 远端同步核验；随后运行 coder-start 作为 Coder 入口 Gate 的提前自检）
  → coder-start    → Coder
  → cleaner-start  → Cleaner
  → owner-gate-start → OwnerGate
  → delivery-start → Deliverer
  → merge-ready    → Owner

COMPLEX：
TaskBuilder（contract.status=PENDING；Commit + Push + 只读同步自检，不运行 coder-start）
  → Analyst：A1 Contract Evidence → A2 Approval Metadata（Commit + Push）
  → coder-start    → Coder
  → cleaner-start  → Cleaner
  → owner-gate-start → OwnerGate
  → delivery-start → Deliverer
  → merge-ready    → Owner

ARCHITECT：
TaskBuilder（contract.status=PENDING；Commit + Push + 只读同步自检，不运行 coder-start）
  → Analyst：A1 Contract Evidence（限定结构交付）→ A2 Approval Metadata（Commit + Push）
  → coder-start    → Architect（复用「实现授权」检查，不使其成为 Coder）
  → D1 结构快照 → D2 Review Metadata（Commit + Push）
  → cleaner-start  → OwnerGate V1 独立结构验证（新 Session；持久化 CLEAN，review.target=D1）
  → owner-gate-start → OwnerGate V2 Owner 决策（Commit + Push）
  → delivery-start → Deliverer（结构交付验收）
  → merge-ready    → Owner
```

- `coder-start` 要求 Contract 为 `APPROVED` 或 `NOT_REQUIRED`，因此 `COMPLEX` / `ARCHITECT` 的 `PENDING` 阶段不得运行它，也不得为了通过它把 Contract 改成 `NOT_REQUIRED`。
- `owner-gate-start` `PASS` 只证明 CLEAN 与 Git 收尾满足机器约束，**不等于 Owner 已经接受**；OwnerGate 必须进入明确的 Owner 决策阶段。
- `delivery-start` `PASS` 只表示可以开始验收；`merge-ready` 才是最后一个 Agent Gate，`PASS` 之后才允许向 Owner 输出可集成结论。
- `ARCHITECT` 由 P05 接入：TaskBuilder 出口与 `COMPLEX` 相同（`PENDING` → Analyst），新增 Architect Role，结构验证由 OwnerGate 的 V1 阶段在独立 Session 中承担，**不经过 Cleaner**，且**不新增 Gate**（五个 Gate 的语义与 `blocked` 拦截完全不变）。不得被删除或降级为 `NORMAL`。

---

## 13. handoffReady

正常角色 Handoff 需要满足的 Clean Exit 前提（每个出口 Gate 的共同前提）：

- 当前不是 detached HEAD；
- working tree clean；
- remote feature（`origin/<current-feature>`）存在；
- local HEAD == remote feature HEAD。

**核心原因**：如果 Artifact / Code 只存在于本地 dirty working tree，下一 Session 接手时事实是不稳定的。「Handoff Ready」本质上是「跨 Session 事实已经稳定落到 Git」。

---

## 14. CLEAN != Cleaner Session Complete

这是 `product-view-count-v1` dogfood 的第二个真实缺陷，必须单独说明。

### 原问题

Cleaner 审查完成 → `review.status = CLEAN` → 写 `findings.md` → 写 `core-logic.md` → 更新 `state.yaml` → 直接宣布 OwnerGate。但这些 Cleaner Artifact 尚未 commit / push，导致 `owner-gate-start` 正确失败（working tree dirty）。

### 正式设计原则

**CLEAN 只代表「审查结果为 CLEAN」，不等于 Cleaner Session 完成。**

Cleaner 得出 CLEAN 后仍必须完成：

1. 持久化 `findings.md`；
2. 持久化 `core-logic.md`；
3. 持久化 review state（`review.status=CLEAN`、`review.target=C1` 不变）；
4. `git status --short`；
5. 只 stage Cleaner-owned files；
6. `git diff --cached`；
7. commit；
8. push current feature；
9. 确认 worktree clean；
10. 确认 local == remote；
11. 运行 `owner-gate-start`；
12. PASS 后正式 HANDOFF → OwnerGate。

若 `owner-gate-start` FAIL：不得宣布 HANDOFF，Cleaner Session 不结束；失败原因在 Cleaner authority 内则继续修复，需要 Owner decision / action 则进入相应 Waiting 机制，真正无法继续才 BLOCKED。

同时说明：Cleaner artifact commit 是 **review-neutral tail**。`review.target` 必须继续指向 Coder 的 Implementation Evidence Commit C1，**不能前移成 Cleaner 自己的 commit**。

---

## 15. 通用 Completion Contract

把 Cleaner Finding 提升为通用原则：**Business Result != Role Session Complete**。

正常角色正式 Handoff 前必须：

1. 当前角色职责全部完成；
2. owned artifacts 已持久化；
3. 应 commit 的已 commit；
4. 应 push 的已 push；
5. working tree clean；
6. remote synced；
7. 下一角色入口 Gate PASS；
8. NEXT_ROLE 明确；
9. NEXT_ACTION 明确；
10. NEXT_TARGET 明确。

只有这样才允许正式 HANDOFF。

---

## 16. Handoff 的真正语义

Handoff 是「**当前角色结束，把控制权正式交给下一个角色**」。

它**不是**：

- 「我暂时需要 Owner 回答一个问题」（那是 Decision Checkpoint）；
- 「我暂时需要 Owner 做一次 branch 操作」（那是 Owner Action，非 Handoff）；
- 「同一根因已经两轮，必须停下」（那是 DANGER 停机，输出 `RESULT=DANGER`，不交接下一角色）。

正常 Handoff：当前 Session 到此结束。下一角色：新开独立 Session。如果未来流程再次回到同一个角色，也是新的 Session。

---

## 17. Decision Checkpoint

Decision Checkpoint（`WAITING_FOR_OWNER_DECISION`）**不是 Handoff**。

例如 Analyst 发现设计决策需要 Owner 确认：进入 `WAITING_FOR_OWNER_DECISION`，但 Analyst Session 不结束，Owner 在当前 Session 回答，Analyst 继续原 Session。

同理，OwnerGate 在请求 Owner 核心逻辑决策时也使用 `WAITING_FOR_OWNER_DECISION`，Session 保持。

这不是 HANDOFF → Owner。

---

## 18. Owner Action（当前语义）

当前 Workflow V2 **不保留**一个名为 `WAITING_FOR_OWNER_ACTION` 的持久状态。

Owner-only 动作（例如 Owner 创建 / 切换 feature branch；§3 的 `TB-BRANCH-1` 受控初始分支创建除外）发生在**角色 Session 之外**：Owner 在流程开始前或角色交接间隙准备 branch。如果 Agent 在 Session 中发现需要 Owner 执行某个只有 Owner 有 Authority 的动作、且动作完成后原角色仍需继续工作，这属于**同 Session 内的一次 Owner Action 请求**——Agent 暂停等待，Owner 完成后原 Session 继续，不机械理解成 Role Handoff，也不持久化一个独立状态。

只有当前角色**真的无法继续**、且不能通过简单 Decision / Owner Action 恢复时，才进入 `blocked.active=true`（BLOCKED，见 §19）。

---

## 19. BLOCKED

`blocked`（`state.yaml` 中的 `active / by / reason`）应用于：当前角色真的无法继续，且不能通过简单 Decision Checkpoint / Owner Action 恢复。

不要把所有「需要 Owner 做一下动作」都写成 BLOCKED Handoff。BLOCKED 是正交阻塞事实，不是生命周期阶段。

V3.1 起 `blocked` 还承担**循环熔断停机**（§31）：同一根因的两轮预算耗尽时，DANGER 协议把 `blocked.active` 置为 `true` 并停止 Workflow。该路径不新增状态字段，也不改变 `blocked` 的语义——它仍然是「真实阻塞事实」，只是由协议而非 Gate 写入。解除 `blocked` 只表示恢复条件成立，**不**使旧 Review / Owner / Delivery 结论重新有效。

---

## 20. Handoff 数据内容

Handoff 只携带**最小必要导航和事实**，不复制完整推理过程、不携带大量主观评价、不替下一 Reviewer 做判断。

V3.2.0 起出站字段由 `.agent/specs/WorkflowHandoffContract.md` 统一定义，七个角色一致：

```text
RESULT / CURRENT_ROLE / NEXT_ROLE / NEXT_ACTION / NEXT_TARGET / HANDOFF_PROMPT
```

- **Git SHA** 与 **`REMOTE_SYNCED=YES`**（local == `origin/<current-feature>`）是 `NEXT_TARGET` 必须携带的事实，不是可选说明；
- `NEXT_ROLE` 必须唯一（`NEXT_ROLE=NONE` 是合法停止），`NEXT_ACTION` 以 `HANDOFF` / `FAILURE_HANDOFF` / `OWNER_DECISION` / `SAME_SESSION` / `STOP` 之一开头；
- 跨角色失败必须附完整可执行 `HANDOFF_PROMPT`；DANGER、BLOCKED 与 Owner 决策等待不得生成下游执行 Prompt；
- Owner 决策检查点与 OwnerGate V1→V2 是同 Session 推进，不是跨 Session Handoff。

其余（已完成的 Artifact、关键结论、非阻塞风险）只保留决策所需内容。Handoff 给「路线」，Git + Artifacts 给「事实」。Git 提交与 HANDOFF 的完成条件、七角色例外矩阵见 `.agent/specs/WorkflowGitLifecycle.md`。

---

## 21. Git Authority / Safety

### 允许的普通操作

```text
fetch / status / diff / log / show / rev-parse
switch 到已存在且属于当前任务的分支
pull --ff-only 当前 feature
显式 add
commit
push 当前 feature
```

### 禁止的高风险操作

```text
force / force-with-lease
reset --hard
clean -fd
rebase 已发布历史
随意 amend 已发布 commit
删除 remote branch
git add -A / git add .
```

Analyst 的 Registry-only develop exception 单独按 §4 Registry Authority 规则处理。

TaskBuilder 的受控初始分支创建（`TB-BRANCH-1`）单独按 §3 与 `.agent/roles/TaskBuilderPrompt.md` 处理：仅限 Owner 逐项授权的**当前任务初始** Feature Branch，不构成通用创建分支权限，也不改变本节其余允许 / 禁止项。

---

## 22. Git 当前事实优先

如果 Agent Prompt 计划「修改 → stage → commit → push」，但 Agent 检查真实 Git 后发现目标修改**已经存在于一个已 push commit**，则应**接受真实 Git 事实**，不要为了「匹配计划步骤」重复提交。

更不能仅仅因为 commit subject 和 Prompt 建议的不完全一样，就 amend / rewrite history / force push。

**计划步骤不是事实，Git 是事实。**

---

## 23. Commit Message 规范

默认格式：

```text
<type>(<scope>): <中文摘要>
```

示例：

```text
feat(product): 增加商品浏览量计数
fix(workflow): 修复 Cleaner 过早交接问题
docs(workflow): 明确 HANDOFF 出站条件
test(product): 补充浏览量并发测试
```

- `type` / `scope` 保留英文 Conventional Commit 关键字（常用 `feat` / `fix` / `refactor` / `test` / `docs` / `chore`）。
- `summary` 默认中文。不要求 Agent summary 必须英文，不因中文 commit message 阻塞流程。

同时明确：Commit Message 是规范，不是重写历史的理由。已有已 push commit，只要内容正确，不因语言或 scope 不完全符合建议而 amend / rebase / force push。

---

## 24. Deliverer 与 Merge Ready

Deliverer **不负责最终 merge**。Deliverer 负责验证：当前 feature + 当前 review target + 当前 develop base 是否满足交付条件。

- delivery PASS 只意味着「可以交给 Owner 做 Shared Integration」。
- `merge-ready` 也只意味着「机械条件已满足」。
- 最终 feature → develop 仍然是 Owner Authority。

---

## 25. Delivery Freshness

`delivery` 绑定三个不可变 SHA：

```text
delivery.review_target  # 被验证的业务实现 C1
delivery.feature_head   # 实际参与集成验证的 feature snapshot
delivery.develop_base   # 验证时 shared develop 基线
```

为什么都需要：Deliverer PASS 后自己仍可能产生 `delivery.md`、`state.yaml`，因此不能简单要求 `current HEAD == feature_head`。真实实现（`deliveryFreshness`）改为：

1. `delivery.feature_head` 是当前 remote feature HEAD 的祖先；
2. `feature_head..remote HEAD` 只允许当前 task 的 delivery-neutral artifacts；
3. 当前 `origin/develop == delivery.develop_base`。

`origin/develop` 前进后，旧 PASS 自动失去 Merge Ready 效力。

DANGER 恢复同样受这条规则约束：解除 `blocked`、补记 `loop-ledger.md` / `danger-report.md` 之后，若 `delivery.feature_head..remote feature HEAD` 之间出现了这两个证据文件之外的变更，或 `origin/develop` 已前进，旧 Delivery PASS 仍然失效，必须重新执行 `delivery-start` 并重新绑定三个 SHA。Neutral Tail 只豁免证据文件本身，不豁免实质变化。

---

## 26. Registry Authority 与 Generic Shared Resource 的关系

本轮只整理当前 Workflow 内部语义，**不设计跨项目迁移机制**。

Registry（`.agent/registry/*`，位于 `develop`）的作用是：在**同一个仓库的并行任务之间**协调**项目声明的 Shared Resource Kind** 的分配，避免资源碰撞。它属于 Workflow shared coordination state。

P2 起 Registry 的业务含义泛化：Workflow Core 不预置任何资源类型，只把每个 Registry row 抽象为 `Value / Owner / Status`（第 1 列 = Value、倒数第 3 列 = Owner、倒数第 2 列 = Status）。`migration_version` / `error_code_domain` 只是当前 my-shop 通过 `.agent/workflow.yaml` 声明的 resource kind，不是 Workflow Core builtin type。

- 状态三态：`RESERVED`（已落 develop、Feature 未合并）/ `ACTIVE`（已合并，终态）/ `RELEASED`（取消释放）。
- 复用规则由各 resource kind 的 Project Policy 决定（如 my-shop 的 migration version 一经分配永久 tombstone、错误码域仅纯 `RESERVED` 阶段可复用）；Workflow Core 不承载这些业务规则。
- 并行竞争防护：无外部锁，以 Git 提交顺序 + 冲突检测串行化——先提交到 develop 者胜。

跨项目迁移 / 复用的具体流程见 §28 Project Adoption 与 `.agent/specs/WorkflowAdoption.md`。

---

## 27. Workflow V2 Dogfood Findings

只记录真正改变 Design 的 Finding。

### Finding #1：Analyst 无 Registry mutation authority

- **问题**：Analyst 负责 Resource Reservation，但没有 Registry mutation authority，导致 Owner 变成机械 Registry 操作员。
- **修复**：授予 Analyst Registry-only develop authority（§4）。

### Finding #2：Cleaner CLEAN 后过早进入 OwnerGate

- **问题**：Cleaner 得出 CLEAN 后在 Artifacts 尚未 commit / push 时过早进入 OwnerGate，`owner-gate-start` 因 working tree dirty 正确失败。
- **修复**：CLEAN != Session Complete（§14）。Handoff 前必须完成 Artifact persistence + Git clean exit + next gate PASS。

这两个 Finding 说明为什么 V2 要进行真实 dogfood，而不能只靠静态设计。

---

## 28. Project Adoption / Cross-project Migration

Workflow V2 Core（Roles、Authority、Evidence Snapshot、Handoff、Gate、Completion Contract、Git safety model、Registry coordination concept）跨项目稳定，可以迁移。项目差异（框架、目录、build/test、migration、design mapping、shared resource applicability、项目工程约束）通过**一次性 Project Adoption** 解决。

关键结论：

- Workflow Core 可迁移；项目差异由一次性 Adoption 解决。
- `PROJECT_ADAPTATION.md` 是**临时工作单**，只存在于迁移 / Adoption 过程中。
- Adoption 完成后 `PROJECT_ADAPTATION.md` 必须删除，不成为长期事实源。
- Adoption 不新增长期 Agent Role；完成后 `NORMAL` / `COMPLEX` 回到原六角色模型（`ARCHITECT` 的 Architect 由路线启用，不来自 Adoption）。
- 正常运行不依赖 `PROJECT_ADAPTATION.md`。

Adoption 的五阶段（DISCOVER → RESOLVE → MATERIALIZE → VERIFY → CLEANUP）、`PROJECT_ADAPTATION.md` 生命周期、Migration / Registry 的 `ENABLED / DISABLED / NOT_APPLICABLE` 抽象、以及当前源项目 Validator 的项目耦合清单，详见 `.agent/specs/WorkflowAdoption.md`，不在本文重复。模板见 `.agent/templates/PROJECT_ADAPTATION.template.md`。

---

## 29. Machine Configuration（.agent/workflow.yaml）

Workflow 有三层事实：

1. **Workflow Core**：Roles、Authority、Evidence Snapshot、Handoff、Gate、Completion Contract、Git safety model、Registry coordination concept——跨项目稳定，固化在 `.agent/engine/internal/workflow/*` 与 `.agent/specs/*`、`.agent/roles/*`。
2. **Machine Configuration**：`.agent/workflow.yaml`——「跨项目会变化、且 Workflow Engine runtime 必须知道的最小参数」。
3. **Project Policy**：README / Makefile / `docs/design/*` / 宿主根 `AGENTS.md` 等——项目工程约束（宿主所有，可选），供 Agent 读取，不供 `workflow-check` 机器解析。Workflow 专用指令入口不属于这一层，见 §30。

`.agent/workflow.yaml` 是长期机器配置，**不是** `PROJECT_ADAPTATION.md`（一次性临时工作单）、不是 Task Artifact、不是业务配置。它在 Adoption 完成后长期存在，供 `workflow-check` 读取。

当前 v1 schema：

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

约束：

- `schema_version` 必须为 `1`；缺失或不支持 → exit 2。
- `git.integration_branch` 必填；Engine 据此构造 `origin/<integration_branch>`。
- `resources` 是 arbitrary map：`resource kind → { registry }`。Workflow Core 不预置任何固定 kind，新增 `im_migration_version` 等 kind 无需修改 Go 代码。
- 每个 resource kind 必须：
  - 是合法 `lower_snake_case`（`^[a-z][a-z0-9_]*$`）；
  - 有非空 registry 路径，且路径规范化后严格位于 `.agent/registry/` 下（不能 absolute、不能 `..` traversal）；
  - 不同 kind 不得声明完全相同的 registry 路径（避免无 kind discriminator 的解析歧义）。
- 配置损坏（缺失 / 无法解析 / 缺 `integration_branch` / 非法 kind / 缺 registry / registry 路径非法 / registry 无法读取）一律 exit 2，不误报为「resource not reserved」。

硬编码不等于错误：`.agent/tasks/<task-id>` 仍属于 Workflow Core Convention，本轮不配置化。

### Generic Shared Resource Reservation 三层关系

P2 后，Workflow Core 只认识 Generic Shared Resource Reservation，不再认识 migration / error code 等具体业务语义。三者关系：

```text
Task State
resources.reservations.<resource-kind>
        ↓ 按 kind 动态绑定
Machine Configuration
resources.<resource-kind>.registry
        ↓ 读取 origin/<integration-branch>:<registry-path>
Shared Registry
reservation row（Value / Owner / Status）
```

`migration_version` / `error_code_domain` 只是当前 my-shop Project Config 声明的 resource kind，不是 Workflow Core builtin type。未来新增 `im_migration_version`、`nats_subject` 等 kind 只改 `.agent/workflow.yaml`，无需修改 Validator Core。

---

## 30. Agent Directive 隔离（V3.1）

V3.0.0 及更早版本把 Workflow 入口放在仓库根 `AGENTS.template.md`，Adoption 时落到宿主根 `AGENTS.md`。这带来三个问题：

1. **规则污染**：Workflow 内部流程进入宿主业务规范文件，普通 Agent 读取根规则时被动继承 Gate、Git 白名单与 Handoff 契约；
2. **升级冲突**：入口模板与宿主业务规则共用同一文件，升级只能整文件覆盖，容易破坏宿主内容；
3. **归属不清**：宿主根 `AGENTS.md` 是宿主资产，Workflow 不应拥有写入权。

V3.1 的隔离方式：

| 关注点 | 位置 | 归属 |
| --- | --- | --- |
| Workflow 角色指令、激活与防逃逸 | `.agent/specs/WorkflowAgentDirective.md` | Workflow |
| 协作协议、职责边界 | `.agent/specs/*` | Workflow |
| 宿主业务规范 | 宿主根 `AGENTS.md` | 宿主（可选，只读） |
| Git / Gate / Registry 机器事实 | `.agent/engine/` 读取 Git 与 `.agent/workflow.yaml` | Workflow Core |

设计取舍：

- 入口不使用 `AGENTS.md` 命名，降低目录级自动发现带来的规则污染风险；但**不宣称**任何平台的自动发现行为，Role 必须显式读取。
- 激活判定（角色指定 + Task 指定 + 以该角色职责运行）是 Agent 行为协议，不是 Engine 的 Session 身份认证；Engine 无法验证 Session 是否真的按角色运行。
- 显式加载与非 `AGENTS.md` 命名只降低风险，不构成绝对不可绕过的隔离保证；防逃逸仍依赖 Role 遵守 Prompt。
- 普通 Session 不继承 Workflow 约束，因此在 Owner 授权与宿主安全策略允许时可以正常开发、Commit 与 Push；Workflow 不改变平台权限或分支保护。
- 已激活 Role 不得自述退出；退出需 Owner 明确指令。

Engine 影响：**无**。`workflow-check` 不读取任何角色 Markdown 或 Directive，因此本隔离不改变 CLI、Gate、State、Config 与 Registry 行为。

精确的激活条件、缺失处理与退出规则只在 `.agent/specs/WorkflowAgentDirective.md` 维护，本文不复制。

---

## 31. 循环熔断（V3.1）

### 为什么需要

长任务里真正拖垮流程的不是单次失败，而是**同一根因的阻塞往返**：Coder 修一版、Cleaner 退回、Owner 改一次决定、Contract 再修订，几轮之后没人能说清「这个问题已经问过多少次、还该不该继续」。V3 原有的 `blocked` 只能表达「现在卡住了」，不能表达「已经卡过两轮，第三轮不应再发生」。

V3.1 的应对是**两轮预算 + 可追溯账本 + 原子停机**，而不是新的状态机：

- 计数键是 `(task_id, loop_key)`，`loop_key` 由问题类别与稳定语义根因组成；
- 默认每个 segment 允许 2 次 `REQUEST` 往返，第 3 次发起前必须 DANGER；
- 账本 `.agent/tasks/<task>/loop-ledger.md` 只追加 `REQUEST` / `DANGER` / `CLOSE` / `RESET` 事件；
- DANGER 报告 `.agent/tasks/<task>/danger-report.md` 固定记录两轮证据、真实状态、最小待决问题与允许的恢复方式；
- 停机复用 `blocked.active=true`，五个 Gate 一律拒绝；
- 只有 Owner 明确授权才能 `CLOSE` / `RESET`；含糊回复不解除 `blocked`。

### 为什么不做成新状态机

- **不新增 State Schema**：`blocked` 已有 `active / by / reason`，足够表达停机；账本不是第二状态权威，`state.yaml` 仍是唯一机器事实源，二者冲突时以 `blocked` 为准。
- **不新增 Gate**：五个 Gate 已经统一检查 `blocked`，再加一个「loop gate」只会增加绕过面。
- **不做调度器**：谁发起、谁记账、谁停机的责任落在角色协议上，Engine 保持只读。

### 诚实的验证边界

`workflow-check` 不读取这两个文件，也不解析账本。Engine 能机械保证的只有：

1. `blocked.active=true` 时五个 Gate 全部 FAIL；
2. 这两个文件在**当前 Task 根目录**内属于 Review-neutral / Delivery-neutral，记录循环不会破坏 CLEAN 或合法 Delivery Tail；
3. 其他路径（生产代码、测试、其他 Task 同名文件、本 Task 子目录同名文件）仍是 substantive change。

Engine **不能**保证：账本 append-only、轮次真实、根因归类正确、DANGER 报告未编造、跨 Session 一定重新读取账本。这些是 Prompt / Spec 约束，依赖角色遵守与 Review 复核，不能宣称为机器级限流。

精确的 loop_key 构造、去重指纹、write-ahead 步骤、账本字段、DANGER 报告内容与 Owner 恢复流程只在 `.agent/specs/WorkflowLoopCircuitBreaker.md` 维护，本文不复制。

---

## 32. ARCHITECT 路线（V3.1.0）

### 为什么需要第三条路线

`NORMAL` / `COMPLEX` 都以「实现可观察业务行为」为目标：Coder 写业务代码，Cleaner 审查业务实现是否满足 Task 与 Contract。结构设计任务不同：

- 交付物是模块边界、接口、依赖方向与结构骨架，业务行为可以（也应当）不在本任务内实现；
- 用「Cleaner 审查业务实现」的标准衡量结构成果，会强迫 Architect 顺手实现业务功能，或让审查方用错误标准放行；
- 用 `NORMAL` 直接交 Coder 则丢失独立结构审查，等于让结构设计自己证明自己。

因此 `ARCHITECT` 是一条**独立路线**，不是 `COMPLEX` 的别名，也不是 `NORMAL` 的降级。

### 角色与阶段

`TaskBuilder → Analyst → Architect → OwnerGate → Deliverer → Owner`，其中 OwnerGate 承担两个阶段：

| 阶段 | 执行者 | 入口 Gate | 产出 |
| --- | --- | --- | --- |
| V1 独立结构验证 | 被明确指定为 ARCHITECT OwnerGate 的**新 Session** | `cleaner-start` | `findings.md`、`core-logic.md`、`review.status=CLEAN`（`review.target=D1`），Commit + Push 后运行 `owner-gate-start` |
| V2 Owner 决策 | 同一 OwnerGate Session | `owner-gate-start` | `owner-decision.md`、`owner.status=ACCEPTED`（`owner.review_target=D1`），Commit + Push 后运行 `delivery-start` |

### 为什么 ARCHITECT 不经过 Cleaner

Cleaner 的职责与证据标准绑定在「业务实现 + 业务测试」上（§7、§14）：它审 C1、检查 AC 行为、判断测试能否识别错误实现。结构任务没有业务实现可审，让 Cleaner 走同一流程只会产生两种坏结果：Cleaner 以业务标准拒绝结构交付，或 Cleaner 用结构标准放行而 Cleaner 的 `CLEAN` 语义被悄悄改写。

`ARCHITECT` 的处理是把**验证职责**交给同一条路线上真正需要独立判断的一方：OwnerGate 在 V1 阶段先做结构验证，再请求 Owner 决策。这样既保留「验证者 ≠ 作者」，又不改写 Cleaner 的职责边界。

### 为什么不新增 Gate

`ARCHITECT` 复用的五个 Gate 已覆盖它需要的全部机器事实：

- `coder-start`：Contract 已批准 + 资源已授权 + `handoffReady` → 「可以按 Contract 动手」；
- `cleaner-start`：`review.status=PENDING` + `review.target` 属于当前 feature 历史 + Review Tail 仅 neutral → 「Review Request 已稳定落 Git」；
- `owner-gate-start`：CLEAN 仍有效 → 「可以请求 Owner 决策」；
- `delivery-start` / `merge-ready`：Owner 已接受 + Delivery 绑定与新鲜度。

新增 Gate 只会增加绕过面，且无法弥补「验证者身份不可机器认证」这一真实边界。复用的代价是要在 Prompt 层写清 Gate 在该路线下的语义（`coder-start` ≠ 变成 Coder；`cleaner-start` ≠ 交给 Cleaner），这由 `.agent/roles/*` 承担。

### 证据链

```text
A1 contract.md（结构交付限定）→ A2 contract.status=APPROVED / target=A1
D1 结构骨架 + architecture.md + 结构测试 → D2 review.status=PENDING / target=D1
V1 findings.md + core-logic.md + review.status=CLEAN（target 保持 D1）
V2 owner-decision.md + owner.status=ACCEPTED / review_target=D1
Deliverer delivery.md + delivery.review_target / feature_head / develop_base
```

`architecture.md` 与结构测试都在 D1 内；D1 之后只允许当前 Task 的 Review-neutral / Delivery-neutral artifacts。D1 之后修改结构代码或 `architecture.md` 会使旧 CLEAN 机械失效。

### 与 COMPLEX 的边界

- 只要需要完整业务功能、数据库 Migration、未获批准的 DB Schema 变更、新外部依赖或构建 / 部署模型变化，就不是 `ARCHITECT` 能交付的范围；
- Architect 遇到这类需求必须停止并回交 Analyst / Owner，由 Owner 明确决定是否切换到 `COMPLEX`；
- 切换后必须重新核对 Contract 与 Evidence：`ARCHITECT` 的 `CLEAN` 只是结构验证结论，**不是** `COMPLEX` 的 Cleaner 审查结论，也不覆盖业务实现正确性。

### 诚实的验证边界

Engine 只能机械保证：

1. `review.status` / `review.target` / Tail 白名单 / `handoffReady` / Contract / Owner / Delivery 绑定符合既有规则；
2. `blocked.active=true` 时五个 Gate 一律拒绝，`ARCHITECT` 不能靠换 Gate 绕过 DANGER；
3. `cleaner-start` 要求 `PENDING`，因此自行写 `CLEAN` 后再运行它必然 `FAIL`。

Engine **不能**保证：执行 V1 的 Session 不是 Architect 本人、Git 作者真实独立、`findings.md` 中的验证确实执行过、`architecture.md` 与实际结构一致。这些是 Role Prompt + Session 隔离 + Review 证据的约束，不能宣称为机器级身份认证。

### 非回归

`NORMAL` / `COMPLEX` 的角色顺序、Contract / Review / Owner / Delivery 语义、五个 Gate、`state.yaml` schema 与本路线引入前完全一致。`ARCHITECT` 只新增角色 Prompt 与文档语义，`workflow-check` 无代码变更。

---

## 附录：ASCII Flow

```text
NORMAL / COMPLEX：
TaskBuilder
   ↓
Analyst（Contract Decision / Registry Reservation）
   ↓
Coder
   ↓
Cleaner
   ↓
OwnerGate（Owner Core Logic Decision）
   ↓
Deliverer
   ↓
Owner Integration

ARCHITECT：
TaskBuilder（contract.status=PENDING）
   ↓
Analyst（A1/A2，Contract 限定结构交付）
   ↓
Architect（D1 结构快照 → D2 Review Metadata）
   ↓
OwnerGate V1（新 Session：独立结构验证，review.target=D1）
   ↓
OwnerGate V2（Owner 决策，owner.review_target=D1）
   ↓
Deliverer（结构交付验收）
   ↓
Owner Integration
```

Evidence Snapshot 与 Neutral Tail 的时间轴：

```text
NORMAL / COMPLEX：
C1 Implementation Evidence Commit
   ↓
Cleaner neutral artifacts（findings.md / core-logic.md / state.yaml）
   ↓
OwnerGate（owner-decision.md / state.owner）
   ↓
Delivery neutral artifacts（delivery.md / state.yaml）
   ↓
Owner Integration

ARCHITECT：
D1 结构骨架 + architecture.md + 结构测试
   ↓
OwnerGate V1 neutral artifacts（findings.md / core-logic.md / state.yaml）
   ↓
OwnerGate V2（owner-decision.md / state.owner）
   ↓
Delivery neutral artifacts（delivery.md / state.yaml）
   ↓
Owner Integration
```

`review.target` 始终指向 C1（`ARCHITECT` 下指向 D1），不前移到审查方自己的 commit。
