#!/usr/bin/env python3
"""V3.3 TaskBuilder 自主初始化与任务依赖的一致性检查。

这些检查覆盖 Prompt / Spec / 模板 / 发行文件之间的规则一致性；它们不能证明
LLM 在真实会话中一定遵守 Prompt，也不能替代 Gate。真实 Git 行为由
`test_git_lifecycle.py` 中的 `baseline` / `--preserve-foreign` 用例验证。
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PROMPT = REPO / ".agent/roles/TaskBuilderPrompt.md"
COLLAB = REPO / ".agent/specs/AgentCollaborationSpecification.md"
BOUNDARY = REPO / ".agent/specs/Five-AgentResponsibilityBoundary.md"
GIT = REPO / ".agent/specs/WorkflowGitLifecycle.md"
HANDOFF = REPO / ".agent/specs/WorkflowHandoffContract.md"
ROUTE = REPO / ".agent/specs/WorkflowRouteSelection.md"
DESIGN = REPO / "docs/design/agent-workflow.md"
VERSION = REPO / ".agent/VERSION"
MANIFEST = REPO / "PACKAGE_MANIFEST.md"
CNB = REPO / ".cnb.yml"
README = REPO / "README.md"
AGENT_README = REPO / ".agent/README.md"
RELEASE_330 = REPO / "docs/releases/v3.3.0.md"
RELEASE_310 = REPO / "docs/releases/v3.1.0.md"

CURRENT_RULE_FILES = (PROMPT, COLLAB, BOUNDARY, GIT, HANDOFF, ROUTE, DESIGN, AGENT_README)


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TaskBuilderAutonomyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.prompt = text(PROMPT)
        cls.collab = text(COLLAB)
        cls.boundary = text(BOUNDARY)
        cls.git = text(GIT)
        cls.handoff = text(HANDOFF)
        cls.route = text(ROUTE)
        cls.design = text(DESIGN)

    def test_no_legacy_owner_input_requirements(self) -> None:
        legacy = (
            "Owner 逐项",
            "授权原话",
            "五要素",
            "Owner 必须逐项提供",
            "base_sha: <Owner",
            "完整分支名、起始分支、起始 Commit SHA、Task Slug",
        )
        for path in CURRENT_RULE_FILES:
            body = text(path)
            for phrase in legacy:
                with self.subTest(path=str(path), phrase=phrase):
                    self.assertNotIn(phrase, body)

    def test_owner_input_model_is_minimal(self) -> None:
        for phrase in (
            "owner_request: <任务目标或任务清单>",
            "constraints: <可选",
            "task_slug: <可选",
            "缺失不构成停止条件",
            "不得要求 Owner 补齐",
        ):
            self.assertIn(phrase, self.prompt, phrase)

    def test_authorization_is_narrow(self) -> None:
        for phrase in (
            "即视为授权",
            "该例外**不包含**",
            "Merge、Rebase、Force Push",
            "Push `main` / `master` / 共享集成分支",
            "修改其他任务的交付结果",
        ):
            self.assertIn(phrase, self.prompt, phrase)

    def test_git_baseline_discovery_rules(self) -> None:
        for phrase in (
            "TB-BASE-1",
            "当前检出的分支就是默认起始分支",
            "不得擅自切换到 develop",
            "git rev-parse HEAD",
            "git fetch <remote>",
            "AHEAD",
            "BEHIND",
            "DIVERGED",
            "不得自动 `pull`",
            "不得使用 `git reset --hard`",
            "git clean",
            "git stash",
            "TB-BASE-2",
        ):
            self.assertIn(phrase, self.prompt, phrase)

    def test_detached_head_is_not_guessed(self) -> None:
        self.assertIn("BASE_UNRESOLVED", self.prompt)
        self.assertIn("detached HEAD", self.prompt)
        self.assertIn("候选分支", self.prompt)
        self.assertIn("BASE_UNRESOLVED|OWNER|STOP|none", self.prompt)

    def test_naming_rules(self) -> None:
        for phrase in (
            "TB-NAME-1",
            "task/<task-slug>",
            "check-ref-format",
            "最多尝试 3 次",
            "不得覆盖已有任务目录或分支",
            "owner_update",
        ):
            self.assertIn(phrase, self.prompt, phrase)

    def test_query_classification(self) -> None:
        for phrase in (
            "TB-QUERY-1",
            "第一类：可从事实确定",
            "第二类：可合理假设",
            "第三类：可延后",
            "第四类：会改变业务结果",
            "第五类：不可逆或越权",
            "Decision Checkpoint",
            "Analyst",
        ):
            self.assertIn(phrase, self.prompt, phrase)

    def test_dependency_model(self) -> None:
        for body, name in ((self.prompt, "TaskBuilder Prompt"), (self.collab, "Collaboration Spec")):
            with self.subTest(spec=name):
                self.assertIn("Blocking", body)
                self.assertIn("Reference-only", body)
                self.assertIn("Parallel", body)
        for phrase in (
            "TB-DEP-1",
            "Blocking Dependency",
            "Reference-only Dependency",
            "UPSTREAM_NOT_DELIVERED",
            "不新增 `state.yaml` 字段",
            "不引入依赖图引擎",
        ):
            self.assertIn(phrase, self.prompt, phrase)
        self.assertIn("## Dependencies", self.prompt)
        self.assertIn("## Dependencies", self.collab)

    def test_route_cannot_be_downgraded_for_gate(self) -> None:
        for phrase in (
            "技术栈名称不是路线依据",
            "不得为了通过 `coder-start`",
            "不得为了绕过校验把 `COMPLEX` / `ARCHITECT` 写成 `NORMAL`",
        ):
            self.assertIn(phrase, self.prompt, phrase)
        self.assertIn("不得为了通过 `coder-start`", self.route)

    def test_bootstrap_commit_scope(self) -> None:
        for phrase in (
            "git commit -m \"<Conventional Commit + 中文摘要>\" -- .agent/tasks/<task-slug>",
            "已暂存的外来路径保留但不进入本次提交",
            "BOOTSTRAP_HELD",
        ):
            self.assertIn(phrase, self.prompt, phrase)

    def test_handoff_matrix_and_exit_table_are_aligned(self) -> None:
        for phrase in (
            "TASK_BUILDER|HELD_PRESERVED_CHANGES|BOOTSTRAP_HELD|OWNER|STOP|none|YES",
            "TASK_BUILDER|BASE_UNRESOLVED|BASE_UNRESOLVED|OWNER|STOP|none|YES",
            "TASK_BUILDER|UPDATED_IMPLEMENTATION|UPDATE_COMPLETE|CODER|HANDOFF|coder-start|YES",
            "TASK_BUILDER|UPDATED_ANALYSIS|UPDATE_COMPLETE|ANALYST|HANDOFF|none|YES",
        ):
            self.assertIn(phrase, self.handoff, phrase)
        self.assertIn("BOOTSTRAP_HELD|OWNER|STOP|none", self.prompt)
        self.assertIn("BASE_UNRESOLVED|OWNER|STOP|none", self.prompt)
        self.assertIn("UPDATE_COMPLETE|CODER|HANDOFF|coder-start", self.prompt)
        self.assertIn("UPDATE_COMPLETE|ANALYST|HANDOFF|none", self.prompt)

    def test_owner_update_has_terminal_and_clean_invalidation(self) -> None:
        """owner_update 必须有合法终态，且不得伪造 CLEANER 出口。"""
        self.assertIn("UPDATE_COMPLETE", self.prompt)
        self.assertNotIn("UPDATE_COMPLETE|CLEANER", self.prompt)
        self.assertIn("不存在 `UPDATE_COMPLETE → CLEANER`", self.prompt)
        for phrase in ("Review-neutral 白名单", "`CLEAN` 在 `owner-gate-start` 失效", "`review.status=PENDING`"):
            self.assertIn(phrase, self.prompt, phrase)
        # task.md 不在 Review-neutral 白名单：这是退出路径的机器事实依据。
        paths = (REPO / ".agent/engine/internal/workflow/paths.go").read_text(encoding="utf-8")
        review_neutral = paths.split("reviewNeutralBasenames", 1)[1].split("}", 1)[0]
        self.assertNotIn("task.md", review_neutral, "task.md 进入 Review-neutral 会推翻 CLEAN 失效结论")

    def test_update_input_model_is_minimal(self) -> None:
        section = self.prompt.split("Owner 修改已有任务：", 1)[1].split("没有 Owner 需求时", 1)[0]
        for field in ("mode: owner_update", "task_path:", "owner_decision:"):
            self.assertIn(field, section, field)
        for forbidden in ("branch:", "base_branch:", "base_sha:", "task_slug:"):
            self.assertNotIn(forbidden, section, f"更新输入不得要求 {forbidden}")

    def test_taskbuilder_has_circuit_breaker_actions(self) -> None:
        self.assertIn("### 计数动作（循环熔断）", self.prompt)
        for phrase in ("loop-ledger.md", "handoff_target", "receiver_action", "DANGER"):
            self.assertIn(phrase, self.prompt, phrase)

    def test_git_lifecycle_wiring(self) -> None:
        for phrase in (
            "GL-00A",
            "GL-00B",
            "GL-00C",
            "GL-00D",
            "GL-00E",
            "baseline",
            "--preserve-foreign",
            "pathspec",
        ):
            self.assertIn(phrase, self.git, phrase)
        self.assertIn("当前检出分支", self.collab)
        self.assertIn("自动发现基线", self.boundary)

    def test_design_documents_v33_semantics(self) -> None:
        self.assertIn("## 33. TaskBuilder 自主初始化与任务依赖（V3.3）", self.design)
        self.assertIn("BOOTSTRAP_HELD", self.design)
        self.assertIn("BASELINE", self.design.upper())

    def test_version_and_release_metadata(self) -> None:
        version = text(VERSION).strip()
        self.assertEqual(version, "3.4.0")
        manifest = text(MANIFEST)
        self.assertIn("Workflow V3.4.0", manifest)
        self.assertIn("3.4.0", manifest)
        cnb = text(CNB)
        self.assertIn("v3.4.0:", cnb)
        self.assertIn("RELEASE_TAG: v3.4.0", cnb)
        self.assertIn("workflow-check_3.4.0_linux_amd64", cnb)
        self.assertNotIn("workflow-check_3.3.0_linux_amd64", cnb)
        release = REPO / "docs/releases/v3.4.0.md"
        self.assertTrue(release.is_file(), "缺少 docs/releases/v3.4.0.md")
        self.assertIn("V3.4.0", text(release))
        for readme in (README, AGENT_README):
            self.assertIn("V3.4", text(readme), str(readme))

    def test_v310_is_marked_historical(self) -> None:
        body = text(RELEASE_310)
        self.assertIn("历史", body)
        self.assertIn("V3.3.0", body)
        self.assertIn("TB-BRANCH-1", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
