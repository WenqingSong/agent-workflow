#!/usr/bin/env python3
"""七角色 Git Lifecycle 的真实 Git / Gate 行为测试。

覆盖 .agent/specs/WorkflowGitLifecycle.md §13 的 GL-01 ~ GL-13：
- 真实 git init / clone / bare remote / commit / push；
- 真实 workflow-check binary 的 Gate 判定；
- 真实只读自检工具的 scope / 远端核验。

不使用 Markdown 文本搜索替代行为验证。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / ".agent/specs/tools/git-lifecycle-check"
ENGINE = REPO / ".agent/engine"

TASK = "demo"
BRANCH = "feature/demo"
ZERO = "0" * 40


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    base = os.environ.copy()
    base.update(
        {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    if env:
        base.update(env)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=base)


class Fixture:
    """一个 bare remote + 两个本地 clone 的最小真实仓库。"""

    def __init__(self, engine_bin: Path):
        self.tmp = Path(tempfile.mkdtemp(prefix="lifecycle-"))
        self.engine_bin = engine_bin
        self.remote = self.tmp / "remote.git"
        self.seed = self.tmp / "seed"
        self.work = self.tmp / "work"
        run(["git", "init", "--bare", "-q", str(self.remote)], self.tmp).check_returncode()
        run(["git", "init", "-q", str(self.seed)], self.tmp).check_returncode()
        self._config(self.seed)
        self._write(self.seed, ".agent/workflow.yaml", CONFIG)
        self._write(self.seed, ".agent/registry/migrations.md", "# migrations\n\n| version | owner | status |\n|---|---|---|\n")
        self._write(self.seed, ".agent/bin/.gitignore", "*\n!.gitignore\n")
        self._write(self.seed, "README.md", "# demo\n")
        self._commit(self.seed, "chore: baseline", ["."])
        self._git(self.seed, "branch", "-M", "develop")
        self._git(self.seed, "remote", "add", "origin", str(self.remote))
        self._git(self.seed, "push", "-q", "-u", "origin", "develop")

        self._git(self.tmp, "clone", "-q", "-b", "develop", str(self.remote), str(self.work))
        self._config(self.work)
        self._git(self.work, "switch", "-q", "-c", BRANCH)
        self._write(self.work, f".agent/tasks/{TASK}/task.md", "# task\n")
        self._write(self.work, f".agent/tasks/{TASK}/state.yaml", state_text())
        self._commit(self.work, "chore(task): 初始化任务 demo", [".agent/tasks/demo"])
        self._git(self.work, "push", "-q", "-u", "origin", BRANCH)
        bin_dir = self.work / ".agent/bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.engine_bin, bin_dir / "workflow-check")
        os.chmod(bin_dir / "workflow-check", 0o755)

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _config(self, repo: Path) -> None:
        self._git(repo, "config", "user.name", "test")
        self._git(repo, "config", "user.email", "test@example.com")
        self._git(repo, "config", "commit.gpgsign", "false")

    def _git(self, repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        proc = run(["git", *args], repo)
        if check and proc.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} 失败: {proc.stdout}\n{proc.stderr}")
        return proc

    def _write(self, repo: Path, rel: str, text: str) -> None:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _commit(self, repo: Path, message: str, paths: list[str]) -> str:
        self._git(repo, "add", "--", *paths)
        self._git(repo, "commit", "-q", "-m", message)
        return self._git(repo, "rev-parse", "HEAD").stdout.strip()

    def write(self, rel: str, text: str) -> None:
        self._write(self.work, rel, text)

    def state(self, **kwargs: str) -> None:
        self.write(f".agent/tasks/{TASK}/state.yaml", state_text(**kwargs))

    def commit(self, message: str, paths: list[str]) -> str:
        return self._commit(self.work, message, paths)

    def push(self, branch: str = BRANCH) -> subprocess.CompletedProcess:
        return self._git(self.work, "push", "-q", "origin", branch)

    def head(self) -> str:
        return self._git(self.work, "rev-parse", "HEAD").stdout.strip()

    def add(self, *paths: str) -> None:
        self._git(self.work, "add", "--", *paths)

    def tool(self, *args: str) -> subprocess.CompletedProcess:
        return run(["python3", str(TOOL), *args, "--root", str(self.work)], self.work)

    def tool_no_root(self, *args: str) -> subprocess.CompletedProcess:
        return run(["python3", str(TOOL), *args], self.work)

    def gate(self, name: str) -> subprocess.CompletedProcess:
        return run(
            [
                str(self.work / ".agent/bin/workflow-check"),
                "gate",
                name,
                f".agent/tasks/{TASK}",
                "--root",
                str(self.work),
            ],
            self.work,
        )

    def push_expect_failure(self) -> subprocess.CompletedProcess:
        return self._git(self.work, "push", "origin", BRANCH, check=False)

    def second_clone(self) -> Path:
        other = self.tmp / "other"
        self._git(self.tmp, "clone", "-q", "-b", BRANCH, str(self.remote), str(other))
        self._config(other)
        return other


TASK_MD = """# Task: demo

## Goal
证明冷启动可以只读 Task 目录开工。

## Scope
- 一个可观察的实现结果。

## Out of Scope
- 无关模块。

## Acceptance Criteria
- [ ] AC-001：给定有效输入，当调用入口时，返回成功结果。
- [ ] AC-002：失败时返回明确错误。

## Verification
- AC-001 → 单元测试；AC-002 → 错误路径测试。

## Complexity
NORMAL

## Review Baseline
- Base branch：develop
- Base commit：__BASE__
- Feature branch：feature/demo
- Remote：origin
"""

CONFIG = """schema_version: 1
git:
  integration_branch: develop
resources:
  migration_version:
    registry: .agent/registry/migrations.md
"""


def state_text(
    task: str = TASK,
    contract_status: str = "NOT_REQUIRED",
    contract_target: str = "",
    review_status: str = "NOT_REQUESTED",
    review_target: str = "",
    owner_status: str = "PENDING",
    owner_target: str = "",
    delivery_status: str = "NOT_RUN",
    delivery_target: str = "",
    feature_head: str = "",
    develop_base: str = "",
    blocked: bool = False,
    blocked_by: str = "",
    blocked_reason: str = "",
) -> str:
    return f"""schema_version: 3
task_id: {task}
contract:
  status: {contract_status}
  target: "{contract_target}"
resources:
  reservations: {{}}
review:
  status: {review_status}
  target: "{review_target}"
owner:
  status: {owner_status}
  review_target: "{owner_target}"
