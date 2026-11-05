# ledgerd

个人账本 HTTP 服务。Go 标准库实现，无第三方依赖。

## 运行

```bash
GOWORK=off go run ./cmd/ledgerd --addr 127.0.0.1:8080 --data ledger.jsonl
```

启动后 stdout 输出 `LEDGERD_ADDR=<实际监听地址>`，便于脚本发现端口。
收到 SIGTERM 后在 5 秒内完成在途请求并退出。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/accounts` | 创建账户，`{"name":"..."}` → 201 |
| `GET` | `/accounts/{id}` | 账户与余额 |
| `GET` | `/accounts/{id}/balance` | 余额 |
| `POST` | `/accounts/{id}/entries` | 记一笔分录 |
| `GET` | `/accounts/{id}/entries?limit=&after=` | 分页查询流水 |
| `GET` | `/healthz` | 健康检查 |

记分录请求：

```json
{"kind": "income", "amount_cents": 10000, "memo": "工资"}
```

- `kind`：`income` 或 `expense`
- `amount_cents`：正整数，最小货币单位（分），上限 `1e11`
- `Idempotency-Key`（请求头，可选）：1..128 个字符，不得含首尾空白；同键同载荷返回同一分录（HTTP 200，`replayed: true`）；同键不同载荷返回 409；提供空值返回 400
- 所有带 body 的请求必须声明 `Content-Type: application/json`（否则 415）

金额一律使用整数分，不使用浮点。

## 错误语义

| 状态码 | code | 场景 |
| --- | --- | --- |
| 400 | `invalid_body` / `invalid_<field>` | JSON 非法、未知字段、参数越界 |
| 404 | `account_not_found` | 账户不存在 |
| 404 | `not_found` | 未知路径 |
| 409 | `insufficient_funds` | 余额不足（不写分录） |
| 409 | `idempotency_conflict` | 同键不同载荷（不写分录） |
| 405 | `method_not_allowed` | 路径存在但方法不支持（响应含 `Allow`） |
| 413 | `body_too_large` | 请求体超过 1 MiB |
| 415 | `unsupported_media_type` | `Content-Type` 不是 `application/json` |
| 500 | `internal_error` | 持久化或内部错误 |

## 持久化

- append-only JSONL，每行 `<payload-json> <sha256>\n`，记录含 `version` 字段（未知版本拒绝启动）；
- 每次写入后 `fsync`，文件首次创建时同步目录；写失败会回滚到写入前偏移，回滚失败则拒绝后续写入；
- 启动时重放：损坏（JSON 非法、校验和齐全但不匹配、seq 不连续、版本未知）→ fail closed，非 0 退出且不修改文件；
- 仅当**最后一行缺少校验和或校验和长度不足**（撕裂写的特征）时才截断到最后一条完整记录并打印警告；
- 威胁模型：行校验和用于发现意外损坏与位翻转，**不防**有写权限的恶意编辑，也不防按行删除尾部记录。
- 格式版本：当前为 `version: 1`。没有 `version` 字段的早期日志（e64ed0f 之前的开发构建产物）不兼容，会以 `unsupported record version 0` 拒绝启动，需删除或手工迁移。

## 验证

```bash
GOWORK=off go vet ./...
GOWORK=off go test ./... -count=1
GOWORK=off go test -race ./... -count=1
```

## 限制

- 单进程使用；不支持多个进程写同一数据文件（无文件锁）。
- 无认证、无 TLS、无多币种、无复式记账。
- 日志不压缩；幂等键索引由全量重放重建；账户、分录与幂等索引全部驻留内存，内存随账本线性增长。
- 写入在同一把锁内串行 fsync，读请求会在 fsync 期间排队；分页按 seq 线性扫描，复杂度 O(账本大小)。
- SIGTERM 后 5 秒内未完成在途请求时强制关闭，进程以非 0 退出码结束。
- `kill -9` 后新进程可读到已确认分录，但该现象本身不构成 fsync 的证明（页缓存仍在）；落盘正确性依赖 `Write → Sync → 返回` 的代码路径。
