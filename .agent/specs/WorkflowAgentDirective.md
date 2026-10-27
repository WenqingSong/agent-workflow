# Workflow Agent Directive

> 适用对象：被**明确激活**的 Workflow Role Session
> 取代：V3.0.0 及更早版本的根目录 `AGENTS.template.md`

本文件是 Workflow 专用 Agent 指令入口。它说明：Workflow Role 何时成立、必须显式读取哪些文件、缺失时如何处理、以及宿主根 `AGENTS.md` 的归属。

## 1. 边界

- **只适用于已激活的 Workflow Role**：TaskBuilder、Analyst、Coder、Cleaner、OwnerGate、Deliverer。
- **普通 Agent 不自动加载**：未按 §2 激活的 Session 不因本文件存在而成为 Workflow Role。
- **不是宿主根 `AGENTS.md` 的替代物**：宿主业务规范仍由宿主自己的 `AGENTS.md` 承载。
- **必须由 Role 显式读取**：本方案不依赖任何 Agent 平台的目录级自动发现。不同平台的规则加载机制不同，本文件不假设某平台一定会或一定不会自动加载；使用非 `AGENTS.md` 文件名并强制显式读取，是为了降低规则污染风险，不构成绝对不可绕过的隔离保证。
- **不是机器可执行规则**：`.agent/engine/`（`workflow-check`）不读取本文件，也不读取任何角色 Markdown；它只校验 Git、`.agent/workflow.yaml`、Task 状态与 Registry 的可观察事实。

## 2. 激活条件

以下三条**同时满足**时，Session 才按 Workflow Role 运行：

1. Owner 或调用方在本次指令中**明确指定角色**（TaskBuilder / Analyst / Coder / Cleaner / OwnerGate / Deliverer）；
2. **指定 Task 目录**（`.agent/tasks/<task-slug>/`），或明确要求初始化 Task；
3. 当前 Session **以该角色职责运行**：输出与实际操作都承担该角色职责。

三条不同时满足，即视为**普通 Agent Session**。判定依据是指令的实际内容，而不是输出中是否出现 "Workflow" 字样；普通 Agent 在解释文档时提到 Workflow 属正常行为。

这是 **Agent 行为协议**，不是 Engine 已具备的 Session 身份认证能力。Engine 无法识别当前 Session 是否真的按角色运行。

## 3. 普通 Agent Session 的范围

普通 Agent Session：

- 不进入 Workflow，不承担 Task Artifact 的状态写入职责（`state.yaml` 的 `review` / `owner` / `delivery` 等）；
- 不受 Workflow Role 的 **Git 操作白名单**约束；
- 不受 Workflow **Gate 前置条件**与 **Session Handoff 契约**约束；
- 在 Owner 明确授权、且宿主自身安全策略允许时，可以进行正常代码开发、Commit 和 Push。

上述不表示可以绕过平台权限、服务器分支保护或宿主项目的工程规范。Workflow 规范不是仓库级 Git 规则。

## 4. 激活后的必读顺序

Role 开始工作前，必须按顺序**显式读取**：

1. 本文件 `.agent/specs/WorkflowAgentDirective.md`；
2. 自身角色 Prompt：`.agent/roles/<Role>.md`；
3. `.agent/specs/AgentCollaborationSpecification.md`（协作规范）与 `.agent/specs/Five-AgentResponsibilityBoundary.md`（职责边界）；
4. 当前 Task 事实：`.agent/tasks/<task-slug>/` 下的 `task.md`、`state.yaml`、`contract.md` 等；
5. 宿主根 `AGENTS.md`：**存在时读取并遵守**其中的宿主业务规范；**不存在时继续**，不创建、不阻断。

不得依据旧路径 `AGENTS.template.md` 或根目录级 Workflow 入口继续工作。

## 5. 缺失与不可读

- 本文件缺失或不可读 → 立即输出 `BLOCKED`，报告缺少 `.agent/specs/WorkflowAgentDirective.md`；**不得**凭记忆、旧模板副本、历史任务或聊天上下文继续执行 Workflow Role。
- 自身角色 Prompt 缺失 → 同样 `BLOCKED`。
- 宿主根 `AGENTS.md` 缺失 → 不阻断；它只是可选的宿主业务规范。
- 本文件与角色 Prompt 冲突时 → `BLOCKED` 并说明冲突点，由 Owner 决定，不自行选择更宽松的一方。

