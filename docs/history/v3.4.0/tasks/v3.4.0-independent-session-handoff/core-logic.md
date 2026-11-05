# Core Logic — V3.4.0

## CL-001 阶段推导取代「交接说明书」

- 机制：`WorkflowAgentDirective.md` §2.2 把五个 Gate 的入口条件反推成一张表；角色启动后先判 `blocked`，再按 `route / contract / review / owner / delivery` 命中唯一 stage，得到 actor 与入口 Gate。
- 为什么值得 Owner 验证：这是「Agent 通过 Task 协作」的核心。若表与 Gate 语义漂移，新 Session 会走错阶段；为此 `test_cold_start.py` 从 `state.go` 枚举生成覆盖检查，`test_git_lifecycle.py` 用真实 Gate 验证主要行。
- 亲自验证：读 §2.2 表，任选一行，用 `.agent/bin/workflow-check gate <actor 的入口 Gate> <task>` 观察 PASS/FAIL 是否与该行一致。

## CL-002 失败原因落盘且不破坏 CLEAN

- 机制：`FAILURE_HANDOFF` 前必须把原因写入 `findings.md` / `owner-decision.md` / `delivery.md` / `loop-ledger.md`；`loop-ledger.md` 本就在 Review-neutral 与 Delivery-neutral 白名单内，因此「记录失败」与「保持审查结论有效」不冲突。
- 为什么值得 Owner 验证：这是删除 `HANDOFF_PROMPT` 后信息不丢失的唯一保证。
- 亲自验证：GL-15（真实 Git）：写入带 `handoff_target` / `receiver_action` 的账本事件后，`owner-gate-start` 仍 PASS，且 `git show HEAD:<ledger>` 可恢复原因。

## CL-003 `task.md` 变更必然使 CLEAN 失效

- 机制：`paths.go` 的 Review-neutral 白名单不含 `task.md`，因此 Owner 更新任务定义后，旧 CLEAN 在 `owner-gate-start` 必然 FAIL。V3.4 不再允许把这种更新交接给 Cleaner，只能由 Coder 重建 Evidence 或 Analyst 重定 Contract。
- 亲自验证：GL-16（真实 Git）：修改 `task.md` 后 `owner-gate-start` 与 `cleaner-start` 均非 0。
