# V3.4.0 历史归档

本目录保存 Workflow V3.4.0 的规范、角色 Prompt、设计文档与任务证据，仅用于追溯，**不是当前规范**。

V4.0.0 已删除 V3 的以下机制：

- Workflow Engine（`.agent/engine/`，Go 实现）
- `state.yaml` 与 schema 校验
- 五个机器 Gate（`coder-start` / `cleaner-start` / `owner-gate-start` / `delivery-start` / `merge-ready`）
- Stage 推导表与 Route 选择
- 固定交接矩阵与 `HANDOFF_PROMPT`
- 程序维护的任务生命周期
- 为上述机制服务的二进制

被删除的实现仍保存在 Git 历史中，可通过 `git log -- .agent/engine` 查看。

## 目录

| 路径 | 内容 |
| --- | --- |
| `specs/` | V3.4.0 七份规范（含 `WorkflowRouteSelection.md`，V4 已无此概念） |
| `roles/` | V3.4.0 七个角色 Prompt |
| `design/agent-workflow.md` | V3 架构设计与状态模型 |
| `templates/` | V3 的 `workflow.yaml` 与 Registry 模板（V4 不再使用） |
| `tasks/v3.4.0-independent-session-handoff/` | V3.4.0 自身的任务证据，含 `state.yaml` 样本 |

## 保留理由

- 历史版本的设计取舍需要可追溯；
- 迁移到 V4 的宿主可能需要对照旧规则；
- 任务证据是当时交付的凭据，不能因版本升级而消失。

## 使用限制

- 不得把本目录内容当作 V4 规范执行；
- 不得据此恢复 Engine、Gate、Stage 或 `state.yaml`；
- 需要理解 V4 请读 `.agent/specs/WorkflowAgentDirective.md`。
