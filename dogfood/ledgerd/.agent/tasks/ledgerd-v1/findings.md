# Findings — ledgerd

## Review Target

64981e312e12eae7f05697287109aa1794e39f12（第七轮复审对象）

审查历史：

| 轮次 | 对象 | 结论 | 审查方式 |
| --- | --- | --- | --- |
| 1 | 36957cb | CHANGES_REQUIRED（1 BLOCKER + 1 MAJOR + 12 MINOR） | 只读 Subagent，静态审查，无 shell |
| 2 | e64ed0f | CLEAN（0 BLOCKER / 0 MAJOR / 12 MINOR） | 只读 Subagent，静态审查，无 shell |
| 3 | bd19c3f | CHANGES_REQUIRED（0 BLOCKER / 2 MAJOR / 5 MINOR） | 只读 Subagent，静态审查，无 shell |
| 4 | 408e662 | CHANGES_REQUIRED（0 BLOCKER / 2 MAJOR / 6 MINOR） | 只读 Subagent，静态审查，无 shell |
| 5 | 04b3f54 | CHANGES_REQUIRED（0 BLOCKER / 3 MAJOR / 4 MINOR） | 只读 Subagent，静态审查，无 shell |
| 6 | b4b777e | CHANGES_REQUIRED（0 BLOCKER / 2 MAJOR / 2 MINOR） | 只读 Subagent，静态审查，无 shell |
| 7 | 64981e3 | CLEAN（0 BLOCKER / 0 MAJOR；2 MINOR + 1 INFO） | 只读 Subagent，静态审查，无 shell |

## Review Conclusion

- 结论: CLEAN（第七轮，64981e3）
- 审查者: CLEANER（只读 Subagent，未参与实现）
- 时间: 2026-11-05T09:20:00Z
- 说明: K1-K4 全部闭环；正常文件、尾部空行、撕裂尾、补换行四条路径无回归。附前置条件 L2（`.gitignore` 使 `cmd/ledgerd/` 未被跟踪），已在交付提交 df179cc 修复，并由干净克隆（20 个文件、vet/test/-race 全通过）机械验证。

---

## 第一轮（对象 36957cb）

### F1 末行截断策略过宽：会静默丢弃完整记录，单行账本被清空
- Severity: BLOCKER
- Status: FIXED
- Category: 持久化 / 数据丢失
- Evidence: `journal.go` 对所有末行解码错误一律截断；未区分「缺校验和」与「校验和不匹配」。
- Impact: 完整记录被篡改或位翻转时被删除；单行账本被截断为 0 字节。
- Required fix: 区分不完整与真实损坏。
- Resolution: e64ed0f（后续在 408e662 改为按行终止符判定，见 H1）

### F2 journal.Append 失败后不回滚文件状态
- Severity: MAJOR
- Status: FIXED
- Category: 持久化 / 错误处理
- Evidence: 写失败后不截断、不标记不可用，下次写入复用 seq。
- Impact: 半行粘连或 seq 重复导致永久 `ErrCorrupt`。
- Required fix: 写前记录偏移，失败回滚；回滚失败拒绝后续写入。
- Resolution: e64ed0f；回滚成功路径测试在 408e662（H3）

### F3 kill -9 用例不能证明 fsync
- Severity: MINOR
- Status: FIXED
- Category: 测试有效性
- Resolution: e64ed0f（注释与 README 降级为「跨进程可见」）

### F4 单锁覆盖 fsync；SIGTERM 超时退出码未写入记录
- Severity: MINOR
- Status: FIXED
- Category: 文档
- Resolution: e64ed0f（README「限制」）

### F5 方法不匹配一律 404
- Severity: MINOR
- Status: FIXED
- Category: HTTP 语义
- Resolution: e64ed0f（405 + Allow，含 HEAD）

### F6 不校验 Content-Type；空 body 文案误导
- Severity: MINOR
- Status: FIXED
- Category: HTTP 语义
- Resolution: e64ed0f（415；空 body 单独报错）

### F7 空幂等键未拒绝；键长按字节
- Severity: MINOR
- Status: FIXED
- Category: 错误语义
- Resolution: e64ed0f（空键 400；按 rune；错误码在 bd19c3f 统一）

### F8 幂等跨重启只在同进程内验证
- Severity: MINOR
- Status: FIXED
- Category: 测试覆盖
- Resolution: e64ed0f（`TestIdempotencySurvivesRestart`）

### F9 并发测试绕开真实 journal
- Severity: MINOR
- Status: FIXED
- Category: 测试覆盖
- Resolution: e64ed0f（`TestConcurrentPostsWithRealJournal`）

