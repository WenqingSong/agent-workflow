# 自研 Workflow V3：基于 Git 证据的多 Agent 工程协作框架

这是一个面向 **AI 辅助软件开发** 的源码分发包：用六个独立角色会话完成需求拆解、设计、实现、审查、人工决策与交付验证；用 Git 提交和仓库内的任务文件保存跨会话事实；用独立的 Go 校验器判断能否进入下一阶段。

项目关注的是 **Agent 写出的代码如何被审查、决策和交付**。它提供协作协议与只读校验器，不负责自动调用模型、调度 Agent 或替人合并代码。

| 项目属性 | 当前实现 |
| --- | --- |
| 类型 | 可复制到其他 Git 仓库的源码包（source-vendored package） |
| 核心组成 | 六角色协作规范、任务状态与证据文件、五个机器 Gate、Go 校验器、项目接入规范 |
| 运行依赖 | Git；构建校验器需要 Go 1.23+ |
| 版本 | Workflow V3.0.0；Go Module 名 `workflow-v2-engine` 是保留的历史标识 |

## 为什么做这个项目

在多轮 AI 辅助开发中，只靠聊天记录传递任务，容易出现三个问题：后续角色沿用过期上下文、实现者审查自己的代码、审查结论与后来变化的代码不再对应。项目把角色间的交接建立在 **Git 提交 + 仓库文件** 上，并在交接前检查实际仓库状态。

```text
TaskBuilder → Analyst → Coder → Cleaner → OwnerGate → Deliverer → 人类 Owner 集成
    任务       设计       实现      独立审查     人工决策记录     交付验证
```

每个角色使用独立会话，重新读取当前 Git 和任务文件。人类 Owner 保留关键设计接受、核心逻辑接受及最终分支集成的决定权。角色职责与可修改文件的边界见 [协作规范](.agent/specs/AgentCollaborationSpecification.md) 和 [职责边界](.agent/specs/Five-AgentResponsibilityBoundary.md)。

## 核心设计

### 1. 用提交固定被审查的对象

Contract、代码审查和交付验证分别绑定明确的 Git commit SHA。以代码审查为例，`review.target` 指向实现提交；后续只允许该阶段定义的任务元数据提交。如果实现代码在审查后变化，旧的 `CLEAN` 结论会在校验时失效，而不是继续信任一个写在状态文件里的结果。

例如：Cleaner 针对实现提交 `C1` 给出 `CLEAN`，之后有人修改业务代码形成 `C2`；即使状态文件仍写着 `CLEAN`，后续 Gate 也会拒绝沿用针对 `C1` 的审查结论。

### 2. 用只读 Gate 检查交接条件

`.agent/engine/` 是独立 Go Module，提供 `workflow-check`。它读取 Git、`.agent/workflow.yaml`、任务状态及 Registry，返回 `PASS`、`FAIL` 或 `ERROR`；不会修改文件、提交或推送。

| Gate | 检查目的 |
| --- | --- |
| `coder-start` | Contract 约束和共享资源授权是否有效 |
| `cleaner-start` | 待审查实现是否已提交、已推送且目标稳定 |
| `owner-gate-start` | 独立审查结论是否仍对应当前实现 |
| `delivery-start` | 人类接受的审查目标是否仍有效 |
| `merge-ready` | 交付验证是否仍对应当前 feature 与集成分支基线 |

Gate 同时检查交接所需的 Git 状态，例如工作区是否干净、本地 feature 是否已同步到远端。`merge-ready` 仅表示机器条件满足，最终合并仍由 Owner 决定。

### 3. 在共享分支登记跨任务资源

对迁移版本号、错误码域等需要全局分配的资源，任务状态只声明需要的值；校验器再从配置指定的集成分支 Registry 读取授权记录，检查资源值、所属任务和状态。资源种类由项目配置声明，Go 校验器不内置具体的业务资源类型。

### 4. 将通用规则与项目差异分开

角色职责、证据绑定和 Gate 属于通用 Workflow Core。首次接入一个项目时，再通过 [Adoption 规范](.agent/specs/WorkflowAdoption.md) 确定集成分支、构建命令、Registry 路径等项目事实，并生成该项目的 `.agent/workflow.yaml`。接入流程是 `DISCOVER → RESOLVE → MATERIALIZE → VERIFY → CLEANUP`。

### 5. 把 Workflow 指令与宿主规则分开

Workflow 专用入口是 [`.agent/specs/WorkflowAgentDirective.md`](.agent/specs/WorkflowAgentDirective.md)，文件名刻意不使用 `AGENTS.md`，避免与宿主的目录级自动加载规则混在一起。它只适用于被明确激活的 Workflow Role；普通 Agent Session 不会自动套用 Workflow 的 Git 白名单、Gate 和 Handoff 约束。

