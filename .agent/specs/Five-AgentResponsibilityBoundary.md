# 六角色职责边界

## 1. 总则

六个角色是能力分工，不是每个任务都必须走完的组织架构。

```text
TaskBuilder：定义要做什么
Analyst：解决关键设计选择
Coder：实现并自测
Cleaner：独立审查实现与测试
OwnerGate：解释核心机制并请求 Owner 决策
Deliverer：验证重要里程碑能否真实运行
Owner：做决定并最终接受
```

Owner 不是第七个 Agent，而是整个 Workflow 的 **Decision Authority + Shared Repository Authority**。

共享流程、状态和文件规则见 `.agent/specs/AgentCollaborationSpecification.md`。各角色 Prompt 只说明本角色怎样工作，不重复整套工程制度。

本文件描述的角色边界、Gate 与 Handoff 约束只适用于按 `.agent/specs/WorkflowAgentDirective.md` §2 明确激活的 Workflow Role Session。普通 Agent Session 不因本文件存在而自动获得 Workflow Role 职责，也不自动受其 Git、Gate 与 Artifact 约束。

Workflow V2 状态模型（`state.yaml` schema、Evidence Snapshot / Neutral Tail、五个 Gate、INV-1 ~ INV-7、Global Registry 状态机、Handoff Contract）见 `docs/design/agent-workflow.md`。当前状态唯一机器权威源是 `.agent/tasks/<task-slug>/state.yaml`。

## 2. Owner

Owner 回答：我要什么、哪些重要方案可以接受、哪些核心逻辑必须掌握、是否最终接受？

Owner 负责：

- 决定 Goal、Scope、Out of Scope 和 Acceptance Criteria；
- 决定 Task / Branch Boundary：新任务还是延续、是否开新 feature、branch 名称与基线；需要时把「创建当前任务初始分支」这一窄操作按 `TB-BRANCH-1` 明确授权给 TaskBuilder；
- 确认复杂任务的关键方案（Contract Decision）；
- 阅读核心逻辑说明，做 Core Logic Decision（ACCEPT / REJECT / 继续询问）；
- 决定 Shared Integration：何时/如何 merge、merge 顺序、squash 方式、并行依赖；
- 决定最终 feature→develop integration 的 Commit、Merge、Push 和 Deploy。

Owner 不修改 `contract.md`、`owner-decision.md`、`state.yaml`（由对应角色在明确指令下机械持久化）。Owner 不必逐行代替 Cleaner Review，也不因 Agent 给出 `CLEAN` 或 `PASS` 自动接受任务。

## 3. TaskBuilder

TaskBuilder 回答：本次究竟交付什么，怎样判断完成？

负责：

- 把 Owner 需求整理成可执行、可验收的 `task.md`；
- 明确 Scope、Out of Scope、AC、验证要求和 Git 基线；
- 区分事实、合理假设和需要 Owner 决定的问题；
- 判断 Design Impact（`NONE` / `UPDATE` / `NEW`）；当为 `NEW/UPDATE` 时在 `task.md` 声明目标 Design Artifact，并纳入 Scope、Deliverables 与 AC；
- 判断任务直接交 Coder，还是先交 Analyst（Complexity）；
- 声明全局资源需求（类型 + 语义），不写具体域号 / version 号；
- 在 Owner 明确授权下执行 `TB-BRANCH-1`：创建当前任务的初始 Feature Branch、初始化任务 Artifact、Commit、普通 Push，并核验远端 SHA 与工作区状态。

不负责：写生产代码、替 Analyst 完成复杂设计、Review 实现，或替 Owner 创造关键业务规则。

TaskBuilder 默认不拥有分支生命周期，目标 branch 由 Owner 预先准备。唯一例外是 `TB-BRANCH-1`：Owner 逐项给出完整分支名、起始分支、起始 Commit SHA、Task Slug 并明确授权后，TaskBuilder 可为**当前任务**创建初始 Feature Branch 并完成首次 Commit / Push。TaskBuilder 仍禁止删除或重命名分支、Merge、Rebase、Force Push、Push `main` 或共享集成分支；该例外不改变 Engine、Gate 与 `state.yaml` schema。

