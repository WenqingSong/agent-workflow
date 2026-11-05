# Decisions — ledgerd

## 已决

### D1 Dogfood 位置

- Decision: 代码与记录放在框架仓库的 `dogfood/ledgerd/`，使用同一个 `v4.0.0` 分支的真实提交历史。
- Decided by: Lead
- Decided at: 2026-11-05T00:00:00Z
- Rationale: 需要可交付、可核验的真实 Git 历史；独立仓库无法随本次交付访问。
- Alternatives: 在 `/tmp` 建独立仓库（无法交付，否决）；新建远程仓库（需要额外创建权限，未授权）。
- Impact: dogfood 记录位于 `dogfood/ledgerd/.agent/tasks/`，与框架根 `.agent/` 隔离。

### D2 余额不足的语义

- Decision: 拒绝支出，返回 409 `insufficient_funds`，不写入分录。
- Decided by: Lead（依据账本业务常识，Owner 未提出相反要求）
- Decided at: 2026-11-05T00:00:00Z
- Rationale: 个人账本默认不允许负余额；允许透支会让「余额」失去意义。
- Alternatives: 允许负余额（需要额外的信用额度模型，超出范围）。
- Impact: HTTP 错误语义；领域层校验。

### D3 幂等语义

- Decision:
  - 幂等键通过请求头 `Idempotency-Key` 传入，作用域为账户；
  - 幂等键与分录一起写入日志，重启后仍生效；
  - 相同键 + 相同载荷：返回同一分录，HTTP 200，`replayed: true`；
  - 相同键 + 不同载荷（kind/amount/memo 任一不同）：409 `idempotency_conflict`，不写入；
  - 键长度 1..128，超长或空视为非法输入（400）。
- Decided by: Lead（ANALYST 提出，属验收标准细化）
- Decided at: 2026-11-05T00:00:00Z
- Rationale: 幂等若不跨重启就不成立；同键不同载荷必须拒绝，否则账目被静默改写。
- Alternatives: 键仅内存有效（重启后重复记账，否决）；同键不同载荷覆盖（数据损坏，否决）。
- Impact: 日志字段、领域校验、HTTP 状态码。

### D4 日志损坏恢复策略

- Decision:
  - 每行带 SHA-256 校验和（`<payload> <sha256>\n`）；
  - **仅末行**不完整（JSON 解析失败或校验和不匹配）视为崩溃残留，截断到最后一个完整行并打印警告；
  - 中间行损坏、未知记录类型、未知版本：fail closed，进程非 0 退出，不修改文件。
- Decided by: Lead（ANALYST 提出）
- Decided at: 2026-11-05T00:00:00Z
- Rationale: 兼顾崩溃恢复与静默丢钱；中间损坏一定是真实损坏。
- Alternatives: 一律 fail closed（一次崩溃后无法启动）；一律截断（静默丢钱，违反 task.md 约束）。
- Impact: 存储格式、启动流程、AC-9。

### D5 金额与溢出

- Decision: 金额为 `int64` 最小货币单位，必须 > 0 且 ≤ 1e11；余额超出 `int64` 预算时拒绝（400），不返回 500。
- Decided by: Lead
- Decided at: 2026-11-05T00:00:00Z
- Rationale: 账本溢出属输入域问题，不应表现为服务器错误。
- Impact: 领域校验。

### D6 运行时边界

- Decision: 请求体上限 1 MiB（`http.MaxBytesReader`，超限 413）；SIGTERM 后 5 秒内完成在途请求，超时强制关闭；服务监听地址通过 `LEDGERD_ADDR=` 日志行输出，便于测试。
- Decided by: Lead
- Decided at: 2026-11-05T00:00:00Z
- Rationale: 可测试、可观测；避免用 sleep 猜启动完成。
- Impact: HTTP 层、命令入口、进程级测试。

### D7 单进程约束

- Decision: 不支持同一数据文件被多个进程同时写入；不实现文件锁，在 `delivery.md` 已知限制中声明。
- Decided by: Lead
- Decided at: 2026-11-05T00:00:00Z
- Rationale: 任务范围明确单进程；文件锁会引入平台差异，收益低于成本。
- Alternatives: `syscall.Flock`（非跨平台，且需要处理陈旧锁）。
- Impact: 已知限制。

### D8 AC 澄清与补充

- Decision:
  - AC-1/2/3 在 `dogfood/ledgerd` 目录执行，CI 等价命令为 `GOWORK=off go -C dogfood/ledgerd ...`；
  - AC-5 的「重启」指新进程，含 `kill -9`；
  - 新增 AC-9 验证 task.md 已存在的「损坏 fail closed」约束。
- Decided by: Lead（澄清与补齐既有约束的验证项，不扩大范围）
- Decided at: 2026-11-05T00:00:00Z
- Rationale: 原 AC 不可机检，会导致「测试通过」无法证明任何事。
- Impact: task.md 的验收标准。

## 待决

暂无
