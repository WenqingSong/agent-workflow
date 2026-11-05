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
| `GET` | `/accounts/{id}/entries?limit=&after=` | 分页查询流水；`limit` 1..500（默认 50），`after` 为上一页返回的 `next_cursor`；仅当确实存在后续记录时返回 `next_cursor` |
| `GET` | `/healthz` | 健康检查 |

所有 `GET` 路由同时支持 `HEAD`（只看状态码与头，不返回 body）；方法不匹配时返回 405，`Allow` 头列出允许的方法（GET 路由同时列出 `HEAD`）。

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
| 400 | `invalid_query` | `limit` / `after` 不合法 |
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
- 启动时按**末行是否以换行终止**区分崩溃残留与真实损坏：
  - 已终止的行损坏（校验和不匹配、JSON 非法、`seq` 不连续、版本未知、空行）→ fail closed，非 0 退出且不修改文件；
  - 未终止的末行解码失败 → 视为撕裂写，截断到最后一条完整记录并打印 `torn_tail` 警告；
  - 未终止的末行解码成功 → 补写换行并打印 `repaired_newline` 警告，避免后续追加与上一行粘连；
- 边界：未终止末行中的**任何**损坏都会被当作撕裂写截断（包括校验和齐全但不匹配）；这是为保留崩溃恢复能力而接受的取舍，不提供「拒绝启动」保护，但不会把损坏行当作有效记录；
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
