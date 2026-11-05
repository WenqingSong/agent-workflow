# Contract — V3.4.0 独立 Session 协作与轻量化交接

Status: APPROVED（Analyst A1 交付，等待 Coder 实现）

## 1. 问题陈述（已核实事实）

V3.3 已把任务事实写进 Task，但**启动协议不存在**：规范中没有「Owner 如何启动下一个 Agent」的定义，唯一可复制的载荷是 `HANDOFF_PROMPT`。四处硬约束叠加，使 Agent 必然产出长篇 Prompt：

1. `WorkflowHandoffContract.md` §3 / §4 要求 10 项内容；
2. §8 矩阵对所有跨角色 `HANDOFF` / `FAILURE_HANDOFF` 标记 `prompt=REQUIRED`；
3. `test_handoff_contract.py` 断言 `prompt=REQUIRED`，且出口表必须与矩阵逐项相等；
4. 没有 `mode` / `finding_ids` 的推导规则，Agent 若不给 Prompt，接收者无法确定执行模式。

同时存在三个事实性缺陷：`owner_update` 无合法终态与 `CLEANER` 出口；`RESULT=STOPPED` 无出口行；Coder 必读清单不含 `findings.md` / `owner-decision.md` / `delivery.md`。

## 2. 已批准的设计决定

| ID | 决定 | 理由 | 影响面 |
| --- | --- | --- | --- |
| D-1 | 交接块固定 **4 字段**：`RESULT` / `NEXT_ROLE` / `TASK_PATH` / `OWNER_ACTION`；可选 `NOTE` 最多 2 行 | 与 Owner 目标格式一致；`NEXT_ACTION` 的五种语义压缩进 `OWNER_ACTION` 的固定取值 | 契约、7 角色、测试 |
| D-2 | `OWNER_ACTION` 取值：`NONE`（开新 Session 跑 `NEXT_ROLE`）/ `CONTINUE`（同 Session 继续）/ `DECIDE: <最小问题>` / `DO: <必须先做的动作>` / `STOP` | 保留审计 D-4 的「同 Session / 新 Session」区分能力，不新增第 5 字段 | 契约、矩阵 |
| D-3 | 失败原因持久化复用 `loop-ledger.md`，新增 `handoff_target` / `receiver_action` 两个必填字段；不新增 `handoff.md`，不改 `paths.go` | `loop-ledger.md` 已在 Review-neutral 与 Delivery-neutral 双白名单，Engine 不解析，append-only + write-ahead 已存在 | 熔断规范、契约 §失败 |
| D-4 | 不新增 `owner.return_to`；`owner.status=REJECTED` 的退回对象继续由 `owner-decision.md` 的 `Return To` 承载，缺失时 `BLOCKED` | 唯一不可机器推导的组合，单字段收益不足以改动 schema | 推导表标注 |
| D-5 | 阶段与 `mode` 推导表写在 `WorkflowAgentDirective.md`，其余文件只引用，不复制 | 消除「同一事实多份副本」的历史漂移（`SRC-09`） | Directive、7 角色、协作规范 |
| D-6 | 不修改 `.agent/engine/**`、五个 Gate、`state.yaml` schema、安装/升级与分支机制 | 机器层已经由 Task + Git 驱动；改动收益远小于回归风险 | 验收 AC-007 |

## 3. 出站交接契约 v2（唯一权威）

### 3.1 字段

```text
## 出站交接

RESULT: <本角色终态码>
NEXT_ROLE: <ANALYST|ARCHITECT|CODER|CLEANER|OWNER_GATE|DELIVERER|TASK_BUILDER|OWNER|NONE>
TASK_PATH: .agent/tasks/<task-slug>/
OWNER_ACTION: <NONE|CONTINUE|DECIDE: ...|DO: ...|STOP>
```

- 四行必须存在、顺序固定、不得改名；
- `NOTE` 可选，最多 2 行，只能写「无法持久化的启动前提」（例如外部环境变量位置）；禁止写 AC、SHA、文件清单、实现步骤、Gate 前置条件；
- 交接块是最终回答的最后一个章节，块后不得追加内容；
- **禁止** `HANDOFF_PROMPT`：V3.4 起不再产出、不再消费；历史交接块中的该字段一律**不得**作为事实来源。

### 3.2 `OWNER_ACTION` 语义

