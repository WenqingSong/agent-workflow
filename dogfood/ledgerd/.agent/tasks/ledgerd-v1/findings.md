# Findings — ledgerd

## Review Target

e64ed0f63a083983bde27d719e2603f378ce0a65（第二轮审查对象）

历史审查对象：

- 36957cbb09064169c05d7c36225ef067c7420ff5（第一轮，CHANGES_REQUIRED）

## Review Conclusion

- 结论: CHANGES_REQUIRED（第一轮 36957cb）
- 审查者: CLEANER（只读 Subagent，未参与实现；静态审查，无 shell）
- 时间: 2026-11-05T02:10:00Z
- 说明: 1 BLOCKER（F1）、1 MAJOR（F2）、12 MINOR。全部已在 e64ed0f 修复，等待第二轮复审。

## Findings

### F1 末行截断策略过宽：会静默丢弃完整记录，单行账本被清空
- Severity: BLOCKER
- Status: FIXED
- Category: 持久化 / 数据丢失
- Evidence: `internal/journal/journal.go` 第一轮对所有末行解码错误一律截断；`decodeLine` 未区分「缺校验和」与「校验和不匹配」。
- Impact: 完整记录被篡改或位翻转时被当作崩溃残留删除；单行账本会被截断为 0 字节，违反「损坏必须 fail closed」。
- Required fix: 区分 `errIncomplete`（缺分隔符 / 校验和长度不足）与其他损坏；后者一律 `ErrCorrupt`。
- Review Target: 36957cb
- Resolution: e64ed0f。新增 `TestCompleteLastLineWithBadChecksumFailsClosed`、`TestSingleCorruptLineIsNotCleared`。

### F2 journal.Append 失败后不回滚文件状态：seq 重用使临时 I/O 错误变成永久损坏
- Severity: MAJOR
- Status: FIXED
- Category: 持久化 / 错误处理
- Evidence: 第一轮 `Append` 在 Write/Sync 失败后既不截断也不标记不可用，下一次写入复用 seq。
- Impact: 半行粘连或 seq 重复导致重放永久 `ErrCorrupt`。
- Required fix: 写入前记录偏移，失败时 `Truncate + Seek + Sync`；回滚失败置 `broken`，后续写入直接失败。
- Review Target: 36957cb
- Resolution: e64ed0f。新增 `TestAppendFailureMarksJournalUnusable`。

### F3 kill -9 用例不能证明 fsync，注释与交接结论强于证据
- Severity: MINOR
- Status: FIXED
- Category: 测试有效性 / 记录一致性
- Evidence: SIGKILL 不清空页缓存，即使不 fsync 测试仍通过。
- Impact: 可能掩盖 fsync 回归。
- Required fix: 修正注释与交付措辞，明确该用例只证明跨进程可见与格式自洽。
- Review Target: 36957cb
- Resolution: e64ed0f（`process_test.go` 注释与 README「限制」）。

### F4 单锁覆盖 fsync；SIGTERM 超时退出码未写入记录
- Severity: MINOR
- Status: FIXED
- Category: 并发性能 / 文档
- Evidence: `service.mu` 覆盖 `store.Append`；`Shutdown` 超时返回 error → 退出码 1。
- Impact: 读被写阻塞；超时语义与文档不一致。
- Required fix: 在 README 限制中写明串行 fsync 与超时退出码。
- Review Target: 36957cb
- Resolution: e64ed0f（README「限制」）。

### F5 方法不匹配一律 404，永不返回 405 / Allow
- Severity: MINOR
- Status: FIXED
- Category: HTTP 语义
- Evidence: `"/"` 兜底拦截了所有方法，绕过 ServeMux 的 405 分支。
- Impact: 客户端无法区分路径不存在与方法不支持。
- Required fix: 为已知路径注册无方法前缀的 405 handler，设置 `Allow`。
- Review Target: 36957cb
- Resolution: e64ed0f。新增 `methodNotAllowed` 与对应测试。

