#!/usr/bin/env python3
"""冷启动契约：启动串、阶段推导、mode 推导与事实可得性。

这些是规范层检查：它们证明「只给角色 + Task 目录」时规范提供了唯一推导路径，
不证明某个 LLM 一定照做。真实 Git / Gate 下的可执行证明见
`test_git_lifecycle.py` 的 GL-14 / GL-15。

每个用例的 `doc` 都指向它守护的规范位置，便于 Cleaner 复核。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DIRECTIVE = ROOT / ".agent/specs/WorkflowAgentDirective.md"
COLLAB = ROOT / ".agent/specs/AgentCollaborationSpecification.md"
STATE_GO = ROOT / ".agent/engine/internal/workflow/state.go"

ROLE_FILES = {
    "TASK_BUILDER": ".agent/roles/TaskBuilderPrompt.md",
    "ANALYST": ".agent/roles/AnalystAgent.md",
    "ARCHITECT": ".agent/roles/ArchitectAgent.md",
    "CODER": ".agent/roles/CoderAgent.md",
    "CLEANER": ".agent/roles/CleanerAgent.md",
    "OWNER_GATE": ".agent/roles/OwnerGateAgent.md",
    "DELIVERER": ".agent/roles/DelivererAgent.md",
}

GATE_NAMES = {"coder-start", "cleaner-start", "owner-gate-start", "delivery-start", "merge-ready", "none"}
LAUNCHER = "以 <ROLE> 角色执行任务"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def markdown_table(body: str, heading: str) -> list[list[str]]:
    """读取 heading 之后的第一张 Markdown 表格。"""
    section = body.split(heading, 1)[1]
    rows: list[list[str]] = []
    started = False
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if started and rows:
                break
            continue
        started = True
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(set(c) <= {"-", ":", " "} for c in cells):
            continue
        rows.append(cells)
    assert rows, f"{heading} 之后没有表格"
    return rows


def go_enum(name: str) -> set[str]:
    body = text(STATE_GO)
    block = body.split(name, 1)[1].split(")", 1)[0]
    return set(re.findall(r'"([A-Z_]+)"', block))


class ColdStartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directive = text(DIRECTIVE)
        cls.collab = text(COLLAB)
        cls.roles = {role: text(ROOT / path) for role, path in ROLE_FILES.items()}

    def test_launcher_is_defined_once_and_used_by_all_roles(self) -> None:
        self.assertIn(LAUNCHER, self.directive)
        self.assertIn("`.agent/tasks/<task-slug>/`", self.directive)
        self.assertIn("Session Bootstrap Protocol", self.directive)
        self.assertIn("只需要两行", self.directive)
        for role, body in self.roles.items():
            if role == "TASK_BUILDER":
                # TaskBuilder 是入口角色，使用 mode: create / owner_update，不经过启动串。
                continue
            with self.subTest(role=role):
                self.assertIn(f"以 {role} 角色执行任务", body, f"{role} 未给出启动串")

    def test_bootstrap_five_steps_and_opening_declaration(self) -> None:
        section = self.directive.split("### 2.1", 1)[1].split("### 2.2", 1)[0]
        for i in range(1, 6):
            self.assertRegex(section, rf"(?m)^{i}\. ", f"缺少第 {i} 步")
        for field in ("ROLE:", "TASK:", "STAGE:", "TARGET:", "ENTRY_GATE:"):
            self.assertIn(field, section, f"开工声明缺少 {field}")
        self.assertIn("不是 Gate 结论", section)
        for role, body in self.roles.items():
            if role == "TASK_BUILDER":
                continue
            self.assertIn("开工声明", body, f"{role} 未引用开工声明")

    def test_stage_table_covers_gate_state_space(self) -> None:
        rows = markdown_table(self.directive, "### 2.2 Stage Derivation Table")
        header = rows[0]
        body = rows[1:]
        self.assertEqual(
            header,
            ["route", "contract", "review", "owner", "delivery", "stage", "actor", "入口 Gate"],
            header,
        )
        gates = set()
        for row in body:
            cell = row[-1].replace("`", "")
            token = cell.split("（")[0].split()[0]
            gates.add(token if token in GATE_NAMES else token)
        unknown = {g for g in gates if g not in GATE_NAMES and not g.startswith("任意")}
        self.assertFalse(unknown, f"推导表出现未知 Gate: {sorted(unknown)}")
        for gate in GATE_NAMES - {"none"}:
            self.assertIn(gate, gates, f"推导表未覆盖 {gate}")

        joined = "\n".join(",".join(r) for r in body)
        for value in go_enum("ReviewStatus = "):
            self.assertIn(value, joined, f"推导表未覆盖 review.status={value}")
        for value in ("PENDING", "ACCEPTED", "REJECTED"):
            self.assertIn(value, joined, f"推导表未覆盖 owner.status={value}")
        for value in ("NOT_RUN", "PASS"):
            self.assertIn(value, joined, f"推导表未覆盖 delivery.status={value}")
        for route in ("NORMAL", "COMPLEX", "ARCHITECT"):
            self.assertIn(route, joined, f"推导表未覆盖 route={route}")

    def test_stage_table_rows_are_unique_and_label_unresolvable(self) -> None:
        rows = markdown_table(self.directive, "### 2.2 Stage Derivation Table")[1:]
        keys = [tuple(r[:5]) for r in rows]
        self.assertEqual(len(keys), len(set(keys)), "推导表存在重复状态组合")
        ambiguous = [r for r in rows if "不可机器推导" in ",".join(r)]
        self.assertEqual(len(ambiguous), 1, "必须恰好一行标注不可机器推导")
        self.assertIn("REJECTED", ",".join(ambiguous[0]))
        self.assertIn("owner-decision.md", ",".join(ambiguous[0]))
        self.assertIn("BLOCKED", ",".join(ambiguous[0]))
        self.assertIn("blocked.active", self.directive)

    def test_mode_derivation_covers_all_roles(self) -> None:
        rows = markdown_table(self.directive, "### 2.3")
        body = rows[1:]
        roles_in_table = {r[0].replace("`", "").strip() for r in body}
        self.assertEqual(roles_in_table, {"Analyst", "Architect", "Coder", "Cleaner", "Deliverer", "OwnerGate", "TaskBuilder"})
        joined = "\n".join("|".join(r) for r in body)
        for mode in (
            "analysis",
            "record_owner_decision",
            "reconsideration",
            "architecture_delivery",
            "structural_repair",
            "implementation",
            "finding_fix",
            "initial_review",
            "re_review",
            "milestone_verification",
            "re_verification",
            "owner_update",
        ):
            self.assertIn(mode, joined, f"mode 推导缺少 {mode}")
        self.assertIn("findings.md", joined, "finding_ids 必须从 findings.md 推导")

    def test_no_role_requires_mode_or_finding_ids_as_input(self) -> None:
        for role, body in self.roles.items():
            if role == "TASK_BUILDER":
                # TaskBuilder 的 mode 是 Owner 输入（create / owner_update），不是角色阶段模式。
                continue
            with self.subTest(role=role):
                self.assertNotRegex(
                    body,
                    r"(?m)^mode: (?!create|owner_update)",
                    f"{role} 仍把 mode 作为必填输入",
                )
                self.assertNotRegex(body, r"(?m)^finding_ids:", f"{role} 仍把 finding_ids 作为必填输入")
                self.assertIn("不要求 `mode`", body, f"{role} 未说明 mode 可推导")
                self.assertIn("WorkflowAgentDirective.md` §2.2 / §2.3", body, f"{role} 未引用唯一推导来源")

    def test_legacy_input_conflict_becomes_blocked(self) -> None:
        section = self.directive.split("### 2.4", 1)[1].split("## 3.", 1)[0]
        self.assertIn("冲突", section)
        self.assertIn("BLOCKED", section)
        self.assertIn("不得静默忽略", section)
        self.assertIn("HANDOFF_PROMPT", section)
        self.assertIn("不得作为事实来源", section)

    def test_derivation_table_is_referenced_not_duplicated(self) -> None:
        self.assertIn("§2.2", self.collab)
        self.assertIn("不复制推导表", self.collab)
        for role, body in self.roles.items():
            self.assertNotIn("| route | contract | review | owner | delivery |", body, f"{role} 复制了推导表")

    def test_cleaner_cold_start_has_all_facts(self) -> None:
        """Cleaner 只读 Task + Git 时，六类事实都必须有规范来源。"""
        cleaner = self.roles["CLEANER"]
        facts = {
            "审查对象": ("state.review.target", "cleaner-start"),
            "验收标准": ("task.md", "AC"),
            "审查基线": ("Review Baseline",),
            "路线": ("state.yaml", "route"),
            "当前模式": ("review.status=PENDING", "re_review"),
            "复审对象": ("findings.md", "OPEN"),
        }
        for fact, needles in facts.items():
            joined = cleaner + self.directive
            for needle in needles:
                self.assertIn(needle, joined, f"冷启动缺少事实 {fact} 的来源：{needle}")

    def test_owner_gate_and_deliverer_cold_start(self) -> None:
        for role, needles in {
            "OWNER_GATE": ("owner-gate-start", "owner-decision.md", "review.target"),
            "DELIVERER": ("delivery-start", "delivery.md", "Milestone"),
        }.items():
            body = self.roles[role] + self.directive
            for needle in needles:
                self.assertIn(needle, body, f"{role} 冷启动缺少 {needle}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