| 取值 | 含义 | 对开发者的动作 |
| --- | --- | --- |
| `NONE` | 正常交接或失败交接，事实已落盘 | 开新 Session：`以 <NEXT_ROLE> 角色执行任务：<TASK_PATH>` |
| `CONTINUE` | 同 Session 阶段推进（原 `SAME_SESSION`） | 无需操作，继续当前 Session |
| `DECIDE: <最小问题>` | 等待 Owner 决策（原 `OWNER_DECISION`） | 回答该问题 |
| `DO: <动作>` | 需要 Owner 先执行不可由 Agent 完成的操作（授权、环境、外部系统） | 执行该动作后重跑入口 Gate |
| `STOP` | 合法停止（`BLOCKED` / `DANGER` / Owner 终止） | 按 `NEXT_ROLE` 与 `NOTE` 解除条件处理 |

### 3.3 反模式（静态可检查）

1. 复述 AC / Scope / 不变量；
2. 罗列完整 40 位 SHA（SHA 只在 `state.yaml` 与 Git）；
3. 写「下一步先读 X 文件」（那是 Role Prompt 的必读清单）；
4. 写「注意不要改 contract.md」（那是 owned artifact 规则）；
5. `NOTE` 超过 2 行，或出现 `AC-`、`.md` / `.go` 路径、40 位 hex。

## 4. Session Bootstrap 与阶段推导（唯一权威）

### 4.1 启动串

```text
以 <ROLE> 角色执行任务：
.agent/tasks/<task-slug>/
```

可选第三行 `NOTE: <≤2 行>`。这是 Owner 唯一需要提供的输入。旧式输入（`mode: ...` / `task_path: <file>` / `finding_ids: ...`）仍被接受，但只作为「覆盖提示」：与推导结果冲突时输出 `BLOCKED`，不得静默忽略。

### 4.2 角色开工五步

1. 读 Directive §4 规范链；
2. 读 Task 目录内全部现存 Artifact；
3. 按 §4.3 推导 stage、`mode`、入口 Gate；
4. 输出 5 行开工声明（`ROLE` / `TASK` / `STAGE` / `TARGET` / `ENTRY_GATE`）；
5. 运行入口 Gate，PASS 后进入职责。

开工声明是「独立理解」的可核对证据，取代上一角色的长篇叙述；它不是 Gate 结论。

### 4.3 阶段推导表（由五个 Gate 语义反推）

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
| 任意 | 任意 | CLEAN | REJECTED | NOT_RUN | RETURN_PENDING | **不可机器推导**：读 `owner-decision.md` 的 `Return To`；缺失 → BLOCKED | none |
| 任意 | 任意 | 任意 | 任意 | 任意 | BLOCKED | NONE | 任意 Gate 均 FAIL |

### 4.4 `mode` 与 Finding 推导

| Role | 推导 | 覆盖提示冲突时 |
| --- | --- | --- |
| Analyst | `contract.status=PENDING` 且有 Owner 已确认文本 → `record_owner_decision`；`APPROVED` 且被要求重审 → `reconsideration`；否则 `analysis` | BLOCKED |
| Architect | `review.status=CHANGES_REQUIRED` → `structural_repair`；否则 `architecture_delivery` | BLOCKED |
| Coder | `review.status=CHANGES_REQUIRED` / `owner.status=REJECTED` / `delivery.status=FAIL` → `finding_fix`；否则 `implementation` | BLOCKED |
| Cleaner | `review.status=PENDING` → `initial_review`；`CHANGES_REQUIRED` → `re_review` | BLOCKED |
| Deliverer | `delivery.status=NOT_RUN` → `milestone_verification`；`FAIL` → `re_verification` | BLOCKED |
| OwnerGate | `review.status=PENDING` + ARCHITECT → V1；`CLEAN` → V2 | BLOCKED |
| TaskBuilder | 有 `task_path` → `owner_update`；否则 `create` | BLOCKED |

`finding_ids` 不来自输入：`finding_fix` 模式下取 `findings.md` 中 `Status: OPEN` 的全部编号；`review.review_round` 与既有 Finding 决定 `re_review` 的对象数量。`findings.md` 缺失或全部 CLOSED 时输出 `BLOCKED`，不得自行提出新 Finding。

## 5. 失败与决策的持久化不变量

1. 任何 `FAILURE_HANDOFF` 在交接前必须在 `loop-ledger.md` 追加一条 `REQUEST` 事件，至少含 `handoff_target` 与 `receiver_action`（≤200 字），commit + push + 远端核验；否则只能 `STOP`（`PERSISTENCE_FAILURE`）。
2. Cleaner 的 `CHANGES_REQUIRED` 原因写在 `findings.md`；OwnerGate 的 `REJECTED` 原因与退回对象写在 `owner-decision.md`；Deliverer 的 `FAIL_*` 写在 `delivery.md`。交接块不复述这些内容。
3. Owner 终止任务：`RESULT=STOPPED`、`NEXT_ROLE=NONE`、`OWNER_ACTION=STOP`；**不得**写 `blocked.active=true`（终止 ≠ 阻塞）。
4. Coder / Analyst / TaskBuilder 失败时没有自有 Artifact，其失败原因写 `loop-ledger.md`，不得依赖聊天记录。

