# Workflow V3.0.0

这是一个 source-vendored engineering workflow package。

包含：

- six role workflow
- Workflow Engine
- machine gates
- Evidence Snapshot
- Generic Shared Resources
- strict YAML
- Adoption specification/templates

宿主要求：

- Git
- Go toolchain（用于 Engine）

首次使用：

1. 将 package assets materialize 到宿主 repository
2. 执行 Adoption：
   DISCOVER
   RESOLVE
   MATERIALIZE
   VERIFY
   CLEANUP
3. 生成项目自己的 `.agent/workflow.yaml`
4. 初始化项目需要的 Registry
5. build workflow-check
6. 开始正常六角色 Workflow

Known Validation Gap：

Cross-project Adoption Dogfood 未执行，
由 Owner 主动延期。
