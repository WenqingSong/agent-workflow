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
- `Idempotency-Key`（请求头，可选）：同键同载荷返回同一分录（HTTP 200，`replayed: true`）；同键不同载荷返回 409

金额一律使用整数分，不使用浮点。

## 错误语义

| 状态码 | code | 场景 |
| --- | --- | --- |
| 400 | `invalid_body` / `invalid_<field>` | JSON 非法、未知字段、参数越界 |
| 404 | `account_not_found` | 账户不存在 |
| 404 | `not_found` | 未知路径 |
| 409 | `insufficient_funds` | 余额不足（不写分录） |
| 409 | `idempotency_conflict` | 同键不同载荷（不写分录） |
| 413 | `body_too_large` | 请求体超过 1 MiB |
| 500 | `internal_error` | 持久化或内部错误 |

## 持久化

- append-only JSONL，每行 `<payload-json> <sha256>\n`；
- 每次写入后 `fsync`，文件首次创建时同步目录；
- 启动时重放：中间行损坏（JSON 非法、校验和不匹配、seq 不连续）→ fail closed，非 0 退出且不修改文件；
- 仅**最后一行**不完整时视为崩溃残留，截断到最后一条完整记录并打印警告。

## 验证

```bash
GOWORK=off go vet ./...
GOWORK=off go test ./... -count=1
GOWORK=off go test -race ./... -count=1
```

## 限制

- 单进程使用；不支持多个进程写同一数据文件（无文件锁）。
- 无认证、无 TLS、无多币种、无复式记账。
- 日志不压缩；幂等键索引由全量重放重建。