### F10 记录中的量化声明无法核实
- Severity: MINOR
- Status: FIXED
- Category: 记录一致性
- Resolution: e64ed0f + 本文件；计划中的数字已按实际运行结果更新

### F11 plan.md 的 Next Action 陈旧
- Severity: MINOR
- Status: FIXED
- Category: 记录一致性
- Resolution: 每轮更新；第三轮复发见 H7

### F12 全量内存索引与 O(n) 分页限制未写全
- Severity: MINOR
- Status: FIXED
- Category: 文档
- Resolution: e64ed0f（README「限制」）

### F13 无格式版本字段
- Severity: MINOR
- Status: FIXED
- Category: 持久化 / 前向兼容
- Resolution: e64ed0f（`version` 字段 + 未知版本拒绝）

### F14 writeJSON 忽略编码错误
- Severity: MINOR
- Status: FIXED
- Category: 可观测性
- Resolution: e64ed0f；logger 归属在 408e662 修正（H4）

---

## 第二轮（对象 e64ed0f，结论 CLEAN，12 MINOR）

### G1 数量声明仍不可复核、修复交接无测试证据
- Severity: MINOR / Status: FIXED
- Resolution: 记录改为引用可复核的运行结果。`delivery.md` 由 DELIVERER 在 plan.md 第 10 步填写（当前：未填写，全部 AC 未验证）。

### G2 decisions D4 与实现冲突
- Severity: MINOR / Status: FIXED
- Resolution: decisions.md 追加 D4-rev1。

### G3 README 未覆盖 405/415 与请求约束
- Severity: MINOR / Status: FIXED
- Resolution: README 补齐错误码、Content-Type、幂等键与分页语义。

### G4 撕裂尾判定可能假阳性
- Severity: MINOR / Status: FIXED
- Resolution: bd19c3f 引入 JSON 结构启发式；408e662 改为按行终止符判定（更彻底）。

### G5 rollback 忽略 Sync 错误
- Severity: MINOR / Status: FIXED
- Resolution: bd19c3f（Sync 失败置 broken）。

### G6 F2 回滚路径无测试
- Severity: MINOR / Status: FIXED
- Resolution: 408e662（`TestRollbackKeepsJournalUsable`，注入 afterWrite 失败）。

### G7 413 只覆盖声明长度路径
- Severity: MINOR / Status: FIXED
- Resolution: bd19c3f（无 Content-Length 的 chunked 413 用例）。

### G8 编码失败绕过注入 logger
- Severity: MINOR / Status: FIXED
- Resolution: bd19c3f 改用注入 logger；408e662 改为只读 `logger()` 兜底（H4）。

### G9 HEAD 未验证
- Severity: MINOR / Status: FIXED
- Resolution: 408e662（healthz、accounts、balance、entries 四条 HEAD 用例）。

### G10 旧日志（无 version）无法启动缺少说明
- Severity: MINOR / Status: FIXED
- Resolution: bd19c3f（README 说明不兼容）。

### G11 幂等错误码命名不一致
- Severity: MINOR / Status: FIXED
- Resolution: bd19c3f（统一为 `invalid_idempotency_key`）。

### G12 满页但末页仍返回游标
- Severity: MINOR / Status: FIXED
- Resolution: bd19c3f（多取一条判断 hasMore）。

---

## 第三轮（对象 bd19c3f，结论 CHANGES_REQUIRED）

### H1 结构位翻转仍可清空末行/单行账本
- Severity: MAJOR
- Status: FIXED
- Category: 持久化 / 正确性
- Evidence: `json.Valid` 反推撕裂写，无法区分「截断前缀」与「完整写入后被破坏」。
- Impact: 单行账本被截断为 0 字节，违反 fail closed 约束。
- Required fix: 以「末行是否以换行终止」判定撕裂写。
- Resolution: 408e662。`load` 记录 `terminated`；未终止末行解码失败才截断；已终止行损坏一律 `ErrCorrupt`。新增 `TestStructuralCorruptionOnTerminatedLineFailsClosed`。

### H2 完整但缺终止换行的末行会导致后续追加粘连
- Severity: MINOR
- Status: FIXED
- Category: 持久化
- Resolution: 408e662（自动补换行并 Sync；`TestMissingTrailingNewlineIsRepaired`）。

### H3 回滚路径无测试
- Severity: MINOR
- Status: FIXED
- Category: 测试有效性
- Resolution: 408e662（`afterWrite` 测试钩子 + `TestRollbackKeepsJournalUsable`）。

