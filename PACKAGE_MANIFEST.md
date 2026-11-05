# Workflow V4.0.0 — Package Manifest

## Workflow Version

4.0.0

| 字段 | 值 |
| --- | --- |
| Source Version | `4.0.0`（`.agent/VERSION`） |
| Source Commit | 由发布构建从 `git rev-parse HEAD` 推导，不写死 |
| Release Tag | `v4.0.0`（由发布流水线在 Tag 推送后创建） |
| Published Status | `NOT_RELEASED`（构建产物标记；平台状态以 CNB 校验为准） |

## Package Type

Source Distribution（源码分发包），由 `.agent/adoption/workflow-adoption dist` 从干净 commit 构建。**不包含任何编译产物。**

## File Classes

| 分类 | 内容 | 进包 | 安装到宿主 |
| --- | --- | --- | --- |
| `managed` | `.agent/specs/**`、`.agent/roles/**`、`.agent/templates/**`、`.agent/tools/**`、`.agent/adoption/**`（`tests/` 除外）、`.agent/README.md`、`.agent/VERSION` | 是 | 是，参与升级 |
| `package_only` | 根 `README.md`、`PACKAGE_MANIFEST.md`、`.cnb.yml`、`docs/**`、`dogfood/**`、`scripts/release/**`、`.agent/adoption/tests/**` | 是 | 否 |
| `generated` | `.agent/upstream.json` | 否 | 仅由成功事务写入 |
| `protected` | 宿主 `AGENTS.md`、宿主 README / CI / 业务代码、`.agent/tasks/**`、`.agent/registry/**`、`.git/**` | 否 | 永不覆盖或删除 |

## Included

- Specs（7 份）：Directive、Collaboration、Responsibility Boundary、Handoff Contract、Git Lifecycle、Loop Circuit Breaker、Adoption
- Roles（7 个）：TaskBuilder、Analyst、Architect、Coder、Cleaner、OwnerGate、Deliverer
- Templates：六份任务记录模板、接入记录模板、`upstream.json` 模板
- Tools：`.agent/tools/git-facts`（只读 Git 事实）
- Adoption tooling：dist / inspect / install / upgrade / rollback / verify / check
- Docs：`docs/design/agent-workflow.md`（V4 设计）、`docs/history/v3.4.0/`（V3 归档）、`docs/releases/`
- Dogfood：`dogfood/ledgerd/`（用 V4 开发的 Go 账本服务与工作记录）
- Release engineering：`scripts/release/`、`.cnb.yml`

## Excluded

- Workflow Engine 与任何编译二进制
- `state.yaml`、Gate、Stage、Route、Schema 解析器
- 宿主 `.agent/upstream.json`、`.agent/tasks/`、`.agent/registry/`
- 宿主 `AGENTS.md`、业务代码、业务测试、宿主 CI
- 凭据、`.git/**`、本地 `dist/`

## Source Distribution Contract

```bash
.agent/adoption/workflow-adoption dist --source . --output dist/
```

产物：

```text
dist/agent-workflow-src-4.0.0.tar.gz
dist/agent-workflow-src-4.0.0.tar.gz.sha256
archive top-level: agent-workflow-src-4.0.0/
  MANIFEST.json
  README.md
  PACKAGE_MANIFEST.md
  .cnb.yml
  .agent/**
  docs/**
  dogfood/**
  scripts/release/**
```

`MANIFEST.json`：

- `format` / `kind` / `framework_version`
- `source.repository` / `source.commit` / `source.tree`
- `files[]`：`path` / `sha256` / `mode` / `install`
- `excluded[]`
- `release.tag` / `release.url`（构建时为空）

构建要求：

- 源仓库必须干净（含无未跟踪文件，`dist/` 等被忽略的除外）；
- 只打包显式分类的 tracked 文件，出现未分类文件即失败；
- 拒绝绝对路径、`..` 穿越、符号链接、硬链接、设备/FIFO；
- 归档确定性：固定 mtime、uid/gid、uname/gname 与排序。

## Release Consumer Contract

| 项目 | 契约 |
| --- | --- |
| 资产 | `agent-workflow-src-4.0.0.tar.gz`、`.sha256`、`agent-workflow-release-notes-4.0.0.md`、`SHA256SUMS`、`version.json` |
| 命名 | 版本号命名，不含 commit 后缀；Commit 绑定由归档顶层目录、`MANIFEST.json` 与 `version.json` 承担 |
| `SHA256SUMS` | `<sha>  <file>`，按文件名排序；覆盖源码包、源码包校验文件与 Release Notes |
| 哈希环 | `SHA256SUMS` 不覆盖自身与 `version.json`；`version.json` 记录 `SHA256SUMS` 摘要并自排除 |
| `version.json` | Version、Tag、Source Commit、构建时间、资产 SHA256、`published_status` |
| 发布顺序 | `tag_push` → 守卫 → 测试 → 构建 → 本地校验 → pre-release → 上传 → 平台侧回读校验 → promote → 复核 → `latest` |
| 失败语义 | 任一阶段失败即非零退出；未通过附件核对不得 promote，未通过复核不得标记 latest |
| 禁止 | 不得构建或上传旧 Engine 二进制；不得在 `release.tag` 为空时声称已发布 |

## Verification

```bash
python3 -m unittest discover -s .agent/adoption/tests -v
python3 -m unittest discover -s scripts/release/tests -v
.agent/adoption/workflow-adoption check --repo .
python3 scripts/release/release.py guard --repo . --tag v4.0.0
python3 scripts/release/release.py build --repo . --output dist --tag v4.0.0
python3 scripts/release/release.py verify --dir dist --version 4.0.0 --tag v4.0.0 --commit <sha>
```

安装/升级规范见 `.agent/specs/WorkflowAdoption.md`。
