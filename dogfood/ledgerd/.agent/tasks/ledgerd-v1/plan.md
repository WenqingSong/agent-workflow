# Plan — ledgerd

## Current Status

IN_PROGRESS

## Steps

| # | 步骤 | 负责角色 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| 1 | 建立任务记录与验收标准 | TASK-BUILDER | DONE | commit 453830f |
| 2 | 需求、约束与风险分析（并行两份） | ANALYST | DONE | analysis.md（两份只读 Subagent） |
| 3 | 结构决策与接口契约 | LEAD | DONE | decisions.md D1-D8 |
| 4 | 实现领域层、存储层、HTTP 层与命令入口 | CODER | DONE | commit 36957cb |
| 5 | 单元、集成与并发测试 | CODER | DONE | commit 36957cb（33 个测试） |
| 6 | 独立审查（不得由作者执行） | CLEANER | IN_PROGRESS | 审查对象 36957cb |
| 7 | 修复 Finding 并复验 | LEAD | PENDING | — |
| 8 | 复审至 CLEAN | CLEANER | PENDING | — |
| 9 | 冷启动验证（全新 Session 只读任务目录） | CLEANER | PENDING | — |
| 10 | 交付记录与验收核对 | DELIVERER | PENDING | — |

## Next Action

CODER 在 `dogfood/ledgerd/` 实现：`internal/journal`（带校验和的 JSONL + fsync + 末行截断）、`internal/ledger`（领域校验、幂等、余额不变量）、`internal/httpapi`（路由与错误语义）、`cmd/ledgerd`（监听、优雅退出），并补齐单元/集成/并发测试。

## Blockers

- 无
