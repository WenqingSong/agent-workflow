#!/usr/bin/env python3
"""Workflow V3.1.0 release engineering tool.

职责边界：

- 只从**真实 Git 事实**推导版本、Commit 与资产内容，不接受脚本里写死的 Commit；
- 只调用 P07 的真实分发包工具生成 Source Distribution，不另造分发契约；
- 构建产物确定性：无时间戳、无主机路径、无随机字段；
- 所有校验失败一律 fail closed（非零退出 + FAIL 报告），不存在“退出码为 0 就当作
  校验通过”的分支。

子命令：

    guard            Tag / Annotated / Commit / VERSION 一致性守卫
    build            四平台 Binary + Source Distribution + SHA256SUMS + version.json
    verify-assets    资产集合、SHA256SUMS、version.json、Binary 平台与版本注入校验
    verify-dist      Source Distribution 成员 / MANIFEST / 七角色完整性校验
    verify-uploaded  用平台侧重新下载的副本核对上传结果（附件验证）
    promote-gate     promote 前置门槛：没有 PASS 验证报告就不得 promote / 标记 latest
    repro            两次独立构建，比较是否可复现

退出码：0 通过；1 校验失败 / 阻塞；2 输入或前置条件错误。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

FRAMEWORK = "agent-workflow"
BINARY = "workflow-check"
MODULE = "workflow-v2-engine"
REPOSITORY = "https://cnb.cool/framework-station/agent-workflow"
RELEASE_NOTES_SOURCE = "docs/releases/v{version}.md"
VERSION_FILE = ".agent/VERSION"

# 固定构建矩阵：顺序稳定，写入 version.json 与报告时保持一致。
BUILD_MATRIX = (
    ("linux", "amd64", ""),
    ("linux", "arm64", ""),
    ("darwin", "arm64", ""),
    ("windows", "amd64", ".exe"),
)

# 在这台机器上真实执行 Binary 的平台；其余平台只能做静态校验。
RUNTIME_VERIFIED = (("linux", "amd64"),)

BUILD_FLAGS = ("-trimpath", "-buildvcs=false", "-mod=readonly")
LDFLAGS_TEMPLATE = "-s -w -X main.version={version} -X main.commit={commit} -X main.buildSource=release"

# 分发包必须包含的关键成员（七角色 / 全部 Specs / Directive / DANGER / 工具 / 引擎 / 模板）。
REQUIRED_DIST_PATHS = (
    "README.md",
    "PACKAGE_MANIFEST.md",
    ".cnb.yml",
    ".agent/VERSION",
    ".agent/README.md",
    ".agent/roles/TaskBuilderPrompt.md",
    ".agent/roles/AnalystAgent.md",
    ".agent/roles/ArchitectAgent.md",
    ".agent/roles/CoderAgent.md",
    ".agent/roles/CleanerAgent.md",
    ".agent/roles/OwnerGateAgent.md",
    ".agent/roles/DelivererAgent.md",
    ".agent/specs/WorkflowAgentDirective.md",
    ".agent/specs/WorkflowLoopCircuitBreaker.md",
    ".agent/specs/AgentCollaborationSpecification.md",
    ".agent/specs/Five-AgentResponsibilityBoundary.md",
    ".agent/specs/WorkflowAdoption.md",
    ".agent/adoption/workflow-adoption",
    ".agent/adoption/workflow_adoption.py",
    ".agent/engine/go.mod",
    ".agent/engine/go.sum",
    ".agent/engine/cmd/workflow-check/main.go",
    ".agent/engine/cmd/workflow-check/version.go",
    ".agent/engine/internal/workflow/gates.go",
    ".agent/templates/PROJECT_ADAPTATION.template.md",
    ".agent/templates/workflow.yaml.template",
    ".agent/templates/upstream.yaml.template",
    ".agent/templates/registry/registry.template.md",
    "docs/design/agent-workflow.md",
)

# 正式源码包绝不能包含的内容（宿主数据、运行时证据、Git 历史）。
FORBIDDEN_DIST_PATHS = (
    "AGENTS.md",
    ".agent/workflow.yaml",
    ".agent/upstream.yaml",
    ".agent/bin/workflow-check",
)
FORBIDDEN_DIST_PREFIXES = (".git/", ".agent/tasks/", ".agent/registry/")

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_PRECONDITION = 2


class ReleaseError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = EXIT_FAIL, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def run(
    cmd: list[str],
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        env=env,
    )
    if check and proc.returncode != 0:
        raise ReleaseError(
            "COMMAND_FAILED",
            f"命令失败（exit {proc.returncode}）: {' '.join(cmd)}",
            details={"stdout": proc.stdout, "stderr": proc.stderr},
        )
    return proc


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = run(["git", "-C", str(repo), *args], check=check)
    return proc.stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise ReleaseError("REPORT_MISSING", f"缺少必需文件: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReleaseError("REPORT_INVALID", f"{path} 不是合法 JSON: {exc}") from exc


def emit(report: dict[str, Any], as_json: bool, exit_code: int) -> int:
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(f"STATUS: {report.get('status', 'UNKNOWN')}")
        for key in ("command", "tag", "version", "source_commit", "assets", "failures"):
            if key in report and report[key] not in (None, "", [], {}):
                print(f"{key}: {report[key]}")
    return exit_code


def tag_for_version(version: str) -> str:
    return f"v{version}"


def version_from_tag(tag: str) -> str:
    if not tag.startswith("v"):
        raise ReleaseError("TAG_FORMAT", f"Tag 必须以 v 开头: {tag!r}")
    return tag[1:]


def read_version_file(repo: Path) -> str:
    path = repo / VERSION_FILE
    if not path.is_file():
        raise ReleaseError("VERSION_MISSING", f"缺少 {VERSION_FILE}")
    value = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ReleaseError("VERSION_INVALID", f"{VERSION_FILE} 不是语义版本: {value!r}")
    return value


def binary_name(version: str, goos: str, goarch: str, suffix: str) -> str:
    return f"{BINARY}_{version}_{goos}_{goarch}{suffix}"


def release_notes_asset(version: str) -> str:
    return f"{FRAMEWORK}-release-notes-{version}.md"


def dist_archive_name(commit: str) -> str:
    return f"{FRAMEWORK}-src-{commit[:12]}.tar.gz"


def go_env() -> dict[str, str]:
    env = os.environ.copy()
    env["CGO_ENABLED"] = "0"
    env["GOWORK"] = "off"
    env["GOFLAGS"] = "-mod=readonly"
    return env


def go_toolchain() -> str:
    proc = run(["go", "env", "GOVERSION"], check=False)
    value = proc.stdout.strip()
    return value or "unknown"


# ---------------------------------------------------------------------------
# guard
# ---------------------------------------------------------------------------


def check_tag(repo: Path, tag: str) -> dict[str, Any]:
    """校验 Tag 存在、为 Annotated Tag、指向可解析 Commit、且与 .agent/VERSION 一致。"""
    if not repo.is_dir():
        raise ReleaseError("REPO_MISSING", f"仓库目录不存在: {repo}", EXIT_PRECONDITION)
    if not (repo / ".git").exists():
        raise ReleaseError("REPO_NOT_GIT", f"不是 Git 仓库根目录: {repo}", EXIT_PRECONDITION)
    if not tag or tag.strip() != tag:
        raise ReleaseError("TAG_INVALID", f"Tag 名为空或包含首尾空白: {tag!r}", EXIT_PRECONDITION)

    expected_version = read_version_file(repo) if (repo / VERSION_FILE).is_file() else None
    ref = f"refs/tags/{tag}"

    proc = run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", ref], check=False)
    if proc.returncode != 0:
        raise ReleaseError("TAG_NOT_FOUND", f"Tag 不存在: {tag}")

    obj_type = git(repo, "cat-file", "-t", ref)
    if obj_type != "tag":
        raise ReleaseError(
            "TAG_NOT_ANNOTATED",
            f"Tag 不是 Annotated Tag（类型 {obj_type}）；发布 Tag 必须带注释对象",
        )

    commit = git(repo, "rev-list", "-n", "1", ref)
    if not FULL_SHA.match(commit):
        raise ReleaseError("COMMIT_UNRESOLVED", f"无法把 Tag {tag} 解析为完整 40 位 Commit: {commit!r}")

    tagged_version = git(repo, "show", f"{commit}:{VERSION_FILE}", check=False).strip()
    if not tagged_version:
        raise ReleaseError("VERSION_UNREADABLE", f"Tag 指向的 Commit 缺少 {VERSION_FILE}")

    tag_version = version_from_tag(tag)
    if tagged_version != tag_version:
        raise ReleaseError(
            "VERSION_TAG_MISMATCH",
            f"Tag {tag} 与 {VERSION_FILE} 不一致：Tag 表示 {tag_version}，文件为 {tagged_version}",
        )

    head = git(repo, "rev-parse", "HEAD")
    tagger = git(repo, "for-each-ref", "--format=%(taggername) %(taggeremail)", ref)

    return {
        "status": "GUARD_OK",
        "command": "guard",
        "repository": REPOSITORY,
        "tag": tag,
        "version": tagged_version,
        "source_commit": commit,
        "head_commit": head,
        "head_at_tag": head == commit,
        "annotated": True,
        "tagger": tagger,
    }


def cmd_guard(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    report = check_tag(repo, args.tag)
    if args.require_head_at_tag and not report["head_at_tag"]:
        report["status"] = "GUARD_FAILED"
        report["failures"] = [
            f"HEAD {report['head_commit']} 与 Tag {args.tag} 指向的 Commit {report['source_commit']} 不一致；"
            "发布构建必须从被 Tag 的精确 Commit 进行"
        ]
        if args.report:
            write_json(Path(args.report), report)
        return emit(report, args.json, EXIT_FAIL)
    if args.report:
        write_json(Path(args.report), report)
    return emit(report, args.json, EXIT_OK)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def repo_status_lines(repo: Path) -> list[str]:
    return [line for line in git(repo, "status", "--porcelain").splitlines() if line.strip()]


def filter_own_output(lines: list[str], rel_output: str) -> list[str]:
    kept: list[str] = []
    for line in lines:
        path = line[3:].strip().strip('"').rstrip("/")
        if path == rel_output or path.startswith(rel_output + "/"):
            continue
        kept.append(line)
    return kept


def require_clean(repo: Path, output: Path) -> None:
    try:
        rel_output = output.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        rel_output = ""
    lines = repo_status_lines(repo)
    if rel_output:
        lines = filter_own_output(lines, rel_output)
    if lines:
        raise ReleaseError(
            "SOURCE_DIRTY",
            "发布构建要求工作区完全干净（含未跟踪文件）",
            details={"paths": lines},
        )


def build_binary(repo: Path, output: Path, version: str, commit: str, goos: str, goarch: str, suffix: str) -> Path:
    target = output / binary_name(version, goos, goarch, suffix)
    env = go_env()
    env["GOOS"] = goos
    env["GOARCH"] = goarch
    ldflags = LDFLAGS_TEMPLATE.format(version=version, commit=commit)
    cmd = [
        "go",
        "build",
        *BUILD_FLAGS,
        "-ldflags",
        ldflags,
        "-o",
        str(target),
        "./cmd/workflow-check",
    ]
    run(cmd, cwd=repo / ".agent" / "engine", env=env)
    if not target.is_file() or target.stat().st_size == 0:
        raise ReleaseError("BUILD_EMPTY", f"构建产物缺失或为空: {target}")
    return target


def build_source_distribution(repo: Path, output: Path) -> dict[str, Any]:
    tool = repo / ".agent/adoption/workflow-adoption"
    if not tool.is_file():
        raise ReleaseError("ADOPTION_TOOL_MISSING", f"缺少 P07 分发包工具: {tool}", EXIT_PRECONDITION)
    proc = run(
        [sys.executable, str(tool), "dist", "--source", str(repo), "--output", str(output), "--json"],
        check=False,
    )
    if proc.returncode != 0:
        raise ReleaseError(
            "DIST_FAILED",
            "P07 分发包工具拒绝生成 Source Distribution",
            details={"stdout": proc.stdout, "stderr": proc.stderr},
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseError("DIST_OUTPUT", f"分发包工具输出不是 JSON: {exc}", details={"stdout": proc.stdout})
    archive = Path(payload["archive"])
    if not archive.is_file():
        raise ReleaseError("DIST_MISSING", f"分发包归档不存在: {archive}")
    return payload


def asset_manifest(version: str, commit: str) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for goos, goarch, suffix in BUILD_MATRIX:
        level = "runtime" if (goos, goarch) in RUNTIME_VERIFIED else "static"
        assets.append(
            {
                "name": binary_name(version, goos, goarch, suffix),
                "kind": "binary",
                "goos": goos,
                "goarch": goarch,
                "verification_level": level,
            }
        )
    assets.append(
        {
            "name": dist_archive_name(commit),
            "kind": "source-distribution",
            "verification_level": "content",
        }
    )
    assets.append(
        {
            "name": f"{dist_archive_name(commit)}.sha256",
            "kind": "source-distribution-checksum",
            "verification_level": "derived",
        }
    )
    assets.append(
        {
            "name": release_notes_asset(version),
            "kind": "release-notes",
            "verification_level": "content",
        }
    )
    return assets


def write_checksums(assets_dir: Path, names: list[str]) -> Path:
    lines = []
    for name in sorted(names):
        path = assets_dir / name
        if not path.is_file():
            raise ReleaseError("ASSET_MISSING", f"生成 SHA256SUMS 时缺少资产: {name}")
        lines.append(f"{sha256_file(path)}  {name}")
    target = assets_dir / "SHA256SUMS"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def cmd_build(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    output = Path(args.output)
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()
    tag = args.tag

    guard = check_tag(repo, tag)
    version = guard["version"]
    commit = guard["source_commit"]
    if guard["head_commit"] != commit:
        raise ReleaseError(
            "HEAD_NOT_AT_TAG",
            f"HEAD {guard['head_commit']} 不等于 Tag {tag} 指向的 {commit}；发布构建必须从该 Commit 进行",
        )
    require_clean(repo, output)

    output.mkdir(parents=True, exist_ok=True)
    reports_dir = output / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    built: list[dict[str, Any]] = []
    for goos, goarch, suffix in BUILD_MATRIX:
        path = build_binary(repo, output, version, commit, goos, goarch, suffix)
        built.append(
            {
                "name": path.name,
                "goos": goos,
                "goarch": goarch,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )

    dist = build_source_distribution(repo, output)
    if dist.get("source_commit") != commit:
        raise ReleaseError(
            "DIST_COMMIT_MISMATCH",
            f"分发包 Commit {dist.get('source_commit')} 与 Tag Commit {commit} 不一致",
        )
    if dist.get("framework_version") != version:
        raise ReleaseError(
            "DIST_VERSION_MISMATCH",
            f"分发包版本 {dist.get('framework_version')} 与 {version} 不一致",
        )

    notes_source = repo / RELEASE_NOTES_SOURCE.format(version=version)
    if not notes_source.is_file():
        raise ReleaseError(
            "NOTES_MISSING",
            f"缺少 Release Notes 源文件: {notes_source.relative_to(repo).as_posix()}",
            EXIT_PRECONDITION,
        )
    notes_asset_path = output / release_notes_asset(version)
    notes_asset_path.write_bytes(notes_source.read_bytes())

    names = [a["name"] for a in asset_manifest(version, commit)]
    checksums_path = write_checksums(output, names)
    manifest = dist["manifest"]
    manifest_bytes_sha = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()

    assets_payload = []
    for spec in asset_manifest(version, commit):
        path = output / spec["name"]
        assets_payload.append(
            {
                "name": spec["name"],
                "kind": spec["kind"],
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
                "verification_level": spec["verification_level"],
                "goos": spec.get("goos", ""),
                "goarch": spec.get("goarch", ""),
            }
        )

    version_json = {
        "format": 1,
        "framework": FRAMEWORK,
        "binary": BINARY,
        "version": version,
        "tag": tag,
        "source_commit": commit,
        "source_tree": dist["manifest"]["source"]["tree"],
        "source_repository": REPOSITORY,
        "published_status": "NOT_RELEASED",
        "release_channel": "release-candidate",
        "go": {
            "toolchain": go_toolchain(),
            "module": MODULE,
            "cgo_enabled": "0",
        },
        "build": {
            "command": "go build",
            "package": "./cmd/workflow-check",
            "go_flags": list(BUILD_FLAGS),
            "ldflags": LDFLAGS_TEMPLATE.format(version=version, commit=commit),
            "env": {"CGO_ENABLED": "0", "GOWORK": "off", "GOFLAGS": "-mod=readonly"},
            "injected": ["main.version", "main.commit", "main.buildSource"],
            "timestamps_embedded": False,
        },
        "platforms": [
            {
                "goos": spec["goos"],
                "goarch": spec["goarch"],
                "asset": spec["name"],
                "verification_level": spec["verification_level"],
            }
            for spec in asset_manifest(version, commit)
            if spec["kind"] == "binary"
        ],
        "assets": assets_payload,
        "checksums": {
            "file": "SHA256SUMS",
            "sha256": sha256_file(checksums_path),
            "covers": sorted(names),
            "excludes": ["SHA256SUMS", "version.json"],
            "rule": (
                "SHA256SUMS 只覆盖二进制、源码包、源码包校验文件与 Release Notes；"
                "version.json 记录 SHA256SUMS 的摘要并自排除。两者不存在互相引用的哈希环。"
            ),
        },
        "distribution": {
            "file": dist["archive"].rsplit("/", 1)[-1],
            "sha256": dist["sha256"],
            "top_level": f"{FRAMEWORK}-src-{commit[:12]}",
            "format": manifest["format"],
            "kind": manifest["kind"],
            "framework_version": manifest["framework_version"],
            "manifest_sha256": manifest_bytes_sha,
            "manifest_file_count": len(manifest["files"]),
            "managed_count": len(manifest["install"]["managed"]),
            "package_only_count": len(manifest["install"]["package_only"]),
            "compatibility": manifest["compatibility"],
            "release_tag": manifest["release"]["tag"],
            "release_url": manifest["release"]["url"],
        },
        "release_notes": {
            "source": RELEASE_NOTES_SOURCE.format(version=version),
            "asset": release_notes_asset(version),
        },
    }
    write_json(output / "version.json", version_json)

    report = {
        "status": "BUILD_OK",
        "command": "build",
        "tag": tag,
        "version": version,
        "source_commit": commit,
        "output": str(output),
        "assets": [a["name"] for a in assets_payload],
        "distribution_file_count": version_json["distribution"]["manifest_file_count"],
        "go_toolchain": version_json["go"]["toolchain"],
    }
    write_json(reports_dir / "build.json", report)
    return emit(report, args.json, EXIT_OK)


# ---------------------------------------------------------------------------
# 二进制静态 / 运行时校验
# ---------------------------------------------------------------------------


def elf_identity(data: bytes) -> tuple[int, int] | None:
    if data[:4] != b"\x7fELF":
        return None
    ei_class = data[4]
    endian = "<" if data[5] == 1 else ">"
    machine = struct.unpack(endian + "H", data[18:20])[0]
    return ei_class, machine


def pe_machine(data: bytes) -> int | None:
    if data[:2] != b"MZ":
        return None
    offset = struct.unpack("<I", data[0x3C:0x40])[0]
    if data[offset : offset + 4] != b"PE\x00\x00":
        return None
    return struct.unpack("<H", data[offset + 4 : offset + 6])[0]


def macho_cputype(data: bytes) -> int | None:
    if len(data) < 8:
        return None
    magic = struct.unpack(">I", data[:4])[0]
    if magic == 0xFEEDFACF:
        endian = ">"
    elif struct.unpack("<I", data[:4])[0] == 0xFEEDFACF:
        endian = "<"
    else:
        return None
    return struct.unpack(endian + "i", data[4:8])[0]


ELF_MACHINE = {"amd64": 0x3E, "arm64": 0xB7}
PE_MACHINE = {"amd64": 0x8664, "arm64": 0xAA64}
MACHO_CPUTYPE = {"amd64": 0x01000007, "arm64": 0x0100000C}


def check_binary_header(path: Path, goos: str, goarch: str) -> list[str]:
    data = path.read_bytes()
    failures: list[str] = []
    if goos == "linux":
        ident = elf_identity(data)
        if ident is None:
            return [f"{path.name}: 不是 ELF 文件"]
        ei_class, machine = ident
        if ei_class != 2:
            failures.append(f"{path.name}: ELF 不是 64 位（class={ei_class}）")
        if machine != ELF_MACHINE[goarch]:
            failures.append(f"{path.name}: ELF machine=0x{machine:x} 与 {goarch} 不符")
    elif goos == "windows":
        if pe_machine(data) is None:
            failures.append(f"{path.name}: 不是 PE 文件")
        elif pe_machine(data) != PE_MACHINE[goarch]:
            failures.append(f"{path.name}: PE machine 与 {goarch} 不符")
    elif goos == "darwin":
        cputype = macho_cputype(data)
        if cputype is None:
            failures.append(f"{path.name}: 不是 64 位 Mach-O 文件")
        elif cputype != MACHO_CPUTYPE[goarch]:
            failures.append(f"{path.name}: Mach-O cputype 与 {goarch} 不符")
    else:
        failures.append(f"{path.name}: 未支持的 GOOS {goos}")
    return failures


def check_build_info(path: Path, version: str, commit: str, goos: str, goarch: str) -> list[str]:
    proc = run(["go", "version", "-m", str(path)], check=False)
    if proc.returncode != 0:
        return [f"{path.name}: go version -m 失败: {proc.stderr.strip()}"]
    fields: dict[str, str] = {}
    build_settings: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 2 and parts[0] in ("path", "mod"):
            fields[parts[0]] = parts[1]
        if len(parts) >= 2 and parts[0] == "build":
            build_settings.append(parts[1])
    failures: list[str] = []
    if fields.get("path") != f"{MODULE}/cmd/workflow-check":
        failures.append(f"{path.name}: buildinfo path 非预期: {fields.get('path')!r}")
    joined = "\n".join(build_settings)
    for expected in (f"GOOS={goos}", f"GOARCH={goarch}", "-trimpath=true", "CGO_ENABLED=0"):
        if expected not in joined:
            failures.append(f"{path.name}: buildinfo 缺少 {expected}")
    if "-buildvcs=true" in joined:
        failures.append(f"{path.name}: buildinfo 显示 -buildvcs=true，破坏可复现构建")

    # Go 的 buildinfo 不记录 -ldflags，因此用被注入的字面常量核对：
    # -X main.commit / -X main.version 会作为只读字符串进入二进制数据段。
    data = path.read_bytes()
    if commit.encode() not in data:
        failures.append(f"{path.name}: 二进制中找不到注入的 Commit 字面值 {commit}")
    if version.encode() not in data:
        failures.append(f"{path.name}: 二进制中找不到注入的版本字面值 {version}")
    return failures


def check_runtime_version(path: Path, version: str, commit: str) -> list[str]:
    proc = run([str(path), "version", "--json"], check=False)
    if proc.returncode != 0:
        return [f"{path.name}: 运行时 version 失败（exit {proc.returncode}）: {proc.stderr.strip()}"]
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return [f"{path.name}: 运行时 version 输出不是 JSON: {exc}"]
    failures = []
    if payload.get("framework_version") != version:
        failures.append(f"{path.name}: 运行时 framework_version={payload.get('framework_version')!r} != {version}")
    if payload.get("source_commit") != commit:
        failures.append(f"{path.name}: 运行时 source_commit={payload.get('source_commit')!r} != {commit}")
    if payload.get("build_source") != "release":
        failures.append(f"{path.name}: 运行时 build_source={payload.get('build_source')!r} != release")
    return failures


# ---------------------------------------------------------------------------
# Source Distribution 校验
# ---------------------------------------------------------------------------


def verify_distribution(archive: Path, version: str, commit: str) -> dict[str, Any]:
    if not archive.is_file():
        raise ReleaseError("DIST_MISSING", f"分发包不存在: {archive}")
    if archive.stat().st_size == 0:
        raise ReleaseError("DIST_EMPTY", f"分发包为空: {archive}")

    failures: list[str] = []
    top = f"{FRAMEWORK}-src-{commit[:12]}"
    members: dict[str, tuple[bytes, int]] = {}

    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                name = member.name
                if member.isdir():
                    continue
                if not member.isfile():
                    failures.append(f"{name}: 非普通文件（type={member.type!r}）")
                    continue
                if name.startswith("/") or ".." in PurePosixPath(name).parts:
                    failures.append(f"{name}: 不安全路径")
                    continue
                parts = PurePosixPath(name).parts
                if len(parts) < 2 or parts[0] != top:
                    failures.append(f"{name}: 顶层目录不是 {top}/")
                    continue
                rel = str(PurePosixPath(*parts[1:]))
                members[rel] = (tar.extractfile(member).read(), member.mode)
    except tarfile.TarError as exc:
        raise ReleaseError("DIST_CORRUPT", f"分发包无法读取: {exc}")

    if "MANIFEST.json" not in members:
        raise ReleaseError("DIST_MANIFEST_MISSING", "分发包缺少 MANIFEST.json")
    manifest_bytes, _ = members.pop("MANIFEST.json")
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise ReleaseError("DIST_MANIFEST_INVALID", f"MANIFEST.json 不是合法 JSON: {exc}")

    for path, label in (
        (manifest.get("format"), "format"),
        (manifest.get("kind"), "kind"),
    ):
        if label == "format" and path != 1:
            failures.append(f"MANIFEST.json format 非预期: {path!r}")
        if label == "kind" and path != "source-distribution":
            failures.append(f"MANIFEST.json kind 非预期: {path!r}")
    source = manifest.get("source") or {}
    if source.get("commit") != commit:
        failures.append(f"MANIFEST.json source.commit={source.get('commit')!r} != {commit}")
    if not FULL_SHA.match(str(source.get("tree", ""))):
        failures.append("MANIFEST.json source.tree 不是完整 40 位 SHA")
    if manifest.get("framework_version") != version:
        failures.append(f"MANIFEST.json framework_version={manifest.get('framework_version')!r} != {version}")
    release = manifest.get("release") or {}
    if release.get("tag") or release.get("url"):
        failures.append("MANIFEST.json release.tag / release.url 不得在正式发布前填写")

    compatibility = manifest.get("compatibility") or {}
    if compatibility.get("config_schema") != 1:
        failures.append(f"MANIFEST.json config_schema 非预期: {compatibility.get('config_schema')!r}")

    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ReleaseError("DIST_MANIFEST_INVALID", "MANIFEST.json 缺少 files 列表")
    declared = {e.get("path"): e for e in entries if isinstance(e, dict)}
    if len(declared) != len(entries):
        failures.append("MANIFEST.json files 存在重复或非法条目")

    actual = set(members)
    missing = sorted(set(declared) - actual)
    extra = sorted(actual - set(declared))
    if missing:
        failures.append(f"MANIFEST 声明但归档缺失: {missing}")
    if extra:
        failures.append(f"归档存在但 MANIFEST 未声明: {extra}")

    managed: list[str] = []
    package_only: list[str] = []
    for path, entry in sorted(declared.items()):
        if path not in members:
            continue
        data, mode = members[path]
        digest = hashlib.sha256(data).hexdigest()
        if entry.get("sha256") != digest:
            failures.append(f"{path}: SHA256 与 MANIFEST 不一致")
        expected_mode = "100755" if mode & 0o111 else "100644"
        if entry.get("mode") != expected_mode:
            failures.append(f"{path}: mode {entry.get('mode')!r} 与归档 {expected_mode} 不一致")
        install = entry.get("install")
        if install == "managed":
            managed.append(path)
        elif install == "package_only":
            package_only.append(path)
        else:
            failures.append(f"{path}: install 分类非 managed/package_only: {install!r}")

    install = manifest.get("install") or {}
    if sorted(install.get("managed") or []) != sorted(managed):
        failures.append("MANIFEST.json install.managed 与 files[].install 分类不一致")
    if sorted(install.get("package_only") or []) != sorted(package_only):
        failures.append("MANIFEST.json install.package_only 与 files[].install 分类不一致")

    for forbidden in FORBIDDEN_DIST_PATHS:
        if forbidden in members:
            failures.append(f"分发包包含禁止内容: {forbidden}")
    for prefix in FORBIDDEN_DIST_PREFIXES:
        leaked = [p for p in members if p == prefix.rstrip("/") or p.startswith(prefix)]
        if leaked:
            failures.append(f"分发包包含禁止内容: {leaked[:3]}")

    for required in REQUIRED_DIST_PATHS:
        if required not in members:
            failures.append(f"分发包缺少必需成员: {required}")
    version_member = members.get(VERSION_FILE)
    if version_member is None:
        failures.append(f"分发包缺少 {VERSION_FILE}")
    elif version_member[0].decode("utf-8", "replace").strip() != version:
        failures.append(f"分发包内 {VERSION_FILE} 与 {version} 不一致")

    roles = sorted(p for p in members if p.startswith(".agent/roles/"))
    specs = sorted(p for p in members if p.startswith(".agent/specs/"))
    if len(roles) != 7:
        failures.append(f"七角色不完整：实际 {len(roles)} 个角色文件 {roles}")
    if len(specs) != 5:
        failures.append(f"Specs 不完整：实际 {len(specs)} 个 {specs}")

    report = {
        "status": "PASS" if not failures else "FAIL",
        "command": "verify-dist",
        "archive": archive.name,
        "archive_sha256": sha256_file(archive),
        "source_commit": commit,
        "version": version,
        "manifest_sha256": manifest_sha,
        "member_count": len(members),
        "manifest_file_count": len(declared),
        "managed_count": len(managed),
        "package_only_count": len(package_only),
        "role_count": len(roles),
        "spec_count": len(specs),
        "top_level": top,
        "failures": failures,
    }
    return report


# ---------------------------------------------------------------------------
# verify-assets
# ---------------------------------------------------------------------------


def sha256sums_entries(assets_dir: Path) -> dict[str, str]:
    path = assets_dir / "SHA256SUMS"
    if not path.is_file():
        raise ReleaseError("CHECKSUMS_MISSING", f"缺少 SHA256SUMS: {path}")
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ReleaseError("CHECKSUMS_FORMAT", f"SHA256SUMS 行格式非法: {line!r}")
        entries[match.group(2)] = match.group(1)
    if not entries:
        raise ReleaseError("CHECKSUMS_EMPTY", "SHA256SUMS 为空")
    return entries


def verify_assets_dir(assets_dir: Path, expected_version: str, expected_commit: str) -> dict[str, Any]:
    failures: list[str] = []
    version_json_path = assets_dir / "version.json"
    if not version_json_path.is_file():
        raise ReleaseError("VERSION_JSON_MISSING", f"缺少 version.json: {version_json_path}")

    payload = read_json(version_json_path)
    if payload.get("version") != expected_version:
        failures.append(f"version.json version={payload.get('version')!r} != {expected_version}")
    if payload.get("tag") != tag_for_version(expected_version):
        failures.append(f"version.json tag={payload.get('tag')!r} != {tag_for_version(expected_version)}")
    if payload.get("source_commit") != expected_commit:
        failures.append(f"version.json source_commit={payload.get('source_commit')!r} != {expected_commit}")
    if payload.get("published_status") != "NOT_RELEASED":
        failures.append("version.json published_status 必须是 NOT_RELEASED（本任务不做正式发布）")

    declared_assets = payload.get("assets") or []
    if not declared_assets:
        raise ReleaseError("VERSION_JSON_ASSETS", "version.json 缺少 assets 列表")
    names = [a["name"] for a in declared_assets]
    if len(set(names)) != len(names):
        failures.append("version.json assets 存在重名")

    expected_names = [a["name"] for a in asset_manifest(expected_version, expected_commit)]
    if sorted(names) != sorted(expected_names):
        failures.append(
            f"version.json 资产集合与构建矩阵不一致: 缺 {sorted(set(expected_names) - set(names))} 多 {sorted(set(names) - set(expected_names))}"
        )

    actual_files = sorted(
        p.name for p in assets_dir.iterdir() if p.is_file() and p.name not in ("SHA256SUMS", "version.json")
    )
    if sorted(names) != actual_files:
        failures.append(
            f"资产目录内容与 version.json 不一致: 目录多 {sorted(set(actual_files) - set(names))} 缺 {sorted(set(names) - set(actual_files))}"
        )

    checksums = sha256sums_entries(assets_dir)
    if sorted(checksums) != sorted(names):
        failures.append(
            f"SHA256SUMS 覆盖集合与资产不一致: 缺 {sorted(set(names) - set(checksums))} 多 {sorted(set(checksums) - set(names))}"
        )
    recorded = payload.get("checksums") or {}
    if recorded.get("sha256") != sha256_file(assets_dir / "SHA256SUMS"):
        failures.append("version.json 记录的 SHA256SUMS 摘要与实际文件不一致")
    if sorted(recorded.get("covers") or []) != sorted(names):
        failures.append("version.json checksums.covers 与 assets 不一致")
    if sorted(recorded.get("excludes") or []) != ["SHA256SUMS", "version.json"]:
        failures.append("version.json checksums.excludes 必须精确为 SHA256SUMS 与 version.json（避免哈希环）")

    asset_results: list[dict[str, Any]] = []
    for spec in declared_assets:
        name = spec["name"]
        path = assets_dir / name
        if not path.is_file():
            failures.append(f"缺少资产: {name}")
            continue
        if path.stat().st_size == 0:
            failures.append(f"资产为空: {name}")
            continue
        digest = sha256_file(path)
        if digest != spec.get("sha256"):
            failures.append(f"{name}: SHA256 与 version.json 不一致")
        if checksums.get(name) != digest:
            failures.append(f"{name}: SHA256 与 SHA256SUMS 不一致")
        if spec.get("size") != path.stat().st_size:
            failures.append(f"{name}: 文件大小与 version.json 不一致")
        asset_results.append(
            {
                "name": name,
                "kind": spec.get("kind"),
                "sha256": digest,
                "size": path.stat().st_size,
                "verification_level": spec.get("verification_level"),
            }
        )

    # 真实执行 sha256sum -c SHA256SUMS（不能只比较自算摘要）。
    tool = shutil.which("sha256sum")
    if tool is None:
        failures.append("环境中缺少 sha256sum，无法执行真实校验和验证")
    else:
        proc = run([tool, "-c", "SHA256SUMS"], cwd=assets_dir, check=False)
        if proc.returncode != 0:
            failures.append(f"sha256sum -c SHA256SUMS 失败: {proc.stdout.strip() or proc.stderr.strip()}")

    # Binary 平台与版本注入校验。
    platform_results: list[dict[str, Any]] = []
    for platform in payload.get("platforms") or []:
        name = platform["asset"]
        path = assets_dir / name
        if not path.is_file():
            continue
        goos, goarch = platform["goos"], platform["goarch"]
        level = platform.get("verification_level")
        fails = check_binary_header(path, goos, goarch)
        fails += check_build_info(path, expected_version, expected_commit, goos, goarch)
        runtime_ok = None
        if (goos, goarch) in RUNTIME_VERIFIED:
            runtime_fails = check_runtime_version(path, expected_version, expected_commit)
            fails += runtime_fails
            runtime_ok = not runtime_fails
        elif level != "static":
            failures.append(f"{name}: 未实机运行的平台必须标记 static，实际 {level!r}")
        failures.extend(fails)
        platform_results.append(
            {
                "asset": name,
                "goos": goos,
                "goarch": goarch,
                "verification_level": level,
                "runtime_executed": runtime_ok,
                "status": "PASS" if not fails else "FAIL",
                "failures": fails,
            }
        )

    # Source Distribution 内容校验。
    dist_report = None
    dist_name = (payload.get("distribution") or {}).get("file")
    if not dist_name:
        failures.append("version.json 缺少 distribution.file")
    else:
        dist_path = assets_dir / dist_name
        if not dist_path.is_file():
            failures.append(f"缺少 Source Distribution: {dist_name}")
        else:
            dist_report = verify_distribution(dist_path, expected_version, expected_commit)
            failures.extend(dist_report["failures"])
            if dist_report["archive_sha256"] != (payload.get("distribution") or {}).get("sha256"):
                failures.append("Source Distribution SHA256 与 version.json 不一致")
            if dist_report["manifest_sha256"] != (payload.get("distribution") or {}).get("manifest_sha256"):
                failures.append("Source Distribution MANIFEST.json 摘要与 version.json 不一致")
            if dist_report["manifest_file_count"] != (payload.get("distribution") or {}).get("manifest_file_count"):
                failures.append("Source Distribution 成员数量与 version.json 不一致")

    report = {
        "status": "PASS" if not failures else "FAIL",
        "command": "verify-assets",
        "tag": payload.get("tag"),
        "version": payload.get("version"),
        "source_commit": payload.get("source_commit"),
        "asset_count": len(asset_results),
        "assets": asset_results,
        "platforms": platform_results,
        "distribution": dist_report,
        "checksums_verified": tool is not None and not any("sha256sum -c" in f for f in failures),
        "failures": failures,
    }
    return report


def cmd_verify_assets(args: argparse.Namespace) -> int:
    assets_dir = Path(args.dir).resolve()
    if not assets_dir.is_dir():
        raise ReleaseError("DIR_MISSING", f"资产目录不存在: {assets_dir}", EXIT_PRECONDITION)
    version = args.version or read_version_file(Path(args.repo).resolve())
    commit = args.commit
    if not commit:
        version_json = read_json(assets_dir / "version.json")
        commit = version_json.get("source_commit", "")
    if not FULL_SHA.match(str(commit)):
        raise ReleaseError("COMMIT_INVALID", f"Commit 不是完整 40 位 SHA: {commit!r}", EXIT_PRECONDITION)

    report = verify_assets_dir(assets_dir, version, commit)
    if args.report:
        write_json(Path(args.report), report)
    return emit(report, args.json, EXIT_OK if report["status"] == "PASS" else EXIT_FAIL)


# ---------------------------------------------------------------------------
# verify-uploaded / promote-gate
# ---------------------------------------------------------------------------


def cmd_verify_uploaded(args: argparse.Namespace) -> int:
    assets_dir = Path(args.assets).resolve()
    download_dir = Path(args.downloads).resolve()
    if not download_dir.is_dir():
        raise ReleaseError("DIR_MISSING", f"下载目录不存在: {download_dir}", EXIT_PRECONDITION)
    payload = read_json(assets_dir / "version.json")
    version = payload.get("version")
    commit = payload.get("source_commit")

    failures: list[str] = []
    results: list[dict[str, Any]] = []
    for spec in payload.get("assets") or []:
        name = spec["name"]
        local = assets_dir / name
        remote = download_dir / name
        if not remote.is_file():
            failures.append(f"平台侧缺少附件: {name}")
            continue
        if remote.stat().st_size == 0:
            failures.append(f"平台侧附件为空: {name}")
            continue
        digest = sha256_file(remote)
        if digest != sha256_file(local):
            failures.append(f"{name}: 平台侧 SHA256 与本地构建不一致")
        if remote.stat().st_size != local.stat().st_size:
            failures.append(f"{name}: 平台侧文件大小与本地不一致")
        results.append({"name": name, "sha256": digest, "size": remote.stat().st_size, "status": "PASS"})

    expected_count = len(payload.get("assets") or [])
    if len(results) != expected_count:
        failures.append(f"附件数量不匹配: 期望 {expected_count}，实际 {len(results)}")

    report = {
        "status": "PASS" if not failures else "FAIL",
        "command": "verify-uploaded",
        "tag": payload.get("tag"),
        "version": version,
        "source_commit": commit,
        "expected_count": expected_count,
        "verified_count": len(results),
        "attachments": results,
        "failures": failures,
    }
    if args.report:
        write_json(Path(args.report), report)
    return emit(report, args.json, EXIT_OK if report["status"] == "PASS" else EXIT_FAIL)


def cmd_promote_gate(args: argparse.Namespace) -> int:
    stage = args.stage
    failures: list[str] = []
    report_payload: dict[str, Any] = {}
    if not Path(args.report).is_file():
        failures.append(f"缺少 {stage} 阶段验证报告: {args.report}")
    else:
        report_payload = read_json(Path(args.report))

    assets_payload = {}
    if args.assets:
        if not (Path(args.assets) / "version.json").is_file():
            failures.append(f"缺少 version.json: {args.assets}")
        else:
            assets_payload = read_json(Path(args.assets) / "version.json")

    if report_payload:
        if report_payload.get("status") != "PASS":
            failures.append(f"{stage} 验证报告状态不是 PASS: {report_payload.get('status')!r}")
        if assets_payload:
            if report_payload.get("source_commit") != assets_payload.get("source_commit"):
                failures.append("验证报告 Commit 与 version.json 不一致")
            if report_payload.get("version") != assets_payload.get("version"):
                failures.append("验证报告版本与 version.json 不一致")
        if report_payload.get("command") == "verify-uploaded":
            expected = len(assets_payload.get("assets") or [])
            if report_payload.get("verified_count") != expected or expected == 0:
                failures.append(
                    f"附件验证数量不足: 验证 {report_payload.get('verified_count')} / 期望 {expected}"
                )
        if report_payload.get("command") == "verify-assets":
            if report_payload.get("asset_count") != len(assets_payload.get("assets") or []):
                failures.append("本地资产验证数量与 version.json 不一致")
            if not report_payload.get("checksums_verified"):
                failures.append("本地资产验证未执行真实的 sha256sum -c")
            for platform in report_payload.get("platforms") or []:
                if platform.get("status") != "PASS":
                    failures.append(f"平台校验未通过: {platform.get('asset')}")
                if (platform.get("goos"), platform.get("goarch")) in RUNTIME_VERIFIED and platform.get("runtime_executed") is not True:
                    failures.append(f"Linux/amd64 未真实运行 version: {platform.get('asset')}")

    if args.expected_commit and assets_payload:
        if assets_payload.get("source_commit") != args.expected_commit:
            failures.append("version.json Commit 与期望 Commit 不一致")

    result = {
        "status": "PASS" if not failures else "BLOCKED",
        "command": "promote-gate",
        "stage": stage,
        "report": args.report,
        "version": assets_payload.get("version"),
        "source_commit": assets_payload.get("source_commit"),
        "failures": failures,
    }
    if args.out:
        write_json(Path(args.out), result)
    if failures:
        print("BLOCKED: promote 前置条件不满足，禁止进入 GA / latest", file=sys.stderr)
    return emit(result, args.json, EXIT_OK if not failures else EXIT_FAIL)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    """按 version.json 输出精确资产清单，供附件上传 / 下载使用（不含 SHA256SUMS / version.json）。"""
    payload = read_json(Path(args.dir).resolve() / "version.json")
    names = [spec["name"] for spec in payload.get("assets") or []]
    if not names:
        raise ReleaseError("ASSETS_EMPTY", "version.json 没有资产")
    if args.format == "upload":
        print(",".join(f"{args.dir.rstrip('/')}/{name}" for name in names))
    else:
        print(",".join(names))
    return EXIT_OK


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


REPORT_STAGES = (
    ("guard", "guard.json"),
    ("build", "build.json"),
    ("local_verification", "local-verification.json"),
    ("upload_verification", "upload-verification.json"),
    ("promote_gate_pre", "promote-gate-pre.json"),
    ("post_promote_verification", "post-promote-verification.json"),
    ("promote_gate_post", "promote-gate-post.json"),
    ("final_verification", "final-verification.json"),
    ("promote_gate_latest", "promote-gate-latest.json"),
)


def cmd_report(args: argparse.Namespace) -> int:
    reports_dir = Path(args.reports).resolve()
    assets_dir = Path(args.dir).resolve()
    version_json = read_json(assets_dir / "version.json")
    stages: list[dict[str, Any]] = []
    failures: list[str] = []
    for label, filename in REPORT_STAGES:
        path = reports_dir / filename
        if not path.is_file():
            stages.append({"stage": label, "report": filename, "status": "MISSING"})
            failures.append(f"缺少阶段报告: {filename}")
            continue
        payload = read_json(path)
        status = payload.get("status")
        stages.append({"stage": label, "report": filename, "status": status})
        if status not in ("PASS", "GUARD_OK", "BUILD_OK"):
            failures.append(f"{label} 阶段未通过: {status!r}")
    if args.require_all and not Path(args.reports, "promote-gate-latest.json").is_file():
        failures.append("未执行到 latest 阶段，不能声称 GA 完成")

    report = {
        "status": "PASS" if not failures else "BLOCKED",
        "command": "report",
        "tag": version_json.get("tag"),
        "version": version_json.get("version"),
        "source_commit": version_json.get("source_commit"),
        "ga_claimed": not failures,
        "stages": stages,
        "failures": failures,
    }
    write_json(reports_dir / "release-verification-report.json", report)
    if not args.json:
        print(f"RELEASE VERIFICATION REPORT  tag={report['tag']}  commit={report['source_commit']}")
        for stage in stages:
            print(f"  {stage['stage']:<28} {stage['status']}")
        print(f"RESULT: {report['status']}")
    return emit(report, args.json, EXIT_OK if not failures else EXIT_FAIL)


# ---------------------------------------------------------------------------
# repro
# ---------------------------------------------------------------------------


def cmd_repro(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    workdir = Path(args.workdir).resolve() if args.workdir else Path(tempfile.mkdtemp(prefix="workflow-repro-"))
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        first = workdir / "build-a"
        second = workdir / "build-b"
        for target in (first, second):
            if target.exists():
                shutil.rmtree(target)
            ns = argparse.Namespace(
                repo=str(repo), output=str(target), tag=args.tag, json=False
            )
            code = cmd_build(ns)
            if code != EXIT_OK:
                raise ReleaseError("REPRO_BUILD_FAILED", f"复现构建失败: {target}")

        def snapshot(directory: Path) -> dict[str, str]:
            return {
                p.name: sha256_file(p)
                for p in sorted(directory.iterdir())
                if p.is_file() and p.name not in ("SHA256SUMS", "version.json")
            }

        a, b = snapshot(first), snapshot(second)
        failures = []
        if sorted(a) != sorted(b):
            failures.append(f"两次构建资产集合不同: {sorted(set(a) ^ set(b))}")
        for name in sorted(set(a) & set(b)):
            if a[name] != b[name]:
                failures.append(f"{name}: 两次独立构建 SHA256 不同")
        for doc in ("SHA256SUMS", "version.json"):
            if (first / doc).read_bytes() != (second / doc).read_bytes():
                failures.append(f"{doc}: 两次独立构建内容不同")

        report = {
            "status": "PASS" if not failures else "FAIL",
            "command": "repro",
            "tag": args.tag,
            "version": read_version_file(repo),
            "artifacts": len(a),
            "failures": failures,
        }
        if args.report:
            write_json(Path(args.report), report)
        return emit(report, args.json, EXIT_OK if not failures else EXIT_FAIL)
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release.py", description="Workflow V3.1.0 release engineering tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p_guard = sub.add_parser("guard", help="Tag / Annotated / Commit / VERSION 守卫")
    p_guard.add_argument("--repo", required=True)
    p_guard.add_argument("--tag", required=True)
    p_guard.add_argument("--report")
    p_guard.add_argument("--require-head-at-tag", action="store_true", default=True)
    p_guard.add_argument("--json", action="store_true")
    p_guard.set_defaults(func=cmd_guard)

    p_build = sub.add_parser("build", help="构建全部 Release 资产")
    p_build.add_argument("--repo", required=True)
    p_build.add_argument("--output", required=True)
    p_build.add_argument("--tag", required=True)
    p_build.add_argument("--json", action="store_true")
    p_build.set_defaults(func=cmd_build)

    p_verify = sub.add_parser("verify-assets", help="校验资产集合、SHA256SUMS、version.json 与 Binary")
    p_verify.add_argument("--dir", required=True)
    p_verify.add_argument("--repo", default=".")
    p_verify.add_argument("--version")
    p_verify.add_argument("--commit")
    p_verify.add_argument("--report")
    p_verify.add_argument("--json", action="store_true")
    p_verify.set_defaults(func=cmd_verify_assets)

    p_downloaded = sub.add_parser("verify-uploaded", help="用平台侧下载副本核对附件")
    p_downloaded.add_argument("--assets", required=True)
    p_downloaded.add_argument("--downloads", required=True)
    p_downloaded.add_argument("--report")
    p_downloaded.add_argument("--json", action="store_true")
    p_downloaded.set_defaults(func=cmd_verify_uploaded)

    p_gate = sub.add_parser("promote-gate", help="promote / latest 前置门槛")
    p_gate.add_argument("--stage", required=True, choices=("pre-promote", "post-promote", "latest"))
    p_gate.add_argument("--report", required=True)
    p_gate.add_argument("--assets")
    p_gate.add_argument("--expected-commit")
    p_gate.add_argument("--out")
    p_gate.add_argument("--json", action="store_true")
    p_gate.set_defaults(func=cmd_promote_gate)

    p_list = sub.add_parser("list", help="输出精确资产清单（供附件上传 / 下载）")
    p_list.add_argument("--dir", required=True)
    p_list.add_argument("--format", choices=("upload", "download"), default="upload")
    p_list.set_defaults(func=cmd_list)

    p_report = sub.add_parser("report", help="汇总各阶段报告，输出 Release Verification Report")
    p_report.add_argument("--dir", required=True)
    p_report.add_argument("--reports", required=True)
    p_report.add_argument("--require-all", action="store_true")
    p_report.add_argument("--json", action="store_true")
    p_report.set_defaults(func=cmd_report)

    p_repro = sub.add_parser("repro", help="两次独立构建并比较可复现性")
    p_repro.add_argument("--repo", required=True)
    p_repro.add_argument("--tag", required=True)
    p_repro.add_argument("--workdir")
    p_repro.add_argument("--keep", action="store_true")
    p_repro.add_argument("--report")
    p_repro.add_argument("--json", action="store_true")
    p_repro.set_defaults(func=cmd_repro)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ReleaseError as exc:
        payload = {
            "status": "FAIL",
            "command": getattr(args, "command", "unknown"),
            "error_code": exc.code,
            "error": exc.message,
            "error_details": exc.details,
            "exit_code": exc.exit_code,
        }
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(f"ERROR[{exc.code}]: {exc.message}", file=sys.stderr)
            if exc.details:
                print(json.dumps(exc.details, indent=2, ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001
        payload = {
            "status": "FAIL",
            "command": getattr(args, "command", "unknown"),
            "error_code": "INTERNAL",
            "error": str(exc),
            "exit_code": EXIT_FAIL,
        }
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(f"ERROR[INTERNAL]: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
