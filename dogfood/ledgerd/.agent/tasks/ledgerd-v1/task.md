# Task: ledgerd — Go 个人账本 HTTP 服务

## Goal

用 Workflow V4 实际开发一个可运行的 Go 个人账本 HTTP 服务 `ledgerd`：支持创建账户、记入收入/支出、查询余额与流水、持久化到磁盘并在重启后恢复。交付必须包含单元测试、集成测试与并发验证，作为 V4 的真实 Dogfood 证据。

## Scope

- Go 实现，模块 `ledgerd`，尽可能只使用标准库。
- 领域层：账户、分录、余额、金额精度、错误语义。
- 存储层：append-only JSONL 日志 + 启动重放，写入后 `fsync`。
- HTTP 层：`net/http` 路由、JSON 编解码、请求体大小限制、错误响应。
- 命令入口：`cmd/ledgerd`，支持 `--addr`、`--data`，支持优雅退出。
- 测试：领域单元测试、存储重放测试、HTTP 集成测试、并发写入测试（`-race`）。
- V4 工作记录：六份 Markdown，含独立审查与冷启动验证。

## Out of Scope

- 多用户认证、授权、TLS。
- 复式记账（借贷双方科目）、多币种换算。
- 数据库（SQLite/Postgres）、分布式一致性、跨节点复制。
- Web UI、CLI 客户端、Docker 镜像。

## Constraints

- Go 1.24；`GOWORK=off`；不引入第三方依赖。
- 金额使用整数最小货币单位（分），禁止 float。
- 数据文件损坏时必须 fail closed，不得静默丢弃。
- 单进程内并发安全；跨进程不保证。
- 所有测试必须可在离线环境运行。

## Acceptance Criteria

> 以下命令均在 `dogfood/ledgerd/` 目录执行；CI 等价命令为 `GOWORK=off go -C dogfood/ledgerd ...`（决策 D8）。

- [ ] AC-1：`go vet ./...` 通过。
- [ ] AC-2：`go test ./... -count=1` 通过，包含领域单元测试、存储重放测试与 HTTP 集成测试。
- [ ] AC-3：`go test -race ./... -count=1` 通过；并发写入用例断言「成功笔数 × 金额 + 最终余额 = 初始余额」且余额非负。
- [ ] AC-4：服务可启动，真实 HTTP 请求可创建账户、记收入/支出、查询余额与流水。
- [ ] AC-5：数据写入后以**新进程**重启可恢复账户、分录与余额；`kill -9` 后已返回 2xx 的分录不丢失。
- [ ] AC-6：错误语义：非法输入 400、账户不存在 404、余额不足 409 且不写入、`Idempotency-Key` 重复返回同一分录（200，`replayed: true`）且跨重启有效、同键不同载荷 409 且不写入。
- [ ] AC-7：请求体上限 1 MiB；超限返回 413，且服务在超限请求后仍可正常处理请求。
- [ ] AC-8：收到 SIGTERM 后 5 秒内完成在途请求并以退出码 0 退出。
- [ ] AC-9：数据文件中间行损坏（含合法 JSON 但校验和不匹配）时启动 fail closed：非 0 退出、文件字节不变；仅末行不完整时截断到最后一个完整行并告警。

## Review Baseline

- Branch: v4.0.0
- Base Commit: 453830fd2728eee0d91e48fdcd4d729d2be0fb66
- 说明: dogfood 代码与记录全部位于 `dogfood/ledgerd/`，不修改框架文件

## Owner Authorization

- Owner: worklow-owner（通过本轮任务指令授权）
- 授权范围: 可在 `v4.0.0` 分支创建 dogfood 代码与记录、提交并推送
- 需要额外授权: 正式发布（已由本轮任务明确授权）

## Open Questions

- 无（余额不足默认拒绝，见 decisions.md D-2）
