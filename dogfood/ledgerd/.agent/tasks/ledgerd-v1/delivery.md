# Delivery — ledgerd

## 1. 交付范围

- 分支: `v4.0.0`
- 交付 commit: `df179cc7da0e5b1a4ff3d996fd02518c5a0c753e`
- 审查对象: `64981e312e12eae7f05697287109aa1794e39f12`（第七轮）
- 审查结论: CLEAN（0 BLOCKER / 0 MAJOR；1 项前置条件 L2 已由本 commit 修复并机械验证）
- 代码位置: `dogfood/ledgerd/`（20 个受版本控制的文件，含 10 个 Go 文件）

## 2. 验收标准核对

| # | 验收标准 | 结论 | 证据 |
| --- | --- | --- | --- |
| AC-1 | `go vet ./...` 通过 | PASS | 干净克隆：`go -C dogfood/ledgerd vet ./...` 退出码 0 |
| AC-2 | `go test ./... -count=1` 通过，含单元与集成测试 | PASS | 4 个包全部 `ok`；61 个 RUN（含子测试） |
| AC-3 | `-race` 通过，并发用例断言余额不变量 | PASS | `-race` 4 包全部 `ok`；`TestConcurrentExpensesNeverOverdraw` 断言 `success*amount+balance==100` 且 `success==10` |
| AC-4 | 真实 HTTP 可创建账户、记收支、查余额与流水 | PASS | 见 §3「真实启动与请求」：201/201/200，余额 7500，流水 2 条 |
| AC-5 | 新进程重启恢复；`kill -9` 后已确认分录不丢 | PASS | `TestGracefulShutdownAndRestart`、`TestKillNineKeepsAcknowledgedEntry`；手工重启后余额仍为 7500 |
| AC-6 | 400/404/409/幂等 200 同分录/同键不同载荷 409 | PASS | `TestErrorSemantics` 全部子用例；手工 409 与 413 |
| AC-7 | 1 MiB 上限，超限 413，之后仍可用 | PASS | 声明长度与 chunked 两种 413 用例；超限后 `/healthz` 仍 200 |
| AC-8 | SIGTERM 5 秒内完成在途请求，退出码 0 | PASS | `TestGracefulShutdownAndRestart`；手工 SIGTERM `EXIT=0` |
| AC-9 | 中间损坏 fail closed 且不改文件；仅撕裂末行截断 | PASS | `TestMiddleCorruptionFailsClosed`、`TestStructuralCorruptionOnTerminatedLineFailsClosed`、`TestBlankLineFailsClosed`、`TestBlankLineOnlyFileFailsClosed`、`TestTornTailIsTruncated`；进程级 `TestCorruptJournalFailsClosed` |

## 3. 验证命令与结果

### 3.1 框架级（在干净克隆上执行，证明提交树完整）

```text
$ git clone /workspace /tmp/clone && git -C /tmp/clone checkout df179cc
CLONE_HEAD=df179cc7da0e5b1a4ff3d996fd02518c5a0c753e
$ git ls-files dogfood/ledgerd | wc -l
20
$ GOWORK=off go -C dogfood/ledgerd vet ./...
vet=PASS
$ GOWORK=off go -C dogfood/ledgerd test ./... -count=1
ok  	ledgerd/cmd/ledgerd	0.520s
ok  	ledgerd/internal/httpapi	0.537s
ok  	ledgerd/internal/journal	0.031s
ok  	ledgerd/internal/ledger	0.042s
$ GOWORK=off go -C dogfood/ledgerd test -race ./... -count=1
ok  	ledgerd/cmd/ledgerd	1.416s
ok  	ledgerd/internal/httpapi	1.611s
ok  	ledgerd/internal/journal	1.043s
ok  	ledgerd/internal/ledger	1.052s
$ go test -v ./... -count=1 | grep -c '^=== RUN'
61
```

> 干净克隆同时验证了第七轮 L2：`.gitignore` 原规则 `ledgerd` 曾使 `cmd/ledgerd/` 从未进入 Git；已在 df179cc 改为 `/ledgerd` 并补入 `main.go`、`process_test.go`。`git ls-files` 现在列出 10 个 Go 文件。

### 3.2 真实启动与 HTTP 请求（手工执行）

```text
ADDR=127.0.0.1:39695
--- create ---
{"id":"acc_5eea1b35000672b4","name":"日常","balance_cents":0,"created_at":"2026-11-05T06:22:35.960937378Z"}
--- income (Idempotency-Key: demo-1) ---
{"entry_id":"ent_5150c7c8920afe02","account_id":"acc_5eea1b35000672b4","kind":"income","amount_cents":10000,"balance_after_cents":10000,"memo":"工资",...}
--- expense ---
{"entry_id":"ent_db966f3bf133e6dd","account_id":"acc_5eea1b35000672b4","kind":"expense","amount_cents":2500,"balance_after_cents":7500,"memo":"房租",...}
--- balance ---
{"account_id":"acc_5eea1b35000672b4","balance_cents":7500}
--- entries ---
{"items":[...2 条...],"next_cursor":null}
--- insufficient funds status ---
409
--- oversized body status ---
413
--- SIGTERM ---
EXIT=0
--- restart ---
ADDR2=127.0.0.1:34471
{"account_id":"acc_5eea1b35000672b4","balance_cents":7500}
RESTART_EXIT=0
```

## 4. 未验证项

- **fsync 的物理持久化语义**：进程级 `kill -9` 用例只证明跨进程可见与日志格式自洽；SIGKILL 不清空页缓存，无法证明数据真正落盘。fsync 正确性由代码路径（写 → Sync → 更新内存 → 响应）与审查结论承担，未做断电/故障块设备验证。
- **独立审查为静态只读**：6 轮 CLEANER 审查均由只读 Subagent 完成，**无 shell、未运行 go/git**；测试通过与否始终由 Lead 运行，审查者未亲自复跑（与 CleanerAgent 的「亲自运行」要求存在差距，已如实记录）。
- **AI 会话独立性**：无法证明审查 Subagent 与被审代码作者在平台层是不同主体；只能保证审查 Session 未参与实现。
- **跨项目 Adoption**：ledgerd 是本仓库内的 Dogfood 子项目，未在外部真实项目完成 V4 安装/升级验证。
- **并发性能**：未做吞吐/延迟基准；单锁内 fsync 为已知限制。

## 5. 已知限制

- 单进程使用，无文件锁；多个进程写同一数据文件未定义。
- 无认证、无 TLS、无多币种、无复式记账。
- 全量索引驻留内存；分页为 O(账本大小) 线性扫描。
- 写入在同一把锁内串行 fsync，读请求会在 fsync 期间排队。
- SIGTERM 超时（5 秒）未完成在途请求时以非 0 退出。
- 未终止末行中的任何损坏都会被当作撕裂写截断（D4-rev2 接受的取舍）。
- 日志不压缩；幂等索引由全量重放重建。

## 6. 回滚方式

- 代码：`git revert df179cc`（或回退到 `453830f` 后重新应用框架变更）。
- 数据：ledgerd 数据文件为 append-only JSONL；回滚代码不改变既有日志格式（`version: 1`）。
- 框架：使用 `workflow-adoption rollback --target <host>` 回滚安装事务。
