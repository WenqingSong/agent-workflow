#!/usr/bin/env python3
"""Workflow V4 发布工具。

子命令:
    guard           Tag / 版本 / Commit 守卫
    build           构建源码资产（源码包、校验文件、SHA256SUMS、version.json、Release Notes）
    verify          校验资产集合、哈希、SHA256SUMS 与 version.json
    repro           两次独立构建比较可复现性
    check-pipeline  静态检查 .cnb.yml 是否覆盖全部资产且未引用旧 Engine 二进制
    report          汇总各阶段 JSON 报告

V4 发布只产出源码资产，不构建任何二进制。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

FORMAT = 1
PUBLISHED_STATUS = "NOT_RELEASED"
ADOPTION_PATH = Path(".agent/adoption/workflow_adoption.py")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ReleaseError("GIT_FAILED", f"git {' '.join(args)} 失败: {proc.stderr.strip()}")
    return proc.stdout.strip()


class ReleaseError(RuntimeError):
    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def load_adoption(repo: Path):
    path = repo / ADOPTION_PATH
    if not path.is_file():
        raise ReleaseError("ADOPTION_MISSING", f"缺少 {ADOPTION_PATH}")
    spec = importlib.util.spec_from_file_location("workflow_adoption", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def version_from_tag(tag: str) -> str:
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ReleaseError("TAG_INVALID", f"Tag 必须是 v<major>.<minor>.<patch>: {tag}")
    return tag[1:]


def asset_names(version: str) -> list[str]:
    return [
        f"agent-workflow-src-{version}.tar.gz",
        f"agent-workflow-src-{version}.tar.gz.sha256",
        f"agent-workflow-release-notes-{version}.md",
        "SHA256SUMS",
        "version.json",
    ]


def write_report(path: str | None, payload: dict) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                print(f"{key}: {json.dumps(value, ensure_ascii=False)}")
            else:
                print(f"{key}: {value}")


# --------------------------------------------------------------------------- #
# guard
# --------------------------------------------------------------------------- #


def cmd_guard(args) -> int:
    repo = Path(args.repo).resolve()
    version = version_from_tag(args.tag)

    if not (repo / ".git").exists():
        raise ReleaseError("NOT_A_GIT_REPO", f"不是 Git 仓库: {repo}")

    version_file = (repo / ".agent/VERSION").read_text(encoding="utf-8").strip()
    if version_file != version:
        raise ReleaseError(
            "VERSION_MISMATCH",
            f".agent/VERSION {version_file} 与 Tag {args.tag} 不一致",
        )

    tag_type = git(repo, "cat-file", "-t", f"refs/tags/{args.tag}")
    if tag_type != "tag":
        raise ReleaseError(
            "TAG_NOT_ANNOTATED",
            f"{args.tag} 不是 annotated tag（类型 {tag_type}）",
        )

    tag_commit = git(repo, "rev-list", "-n", "1", f"refs/tags/{args.tag}")
    head = git(repo, "rev-parse", "HEAD")
    if head != tag_commit:
        raise ReleaseError(
            "HEAD_NOT_TAG",
            "当前 HEAD 不是 Tag 指向的 Commit",
            {"head": head, "tag_commit": tag_commit},
        )

    expected = args.expected_commit or tag_commit
    if expected != tag_commit:
        raise ReleaseError(
            "COMMIT_MISMATCH",
            "独立预期 Commit 与 Tag 解析结果不一致",
            {"expected": expected, "tag_commit": tag_commit, "source": args.expected_commit_source},
        )

    status = git(repo, "status", "--porcelain")
    if status:
        raise ReleaseError("WORKTREE_DIRTY", "发布构建要求工作区干净", {"status": status.splitlines()[:20]})

    payload = {
        "status": "PASS",
        "tag": args.tag,
        "version": version,
        "tag_type": tag_type,
        "commit": tag_commit,
        "head": head,
        "expected_commit_source": args.expected_commit_source or "tag-resolution",
        "generated_at": utc_now(),
    }
    write_report(args.report, payload)
    emit(payload, args.json)
    return 0


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #


def cmd_build(args) -> int:
    repo = Path(args.repo).resolve()
    output = Path(args.output).resolve()
    version = version_from_tag(args.tag)

    tag_commit = git(repo, "rev-list", "-n", "1", f"refs/tags/{args.tag}")
    head = git(repo, "rev-parse", "HEAD")
    if head != tag_commit:
        raise ReleaseError("HEAD_NOT_TAG", "构建必须发生在 Tag 指向的 Commit 上")

    notes_src = repo / "docs/releases" / f"v{version}.md"
    if not notes_src.is_file():
        raise ReleaseError("NOTES_MISSING", f"缺少 Release Notes: {notes_src}")

    adoption = load_adoption(repo)
    dist = adoption.build_dist(repo, output, expected_commit=tag_commit, repository=None)

    archive = Path(dist["archive"])
    checksum = Path(dist["checksum"])
    notes_name = f"agent-workflow-release-notes-{version}.md"
    notes_dst = output / notes_name
    shutil.copyfile(notes_src, notes_dst)

    # SHA256SUMS 覆盖除自身与 version.json 之外的资产
    covered = [archive.name, checksum.name, notes_name]
    lines = [f"{sha256_file(output / name)}  {name}" for name in sorted(covered)]
    sums_path = output / "SHA256SUMS"
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assets = []
    for name in covered:
        path = output / name
        assets.append({
            "name": name,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "verification": "content",
        })

    manifest = adoption.load_package(archive)["manifest"]

    version_doc = {
        "format": FORMAT,
        "version": version,
        "tag": args.tag,
        "source_commit": tag_commit,
        "source_tree": git(repo, "rev-parse", f"{tag_commit}^{{tree}}"),
        "repository": manifest["source"].get("repository", ""),
        "generated_at": utc_now(),
        "published_status": PUBLISHED_STATUS,
        "assets": assets,
        "sha256sums": {
            "name": sums_path.name,
            "sha256": sha256_file(sums_path),
            "covers": sorted(covered),
        },
        "distribution": {
            "version": manifest["framework_version"],
            "files": len(manifest["files"]),
            "manifest_format": manifest["format"],
            "archive_top": dist["archive"].split("/")[-1].removesuffix(".tar.gz"),
        },
        "binaries": [],
    }
    (output / "version.json").write_text(
        json.dumps(version_doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    payload = {
        "status": "PASS",
        "version": version,
        "tag": args.tag,
        "commit": tag_commit,
        "output": str(output),
        "assets": asset_names(version),
        "source_archive_sha256": dist["sha256"],
        "managed_files": dist["managed"],
        "package_only_files": dist["package_only"],
        "generated_at": utc_now(),
    }
    write_report(args.report, payload)
    emit(payload, args.json)
    return 0


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #


def parse_sums(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ReleaseError("SUMS_INVALID", f"SHA256SUMS 行格式错误: {line!r}")
        entries[match.group(2)] = match.group(1)
    return entries


def cmd_verify(args) -> int:
    directory = Path(args.dir).resolve()
    version = args.version or version_from_tag(args.tag or "")
    expected = asset_names(version)

    missing = [name for name in expected if not (directory / name).is_file()]
    extra = sorted(
        p.name for p in directory.iterdir()
        if p.is_file() and p.name not in expected and not p.name.endswith(".tmp")
    )
    if missing:
        raise ReleaseError("ASSET_MISSING", "缺少发布资产", {"missing": missing})

    actual_hashes = {name: sha256_file(directory / name) for name in expected}

    sums = parse_sums(directory / "SHA256SUMS")
    covered = sorted(set(expected) - {"SHA256SUMS", "version.json"})
    if sorted(sums) != covered:
        raise ReleaseError(
            "SUMS_COVERAGE",
            "SHA256SUMS 覆盖范围不正确",
            {"expected": covered, "actual": sorted(sums)},
        )
    for name, digest in sums.items():
        if actual_hashes[name] != digest:
            raise ReleaseError("SUMS_MISMATCH", f"SHA256SUMS 与实际文件不一致: {name}")

    version_doc = json.loads((directory / "version.json").read_text(encoding="utf-8"))
    if version_doc.get("version") != version:
        raise ReleaseError("VERSION_MISMATCH", "version.json 的 version 不正确")
    if args.tag and version_doc.get("tag") != args.tag:
        raise ReleaseError("TAG_MISMATCH", "version.json 的 tag 不正确")
    if version_doc.get("sha256sums", {}).get("sha256") != actual_hashes["SHA256SUMS"]:
        raise ReleaseError("SUMS_DIGEST_MISMATCH", "version.json 记录的 SHA256SUMS 摘要不一致")

    recorded = {item["name"]: item["sha256"] for item in version_doc.get("assets", [])}
    for name in covered:
        if recorded.get(name) != actual_hashes[name]:
            raise ReleaseError("ASSET_HASH_MISMATCH", f"version.json 记录的资产哈希不一致: {name}")

    if version_doc.get("binaries"):
        raise ReleaseError("BINARY_PRESENT", "V4 发布不得包含二进制资产")

    if args.commit:
        if version_doc.get("source_commit") != args.commit:
            raise ReleaseError(
                "COMMIT_MISMATCH",
                "version.json 的 source_commit 与独立预期不一致",
                {"recorded": version_doc.get("source_commit"), "expected": args.commit},
            )
        if args.repo:
            repo = Path(args.repo).resolve()
            if (repo / ".git").exists():
                actual = git(repo, "rev-parse", "HEAD")
                if args.tag:
                    tag_commit = git(repo, "rev-list", "-n", "1", f"refs/tags/{args.tag}")
                    if tag_commit != args.commit:
                        raise ReleaseError("TAG_COMMIT_MISMATCH", "Tag 指向的 Commit 与预期不一致")
                if actual != args.commit:
                    raise ReleaseError("HEAD_MISMATCH", "当前 HEAD 与预期 Commit 不一致")

    # 源码包成员与 MANIFEST 交叉校验（不依赖包内自报）
    if args.repo:
        adoption = load_adoption(Path(args.repo).resolve())
    else:
        adoption = load_adoption(Path(__file__).resolve().parents[2])
    package = adoption.load_package(directory / f"agent-workflow-src-{version}.tar.gz")
    if package["version"] != version:
        raise ReleaseError("PACKAGE_VERSION_MISMATCH", "源码包版本与发布版本不一致")
    if args.commit and package["commit"] != args.commit:
        raise ReleaseError("PACKAGE_COMMIT_MISMATCH", "源码包 Commit 与预期不一致")
    if package["manifest"]["release"]["tag"]:
        raise ReleaseError("PACKAGE_PRETAGGED", "构建时的源码包不得预先写入 release.tag")

    payload = {
        "status": "PASS",
        "version": version,
        "tag": args.tag,
        "commit": package["commit"],
        "assets": expected,
        "sha256": actual_hashes,
        "package_files": len(package["files"]),
        "unexpected_files": extra,
        "generated_at": utc_now(),
    }
    write_report(args.report, payload)
    emit(payload, args.json)
    return 0


# --------------------------------------------------------------------------- #
# verify-uploaded
# --------------------------------------------------------------------------- #


def cmd_verify_uploaded(args) -> int:
    """用上传前记录的哈希核对平台侧下载副本。"""
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        raise ReleaseError("MANIFEST_MISSING", f"缺少上传前快照: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest.get("sha256") or {}
    if not expected:
        raise ReleaseError("MANIFEST_INVALID", "上传前快照缺少 sha256 映射")

    downloads = Path(args.downloads)
    missing = [name for name in expected if not (downloads / name).is_file()]
    if missing:
        raise ReleaseError("DOWNLOAD_MISSING", "平台侧副本缺失", {"missing": missing})

    mismatched = []
    for name, digest in expected.items():
        actual = sha256_file(downloads / name)
        if actual != digest:
            mismatched.append({"name": name, "expected": digest, "actual": actual})
    if mismatched:
        raise ReleaseError("DOWNLOAD_MISMATCH", "平台侧副本与上传前哈希不一致", {"files": mismatched})

    version_doc = json.loads((downloads / "version.json").read_text(encoding="utf-8"))
    if args.commit and version_doc.get("source_commit") != args.commit:
        raise ReleaseError("COMMIT_MISMATCH", "下载副本的 source_commit 与预期不一致")
    if version_doc.get("binaries"):
        raise ReleaseError("BINARY_PRESENT", "下载副本包含二进制资产")

    payload = {
        "status": "PASS",
        "files": sorted(expected),
        "commit": version_doc.get("source_commit"),
        "sha256": expected,
        "generated_at": utc_now(),
    }
    write_report(args.report, payload)
    emit(payload, args.json)
    return 0


# --------------------------------------------------------------------------- #
# repro
# --------------------------------------------------------------------------- #


def cmd_repro(args) -> int:
    repo = Path(args.repo).resolve()
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for index in (1, 2):
            if index == 2:
                # 跨越时间边界，确保归档不含易变字段（如 generated_at）。
                time.sleep(1.1)
            out = Path(tmp) / f"build{index}"
            subprocess.run(
                [
                    sys.executable, str(Path(__file__)),
                    "build", "--repo", str(repo), "--output", str(out), "--tag", args.tag, "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            version = version_from_tag(args.tag)
            results.append({
                "archive": sha256_file(out / f"agent-workflow-src-{version}.tar.gz"),
                "notes": sha256_file(out / f"agent-workflow-release-notes-{version}.md"),
                "sums": sha256_file(out / "SHA256SUMS"),
            })

    if results[0]["archive"] != results[1]["archive"]:
        raise ReleaseError("NOT_REPRODUCIBLE", "两次构建的源码包哈希不同", results)
    if results[0]["notes"] != results[1]["notes"]:
        raise ReleaseError("NOT_REPRODUCIBLE", "两次构建的 Release Notes 哈希不同", results)

    payload = {
        "status": "PASS",
        "archive_sha256": results[0]["archive"],
        "builds": results,
        "note": "version.json 含 generated_at，因此不参与可复现性比较",
    }
    write_report(args.report, payload)
    emit(payload, args.json)
    return 0


# --------------------------------------------------------------------------- #
# check-pipeline
# --------------------------------------------------------------------------- #


def cmd_check_pipeline(args) -> int:
    config_path = Path(args.config)
    if not config_path.is_file():
        raise ReleaseError("CONFIG_MISSING", f"缺少 CI 配置: {config_path}")
    text = config_path.read_text(encoding="utf-8")

    version = args.version
    if not version:
        version_file = Path(args.repo or ".") / ".agent/VERSION"
        version = version_file.read_text(encoding="utf-8").strip()
    assets = asset_names(version)

    problems = []
    if f"{args.tag or 'v' + version}:" not in text:
        problems.append(f".cnb.yml 缺少 {args.tag or 'v' + version} 的触发配置")
    if "tag_push:" not in text:
        problems.append(".cnb.yml 缺少 tag_push 事件")
    for name in assets:
        if text.count(name) < 2:
            problems.append(f"资产未同时出现在上传与下载阶段: {name}")

    if "go build" in text or "workflow-check_" in text or ".agent/engine" in text:
        problems.append(".cnb.yml 仍引用旧 Engine 构建")

    required_stages = ("guard", "build", "verify", "release", "upload", "download")
    lowered = text.lower()
    for stage in required_stages:
        if stage not in lowered:
            problems.append(f".cnb.yml 缺少发布阶段关键字: {stage}")

    if problems:
        raise ReleaseError("PIPELINE_INCOMPLETE", "CI 配置未覆盖完整发布链路", {"problems": problems})

    payload = {
        "status": "PASS",
        "config": str(config_path),
        "assets": assets,
        "generated_at": utc_now(),
    }
    write_report(args.report, payload)
    emit(payload, args.json)
    return 0


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #


def cmd_report(args) -> int:
    reports_dir = Path(args.reports)
    if not reports_dir.is_dir():
        raise ReleaseError("REPORTS_MISSING", f"报告目录不存在: {reports_dir}")

    summary = {}
    failed = []
    for path in sorted(reports_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            failed.append({"report": path.name, "error": "JSON 无法解析"})
            continue
        status = data.get("status", "UNKNOWN")
        summary[path.stem] = status
        if status != "PASS":
            failed.append({"report": path.name, "status": status})

    if args.require_all and failed:
        raise ReleaseError("REPORT_FAILED", "存在未通过的报告", {"reports": summary, "failed": failed})

    payload = {"status": "PASS" if not failed else "FAIL", "reports": summary, "failed": failed}
    emit(payload, args.json)
    return 0 if not failed else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release.py", description="Workflow V4 发布工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_guard = sub.add_parser("guard")
    p_guard.add_argument("--repo", required=True)
    p_guard.add_argument("--tag", required=True)
    p_guard.add_argument("--expected-commit")
    p_guard.add_argument("--expected-commit-source")
    p_guard.add_argument("--report")
    p_guard.add_argument("--json", action="store_true")
    p_guard.set_defaults(func=cmd_guard)

    p_build = sub.add_parser("build")
    p_build.add_argument("--repo", required=True)
    p_build.add_argument("--output", required=True)
    p_build.add_argument("--tag", required=True)
    p_build.add_argument("--report")
    p_build.add_argument("--json", action="store_true")
    p_build.set_defaults(func=cmd_build)

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--dir", required=True)
    p_verify.add_argument("--repo")
    p_verify.add_argument("--version")
    p_verify.add_argument("--tag")
    p_verify.add_argument("--commit")
    p_verify.add_argument("--report")
    p_verify.add_argument("--json", action="store_true")
    p_verify.set_defaults(func=cmd_verify)

    p_uploaded = sub.add_parser("verify-uploaded")
    p_uploaded.add_argument("--assets", default="dist")
    p_uploaded.add_argument("--downloads", required=True)
    p_uploaded.add_argument("--manifest", required=True)
    p_uploaded.add_argument("--commit")
    p_uploaded.add_argument("--report")
    p_uploaded.add_argument("--json", action="store_true")
    p_uploaded.set_defaults(func=cmd_verify_uploaded)

    p_repro = sub.add_parser("repro")
    p_repro.add_argument("--repo", required=True)
    p_repro.add_argument("--tag", required=True)
    p_repro.add_argument("--report")
    p_repro.add_argument("--json", action="store_true")
    p_repro.set_defaults(func=cmd_repro)

    p_pipe = sub.add_parser("check-pipeline")
    p_pipe.add_argument("--config", default=".cnb.yml")
    p_pipe.add_argument("--repo", default=".")
    p_pipe.add_argument("--version")
    p_pipe.add_argument("--tag")
    p_pipe.add_argument("--report")
    p_pipe.add_argument("--json", action="store_true")
    p_pipe.set_defaults(func=cmd_check_pipeline)

    p_report = sub.add_parser("report")
    p_report.add_argument("--reports", required=True)
    p_report.add_argument("--require-all", action="store_true")
    p_report.add_argument("--json", action="store_true")
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ReleaseError as exc:
        payload = {"status": "BLOCKED", "code": exc.code, "message": exc.message}
        if exc.detail:
            payload["detail"] = exc.detail
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"BLOCKED [{exc.code}] {exc.message}")
            for key, value in (exc.detail or {}).items():
                print(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
