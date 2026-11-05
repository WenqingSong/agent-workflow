# Agent Workflow V4：由 Lead Agent 组织的多 Agent 协作框架

一个面向 AI 辅助软件开发的**源码分发包**。它不提供工作流引擎，而是提供：

- **行为规范**：协作原则、权责边界、交接格式、Git 纪律、失败处理；
- **角色能力**：七个专业角色的 Prompt，供 Lead 按需调用；
- **工作记录**：六份普通 Markdown，让任何新 Session 只靠仓库恢复上下文；
- **工程支撑**：安装、升级、回滚、校验、一致性检查、源码发布。

| 项目属性 | 当前实现 |
| --- | --- |
| 类型 | 可安装到任意 Git 仓库的源码包 |
| 核心机制 | Lead Agent 负责制 + 工作记录 + 独立审查 + Git 证据 |
| 运行依赖 | Git；工具需要 Python 3.10+ |
| 版本 | `4.0.0`（`.agent/VERSION`） |
| 发布状态 | `v4.0.0` 由 annotated tag 触发 CNB 发布流水线，以远端 Tag、Release 与附件 SHA256 核验为准 |

## 为什么是 V4

V3 用 Go Engine、`state.yaml`、五个 Gate 和 Stage 推导表来约束流程。问题是：

- 状态文件会与真实代码脱节，Gate 通过不等于代码正确；
- 角色被固化成流水线工位，无法按任务规模弹性组织；
- 机器状态机需要维护，规则改动要先改引擎；
- Agent 把精力花在「让状态合法」而不是「把事做对」。

V4 的判断是：**流程应由负责的 Agent 组织，事实应由 Git 与 Markdown 承载，机器只做它真正擅长的静态检查。**

因此 V4 删除 Engine、`state.yaml`、Schema 解析、Gate、Stage、Route、固定交接矩阵与相关二进制；保留并强化三件事：责任主体、工作记录、独立审查。

## 架构

```text
Owner
  │ 目标、范围、授权、关键决策、最终验收
  ▼
Lead Agent（唯一责任主体）
  │
  ├── 工作记录  task.md / plan.md / handoff.md / findings.md / delivery.md / decisions.md
  ├── 专业角色  TaskBuilder / Analyst / Architect / Coder / Deliverer
  ├── 独立审查  Cleaner（只读）
  └── 决策协助  OwnerGate
底层：平台原生文件、Git、命令执行、Subagent
```

四层结构：

| 层 | 位置 | 职责 |
| --- | --- | --- |
| 行为规范 | `.agent/specs/` | 协作原则、方法与边界 |
| 角色能力 | `.agent/roles/` | 七个角色 Prompt |
| 工作记录 | `.agent/tasks/<slug>/` | 普通 Markdown，记录事实与决策 |
| 工程支撑 | `.agent/adoption/`、`.agent/tools/`、`scripts/release/` | 安装、升级、回滚、发布、一致性检查 |

## 核心设计

### 1. Lead 是唯一责任主体

一个任务只有一个 Lead，对结果负责：建档、计划、决定调用哪些角色、保证审查独立、提交与交付。角色是能力边界，不是工位——可以跳过、并行、多轮。

### 2. 事实写在仓库里

角色只拿「角色名 + 任务目录」启动，读取六份记录与 Git 事实，输出开工声明后开工。聊天记录不是事实来源。冷启动必须能回答五个问题：目标、已完成、未完成、当前问题、下一步。

### 3. 作者不得自审

Cleaner 只读，且不得审查自己参与实现的变更。审查结论绑定完整 commit SHA；审查后代码一旦变化，结论自动失效，必须重新审查。无法获得独立审查者时必须如实标注，不得声称已通过独立审查。

### 4. 失败处理简单可执行

先判断失败类别；同一条路径最多两次有依据的尝试；仍失败升级 Lead；涉及范围、授权、重大架构和不可逆操作升级 Owner。所有失败写入 `findings.md`，不引入程序化预算或状态机。

### 5. 发布只产出源码资产

Release 包含源码归档、SHA256、`SHA256SUMS`、`version.json` 与 Release Notes。不再构建 Engine 二进制。

## 安装

```bash
# 构建源码分发包（源仓库必须干净）
.agent/adoption/workflow-adoption dist --source . --output dist/

# 首次安装（默认 Dry-run）
.agent/adoption/workflow-adoption install \
  --target /path/to/host \
  --package dist/agent-workflow-src-4.0.0.tar.gz \
  --distribution-sha256 <sha256> \
  --source-commit <40-hex> \
  --integration-branch <owner-confirmed-branch> \
  --owner <owner> --owner-authorized --execute

# 升级 / 回滚 / 校验
.agent/adoption/workflow-adoption upgrade  --target /path/to/host --package <tgz> \
  --distribution-sha256 <sha> --source-commit <40-hex> --owner <owner> --owner-authorized --execute
.agent/adoption/workflow-adoption rollback --target /path/to/host --execute
.agent/adoption/workflow-adoption verify   --target /path/to/host
```

安装/升级只写 `.agent/` 下的 `managed` 文件，不创建、不覆盖宿主 `AGENTS.md`、README、CI、业务代码与任务记录。

## 验证

```bash
python3 -m unittest discover -s .agent/adoption/tests -v
python3 -m unittest discover -s scripts/release/tests -v
.agent/adoption/workflow-adoption check --repo .
```

## 仓库结构

```text
.agent/specs/          七份行为规范（入口：WorkflowAgentDirective.md）
.agent/roles/          七个角色 Prompt
.agent/templates/      任务记录模板、接入模板、provenance 模板
.agent/adoption/       安装 / 升级 / 回滚 / 校验 / 一致性检查
.agent/tools/          只读 Git 事实
docs/design/           V4 架构设计
docs/history/v3.4.0/   V3.4.0 归档（只供追溯）
docs/releases/         版本发布说明
dogfood/ledgerd/       Dogfood 项目：用 V4 实际开发的 Go 账本服务
scripts/release/       源码资产构建与发布校验
```

## 已知边界

- 框架不认证 Agent 身份与 Session 独立性；独立审查是协作约定，不是机器可验证事实。
- `check` 是静态文件与文本检查，不解析任务状态，不能证明 Agent 遵守了规范。
- 安装/升级工具在 Linux 完成真实 E2E 验证；Windows 原生未验证。
- Dogfood 在单仓库内以子项目形式完成，真实提交历史保留在本仓库 `v4.0.0` 分支；未在多个外部真实项目完成跨项目接入。
