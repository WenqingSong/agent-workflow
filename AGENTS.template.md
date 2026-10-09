# AGENTS

本仓库使用 **Workflow V3**。Agent 进入仓库后，先按职责定位规则，再阅读宿主项目长期工程规范。

## Workflow 入口

执行 Workflow Task 前，根据职责读取：

1. 协作规范：`.agent/specs/AgentCollaborationSpecification.md`
2. 职责边界：`.agent/specs/Five-AgentResponsibilityBoundary.md`
3. 本角色 Prompt：`.agent/roles/<Role>.md`
4. 机器配置：`.agent/workflow.yaml`
5. 当前任务事实：`.agent/tasks/<task-id>/` 下的 `task.md` / `state.yaml` / `contract.md` 等

架构 Design（WHY / architecture）：

`docs/design/agent-workflow.md`

首次跨项目 Adoption：

`.agent/specs/WorkflowAdoption.md` + `.agent/templates/PROJECT_ADAPTATION.template.md`

`.agent/` namespace 索引见 `.agent/README.md`。

Workflow Engine（只读 Gate Validator `workflow-check`）是独立 Go Module，位于 `.agent/engine/`（`module workflow-v2-engine`），不依赖宿主业务 Go Module、不通过 `go.work`/`replace` 关联：

```text
# 构建本地 binary（进入 .agent/bin/，被 Git ignore）
go -C .agent/engine build -o ../bin/workflow-check ./cmd/workflow-check

# 运行（正式 Gate 机器入口优先 direct binary，保留 0/1/2 exit code）
.agent/bin/workflow-check gate <gate> <task>
```

Engine 测试与业务测试分开：Engine 用 `GOWORK=off go -C .agent/engine test ./...`；业务模块在宿主 repo 根按其自身方式测试（不含 Engine）。

注意：不要从历史 `.agent/tasks/*` 推断当前 Workflow 规则。当前 Workflow 权威来源是：

- `docs/design/agent-workflow.md`（Architecture）
- `.agent/specs/*`（Execution Specifications）
- `.agent/roles/*`（Role Instructions）
- `.agent/workflow.yaml`（Machine Configuration）

---

## 宿主项目工程规范

<!-- Adoption 时由宿主项目 materialize 其自身的长期工程规范（框架、目录结构、build/test 命令、业务约定等）。本模板不携带任何具体项目规则。 -->
