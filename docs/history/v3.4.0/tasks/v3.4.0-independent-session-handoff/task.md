# Task: V3.4.0 独立 Session 协作与轻量化交接

## Goal

把跨角色协作的事实来源从「上一个 Agent 编写的 HANDOFF_PROMPT」彻底切换到「Task 目录 + state.yaml + Git 事实 + Gate」，使任意角色都能在一个全新的、没有历史聊天记录的 Session 中，仅凭角色名与 Task 目录完成本职工作和交接；并以 V3.4.0 正式发布该机制的框架源码包。

## Scope

- 重写 `.agent/specs/WorkflowHandoffContract.md`：出站交接固定 4 字段（`RESULT` / `NEXT_ROLE` / `TASK_PATH` / `OWNER_ACTION`），删除 10 项 `HANDOFF_PROMPT` 模板与强制要求；矩阵删除 `prompt` 列；补齐 `STOPPED` 终态与反模式清单。
- 在 `.agent/specs/WorkflowAgentDirective.md` 新增 Session Bootstrap Protocol、Stage Derivation Table、`mode` / Finding 推导规则、开工声明（opening declaration）与推导失败处理。
- 适配七个 Role Prompt（TaskBuilder、Analyst、Architect、Coder、Cleaner、OwnerGate、Deliverer）：输入统一为 `ROLE` + `TASK_PATH` + 可选 `NOTE`；必读清单补齐 `findings.md` / `owner-decision.md` / `delivery.md` / `loop-ledger.md`；出站改为 4 字段。
- TaskBuilder：保留 `mode: create` / `mode: owner_update` 最简输入；新增 `UPDATE_COMPLETE` 终态（`CODER` / `ANALYST` 出口；CLEANER 出口经源码核实不成立）；写明 `task.md` 变更会使既有 CLEAN 机械失效；补齐循环熔断计数动作。
- 共享规范一致性：`AgentCollaborationSpecification.md` 增加 Artifact × Role 输入所有权矩阵与「不得把上一 Agent 叙述当事实」硬规则；`Five-AgentResponsibilityBoundary.md` 的交接序列改为引用推导表；`WorkflowGitLifecycle.md` 的出站字段与 `SRC-12` 记录；`WorkflowLoopCircuitBreaker.md` 的 `handoff_target` / `receiver_action` 字段。
- 测试：重写 `.agent/adoption/tests/test_handoff_contract.py`；扩展 `test_taskbuilder_autonomy.py`；新增冷启动事实可得性与失败交接可恢复性的真实 Git 行为测试。
- 发布：`.agent/VERSION`、`PACKAGE_MANIFEST.md`、`README.md`、`.agent/README.md`、`docs/design/agent-workflow.md`、`docs/releases/v3.4.0.md`、`.cnb.yml` 更新至 3.4.0。

## Out of Scope

- 不修改 `.agent/engine/**`（五个 Gate、`state.yaml` schema、route 校验、`paths.go` Tail 白名单）。
- 不新增、不放宽、不改名任何 Gate；不新增 state 字段（包括 `owner.return_to`）。
- 不修改安装 / 升级机制（`.agent/adoption/workflow_adoption.py`）与分支管理机制。
- 不合并 `Five-AgentResponsibilityBoundary.md` 与 `AgentCollaborationSpecification.md`。
- 不为「已终止任务」写入 `blocked.active=true`。

## Milestone

Milestone: V3.4.0

## Design Impact

Design Impact: UPDATE
Design Artifact: docs/design/agent-workflow.md

## Acceptance Criteria