### H4 writeJSON nil 兜底写共享字段，serviceError 无保护
- Severity: MINOR
- Status: FIXED
- Category: 并发 / 可维护性
- Resolution: 408e662（只读 `logger()`，两处统一使用）。

### H5 HEAD 仅覆盖 healthz
- Severity: MINOR
- Status: FIXED
- Category: 测试覆盖
- Resolution: 408e662（四条 GET 路由均测 HEAD）。

### H6 文档与常量不一致
- Severity: MINOR
- Status: FIXED
- Category: 文档
- Resolution: 408e662（`invalid_query` 入表、分页语义、Allow 含 HEAD、文案引用常量）。

### H7 记录未反映第二/三轮，G1/G6 无主
- Severity: MAJOR
- Status: FIXED
- Category: 记录一致性
- Resolution: 本文件补录第二/三轮全部条目与结论；`handoff.md` 补齐三轮回交接；`plan.md` 刷新。`delivery.md` 由 DELIVERER 在 plan.md 第 10 步填写（当前状态：待填写）。

---

## 第四轮（对象 408e662，结论 CHANGES_REQUIRED）

### I1 README 与决策记录仍描述旧截断判据
- Severity: MAJOR / Status: FIXED
- Category: 文档 / 记录一致性
- Evidence: README 与 D4 仍写「校验和不匹配即 fail closed」，而代码对未终止末行的任何解码失败都截断。
- Resolution: 04b3f54（README、包注释按行终止符重写；decisions 追加 D4-rev2 撤销 D4-rev1）。

### I2 findings 声称 delivery.md 已填写，实际为空
- Severity: MAJOR / Status: FIXED
- Category: 记录一致性
- Resolution: b4b777e 更正 H7 一处；G1/I8 两处同类表述在本轮提交更正（见 K2）。

### I3 单行+无换行+损坏会清空，且行为无测试
- Severity: MINOR / Status: FIXED
- Resolution: 04b3f54（`TestSingleUnterminatedCorruptLineIsTruncated`；边界写入 README 与 D4-rev2）。

### I4 中间空行被静默忽略；告警行号非物理行号
- Severity: MINOR / Status: FIXED
- Resolution: 04b3f54（中间空行 `ErrCorrupt`；`Warning.Line` 用物理行号；新增 `TestBlankLineFailsClosed`）。

### I5 缺少「校验和正确但 JSON 非法」的用例；注释与实际分支不符
- Severity: MINOR / Status: FIXED
- Resolution: 04b3f54（`TestChecksumValidButInvalidJSON` 终止/未终止两条；J4 进一步修正撕裂尾用例注释）。

### I6 Allow 未断言 HEAD；README 未说明 HEAD
- Severity: MINOR / Status: FIXED
- Resolution: b4b777e（Allow 断言含 HEAD；HEAD /accounts 断言 405 且不含 HEAD；README 增加 HEAD 说明）。

### I7 告警文案不能区分「补换行」与「撕裂尾」
- Severity: MINOR / Status: FIXED
- Resolution: 04b3f54（`Warning.Kind`；main 按 Kind 打印不同文案）。

### I8 记录缺逐字验证输出与测试计数
- Severity: MINOR / Status: OPEN（待 DELIVERER 执行）
- Resolution: 由 DELIVERER 在 delivery.md §3 写入真实命令与输出（`go vet` / `go test` / `go test -race`）；当前尚未填写。

---

## 第五轮（对象 04b3f54，结论 CHANGES_REQUIRED）

### J1 尾部多余空行被静默接受，下一次追加后永久无法启动
- Severity: MAJOR / Status: FIXED
- Category: 持久化 / 正确性
- Evidence: `load` 用 `lastNonEmpty` 跳过尾部空段，`"L1\n\n"` 可正常打开；追加后变中间空行 → `ErrCorrupt` 且无修复路径。
- Impact: 一次多余换行即可让账本在下一次写入后永久不可启动。
- Required fix: 只允许末尾换行产生的一个空段；出现额外空段立即 fail closed。
- Resolution: b4b777e。`load` 校验尾部空段；新增 `TestTrailingBlankLineFailsClosed`；README 与包注释同步。

### J2 D4 原文未标注被取代
- Severity: MINOR / Status: FIXED
- Resolution: b4b777e（D4 标题标注已被 D4-rev1/D4-rev2 取代）。

### J3 findings 仍声称 delivery.md 已填写（I2 未修）
- Severity: MAJOR / Status: FIXED
- Resolution: b4b777e（见 I2）。