## 6. TaskBuilder 更新路径

- `mode: owner_update` 的输入只有 `task_path` + `owner_decision`（+ 可选 `extra_instruction`）；不得要求 Owner 提供分支、基线、Slug。
- 新增终态 `UPDATE_COMPLETE`，出口：`UPDATE_COMPLETE|CODER|HANDOFF|coder-start` 与 `UPDATE_COMPLETE|ANALYST|HANDOFF|none`；取消「用 `BOOTSTRAP_COMPLETE` 表达更新」。**不存在 `UPDATE_COMPLETE → CLEANER`**：`paths.go` 的 Review-neutral 白名单不含 `task.md`，任何 `task.md` 变更都会使既有 `CLEAN` 在 `owner-gate-start` 失效，且 `cleaner-start` 要求 `review.status=PENDING`；因此更新后只能由 Coder 重建 Evidence 或由 Analyst 重定 Contract（源码证据：`.agent/engine/internal/workflow/paths.go`、`gates.go` GateCleanerStart）。
- **CLEAN 失效规则**：`task.md` 不在 Review-neutral 白名单（`paths.go`），因此任何 `task.md` 变更都会使既有 `CLEAN` 在 `owner-gate-start` 失败。TaskBuilder 必须在更新后判断：改动影响验收/Scope → `NEXT_ROLE=CODER`（重建 C1）；仅影响不需要重新实现的元数据 → 仍需 Coder 重建证据，或由 Owner 明确终止；不得声称 CLEAN 仍有效。
- TaskBuilder 补齐循环熔断计数动作（出口表已有 `DANGER` 行，此前无可触发动作）。

## 7. 不变量（实现必须保持）

- INV-1：五个 Gate 的名称、语义、exit code 不变；`state.yaml` schema 仍为 3，不新增字段。
- INV-2：`.agent/engine/**` 与 `.agent/adoption/workflow_adoption.py` 零改动。
- INV-3：Review-target / CLEAN / Owner 绑定 / Delivery 三重 SHA 的机器校验不变。
- INV-4：Task Artifact 是唯一任务事实来源；交接块、聊天记录、历史 `HANDOFF_PROMPT` 都不是事实来源。
- INV-5：任何简化都不得减少独立审查、Owner 决策与交付验证的要求。

## 8. 验收与测试映射

| AC | 验证 |
| --- | --- |
| AC-001 / AC-003 / AC-005 | `test_cold_start.py`：启动串唯一、五步、推导表覆盖 Gate 状态组合、`mode` 推导齐全、旧式输入冲突 → BLOCKED |
| AC-002 | `test_handoff_contract.py`：四字段、七列矩阵、出口表相等、无 `HANDOFF_PROMPT`、反模式 |
| AC-004 | `test_taskbuilder_autonomy.py`：`UPDATE_COMPLETE` 两条出口、CLEAN 失效规则、最简输入 |
| AC-006 | `test_handoff_contract.py` + `test_git_lifecycle.py`：`loop-ledger` 字段与恢复用例 |
| AC-007 | Engine 测试原样通过 + `git diff main -- .agent/engine` 为空 |
| AC-008 / AC-009 | 全量测试 + 发布脚本测试 |

## 9. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 推导表与 Gate 实现漂移 | `test_cold_start.py` 从 `state.go` 枚举生成组合并与表比对；表内每行标注对应 Gate |
| 删除 `HANDOFF_PROMPT` 后失败原因丢失 | D-3 强制落盘 + 测试断言 `FAILURE_HANDOFF` 行必须有持久化字段 |
| 旧任务（无 `route`）被误推导 | 推导表对 `route` 缺失不推断，要求 Owner 显式指定角色，否则 BLOCKED |
| 七角色并发修改产生冲突 | 先冻结本契约（T1/T2），再按输入段 → 出口段两遍修改，最后统一跑出口表测试 |
| 本仓库作为源码分发包无法携带 Task 数据 | 发布提交前移除 `.agent/tasks/**`（分发包 fail-closed 规则），Task 证据保留在 `v3.4.0` 之前的提交 |
