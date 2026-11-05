#!/usr/bin/env python3
"""Workflow V4 adoption tooling tests.

覆盖：dist / inspect / install / upgrade(B-L-N) / rollback / verify / check。
所有测试在临时 Git 仓库执行，不触碰真实工作区。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / ".agent/adoption/workflow_adoption.py"

spec = importlib.util.spec_from_file_location("workflow_adoption", MODULE_PATH)
wa = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(wa)


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr}")
    return proc.stdout.strip()


FRAMEWORK_FILES = {
    ".agent/VERSION": "4.0.0\n",
    ".agent/README.md": "# V4 runtime\n\n" + ("runtime docs\n" * 20),
    ".agent/specs/WorkflowAgentDirective.md": "# Directive\n\n" + ("spec body\n" * 40),
    ".agent/specs/AgentCollaborationSpecification.md": "# Collaboration\n\n" + ("spec body\n" * 40),
    ".agent/specs/Five-AgentResponsibilityBoundary.md": "# Boundary\n\n" + ("spec body\n" * 40),
    ".agent/specs/WorkflowHandoffContract.md": "# Handoff\n\n" + ("spec body\n" * 40),
    ".agent/specs/WorkflowGitLifecycle.md": "# Git\n\n" + ("spec body\n" * 40),
    ".agent/specs/WorkflowLoopCircuitBreaker.md": "# Breaker\n\n" + ("spec body\n" * 40),
    ".agent/specs/WorkflowAdoption.md": "# Adoption\n\n" + ("spec body\n" * 40),
    ".agent/roles/TaskBuilderPrompt.md": "# TaskBuilder\n\n## 角色\n",
    ".agent/roles/AnalystAgent.md": "# Analyst\n\n## 角色\n",
    ".agent/roles/ArchitectAgent.md": "# Architect\n\n## 角色\n",
    ".agent/roles/CoderAgent.md": "# Coder\n\n## 角色\n",
    ".agent/roles/CleanerAgent.md": "# Cleaner\n\n## 角色\n",
    ".agent/roles/OwnerGateAgent.md": "# OwnerGate\n\n## 角色\n",
    ".agent/roles/DelivererAgent.md": "# Deliverer\n\n## 角色\n",
    ".agent/templates/task/task.md": "# Task\n",
    ".agent/templates/task/plan.md": "# Plan\n\n## Next Action\n",
    ".agent/templates/task/handoff.md": "# Handoff\n",
    ".agent/templates/task/findings.md": "# Findings\n",
    ".agent/templates/task/delivery.md": "# Delivery\n",
    ".agent/templates/task/decisions.md": "# Decisions\n",
    ".agent/templates/upstream.json.template": "{}\n",
    ".agent/tools/git-facts": "#!/usr/bin/env python3\nprint('ok')\n",
    "README.md": "# framework\n",
    "PACKAGE_MANIFEST.md": "# manifest\n",
    ".cnb.yml": "v4.0.0:\n  tag_push: []\n",
    ".gitignore": "dist/\n",
}

HISTORY_FILES = {
    "docs/history/v3.4.0/README.md": "# history\n",
    "docs/history/v3.4.0/specs/WorkflowAgentDirective.md": "# v3\n",
    "docs/history/v3.4.0/roles/CoderAgent.md": "# v3\n",
}


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    tool = root / ".agent/tools/git-facts"
    if tool.exists():
        tool.chmod(0o755)


def init_repo(root: Path, files: dict[str, str], message: str = "init") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    write_tree(root, files)
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return root


def commit_all(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


class FrameworkRepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.fw = init_repo(self.base / "framework", dict(FRAMEWORK_FILES))
        self.host = init_repo(self.base / "host", {"app.txt": "business\n", "AGENTS.md": "host rules\n"})

    def build(self, output: str = "dist") -> dict:
        return wa.build_dist(self.fw, self.base / output)

    def install(self) -> dict:
        package = self.build()
        load = wa.load_package(Path(package["archive"]), package["sha256"])
        plan = wa.plan_install(self.host, load)
        return wa.execute_transaction(self.host, plan, load, "main", "owner")


class DistTests(FrameworkRepoCase):
    def test_dist_builds_manifest_and_checksum(self) -> None:
        result = self.build()
        archive = Path(result["archive"])
        self.assertTrue(archive.is_file())
        self.assertEqual(result["version"], "4.0.0")
        self.assertEqual(result["commit"], git(self.fw, "rev-parse", "HEAD"))

        package = wa.load_package(archive, result["sha256"])
        manifest = package["manifest"]
        self.assertEqual(manifest["kind"], wa.KIND)
        self.assertEqual(manifest["release"]["tag"], "")
        paths = {entry["path"] for entry in manifest["files"]}
        self.assertIn(".agent/VERSION", paths)
        self.assertIn("README.md", paths)
        self.assertNotIn(".agent/tasks/x/task.md", paths)
        self.assertTrue(any(e["install"] == "managed" for e in manifest["files"]))
        self.assertTrue(any(e["install"] == "package_only" for e in manifest["files"]))

    def test_dist_is_deterministic(self) -> None:
        first = self.build("dist1")
        # 跨越时间边界：归档中不得含 generated_at 等易变字段。
        import time as _time

        _time.sleep(1.1)
        second = self.build("dist2")
        self.assertEqual(first["sha256"], second["sha256"])

    def test_manifest_has_no_volatile_fields(self) -> None:
        result = self.build()
        package = wa.load_package(Path(result["archive"]))
        manifest = package["manifest"]
        self.assertNotIn("generated_at", manifest)
        self.assertEqual(manifest["release"], {"tag": "", "url": ""})

    def test_dist_rejects_dirty_tree(self) -> None:
        (self.fw / ".agent/README.md").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(wa.AdoptionError) as ctx:
            self.build()
        self.assertEqual(ctx.exception.code, "SOURCE_DIRTY")

    def test_dist_rejects_unclassified_file(self) -> None:
        (self.fw / "unknown.txt").write_text("x\n", encoding="utf-8")
        git(self.fw, "add", "unknown.txt")
        git(self.fw, "commit", "-q", "-m", "unknown")
        with self.assertRaises(wa.AdoptionError) as ctx:
            self.build()
        self.assertEqual(ctx.exception.code, "UNCLASSIFIED_FILES")

    def test_dist_rejects_host_task_data(self) -> None:
        (self.fw / ".agent/tasks/t1").mkdir(parents=True)
        (self.fw / ".agent/tasks/t1/task.md").write_text("# t\n", encoding="utf-8")
        commit_all(self.fw, "task data")
        result = self.build()
        package = wa.load_package(Path(result["archive"]))
        self.assertFalse(any(p.startswith(".agent/tasks") for p in package["files"]))

    def test_inspect_rejects_bad_checksum(self) -> None:
        result = self.build()
        with self.assertRaises(wa.AdoptionError) as ctx:
            wa.load_package(Path(result["archive"]), "0" * 64)
        self.assertEqual(ctx.exception.code, "CHECKSUM_MISMATCH")


class InstallTests(FrameworkRepoCase):
    def test_install_writes_managed_files_and_provenance(self) -> None:
        result = self.install()
        self.assertTrue((self.host / ".agent/VERSION").is_file())
        self.assertTrue((self.host / ".agent/specs/WorkflowAgentDirective.md").is_file())
        self.assertFalse((self.host / "README.md").exists())
        self.assertEqual((self.host / "AGENTS.md").read_text(encoding="utf-8"), "host rules\n")
        self.assertTrue((self.host / ".agent/upstream.json").is_file())
        self.assertFalse((self.host / "README.md").exists())
        self.assertEqual(result["plan"]["action"], "install")

        upstream = json.loads((self.host / ".agent/upstream.json").read_text(encoding="utf-8"))
        self.assertEqual(upstream["framework_version"], "4.0.0")
        self.assertNotIn("README.md", upstream["files"])
        self.assertEqual(wa.verify_target(self.host)["status"], "PASS")

    def test_install_refuses_conflicting_file(self) -> None:
        target = self.host / ".agent/roles/CoderAgent.md"
        target.parent.mkdir(parents=True)
        target.write_text("local edit\n", encoding="utf-8")
        package = self.build()
        load = wa.load_package(Path(package["archive"]), package["sha256"])
        with self.assertRaises(wa.AdoptionError) as ctx:
            wa.plan_install(self.host, load)
        self.assertEqual(ctx.exception.code, "ALREADY_INSTALLED")

    def test_install_does_not_copy_package_only(self) -> None:
        self.install()
        self.assertFalse((self.host / "docs").exists())
        self.assertFalse((self.host / ".cnb.yml").exists())

    def test_verify_detects_tampering(self) -> None:
        self.install()
        (self.host / ".agent/roles/CoderAgent.md").write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(wa.AdoptionError) as ctx:
            wa.verify_target(self.host)
        self.assertEqual(ctx.exception.code, "VERIFY_FAILED")

    def test_verify_detects_legacy_engine(self) -> None:
        self.install()
        (self.host / ".agent/engine").mkdir(parents=True)
        with self.assertRaises(wa.AdoptionError) as ctx:
            wa.verify_target(self.host)
        self.assertEqual(ctx.exception.code, "VERIFY_FAILED")
        self.assertTrue(any("engine" in p["path"] for p in ctx.exception.detail["problems"]))


class UpgradeTests(FrameworkRepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.install()
        self.installed_commit = git(self.fw, "rev-parse", "HEAD")

    def new_package(self, updates: dict[str, str], message: str = "upstream change") -> dict:
        write_tree(self.fw, updates)
        commit_all(self.fw, message)
        return wa.build_dist(self.fw, self.base / f"dist-{git(self.fw, 'rev-parse', '--short', 'HEAD')}")

    def test_upgrade_replaces_unmodified_files(self) -> None:
        package = self.new_package({".agent/roles/CoderAgent.md": "# Coder\n\n## 角色\n\nv2\n"})
        load = wa.load_package(Path(package["archive"]), package["sha256"])
        plan = wa.plan_upgrade(self.host, load, set())
        self.assertIn(".agent/roles/CoderAgent.md", plan["overwrite"])
        wa.execute_transaction(self.host, plan, load, "main", "owner")
        self.assertIn("v2", (self.host / ".agent/roles/CoderAgent.md").read_text(encoding="utf-8"))
        self.assertEqual(wa.verify_target(self.host)["status"], "PASS")

    def test_upgrade_keeps_local_change_when_upstream_unchanged(self) -> None:
        (self.host / ".agent/roles/AnalystAgent.md").write_text("local tweak\n", encoding="utf-8")
        package = self.new_package({".agent/roles/CoderAgent.md": "# Coder\n\n## 角色\n\nv2\n"})
        load = wa.load_package(Path(package["archive"]), package["sha256"])
        plan = wa.plan_upgrade(self.host, load, set())
        self.assertIn(".agent/roles/AnalystAgent.md", plan["kept_local"])
        wa.execute_transaction(self.host, plan, load, "main", "owner")
        self.assertEqual(
            (self.host / ".agent/roles/AnalystAgent.md").read_text(encoding="utf-8"), "local tweak\n"
        )

    def test_upgrade_conflict_writes_nothing(self) -> None:
        (self.host / ".agent/roles/CoderAgent.md").write_text("local edit\n", encoding="utf-8")
        package = self.new_package({".agent/roles/CoderAgent.md": "# Coder\n\n## 角色\n\nupstream v2\n"})
        load = wa.load_package(Path(package["archive"]), package["sha256"])
        before = (self.host / ".agent/roles/CoderAgent.md").read_text(encoding="utf-8")
        with self.assertRaises(wa.AdoptionError) as ctx:
            wa.plan_upgrade(self.host, load, set())
        self.assertEqual(ctx.exception.code, "CONFLICT")
        self.assertEqual((self.host / ".agent/roles/CoderAgent.md").read_text(encoding="utf-8"), before)

    def test_upgrade_keep_local_option(self) -> None:
        (self.host / ".agent/roles/CoderAgent.md").write_text("local edit\n", encoding="utf-8")
        package = self.new_package({".agent/roles/CoderAgent.md": "# Coder\n\n## 角色\n\nupstream v2\n"})
        load = wa.load_package(Path(package["archive"]), package["sha256"])
        plan = wa.plan_upgrade(self.host, load, {".agent/roles/CoderAgent.md"})
        wa.execute_transaction(self.host, plan, load, "main", "owner")
        self.assertEqual(
            (self.host / ".agent/roles/CoderAgent.md").read_text(encoding="utf-8"), "local edit\n"
        )

    def test_upgrade_requires_base(self) -> None:
        (self.host / ".agent/upstream.json").unlink()
        package = self.new_package({".agent/roles/CoderAgent.md": "# Coder\n\n## 角色\n\nv2\n"})
        load = wa.load_package(Path(package["archive"]), package["sha256"])
        with self.assertRaises(wa.AdoptionError) as ctx:
            wa.plan_upgrade(self.host, load, set())
        self.assertEqual(ctx.exception.code, "BASE_UNKNOWN")

    def test_upgrade_removes_file_deleted_upstream(self) -> None:
        (self.fw / ".agent/templates/upstream.json.template").unlink()
        commit_all(self.fw, "drop template")
        package = wa.build_dist(self.fw, self.base / "dist-drop")
        load = wa.load_package(Path(package["archive"]), package["sha256"])
        plan = wa.plan_upgrade(self.host, load, set())
        self.assertIn(".agent/templates/upstream.json.template", plan["delete"])
        wa.execute_transaction(self.host, plan, load, "main", "owner")
        self.assertFalse((self.host / ".agent/templates/upstream.json.template").exists())

    def test_rollback_restores_previous_install(self) -> None:
        package = self.new_package({".agent/roles/CoderAgent.md": "# Coder\n\n## 角色\n\nv2\n"})
        load = wa.load_package(Path(package["archive"]), package["sha256"])
        plan = wa.plan_upgrade(self.host, load, set())
        result = wa.execute_transaction(self.host, plan, load, "main", "owner")
        txn = self.host / ".agent/.transactions" / result["transaction"]

        wa.rollback_transaction(self.host, txn)
        self.assertNotIn("v2", (self.host / ".agent/roles/CoderAgent.md").read_text(encoding="utf-8"))
        upstream = json.loads((self.host / ".agent/upstream.json").read_text(encoding="utf-8"))
        self.assertEqual(upstream["source_commit"], self.installed_commit)


class CheckTests(FrameworkRepoCase):
    def test_check_passes_on_complete_repo(self) -> None:
        write_tree(self.fw, HISTORY_FILES)
        commit_all(self.fw, "history")
        result = wa.check_repository(self.fw)
        self.assertEqual(result["status"], "PASS", result["failures"])

    def test_check_detects_missing_spec(self) -> None:
        write_tree(self.fw, HISTORY_FILES)
        (self.fw / ".agent/specs/WorkflowAdoption.md").unlink()
        commit_all(self.fw, "remove spec")
        result = wa.check_repository(self.fw)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("WorkflowAdoption" in f["error"] for f in result["failures"]))

    def test_check_detects_legacy_state_file(self) -> None:
        write_tree(self.fw, HISTORY_FILES)
        (self.fw / ".agent/tasks/t1").mkdir(parents=True)
        (self.fw / ".agent/tasks/t1/state.yaml").write_text("schema_version: 3\n", encoding="utf-8")
        commit_all(self.fw, "legacy state")
        result = wa.check_repository(self.fw)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("state.yaml" in f["error"] for f in result["failures"]))

    def test_check_detects_forbidden_term_in_spec(self) -> None:
        write_tree(self.fw, HISTORY_FILES)
        path = self.fw / ".agent/specs/WorkflowAgentDirective.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n运行 workflow-check gate\n", encoding="utf-8")
        commit_all(self.fw, "term")
        result = wa.check_repository(self.fw)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("workflow-check" in f["error"] for f in result["failures"]))

    def test_check_detects_non_executable_tool(self) -> None:
        write_tree(self.fw, HISTORY_FILES)
        (self.fw / ".agent/tools/git-facts").chmod(0o644)
        result = wa.check_repository(self.fw)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("不可执行" in f["error"] for f in result["failures"]))


class RealRepositoryTests(unittest.TestCase):
    def test_real_repository_is_consistent(self) -> None:
        result = wa.check_repository(REPO_ROOT)
        self.assertEqual(result["status"], "PASS", result["failures"])

    def test_real_repository_has_no_engine(self) -> None:
        self.assertFalse((REPO_ROOT / ".agent/engine").exists())
        self.assertFalse((REPO_ROOT / ".agent/bin").exists())
        self.assertFalse((REPO_ROOT / ".agent/specs/WorkflowRouteSelection.md").exists())

    def test_history_archive_present(self) -> None:
        history = REPO_ROOT / "docs/history/v3.4.0"
        self.assertTrue((history / "README.md").is_file())
        self.assertEqual(len(list((history / "specs").glob("*.md"))), 8)
        self.assertEqual(len(list((history / "roles").glob("*.md"))), 7)
        self.assertTrue((history / "tasks/v3.4.0-independent-session-handoff/state.yaml").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