- [ ] AC-001：全新 Session 只被给予角色名与 `.agent/tasks/<slug>/` 时，Directive 给出唯一启动串、必读顺序、开工声明与推导失败处理；角色不需要 `mode` 或 `finding_ids` 作为必填输入。
- [ ] AC-002：所有角色出站块固定为 `RESULT` / `NEXT_ROLE` / `TASK_PATH` / `OWNER_ACTION` 四字段，且不再出现强制 `HANDOFF_PROMPT`；矩阵为 `role|terminal|result|next_role|action|gate|enforced` 七列。
- [ ] AC-003：`mode` 与 `finding_ids` 由 `state.yaml` 与 `findings.md` 的推导规则确定；推导冲突或无法推导时输出 `BLOCKED`，不得猜测。
- [ ] AC-004：TaskBuilder 的 `create` 只需 `mode` + `owner_request`，`owner_update` 只需 `task_path` + `owner_decision`；`UPDATE_COMPLETE` 有合法出口（`CODER` / `ANALYST`），并说明 `task.md` 变更使既有 CLEAN 失效、为何不存在 `CLEANER` 出口。
- [ ] AC-005：Directive 的阶段推导表覆盖 `route` / `contract` / `review` / `owner` / `delivery` 的全部关键组合，唯一不可机器推导的组合（Owner `REJECTED` 的退回对象）显式标注并给出 `BLOCKED` 兜底。
- [ ] AC-006：失败原因必须持久化到 Task Artifact（`findings.md` / `owner-decision.md` / `delivery.md` / `loop-ledger.md`）；`FAILURE_HANDOFF` 在发出前必须已有落盘记录；`loop-ledger.md` 增加 `handoff_target` 与 `receiver_action`。
- [ ] AC-007：`.agent/engine/**` 零改动，`GOWORK=off go -C .agent/engine test -count=1 ./...` 原样通过；五个 Gate 语义不变。
- [ ] AC-008：静态测试校验四字段契约、七角色一致性、阶段推导覆盖、无 `mode` 必填、无 `STOPPED` 缺失、无任务复述；行为测试在临时 Git 仓库中证明「只读 Task 目录 + Git」可得全部审查事实，以及失败交接可从 `loop-ledger.md` 恢复。
- [ ] AC-009：`.agent/VERSION`、`PACKAGE_MANIFEST.md`、`README.md`、`.agent/README.md`、design doc、`docs/releases/v3.4.0.md`、`.cnb.yml` 与 3.4.0 一致，发布脚本测试通过。
- [ ] AC-010：Cleaner 在无 Coder 会话上下文的独立 Session 中仅凭 Task 路径完成审查、落盘 `findings.md` / `core-logic.md`、输出四字段交接；OwnerGate 与 Deliverer 同样能仅凭 Task 判断当前决策点与交付条件。

## Relevant Context

- 已核实事实：当前仓库为 Agent Workflow 源仓库（远端 `https://cnb.cool/framework-station/agent-workflow`）；`main` = `6c805dab8a5656bccbc7d34abac48222d7abe203` = Tag `v3.3.0`；`.agent/VERSION` = `3.3.0`。
- 已核实事实：审计报告确认 10 项 `HANDOFF_PROMPT` 中 9 项可由 Task / 规范推导；唯一会话内事实是失败原因，但它没有统一持久化载体。
- 已核实事实：`test_handoff_contract.py:177-181` 强制 `prompt=REQUIRED`；`test_handoff_contract.py:238-246` 强制出口表与矩阵逐项相等。两者是「Agent 必然写长 Prompt」的硬约束来源。
- 已核实事实：`paths.go` 的 Review-neutral 白名单不含 `task.md`，因此 TaskBuilder 更新 `task.md` 会使既有 CLEAN 失效（`owner-gate-start` FAIL）。
- 已核实事实：`TaskBuilderPrompt.md` 的出口表无 `UPDATE_COMPLETE`、无 `NEXT_ROLE=CLEANER` 行。
- 已核实事实：`CoderAgent.md` 的必读清单不含 `findings.md` / `owner-decision.md` / `delivery.md`。
- 已核实事实：规范中不存在启动串 / launcher 定义（全文检索为空）。
- Assumption：本仓库作为框架源码库不跟踪 `.agent/tasks/**` 与 `.agent/workflow.yaml`（分发包禁止携带宿主数据，`workflow_adoption.py` 对 tracked 宿主数据 fail-closed）。Task 证据以工作区文件形式保留，Gate 仍以真实 Git 与 `state.yaml` 判定。
- Assumption：V3.4 保持 `schema_version: 3`，不引入 state 迁移。

## Dependencies

- Blocking: NONE
- Reference-only: 此前完成的《Agent Workflow V3.3.0 — Agent 协作与任务交接机制专项审计报告》（只读审计，未落盘为仓库文件；结论按本任务逐条源码复核）。

## Verification

- AC-001 / AC-003 / AC-005 → `test_cold_start.py`（阶段推导覆盖、必填输入缺失、推导冲突）+ 人工冷启动 Session 记录。
- AC-002 / AC-008 → `test_handoff_contract.py`（四字段、矩阵、出口表、反模式）+ `test_taskbuilder_autonomy.py`。
- AC-004 → `test_taskbuilder_autonomy.py`（`UPDATE_COMPLETE` 出口集合、CLEAN 失效规则）。
- AC-006 → `test_handoff_contract.py` + `test_git_lifecycle.py` 的失败交接恢复用例。
- AC-007 → `GOWORK=off go -C .agent/engine test -count=1 ./...` 与 `git diff --stat main -- .agent/engine`。
- AC-009 → `python3 scripts/release/tests/test_release_scripts.py`、`python3 .agent/adoption/tests/test_workflow_adoption.py`。
- AC-010 → 本任务 Cleaner / OwnerGate / Deliverer 三个阶段的真实执行记录（本仓库 `.agent/tasks/v3.4.0-independent-session-handoff/`）。

## Complexity

