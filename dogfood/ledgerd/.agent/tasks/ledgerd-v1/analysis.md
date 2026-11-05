# Analysis — ledgerd

> 由 Lead 誊录两份并行 ANALYST 输出。执行者：只读 Subagent（工具权限：读文件、检索；**无 shell、无写权限**）。
> 两份分析均未落盘，本文件是 Lead 的誊录，不是 Subagent 直接写入。

## A. 事实基线

| 事实 | 出处 |
| --- | --- |
| `dogfood/ledgerd/` 下只有任务记录，无任何 `.go` 与 `go.mod` | 全仓库检索 0 命中 |
| CI 已硬编码 dogfood 的 `vet/test/-race/build` | `.cnb.yml` 分支与 Tag 阶段 |
| `check` 要求 dogfood 存在 `go.mod` 与六份记录 | `.agent/adoption/workflow_adoption.py` |

结论：实现之前，框架 CI 必然失败；这是必须先消除的事实型缺口。

## B. 需求拆解（节选，完整 14 项见 Subagent 原文）

- B-1 创建账户 → 201 + GET 余额为 0
- B-2 收入/支出 → 余额 = 分录聚合
- B-6 错误语义：400 / 404 / 409
- B-8 请求体上限 → 413，且服务之后仍可用
- B-9 持久化：正常退出与 `kill -9` 后重启都不丢已确认数据
- B-10 损坏 fail closed
- B-11 并发不超支：成功笔数 × 金额 + 最终余额 = 初始余额
- B-12 SIGTERM 完成在途请求并退出

## C. 关键风险（Lead 采纳）

| ID | 风险 | 采纳的应对 |
| --- | --- | --- |
| R1 | `-race` 通过 ≠ 并发正确；丢失更新可在 race 全绿时存在 | 并发测试断言余额不变量与成功笔数，不只看 race |
| R2 | 持久化假通过：未 fsync 或同进程重开 | 进程级集成测试：SIGTERM 与 `kill -9` 后重启新进程 |
| P3 | 崩溃尾部半行与「一律 fail closed」冲突 | D-4：只容忍**末行**不完整；中间损坏一律 fail closed |
| P4 | 合法 JSON 被静默篡改无法发现 | D-5：每行加 SHA-256 校验和 |
| E2 | 413 拦截太晚导致内存被打爆 | 使用 `http.MaxBytesReader`，并测试三种超限输入 |
| E5 | 幂等键只存内存，重启后重复记账 | D-3：幂等键写入日志，重放生效 |

## D. 分析指出的 AC 缺口（Lead 处理）

| 缺口 | 处理 |
| --- | --- |
| AC-1/2/3 缺少执行目录与 `GOWORK` | 澄清：命令在 `dogfood/ledgerd` 执行；CI 使用 `GOWORK=off go -C dogfood/ledgerd ...`，语义不变 |
| AC-5 未定义「重启」 | 澄清：必须是新进程，含 `kill -9` 场景 |
| AC-6 幂等语义不完整 | 决策 D-3 明确规定键、状态码、冲突、跨重启 |
| AC-7 无上限数值 | 决策 D-6：1 MiB |
| AC-8 无超时 | 决策 D-6：5 秒 |
| 损坏 fail closed 无 AC | 新增 AC-9 验证既有约束（不是扩大范围） |
