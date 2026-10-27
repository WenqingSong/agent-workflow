# Workflow V3.0.0 — Package Manifest

## Workflow Version

3.0.0

> 这是当前 `.agent/VERSION` 的内容版本。P07 不把开发基线冒充 `v3.1.0`；
> 正式 `v3.1.0` Tag、CI 构建、校验和与 Release 资产由 P08 负责。

## Package Type

Source Distribution（源码分发包），由 `.agent/adoption/workflow-adoption dist`
从干净、精确的 Commit 构建。

## File Classes

| 分类 | 内容 | 进入分发包 | 安装到宿主 |
| --- | --- | --- | --- |
| `managed` | `.agent/roles/**`、`.agent/specs/**`、`.agent/templates/**`、`.agent/engine/**`、`.agent/adoption/**`（`tests/` 除外）、`.agent/README.md`、`.agent/VERSION`、`.agent/bin/.gitignore` | 是 | 是；升级时参与 B/L/N |
| `package_only` | 根 `README.md`、`PACKAGE_MANIFEST.md`、`.cnb.yml`、`docs/design/agent-workflow.md`、`.agent/adoption/tests/**` | 是 | 否；仅供阅读、发布校验与追溯 |
| `generated` | `.agent/workflow.yaml`、`.agent/upstream.yaml`、`.agent/bin/workflow-check` | 否 | 仅由成功的 install / upgrade 事务生成或更新 |
| `protected` | 根 `AGENTS.md`、宿主 README / CI / Design、`.agent/workflow.yaml`、`.agent/tasks/**`、`.agent/registry/**`、业务文件、已有证据 | 否 | 永不覆盖或删除 |

`managed` 与 `package_only` 的精确清单由分发包内 `MANIFEST.json` 的
`files[].path` / `files[].install`、`install.managed`、
`install.package_only` 给出；工具会交叉校验，分类不一致时分发或安装失败。

## Included

- Engine（`.agent/engine/`，独立 Go Module `workflow-v2-engine`）
- Roles（`.agent/roles/`）
- Specs（`.agent/specs/`，含 Workflow 专用指令入口与 Adoption / 安装 / 升级规范）
- Templates（`.agent/templates/`：Adoption、workflow.yaml、upstream.yaml、clean registry）
- Adoption tooling（`.agent/adoption/`：分发、首装、升级、校验、恢复）
- Bootstrap material（`.agent/README.md`、`.agent/VERSION`、`.agent/bin/.gitignore`）
- Architecture Design（`docs/design/agent-workflow.md`，`package_only`）
- Root `README.md`、本 Manifest、`.cnb.yml`（`package_only`）

## Excluded

以下内容绝不能进入发布包：

- Host live configuration：`.agent/workflow.yaml`
- Provenance / B snapshot：`.agent/upstream.yaml`
- Project runtime tasks：`.agent/tasks/`
- Live registry allocations：`.agent/registry/`
- Runtime binary：`.agent/bin/workflow-check`
- Host root `AGENTS.md`（宿主所有：安装不创建、不覆盖、不删除）
- Host business code、business tests、business design、host CI
- Secrets
- Git history / 根 `.git/**`

## Source Distribution Contract

构建：

```bash
.agent/adoption/workflow-adoption dist \
  --source . \
  --output dist/
```

产物：

```text
dist/agent-workflow-src-<commit12>.tar.gz
dist/agent-workflow-src-<commit12>.tar.gz.sha256
archive top-level: agent-workflow-src-<commit12>/
  MANIFEST.json
  README.md
  PACKAGE_MANIFEST.md
  .cnb.yml
  .agent/...
  docs/design/agent-workflow.md
```

`MANIFEST.json` 至少包含：

- `format` / `kind` / `framework_version`
- `source.repository` / `source.commit` / `source.tree`
- `compatibility.config_schema = 1`、`compatibility.state_schema = [2, 3]`
- 每个成员的 `path` / `sha256` / `mode` / `install` 分类
- `install.managed`、`install.package_only`、`install.generated`、`install.protected`
- `excluded` 清单
- `release.tag = ""`、`release.url = ""`（P08 发布前不得填写）

构建要求：

- 源仓库必须干净，包括无未跟踪文件；
- 只接受显式分类的 tracked 文件；出现未分类文件时构建失败；
- tracked 的宿主数据（workflow.yaml / upstream.yaml / tasks / registry / binary / AGENTS.md）一律使构建失败；
- 归档路径安全：拒绝绝对路径、`..` 穿越、符号链接、硬链接、设备 / FIFO；
- 归档确定性：固定 mtime、uid/gid、uname/gname 与排序。

## Release Consumer Contract（P08）

| 项目 | 契约 |
| --- | --- |
| Source Distribution 内容 | 见 `Source Distribution Contract` 与 `MANIFEST.json` |
| 绝不能打包 | 见 `Excluded` |
| Release Binary 位置 | 宿主 `.agent/bin/workflow-check`（`0755`，被 `.agent/bin/.gitignore` 忽略） |
| Binary 来源核验 | 必须与 `source.commit` 使用同一 Engine 源码构建；发布 SHA256；安装时传 `--workflow-check`、`--workflow-check-sha256`、`--workflow-check-commit` |
| 安装来源记录 | 只由成功的 install / upgrade 事务写 `.agent/upstream.yaml`；`source.release` 在正式 Tag 前为空 |
| 更新 upstream.yaml 的时机 | 安装成功且全部校验通过；升级无 CONFLICT / BASE_UNKNOWN / 未授权删除且写入后校验通过 |
| Source Distribution 验证 | `workflow-adoption inspect --package <archive> --expected-sha256 <sha>` |
| Host 最终状态验证 | `workflow-adoption verify --target <host>`；必须检查文件、binary、B 快照、真实 Gate 与历史 Evidence |
| 发布责任 | P08 负责 `v3.1.0` Tag、CI 构建、校验和、Release 资产与正式版本标记 |

本 Manifest 不引用尚不存在的 `v3.1.0` Release URL。P08 发布前不得把
`release.tag` / `release.url` 填成非空值，也不得把开发 Commit 描述为 GA。

## Engine Module

workflow-v2-engine

> 注意：这是 historical technical identifier，不等于 Workflow major version。

## Verification

```bash
GOWORK=off go -C .agent/engine test -count=1 ./...
GOWORK=off go -C .agent/engine vet ./...
python3 .agent/adoption/tests/test_workflow_adoption.py -v
git diff --check
```

安装 / 升级规范与 B/L/N 决策表见 `.agent/specs/WorkflowAdoption.md`。
工具用法、平台边界与恢复方式见 `.agent/adoption/README.md`。