COMPLEX

原因：改造跨角色共享协议、七个角色输入输出契约、静态与行为测试、发布元数据；存在多处相互依赖的规范因果链（Gate 语义 → 阶段推导 → 角色出口 → 测试断言），需要先定协议再改角色，且必须保证五个 Gate 与 state schema 零改动。

## Analyst Questions

- Q-1：出站块是否严格采用 4 字段（无 `NEXT_ACTION`）？审计建议保留 `NEXT_ACTION` 以区分同 Session / 新 Session；Owner 目标格式为 4 字段。需要在 `OWNER_ACTION` 内编码 `NONE` / `CONTINUE` / `DECIDE:` / `DO:` / `STOP`，以同时满足两者。
- Q-2：失败原因持久化载体是否复用 `loop-ledger.md`（审计 D-1 建议 a），而不是新增 `handoff.md` 或修改 `paths.go`？
- Q-3：`owner.status=REJECTED` 的退回对象是否继续只由 `owner-decision.md` 的 `Return To` 承载（不新增 `owner.return_to`）？
- Q-4：TaskBuilder 更新 `task.md` 后是否一律要求 Coder 重建 C1（使 CLEAN 失效）？

## Review Baseline

- Base branch：main
- Base commit：6c805dab8a5656bccbc7d34abac48222d7abe203
- Base branch kind：INTEGRATION
- Base upstream：origin/main
- Base sync：IN_SYNC
- Feature branch：task/v3.4.0-independent-session-handoff
- Remote：origin
- 任务开始时已有修改：`dist/`（未跟踪构建产物，审计前已存在）
- 已有修改的处理：保留
- 重叠修改的区分方式：本任务只提交 `.agent/specs/**`、`.agent/roles/**`、`.agent/adoption/tests/**`、`docs/**`、根 `README.md`、`PACKAGE_MANIFEST.md`、`.cnb.yml`、`.agent/VERSION`、`.agent/README.md`；`dist/` 不纳入本任务任何提交。

## Initial Route

交 Analyst（COMPLEX）

## Execution Plan（V3.4 任务执行清单）

| Task ID | 目标 | 修改范围 | 验收 | 前置依赖 | 并行 / 串行 | 分支 | 交付物 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T1 | 冻结轻量交接协议与阶段推导（唯一权威） | `WorkflowHandoffContract.md`、`WorkflowAgentDirective.md` | AC-002/003/005 | NONE | 串行（协议先行） | task/v3.4.0-independent-session-handoff | 契约 v2 + 推导表 |
| T2 | 失败原因与 Owner 决策的持久化规则 | `WorkflowLoopCircuitBreaker.md`、`WorkflowHandoffContract.md` §失败、`AgentCollaborationSpecification.md` | AC-006 | T1 | 串行（依赖 T1 的字段名） | 同上 | `handoff_target`/`receiver_action` + 落盘规则 |
| T3 | 七个 Role Prompt 输入侧适配 | `.agent/roles/*.md` 的输入与必读清单 | AC-001/003/010 | T1 | 可与 T4 并行，不同文件 | 同上 | 7 份输入段 |
| T4 | 七个 Role Prompt 出站侧适配 | `.agent/roles/*.md` 的 `# handoff-exits` 与出站段 | AC-002/004 | T1 | 与 T3 同文件，串行合并 | 同上 | 7 份出口表 |
| T5 | TaskBuilder 更新路径与熔断动作补齐 | `TaskBuilderPrompt.md`、`test_taskbuilder_autonomy.py` | AC-004 | T1/T4 | 串行 | 同上 | `UPDATE_COMPLETE` + `CLEANER` 出口 |
| T6 | 契约与冷启动测试 | `test_handoff_contract.py`、新增 `test_cold_start.py`、`test_git_lifecycle.py` 行为用例 | AC-002/005/006/008 | T1–T5 | 串行（验证收口） | 同上 | 静态 + 行为测试 |
| T7 | 文档与发布元数据 | design doc、README、`.agent/README.md`、PACKAGE_MANIFEST、VERSION、release notes、`.cnb.yml` | AC-009 | T1–T6 | 可与 T6 部分并行（不同文件） | 同上 | 3.4.0 元数据 |
| T8 | 集成验证、独立审查与交付 | 全量测试、五个 Gate、Cleaner/OwnerGate/Deliverer | AC-007/010 + 发布 | T1–T7 | 串行（Gate 顺序） | 同上 → main | 审查证据 + 交付记录 + Tag v3.4.0 |

串行主线：T1 → T2 → T3/T4 → T5 → T6 → T7 → T8。
可并行：T3 与 T4 的编辑范围重叠（同一批文件），按「先输入段、后出口段」两遍提交；T7 的文档与 T6 的测试互不重叠，可交叉进行。