### J4 撕裂尾用例注释仍按已撤销的长度判据描述
- Severity: MINOR / Status: FIXED
- Resolution: b4b777e（注释改为 D4-rev2 的终止符规则）。

### J5 Allow 未断言 HEAD；README 未说明 HEAD（I6 残留）
- Severity: MINOR / Status: FIXED
- Resolution: b4b777e。

### J6 修复类告警与空行用例断言偏松
- Severity: MINOR / Status: FIXED
- Resolution: b4b777e（补 `WarningRepairedNewline` 与 before/after 字节比对）。

### J7 第四/五轮结论未落盘，冷启动五问不可答
- Severity: MAJOR / Status: FIXED
- Resolution: b4b777e（本文件补录两轮结论；handoff 补两轮交接；plan 刷新）。

---

## 第六轮（对象 b4b777e，结论 CHANGES_REQUIRED）

### K1 只含换行的日志被当作空账本，首次追加后永久损坏
- Severity: MAJOR / Status: FIXED
- Category: 持久化 / 正确性
- Evidence: `lastNonEmpty < 0` 时直接返回空账本；`"\n"` 文件被接受，追加后变中间空行 → 永久 `ErrCorrupt`。
- Required fix: 数据非空但无内容行时 fail closed。
- Resolution: 64981e3（`len(data) > 0` 时 `ErrCorrupt`；3 条 `TestBlankLineOnlyFileFailsClosed` 用例）。

### K2 findings 仍有两处声称 delivery.md 已写入真实运行结果
- Severity: MAJOR / Status: FIXED
- Category: 记录一致性
- Resolution: 64981e3（G1/I8 表述更正；I8 状态改为 `OPEN（待 DELIVERER 执行）`）。

### K3 fail closed 缺少恢复指引；尾部空行错误无行号
- Severity: MINOR / Status: FIXED
- Resolution: 64981e3（README 增加「启动失败时的处置」；错误带行号）。

### K4 同一 commit 的测试计数在 plan 与 handoff 中矛盾
- Severity: MINOR / Status: FIXED
- Resolution: 64981e3（plan.md 引用 handoff 的当时实测，并注明当前树计数）。

---

## 第七轮（对象 64981e3，结论 CLEAN）

### L1 plan.md 的「当前树 33 个顶层 Test」与实际不符
- Severity: MINOR / Status: FIXED
- Evidence: 实际为 35 个以 `Test` 开头的函数（含 `TestMain`）、61 个 RUN。
- Resolution: 记录改为引用实际运行计数（见 delivery.md §3.1）。

### L2 `.gitignore` 的 `ledgerd` 规则使 `cmd/ledgerd/` 从未进入 Git
- Severity: MINOR（若未跟踪则升级 BLOCKER）/ Status: FIXED
- Category: 仓库卫生 / 交付完整性
- Evidence: `git check-ignore -v dogfood/ledgerd/cmd/ledgerd/main.go` 命中 `.gitignore:2:ledgerd`；`git ls-files dogfood/ledgerd/cmd` 为空。
- Impact: 命令入口与 4 个进程级用例不在提交历史中，新克隆无法构建/运行 AC-4/5/8/9 的验证代码。
- Resolution: df179cc（规则改为 `/ledgerd`，补入 `main.go` 与 `process_test.go`）；`git ls-files` 现列出 10 个 Go 文件；干净克隆 vet/test/-race 全通过。

### L3 只含空行的文件被当作空账本（K1）
- Severity: MAJOR / Status: FIXED
- Resolution: 64981e3，第七轮复核通过。

### L4 findings 声称 delivery.md 已写入（K2）
- Severity: MAJOR / Status: FIXED
- Resolution: 64981e3 更正；delivery.md 已由 DELIVERER 填写。

### L5 缺恢复指引（K3）
- Severity: MINOR / Status: FIXED
- Resolution: 64981e3。

### L6 G1 首句表述略超前
- Severity: MINOR / Status: FIXED
- Resolution: delivery.md 已产出具名输出，G1 现指向 delivery.md §3。

### L7 handoff 结尾措辞自相矛盾
- Severity: INFO / Status: FIXED
- Resolution: 改为「代码类 Finding 无 OPEN；I8 由 DELIVERER 执行」。

### L8 记录在对象 commit 之后的 docs 提交中落盘
- Severity: INFO / Status: ACCEPTED
- 说明: 审查对象为代码 commit；记录文件在其后的 docs 提交中落盘，属既定惯例。交付记录绑定代码 commit `df179cc`。
