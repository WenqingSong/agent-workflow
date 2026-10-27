# Workflow V3.1.0 — Package Manifest

## Workflow Version

3.1.0

> 这是当前 `.agent/VERSION` 的内容版本（Source Version）。
>
> | 字段 | 值 |
> | --- | --- |
> | Source Version | `3.1.0` |
> | Source Commit | 真实完整 Git SHA（由发布构建从 `git rev-parse HEAD` 推导，不使用写死值） |
> | Release Tag | 未来的 `v3.1.0`（本 Manifest 不预填、不声称已存在） |
> | Published Status | `NOT_RELEASED` |
>
> 版本号与 Git Commit 是两个独立字段：版本号来自 `.agent/VERSION`，Commit 来自
> Git 对象。P08-a 只做发布准备与候选集成，不创建正式 Tag / Release。

## Package Type

Source Distribution（源码分发包），由 `.agent/adoption/workflow-adoption dist`
从干净、精确的 Commit 构建。

## File Classes

| 分类 | 内容 | 进入分发包 | 安装到宿主 |
| --- | --- | --- | --- |
| `managed` | `.agent/roles/**`、`.agent/specs/**`、`.agent/templates/**`、`.agent/engine/**`、`.agent/adoption/**`（`tests/` 除外）、`.agent/README.md`、`.agent/VERSION`、`.agent/bin/.gitignore` | 是 | 是；升级时参与 B/L/N |
| `package_only` | 根 `README.md`、`PACKAGE_MANIFEST.md`、`.cnb.yml`、`docs/design/agent-workflow.md`、`docs/releases/**`、`scripts/release/**`、`.agent/adoption/tests/**` | 是 | 否；仅供阅读、发布校验与追溯 |
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
- Release Notes（`docs/releases/v3.1.0.md`，`package_only`）
- Release engineering（`scripts/release/`：四平台构建、资产校验、promote 门槛与失败注入测试，`package_only`）
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
| Source Distribution 内容 | 见 `Source Distribution Contract` 与包内 `MANIFEST.json` |
| 绝不能打包 | 见 `Excluded` |
| Release Binary 位置 | 宿主 `.agent/bin/workflow-check`（`0755`，被 `.agent/bin/.gitignore` 忽略） |
| 发布 Binary 命名 | `workflow-check_3.1.0_<goos>_<goarch>[.exe]`；四个目标：`linux_amd64`、`linux_arm64`、`darwin_arm64`、`windows_amd64` |
| Binary 构建参数 | `CGO_ENABLED=0`、`GOWORK=off`、`GOFLAGS=-mod=readonly`、`go build -trimpath -buildvcs=false -mod=readonly`；`-ldflags="-s -w -X main.version=<VERSION> -X main.commit=<HEAD> -X main.buildSource=release"` |
| Binary 版本可核验 | `workflow-check version` / `version --json` 输出 framework_version、source_commit、build_source、go_version、platform；`build_source=release` 表示发布资产，`source` 表示从分发包 Engine 构建，未注入时为 `dev` / `unknown` |
| 安装后 Binary 自证 | 从分发包构建的 `.agent/bin/workflow-check` 注入分发包的版本与 Commit（`build_source=source`）；使用发布资产安装时为 `build_source=release` |
| Binary 来源核验 | 必须与 `source.commit` 使用同一 Engine 源码构建；安装时传 `--workflow-check`、`--workflow-check-sha256`、`--workflow-check-commit` |
| 校验和文件 | `SHA256SUMS`（`<sha>  <file>`，按文件名排序）；覆盖四平台 Binary、Source Distribution、Source Distribution 校验文件与 Release Notes |
| 元数据文件 | `version.json`：Version、Tag、Source Commit、Go Toolchain、Build Flags、平台矩阵、每项资产 SHA256 与验证等级、Distribution Manifest 信息、`published_status=NOT_RELEASED` |
| 哈希环规则 | `SHA256SUMS` 不覆盖自身与 `version.json`；`version.json` 记录 `SHA256SUMS` 摘要并自排除。清单可独立校验，无循环引用 |
| 验证等级 | `runtime`：在构建环境真实执行 `version`（Linux x86-64）；`static`：仅交叉编译 + 文件类型 / 架构 / Go buildinfo 校验；`content`：成员与 SHA256 校验；`derived`：由其他资产推导 |
| 安装来源记录 | 只由成功的 install / upgrade 事务写 `.agent/upstream.yaml`；`source.release` 在正式 Tag 前为空 |
| 更新 upstream.yaml 的时机 | 安装成功且全部校验通过；升级无 CONFLICT / BASE_UNKNOWN / 未授权删除且写入后校验通过 |
| Source Distribution 验证 | `workflow-adoption inspect --package <archive> --expected-sha256 <sha>`；发布侧另用 `scripts/release/release.py verify-dist` 校验成员、MANIFEST 与七角色完整性 |
| Host 最终状态验证 | `workflow-adoption verify --target <host>`；必须检查文件、binary、B 快照、真实 Gate 与历史 Evidence |
| 发布顺序 | `tag_push` → 守卫 → 构建 + 校验 → pre-release（`preRelease=true, latest=false`）→ 上传附件 → 平台侧重新下载核对 → promote（`overlying=true`）→ 再核对 → `latest=true` |
| 失败语义 | 任一阶段失败即非零退出，后续阶段不执行；未通过附件核对不得 promote，未通过 promote 后核对不得标记 latest，也不得声称 GA 完成 |
| 发布责任 | P08 负责 `v3.1.0` Tag、CI 构建、校验和、Release 资产与正式版本标记 |

本 Manifest 不引用尚不存在的 `v3.1.0` Release URL。正式发布前不得把
`release.tag` / `release.url` 填成非空值，也不得把开发 Commit 描述为 GA。
Source Distribution 内的 `MANIFEST.json` 由 P07 工具生成，其
`release.tag` / `release.url` 必须保持为空，构建校验会拒绝非空值。

## Engine Module

workflow-v2-engine

> 注意：这是 historical technical identifier，不等于 Workflow major version。

## Verification

```bash
GOWORK=off go -C .agent/engine test -count=1 ./...
GOWORK=off go -C .agent/engine vet ./...
GOWORK=off go -C .agent/engine build ./...
GOWORK=off go -C .agent/engine test -race -count=1 ./...
python3 .agent/adoption/tests/test_workflow_adoption.py -v
python3 scripts/release/tests/test_release_scripts.py -v
git diff --check
```

发布资产校验（在发布候选 Commit 上）：

```bash
# Tag / Annotated / Commit / VERSION 守卫
python3 scripts/release/release.py guard --repo . --tag v3.1.0

# 构建全部资产（输出目录必须是独立的顶层未跟踪目录）
python3 scripts/release/release.py build --repo . --output dist --tag v3.1.0

# 资产集合 / SHA256SUMS / Binary / Source Distribution 校验
python3 scripts/release/release.py verify-assets --dir dist --report dist/reports/local-verification.json

# 两次独立构建比较可复现性
python3 scripts/release/release.py repro --repo . --tag v3.1.0
```

安装 / 升级规范与 B/L/N 决策表见 `.agent/specs/WorkflowAdoption.md`。
工具用法、平台边界与恢复方式见 `.agent/adoption/README.md`。
