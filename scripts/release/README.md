# Release Engineering（P08）

本目录只做发布工程：版本守卫、四平台构建、Source Distribution 打包、资产校验与
promote 门槛。它不重新实现 Gate、不改 State / Config Schema、不重造 P07 分发契约。

## 1. 命令

```bash
# Tag / Annotated / Commit / VERSION 守卫（不通过即非零退出）
# --expected-commit 是独立预期来源：CI 传 CNB_COMMIT，缺失时传 Git 对 Tag 的解析结果。
python3 scripts/release/release.py guard --repo . --tag v3.2.0 \
  --expected-commit "$(git rev-list -n 1 refs/tags/v3.2.0)" \
  --expected-commit-source git-tag-resolution

# 解析仓库来源（正式仓库 / 独立 RC 测试仓库），无来源或来源冲突即失败
python3 scripts/release/release.py repository --repo . --tag v3.2.0 --json

# 构建全部资产（输出目录必须是独立的顶层未跟踪目录，例如 dist）
python3 scripts/release/release.py build --repo . --output dist --tag v3.2.0 --json

# 资产集合 / SHA256SUMS / Binary / Source Distribution 全量校验
# --commit 必填：不得回退到 version.json 自报的 Commit。
python3 scripts/release/release.py verify-assets --dir dist --repo . \
  --version 3.2.0 --tag v3.2.0 --commit <40-hex> \
  --report dist/reports/local-verification.json --json

# 固定 9 项正式附件的上传前快照（独立记录每项 SHA256）
python3 scripts/release/release.py snapshot --assets dist --out dist/expected \
  --manifest dist/reports/expected-assets.json --json

# 用平台侧重新下载的副本核对 9 项附件
python3 scripts/release/release.py verify-uploaded --assets dist/expected --downloads . \
  --manifest dist/reports/expected-assets.json --commit <40-hex> \
  --report dist/reports/upload-verification.json --json

# promote / latest 前置门槛（没有 PASS 报告就 BLOCKED）
python3 scripts/release/release.py promote-gate --stage pre-promote \
  --report dist/reports/upload-verification.json --assets dist/expected \
  --tag v3.2.0 --expected-commit <40-hex> --out dist/reports/promote-gate-pre.json

# 校验 .cnb.yml 的上传 / 下载 / 复核阶段是否覆盖完整 9 项（无 glob）
python3 scripts/release/release.py check-pipeline --config .cnb.yml --dir dist --json

# 两个独立克隆中各构建一次，比较资产是否逐字节一致（要求 Tag 存在）
python3 scripts/release/release.py repro --repo . --tag v3.2.0 --json

# 汇总各阶段报告（Release Verification Report）
python3 scripts/release/release.py report --dir dist --reports dist/reports --require-all

# 输出精确清单（attachments 默认 9 项；assets 为 7 项业务资产）
python3 scripts/release/release.py list --dir dist --scope attachments --format upload
```

退出码：`0` 通过；`1` 校验失败 / 阻塞；`2` 输入或前置条件错误。

## 2. 构建矩阵与参数

| 平台 | GOOS | GOARCH | 资产名 |
| --- | --- | --- | --- |
| Linux x86-64 | `linux` | `amd64` | `workflow-check_3.2.0_linux_amd64` |
| Linux ARM64 | `linux` | `arm64` | `workflow-check_3.2.0_linux_arm64` |
| macOS Apple Silicon | `darwin` | `arm64` | `workflow-check_3.2.0_darwin_arm64` |
| Windows x86-64 | `windows` | `amd64` | `workflow-check_3.2.0_windows_amd64.exe` |

固定参数：

```text
CGO_ENABLED=0
GOWORK=off
GOFLAGS=-mod=readonly
go build -trimpath -buildvcs=false -mod=readonly \
  -ldflags "-s -w -X main.version=<VERSION> -X main.commit=<HEAD> -X main.buildSource=release"
```

- 版本来自 `.agent/VERSION`，Commit 来自 `git rev-list -n 1 refs/tags/<tag>`，不写死；
- 不注入构建时间，资产可复现；
- 未注入版本的 `go build` 输出 `dev` / `unknown` / `dev`。

## 3. 正式附件：7 项业务资产 + 2 项校验元数据

| 类别 | 文件 | 说明 |
| --- | --- | --- |
| 业务 | 4 个 `workflow-check_3.2.0_*` | 四平台 Binary |
| 业务 | `agent-workflow-src-3.2.0.tar.gz` | Source Distribution（P07 产出） |
| 业务 | `agent-workflow-src-3.2.0.tar.gz.sha256` | Source Distribution 校验文件 |
| 业务 | `agent-workflow-release-notes-3.2.0.md` | Release Notes |
| 元数据 | `SHA256SUMS` | 只覆盖 7 项业务资产 |
| 元数据 | `version.json` | 7 项业务资产摘要 + `SHA256SUMS` 自身摘要 |

合计 **9 项**，全部作为正式 Release 附件上传。

- `SHA256SUMS` 不覆盖 `version.json`；`version.json` 记录 `SHA256SUMS` 摘要并自排除，
  两者不存在互相引用的哈希环；
- 但 `version.json` 能自证的部分不足以证明“平台上的附件没被替换”，因此上传前用
  `snapshot` 独立记录 9 项 SHA256，平台侧复核只与快照比较；