## 4. Analyst

Analyst 回答：复杂问题有哪些约束，可行方案是什么，哪些性质必须保持？

仅在存在真正的设计选择、根因不明、安全、协议、并发或一致性风险时启用。

负责：

- 调查当前代码、配置、数据和测试；
- 比较少量真正可行的方案并给出推荐；
- 明确业务不变量、错误语义和验证方法；
- 起草 `contract.md`，请求 Owner Contract Decision；
- Owner 确认后，准确记录最终决定（两段式提交：A1 contract 内容 → A2 只写 `state.contract.status=APPROVED`、`state.contract.target=<A1 完整 SHA>`），随后 Commit + Push 当前 feature、核验远端同步并运行 `coder-start`，`PASS` 后才 HANDOFF Coder；Owner 未批准或拒绝时不得写 `APPROVED`，也不得改成 `NOT_REQUIRED` 绕过；
- 作为长期 Design（`docs/design/*`）内容的主责：`Design Impact = NEW/UPDATE` 时，在 Contract 经 Owner `APPROVED` 后、Coder 实现前新增/更新对应 Design Artifact；
- 读 `.agent/workflow.yaml` 确认已声明的 Shared Resource Kinds，再读对应 `.agent/registry/*` 派生候选资源，在 Contract APPROVED 后形成 Registry-only commit（仅含配置已声明的 Registry 文件）并 push `origin develop` 落实 `RESERVED`，然后验证 Reservation 真正进入 `origin/develop`；不得凭空自选编号，不得自行新增 resource kind 或修改 `.agent/workflow.yaml`。

不负责：修改生产代码、测试或任务目标；不能批准自己的推荐方案；除 Registry-only 例外（仅 `.agent/workflow.yaml` 已声明的 Registry 文件）外，不修改 shared develop 上的其他任何内容。

Analyst 是 Reservation 的语义负责人，并持有唯一的 Registry-only develop mutation authority（Registry-only Develop Authority）；Owner 仍独占 branch 生命周期与最终 feature→develop integration。

Analyst 的 Registry-only shared-develop mutation 仅限 `.agent/workflow.yaml` 声明的 Registry 文件（当前 my-shop 为 `.agent/registry/migrations.md` 与 `.agent/registry/error-codes.md`）。正常任务期间 Analyst 只读 `.agent/workflow.yaml`，不修改它。

## 5. Coder

Coder 回答：怎样在已确定的范围和约束内完成实现？

负责：

- 实现当前 AC 对应的生产行为；
- 编写正常、关键失败和重要边界测试；
- 对关键不变量提供至少一个能区分正确与错误实现的测试；
- 运行适用的格式化、测试、静态检查和构建；
- 检查本次完整变更，清理调试内容和无关修改；
- 修复 Cleaner 指定的 Finding 并提供回归证据；
- 只使用 APPROVED Contract 中的已分配全局资源；新增需求走 Contract Revision，不得自行推断编号（含 `max+1`）。

发起 Review Request（两段式提交）：C1（业务代码 + 测试，先完成验证再提交）→ C2（只改 `state.yaml` 写入 `review.status=PENDING`、`review.target=<C1 完整 SHA>`；不得混入业务代码）。C1、C2 都必须 Commit 并 Push 当前 feature，确保工作区 clean 且 local == remote，再运行 `cleaner-start`，`PASS` 后才 HANDOFF Cleaner。Cleaner 审 C1，不是 C2。

不负责：自行改变 Task 或已确认 Contract、修改 Finding 状态、宣布实现 `CLEAN`、写 `owner`/`delivery` 状态。

长期 Design 不是 Coder 的所有物：Coder 按 Contract 实现，不得擅自改变长期架构事实；实现中发现与 Contract/Design 冲突时，走 Contract Revision 交 Analyst 与 Owner。

## 6. Cleaner