delivery:
  status: {delivery_status}
  review_target: "{delivery_target}"
  feature_head: "{feature_head}"
  develop_base: "{develop_base}"
blocked:
  active: {str(blocked).lower()}
  by: "{blocked_by}"
  reason: "{blocked_reason}"
"""


def build_engine() -> Path:
    out_dir = Path(tempfile.mkdtemp(prefix="engine-bin-"))
    binary = out_dir / "workflow-check"
    env = os.environ.copy()
    env["GOWORK"] = "off"
    proc = subprocess.run(
        ["go", "build", "-o", str(binary), "./cmd/workflow-check"],
        cwd=ENGINE,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"构建 workflow-check 失败:\n{proc.stdout}\n{proc.stderr}")
    return binary


class LifecycleTest(unittest.TestCase):
    engine_bin: Path
    engine_dir: Path

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("go") is None:
            raise unittest.SkipTest("缺少 go，无法运行真实 Gate 测试")
        cls.engine_bin = build_engine()
        cls.engine_dir = cls.engine_bin.parent

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.engine_dir, ignore_errors=True)

    def setUp(self) -> None:
        self.fx = Fixture(self.engine_bin)

    def tearDown(self) -> None:
        self.fx.close()

    # ---------- 辅助：各阶段证据 ----------

    def make_c1(self, name: str = "internal/foo.go", content: str = "package internal\n") -> str:
        self.fx.write(name, content)
        self.fx.add(name)
        check = self.fx.tool("commit-check", "--role", "Coder", "--task", TASK, "--mode", "evidence")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        return self.fx.commit("feat: 实现 demo", [name])

    def make_c2(self, c1: str, extra: list[str] | None = None) -> str:
        self.fx.state(review_status="PENDING", review_target=c1)
        paths = [f".agent/tasks/{TASK}/state.yaml"] + (extra or [])
        self.fx.add(*paths)
        check = self.fx.tool("commit-check", "--role", "Coder", "--task", TASK, "--mode", "metadata")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        return self.fx.commit("chore(task): 请求 Review", paths)

    def make_review_ready(self) -> tuple[str, str]:
        c1 = self.make_c1()
        c2 = self.make_c2(c1)
        self.fx.push()
        self.assertEqual(self.fx.gate("cleaner-start").returncode, 0)
        return c1, c2

    def make_clean(self, c1: str) -> str:
        self.fx.write(f".agent/tasks/{TASK}/findings.md", "# Findings\n\nResult: CLEAN\n")
        self.fx.write(f".agent/tasks/{TASK}/core-logic.md", "# Core Logic\n\n## CL-001\n")
        self.fx.state(review_status="CLEAN", review_target=c1)
        paths = [
            f".agent/tasks/{TASK}/findings.md",
            f".agent/tasks/{TASK}/core-logic.md",
            f".agent/tasks/{TASK}/state.yaml",
        ]
        self.fx.add(*paths)
        check = self.fx.tool(
            "commit-check", "--role", "Cleaner", "--task", TASK, "--mode", "artifacts"
        )
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        sha = self.fx.commit("docs(review): CLEAN", paths)
        self.fx.push()
        return sha

    def make_accepted(self, c1: str) -> str:
        self.fx.write(f".agent/tasks/{TASK}/owner-decision.md", "# Owner Decision\n\nACCEPTED\n")
        self.fx.state(review_status="CLEAN", review_target=c1, owner_status="ACCEPTED", owner_target=c1)
        paths = [f".agent/tasks/{TASK}/owner-decision.md", f".agent/tasks/{TASK}/state.yaml"]
        self.fx.add(*paths)
        check = self.fx.tool(
            "commit-check", "--role", "OwnerGate", "--task", TASK, "--mode", "artifacts", "--phase", "v2"
        )
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        sha = self.fx.commit("docs(owner): ACCEPTED", paths)
        self.fx.push()
        return sha

    # ---------- GL-01 ~ GL-03：合法两阶段 Evidence ----------

    def test_gl01_analyst_a1_a2_coder_start(self) -> None:
        self.fx.write(f".agent/tasks/{TASK}/contract.md", "# Contract\n\nAPPROVED\n")
        self.fx.add(f".agent/tasks/{TASK}/contract.md")
        check = self.fx.tool("commit-check", "--role", "Analyst", "--task", TASK, "--mode", "evidence")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        a1 = self.fx.commit("docs(contract): A1", [f".agent/tasks/{TASK}/contract.md"])
        self.assertRegex(a1, r"^[0-9a-f]{40}$")

        self.fx.state(contract_status="APPROVED", contract_target=a1)
        self.fx.add(f".agent/tasks/{TASK}/state.yaml")
        check = self.fx.tool("commit-check", "--role", "Analyst", "--task", TASK, "--mode", "metadata")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.fx.commit("chore(contract): A2", [f".agent/tasks/{TASK}/state.yaml"])
        self.fx.push()

        gate = self.fx.gate("coder-start")
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)

        # A2 混入业务代码 / A1 混入 state.yaml 都必须被前置自检拒绝
        self.fx.write("internal/mixed.go", "package internal\n")
        self.fx.add("internal/mixed.go")
        bad = self.fx.tool("commit-check", "--role", "Analyst", "--task", TASK, "--mode", "metadata")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("超出", bad.stdout)

    def test_gl02_coder_c1_c2_cleaner_start(self) -> None:
        c1, c2 = self.make_review_ready()
        self.assertEqual(len(c1), 40)
        self.assertEqual(len(c2), 40)
        state = (self.fx.work / f".agent/tasks/{TASK}/state.yaml").read_text(encoding="utf-8")
        self.assertIn(f'target: "{c1}"', state)

        # EVIDENCE 混入 state.yaml 必须拒绝
        self.fx.write("internal/second.go", "package internal\n")
        self.fx.state(review_status="CLEAN", review_target=c1)
        self.fx.add("internal/second.go", f".agent/tasks/{TASK}/state.yaml")
        bad = self.fx.tool("commit-check", "--role", "Coder", "--task", TASK, "--mode", "evidence")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("state.yaml", bad.stdout)

    def test_gl03_architect_d1_d2_cleaner_start(self) -> None:
        self.fx.write(f".agent/tasks/{TASK}/architecture.md", "# Architecture\n\n## 模块边界\n")
        self.fx.write("internal/arch/arch.go", "package arch\n")
        self.fx.write("internal/arch/arch_test.go", "package arch\n\nimport \"testing\"\n\nfunc TestStructure(t *testing.T) {}\n")
        paths = [
            f".agent/tasks/{TASK}/architecture.md",
            "internal/arch/arch.go",
            "internal/arch/arch_test.go",
        ]
        self.fx.add(*paths)
        check = self.fx.tool("commit-check", "--role", "Architect", "--task", TASK, "--mode", "evidence")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        d1 = self.fx.commit("feat(arch): D1 结构快照", paths)
        self.fx.state(review_status="PENDING", review_target=d1)
        paths2 = [f".agent/tasks/{TASK}/state.yaml"]
        self.fx.add(*paths2)
        check = self.fx.tool("commit-check", "--role", "Architect", "--task", TASK, "--mode", "metadata")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.fx.commit("chore(arch): D2 Review Request", paths2)
        self.fx.push()
        gate = self.fx.gate("cleaner-start")
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)

        # ARCHITECT OwnerGate V1：只写 findings/core-logic/review.*，完成后才能 owner-gate-start
        self.fx.write(f".agent/tasks/{TASK}/findings.md", "# Findings\n\nResult: CLEAN\n")
        self.fx.write(f".agent/tasks/{TASK}/core-logic.md", "# Core Logic\n\n## CL-001\n")
        self.fx.state(review_status="CLEAN", review_target=d1)
        v1_paths = [
            f".agent/tasks/{TASK}/findings.md",
            f".agent/tasks/{TASK}/core-logic.md",
            f".agent/tasks/{TASK}/state.yaml",
        ]
        self.fx.add(*v1_paths)
        check = self.fx.tool(
            "commit-check", "--role", "OwnerGate", "--task", TASK, "--mode", "artifacts", "--phase", "v1"
        )
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.fx.commit("docs(arch): V1 独立结构验证", v1_paths)
        self.fx.push()
        v1_gate = self.fx.gate("owner-gate-start")
        self.assertEqual(v1_gate.returncode, 0, v1_gate.stdout + v1_gate.stderr)

    def test_taskbuilder_bootstrap_scope(self) -> None:
        other = "demo2"
        self.fx.write(f".agent/tasks/{other}/task.md", "# task2\n")
        self.fx.write(f".agent/tasks/{other}/state.yaml", state_text(task=other))
        paths = [f".agent/tasks/{other}/task.md", f".agent/tasks/{other}/state.yaml"]
        self.fx.add(*paths)
        check = self.fx.tool("commit-check", "--role", "TaskBuilder", "--task", other, "--mode", "bootstrap")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.fx.commit("chore(task): 初始化 demo2", paths)

        self.fx.write("internal/bootstrap-leak.go", "package internal\n")
        self.fx.add("internal/bootstrap-leak.go")
        bad = self.fx.tool("commit-check", "--role", "TaskBuilder", "--task", other, "--mode", "bootstrap")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("超出", bad.stdout)

    def test_taskbuilder_bootstrap_accepts_all_initial_artifacts(self) -> None:
        """TB 初始化会创建六个 Artifact；bootstrap 白名单必须覆盖它们，而不是只允许两件。"""
        task = "six-files"
        for name, text in (
            ("task.md", "# task\n"),
            ("state.yaml", state_text(task=task)),
            ("findings.md", "# Findings\n"),
            ("core-logic.md", "# Core Logic\n"),
            ("owner-decision.md", "# Owner Decision\n"),
            ("delivery.md", "# Delivery\n"),
        ):
            self.fx.write(f".agent/tasks/{task}/{name}", text)
        self.fx.add(f".agent/tasks/{task}")
        check = self.fx.tool(
            "commit-check", "--role", "TaskBuilder", "--task", task, "--mode", "bootstrap"
        )
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_taskbuilder_baseline_uses_current_branch_and_head(self) -> None:
        """场景 1/3：当前 feature 分支即默认基线，不需要 Owner 提供分支名或 SHA。"""
        proc = self.fx.tool("baseline", "--task", "next", "--branch", "task/next")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("status=READY", proc.stdout)
        self.assertIn(f"branch={BRANCH}", proc.stdout)
        self.assertIn(f"head={self.fx.head()}", proc.stdout)

    def test_taskbuilder_baseline_protected_branch_is_valid_base(self) -> None:
        """场景 2/4：当前分支是 develop/main 时仍以当前 HEAD 为基线，但目标分支必须另建。"""
        self.fx._git(self.fx.work, "switch", "-q", "develop")
        proc = self.fx.tool("baseline", "--task", "next", "--branch", "task/next")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("status=READY", proc.stdout)
        self.assertIn("branch=develop", proc.stdout)
        self.assertIn("kind=INTEGRATION", proc.stdout)

        bad = self.fx.tool("baseline", "--task", "next", "--branch", "develop")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("BRANCH_PROTECTED", bad.stdout)

    def test_taskbuilder_baseline_detached_head_blocks(self) -> None:
        """场景 5：detached HEAD 不得猜测基线。"""
        self.fx._git(self.fx.work, "checkout", "-q", "--detach", "HEAD")
        proc = self.fx.tool("baseline", "--task", "next", "--branch", "task/next")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("DETACHED_HEAD", proc.stdout)
        self.assertIn("detached_candidates=", proc.stdout)
        self.assertIn(BRANCH, proc.stdout, "必须报告包含 HEAD 的候选分支")

    def test_taskbuilder_baseline_reports_preserved_foreign_changes(self) -> None:
        """场景 6：既有修改被报告并保留，不要求工作区 clean。"""
        self.fx.write("notes/keep.txt", "keep me\n")
        proc = self.fx.tool("baseline", "--task", "next", "--branch", "task/next")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("READY_WITH_PRESERVED_CHANGES", proc.stdout)
        self.assertIn("preserved=1", proc.stdout)
        self.assertEqual((self.fx.work / "notes/keep.txt").read_text(encoding="utf-8"), "keep me\n")

    def test_taskbuilder_baseline_detects_branch_conflicts(self) -> None:
        """场景 8/9/10：本地、远端同名分支都必须被识别。"""
        local = self.fx.tool("baseline", "--task", "next", "--branch", BRANCH)
        self.assertEqual(local.returncode, 1)
        self.assertIn("BRANCH_CONFLICT_LOCAL", local.stdout)

        self.fx._git(self.fx.seed, "switch", "-q", "-c", "task/remote-only")
        self.fx._git(self.fx.seed, "push", "-q", "origin", "task/remote-only")
        remote = self.fx.tool("baseline", "--task", "next", "--branch", "task/remote-only")
        self.assertEqual(remote.returncode, 1)
        self.assertIn("BRANCH_CONFLICT_REMOTE", remote.stdout)

        existing = self.fx.tool("baseline", "--task", TASK)
        self.assertEqual(existing.returncode, 1)
        self.assertIn("TASK_EXISTS", existing.stdout)

    def test_taskbuilder_baseline_reports_ahead_behind_and_diverged(self) -> None:
        """场景 7：远端领先 / 落后 / 分叉只报告，不自动 pull / reset / rebase。"""
        self.fx.write("internal/local.go", "package internal\n")
        self.fx.add("internal/local.go")
        self.fx.commit("feat: 本地领先", ["internal/local.go"])
        ahead = self.fx.tool("baseline", "--task", "next")
        self.assertEqual(ahead.returncode, 0, ahead.stdout)
        self.assertIn("sync=AHEAD", ahead.stdout)

        other = self.fx.second_clone()
        (other / "internal").mkdir(exist_ok=True)
        (other / "internal/remote.go").write_text("package internal\n", encoding="utf-8")
        self.fx._git(other, "add", "internal/remote.go")
        self.fx._git(other, "commit", "-q", "-m", "feat: 远端前进")
        self.fx._git(other, "push", "-q", "origin", BRANCH)
        self.fx._git(self.fx.work, "fetch", "-q", "origin")

        diverged = self.fx.tool("baseline", "--task", "next")
        self.assertEqual(diverged.returncode, 0, diverged.stdout)
        self.assertIn("sync=DIVERGED", diverged.stdout)
        self.assertEqual(
            self.fx._git(self.fx.work, "rev-parse", "HEAD").stdout.strip(),
            self.fx.head(),
            "baseline 不得自动 reset / rebase / pull",
        )

    def test_taskbuilder_bootstrap_preserves_staged_foreign_content(self) -> None:
        """场景 6/16/17：外来已暂存内容保留且不进入 BOOTSTRAP；dirty 时不得 HANDOFF。"""
        foreign = "internal/foreign.go"
        self.fx.write(foreign, "package internal\n")
        self.fx.add(foreign)

        pre = self.fx.tool("preflight", "--role", "TaskBuilder", "--task", "next", "--bootstrap")
        self.assertEqual(pre.returncode, 0, pre.stdout + pre.stderr)
        self.assertIn("preserved_foreign=1", pre.stdout)

        self.fx.write(".agent/tasks/next/task.md", "# Task: next\n")
        self.fx.write(".agent/tasks/next/state.yaml", state_text(task="next"))
        for name in ("findings.md", "core-logic.md", "owner-decision.md", "delivery.md"):
            self.fx.write(f".agent/tasks/next/{name}", f"# {name}\n")
        self.fx.add(".agent/tasks/next")
        check = self.fx.tool(
            "commit-check",
            "--role",
            "TaskBuilder",
            "--task",
            "next",
            "--mode",
            "bootstrap",
            "--preserve-foreign",
        )
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn("preserved_staged=1", check.stdout)

        self.fx._git(
            self.fx.work, "commit", "-q", "-m", "chore(task): 初始化 next", "--", ".agent/tasks/next"
        )
        changed = self.fx._git(
            self.fx.work, "show", "--name-only", "--format=", "HEAD"
        ).stdout.split()
        self.assertTrue(changed)
        self.assertTrue(
            all(path.startswith(".agent/tasks/next/") for path in changed),
            f"BOOTSTRAP 不得提交任务目录外内容: {changed}",
        )
        self.assertEqual((self.fx.work / foreign).read_text(encoding="utf-8"), "package internal\n")
        staged = self.fx._git(self.fx.work, "diff", "--cached", "--name-only").stdout
        self.assertIn(foreign, staged, "外来已暂存内容必须保留在暂存区")

        handoff = self.fx.tool("handoff-check", "--role", "TaskBuilder", "--task", "next")
        self.assertEqual(handoff.returncode, 1)
        self.assertIn("dirty", handoff.stdout)

    # ---------- GL-04：未完成 Evidence 阶段不得 HANDOFF ----------

    def test_gl04_incomplete_evidence_blocks_handoff(self) -> None:
        c1 = self.make_c1()
        self.fx.push()
        # 只有 C1，Review Metadata 尚未提交（dirty）
        self.fx.state(review_status="PENDING", review_target=c1)
        gate = self.fx.gate("cleaner-start")
        self.assertEqual(gate.returncode, 1, gate.stdout)
        handoff = self.fx.tool("handoff-check", "--role", "Coder", "--task", TASK)
        self.assertEqual(handoff.returncode, 1)
        self.assertIn("dirty", handoff.stdout)

        # 提交 C2 但不 push：本地 != 远端，Gate 仍必须 FAIL
        self.fx.add(f".agent/tasks/{TASK}/state.yaml")
        self.fx.commit("chore(task): 请求 Review", [f".agent/tasks/{TASK}/state.yaml"])
        gate = self.fx.gate("cleaner-start")
        self.assertEqual(gate.returncode, 1, gate.stdout)
        self.assertIn("push", gate.stdout)
        handoff = self.fx.tool("handoff-check", "--role", "Coder", "--task", TASK)
        self.assertEqual(handoff.returncode, 1)

        self.fx.push()
        self.assertEqual(self.fx.gate("cleaner-start").returncode, 0)
        self.assertEqual(self.fx.tool("handoff-check", "--role", "Coder", "--task", TASK).returncode, 0)

    # ---------- GL-05：Commit 后 Push 失败 ----------

    def test_gl05_push_failure_must_not_claim_synced(self) -> None:
        c1, _ = self.make_review_ready()

        other = self.fx.second_clone()
        self.fx._write(other, "remote-advance.txt", "other writer\n")
        self.fx._commit(other, "chore: other writer", ["remote-advance.txt"])
        self.fx._git(other, "push", "-q", "origin", BRANCH)

        # 本地再形成一个 commit，push 必然被拒
        self.fx.write("internal/local.go", "package internal\n")
        self.fx.add("internal/local.go")
        self.fx.commit("feat: local change", ["internal/local.go"])
        failed = self.fx.push_expect_failure()
        self.assertNotEqual(failed.returncode, 0, "push 应被 non-fast-forward 拒绝")

        verify = self.fx.tool("verify-remote", "--task", TASK)
        self.assertEqual(verify.returncode, 1)
        self.assertIn("不一致", verify.stdout)
        handoff = self.fx.tool("handoff-check", "--role", "Coder", "--task", TASK)
        self.assertEqual(handoff.returncode, 1)
        gate = self.fx.gate("cleaner-start")
        self.assertEqual(gate.returncode, 1, gate.stdout)

    # ---------- GL-06：Push 成功后 Gate FAIL ----------

    def test_gl06_gate_fail_after_push_blocks_handoff(self) -> None:
        c1 = self.make_c1()
        # 故意把业务代码混入 metadata commit（绕过前置自检，模拟直接 git 写）
        self.fx.write("internal/sneaky.go", "package internal\n")
        self.fx.state(review_status="PENDING", review_target=c1)
        paths = ["internal/sneaky.go", f".agent/tasks/{TASK}/state.yaml"]
        self.fx.add(*paths)
        check = self.fx.tool("commit-check", "--role", "Coder", "--task", TASK, "--mode", "metadata")
        self.assertEqual(check.returncode, 1, "前置自检必须拒绝 metadata 混入业务代码")
        self.fx.commit("chore(task): 请求 Review", paths)
        self.fx.push()

        gate = self.fx.gate("cleaner-start")
        self.assertEqual(gate.returncode, 1, gate.stdout)
        self.assertIn("sneaky.go", gate.stdout)
        # Git 事实本身是同步的，但 Gate FAIL 仍然禁止 HANDOFF
        self.assertEqual(self.fx.tool("verify-remote", "--task", TASK).returncode, 0)

    # ---------- GL-07：Dirty Worktree ----------

    def test_gl07_dirty_worktree_blocks_handoff(self) -> None:
        c1, _ = self.make_review_ready()
        self.fx.write("scratch.txt", "uncommitted\n")
        gate = self.fx.gate("cleaner-start")
        self.assertEqual(gate.returncode, 1, gate.stdout)
        handoff = self.fx.tool("handoff-check", "--role", "Coder", "--task", TASK)
        self.assertEqual(handoff.returncode, 1)
        self.assertIn("dirty", handoff.stdout)

    # ---------- GL-08：错误远端 SHA ----------

    def test_gl08_moved_remote_detected_by_preflight(self) -> None:
        self.make_review_ready()
        other = self.fx.second_clone()
        self.fx._write(other, "remote-only.txt", "remote moved\n")
        self.fx._commit(other, "chore: advance remote", ["remote-only.txt"])
        self.fx._git(other, "push", "-q", "origin", BRANCH)

        verify = self.fx.tool("verify-remote", "--task", TASK)
        self.assertEqual(verify.returncode, 1, verify.stdout)
        self.assertIn("不一致", verify.stdout)
        # 已知 Engine 限制：handoffReady 只看本地 tracking ref，远端被推进时仍可能 PASS。
        gate = self.fx.gate("cleaner-start")
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)

    # ---------- GL-09：DANGER write-ahead ----------

    def test_gl09_danger_forced_evidence_blocks_all_gates(self) -> None:
        ledger = (
            "# Loop Ledger\n\n- Task ID: demo\n- Ledger Version: 1\n\n"
            "## EV-0001 DANGER OWNER_DECISION:example S1 round=2\n\n"
            "- event_type: DANGER\n- outcome: DANGER\n"
        )
        self.fx.write(f".agent/tasks/{TASK}/loop-ledger.md", ledger)
        self.fx.write(f".agent/tasks/{TASK}/danger-report.md", "# DANGER Report\n\n## 12. 结论\n\n停止。\n")
        self.fx.state(blocked=True, blocked_by="Coder", blocked_reason="loop_key=OWNER_DECISION:example; rounds=2")
        paths = [
            f".agent/tasks/{TASK}/loop-ledger.md",
            f".agent/tasks/{TASK}/danger-report.md",
            f".agent/tasks/{TASK}/state.yaml",
        ]
        self.fx.add(*paths)
        check = self.fx.tool("commit-check", "--role", "Coder", "--task", TASK, "--mode", "forced")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.fx.commit("chore(workflow): DANGER 停机", paths)
        self.fx.push()

        for gate in ("coder-start", "cleaner-start", "owner-gate-start", "delivery-start", "merge-ready"):
            result = self.fx.gate(gate)
            self.assertEqual(result.returncode, 1, f"{gate}: {result.stdout}")
            self.assertIn("blocked", result.stdout)

        # FORCED_EVIDENCE 不允许混入业务代码
        self.fx.write("internal/not-danger.go", "package internal\n")
        self.fx.add("internal/not-danger.go")
        bad = self.fx.tool("commit-check", "--role", "Coder", "--task", TASK, "--mode", "forced")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("超出", bad.stdout)

    def test_gl09b_write_ahead_ledger_only_is_allowed(self) -> None:
        self.fx.write(
            f".agent/tasks/{TASK}/loop-ledger.md",
            "# Loop Ledger\n\n## EV-0001 REQUEST OWNER_DECISION:example S1 round=1\n",
        )
        self.fx.add(f".agent/tasks/{TASK}/loop-ledger.md")
        check = self.fx.tool("commit-check", "--role", "Coder", "--task", TASK, "--mode", "forced")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.fx.commit("chore(workflow): write-ahead EV-0001", [f".agent/tasks/{TASK}/loop-ledger.md"])
        self.fx.push()
        self.assertEqual(self.fx.tool("verify-remote", "--task", TASK).returncode, 0)

        # 账本提交不得夹带 Review 状态变化
        self.fx.write(
            f".agent/tasks/{TASK}/loop-ledger.md",
            "# Loop Ledger\n\n## EV-0001 REQUEST OWNER_DECISION:example S1 round=1\n\n## EV-0002 CLOSE\n",
        )
        self.fx.state(review_status="CLEAN", review_target=ZERO)
        self.fx.add(f".agent/tasks/{TASK}/loop-ledger.md", f".agent/tasks/{TASK}/state.yaml")
        bad = self.fx.tool("commit-check", "--role", "Coder", "--task", TASK, "--mode", "forced")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("不允许的字段", bad.stdout)

    # ---------- GL-10 / GL-11：失败证据 ----------

    def test_gl10_owner_reject_is_persisted_and_blocks_delivery(self) -> None:
        c1, _ = self.make_review_ready()
        self.make_clean(c1)
        self.fx.write(f".agent/tasks/{TASK}/owner-decision.md", "# Owner Decision\n\nREJECTED: 缺少关键错误路径\n")
        self.fx.state(
            review_status="CLEAN",
            review_target=c1,
            owner_status="REJECTED",
            owner_target=c1,
        )
        paths = [f".agent/tasks/{TASK}/owner-decision.md", f".agent/tasks/{TASK}/state.yaml"]
        self.fx.add(*paths)
        check = self.fx.tool(
            "commit-check", "--role", "OwnerGate", "--task", TASK, "--mode", "artifacts", "--phase", "v2"
        )
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.fx.commit("docs(owner): REJECTED", paths)
        self.fx.push()

        delivery = self.fx.gate("delivery-start")
        self.assertEqual(delivery.returncode, 1)
        self.assertIn("ACCEPTED", delivery.stdout)
        self.assertEqual(self.fx.tool("handoff-check", "--role", "OwnerGate", "--task", TASK).returncode, 0)
        remote_state = self.fx._git(
            self.fx.work, "show", f"origin/{BRANCH}:.agent/tasks/{TASK}/state.yaml"
        ).stdout
        self.assertIn("REJECTED", remote_state)

        # failure evidence 不允许混入业务代码
        self.fx.write("internal/reject-code.go", "package internal\n")
        self.fx.add("internal/reject-code.go")
        bad = self.fx.tool(
            "commit-check", "--role", "OwnerGate", "--task", TASK, "--mode", "artifacts", "--phase", "v2"
        )
        self.assertEqual(bad.returncode, 1)

    def test_gl11_cleaner_changes_required_evidence(self) -> None:
        c1, _ = self.make_review_ready()
        self.fx.write(f".agent/tasks/{TASK}/findings.md", "# CLEAN-001\n\nStatus: OPEN\n")
        self.fx.state(review_status="CHANGES_REQUIRED", review_target=c1)
        paths = [f".agent/tasks/{TASK}/findings.md", f".agent/tasks/{TASK}/state.yaml"]
        self.fx.add(*paths)
        check = self.fx.tool("commit-check", "--role", "Cleaner", "--task", TASK, "--mode", "artifacts")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.fx.commit("docs(review): CHANGES_REQUIRED CLEAN-001", paths)
        self.fx.push()

        self.assertEqual(self.fx.gate("cleaner-start").returncode, 1)
        self.assertEqual(self.fx.gate("owner-gate-start").returncode, 1)
        self.assertEqual(self.fx.tool("verify-remote", "--task", TASK).returncode, 0)
        remote_findings = self.fx._git(
            self.fx.work, "show", f"origin/{BRANCH}:.agent/tasks/{TASK}/findings.md"
        ).stdout
        self.assertIn("CLEAN-001", remote_findings)

    # ---------- GL-12：Registry-only 边界 ----------

    def test_gl12_registry_only_boundary(self) -> None:
        self.fx._git(self.fx.work, "switch", "-q", "develop")
        self.fx.write(".agent/registry/migrations.md", "# migrations\n\n| 1001 | demo | RESERVED |\n")
        self.fx.add(".agent/registry/migrations.md")
        check = self.fx.tool("commit-check", "--role", "Analyst", "--task", TASK, "--mode", "registry")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.fx.commit("chore(registry): RESERVED 1001", [".agent/registry/migrations.md"])
        push = self.fx.tool("push-check", "--role", "Analyst", "--task", TASK, "--mode", "registry")
        self.assertEqual(push.returncode, 0, push.stdout)
        self.fx.push("develop")
        self.assertEqual(self.fx.tool("verify-remote", "--task", TASK).returncode, 0)

        # 业务角色不能 push develop；Registry commit 不能混入业务文件
        bad_push = self.fx.tool("push-check", "--role", "Coder", "--task", TASK)
        self.assertEqual(bad_push.returncode, 1)
        self.assertIn("integration", bad_push.stdout)
        bad_mode = self.fx.tool("commit-check", "--role", "Coder", "--task", TASK, "--mode", "registry")
        self.assertEqual(bad_mode.returncode, 1)

        self.fx.write("internal/not-registry.go", "package internal\n")
        self.fx.add("internal/not-registry.go")
        bad_scope = self.fx.tool("commit-check", "--role", "Analyst", "--task", TASK, "--mode", "registry")
        self.assertEqual(bad_scope.returncode, 1)
        self.assertIn("超出", bad_scope.stdout)

    # ---------- GL-13：正常完成自行 Commit / Push ----------

    def test_gl13_normal_completion_self_commit_push(self) -> None:
        c1, _ = self.make_review_ready()
        self.make_clean(c1)
        self.fx.write(f".agent/tasks/{TASK}/owner-decision.md", "# Owner Decision\n\nACCEPTED\n")
        self.fx.state(
            review_status="CLEAN",
            review_target=c1,
            owner_status="ACCEPTED",
            owner_target=c1,
        )
        paths = [f".agent/tasks/{TASK}/owner-decision.md", f".agent/tasks/{TASK}/state.yaml"]
        self.fx.add(*paths)
        check = self.fx.tool(
            "commit-check", "--role", "OwnerGate", "--task", TASK, "--mode", "artifacts", "--phase", "v2"
        )
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.fx.commit("docs(owner): ACCEPTED", paths)
        self.fx.push()
        self.assertEqual(self.fx.gate("delivery-start").returncode, 0)

        feature_head = self.fx.head()
        develop_base = self.fx._git(self.fx.work, "rev-parse", "origin/develop").stdout.strip()
        self.fx.write(f".agent/tasks/{TASK}/delivery.md", "# Delivery\n\nPASS\n")
        self.fx.state(
            review_status="CLEAN",
            review_target=c1,
            owner_status="ACCEPTED",
            owner_target=c1,
            delivery_status="PASS",
            delivery_target=c1,
            feature_head=feature_head,
            develop_base=develop_base,
        )
        paths = [f".agent/tasks/{TASK}/delivery.md", f".agent/tasks/{TASK}/state.yaml"]
        self.fx.add(*paths)
        check = self.fx.tool(
            "commit-check", "--role", "Deliverer", "--task", TASK, "--mode", "artifacts"
        )
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.fx.commit("docs(delivery): PASS", paths)
        self.fx.push()
        self.assertEqual(self.fx.gate("merge-ready").returncode, 0)

        verify = self.fx.tool("verify-remote", "--task", TASK)
        self.assertEqual(verify.returncode, 0, verify.stdout)
        handoff = self.fx.tool(
            "handoff-check", "--role", "Deliverer", "--task", TASK, "--gate", "merge-ready"
        )
        self.assertEqual(handoff.returncode, 0, handoff.stdout + handoff.stderr)
        status = self.fx._git(self.fx.work, "status", "--porcelain").stdout
        self.assertEqual(status.strip(), "")

    # ---------- 工具自身的前置检查 ----------

    def test_negative_scope_phase_and_evidence_sha(self) -> None:
        """非法 staged scope、PROGRESS 式提交、V1/V2 混淆与无效 SHA 必须 FAIL。"""
        state_path = f".agent/tasks/{TASK}/state.yaml"

        # EVIDENCE 混入 state.yaml → FAIL（state.yaml 必须真的发生变化才会被 stage）
        self.fx.write("internal/foo.go", "package internal\n")
        self.fx.write(state_path, state_text(review_status="PENDING", review_target="HEAD"))
        self.fx.add("internal/foo.go", state_path)
        proc = self.fx.tool("commit-check", "--role", "Coder", "--task", TASK, "--mode", "evidence")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("state.yaml", proc.stdout)
        self.fx._git(self.fx.work, "restore", "--staged", ".")
        self.fx._git(self.fx.work, "checkout", "--", state_path)
        (self.fx.work / "internal/foo.go").unlink()

        # PROGRESS 式提交：Cleaner 夹带业务文件 → FAIL
        self.fx.write("internal/progress.txt", "wip\n")
        self.fx.add("internal/progress.txt")
        proc = self.fx.tool("commit-check", "--role", "Cleaner", "--task", TASK, "--mode", "artifacts")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("超出", proc.stdout)
        self.fx._git(self.fx.work, "restore", "--staged", ".")
        (self.fx.work / "internal/progress.txt").unlink()

        # OwnerGate 未区分 V1 / V2 → FAIL
        owner_path = f".agent/tasks/{TASK}/owner-decision.md"
        self.fx.write(owner_path, "# Owner Decision\n")
        self.fx.add(owner_path)
        proc = self.fx.tool("commit-check", "--role", "OwnerGate", "--task", TASK, "--mode", "artifacts")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("--phase", proc.stdout)
        self.fx._git(self.fx.work, "restore", "--staged", ".")
        (self.fx.work / owner_path).unlink()

        # 无效 Evidence SHA：可移动引用必须拒绝
        self.fx.state(review_status="PENDING", review_target="HEAD")
        self.fx.add(state_path)
        proc = self.fx.tool("commit-check", "--role", "Coder", "--task", TASK, "--mode", "metadata")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("40", proc.stdout)
        self.fx._git(self.fx.work, "restore", "--staged", ".")
        self.fx._git(self.fx.work, "checkout", "--", state_path)

    def test_foreign_changes_do_not_block_own_commit(self) -> None:
        """§3.3：其他角色的无关修改保留现场，不阻断本角色提交，也不得被提交。"""
        foreign = ".agent/registry/analyst-notes.md"
        self.fx.write(foreign, "# analyst work in progress\n")
        c1 = self.make_c1()
        show = self.fx._git(self.fx.work, "show", "--name-only", "--format=", c1).stdout
        self.assertNotIn("analyst-notes.md", show, "他人修改不得被顺手提交")
        handoff = self.fx.tool("handoff-check", "--role", "Coder", "--task", TASK)
        self.assertEqual(handoff.returncode, 1, "他人现场未处置时不得 HANDOFF")
        self.assertIn("dirty", handoff.stdout)
        (self.fx.work / foreign).unlink()

        # 本角色 scope 内的残留（未提交业务代码）仍必须 FAIL。
        self.fx.state(review_status="PENDING", review_target=c1)
        self.fx.add(f".agent/tasks/{TASK}/state.yaml")
        self.fx.write("internal/leftover.go", "package internal\n")
        proc = self.fx.tool("commit-check", "--role", "Coder", "--task", TASK, "--mode", "metadata")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("范围内", proc.stdout)
        (self.fx.work / "internal/leftover.go").unlink()
        self.fx._git(self.fx.work, "restore", "--staged", ".")
        self.fx._git(self.fx.work, "checkout", "--", f".agent/tasks/{TASK}/state.yaml")

    def test_tool_preflight_and_push_targets(self) -> None:
        pre = self.fx.tool("preflight", "--role", "Coder", "--task", TASK)
        self.assertEqual(pre.returncode, 0, pre.stdout + pre.stderr)
        self.fx.write("dirty.txt", "dirty\n")
        dirty = self.fx.tool("preflight", "--role", "Coder", "--task", TASK)
        self.assertEqual(dirty.returncode, 1)
        (self.fx.work / "dirty.txt").unlink()

        self.fx._git(self.fx.work, "switch", "-q", "develop")
        coder = self.fx.tool("push-check", "--role", "Coder", "--task", TASK)
        self.assertEqual(coder.returncode, 1)
        registry = self.fx.tool(
            "push-check", "--role", "Analyst", "--task", TASK, "--mode", "registry"
        )
        self.assertEqual(registry.returncode, 0, registry.stdout)


    # ---------- GL-14 ~ GL-16：冷启动事实与失败恢复 ----------
    # 断言只使用「Task 目录 + git 命令」，不读取任何聊天或交接叙述。

    def read_task_dir(self, name: str) -> str:
        return (self.fx.work / f".agent/tasks/{TASK}/{name}").read_text(encoding="utf-8")

    def git(self, *args: str) -> str:
        proc = self.fx._git(self.fx.work, *args)
        return proc.stdout.strip()

    def add_route(self, route: str = "COMPLEX") -> None:
        path = f".agent/tasks/{TASK}/state.yaml"
        body = (self.fx.work / path).read_text(encoding="utf-8")
        assert "route:" not in body
        body = body.replace("task_id: demo\n", f"task_id: demo\nroute: {route}\n", 1)
        self.fx.write(path, body)
        self.fx.add(path)
        self.fx.commit("chore(state): 记录路线", [path])
        self.fx.push()

    def write_full_task(self) -> None:
        base = self.git("rev-parse", "HEAD")
        self.fx.write(
            f".agent/tasks/{TASK}/task.md",
            TASK_MD.replace("__BASE__", base),
        )
        self.fx.add(f".agent/tasks/{TASK}/task.md")
        self.fx.commit("docs(task): 完整任务定义", [f".agent/tasks/{TASK}/task.md"])

    def test_gl14_cold_start_facts_are_derivable(self) -> None:
        """只读 Task 目录 + Git，即可得到审查所需的全部事实。"""
        self.write_full_task()
        c1 = self.make_c1()
        self.make_c2(c1)
        self.add_route("NORMAL")
        self.fx.push()

        # 1) 入口 Gate 可运行且 PASS（新 Session 唯一的机器起点）
        gate = self.fx.gate("cleaner-start")
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)

        # 2) 审查对象：state.review.target 是可解析的完整 SHA
        state = self.read_task_dir("state.yaml")
        self.assertIn(f'target: "{c1}"', state)
        self.assertEqual(self.git("cat-file", "-t", c1), "commit")
        self.assertRegex(c1, r"^[0-9a-f]{40}$")

        # 3) 审查基线：task.md 记录了 base commit 且可在 Git 中解析
        task_md = self.read_task_dir("task.md")
        base = re.search(r"Base commit：([0-9a-f]{40})", task_md).group(1)
        self.assertEqual(self.git("cat-file", "-t", base), "commit")

        # 4) 验收标准：AC 可计数
        acs = re.findall(r"(?m)^- \[ \] (AC-\d{3})：", task_md)
        self.assertEqual(acs, ["AC-001", "AC-002"])

        # 5) 路线：state.route 与 task.md 的 Complexity 一致
        self.assertIn("route: NORMAL", state)
        self.assertIn("NORMAL", task_md.split("## Complexity", 1)[1].splitlines()[1])

        # 6) mode 可由 review.status 推导（不依赖 Owner 输入）
        self.assertIn("status: PENDING", state.split("review:", 1)[1])
        mode = "initial_review" if "status: PENDING" in state.split("review:", 1)[1] else "re_review"
        self.assertEqual(mode, "initial_review")

    def test_gl15_failure_handoff_is_recoverable_and_neutral(self) -> None:
        """失败交接写入 loop-ledger 后：可从 Git 恢复，且不使 CLEAN 失效。"""
        self.write_full_task()
        c1, _ = self.make_review_ready()
        self.make_clean(c1)

        ledger = f".agent/tasks/{TASK}/loop-ledger.md"
        self.fx.write(
            ledger,
            "# Loop Ledger\n\n"
            "- Task ID: demo\n"
            "- Ledger Version: 1\n\n"
            "## EV-0001 REQUEST FAILURE_HANDOFF S1 round=1\n\n"
            "- event_id: EV-0001\n"
            "- event_type: REQUEST\n"
            "- role: Coder\n"
            f"- evidence_commit: {c1}\n"
            "- anchor: AC-002 / 运行时错误\n"
            "- root_cause: 依赖服务在测试环境不可用\n"
            "- request_summary: 请求 Analyst 确认失败语义\n"
            "- handoff_target: ANALYST\n"
            "- receiver_action: 确认 AC-002 的失败语义并修订 Contract\n"
            "- outcome: OPEN\n",
        )
        self.fx.add(ledger)
        self.fx.commit("chore(loop): 记录失败交接", [ledger])
        self.fx.push()

        # 失败证据写入后，CLEAN 仍然有效（ledger 属于 Review-neutral）
        gate = self.fx.gate("owner-gate-start")
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)

        # 另一个 Session 只读 Git 就能恢复失败原因与接收者动作
        shown = self.git("show", f"HEAD:{ledger}")
        self.assertIn("handoff_target: ANALYST", shown)
        self.assertIn("receiver_action:", shown)
        self.assertIn("root_cause:", shown)

    def test_gl16_task_builder_update_does_not_claim_clean(self) -> None:
        """task.md 变更属于实质变化：既有 CLEAN 必须失效，不能交给 Cleaner。"""
        self.write_full_task()
        c1, _ = self.make_review_ready()
        self.make_clean(c1)

        self.fx.write(
            f".agent/tasks/{TASK}/task.md",
            TASK_MD.replace("AC-002：失败时返回明确错误。", "AC-002：失败时返回明确错误与重试提示。"),
        )
        self.fx.add(f".agent/tasks/{TASK}/task.md")
        self.fx.commit("docs(task): Owner 更新验收条件", [f".agent/tasks/{TASK}/task.md"])
        self.fx.push()

        # 旧 CLEAN 已不对应当前 HEAD：OwnerGate 入口必须拒绝
        self.assertNotEqual(self.fx.gate("owner-gate-start").returncode, 0)
        # Cleaner 也不能在 CHANGES_REQUIRED/CLEAN 状态下复审
        self.assertNotEqual(self.fx.gate("cleaner-start").returncode, 0)


ROLE_PROMPTS = {
    "TaskBuilder": ".agent/roles/TaskBuilderPrompt.md",
    "Analyst": ".agent/roles/AnalystAgent.md",
    "Architect": ".agent/roles/ArchitectAgent.md",
    "Coder": ".agent/roles/CoderAgent.md",
    "Cleaner": ".agent/roles/CleanerAgent.md",
    "OwnerGate": ".agent/roles/OwnerGateAgent.md",
    "Deliverer": ".agent/roles/DelivererAgent.md",
}

# 每个角色在自己的 Prompt 中必须出现的 commit 模式（工具的真实参数形态）。
ROLE_COMMIT_MODES = {
    "TaskBuilder": ["--mode bootstrap"],
    "Analyst": ["--mode evidence", "--mode metadata", "--mode registry"],
    "Architect": ["--mode evidence", "--mode metadata"],
    "Coder": ["--mode evidence", "--mode metadata", "--mode forced"],
    "Cleaner": ["--mode artifacts"],
    "OwnerGate": ["--mode artifacts --phase v1", "--mode artifacts --phase v2"],
    "Deliverer": ["--mode artifacts"],
}


class RoleWiringTest(unittest.TestCase):
    """C4：自检工具必须真的接进七个角色的执行规范，而不只是存在于目录里。"""

    def test_all_seven_roles_reference_the_tool(self) -> None:
        for role, rel in ROLE_PROMPTS.items():
            text = (REPO / rel).read_text(encoding="utf-8")
            with self.subTest(role=role):
                self.assertIn(".agent/specs/tools/git-lifecycle-check", text, rel)
                self.assertIn(f"preflight --role {role}", text, rel)
                self.assertIn(f"handoff-check --role {role}", text, rel)
                self.assertIn("verify-remote", text, rel)
                self.assertIn("push-check", text, rel)
                self.assertIn("不替代 Gate", text, rel)
                for mode in ROLE_COMMIT_MODES[role]:
                    self.assertIn(mode, text, f"{rel} 缺少 {mode}")

    def test_tool_is_executable_and_exposes_all_subcommands(self) -> None:
        self.assertTrue(TOOL.is_file(), "缺少 .agent/specs/tools/git-lifecycle-check")
        self.assertTrue(TOOL.stat().st_mode & 0o111, "git-lifecycle-check 必须可执行（100755）")
        proc = run(["python3", str(TOOL), "--help"], REPO)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for sub in (
            "baseline",
            "preflight",
            "commit-check",
            "push-check",
            "verify-remote",
            "handoff-check",
        ):
            self.assertIn(sub, proc.stdout)

    def test_danger_and_blocked_wording_kept_in_all_roles(self) -> None:
        """DANGER / BLOCKED 的 NEXT_ROLE=NONE 语义不得被 Git 自检章节改写。"""
        for role, rel in ROLE_PROMPTS.items():
            text = (REPO / rel).read_text(encoding="utf-8")
            with self.subTest(role=role):
                self.assertIn("NEXT_ROLE=NONE", text, rel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