- `SHA256SUMS` 的预期摘要同时受快照与 `version.json.checksums.sha256` 双重绑定；
- `verify-assets` / `verify-uploaded` 都会真实执行 `sha256sum -c SHA256SUMS`；
- 每个资产标注验证等级：`runtime`（仅 Linux x86-64，真实执行 `version`）、
  `static`（交叉编译 + 文件头架构 + Go buildinfo + 注入字面值）、`content`、`derived`。

Source Distribution **附件名固定为版本号形式**（`agent-workflow-src-3.2.0.tar.gz`），
这样 9 项附件名在 Tag 推送前就已确定，`.cnb.yml` 的上传与下载都能写精确文件名，
不依赖 glob。Commit 绑定不受文件名影响：归档顶层目录、`MANIFEST.json`
（`source.commit` / `source.repository`）、`version.json`、二进制注入值都记录完整事实。

> Go 的 buildinfo 不记录 `-ldflags`，因此注入证据通过两点核对：
> 二进制数据段中的 Commit / 版本字面值，以及 Linux x86-64 上的真实 `version --json` 输出。

## 4. 仓库来源（F2）

`build` 的仓库来源按以下顺序解析，并在 `version.json` / 各报告中区分
`repository`、`source_commit`、`release_tag`：

1. `--repository` 显式参数；
2. CNB 环境：`CNB_REPO_URL_HTTPS` / `CNB_REPO_URL` / `CNB_REPO_WEB_URL` / `CNB_GIT_URL` /
   `GIT_URL`、`CNB_REPO_SLUG`（配 `CNB_WEB_HOST` / `CNB_REPO_HOST`，默认 `cnb.cool`）；
3. Git `remote.origin.url`（仅接受远程地址形式）。

- 全部来源经大小写归一后必须指向同一仓库，否则 `REPOSITORY_CONFLICT` 失败；
- 无任何来源时 `REPOSITORY_UNKNOWN` 失败，**不会**回退到正式仓库常量；
- 正式仓库记为 `repository_mode=formal`，其他仓库记为 `independent-rc`；
- `--repository` 只影响元数据，不参与 Tag / Commit 判定，不能用来绕过 Guard。

## 5. CNB 流水线（`.cnb.yml` 的 `v3.2.0: tag_push`）

顺序：toolchain → tag-guard（绑定 `CNB_COMMIT`）→ release-context（仓库来源）→
engine-verification → adoption-e2e → build-assets → verify-assets-local（独立 `--commit`）→
snapshot-expected（9 项快照）→ check-pipeline-coverage →
create-pre-release（`preRelease=true, latest=false`）→ upload（9 项）→ download（9 项）→
verify-uploaded → promote-gate-pre → promote-ga → reupload（9 项）→ redownload（9 项）→
verify-after-promote → promote-gate-post → mark-latest → redownload（9 项）→
verify-after-latest → promote-gate-latest → release-verification-report。

失败安全：

- 任一阶段非零退出，后续阶段不执行；平台侧最多停留在 pre-release；
- 未完成附件核对不得 promote；未完成 promote 后核对不得标记 `latest`；
- 所有 `git:release` 使用 `overlying: true`（更新语义），**禁止**使用会删除并重建
  Release 的默认 `overlying: false`；
- 不依据“命令退出码为 0”推断附件仍在，一律重新下载 9 项并核对 SHA256、数量与名称；
- `check-pipeline` 静态校验：任一阶段漏传 / 漏下 `SHA256SUMS`、`version.json`，
  或退回 glob、漏传 `--commit` / `--expected-commit`、阶段顺序错乱，都会 FAIL。

## 6. 平台不确定项（必须在独立 RC 仓库验证）

以下行为在本地无法验证，正式发布前必须在**真正独立的 CNB 测试仓库**用 RC Tag 验证，
不得用正式仓库的 `v3.2.0` Tag 做实验：

1. `git:release` 的 `overlying: true` 在 Release 不存在时是否创建（本流程按“创建或更新”设计）；
2. `overlying: true` 更新后是否保留已有附件（流程对此不信任，promote 后重新上传并重新下载核对）；
3. `cnbcool/attachments` 的 `DOWNLOAD` 是否接受 9 项精确文件名列表（本流程已不使用 glob）；
4. `tag_push` 事件下 `CNB_BRANCH` 是否等于 Tag 名（guard 会强制校验，不等则直接失败）；
5. `CNB_COMMIT` 在 `tag_push` 下是否等于 Tag 解析 Commit（不等即 guard 失败；缺失时退化为
   Git 解析并显式记录来源）；
6. `CNB_REPO_SLUG` / `CNB_REPO_URL` 在测试仓库中的实际取值（来源冲突会直接失败）；
7. `golang:1.24` 镜像是否自带 `python3`（缺失时流水线会 `apt-get install python3`）。

RC-C 未在独立仓库执行前，上述项一律标记 `NOT_VERIFIED`，不得声称已验证。

## 7. 禁止事项

- 不在本目录或任何源码提交中存放 Release 资产 / 二进制；
- 不创建正式 `v3.2.0` Tag、不创建正式 GA Release、不把 Release 标记为 latest；
- 不修改 `main`，不 Force Push；
- 不上传 Docker Engine 镜像。