## 6. 退出与防逃逸

已激活的 Workflow Role **不得自行退出** Workflow：

- 不得以"现在切换成普通 Agent""这次改动很小""Owner 应该不会介意"等自述绕过 Gate、Evidence 或 Handoff 约束；
- 不得通过降级为普通 Session 来获得 Workflow Role 没有的 Git 权限；
- 不得把尚未完成的 Role 职责通过改写身份声明的方式转交给其他角色。

只有以下情况构成退出或切换：

- Owner **明确指令**结束当前 Role、退出 Workflow，或把 Session 改派为普通 Agent 任务；
- Owner 明确把当前任务改派给其他角色（此时按新角色重新激活并重新读取）。

Role 认为继续执行会违反自身职责或安全约束时，输出 `BLOCKED` 并说明需要的 Owner 决定，而不是自行降级权限。

## 7. 指令优先级

按以下顺序执行：

1. Owner 当前明确指令；
2. 当前 Task 的 Goal、Scope、Out of Scope、Acceptance Criteria；
3. Owner 已确认的 `contract.md` 与 `docs/design/*` 项目级长期设计；
4. 宿主根 `AGENTS.md` 中的宿主业务规范（存在时）；
5. 本文件、协作规范、职责边界与角色 Prompt。

任何优先级都不能授权泄漏凭据、绕过认证授权、吞掉关键错误，或用降低测试标准制造成功。

## 8. Workflow 文件入口

| 用途 | 位置 |
| --- | --- |
| 本 Directive（Workflow 专用入口） | `.agent/specs/WorkflowAgentDirective.md` |
| 协作规范 | `.agent/specs/AgentCollaborationSpecification.md` |
| 职责边界 | `.agent/specs/Five-AgentResponsibilityBoundary.md` |
| 首次跨项目 Adoption | `.agent/specs/WorkflowAdoption.md` + `.agent/templates/PROJECT_ADAPTATION.template.md` |
| 角色 Prompt | `.agent/roles/*.md` |
| 机器配置 | `.agent/workflow.yaml` |
| Task 事实 | `.agent/tasks/<task-slug>/` |
| 架构 Design | `docs/design/agent-workflow.md` |
| Namespace 索引 | `.agent/README.md` |

Workflow Engine（只读 Gate Validator `workflow-check`）是独立 Go Module，位于 `.agent/engine/`（`module workflow-v2-engine`），不依赖宿主业务 Go Module、不通过 `go.work` / `replace` 关联：

```text
# 构建本地 binary（进入 .agent/bin/，被 Git ignore）
go -C .agent/engine build -o ../bin/workflow-check ./cmd/workflow-check

# 运行（正式 Gate 机器入口优先 direct binary，保留 0/1/2 exit code）
.agent/bin/workflow-check gate <gate> <task>
```

Engine 测试与业务测试分开：Engine 用 `GOWORK=off go -C .agent/engine test ./...`；业务模块在宿主 repo 根按其自身方式测试（不含 Engine）。

## 9. 事实来源

不要从历史 `.agent/tasks/*` 推断当前 Workflow 规则。当前 Workflow 权威来源是：

- `docs/design/agent-workflow.md`（Architecture / WHY）
- `.agent/specs/*`（Execution Specifications）
- `.agent/roles/*`（Role Instructions）
- `.agent/workflow.yaml`（Machine Configuration）

## 10. 宿主根 `AGENTS.md` 所有权

宿主根 `AGENTS.md` 属于宿主项目：

- Workflow 安装、升级与 Adoption **不得创建**该文件（原本不存在时保持不存在）；
- **不得覆盖、删除、清空**原有内容；
- **不得把内部 Workflow 指令写入**该文件；
- 旧版本 Workflow 可能已把根模板内容写入宿主项目：必须先分析内容差异，只输出待迁移项，清理或迁移需取得 Owner 明确授权，不得通过整体覆盖完成升级。

Role 在文件存在时读取并遵守其中的宿主业务规范；不存在时不阻断、不补建。