宿主根 `AGENTS.md` 属于宿主项目：存在时角色读取并遵守，不存在时不创建、不阻断。激活条件、缺失处理与退出规则只在上述 Directive 中维护，本 README 不复制。

## 安装与升级

本仓库是 Source Distribution 的构建源，也可以作为宿主仓库的安装 / 升级来源。首次安装与升级使用 `.agent/adoption/workflow-adoption`，默认 Dry-run，确认计划后才写入：

```bash
# 构建可校验的源码分发包（源仓库必须干净）
.agent/adoption/workflow-adoption dist --source . --output dist/

# 首次安装
.agent/adoption/workflow-adoption install \
  --target /path/to/host \
  --package dist/agent-workflow-src-<commit12>.tar.gz \
  --distribution-sha256 <sha256> \
  --source-commit <full-40-hex> \
  --integration-branch <owner-confirmed-branch> \
  --owner <owner> --owner-authorized --execute

# B/L/N 升级
.agent/adoption/workflow-adoption upgrade \
  --target /path/to/host \
  --package /path/to/new-source.tar.gz \
  --distribution-sha256 <sha256> \
  --source-commit <new-full-40-hex> \
  --owner <owner> --owner-authorized --execute
```

安装 / 升级不会创建或覆盖宿主根 `AGENTS.md`、根 README、CI、业务 Design、`.agent/workflow.yaml`、Tasks、Registry 与历史 Evidence。升级以 `.agent/upstream.yaml` 记录的精确 B 快照做三方比较；冲突、B 缺失或未授权删除时 fail closed。Registry 默认不创建，只有显式 `--resource KIND=PATH` 才创建空表。

工具当前在 Linux 完成真实 E2E 验证；Windows 原生环境未验证，需使用 WSL2 / Linux 容器。正式 `v3.1.0` Tag、Release 与 binary 资产由 P08 负责，本仓库不提前标记 GA。

## 仓库结构

```text
.agent/roles/             六个角色的操作说明
.agent/specs/             Workflow 专用指令入口、协作、职责边界与安装/升级规范
.agent/adoption/          Source Distribution 构建、首装、升级、校验与恢复工具
.agent/engine/            独立 Go Module：workflow-check 及测试
.agent/templates/         项目接入、机器配置、upstream 与 Registry 模板
docs/design/              设计取舍与完整状态模型（package_only，不覆盖宿主）
PACKAGE_MANIFEST.md       分发包包含/排除内容与 P08 发布契约
```

Workflow Role 的入口是 `.agent/specs/WorkflowAgentDirective.md`，由角色 Prompt 显式读取，不依赖目录级自动发现。宿主根 `AGENTS.md` 由宿主维护，本包不创建、不覆盖。

## 如何验证

在本仓库根目录运行校验器的测试、静态检查与构建：

```bash
GOWORK=off go -C .agent/engine test -count=1 ./...
GOWORK=off go -C .agent/engine vet ./...
GOWORK=off go -C .agent/engine build ./...
python3 .agent/adoption/tests/test_workflow_adoption.py -v
```

接入宿主项目后，可构建并运行一个具体任务的 Gate：

```bash
GOWORK=off go -C .agent/engine build -o ../bin/workflow-check ./cmd/workflow-check
.agent/bin/workflow-check gate cleaner-start .agent/tasks/your-task-id
```

本仓库是分发包，**有意不包含**宿主项目的 live `.agent/workflow.yaml`、Registry 分配和任务数据；因此 Gate 示例需在完成 Adoption 的宿主仓库中运行。更完整的接入材料见 [包清单](PACKAGE_MANIFEST.md)、[运行时说明](.agent/README.md) 和 [架构设计](docs/design/agent-workflow.md)。

## 已知边界

- Go 校验器有单元测试和基于临时 Git 仓库的集成测试；它校验可观察的仓库事实，不能证明 Agent 一定遵守 Prompt 中的所有职责约束。
- 跨项目 Adoption 的完整真实项目试用尚未执行。当前仓库提供了接入规范、工具与临时宿主 E2E 测试，不应将其描述为已在多个真实项目完成验证。
- 安装 / 升级工具的真实 E2E 仅在 Linux 临时 Git 仓库验证；macOS 与 Windows 原生环境未验证。Windows 使用 WSL2 / Linux 容器或按规范手工执行。
- 校验器不执行 Git 写操作，也不自动运行六个角色；分支生命周期与最终集成由人类 Owner 控制。
- 正式 `v3.1.0` Tag、Release、校验和与发布 binary 由 P08 产生；当前源码快照不是已发布 GA。