Cleaner 回答：完整相关变更是否满足任务，测试是否真的能发现关键错误？

负责：

- 开始前运行入口 Gate `cleaner-start`，确认 `review.status=PENDING`、`review.target` 是当前 feature 历史中的真实 commit，且 target 之后只含 Review-neutral artifacts；
- 核对 Goal、Scope、Out of Scope、每项 AC 和已确认 Contract；
- 当 `Design Impact = NEW/UPDATE` 时，验证 Task ↔ APPROVED Contract ↔ `docs/design/*` ↔ 最终 Implementation 四者一致；
- COMPLEX 额外验证 Task ↔ Contract、Contract ↔ Design、Implementation ↔ Contract 一致，并确认测试与验收证据覆盖 Contract 的不变量与验证要求；不得因 Analyst 参与而新增或跳过 Gate；
- 审查完整相关 Diff、业务正确性、安全、数据一致性、并发和错误处理；
- 独立运行必要验证；
- 为真实缺陷建立稳定 Finding，并复审修复；
- 审查测试是否验证了结果，而不是只验证 Mock 或调用次数；
- 通过后填写 `findings.md`、`core-logic.md`，并写 `state.review.status=CLEAN`（`review.target` 保持 C1 完整 SHA 不变）；
- `CLEAN` 只是审查结论，不等于完成：得到 `CLEAN` 后仍须 commit Cleaner-owned review artifacts、push 当前 feature、确认 working tree clean 且 remote synced，运行 `owner-gate-start` Gate，`PASS` 后才 HANDOFF OwnerGate（Cleaner 自己的 findings/core-logic/state commit 是 review-neutral tail，`review.target` 不前移）；
- `CHANGES_REQUIRED` 也不是完成：必须把 Finding、`review.status=CHANGES_REQUIRED` 与下一步动作持久化，Commit + Push + 远端核验后退回 Coder；Coder 必须形成新的实现快照与新的 PENDING Review Target，旧 CLEAN 与旧结论不得复用；
- 三边一致性检查 Registry ↔ Contract ↔ 实现，任一漂移 → `CHANGES_REQUIRED`。

默认不修改生产代码或测试；发现问题交 Coder 修复。Cleaner 不改变任务标准，也不替 Owner 最终接受，不写 `owner` 状态（由 OwnerGate 负责）。

结果：`CLEAN`、`CHANGES_REQUIRED` 或 `BLOCKED`。

## 7. OwnerGate

OwnerGate 回答：哪些核心机制真正决定业务正确性，怎样让 Owner 高效确认？

负责：

- 开始前运行入口 Gate `owner-gate-start`；Gate `PASS` 只证明审查证据满足机器约束，不等于 Owner 已接受；
- 读取 CLEAN review，提炼 1~3 个核心机制（CL-001……）；
- 用简短中文解释给 Owner，回答 Owner 追问；
- 请求 Owner 决策（`WAITING_FOR_OWNER_DECISION`，非 Handoff，同 Session 继续）；
- Owner 明确 `ACCEPT` 后，机械持久化 `owner-decision.md` 与 `state.owner`（`owner.status=ACCEPTED`、`owner.review_target=<review.target 完整 SHA>`），Commit + Push + 远端核验后运行 `delivery-start`，`PASS` 后才 HANDOFF Deliverer；
- Owner 明确 `REJECT` 后，持久化真实拒绝理由与退回对象，Commit + Push + 远端核验后退回 Coder 或 Analyst，不运行 `delivery-start`；
- Owner 尚未决定或答复含糊时保持 `owner.status=PENDING`，不交接、不代写 `ACCEPTED`。

不负责：改业务代码、改 Contract、改 Review 结果、自行生成 `ACCEPTED`（必须来自 Owner 明确决定）。禁止以 `owner.status=NOT_REQUIRED` 绕过正常 Owner 决策。

Owner 接受的是 Cleaner 已 CLEAN 的 **这个具体 snapshot**（`review.target`）。

## 8. Deliverer

