# Findings — V3.4.0

## Review Target

- Commit: 见 `state.yaml` 的 `review.target`（审查时的 HEAD，完整 SHA 不在 Markdown 中复制）
- Base: `main` @ `6c805dab8a5656bccbc7d34abac48222d7abe203`（v3.3.0）
- 变更规模：25 files changed, 1155 insertions(+), 493 deletions(-)

## 独立验证（本轮真实执行）

| 检查 | 结果 |
| --- | --- |
| `GOWORK=off go -C .agent/engine test -count=1 ./...` | PASS（2 包 ok） |
| `GOWORK=off go -C .agent/engine vet ./...` | PASS |
| `GOWORK=off go -C .agent/engine build ./...` | PASS |
| `git diff --stat main..HEAD -- .agent/engine` | 0 行（AC-007） |
| `grep -c "^func (v \*Validator) Gate" gates.go` | 5（未新增/未改名） |
| `test_handoff_contract.py` | 21 tests OK |
| `test_cold_start.py` | 10 tests OK |
| `test_taskbuilder_autonomy.py` | 18 tests OK |
| `test_route_acceptance.py` | 10 tests OK |
| `test_git_lifecycle.py` | 32 tests OK（含新增 GL-14/15/16） |
| `test_workflow_adoption.py` | 32 tests OK |
| `test_release_scripts.py` | 59 tests OK |

合计 182 个 Python 测试 + 2 个 Go 包通过。

## AC 结论

| AC | 结论 | 证据 |
| --- | --- | --- |
| AC-001 | PASS | Directive §2.1 启动串 + 五步 + 开工声明；七个角色输入段均给出启动串；`test_cold_start` 覆盖 |
| AC-002 | PASS | 契约 §2.2 四字段；§8 矩阵 7 列；七个 `# handoff-exits` 与矩阵逐项相等；无 `HANDOFF_PROMPT` 必填 |
| AC-003 | PASS | Directive §2.3 覆盖七个角色；`finding_ids` 改为 `findings.md` OPEN 项；冲突 → `BLOCKED` |
| AC-004 | PASS | `UPDATE_COMPLETE` 两条出口（CODER / ANALYST）；`UPDATE_COMPLETE → CLEANER` 经 `paths.go` + `gates.go` 核实不成立，已在契约、Task、Prompt 三处一致声明 |
| AC-005 | PASS | 推导表覆盖五 Gate 与 state 枚举；`RETURN_PENDING` 标注不可机器推导并给 `BLOCKED` 兜底 |
| AC-006 | PASS | 失败落盘载体固定；`loop-ledger.md` 增加 `handoff_target` / `receiver_action`；GL-15 证明写入后 CLEAN 仍有效 |
| AC-007 | PASS | engine diff 为空；五 Gate 名称与语义未变；schema 未变 |
| AC-008 | PASS | 静态 21 + 10 项；行为 GL-14（冷启动事实）、GL-15（失败恢复）、GL-16（task.md 变更使 CLEAN 失效） |
| AC-009 | PASS | VERSION/PACKAGE_MANIFEST/README/.agent/README/design/cnb/release notes 均为 3.4.0 |
| AC-010 | PASS（有限） | 独立只读 Session 仅凭角色 + Task 路径推导出 `REVIEW_PENDING` / `initial_review` / `cleaner-start`，并独立发现 Task↔Contract↔实现不一致（已据此修订 A1''）。该 Session 无写权限与 Gate 二进制，未运行 Gate；本文的正式审查由具备写权限的 Session 完成 |

## Findings

### CLEAN-001（P2，CONSTRAINT，Status: CLOSED）

**现象**：本仓库作为框架源码分发包，`.agent/adoption/workflow_adoption.py` 对 tracked 的 `.agent/tasks/**` fail-closed（`SOURCE_INVALID`），而 `contract.target` 的 R-4a / `contractValid` 又要求该提交的树中存在 `state.yaml` 与 `contract.md`。两者同时成立时，框架仓库无法既跟踪自身 Task、又产出可分发快照。

**证据**：`workflow_adoption.py` 的 `NEVER_PACKAGE_PREFIXES` 与 `classify_path`；`route.go` 的 `routeApprovalAgrees`；本轮 `dist` 相关测试在任务目录被跟踪时 32 + 47 个用例失败、移出后全部通过。

**处置**：Task 证据在 Contract 绑定提交（A1''）中保留，随后从版本库移出并本地排除；证据链仍可通过 `git show <A1'':contract.md>` 复核。这是**已披露的环境约束**，不改变宿主项目的正常用法（宿主项目仍应提交 Task）。是否长期允许框架仓库跟踪自身 Task，属 Owner 决策项。

### CLEAN-002（P3，Status: CLOSED）

`test_taskbuilder_autonomy.py` 保留未被使用的 `RELEASE_330` 常量。无行为影响，后续清理即可。

### CLEAN-003（P3，Status: CLOSED）

Directive §2.2 中 `REVIEW_PENDING` 行的 actor 写作 `CLEANER（ARCHITECT → OWNER_GATE V1）`，两种路线的执行者合并在一个单元格。语义正确但可读性一般；不影响推导结果。

## Result

CLEAN

- 无阻塞 Finding；
- 三个 P2/P3 均已核实并处置或披露；
- 静态与行为证据均可从当前 Commit 复现；
- 未修改 `review.target` 指向的历史。
