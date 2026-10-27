# Release Engineering（P08）

本目录只做发布工程：版本守卫、四平台构建、Source Distribution 打包、资产校验与
promote 门槛。它不重新实现 Gate、不改 State / Config Schema、不重造 P07 分发契约。

## 1. 命令

```bash
# Tag / Annotated / Commit / VERSION 守卫（不通过即非零退出）
python3 scripts/release/release.py guard --repo . --tag v3.1.0

# 构建全部资产（输出目录必须是独立的顶层未跟踪目录，例如 dist）
python3 scripts/release/release.py build --repo . --output dist --tag v3.1.0

# 资产集合 / SHA256SUMS / Binary / Source Distribution 全量校验
python3 scripts/release/release.py verify-assets --dir dist --report dist/reports/local-verification.json

# 用平台侧重新下载的副本核对附件
python3 scripts/release/release.py verify-uploaded --assets dist/expected --downloads .

# promote / latest 前置门槛（没有 PASS 报告就 BLOCKED）
python3 scripts/release/release.py promote-gate --stage pre-promote \
  --report dist/reports/upload-verification.json --assets dist/expected

# 两个独立克隆中各构建一次，比较资产是否逐字节一致（要求 Tag 存在）
python3 scripts/release/release.py repro --repo . --tag v3.1.0

# 汇总各阶段报告（Release Verification Report）
python3 scripts/release/release.py report --dir dist --reports dist/reports --require-all

# 输出精确资产清单（供附件上传 / 下载）
python3 scripts/release/release.py list --dir dist --format upload
```

退出码：`0` 通过；`1` 校验失败 / 阻塞；`2` 输入或前置条件错误。

## 2. 构建矩阵与参数

| 平台 | GOOS | GOARCH | 资产名 |
| --- | --- | --- | --- |
| Linux x86-64 | `linux` | `amd64` | `workflow-check_3.1.0_linux_amd64` |
| Linux ARM64 | `linux` | `arm64` | `workflow-check_3.1.0_linux_arm64` |
| macOS Apple Silicon | `darwin` | `arm64` | `workflow-check_3.1.0_darwin_arm64` |
| Windows x86-64 | `windows` | `amd64` | `workflow-check_3.1.0_windows_amd64.exe` |

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

## 3. 校验和与元数据

- `SHA256SUMS`：覆盖四平台 Binary、Source Distribution、Source Distribution 校验文件、Release Notes，按文件名排序；
- `version.json`：记录 `SHA256SUMS` 的 SHA256，并自排除；`SHA256SUMS` 也不包含 `version.json`，因此不存在哈希环；
- `verify-assets` 会真实执行 `sha256sum -c SHA256SUMS`，不是只比较自算摘要；
- 每个资产标注验证等级：`runtime`（仅 Linux x86-64，真实执行 `version`）、`static`（交叉编译 + 文件头架构 + Go buildinfo + 注入字面值）、`content`、`derived`。

> Go 的 buildinfo 不记录 `-ldflags`，因此注入证据通过两点核对：
> 二进制数据段中的 Commit / 版本字面值，以及 Linux x86-64 上的真实 `version --json` 输出。

## 4. CNB 流水线（`.cnb.yml` 的 `v3.1.0: tag_push`）

顺序：toolchain → tag-guard → engine-verification → adoption-e2e → build-assets →
verify-assets-local → snapshot-expected → create-pre-release（`preRelease=true, latest=false`）→
upload → download → verify-uploaded → promote-gate-pre → promote-ga →
reupload → redownload → verify-after-promote → promote-gate-post → mark-latest →
redownload → verify-after-latest → promote-gate-latest → release-verification-report。

失败安全：

- 任一阶段非零退出，后续阶段不执行；平台侧最多停留在 pre-release；
- 未完成附件核对不得 promote；未完成 promote 后核对不得标记 `latest`；
- 所有 `git:release` 使用 `overlying: true`（更新语义），**禁止**使用会删除并重建
  Release 的默认 `overlying: false`；
- 不依据“命令退出码为 0”推断附件仍在，一律重新下载核对 SHA256 与数量。

## 5. 平台不确定项（必须在独立 RC 仓库验证）

以下行为在本地无法验证，正式发布前必须在**真正独立的 CNB 测试仓库**用 RC Tag 验证，
不得用正式仓库的 `v3.1.0` Tag 做实验：

1. `git:release` 的 `overlying: true` 在 Release 不存在时是否创建（本流程按“创建或更新”设计）；
2. `overlying: true` 更新后是否保留已有附件（流程对此不信任，promote 后重新上传并重新下载核对）；
3. `cnbcool/attachments` 的 `DOWNLOAD` 是否接受 `./dist/agent-workflow-src-*.tar.gz` 这类 glob；
   若只接受精确文件名，需把源码包资产名固定为版本号形式，或改用平台 OpenAPI；
4. `tag_push` 事件下 `CNB_BRANCH` 是否等于 Tag 名（guard 会强制校验，不等则直接失败）；
5. `golang:1.24` 镜像是否自带 `python3`（缺失时流水线会 `apt-get install python3`）。

RC-C 未在独立仓库执行前，上述项一律标记 `NOT_VERIFIED`，不得声称已验证。

## 6. 禁止事项

- 不在本目录或任何源码提交中存放 Release 资产 / 二进制；
- 不创建正式 `v3.1.0` Tag、不创建正式 GA Release、不把 Release 标记为 latest；
- 不修改 `main`，不 Force Push；
- 不上传 Docker Engine 镜像。