### F6 不校验 Content-Type；空 body 与非法 JSON 共用文案
- Severity: MINOR
- Status: FIXED
- Category: HTTP 语义
- Evidence: `decode` 无 Content-Type 判断，空 body 返回「not valid JSON」。
- Impact: 非 JSON 请求被受理；错误文案误导。
- Required fix: 要求 `application/json`（否则 415）；空 body 单独报 `body is required`。
- Review Target: 36957cb
- Resolution: e64ed0f。新增 415 与空 body 测试。

### F7 D3 的「空幂等键非法」未实现；键长按字节计
- Severity: MINOR
- Status: FIXED
- Category: 记录一致性 / 错误语义
- Evidence: 空 `Idempotency-Key` 被当作无幂等；`len()` 按字节。
- Impact: 显式空键时重复记账。
- Required fix: HTTP 层区分头缺失与空值，空值 400；长度按 rune。
- Review Target: 36957cb
- Resolution: e64ed0f。新增空键测试。

### F8 幂等跨重启只在同进程内验证
- Severity: MINOR
- Status: FIXED
- Category: 测试覆盖
- Evidence: `TestReplayFromFile` 同进程重开文件，进程级测试未覆盖幂等。
- Impact: 重启路径的幂等重建可能回归而不被发现。
- Required fix: 新增进程级跨重启幂等测试。
- Review Target: 36957cb
- Resolution: e64ed0f。`TestIdempotencySurvivesRestart`。

### F9 并发测试绕开真实 journal；写盘失败只覆盖「写前失败」
- Severity: MINOR
- Status: FIXED
- Category: 测试覆盖
- Evidence: 并发用例使用 `memStore`，无 fsync 与文件互斥。
- Impact: 真实落盘路径的并发行为未被覆盖。
- Required fix: 增加真实 `journal.File` 的并发用例并在重放后校验不变量。
- Review Target: 36957cb
- Resolution: e64ed0f。`TestConcurrentPostsWithRealJournal`。

### F10 记录中的量化声明无法核实（33 个测试 / 三种超限输入）
- Severity: MINOR
- Status: FIXED
- Category: 记录一致性
- Evidence: 第一轮统计与记录不符；分析承诺三种超限输入，实际只有一种。
- Impact: 交付记录包含不可复核断言。
- Required fix: 改为可复核表述，并补充超限形态。
- Review Target: 36957cb
- Resolution: e64ed0f。更正为「26 个顶层 Test 函数 + 子测试，`go test -v` 共 44 个 RUN」；补充「声明超大 Content-Length」形态。

### F11 plan.md 的 Next Action 陈旧
- Severity: MINOR
- Status: FIXED
- Category: 记录一致性
- Evidence: 步骤 6 已 IN_PROGRESS，Next Action 仍停在 CODER 实现。
- Impact: 冷启动者会误判下一步。
- Required fix: 每步完成后刷新 Next Action。
- Review Target: 36957cb
- Resolution: e64ed0f（plan.md 已刷新）。

### F12 全量内存索引与 O(n) 分页限制未写全
- Severity: MINOR
- Status: FIXED
- Category: 文档
- Evidence: README 只写了「日志不压缩」。
- Impact: 资源边界未声明。
- Required fix: 写明内存模型与分页复杂度。
- Review Target: 36957cb
- Resolution: e64ed0f（README「限制」）。

### F13 无格式版本字段；校验和威胁模型未说明
- Severity: MINOR
- Status: FIXED
- Category: 持久化 / 前向兼容
- Evidence: `Record` 只有 seq/type/data；未知字段被静默忽略。
- Impact: 旧二进制可能静默误读新日志。
- Required fix: 增加 `version` 字段并拒绝未知版本；文档写清威胁模型。
- Review Target: 36957cb
- Resolution: e64ed0f（`RecordVersion=1`，`decodeLine` 校验版本；README 威胁模型）。

### F14 writeJSON 忽略编码错误
- Severity: MINOR
- Status: FIXED
- Category: 可观测性
- Evidence: `_ = encoder.Encode(payload)`。
- Impact: 截断响应无任何记录。
- Required fix: 记录编码失败。
- Review Target: 36957cb
- Resolution: e64ed0f（`slog.Error`）。