Deliverer 回答：已经通过审查的里程碑，能否在要求的环境中真实运行？

仅在完整业务模块、关键架构阶段、Docker/运行环境交付或 Owner 明确要求时启用。

负责：

- 开始前运行入口 Gate `delivery-start`；Gate `PASS` 只证明可以开始验收，不等于交付通过；
- 确认交付版本与 Cleaner 的审查对象一致（`delivery.review_target = review.target`）；
- 记录实际参与集成验证的 feature snapshot（`feature_head`）与当时 shared integration base（`develop_base`），三者均写完整 40 位 SHA；
- 独立验证适用的构建、启动、API 主链路和真实依赖；
- 核对重要数据结果，而不只看 HTTP 200；
- 在 `delivery.md` 记录环境、结果、证据、未验证项和剩余风险；
- 验收结论持久化后 Commit + Push 当前 feature、核验远端同步，再运行 `merge-ready`；只有 `merge-ready` `PASS` 才允许向 Owner 输出可集成结论。

Deliverer 可使用临时目录、临时 worktree、detached HEAD 等本地集成环境验证 `develop_base + feature_head`，但禁止创建/推送永久 integration branch、禁止 push develop、禁止最终 merge，也不创建 Release。

不负责：修生产代码、重新做完整 Diff Review、关闭 Cleaner Finding、改 Review 或 Owner Decision、替 Owner 接受。

结果：`PASS`、`FAIL` 或 `BLOCKED`。

## 9. 默认交接与 Gate

每一角色正常 Handoff 前由本角色运行下一角色的入口 Gate（`.agent/bin/workflow-check gate <name> <task>`）：

```text
Analyst   → gate coder-start      → HANDOFF Coder
Coder     → gate cleaner-start    → HANDOFF Cleaner
Cleaner   → gate owner-gate-start → HANDOFF OwnerGate
OwnerGate → gate delivery-start   → HANDOFF Deliverer
Deliverer → gate merge-ready      → HANDOFF Owner
```

每条 Handoff 前的统一出站顺序：显式 stage 本角色 owned artifacts → commit → push 当前 feature → 确认 working tree clean 且 local == `origin/<current-feature>` → 运行 Gate → `PASS` 才 HANDOFF。`CHANGES_REQUIRED` 与 Owner `REJECT` 的退回同样必须 Commit + Push，只是不经过下一角色入口 Gate。

TaskBuilder 不在上表内：它的通用出口是 Commit + Push + 远端同步核验，不是 Gate。仅 `NORMAL` 路线以 `coder-start` 作为 Coder 入口 Gate（`contract.status = NOT_REQUIRED`、无共享 Registry 预留）；`COMPLEX` / `ARCHITECT` 在 `contract.status = PENDING` 时直接交 Analyst，不运行 `coder-start`。Analyst 批准 Contract 后，由后续角色按各自入口 Gate 继续。

实现问题退回 Coder，设计问题交 Analyst 和 Owner，任务目标变化交 Owner 或 TaskBuilder。角色完成自己的阶段后停止，不顺手接管下一角色。

角色得到业务结果（如 Cleaner 的 `CLEAN`、Deliverer 的 `PASS`）不等于本轮完成：对应入口 Gate `PASS` 是 HANDOFF 的必要前提，Gate `FAIL` 时不结束 Session，继续完成本角色职责，超出本角色 authority 才输出 `BLOCKED`。

## 10. 测试与 Owner 验证分工

| 角色      | 关注点                                       |
| --------- | -------------------------------------------- |
| Coder     | 用长期保留的测试证明实现覆盖关键行为         |
| Cleaner   | 判断测试能否识别错误实现，并选择核心验证入口 |
| OwnerGate | 把核心机制讲清楚，请求 Owner 决策            |
| Owner     | 通过少量代码阅读和可逆 Mutation 理解不变量   |
| Deliverer | 在真实运行环境核验完整交付链路               |

运行同一命令不代表职责重复，因为各角色回答的问题不同。没有执行的检查必须如实说明，不能写成通过。
