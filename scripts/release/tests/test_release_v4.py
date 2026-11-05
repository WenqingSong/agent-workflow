#!/usr/bin/env python3
"""Workflow V4 release tooling tests.

覆盖：guard / build / verify / verify-uploaded / repro / check-pipeline / report。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RELEASE = REPO_ROOT / "scripts/release/release.py"
ADOPTION = REPO_ROOT / ".agent/adoption/workflow_adoption.py"

FRAMEWORK_FILES = {
    ".agent/VERSION": "4.0.0\n",
    ".agent/README.md": "# V4\n\n" + ("runtime\n" * 20),
    ".agent/specs/WorkflowAgentDirective.md": "# Directive\n\n" + ("spec\n" * 40),
    ".agent/specs/AgentCollaborationSpecification.md": "# Collaboration\n\n" + ("spec\n" * 40),
    ".agent/specs/WorkflowHandoffContract.md": "# Handoff\n\n" + ("spec\n" * 40),
    ".agent/roles/CoderAgent.md": "# Coder\n\n## 角色\n",
    ".agent/templates/task/task.md": "# Task\n",
    ".agent/templates/task/plan.md": "# Plan\n\n## Next Action\n",
    ".agent/templates/task/handoff.md": "# Handoff\n",
    ".agent/templates/task/findings.md": "# Findings\n",
    ".agent/templates/task/delivery.md": "# Delivery\n",
    ".agent/templates/task/decisions.md": "# Decisions\n",
    "README.md": "# framework\n",
    "PACKAGE_MANIFEST.md": "# manifest\n",
    ".cnb.yml": "v4.0.0:\n  tag_push: []\n",
    "docs/design/agent-workflow.md": "# design\n",
    "docs/releases/v4.0.0.md": "# Workflow V4.0.0\n\nRelease notes.\n",
    ".gitignore": "dist/\n__pycache__/\n*.pyc\n",
}


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr}")
    return proc.stdout.strip()


def run_release(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["python3", str(RELEASE), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


class ReleaseRepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()

        for rel, content in FRAMEWORK_FILES.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        adoption = self.repo / ".agent/adoption/workflow_adoption.py"
        adoption.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ADOPTION, adoption)

        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "commit.gpgsign", "false")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        git(self.repo, "tag", "-a", "v4.0.0", "-m", "release v4.0.0")
        self.commit = git(self.repo, "rev-parse", "HEAD")
        self.out = self.root / "dist"

    def build(self) -> dict:
        code, output = run_release(
            "build", "--repo", str(self.repo), "--output", str(self.out), "--tag", "v4.0.0", "--json"
        )
        self.assertEqual(code, 0, output)
        return json.loads(output)


class GuardTests(ReleaseRepoCase):
    def test_guard_passes_on_annotated_tag(self) -> None:
        code, output = run_release(
            "guard", "--repo", str(self.repo), "--tag", "v4.0.0",
            "--expected-commit", self.commit, "--json",
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)["status"], "PASS")

    def test_guard_rejects_lightweight_tag(self) -> None:
        git(self.repo, "tag", "-d", "v4.0.0")
        git(self.repo, "tag", "v4.0.0")
        code, output = run_release("guard", "--repo", str(self.repo), "--tag", "v4.0.0", "--json")
        self.assertEqual(code, 2, output)
        self.assertIn("TAG_NOT_ANNOTATED", output)

    def test_guard_rejects_version_mismatch(self) -> None:
        (self.repo / ".agent/VERSION").write_text("4.0.1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "bump")
        git(self.repo, "tag", "-f", "-a", "v4.0.0", "-m", "move")
        code, output = run_release("guard", "--repo", str(self.repo), "--tag", "v4.0.0", "--json")
        self.assertEqual(code, 2, output)
        self.assertIn("VERSION_MISMATCH", output)

    def test_guard_rejects_dirty_tree(self) -> None:
        (self.repo / "README.md").write_text("dirty\n", encoding="utf-8")
        code, output = run_release("guard", "--repo", str(self.repo), "--tag", "v4.0.0", "--json")
        self.assertEqual(code, 2, output)
        self.assertIn("WORKTREE_DIRTY", output)

    def test_guard_rejects_wrong_expected_commit(self) -> None:
        code, output = run_release(
            "guard", "--repo", str(self.repo), "--tag", "v4.0.0", "--expected-commit", "0" * 40, "--json"
        )
        self.assertEqual(code, 2, output)
        self.assertIn("COMMIT_MISMATCH", output)


class BuildVerifyTests(ReleaseRepoCase):
    def test_build_produces_five_assets(self) -> None:
        result = self.build()
        self.assertEqual(result["status"], "PASS")
        for name in (
            "agent-workflow-src-4.0.0.tar.gz",
            "agent-workflow-src-4.0.0.tar.gz.sha256",
            "agent-workflow-release-notes-4.0.0.md",
            "SHA256SUMS",
            "version.json",
        ):
            self.assertTrue((self.out / name).is_file(), name)
        self.assertFalse((self.out / "workflow-check_4.0.0_linux_amd64").exists())

    def test_version_json_binds_commit_and_has_no_binary(self) -> None:
        self.build()
        doc = json.loads((self.out / "version.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["source_commit"], self.commit)
        self.assertEqual(doc["tag"], "v4.0.0")
        self.assertEqual(doc["binaries"], [])
        self.assertEqual(doc["published_status"], "NOT_RELEASED")

    def test_verify_passes(self) -> None:
        self.build()
        code, output = run_release(
            "verify", "--dir", str(self.out), "--repo", str(self.repo),
            "--version", "4.0.0", "--tag", "v4.0.0", "--commit", self.commit, "--json",
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)["status"], "PASS")

    def test_verify_rejects_wrong_commit(self) -> None:
        self.build()
        code, output = run_release(
            "verify", "--dir", str(self.out), "--version", "4.0.0", "--commit", "0" * 40, "--json"
        )
        self.assertEqual(code, 2, output)
        self.assertIn("COMMIT_MISMATCH", output)

    def test_verify_rejects_tampered_asset(self) -> None:
        self.build()
        notes = self.out / "agent-workflow-release-notes-4.0.0.md"
        notes.write_text("tampered\n", encoding="utf-8")
        code, output = run_release("verify", "--dir", str(self.out), "--version", "4.0.0", "--json")
        self.assertEqual(code, 2, output)
        self.assertIn("SUMS_MISMATCH", output)

    def test_verify_rejects_missing_asset(self) -> None:
        self.build()
        (self.out / "SHA256SUMS").unlink()
        code, output = run_release("verify", "--dir", str(self.out), "--version", "4.0.0", "--json")
        self.assertEqual(code, 2, output)
        self.assertIn("ASSET_MISSING", output)

    def test_repro_is_deterministic(self) -> None:
        code, output = run_release("repro", "--repo", str(self.repo), "--tag", "v4.0.0", "--json")
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)["status"], "PASS")

    def test_build_requires_notes(self) -> None:
        (self.repo / "docs/releases/v4.0.0.md").unlink()
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "drop notes")
        git(self.repo, "tag", "-f", "-a", "v4.0.0", "-m", "move")
        code, output = run_release(
            "build", "--repo", str(self.repo), "--output", str(self.out), "--tag", "v4.0.0", "--json"
        )
        self.assertEqual(code, 2, output)
        self.assertIn("NOTES_MISSING", output)


class UploadedTests(ReleaseRepoCase):
    def test_verify_uploaded_matches_downloads(self) -> None:
        self.build()
        report = self.root / "local.json"
        code, output = run_release(
            "verify", "--dir", str(self.out), "--version", "4.0.0",
            "--commit", self.commit, "--report", str(report), "--json",
        )
        self.assertEqual(code, 0, output)

        downloads = self.root / "downloads"
        downloads.mkdir()
        expected = json.loads(report.read_text(encoding="utf-8"))["sha256"]
        for name in expected:
            shutil.copyfile(self.out / name, downloads / name)

        code, output = run_release(
            "verify-uploaded", "--downloads", str(downloads),
            "--manifest", str(report), "--commit", self.commit, "--json",
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)["status"], "PASS")

    def test_verify_uploaded_detects_missing_download(self) -> None:
        self.build()
        report = self.root / "local.json"
        run_release(
            "verify", "--dir", str(self.out), "--version", "4.0.0",
            "--commit", self.commit, "--report", str(report), "--json",
        )
        downloads = self.root / "downloads"
        downloads.mkdir()
        code, output = run_release(
            "verify-uploaded", "--downloads", str(downloads),
            "--manifest", str(report), "--json",
        )
        self.assertEqual(code, 2, output)
        self.assertIn("DOWNLOAD_MISSING", output)


class PipelineTests(unittest.TestCase):
    def test_real_pipeline_config_is_complete(self) -> None:
        code, output = run_release(
            "check-pipeline", "--config", str(REPO_ROOT / ".cnb.yml"),
            "--repo", str(REPO_ROOT), "--tag", "v4.0.0", "--json",
        )
        self.assertEqual(code, 0, output)

    def test_pipeline_config_without_assets_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / ".cnb.yml"
            config.write_text("v4.0.0:\n  tag_push:\n    - name: x\n", encoding="utf-8")
            code, output = run_release(
                "check-pipeline", "--config", str(config), "--version", "4.0.0", "--tag", "v4.0.0", "--json"
            )
            self.assertEqual(code, 2, output)
            self.assertIn("PIPELINE_INCOMPLETE", output)


class ReportTests(unittest.TestCase):
    def test_report_requires_all_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp) / "reports"
            reports.mkdir()
            (reports / "a.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
            code, output = run_release("report", "--reports", str(reports), "--require-all", "--json")
            self.assertEqual(code, 0, output)

            (reports / "b.json").write_text(json.dumps({"status": "BLOCKED"}), encoding="utf-8")
            code, output = run_release("report", "--reports", str(reports), "--require-all", "--json")
            self.assertEqual(code, 2, output)
            self.assertIn("REPORT_FAILED", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
