# Delivery — V3.4.0

## 环境

- 平台：Linux x86-64（`Linux 5.4.241-1-tlinux4-0025.10`），Go 1.24.1，Python 3.11.2
- 仓库：`https://cnb.cool/framework-station/agent-workflow`
- Feature branch / Integration branch：`task/v3.4.0-independent-session-handoff` / `main`

## 绑定（三 SHA）

- `review_target` = `state.review.target` = `1b6fef5478ec4434859d95f342a5884e23ad342e`（Cleaner CLEAN 的目标）
- `feature_head` = 本次 Delivery Evidence 建立时的 feature HEAD
- `develop_base` = `origin/main` = `6c805dab8a5656bccbc7d34abac48222d7abe203`

## 已执行检查

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| `GOWORK=off go -C .agent/engine test -count=1 ./...` | PASS | 2 个包 |
| `GOWORK=off go -C .agent/engine vet ./...` / `build ./...` | PASS | — |
| `test_handoff_contract.py` | PASS | 21 |
| `test_cold_start.py` | PASS | 10 |
| `test_taskbuilder_autonomy.py` | PASS | 18 |
| `test_route_acceptance.py` | PASS | 10 |
| `test_git_lifecycle.py` | PASS | 32（含 GL-14/15/16 真实 Git + Gate） |
| `test_workflow_adoption.py` | PASS | 33（含 tracked Task 自托管用例与 install E2E） |
| `test_release_scripts.py` | PASS | 59 |
| `release.py build --tag v3.4.0` | PASS | 四平台 binary + 源码包 + Release Notes |
| `release.py verify-assets --commit <HEAD> --tag v3.4.0` | PASS | 7 个资产 SHA256 与集合校验 |
| 源码包内容核验 | PASS | `framework_version=3.4.0`；四字段在包内；无 `HANDOFF_PROMPT:`；无 `.agent/tasks/` |

合计 183 个 Python 测试 + 2 个 Go 包通过。

## 关键交付物

- `.agent/specs/WorkflowAgentDirective.md`：启动协议、阶段推导、`mode` 推导、旧输入兼容
- `.agent/specs/WorkflowHandoffContract.md`：四字段契约与反模式
- `.agent/roles/*.md`（7 个）：统一输入与出站
- `.agent/adoption/workflow_adoption.py`：自托管 Task 排除规则（Owner 决策 B）
- `docs/design/agent-workflow.md` §34、`docs/releases/v3.4.0.md`

## 未执行 / 边界

- 真实 LLM 全流程冷启动（新 Session 运行 Gate 并写 Artifact）：未执行；仅完成独立只读 Session 的推导验证。
- macOS / Windows 原生运行：未执行；四平台 binary 中仅 Linux x86-64 为 `runtime` 验证，其余为 `static`。
- CNB 平台发布、附件回读与 `latest` 标记：由 Tag 推送后的流水线执行，本文件不预先声称完成。

## 结果

PASS（待 `merge-ready` 与 Owner 集成）
