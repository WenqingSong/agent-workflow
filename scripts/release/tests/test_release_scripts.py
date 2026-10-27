#!/usr/bin/env python3
"""Release 工程脚本的失败注入测试（RC-B）。

覆盖目标：任何失败都不得产生错误的 GA / latest 状态。

测试只在临时 Git 仓库与临时资产目录中进行：

- 不创建、不推送任何正式 Tag / Release；
- 不访问网络或 CNB 平台；
- 平台侧上传 / 下载用本地目录模拟，验证的是“顺序与门槛”而不是平台实现。
"""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

SOURCE_REPO = Path(__file__).resolve().parents[3]
RELEASE_TOOL = SOURCE_REPO / "scripts" / "release" / "release.py"
TAG = "v3.1.0"
VERSION = "3.1.0"
BINARIES = (
    "workflow-check_3.1.0_linux_amd64",
    "workflow-check_3.1.0_linux_arm64",
    "workflow-check_3.1.0_darwin_arm64",
    "workflow-check_3.1.0_windows_amd64.exe",
)
NOTES_ASSET = "agent-workflow-release-notes-3.1.0.md"


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "p08a",
            "GIT_AUTHOR_EMAIL": "p08a@example.invalid",
            "GIT_COMMITTER_NAME": "p08a",
            "GIT_COMMITTER_EMAIL": "p08a@example.invalid",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 失败: {proc.stderr}")
    return proc.stdout.strip()


