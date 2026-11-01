# Owner Decision — V3.4.0

## 状态

DECISION: PENDING（OwnerGate 已准备好决策材料，等待 Owner 明确回答；本文件不预设答案）

- Review Target：`state.review.target`（CLEAN 绑定的实现快照，见 `state.yaml`）
- Cleaner 结论：CLEAN（无阻塞 Finding；见 `findings.md`）
- 入口 Gate：`owner-gate-start` PASS（本轮真实执行）
- 交付形态：框架源码包 3.4.0（`.agent/VERSION`），五个 Gate / `state.yaml` schema / Engine 零改动

## 请 Owner 决定（最小问题）

**Q1（必需的接受/拒绝）**：是否接受当前 Review Target 的 V3.4.0 实现？

- `ACCEPT`：进入 Deliverer 交付验证 → `merge-ready` → Owner 集成 → 打 Tag `v3.4.0` 并推送发布。
- `REJECT`：请给出退回对象与理由（实现缺陷 → Coder；结构/设计 → Analyst；任务目标变化 → TaskBuilder）。

**Q2（披露项，可与 Q1 一并回答）**：框架源码仓库是否允许长期跟踪自身的 `.agent/tasks/**`？

- 现状：分发包工具对 tracked Task 数据 fail-closed，因此本任务在 Contract 绑定提交后把 Task 移出版本库（本地保留）。证据链可用 `git show <A1'':contract.md>` 复核，但全新 clone 看不到 Task。
- 选项：(a) 保持现状（框架仓库不跟踪 Task，宿主项目照常跟踪）；(b) 调整打包规则，允许框架仓库跟踪自身 Task（需改 `workflow_adoption.py` 与其测试，超出本次范围）。

## 证据摘要

| 项 | 结果 |
| --- | --- |
| 变更规模 | 25 files changed, +1155 / −493 |
| Engine 改动 | 0 行；5 个 Gate 名称与语义未变；schema 仍为 3 |
| Python 测试 | 182 passed（含新增 `test_cold_start.py` 10 项、GL-14/15/16） |
| Go | `test` / `vet` / `build` 全部通过 |
| 独立 Session 冷启动 | 仅给角色与 Task 路径，正确推导 `REVIEW_PENDING` / `initial_review` / `cleaner-start`，并独立发现 Task↔Contract↔实现不一致（已据此重建 A1''） |
| Gate | `coder-start` PASS → `cleaner-start` PASS → `owner-gate-start` PASS |

## 尚待执行（ACCEPT 之后）

1. Deliverer 交付验收（`delivery-start` → 构建源码包 → `merge-ready`）；
2. Owner 集成到 `main`；
3. Tag `v3.4.0` 推送、CNB 流水线、远端 Commit/Tag/资产核验；
4. 发布后核验安装入口包含 V3.4.0 全部修改。
