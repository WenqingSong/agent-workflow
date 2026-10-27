#!/usr/bin/env python3
"""Release 工程脚本的失败注入测试（RC-B）。

覆盖目标：任何失败都不得产生错误的 GA / latest 状态，且正式附件集合必须是
7 项业务资产 + 2 项校验元数据的 9 项。

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
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

SOURCE_REPO = Path(__file__).resolve().parents[3]
RELEASE_TOOL = SOURCE_REPO / "scripts" / "release" / "release.py"
PIPELINE_CONFIG = SOURCE_REPO / ".cnb.yml"
# 版本从仓库真实来源读取：测试随 VERSION 升级，不把 Tag 写死在断言里。
VERSION = (SOURCE_REPO / ".agent" / "VERSION").read_text(encoding="utf-8").strip()
TAG = f"v{VERSION}"
FORMAL_REPO = "https://cnb.cool/framework-station/agent-workflow"
TEST_REPO = "https://cnb.cool/fixture-station/agent-workflow-rc"
BINARIES = (
    f"workflow-check_{VERSION}_linux_amd64",
    f"workflow-check_{VERSION}_linux_arm64",
    f"workflow-check_{VERSION}_darwin_arm64",
    f"workflow-check_{VERSION}_windows_amd64.exe",
)
NOTES_ASSET = f"agent-workflow-release-notes-{VERSION}.md"
DIST_ASSET = f"agent-workflow-src-{VERSION}.tar.gz"
DIST_SHA_ASSET = f"{DIST_ASSET}.sha256"
METADATA_ASSETS = ("SHA256SUMS", "version.json")
BUSINESS_ASSETS = (*BINARIES, DIST_ASSET, DIST_SHA_ASSET, NOTES_ASSET)


def base_env(**extra: str) -> dict[str, str]:
    """清除宿主的 CNB_* 变量，保证测试不依赖运行机器上的平台环境。"""
    env = {key: value for key, value in os.environ.items() if not key.startswith("CNB_")}
    env.update(extra)
    return env


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=base_env(
            GIT_AUTHOR_NAME="p08a",
            GIT_AUTHOR_EMAIL="p08a@example.invalid",
            GIT_COMMITTER_NAME="p08a",
            GIT_COMMITTER_EMAIL="p08a@example.invalid",
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_CONFIG_SYSTEM="/dev/null",
        ),
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 失败: {proc.stderr}")
    return proc.stdout.strip()


def tool(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RELEASE_TOOL), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env or base_env(),
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
    tracked = [rel for rel in listing if not rel.startswith(("dist/", "tmp.md", "expected/"))]
    for rel in tracked:
        src = SOURCE_REPO / rel
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    (repo / ".agent" / "VERSION").write_text(f"{version}\n", encoding="utf-8")
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


# ---------------------------------------------------------------------------
# 共享构建：四平台 Binary + Source Distribution 只构建一次
# ---------------------------------------------------------------------------

_SHARED: dict[str, object] = {}


def shared_build() -> dict[str, object]:
    if _SHARED:
        return _SHARED
    tmp = Path(tempfile.mkdtemp(prefix="p08a-shared-"))
    repo = make_repo(tmp / "src")
    commit = annotated_tag(repo)
    assets = tmp / "assets"
    proc = tool("build", "--repo", str(repo), "--output", str(assets), "--tag", TAG, "--repository", TEST_REPO, "--json")
    if proc.returncode != 0:
        raise AssertionError("共享构建失败: " + proc.stdout + proc.stderr)
    _SHARED.update(
        tmp=tmp,
        repo=repo,
        commit=commit,
        assets=assets,
        version_json=json.loads((assets / "version.json").read_text(encoding="utf-8")),
    )
    return _SHARED


def tearDownModule() -> None:
    if _SHARED:
        shutil.rmtree(Path(str(_SHARED["tmp"])), ignore_errors=True)


def copy_attachments(source: Path, destination: Path, names=None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in names if names is not None else [*BUSINESS_ASSETS, *METADATA_ASSETS]:
        shutil.copy2(source / name, destination / name)


def make_snapshot(assets: Path, destination: Path) -> tuple[Path, Path]:
    out = destination / "expected"
    manifest = destination / "expected-assets.json"
    proc = tool(
        "snapshot",
        "--assets",
        str(assets),
        "--out",
        str(out),
        "--manifest",
        str(manifest),
        "--json",
    )
    if proc.returncode != 0:
        raise AssertionError("snapshot 失败: " + proc.stdout + proc.stderr)
    return out, manifest


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
        self.assertEqual(payload["release_tag"], TAG)
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

    # F3：独立预期 Commit 绑定 -------------------------------------------------

    def test_expected_commit_match_passes(self) -> None:
        repo = make_repo(self.tmp)
        commit = annotated_tag(repo)
        proc = tool(
            "guard",
            "--repo",
            str(repo),
            "--tag",
            TAG,
            "--expected-commit",
            commit,
            "--expected-commit-source",
            "CNB_COMMIT",
            "--json",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["expected_commit_verified"])
        self.assertEqual(payload["expected_commit_source"], "CNB_COMMIT")

    def test_expected_commit_mismatch_fails(self) -> None:
        repo = make_repo(self.tmp)
        annotated_tag(repo)
        proc = tool("guard", "--repo", str(repo), "--tag", TAG, "--expected-commit", "b" * 40, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("GUARD_FAILED", proc.stdout + proc.stderr)
        self.assertIn("不一致", proc.stdout + proc.stderr)

    def test_expected_commit_invalid_fails(self) -> None:
        repo = make_repo(self.tmp)
        annotated_tag(repo)
        proc = tool("guard", "--repo", str(repo), "--tag", TAG, "--expected-commit", "HEAD", "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("40 位 SHA", proc.stdout + proc.stderr)

    def test_expected_commit_empty_fails(self) -> None:
        repo = make_repo(self.tmp)
        annotated_tag(repo)
        proc = tool("guard", "--repo", str(repo), "--tag", TAG, "--expected-commit", "", "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("不得跳过 Commit 绑定", proc.stdout + proc.stderr)

    def test_repository_argument_cannot_bypass_guard(self) -> None:
        repo = make_repo(self.tmp)
        git(repo, "tag", TAG)  # lightweight：必须被拒绝
        proc = tool(
            "guard",
            "--repo",
            str(repo),
            "--tag",
            TAG,
            "--repository",
            TEST_REPO,
            "--json",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("TAG_NOT_ANNOTATED", proc.stdout + proc.stderr)


class ReleaseAssetTests(unittest.TestCase):
    """构建一次真实资产集合，其余用例在其副本上做失败注入。"""

    def setUp(self) -> None:
        self.shared = shared_build()
        self.assets = Path(str(self.shared["assets"]))
        self.repo = Path(str(self.shared["repo"]))
        self.commit = str(self.shared["commit"])
        self.version_json = self.shared["version_json"]

    def copy_assets(self) -> Path:
        target = Path(tempfile.mkdtemp(prefix="p08a-copy-")) / "assets"
        shutil.copytree(self.assets, target)
        return target

    def verify(self, assets: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return tool(
            "verify-assets",
            "--dir",
            str(assets),
            "--repo",
            str(self.repo),
            "--version",
            VERSION,
            "--tag",
            TAG,
            "--commit",
            self.commit,
            *extra,
            "--json",
        )

    def test_build_outputs_exactly_nine_attachments(self) -> None:
        names = sorted(p.name for p in self.assets.iterdir() if p.is_file())
        self.assertEqual(names, sorted([*BUSINESS_ASSETS, *METADATA_ASSETS]))
        self.assertEqual(len(names), 9)
        payload = self.version_json
        self.assertEqual(payload["published_status"], "NOT_RELEASED")
        self.assertEqual(payload["source_commit"], self.commit)
        self.assertEqual(payload["distribution"]["release_tag"], "")
        self.assertEqual(payload["distribution"]["release_url"], "")
        self.assertEqual(payload["distribution"]["file"], DIST_ASSET)
        self.assertEqual(payload["attachments"]["count"], 9)
        self.assertEqual(payload["attachments"]["business_count"], 7)
        self.assertEqual(sorted(payload["attachments"]["metadata"]), sorted(METADATA_ASSETS))
        # P07 的 <commit12> 命名不得残留（否则附件数会多于 9）
        leftover = f"agent-workflow-src-{self.commit[:12]}.tar.gz"
        self.assertFalse((self.assets / leftover).exists(), leftover)

    def test_verify_assets_passes_and_checksums_are_real(self) -> None:
        proc = self.verify(self.assets)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(payload["checksums_verified"])
        self.assertEqual(payload["business_asset_count"], 7)
        self.assertEqual(payload["metadata_asset_count"], 2)
        self.assertEqual(payload["attachment_count"], 9)
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
        proc = self.verify(assets)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("缺少资产", proc.stdout)

    def test_missing_sha256sums_fails(self) -> None:
        assets = self.copy_assets()
        (assets / "SHA256SUMS").unlink()
        proc = self.verify(assets)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("附件目录不是精确的 9 项", proc.stdout)

    def test_missing_version_json_fails(self) -> None:
        assets = self.copy_assets()
        (assets / "version.json").unlink()
        proc = self.verify(assets)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("VERSION_JSON_MISSING", proc.stdout)

    def test_extra_attachment_fails(self) -> None:
        assets = self.copy_assets()
        (assets / "agent-workflow-src-9.9.9.tar.gz").write_bytes(b"stray")
        proc = self.verify(assets)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("附件目录不是精确的 9 项", proc.stdout)

    def test_tampered_binary_fails(self) -> None:
        assets = self.copy_assets()
        with (assets / BINARIES[0]).open("ab") as handle:
            handle.write(b"tampered")
        proc = self.verify(assets)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("SHA256", proc.stdout)

    def test_tampered_checksums_fails(self) -> None:
        assets = self.copy_assets()
        lines = (assets / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        digest, name = lines[0].split("  ", 1)
        lines[0] = f"{'0' * 64}  {name}"
        (assets / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = self.verify(assets)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("sha256sum -c SHA256SUMS 失败", proc.stdout)

    def test_version_commit_mismatch_fails(self) -> None:
        assets = self.copy_assets()
        payload = json.loads((assets / "version.json").read_text())
        payload["source_commit"] = "0" * 40
        (assets / "version.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        proc = self.verify(assets)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("source_commit", proc.stdout)

    def test_manifest_repository_mismatch_fails(self) -> None:
        assets = self.copy_assets()
        archive = assets / DIST_ASSET

        def mutate(top: Path) -> None:
            manifest_path = top / "MANIFEST.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["source"]["repository"] = FORMAL_REPO
            manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

        repack(archive, mutate)
        proc = self.verify(assets, "--repository", TEST_REPO)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("source.repository", proc.stdout)

    def test_dist_missing_architect_fails(self) -> None:
        assets = self.copy_assets()
        archive = assets / DIST_ASSET

        def mutate(top: Path) -> None:
            (top / ".agent" / "roles" / "ArchitectAgent.md").unlink()

        repack(archive, mutate)
        proc = self.verify(assets)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ArchitectAgent.md", proc.stdout)

    def test_manifest_member_mismatch_fails(self) -> None:
        assets = self.copy_assets()
        archive = assets / DIST_ASSET

        def mutate(top: Path) -> None:
            manifest_path = top / "MANIFEST.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            removed = payload["files"].pop()
            payload["install"]["managed"] = [p for p in payload["install"]["managed"] if p != removed["path"]]
            manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

        repack(archive, mutate)
        proc = self.verify(assets)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("MANIFEST", proc.stdout)

    def test_manifest_release_tag_must_stay_empty(self) -> None:
        assets = self.copy_assets()
        archive = assets / DIST_ASSET

        def mutate(top: Path) -> None:
            manifest_path = top / "MANIFEST.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["release"]["tag"] = TAG
            payload["release"]["url"] = "https://example.invalid/release"
            manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

        repack(archive, mutate)
        proc = self.verify(assets)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("release.tag", proc.stdout)

    def test_forbidden_host_data_in_dist_fails(self) -> None:
        assets = self.copy_assets()
        archive = assets / DIST_ASSET

        def mutate(top: Path) -> None:
            (top / ".agent").mkdir(exist_ok=True)
            (top / ".agent" / "workflow.yaml").write_text("schema_version: 1\n", encoding="utf-8")

        repack(archive, mutate)
        proc = self.verify(assets)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(".agent/workflow.yaml", proc.stdout)

    def test_repro_json_is_clean_and_reproducible(self) -> None:
        proc = tool("repro", "--repo", str(self.repo), "--tag", TAG, "--repository", TEST_REPO, "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)  # --json 必须是纯净 JSON，不能被子构建输出污染
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["artifacts"], 7)
        self.assertEqual(payload["attachments"], 9)
        self.assertEqual(payload["repository"], TEST_REPO)
        self.assertEqual(payload["failures"], [])


class CommitBindingTests(unittest.TestCase):
    """F3：预期 Commit 必须来自独立来源，不得采信 version.json 自报值。"""

    def setUp(self) -> None:
        self.shared = shared_build()
        self.assets = Path(str(self.shared["assets"]))
        self.repo = Path(str(self.shared["repo"]))
        self.commit = str(self.shared["commit"])
        self.tmp = Path(tempfile.mkdtemp(prefix="p08a-commit-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_verify_assets_requires_explicit_commit(self) -> None:
        proc = tool("verify-assets", "--dir", str(self.assets), "--repo", str(self.repo), "--version", VERSION, "--json")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("COMMIT_REQUIRED", proc.stdout + proc.stderr)
        self.assertIn("不得回退到 version.json", proc.stdout + proc.stderr)

    def test_verify_assets_unknown_commit_fails(self) -> None:
        proc = tool(
            "verify-assets",
            "--dir",
            str(self.assets),
            "--repo",
            str(self.repo),
            "--version",
            VERSION,
            "--commit",
            "c" * 40,
            "--json",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("不存在", proc.stdout)

    def test_verify_assets_tag_commit_mismatch_fails(self) -> None:
        clone = self.tmp / "clone"
        subprocess.run(
            ["git", "clone", "--quiet", str(self.repo), str(clone)],
            capture_output=True,
            text=True,
            check=True,
            env=base_env(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null"),
        )
        git(clone, "commit", "--allow-empty", "-q", "-m", "advance")
        head = git(clone, "rev-parse", "HEAD")
        proc = tool(
            "verify-assets",
            "--dir",
            str(self.assets),
            "--repo",
            str(clone),
            "--version",
            VERSION,
            "--tag",
            TAG,
            "--commit",
            head,
            "--json",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(f"Tag {TAG} 解析出的 Commit", proc.stdout)

    def test_self_reported_commit_cannot_satisfy_binding(self) -> None:
        """version.json 自报另一个 Commit 时，用它当 --commit 也必须被 Tag 绑定拒绝。"""
        assets = self.tmp / "assets"
        shutil.copytree(self.assets, assets)
        payload = json.loads((assets / "version.json").read_text())
        fake = "d" * 40
        payload["source_commit"] = fake
        (assets / "version.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        proc = tool(
            "verify-assets",
            "--dir",
            str(assets),
            "--repo",
            str(self.repo),
            "--version",
            VERSION,
            "--tag",
            TAG,
            "--commit",
            fake,
            "--json",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(f"Tag {TAG} 解析出的 Commit", proc.stdout)

    def test_verify_uploaded_requires_explicit_commit(self) -> None:
        out, manifest = make_snapshot(self.assets, self.tmp)
        downloads = self.tmp / "downloads"
        copy_attachments(out, downloads)
        proc = tool(
            "verify-uploaded",
            "--assets",
            str(out),
            "--downloads",
            str(downloads),
            "--manifest",
            str(manifest),
            "--json",
        )
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("COMMIT_REQUIRED", proc.stdout + proc.stderr)


class AttachmentMetadataTests(unittest.TestCase):
    """F1：9 项正式附件的上传前快照与平台侧复核。"""

    def setUp(self) -> None:
        self.shared = shared_build()
        self.assets = Path(str(self.shared["assets"]))
        self.commit = str(self.shared["commit"])
        self.tmp = Path(tempfile.mkdtemp(prefix="p08a-meta-"))
        self.expected, self.manifest = make_snapshot(self.assets, self.tmp)
        self.downloads = self.tmp / "downloads"
        copy_attachments(self.expected, self.downloads)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def verify_uploaded(self, *extra: str, downloads: Path | None = None) -> subprocess.CompletedProcess[str]:
        return tool(
            "verify-uploaded",
            "--assets",
            str(self.expected),
            "--downloads",
            str(downloads or self.downloads),
            "--manifest",
            str(self.manifest),
            "--commit",
            self.commit,
            *extra,
            "--json",
        )

    def test_snapshot_records_nine_attachments(self) -> None:
        payload = json.loads(self.manifest.read_text())
        self.assertEqual(payload["attachment_count"], 9)
        self.assertEqual(payload["business_count"], 7)
        self.assertEqual(payload["metadata_count"], 2)
        self.assertEqual(sorted(payload["metadata_assets"]), sorted(METADATA_ASSETS))
        names = sorted(entry["name"] for entry in payload["attachments"])
        self.assertEqual(names, sorted([*BUSINESS_ASSETS, *METADATA_ASSETS]))
        by_name = {entry["name"]: entry for entry in payload["attachments"]}
        self.assertEqual(by_name["SHA256SUMS"]["attachment"], "metadata")
        self.assertEqual(by_name["version.json"]["attachment"], "metadata")
        self.assertEqual(by_name["SHA256SUMS"]["sha256"], payload["checksums"]["binding"])
        self.assertNotIn("version.json", payload["checksums"]["covers"])

    def test_snapshot_rejects_seven_item_upload(self) -> None:
        """上传只有 7 项业务资产 + version.json（缺 SHA256SUMS）→ 快照必须 FAIL。"""
        incomplete = self.tmp / "eight"
        copy_attachments(self.assets, incomplete, names=[*BUSINESS_ASSETS, "version.json"])
        proc = tool(
            "snapshot",
            "--assets",
            str(incomplete),
            "--out",
            str(self.tmp / "eight-expected"),
            "--manifest",
            str(self.tmp / "eight.json"),
            "--json",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ATTACHMENT_MISSING", proc.stdout + proc.stderr)
        self.assertIn("SHA256SUMS", proc.stdout + proc.stderr)

    def test_snapshot_rejects_missing_version_json(self) -> None:
        incomplete = self.tmp / "no-version-json"
        copy_attachments(self.assets, incomplete, names=[*BUSINESS_ASSETS, "SHA256SUMS"])
        proc = tool(
            "snapshot",
            "--assets",
            str(incomplete),
            "--out",
            str(self.tmp / "no-vj-expected"),
            "--manifest",
            str(self.tmp / "no-vj.json"),
            "--json",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ATTACHMENT_MISSING", proc.stdout + proc.stderr)
        self.assertIn("version.json", proc.stdout + proc.stderr)

    def test_nine_uploaded_attachments_pass(self) -> None:
        proc = self.verify_uploaded()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["verified_count"], 9)
        self.assertEqual(payload["expected_count"], 9)
        self.assertTrue(payload["metadata_attachments_verified"])
        self.assertTrue(payload["checksums_verified"])

    def test_missing_sha256sums_fails(self) -> None:
        (self.downloads / "SHA256SUMS").unlink()
        proc = self.verify_uploaded()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("平台侧缺少附件: SHA256SUMS", proc.stdout)
        self.assertIn("校验元数据附件", proc.stdout)

    def test_missing_version_json_fails(self) -> None:
        (self.downloads / "version.json").unlink()
        proc = self.verify_uploaded()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("平台侧缺少附件: version.json", proc.stdout)

    def test_tampered_version_json_fails(self) -> None:
        path = self.downloads / "version.json"
        payload = json.loads(path.read_text())
        payload["source_commit"] = "e" * 40
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        proc = self.verify_uploaded()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("SHA256 与上传前快照不一致", proc.stdout)
        self.assertIn("source_commit", proc.stdout)

    def test_tampered_sha256sums_fails(self) -> None:
        path = self.downloads / "SHA256SUMS"
        lines = path.read_text(encoding="utf-8").splitlines()
        digest, name = lines[0].split("  ", 1)
        lines[0] = f"{'1' * 64}  {name}"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = self.verify_uploaded()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("SHA256 与上传前快照不一致", proc.stdout)

    def test_metadata_tamper_breaks_binding(self) -> None:
        """同时篡改 SHA256SUMS 与其在 version.json 中的摘要，仍必须 FAIL。"""
        sums = self.downloads / "SHA256SUMS"
        sums.write_bytes(sums.read_bytes() + b"\n")
        version_path = self.downloads / "version.json"
        payload = json.loads(version_path.read_text())
        payload["checksums"]["sha256"] = "2" * 64
        version_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        proc = self.verify_uploaded()
        self.assertNotEqual(proc.returncode, 0)

    def test_duplicate_attachment_name_fails(self) -> None:
        shutil.copy2(self.downloads / "SHA256SUMS", self.downloads / "SHA256SUMS (1)")
        proc = self.verify_uploaded()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("重复或未预期的附件", proc.stdout)

    def test_sha256_mismatch_before_after_upload_fails(self) -> None:
        with (self.downloads / BINARIES[2]).open("ab") as handle:
            handle.write(b"x")
        proc = self.verify_uploaded()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("SHA256 与上传前快照不一致", proc.stdout)

    def test_promote_after_metadata_loss_blocks_and_no_ga_claim(self) -> None:
        reports = self.tmp / "reports"
        reports.mkdir()
        (reports / "guard.json").write_text(json.dumps({"status": "GUARD_OK"}), encoding="utf-8")
        (reports / "build.json").write_text(json.dumps({"status": "BUILD_OK"}), encoding="utf-8")
        (reports / "local-verification.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (reports / "upload-verification.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (reports / "promote-gate-pre.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")

        # promote 后 version.json 附件丢失 → 复核必须 FAIL。
        (self.downloads / "version.json").unlink()
        proc = self.verify_uploaded()
        self.assertNotEqual(proc.returncode, 0)
        post = json.loads(proc.stdout)
        self.assertEqual(post["status"], "FAIL")
        (reports / "post-promote-verification.json").write_text(json.dumps(post), encoding="utf-8")

        gate = tool(
            "promote-gate",
            "--stage",
            "post-promote",
            "--report",
            str(reports / "post-promote-verification.json"),
            "--assets",
            str(self.expected),
            "--tag",
            TAG,
            "--expected-commit",
            self.commit,
        )
        self.assertNotEqual(gate.returncode, 0)
        self.assertIn("BLOCKED", gate.stderr)

        report = tool("report", "--dir", str(self.expected), "--reports", str(reports), "--require-all", "--json")
        self.assertNotEqual(report.returncode, 0)
        payload = json.loads(report.stdout)
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertFalse(payload["ga_claimed"])


class RepositorySourceTests(unittest.TestCase):
    """F2：仓库来源必须来自实际环境，不得硬编码正式仓库。"""

    def setUp(self) -> None:
        self.shared = shared_build()
        self.assets = Path(str(self.shared["assets"]))
        self.repo = Path(str(self.shared["repo"]))
        self.commit = str(self.shared["commit"])
        self.tmp = Path(tempfile.mkdtemp(prefix="p08a-repo-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_independent_rc_repository_recorded(self) -> None:
        payload = json.loads((self.assets / "version.json").read_text())
        self.assertEqual(payload["repository"], TEST_REPO)
        self.assertEqual(payload["source_repository"], TEST_REPO)
        self.assertEqual(payload["repository_mode"], "independent-rc")
        self.assertEqual(payload["release_tag"], TAG)
        self.assertEqual(payload["source_commit"], self.commit)

    def test_formal_repository_recorded_with_manifest_binding(self) -> None:
        out = self.tmp / "formal"
        proc = tool(
            "build",
            "--repo",
            str(self.repo),
            "--output",
            str(out),
            "--tag",
            TAG,
            "--repository",
            FORMAL_REPO,
            "--json",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads((out / "version.json").read_text())
        self.assertEqual(payload["repository"], FORMAL_REPO)
        self.assertEqual(payload["repository_mode"], "formal")
        self.assertEqual(payload["source_commit"], self.commit)

        verify = tool(
            "verify-assets",
            "--dir",
            str(out),
            "--repo",
            str(self.repo),
            "--version",
            VERSION,
            "--commit",
            self.commit,
            "--repository",
            FORMAL_REPO,
            "--json",
        )
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

        # 同一 Commit 下切换 repository 不改变 Commit 绑定：Binary 逐字节一致。
        rc_binaries = {name: (self.assets / name).read_bytes() for name in BINARIES}
        for name, data in rc_binaries.items():
            self.assertEqual(data, (out / name).read_bytes(), name)

    def test_repository_mismatch_fails_verification(self) -> None:
        proc = tool(
            "verify-assets",
            "--dir",
            str(self.assets),
            "--repo",
            str(self.repo),
            "--version",
            VERSION,
            "--commit",
            self.commit,
            "--repository",
            FORMAL_REPO,
            "--json",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("repository", proc.stdout)

    def test_repository_unknown_fails_closed(self) -> None:
        repo = make_repo(self.tmp / "fresh")  # 无 origin、无 CNB 环境
        annotated_tag(repo)
        out = self.tmp / "assets"
        proc = tool("build", "--repo", str(repo), "--output", str(out), "--tag", TAG, "--json")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("REPOSITORY_UNKNOWN", proc.stdout + proc.stderr)
        self.assertFalse((out / "version.json").exists())

    def test_repository_conflict_fails_closed(self) -> None:
        # 显式参数与 CNB 环境指向不同仓库：构建前即拒绝，不写出任何 version.json。
        env = base_env(CNB_REPO_URL=FORMAL_REPO)
        proc = tool(
            "build",
            "--repo",
            str(self.repo),
            "--output",
            str(self.tmp / "conflict"),
            "--tag",
            TAG,
            "--repository",
            TEST_REPO,
            "--json",
            env=env,
        )
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("REPOSITORY_CONFLICT", proc.stdout + proc.stderr)
        self.assertFalse((self.tmp / "conflict" / "version.json").exists())

    def test_repository_from_cnb_environment(self) -> None:
        env = base_env(CNB_REPO_SLUG="fixture-station/agent-workflow-rc")
        proc = tool("repository", "--repo", str(self.repo), "--json", env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["repository"], TEST_REPO)
        self.assertEqual(payload["repository_mode"], "independent-rc")
        self.assertIn("CNB_REPO_SLUG", payload["repository_sources"])

    def test_repository_conflict_between_env_and_git_remote(self) -> None:
        env = base_env(CNB_REPO_URL=FORMAL_REPO)
        repo = make_repo(self.tmp / "with-origin")
        git(repo, "remote", "add", "origin", TEST_REPO)
        proc = tool("repository", "--repo", str(repo), "--json", env=env)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("REPOSITORY_CONFLICT", proc.stdout + proc.stderr)


class PipelineConfigTests(unittest.TestCase):
    """F1：.cnb.yml 必须覆盖完整 9 项附件与完整发布周期，且不得使用 glob。"""

    def setUp(self) -> None:
        self.shared = shared_build()
        self.assets = Path(str(self.shared["assets"]))
        self.tmp = Path(tempfile.mkdtemp(prefix="p08a-pipeline-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, config: Path) -> subprocess.CompletedProcess[str]:
        return tool("check-pipeline", "--config", str(config), "--dir", str(self.assets), "--json")

    def mutate(self, stage: str, old: str, new: str) -> Path:
        text = PIPELINE_CONFIG.read_text(encoding="utf-8")
        lines = text.splitlines()
        starts = [index for index, line in enumerate(lines) if re.match(r"^\s*- name:\s*(\S+)", line)]
        bounds = None
        for position, index in enumerate(starts):
            name = re.match(r"^\s*- name:\s*(\S+)", lines[index]).group(1).strip("\"'")
            if name == stage:
                end = starts[position + 1] if position + 1 < len(starts) else len(lines)
                bounds = (index, end)
                break
        if bounds is None:
            raise AssertionError(f"找不到阶段: {stage}")
        start, end = bounds
        block = "\n".join(lines[start:end])
        if old not in block:
            raise AssertionError(f"{stage} 中找不到 {old!r}")
        lines[start:end] = block.replace(old, new, 1).splitlines()
        path = self.tmp / f"{stage}.yml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_pipeline_covers_nine_attachments_in_sync(self) -> None:
        proc = self.check(PIPELINE_CONFIG)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["attachment_count"], 9)
        self.assertEqual(len(payload["stages"]), 5)
        for stage in payload["stages"]:
            self.assertEqual(stage["count"], 9, stage)
            self.assertEqual(sorted(stage["attachments"]), sorted([*BUSINESS_ASSETS, *METADATA_ASSETS]))

    def test_pipeline_uses_no_glob_for_downloads(self) -> None:
        proc = self.check(PIPELINE_CONFIG)
        payload = json.loads(proc.stdout)
        for stage in payload["stages"]:
            self.assertNotIn("*", "".join(stage["attachments"]), stage)

    def test_reupload_missing_version_json_fails(self) -> None:
        config = self.mutate("reupload-after-promote", '              - "./dist/version.json"\n', "")
        proc = self.check(config)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("reupload-after-promote: 附件数量", proc.stdout)

    def test_first_upload_missing_sha256sums_fails(self) -> None:
        config = self.mutate("upload-assets", '              - "./dist/SHA256SUMS"\n', "")
        proc = self.check(config)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("upload-assets", proc.stdout)
        self.assertIn("SHA256SUMS", proc.stdout)

    def test_download_glob_fails(self) -> None:
        config = self.mutate(
            "redownload-after-latest",
            DIST_ASSET,
            "agent-workflow-src-*.tar.gz",
        )
        proc = self.check(config)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("不得使用 glob", proc.stdout)

    def test_verify_stage_without_commit_fails(self) -> None:
        config = self.mutate(
            "verify-after-promote",
            '              --commit "${EXPECTED_COMMIT}" \\\n',
            "",
        )
        proc = self.check(config)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("必须传入独立 --commit", proc.stdout)

    def test_promote_gate_without_expected_commit_fails(self) -> None:
        config = self.mutate(
            "promote-gate-post",
            '              --expected-commit "${EXPECTED_COMMIT}" \\\n',
            "",
        )
        proc = self.check(config)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("promote-gate-post", proc.stdout)

    def test_latest_before_post_verify_fails(self) -> None:
        text = PIPELINE_CONFIG.read_text(encoding="utf-8")
        lines = text.splitlines()
        starts = [index for index, line in enumerate(lines) if re.match(r"^\s*- name:\s*(\S+)", line)]
        names = [re.match(r"^\s*- name:\s*(\S+)", lines[index]).group(1).strip("\"'") for index in starts]
        latest_index = names.index("mark-latest")
        bounds = (starts[latest_index], starts[latest_index + 1])
        block = lines[bounds[0] : bounds[1]]
        rest = lines[: bounds[0]] + lines[bounds[1] :]
        # 把 mark-latest 插到 promote-gate-pre 之前
        target = names.index("promote-gate-pre")
        insert_at = starts[target]
        mutated = rest[:insert_at] + block + rest[insert_at:]
        path = self.tmp / "order.yml"
        path.write_text("\n".join(mutated) + "\n", encoding="utf-8")
        proc = self.check(path)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("阶段顺序", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