def tool(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RELEASE_TOOL), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def make_repo(destination: Path, version: str = VERSION) -> Path:
    """用当前工作树的 tracked 文件构造一个干净的临时仓库。"""
    repo = destination / "repo"
    repo.mkdir(parents=True)
    # 同时包含已跟踪文件与尚未提交的新文件，便于在提交前运行失败注入测试。
    listing = subprocess.run(
        ["git", "-C", str(SOURCE_REPO), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    tracked = [rel for rel in listing if not rel.startswith(("dist/", "tmp.md"))]
    for rel in tracked:
        src = SOURCE_REPO / rel
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    version_file = repo / ".agent" / "VERSION"
    version_file.write_text(f"{version}\n", encoding="utf-8")
    git(repo, "init", "-q", "-b", "develop")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture")
    return repo


def annotated_tag(repo: Path, tag: str = TAG, message: str = "rc") -> str:
    git(repo, "tag", "-a", tag, "-m", message)
    return git(repo, "rev-parse", "HEAD")


def repack(archive: Path, mutate) -> None:
    """解包 -> mutate(顶层目录路径) -> 以确定性参数重新打包。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(root)
        top = next(root.iterdir())
        mutate(top)
        members: list[tuple[str, bytes, int]] = []
        for path in sorted(top.rglob("*")):
            if path.is_file():
                members.append((path.relative_to(root).as_posix(), path.read_bytes(), path.stat().st_mode & 0o777))
        with archive.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
                with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tar:
                    for name, data, mode in members:
                        info = tarfile.TarInfo(name)
                        info.size = len(data)
                        info.mtime = 0
                        info.mode = mode
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        info.type = tarfile.REGTYPE
                        tar.addfile(info, io.BytesIO(data))


class GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="p08a-guard-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_annotated_tag_passes(self) -> None:
        repo = make_repo(self.tmp)
        commit = annotated_tag(repo)
        report = self.tmp / "guard.json"
        proc = tool("guard", "--repo", str(repo), "--tag", TAG, "--report", str(report), "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["status"], "GUARD_OK")
        self.assertEqual(payload["source_commit"], commit)
        self.assertEqual(payload["version"], VERSION)
        self.assertTrue(payload["annotated"])

    def test_lightweight_tag_rejected(self) -> None:
        repo = make_repo(self.tmp)
        git(repo, "tag", TAG)
        proc = tool("guard", "--repo", str(repo), "--tag", TAG, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("TAG_NOT_ANNOTATED", proc.stdout + proc.stderr)

    def test_tag_without_version_match_rejected(self) -> None:
        repo = make_repo(self.tmp, version="3.0.0")
        annotated_tag(repo)
        proc = tool("guard", "--repo", str(repo), "--tag", TAG, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("VERSION_TAG_MISMATCH", proc.stdout + proc.stderr)

    def test_tag_name_mismatch_rejected(self) -> None:
        repo = make_repo(self.tmp)
        annotated_tag(repo, "v3.1.1")
        proc = tool("guard", "--repo", str(repo), "--tag", "v3.1.1", "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("VERSION_TAG_MISMATCH", proc.stdout + proc.stderr)

    def test_missing_tag_rejected(self) -> None:
        repo = make_repo(self.tmp)
        proc = tool("guard", "--repo", str(repo), "--tag", TAG, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("TAG_NOT_FOUND", proc.stdout + proc.stderr)

    def test_unresolvable_commit_rejected(self) -> None:
        repo = make_repo(self.tmp)
        target = repo / "blob.txt"
        target.write_text("not a commit\n", encoding="utf-8")
        blob = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "blob.txt"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        git(repo, "tag", "-a", TAG, "-m", "blob tag", blob)
        proc = tool("guard", "--repo", str(repo), "--tag", TAG, "--json")
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertTrue("COMMIT_UNRESOLVED" in combined or "VERSION_UNREADABLE" in combined, combined)

    def test_head_not_at_tag_rejected(self) -> None:
        repo = make_repo(self.tmp)
        annotated_tag(repo)
        (repo / "extra.txt").write_text("advance\n", encoding="utf-8")
        git(repo, "add", "extra.txt")
        git(repo, "commit", "-q", "-m", "advance")
        proc = tool("guard", "--repo", str(repo), "--tag", TAG, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("GUARD_FAILED", proc.stdout + proc.stderr)


class ReleaseAssetTests(unittest.TestCase):
    """构建一次真实资产集合，其余用例在其副本上做失败注入。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="p08a-assets-"))
        cls.repo = make_repo(cls.tmp / "src")
        cls.commit = annotated_tag(cls.repo)
        cls.assets = cls.tmp / "assets"
        proc = tool("build", "--repo", str(cls.repo), "--output", str(cls.assets), "--tag", TAG, "--json")
        if proc.returncode != 0:
            raise AssertionError("构建失败: " + proc.stdout + proc.stderr)
        cls.version_json = json.loads((cls.assets / "version.json").read_text())
        cls.dist_name = cls.version_json["distribution"]["file"]

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy_assets(self) -> Path:
        target = Path(tempfile.mkdtemp(prefix="p08a-copy-")) / "assets"
        shutil.copytree(self.assets, target)
        return target

    def test_build_outputs_full_asset_set(self) -> None:
        names = sorted(p.name for p in self.assets.iterdir() if p.is_file())
        expected = sorted([*BINARIES, self.dist_name, f"{self.dist_name}.sha256", NOTES_ASSET, "SHA256SUMS", "version.json"])
        self.assertEqual(names, expected)
        self.assertEqual(self.version_json["published_status"], "NOT_RELEASED")
        self.assertEqual(self.version_json["source_commit"], self.commit)
        self.assertEqual(self.version_json["distribution"]["release_tag"], "")
        self.assertEqual(self.version_json["distribution"]["release_url"], "")

    def test_verify_assets_passes_and_checksums_are_real(self) -> None:
        proc = tool("verify-assets", "--dir", str(self.assets), "--version", VERSION, "--commit", self.commit, "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(payload["checksums_verified"])
        runtime = [p for p in payload["platforms"] if p["goos"] == "linux" and p["goarch"] == "amd64"]
        self.assertEqual(len(runtime), 1)
        self.assertTrue(runtime[0]["runtime_executed"])
        for platform in payload["platforms"]:
            if platform["goos"] == "linux" and platform["goarch"] == "amd64":
                continue
            self.assertEqual(platform["verification_level"], "static")
            self.assertIsNone(platform["runtime_executed"])

    def test_missing_binary_fails(self) -> None:
        assets = self.copy_assets()
        (assets / BINARIES[1]).unlink()
        proc = tool("verify-assets", "--dir", str(assets), "--version", VERSION, "--commit", self.commit, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("缺少资产", proc.stdout)

    def test_tampered_binary_fails(self) -> None:
        assets = self.copy_assets()
        with (assets / BINARIES[0]).open("ab") as fh:
            fh.write(b"tampered")
        proc = tool("verify-assets", "--dir", str(assets), "--version", VERSION, "--commit", self.commit, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("SHA256", proc.stdout)

    def test_tampered_checksums_fails(self) -> None:
        assets = self.copy_assets()
        lines = (assets / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        digest, name = lines[0].split("  ", 1)
        lines[0] = f"{'0' * 64}  {name}"
        (assets / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = tool("verify-assets", "--dir", str(assets), "--version", VERSION, "--commit", self.commit, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("sha256sum -c SHA256SUMS 失败", proc.stdout)

    def test_version_commit_mismatch_fails(self) -> None:
        assets = self.copy_assets()
        payload = json.loads((assets / "version.json").read_text())
        payload["source_commit"] = "0" * 40
        (assets / "version.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        proc = tool("verify-assets", "--dir", str(assets), "--version", VERSION, "--commit", self.commit, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("source_commit", proc.stdout)

    def test_dist_missing_architect_fails(self) -> None:
        assets = self.copy_assets()
        archive = assets / self.dist_name

        def mutate(top: Path) -> None:
            (top / ".agent" / "roles" / "ArchitectAgent.md").unlink()

        repack(archive, mutate)
        proc = tool("verify-assets", "--dir", str(assets), "--version", VERSION, "--commit", self.commit, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ArchitectAgent.md", proc.stdout)

    def test_manifest_member_mismatch_fails(self) -> None:
        assets = self.copy_assets()
        archive = assets / self.dist_name

        def mutate(top: Path) -> None:
            manifest_path = top / "MANIFEST.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            removed = payload["files"].pop()
            payload["install"]["managed"] = [p for p in payload["install"]["managed"] if p != removed["path"]]
            manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

        repack(archive, mutate)
        proc = tool("verify-assets", "--dir", str(assets), "--version", VERSION, "--commit", self.commit, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("MANIFEST", proc.stdout)

    def test_manifest_release_tag_must_stay_empty(self) -> None:
        assets = self.copy_assets()
        archive = assets / self.dist_name

        def mutate(top: Path) -> None:
            manifest_path = top / "MANIFEST.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["release"]["tag"] = TAG
            payload["release"]["url"] = "https://example.invalid/release"
            manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

        repack(archive, mutate)
        proc = tool("verify-assets", "--dir", str(assets), "--version", VERSION, "--commit", self.commit, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("release.tag", proc.stdout)

    def test_forbidden_host_data_in_dist_fails(self) -> None:
        assets = self.copy_assets()
        archive = assets / self.dist_name

        def mutate(top: Path) -> None:
            (top / ".agent").mkdir(exist_ok=True)
            (top / ".agent" / "workflow.yaml").write_text("schema_version: 1\n", encoding="utf-8")

        repack(archive, mutate)
        proc = tool("verify-assets", "--dir", str(assets), "--version", VERSION, "--commit", self.commit, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(".agent/workflow.yaml", proc.stdout)

    def test_repro_json_is_clean_and_reproducible(self) -> None:
        proc = tool("repro", "--repo", str(self.repo), "--tag", TAG, "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)  # --json 必须是纯净 JSON，不能被子构建输出污染
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["artifacts"], 7)
        self.assertEqual(payload["failures"], [])

    def test_verify_uploaded_detects_missing_and_tampered(self) -> None:
        downloads = Path(tempfile.mkdtemp(prefix="p08a-dl-"))
        try:
            for name in [*BINARIES, NOTES_ASSET]:
                shutil.copy2(self.assets / name, downloads / name)
            proc = tool("verify-uploaded", "--assets", str(self.assets), "--downloads", str(downloads), "--json")
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("平台侧缺少附件", proc.stdout)

            for name in [self.dist_name, f"{self.dist_name}.sha256"]:
                shutil.copy2(self.assets / name, downloads / name)
            proc = tool("verify-uploaded", "--assets", str(self.assets), "--downloads", str(downloads), "--json")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            with (downloads / BINARIES[2]).open("ab") as fh:
                fh.write(b"x")
            proc = tool("verify-uploaded", "--assets", str(self.assets), "--downloads", str(downloads), "--json")
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("SHA256", proc.stdout)
        finally:
            shutil.rmtree(downloads, ignore_errors=True)


class PromotionSafetyTests(unittest.TestCase):
    """promote / latest 门槛：只有完整 PASS 报告才能推进。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="p08a-promote-"))
        self.assets = self.tmp / "assets"
        self.assets.mkdir()
        payload = {
            "assets": [{"name": name} for name in [*BINARIES, NOTES_ASSET]],
            "version": VERSION,
            "source_commit": "a" * 40,
        }
        (self.assets / "version.json").write_text(json.dumps(payload), encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_report(self, name: str, payload: dict) -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_missing_report_blocks(self) -> None:
        proc = tool("promote-gate", "--stage", "pre-promote", "--report", str(self.tmp / "none.json"), "--assets", str(self.assets))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("BLOCKED", proc.stderr)

    def test_failed_report_blocks(self) -> None:
        report = self.write_report(
            "bad.json",
            {"status": "FAIL", "command": "verify-uploaded", "version": VERSION, "source_commit": "a" * 40, "verified_count": 0},
        )
        proc = tool("promote-gate", "--stage", "pre-promote", "--report", str(report), "--assets", str(self.assets))
        self.assertNotEqual(proc.returncode, 0)

    def test_partial_upload_blocks(self) -> None:
        report = self.write_report(
            "partial.json",
            {"status": "PASS", "command": "verify-uploaded", "version": VERSION, "source_commit": "a" * 40, "verified_count": 3},
        )
        proc = tool("promote-gate", "--stage", "pre-promote", "--report", str(report), "--assets", str(self.assets))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("附件验证数量不足", proc.stdout)

    def test_commit_mismatch_blocks(self) -> None:
        report = self.write_report(
            "mismatch.json",
            {"status": "PASS", "command": "verify-uploaded", "version": VERSION, "source_commit": "b" * 40, "verified_count": 5},
        )
        proc = tool("promote-gate", "--stage", "pre-promote", "--report", str(report), "--assets", str(self.assets))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Commit", proc.stdout)

    def test_full_pass_allows_promotion(self) -> None:
        report = self.write_report(
            "ok.json",
            {"status": "PASS", "command": "verify-uploaded", "version": VERSION, "source_commit": "a" * 40, "verified_count": 5},
        )
        proc = tool("promote-gate", "--stage", "pre-promote", "--report", str(report), "--assets", str(self.assets), "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_report_requires_every_stage(self) -> None:
        reports = self.tmp / "reports"
        reports.mkdir()
        (reports / "guard.json").write_text(json.dumps({"status": "GUARD_OK"}), encoding="utf-8")
        (reports / "build.json").write_text(json.dumps({"status": "BUILD_OK"}), encoding="utf-8")
        proc = tool("report", "--dir", str(self.assets), "--reports", str(reports), "--require-all", "--json")
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertFalse(payload["ga_claimed"])
        self.assertTrue(any(stage["status"] == "MISSING" for stage in payload["stages"]))

    def test_post_promote_failure_prevents_latest(self) -> None:
        """promote 后附件丢失：post 阶段验证失败，latest 门槛必须拒绝，报告不得声称 GA。"""
        reports = self.tmp / "reports"
        reports.mkdir()
        (reports / "guard.json").write_text(json.dumps({"status": "GUARD_OK"}), encoding="utf-8")
        (reports / "build.json").write_text(json.dumps({"status": "BUILD_OK"}), encoding="utf-8")
        (reports / "local-verification.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (reports / "upload-verification.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (reports / "promote-gate-pre.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        lost = self.write_report(
            "post.json",
            {
                "status": "FAIL",
                "command": "verify-uploaded",
                "version": VERSION,
                "source_commit": "a" * 40,
                "verified_count": 4,
                "failures": ["平台侧缺少附件: workflow-check_3.1.0_darwin_arm64"],
            },
        )
        proc = tool("promote-gate", "--stage", "post-promote", "--report", str(lost), "--assets", str(self.assets))
        self.assertNotEqual(proc.returncode, 0)

        shutil.copy2(lost, reports / "post-promote-verification.json")
        proc = tool("report", "--dir", str(self.assets), "--reports", str(reports), "--require-all", "--json")
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertFalse(payload["ga_claimed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
