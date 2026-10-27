# Workflow V3.0.0 — Package Manifest

## Workflow Version

3.0.0

## Package Type

Source Distribution

## Included

- Engine（`.agent/engine/`，独立 Go Module）
- Roles（`.agent/roles/`）
- Specs（`.agent/specs/`，含 Workflow 专用指令入口 `.agent/specs/WorkflowAgentDirective.md`）
- Templates（`.agent/templates/`：Adoption 模板、workflow.yaml 模板、clean registry 模板）
- Architecture Design（`docs/design/agent-workflow.md`）
- Bootstrap material（`.agent/README.md`、`.agent/VERSION`、`.agent/bin/.gitignore`）

## Excluded

- Project runtime tasks（`.agent/tasks/`）
- Host root `AGENTS.md`（宿主所有：安装不创建、不覆盖、不删除）
- Live Registry allocations
- Project workflow.yaml（live）
- Business code
- Business tooling
- Binary（`workflow-check`）
- Secrets
- Git history

## Engine Module

workflow-v2-engine

> 注意：这是 historical technical identifier，不等于 Workflow major version。
