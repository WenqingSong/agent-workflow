#!/usr/bin/env python3
"""Workflow V3.3.0 release engineering tool.

职责边界：

- 只从**真实 Git 事实**推导版本、Commit 与资产内容，不接受脚本里写死的 Commit；
- 只调用 P07 的真实分发包工具生成 Source Distribution，不另造分发契约；
- 构建产物确定性：无时间戳、无主机路径、无随机字段；
- 所有校验失败一律 fail closed（非零退出 + FAIL 报告），不存在“退出码为 0 就当作
  校验通过”的分支。

子命令：

    guard            Tag / Annotated / Commit / VERSION 一致性守卫（可绑定独立预期 Commit）
    repository       解析并打印本次构建的仓库来源（正式 / 独立 RC）
    build            四平台 Binary + Source Distribution + SHA256SUMS + version.json
    verify-assets    资产集合、SHA256SUMS、version.json、Binary 平台与版本注入校验
    verify-dist      Source Distribution 成员 / MANIFEST / 七角色完整性校验
    snapshot         把 9 项正式附件固定为上传前快照（独立记录每项 SHA256）
    verify-uploaded  用平台侧重新下载的副本核对 9 项附件（含校验元数据）
    promote-gate     promote 前置门槛：没有 PASS 验证报告就不得 promote / 标记 latest
    check-pipeline   校验 .cnb.yml 的上传 / 下载 / 复核阶段覆盖完整 9 项附件
    repro            两次独立构建，比较是否可复现

正式附件契约（P09 F1）：

- 业务资产 7 项：4 个 Binary、Source Distribution、其 `.sha256`、Release Notes；
- 校验元数据 2 项：`SHA256SUMS`、`version.json`；
- `SHA256SUMS` 只覆盖业务资产；`version.json` 记录业务资产摘要与 `SHA256SUMS` 自身摘要；
  两者互不覆盖，因此不存在哈希环，但都不足以自证，需要上传前快照独立记录。

退出码：0 通过；1 校验失败 / 阻塞；2 输入或前置条件错误。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
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
# 正式仓库只用于判定“正式 / 独立 RC”模式，不再作为构建时无条件写入的来源。
FORMAL_REPOSITORY = "https://cnb.cool/framework-station/agent-workflow"
RELEASE_NOTES_SOURCE = "docs/releases/v{version}.md"
VERSION_FILE = ".agent/VERSION"

# 正式附件 = 业务资产 7 项 + 校验元数据 2 项。
METADATA_ASSETS = ("SHA256SUMS", "version.json")
BUSINESS_ASSET_COUNT = 7
ATTACHMENT_COUNT = BUSINESS_ASSET_COUNT + len(METADATA_ASSETS)

# 仓库来源候选：显式参数优先，其次 CNB 环境，最后 Git remote。
REPOSITORY_URL_ENV_KEYS = (
    "CNB_REPO_URL_HTTPS",
    "CNB_REPO_URL",
    "CNB_REPO_WEB_URL",
    "CNB_GIT_URL",
    "GIT_URL",
)
REPOSITORY_SLUG_ENV = "CNB_REPO_SLUG"
REPOSITORY_HOST_ENV_KEYS = ("CNB_WEB_HOST", "CNB_REPO_HOST")
DEFAULT_REPOSITORY_HOST = "cnb.cool"
# 来源优先级：显式参数 > CNB 精确 URL > CNB 其他 URL > slug > Git remote。
REPOSITORY_SOURCE_PRIORITY = (
    "argument",
    "CNB_REPO_URL_HTTPS",
    "CNB_REPO_URL",
    "CNB_REPO_WEB_URL",
    "CNB_GIT_URL",
    "GIT_URL",
    REPOSITORY_SLUG_ENV,
    "git:remote.origin",
)

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
    ".agent/specs/WorkflowRouteSelection.md",
    ".agent/specs/WorkflowLoopCircuitBreaker.md",
    ".agent/specs/WorkflowHandoffContract.md",
    ".agent/specs/WorkflowGitLifecycle.md",
    ".agent/specs/AgentCollaborationSpecification.md",
    ".agent/specs/Five-AgentResponsibilityBoundary.md",
    ".agent/specs/WorkflowAdoption.md",
    ".agent/specs/tools/git-lifecycle-check",
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

# 七角色固定清单：数量变化必须是有意的规范变更，而不是新增文件被漏数。
REQUIRED_ROLES = (
    ".agent/roles/TaskBuilderPrompt.md",
    ".agent/roles/AnalystAgent.md",
    ".agent/roles/ArchitectAgent.md",
    ".agent/roles/CoderAgent.md",
    ".agent/roles/CleanerAgent.md",
    ".agent/roles/OwnerGateAgent.md",
    ".agent/roles/DelivererAgent.md",
)

# Specs 顶层规范完整清单（V3.3.0）。新增 / 删除规范必须同步这里与 PACKAGE_MANIFEST。
REQUIRED_SPECS = (
    ".agent/specs/WorkflowAgentDirective.md",
    ".agent/specs/WorkflowRouteSelection.md",
    ".agent/specs/WorkflowLoopCircuitBreaker.md",
    ".agent/specs/WorkflowHandoffContract.md",
    ".agent/specs/WorkflowGitLifecycle.md",
    ".agent/specs/AgentCollaborationSpecification.md",
    ".agent/specs/Five-AgentResponsibilityBoundary.md",
    ".agent/specs/WorkflowAdoption.md",
)

# specs/tools 只允许一个只读自检工具，且必须保持可执行位（100755）。
SPEC_TOOL_PATH = ".agent/specs/tools/git-lifecycle-check"

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
        for key in (
            "command",
            "repository",
            "repository_mode",
            "release_tag",
            "tag",
            "version",
            "source_commit",
            "assets",
            "failures",
        ):
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


def dist_archive_name(version: str) -> str:
    """正式附件里的 Source Distribution 文件名。

    固定为版本号形式（而不是 P07 工具内部的 `<commit12>` 形式），使 9 项附件名
    在 Tag 推送到平台之前就已确定：`.cnb.yml` 的上传与下载都可以写精确文件名，
    不必依赖 glob。Commit 绑定不依赖文件名：归档顶层目录、`MANIFEST.json`、
    `version.json` 与二进制注入值都记录完整 40 位 Commit。
    """
    return f"{FRAMEWORK}-src-{version}.tar.gz"


def p07_archive_name(commit: str) -> str:
    """P07 `workflow-adoption dist` 产出的归档文件名（保持其分发契约）。"""
    return f"{FRAMEWORK}-src-{commit[:12]}.tar.gz"


def release_attachments(version: str, commit: str) -> list[dict[str, Any]]:
    """9 项正式附件：7 项业务资产 + 2 项校验元数据。"""
    attachments = [
        {**spec, "attachment": "business"} for spec in asset_manifest(version, commit)
    ]
    attachments.append({"name": "SHA256SUMS", "kind": "checksums", "attachment": "metadata"})
    attachments.append({"name": "version.json", "kind": "version-metadata", "attachment": "metadata"})
    return attachments


def release_attachment_names(version: str, commit: str) -> list[str]:
    return [spec["name"] for spec in release_attachments(version, commit)]


def is_attachment_like(name: str) -> bool:
    """判断文件名是否属于本版本发布附件（用于识别重复 / 未预期附件）。"""
    prefixes = (
        f"{BINARY}_",
        f"{FRAMEWORK}-src-",
        f"{FRAMEWORK}-release-notes-",
        "SHA256SUMS",
        "version.json",
    )
    return name.startswith(prefixes)


# ---------------------------------------------------------------------------
# 仓库来源（F2：不得无条件写入正式仓库）
# ---------------------------------------------------------------------------


def normalize_repository(value: str) -> str:
    """把常见的 Git remote 写法归一化为 `https://host/group/repo`。"""
    text = (value or "").strip()
    if not text:
        raise ReleaseError("REPOSITORY_INVALID", "仓库来源为空", EXIT_PRECONDITION)
    text = text.rstrip("/")
    if text.endswith(".git"):
        text = text[: -len(".git")].rstrip("/")

    if text.startswith("git@"):
        host, sep, path = text[len("git@") :].partition(":")
        if not sep or not host or not path.strip("/"):
            raise ReleaseError("REPOSITORY_INVALID", f"无法解析的 Git remote: {value!r}", EXIT_PRECONDITION)
        scheme, host, path = "https", host, path
    elif "://" in text:
        scheme, _, rest = text.partition("://")
        host, _, path = rest.partition("/")
        host = host.split("@")[-1]
        if not host or not path.strip("/"):
            raise ReleaseError("REPOSITORY_INVALID", f"无法解析的仓库地址: {value!r}", EXIT_PRECONDITION)
    else:
        # 本地路径、分支名等一律不接受：不得把不可信来源写进 version.json。
        raise ReleaseError(
            "REPOSITORY_INVALID",
            f"仓库来源必须是可识别的远程地址（https / ssh / git@ / 平台 slug）: {value!r}",
            EXIT_PRECONDITION,
        )

    host = host.strip().lower()
    path = re.sub(r"/+", "/", path).strip("/")
    if "." not in host or not path or " " in host:
        raise ReleaseError("REPOSITORY_INVALID", f"仓库地址不合法: {value!r}", EXIT_PRECONDITION)
    return f"{scheme.lower()}://{host}/{path}"


def repository_candidates(repo: Path, explicit: str | None) -> dict[str, str]:
    """收集所有可用的仓库来源；本地路径型 remote 不作为来源。"""
    found: dict[str, str] = {}
    if explicit and explicit.strip():
        found["argument"] = normalize_repository(explicit)
    for key in REPOSITORY_URL_ENV_KEYS:
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        try:
            found[key] = normalize_repository(raw)
        except ReleaseError:
            raise ReleaseError(
                "REPOSITORY_INVALID",
                f"环境变量 {key} 不是可识别的仓库地址: {raw!r}",
                EXIT_PRECONDITION,
            )
    slug = os.environ.get(REPOSITORY_SLUG_ENV, "").strip()
    if slug:
        host = next(
            (os.environ[key].strip() for key in REPOSITORY_HOST_ENV_KEYS if os.environ.get(key, "").strip()),
            DEFAULT_REPOSITORY_HOST,
        )
        found[REPOSITORY_SLUG_ENV] = normalize_repository(f"https://{host}/{slug.lstrip('/')}")
    if repo.is_dir() and (repo / ".git").exists():
        proc = run(["git", "-C", str(repo), "config", "--get", "remote.origin.url"], check=False)
        remote = proc.stdout.strip()
        if remote:
            try:
                found["git:remote.origin"] = normalize_repository(remote)
            except ReleaseError:
                # 本地路径 clone 不是可对外记录的仓库来源。
                pass
    return found


def resolve_repository(repo: Path, explicit: str | None = None, required: bool = True) -> dict[str, Any]:
    """解析仓库来源，来源冲突或缺失时 fail closed，绝不伪造正式仓库。"""
    found = repository_candidates(repo, explicit)
    if not found:
        if not required:
            return {"repository": "", "mode": "unknown", "sources": {}, "reason": "NO_SOURCE"}
        raise ReleaseError(
            "REPOSITORY_UNKNOWN",
            "无法从 --repository、CNB 环境或 Git remote 确定仓库来源；拒绝伪造正式仓库",
            EXIT_PRECONDITION,
        )
    # 平台不同变量的大小写可能不同（CNB_REPO_SLUG 会小写化），按大小写归一后判冲突。
    groups: dict[str, list[str]] = {}
    for source, url in found.items():
        groups.setdefault(url.lower(), []).append(source)
    if len(groups) > 1:
        raise ReleaseError(
            "REPOSITORY_CONFLICT",
            "多个仓库来源指向不同仓库，拒绝写入不确定的 repository",
            EXIT_PRECONDITION,
            details=found,
        )
    repository = next(
        found[source] for source in REPOSITORY_SOURCE_PRIORITY if source in found
    )
    return {
        "repository": repository,
        "mode": "formal" if repository.lower() == FORMAL_REPOSITORY.lower() else "independent-rc",
        "sources": found,
        "reason": "RESOLVED",
    }



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
        "repository": "",
        "release_tag": tag,
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
    # 仓库来源只用于报告，绝不参与 Tag / Commit 判定（不允许换仓库绕过 Guard）。
    repository = resolve_repository(repo, args.repository, required=False)
    report["repository"] = repository["repository"]
    report["repository_mode"] = repository["mode"]
    report["repository_sources"] = repository["sources"]

    failures: list[str] = []
    expected = getattr(args, "expected_commit", None)
    if expected is not None:
        value = expected.strip()
        if not FULL_SHA.match(value):
            failures.append(
                f"--expected-commit 必须是完整 40 位 SHA（实际 {value!r}）；"
                "环境变量缺失或非法时不得跳过 Commit 绑定"
            )
        elif value != report["source_commit"]:
            failures.append(
                f"独立预期 Commit {value} 与 Tag {args.tag} 解析出的 Commit {report['source_commit']} 不一致"
            )
        else:
            report["expected_commit"] = value
            report["expected_commit_source"] = getattr(args, "expected_commit_source", None) or "argument"
            report["expected_commit_verified"] = True

    if args.require_head_at_tag and not report["head_at_tag"]:
        failures.append(
            f"HEAD {report['head_commit']} 与 Tag {args.tag} 指向的 Commit {report['source_commit']} 不一致；"
            "发布构建必须从被 Tag 的精确 Commit 进行"
        )

    if failures:
        report["status"] = "GUARD_FAILED"
        report["failures"] = failures
        if args.report:
            write_json(Path(args.report), report)
        return emit(report, args.json, EXIT_FAIL)
    if args.report:
        write_json(Path(args.report), report)
    return emit(report, args.json, EXIT_OK)


def cmd_repository(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    info = resolve_repository(repo, args.repository, required=not args.optional)
    report = {
        "status": "PASS" if info["repository"] else "UNRESOLVED",
        "command": "repository",
        "repository": info["repository"],
        "repository_mode": info["mode"],
        "repository_sources": info["sources"],
        "release_tag": args.tag or "",
        "source_commit": args.commit or "",
    }
    if args.report:
        write_json(Path(args.report), report)
    return emit(report, args.json, EXIT_OK if info["repository"] else EXIT_PRECONDITION)


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


def require_clean(repo: Path, output: Path, ignore: tuple[str, ...] = ()) -> None:
    try:
        rel_output = output.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        rel_output = ""
    lines = repo_status_lines(repo)
    if rel_output:
        lines = filter_own_output(lines, rel_output)
    for path in ignore:
        lines = filter_own_output(lines, path.strip("/"))
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


def build_source_distribution(repo: Path, output: Path, repository: str) -> dict[str, Any]:
    tool = repo / ".agent/adoption/workflow-adoption"
    if not tool.is_file():
        raise ReleaseError("ADOPTION_TOOL_MISSING", f"缺少 P07 分发包工具: {tool}", EXIT_PRECONDITION)
    # 必须把本次解析出的仓库来源传给 P07 工具：否则 MANIFEST.json 会写入
    # 工具内置的正式仓库，导致独立 RC 仓库的分发包携带错误来源（F2）。
    proc = run(
        [
            sys.executable,
            str(tool),
            "dist",
            "--source",
            str(repo),
            "--output",
            str(output),
            "--repository",
            repository,
            "--json",
        ],
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
            "name": dist_archive_name(version),
            "kind": "source-distribution",
            "verification_level": "content",
        }
    )
    assets.append(
        {
            "name": f"{dist_archive_name(version)}.sha256",
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


def normalize_dist_archive(dist: dict[str, Any], output: Path, version: str) -> dict[str, Any]:
    """把 P07 产出的 `<commit12>` 归档固化为版本号命名。

    P07 工具内部仍按 `<commit12>` 命名并照常写 MANIFEST / 顶层目录；本函数只改
    正式附件的文件名与配套 `.sha256`，使 9 项附件名在 Tag 之前就确定，从而让
    `.cnb.yml` 的上传与下载全部使用精确文件名。字节内容不变，摘要不变。
    """
    produced = Path(dist["archive"])
    target = output / dist_archive_name(version)
    if produced.name == target.name:
        return dist
    if not produced.is_file():
        raise ReleaseError("DIST_MISSING", f"分发包归档不存在: {produced}")
    digest = sha256_file(produced)
    if digest != dist.get("sha256"):
        raise ReleaseError("DIST_DIGEST_MISMATCH", "分发包摘要与工具报告不一致，拒绝重命名")
    target.write_bytes(produced.read_bytes())
    os.chmod(target, 0o644)
    if sha256_file(target) != digest:
        raise ReleaseError("DIST_DIGEST_MISMATCH", "固化后的分发包摘要不一致")
    companion = output / f"{target.name}.sha256"
    companion.write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    # 清掉 P07 原始命名的归档与其校验文件：附件集合必须精确为 9 项。
    if produced != target and produced.exists():
        produced.unlink()
    stale = output / f"{produced.name}.sha256"
    if stale.exists() and stale != companion:
        stale.unlink()
    return {
        **dist,
        "archive": str(target),
        "p07_archive": produced.name,
        "attachment_name_rule": "version-scoped（不使用 commit12，避免下载阶段依赖 glob）",
    }


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
    require_clean(repo, output, tuple(getattr(args, "ignore", ()) or ()))
    # F2：仓库来源必须来自显式参数 / CNB 环境 / Git remote，缺失或冲突一律 fail closed。
    repository = resolve_repository(repo, getattr(args, "repository", None), required=True)

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

    dist = build_source_distribution(repo, output, repository["repository"])
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
    manifest_repository = ((dist.get("manifest") or {}).get("source") or {}).get("repository")
    if manifest_repository != repository["repository"]:
        raise ReleaseError(
            "DIST_REPOSITORY_MISMATCH",
            f"Source Distribution MANIFEST 仓库来源 {manifest_repository!r} 与实际来源 "
            f"{repository['repository']!r} 不一致",
        )
    dist = normalize_dist_archive(dist, output, version)

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
        "release_tag": tag,
        "source_commit": commit,
        "source_tree": dist["manifest"]["source"]["tree"],
        "repository": repository["repository"],
        "source_repository": repository["repository"],
        "repository_mode": repository["mode"],
        "repository_sources": repository["sources"],
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
        "attachments": {
            "business": sorted(names),
            "business_count": len(names),
            "metadata": list(METADATA_ASSETS),
            "metadata_count": len(METADATA_ASSETS),
            "count": ATTACHMENT_COUNT,
            "rule": (
                "正式附件共 9 项：7 项业务资产由 SHA256SUMS 覆盖；"
                "SHA256SUMS 与 version.json 由 version.json.checksums 及上传前快照独立记录，"
                "两者互不覆盖，不存在哈希环。"
            ),
        },
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
            "file": Path(dist["archive"]).name,
            "p07_archive": dist.get("p07_archive", Path(dist["archive"]).name),
            "attachment_name_rule": dist.get("attachment_name_rule", "version-scoped"),
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
        "repository": repository["repository"],
        "repository_mode": repository["mode"],
        "repository_sources": repository["sources"],
        "release_tag": tag,
        "tag": tag,
        "version": version,
        "source_commit": commit,
        "output": str(output),
        "assets": [a["name"] for a in assets_payload],
        "business_asset_count": len(assets_payload),
        "metadata_assets": list(METADATA_ASSETS),
        "attachment_count": ATTACHMENT_COUNT,
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


def verify_distribution(
    archive: Path, version: str, commit: str, repository: str | None = None
) -> dict[str, Any]:
    if not archive.is_file():
        raise ReleaseError("DIST_MISSING", f"分发包不存在: {archive}")
    if archive.stat().st_size == 0:
        raise ReleaseError("DIST_EMPTY", f"分发包为空: {archive}")

    failures: list[str] = []
    top = f"{FRAMEWORK}-src-{commit[:12]}"
    members: dict[str, tuple[bytes, int]] = {}
    observed_tops: set[str] = set()

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
                observed_tops.add(parts[0] if parts else "")
                if len(parts) < 2 or parts[0] != top:
                    failures.append(f"{name}: 顶层目录不是 {top}/")
                    continue
                rel = str(PurePosixPath(*parts[1:]))
                members[rel] = (tar.extractfile(member).read(), member.mode)
    except tarfile.TarError as exc:
        raise ReleaseError("DIST_CORRUPT", f"分发包无法读取: {exc}")

    if "MANIFEST.json" not in members:
        if observed_tops and top not in observed_tops:
            raise ReleaseError(
                "DIST_COMMIT_MISMATCH",
                f"Source Distribution 顶层目录与预期 Commit 不匹配：预期 {top}/，"
                f"实际 {sorted(observed_tops)}",
            )
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
    if repository is not None and source.get("repository") != repository:
        failures.append(
            f"MANIFEST.json source.repository={source.get('repository')!r} != 实际仓库来源 {repository!r}"
        )
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
    specs = sorted(p for p in members if p.startswith(".agent/specs/") and p.endswith(".md"))
    specs_in_tools = sorted(
        p for p in members if p.startswith(".agent/specs/tools/") and not p.endswith("/")
    )
    if len(roles) != len(REQUIRED_ROLES):
        failures.append(f"七角色不完整：实际 {len(roles)} 个角色文件 {roles}")
    missing_specs = sorted(set(REQUIRED_SPECS) - set(specs))
    if missing_specs:
        failures.append(f"Specs 不完整：缺少 {missing_specs}（实际 {len(specs)} 个）")
    extra_specs = sorted(set(specs) - set(REQUIRED_SPECS))
    if extra_specs:
        failures.append(f"Specs 出现未登记规范（需更新 REQUIRED_SPECS）：{extra_specs}")
    if specs_in_tools != [SPEC_TOOL_PATH]:
        failures.append(f"Specs 工具目录必须只含 {SPEC_TOOL_PATH}，实际 {specs_in_tools}")

    # 工具必须保持可执行位：模式来自 Git index，归档与 MANIFEST 必须一致。
    tool_entry = members.get(SPEC_TOOL_PATH)
    if tool_entry is None:
        failures.append(f"分发包缺少只读自检工具: {SPEC_TOOL_PATH}")
    elif not (tool_entry[1] & 0o111):
        failures.append(f"{SPEC_TOOL_PATH} 必须可执行（mode 100755），实际 mode={oct(tool_entry[1])}")

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


def verify_assets_dir(
    assets_dir: Path,
    expected_version: str,
    expected_commit: str,
    expected_repository: str | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    version_json_path = assets_dir / "version.json"
    if not version_json_path.is_file():
        raise ReleaseError("VERSION_JSON_MISSING", f"缺少 version.json: {version_json_path}")

    payload = read_json(version_json_path)
    if payload.get("version") != expected_version:
        failures.append(f"version.json version={payload.get('version')!r} != {expected_version}")
    if payload.get("tag") != tag_for_version(expected_version):
        failures.append(f"version.json tag={payload.get('tag')!r} != {tag_for_version(expected_version)}")
    if payload.get("release_tag") != tag_for_version(expected_version):
        failures.append(
            f"version.json release_tag={payload.get('release_tag')!r} != {tag_for_version(expected_version)}"
        )
    if payload.get("source_commit") != expected_commit:
        failures.append(f"version.json source_commit={payload.get('source_commit')!r} != {expected_commit}")
    if payload.get("published_status") != "NOT_RELEASED":
        failures.append("version.json published_status 必须是 NOT_RELEASED（本任务不做正式发布）")
    if expected_repository is not None:
        if payload.get("repository") != expected_repository:
            failures.append(
                f"version.json repository={payload.get('repository')!r} != 实际仓库来源 {expected_repository!r}"
            )
        if payload.get("source_repository") != expected_repository:
            failures.append("version.json source_repository 与实际仓库来源不一致")

    declared_assets = payload.get("assets") or []
    if not declared_assets:
        raise ReleaseError("VERSION_JSON_ASSETS", "version.json 缺少 assets 列表")
    names = [a["name"] for a in declared_assets]
    if len(set(names)) != len(names):
        failures.append("version.json assets 存在重名")
    if len(names) != BUSINESS_ASSET_COUNT:
        failures.append(f"version.json 业务资产必须是 {BUSINESS_ASSET_COUNT} 项，实际 {len(names)} 项")

    expected_names = [a["name"] for a in asset_manifest(expected_version, expected_commit)]
    if sorted(names) != sorted(expected_names):
        failures.append(
            f"version.json 资产集合与构建矩阵不一致: 缺 {sorted(set(expected_names) - set(names))} 多 {sorted(set(names) - set(expected_names))}"
        )

    expected_attachments = release_attachment_names(expected_version, expected_commit)
    if len(expected_attachments) != ATTACHMENT_COUNT:
        failures.append(f"附件清单必须是 {ATTACHMENT_COUNT} 项，实际 {len(expected_attachments)} 项")

    # 资产目录顶层必须精确等于 9 项正式附件：多一个少一个都算发布缺陷。
    dir_files = sorted(p.name for p in assets_dir.iterdir() if p.is_file())
    if dir_files != sorted(expected_attachments):
        failures.append(
            f"附件目录不是精确的 {ATTACHMENT_COUNT} 项: 缺 {sorted(set(expected_attachments) - set(dir_files))} "
            f"多 {sorted(set(dir_files) - set(expected_attachments))}"
        )

    attachments_block = payload.get("attachments") or {}
    if sorted(attachments_block.get("business") or []) != sorted(names):
        failures.append("version.json attachments.business 与 assets 不一致")
    if sorted(attachments_block.get("metadata") or []) != sorted(METADATA_ASSETS):
        failures.append("version.json attachments.metadata 必须精确为 SHA256SUMS 与 version.json")
    if attachments_block.get("count") != ATTACHMENT_COUNT:
        failures.append(f"version.json attachments.count 必须是 {ATTACHMENT_COUNT}")

    checksums: dict[str, str] = {}
    if not (assets_dir / "SHA256SUMS").is_file():
        failures.append("缺少校验元数据: SHA256SUMS")
    else:
        checksums = sha256sums_entries(assets_dir)
    if checksums and sorted(checksums) != sorted(names):
        failures.append(
            f"SHA256SUMS 覆盖集合与资产不一致: 缺 {sorted(set(names) - set(checksums))} 多 {sorted(set(checksums) - set(names))}"
        )
    if "version.json" in checksums:
        failures.append("SHA256SUMS 不得覆盖 version.json（会形成无法计算的哈希环）")
    recorded = payload.get("checksums") or {}
    if (assets_dir / "SHA256SUMS").is_file() and recorded.get("sha256") != sha256_file(assets_dir / "SHA256SUMS"):
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
    checksums_executed = False
    tool = shutil.which("sha256sum")
    if tool is None:
        failures.append("环境中缺少 sha256sum，无法执行真实校验和验证")
    elif (assets_dir / "SHA256SUMS").is_file():
        proc = run([tool, "-c", "SHA256SUMS"], cwd=assets_dir, check=False)
        if proc.returncode != 0:
            failures.append(f"sha256sum -c SHA256SUMS 失败: {proc.stdout.strip() or proc.stderr.strip()}")
        else:
            checksums_executed = True

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
            dist_report = verify_distribution(
                dist_path, expected_version, expected_commit, expected_repository
            )
            failures.extend(dist_report["failures"])
            if dist_report["archive_sha256"] != (payload.get("distribution") or {}).get("sha256"):
                failures.append("Source Distribution SHA256 与 version.json 不一致")
            if dist_report["manifest_sha256"] != (payload.get("distribution") or {}).get("manifest_sha256"):
                failures.append("Source Distribution MANIFEST.json 摘要与 version.json 不一致")
            if dist_report["manifest_file_count"] != (payload.get("distribution") or {}).get("manifest_file_count"):
                failures.append("Source Distribution 成员数量与 version.json 不一致")

    metadata_attachments = []
    for name in METADATA_ASSETS:
        path = assets_dir / name
        if path.is_file():
            metadata_attachments.append(
                {"name": name, "sha256": sha256_file(path), "size": path.stat().st_size, "attachment": "metadata"}
            )

    report = {
        "status": "PASS" if not failures else "FAIL",
        "command": "verify-assets",
        "repository": payload.get("repository"),
        "repository_mode": payload.get("repository_mode"),
        "release_tag": payload.get("release_tag"),
        "tag": payload.get("tag"),
        "version": payload.get("version"),
        "source_commit": payload.get("source_commit"),
        "expected_commit": expected_commit,
        "asset_count": len(asset_results),
        "business_asset_count": len(asset_results),
        "metadata_asset_count": len(metadata_attachments),
        "attachment_count": len(dir_files),
        "assets": asset_results,
        "metadata_assets": metadata_attachments,
        "platforms": platform_results,
        "distribution": dist_report,
        "checksums_verified": checksums_executed,
        "failures": failures,
    }
    return report


def cmd_verify_assets(args: argparse.Namespace) -> int:
    assets_dir = Path(args.dir).resolve()
    if not assets_dir.is_dir():
        raise ReleaseError("DIR_MISSING", f"资产目录不存在: {assets_dir}", EXIT_PRECONDITION)
    repo = Path(args.repo).resolve()
    version = args.version or read_version_file(repo)
    # F3：预期 Commit 必须由调用方独立提供；不得回退到 version.json 自报的 Commit。
    commit = (getattr(args, "commit", None) or "").strip()
    if not commit:
        raise ReleaseError(
            "COMMIT_REQUIRED",
            "verify-assets 必须显式提供 --commit（独立预期来源）；不得回退到 version.json 自报的 Commit",
            EXIT_PRECONDITION,
        )
    if not FULL_SHA.match(commit):
        raise ReleaseError("COMMIT_INVALID", f"Commit 不是完整 40 位 SHA: {commit!r}", EXIT_PRECONDITION)

    binding: list[str] = []
    if (repo / ".git").exists():
        exists = run(["git", "-C", str(repo), "cat-file", "-e", f"{commit}^{{commit}}"], check=False)
        if exists.returncode != 0:
            binding.append(f"预期 Commit {commit} 在当前仓库中不存在")
    if getattr(args, "tag", None):
        try:
            guard = check_tag(repo, args.tag)
        except ReleaseError as exc:
            binding.append(f"无法独立解析 Tag {args.tag}: {exc.message}")
        else:
            if guard["source_commit"] != commit:
                binding.append(
                    f"Tag {args.tag} 解析出的 Commit {guard['source_commit']} 与预期 Commit {commit} 不一致"
                )
            if guard["version"] != version:
                binding.append(f"Tag {args.tag} 对应版本 {guard['version']} 与预期版本 {version} 不一致")

    repository = resolve_repository(
        repo, getattr(args, "repository", None), required=bool(getattr(args, "repository_required", False))
    )
    if getattr(args, "repository_required", False) and not repository["repository"]:
        binding.append("无法确定实际仓库来源（要求 --repository 或可用的 CNB / Git 来源）")

    try:
        report = verify_assets_dir(assets_dir, version, commit, repository["repository"] or None)
    except ReleaseError as exc:
        report = {
            "status": "FAIL",
            "command": "verify-assets",
            "repository": repository["repository"],
            "repository_mode": repository["mode"],
            "release_tag": tag_for_version(version),
            "version": version,
            "source_commit": commit,
            "expected_commit": commit,
            "asset_count": 0,
            "business_asset_count": 0,
            "metadata_asset_count": 0,
            "attachment_count": 0,
            "assets": [],
            "metadata_assets": [],
            "platforms": [],
            "distribution": None,
            "checksums_verified": False,
            "failures": [f"{exc.code}: {exc.message}"],
        }
    report["repository"] = report.get("repository") or repository["repository"]
    report["repository_mode"] = report.get("repository_mode") or repository["mode"]
    report["failures"].extend(binding)
    if binding or report["failures"]:
        report["status"] = "FAIL"
    if args.report:
        write_json(Path(args.report), report)
    return emit(report, args.json, EXIT_OK if report["status"] == "PASS" else EXIT_FAIL)


# ---------------------------------------------------------------------------
# verify-uploaded / promote-gate
# ---------------------------------------------------------------------------


def cmd_snapshot(args: argparse.Namespace) -> int:
    """把 9 项正式附件固定为上传前快照，并独立记录每项 SHA256。

    快照是平台侧复核的唯一预期来源：既不依赖平台返回，也不依赖 `version.json` 自证。
    """
    assets_dir = Path(args.assets).resolve()
    out_dir = Path(args.out).resolve()
    manifest_path = Path(args.manifest).resolve()
    if not assets_dir.is_dir():
        raise ReleaseError("DIR_MISSING", f"资产目录不存在: {assets_dir}", EXIT_PRECONDITION)

    version_json_path = assets_dir / "version.json"
    if not version_json_path.is_file():
        # 缺少 version.json 属于“正式附件不完整”，不是可忽略的输入缺失。
        raise ReleaseError(
            "ATTACHMENT_MISSING",
            "资产目录缺少校验元数据: version.json（正式附件必须 9 项）",
            EXIT_FAIL,
        )
    payload = read_json(version_json_path)
    version = payload.get("version")
    commit = payload.get("source_commit")
    if not isinstance(version, str) or not version:
        raise ReleaseError("VERSION_JSON_INVALID", "version.json 缺少 version", EXIT_FAIL)
    if not FULL_SHA.match(str(commit)):
        raise ReleaseError("VERSION_JSON_INVALID", f"version.json source_commit 非法: {commit!r}", EXIT_FAIL)

    expected = release_attachments(version, commit)
    expected_names = [spec["name"] for spec in expected]
    if len(expected_names) != ATTACHMENT_COUNT or len(set(expected_names)) != ATTACHMENT_COUNT:
        raise ReleaseError(
            "ATTACHMENT_SET_INVALID",
            f"附件清单必须是 {ATTACHMENT_COUNT} 项且不重名，实际 {len(expected_names)} 项",
            EXIT_FAIL,
        )

    present = sorted(p.name for p in assets_dir.iterdir() if p.is_file())
    missing = [name for name in expected_names if name not in present]
    unexpected = [name for name in present if name not in expected_names]
    if missing:
        raise ReleaseError("ATTACHMENT_MISSING", f"资产目录缺少正式附件: {missing}", EXIT_FAIL)
    if unexpected:
        raise ReleaseError(
            "ATTACHMENT_UNEXPECTED",
            f"资产目录存在不属于 9 项正式附件的文件: {unexpected}",
            EXIT_FAIL,
        )

    # 元数据绑定：SHA256SUMS 摘要必须与 version.json 记录一致，且不覆盖 version.json。
    sums_path = assets_dir / "SHA256SUMS"
    sums_digest = sha256_file(sums_path)
    recorded = (payload.get("checksums") or {}).get("sha256")
    if sums_digest != recorded:
        raise ReleaseError(
            "CHECKSUMS_BINDING_MISMATCH",
            "SHA256SUMS 实际摘要与 version.json 记录的摘要不一致",
            EXIT_FAIL,
        )
    if "version.json" in sha256sums_entries(assets_dir):
        raise ReleaseError("CHECKSUMS_LOOP", "SHA256SUMS 不得覆盖 version.json（哈希环）", EXIT_FAIL)

    out_dir.mkdir(parents=True, exist_ok=True)
    # 清空上一轮快照，避免残留文件让“附件缺失”被误判为通过。
    for stale in out_dir.iterdir():
        if stale.is_file():
            stale.unlink()

    entries: list[dict[str, Any]] = []
    for spec in expected:
        name = spec["name"]
        source = assets_dir / name
        target = out_dir / name
        shutil.copyfile(source, target)
        digest = sha256_file(target)
        if digest != sha256_file(source):
            raise ReleaseError("SNAPSHOT_COPY_FAILED", f"快照复制后摘要不一致: {name}", EXIT_FAIL)
        entries.append(
            {
                "name": name,
                "kind": spec["kind"],
                "attachment": spec["attachment"],
                "sha256": digest,
                "size": target.stat().st_size,
            }
        )

    tool = shutil.which("sha256sum")
    if tool is None:
        raise ReleaseError("TOOL_MISSING", "环境中缺少 sha256sum，无法校验快照", EXIT_FAIL)
    proc = run([tool, "-c", "SHA256SUMS"], cwd=out_dir, check=False)
    if proc.returncode != 0:
        raise ReleaseError(
            "SNAPSHOT_CHECKSUMS_FAILED",
            f"快照 sha256sum -c 失败: {proc.stdout.strip() or proc.stderr.strip()}",
            EXIT_FAIL,
        )

    manifest = {
        "format": 1,
        "command": "snapshot",
        "repository": payload.get("repository"),
        "release_tag": payload.get("release_tag") or payload.get("tag"),
        "version": version,
        "source_commit": commit,
        "attachment_count": ATTACHMENT_COUNT,
        "business_count": BUSINESS_ASSET_COUNT,
        "metadata_count": len(METADATA_ASSETS),
        "metadata_assets": list(METADATA_ASSETS),
        "checksums": {
            "file": "SHA256SUMS",
            "sha256": sums_digest,
            "binding": recorded,
            "covers": sorted(spec["name"] for spec in expected if spec["attachment"] == "business"),
            "excludes": ["SHA256SUMS", "version.json"],
        },
        "attachments": entries,
    }
    write_json(manifest_path, manifest)

    report = {
        "status": "PASS",
        "command": "snapshot",
        "repository": manifest["repository"],
        "release_tag": manifest["release_tag"],
        "version": version,
        "source_commit": commit,
        "attachment_count": ATTACHMENT_COUNT,
        "business_count": BUSINESS_ASSET_COUNT,
        "metadata_count": len(METADATA_ASSETS),
        "snapshot": str(out_dir),
        "manifest": str(manifest_path),
        "attachments": [f"{entry['name']}  {entry['sha256']}" for entry in entries],
        "failures": [],
    }
    if args.report:
        write_json(Path(args.report), report)
    return emit(report, args.json, EXIT_OK)


def verify_remote_version_json(
    path: Path,
    version: str,
    commit: str,
    business_entries: dict[str, dict[str, Any]],
) -> list[str]:
    """对平台侧重新下载的 version.json 做 Schema / Commit / 资产列表 / 哈希核验。"""
    failures: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"平台侧 version.json 不是合法 JSON: {exc}"]

    expected_tag = tag_for_version(version)
    if payload.get("format") != 1:
        failures.append(f"平台侧 version.json format 非预期: {payload.get('format')!r}")
    if payload.get("framework") != FRAMEWORK:
        failures.append(f"平台侧 version.json framework 非预期: {payload.get('framework')!r}")
    if payload.get("version") != version:
        failures.append(f"平台侧 version.json version={payload.get('version')!r} != {version}")
    if payload.get("tag") != expected_tag or payload.get("release_tag") != expected_tag:
        failures.append(f"平台侧 version.json tag / release_tag 必须都是 {expected_tag}")
    if payload.get("source_commit") != commit:
        failures.append(
            f"平台侧 version.json source_commit={payload.get('source_commit')!r} != 预期 Commit {commit}"
        )
    if payload.get("published_status") != "NOT_RELEASED":
        failures.append("平台侧 version.json published_status 必须是 NOT_RELEASED")

    declared = payload.get("assets") or []
    names = [spec.get("name") for spec in declared if isinstance(spec, dict)]
    if len(names) != BUSINESS_ASSET_COUNT or len(set(names)) != len(names):
        failures.append(
            f"平台侧 version.json 业务资产必须是 {BUSINESS_ASSET_COUNT} 项且不重名，实际 {len(names)} 项"
        )
    if sorted(names) != sorted(business_entries):
        failures.append(
            f"平台侧 version.json 资产列表与上传快照不一致: 缺 {sorted(set(business_entries) - set(names))} "
            f"多 {sorted(set(names) - set(business_entries))}"
        )
    for spec in declared:
        name = spec.get("name")
        entry = business_entries.get(name or "")
        if entry is None:
            continue
        if spec.get("sha256") != entry["sha256"]:
            failures.append(f"平台侧 version.json 中 {name} 的 SHA256 与上传快照不一致")
        if spec.get("size") != entry["size"]:
            failures.append(f"平台侧 version.json 中 {name} 的大小与上传快照不一致")

    checksums = payload.get("checksums") or {}
    if checksums.get("file") != "SHA256SUMS":
        failures.append("平台侧 version.json checksums.file 必须是 SHA256SUMS")
    if sorted(checksums.get("covers") or []) != sorted(names):
        failures.append("平台侧 version.json checksums.covers 与业务资产列表不一致")
    if sorted(checksums.get("excludes") or []) != ["SHA256SUMS", "version.json"]:
        failures.append("平台侧 version.json checksums.excludes 必须精确为 SHA256SUMS 与 version.json")

    attachments = payload.get("attachments") or {}
    if attachments.get("count") != ATTACHMENT_COUNT:
        failures.append(f"平台侧 version.json attachments.count 必须是 {ATTACHMENT_COUNT}")
    if sorted(attachments.get("metadata") or []) != sorted(METADATA_ASSETS):
        failures.append("平台侧 version.json attachments.metadata 必须精确为 SHA256SUMS 与 version.json")
    return failures


def cmd_verify_uploaded(args: argparse.Namespace) -> int:
    assets_dir = Path(args.assets).resolve()
    download_dir = Path(args.downloads).resolve()
    if not download_dir.is_dir():
        raise ReleaseError("DIR_MISSING", f"下载目录不存在: {download_dir}", EXIT_PRECONDITION)
    manifest_path = Path(args.manifest).resolve()
    manifest = read_json(manifest_path)
    snapshot_entries = manifest.get("attachments") or []
    if not isinstance(snapshot_entries, list):
        raise ReleaseError("SNAPSHOT_INVALID", "上传快照 manifest 缺少 attachments 列表", EXIT_PRECONDITION)

    payload = read_json(assets_dir / "version.json")
    version = payload.get("version")
    commit = payload.get("source_commit")
    # F3：平台侧复核同样绑定独立预期 Commit，不采信 version.json 自报值。
    expected_commit = (getattr(args, "commit", None) or "").strip()
    if not expected_commit:
        raise ReleaseError(
            "COMMIT_REQUIRED",
            "verify-uploaded 必须显式提供 --commit（独立预期来源），不得采信 version.json 自报的 Commit",
            EXIT_PRECONDITION,
        )
    if not FULL_SHA.match(expected_commit):
        raise ReleaseError("COMMIT_INVALID", f"Commit 不是完整 40 位 SHA: {expected_commit!r}", EXIT_PRECONDITION)

    failures: list[str] = []
    if manifest.get("source_commit") != expected_commit:
        failures.append("上传快照 manifest 的 Commit 与预期 Commit 不一致")
    if commit != expected_commit:
        failures.append(f"快照 version.json 的 Commit {commit!r} 与预期 Commit {expected_commit} 不一致")
    if manifest.get("version") != version:
        failures.append("上传快照 manifest 的版本与 version.json 不一致")

    expected_names = release_attachment_names(version, expected_commit)
    entries = {entry.get("name"): entry for entry in snapshot_entries if isinstance(entry, dict)}
    if len(entries) != len(snapshot_entries):
        failures.append("上传快照 manifest 存在重复或非法条目")
    if len(entries) != ATTACHMENT_COUNT:
        failures.append(f"上传快照必须是 {ATTACHMENT_COUNT} 项，实际 {len(entries)} 项")
    if sorted(entries) != sorted(expected_names):
        failures.append(
            f"上传快照资产集合不是完整的 {ATTACHMENT_COUNT} 项附件: "
            f"缺 {sorted(set(expected_names) - set(entries))} 多 {sorted(set(entries) - set(expected_names))}"
        )

    # 元数据绑定：SHA256SUMS 用固定快照 + version.json.checksums.sha256 双重绑定，避免自证。
    sums_entry = entries.get("SHA256SUMS") or {}
    version_entry = entries.get("version.json") or {}
    recorded = (payload.get("checksums") or {}).get("sha256")
    if not sums_entry.get("sha256"):
        failures.append("上传快照缺少 SHA256SUMS 的独立摘要")
    if not recorded:
        failures.append("version.json 缺少 checksums.sha256")
    if sums_entry.get("sha256") and recorded and sums_entry["sha256"] != recorded:
        failures.append("SHA256SUMS 快照摘要与 version.json.checksums.sha256 不一致")
    if version_entry.get("sha256") != sha256_file(assets_dir / "version.json"):
        failures.append("上传快照中 version.json 的摘要与快照文件不一致")

    results: list[dict[str, Any]] = []
    metadata_ok = True
    for name in expected_names:
        entry = entries.get(name) or {}
        remote = download_dir / name
        if not remote.is_file():
            failures.append(f"平台侧缺少附件: {name}")
            if entry.get("attachment") == "metadata":
                metadata_ok = False
            continue
        if remote.stat().st_size == 0:
            failures.append(f"平台侧附件为空: {name}")
            if entry.get("attachment") == "metadata":
                metadata_ok = False
            continue
        digest = sha256_file(remote)
        if not entry:
            failures.append(f"平台侧附件不在上传快照中: {name}")
        else:
            if digest != entry.get("sha256"):
                failures.append(f"{name}: 平台侧 SHA256 与上传前快照不一致")
            if remote.stat().st_size != entry.get("size"):
                failures.append(f"{name}: 平台侧文件大小与上传前快照不一致")
        results.append(
            {
                "name": name,
                "attachment": entry.get("attachment", "unknown"),
                "kind": entry.get("kind"),
                "sha256": digest,
                "size": remote.stat().st_size,
                "status": "PASS",
            }
        )

    verified_names = {item["name"] for item in results}
    if len(results) != ATTACHMENT_COUNT:
        failures.append(f"附件数量不匹配: 期望 {ATTACHMENT_COUNT}，实际 {len(results)}")
    if any(name not in verified_names for name in METADATA_ASSETS):
        failures.append("缺少校验元数据附件：SHA256SUMS 与 version.json 必须同时存在")
        metadata_ok = False

    # 下载目录不得出现重复或不匹配的附件（例如被平台改名后的副本）。
    extras = sorted(
        name
        for name in (p.name for p in download_dir.iterdir() if p.is_file())
        if is_attachment_like(name) and name not in expected_names
    )
    if extras:
        failures.append(f"下载目录存在重复或未预期的附件: {extras}")

    # 对平台侧 SHA256SUMS 执行真实 sha256sum -c：校验的是平台下载回来的业务资产字节。
    checksums_verified = False
    tool = shutil.which("sha256sum")
    if "SHA256SUMS" in verified_names:
        if tool is None:
            failures.append("环境中缺少 sha256sum，无法对平台侧 SHA256SUMS 执行真实校验")
        else:
            proc = run([tool, "-c", "SHA256SUMS"], cwd=download_dir, check=False)
            if proc.returncode != 0:
                failures.append(
                    f"平台侧 sha256sum -c SHA256SUMS 失败: {proc.stdout.strip() or proc.stderr.strip()}"
                )
            else:
                checksums_verified = True

    # 平台侧 version.json 的 Schema / Commit / 资产列表 / 哈希核验。
    remote_version_json = download_dir / "version.json"
    business_entries = {
        name: entry
        for name, entry in entries.items()
        if (entry or {}).get("attachment") == "business"
    }
    if remote_version_json.is_file():
        failures.extend(
            verify_remote_version_json(remote_version_json, version, expected_commit, business_entries)
        )
        try:
            remote_payload = json.loads(remote_version_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            remote_payload = {}
        if (remote_payload.get("checksums") or {}).get("sha256") != sums_entry.get("sha256"):
            failures.append("平台侧 version.json 记录的 SHA256SUMS 摘要与上传快照不一致")
        if remote_payload.get("attachments", {}).get("count") != ATTACHMENT_COUNT:
            metadata_ok = False

    report = {
        "status": "PASS" if not failures else "FAIL",
        "command": "verify-uploaded",
        "repository": payload.get("repository") or manifest.get("repository"),
        "release_tag": payload.get("release_tag") or manifest.get("release_tag"),
        "tag": payload.get("tag"),
        "version": version,
        "source_commit": expected_commit,
        "expected_count": ATTACHMENT_COUNT,
        "verified_count": len(results),
        "business_count": len(business_entries),
        "metadata_count": len(METADATA_ASSETS),
        "metadata_attachments_verified": metadata_ok,
        "checksums_verified": checksums_verified,
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
            # 平台侧附件数必须等于 9：7 项业务资产 + 2 项校验元数据。
            expected = len(assets_payload.get("assets") or []) + len(METADATA_ASSETS)
            if expected != ATTACHMENT_COUNT:
                failures.append(f"version.json 声明的正式附件总数不是 {ATTACHMENT_COUNT}: {expected}")
            if report_payload.get("verified_count") != expected or expected == 0:
                failures.append(
                    f"附件验证数量不足: 验证 {report_payload.get('verified_count')} / 期望 {expected}"
                )
            if report_payload.get("expected_count") != ATTACHMENT_COUNT:
                failures.append("附件验证报告未按 9 项正式附件核对")
            if report_payload.get("metadata_attachments_verified") is not True:
                failures.append("校验元数据附件（SHA256SUMS / version.json）未通过平台侧核验")
            if not report_payload.get("checksums_verified"):
                failures.append("未在平台侧下载目录执行真实的 sha256sum -c")
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

    expected_commit = (getattr(args, "expected_commit", None) or "").strip()
    if expected_commit and assets_payload:
        if not FULL_SHA.match(expected_commit):
            failures.append(f"期望 Commit 不是完整 40 位 SHA: {expected_commit!r}")
        elif assets_payload.get("source_commit") != expected_commit:
            failures.append("version.json Commit 与期望 Commit 不一致")
        if report_payload and report_payload.get("source_commit") != expected_commit:
            failures.append("验证报告 Commit 与期望 Commit 不一致")

    expected_tag = getattr(args, "tag", None)
    if expected_tag and assets_payload:
        if assets_payload.get("release_tag") != expected_tag:
            failures.append(f"version.json release_tag 与期望 Tag {expected_tag} 不一致")

    result = {
        "status": "PASS" if not failures else "BLOCKED",
        "command": "promote-gate",
        "stage": stage,
        "report": args.report,
        "repository": assets_payload.get("repository"),
        "release_tag": assets_payload.get("release_tag"),
        "version": assets_payload.get("version"),
        "source_commit": assets_payload.get("source_commit"),
        "expected_commit": expected_commit,
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
    """按 version.json 输出精确清单（attachments 默认 9 项，assets 为 7 项业务资产）。"""
    payload = read_json(Path(args.dir).resolve() / "version.json")
    version = payload.get("version")
    commit = payload.get("source_commit")
    if args.scope == "assets":
        names = [spec["name"] for spec in payload.get("assets") or []]
    else:
        if not isinstance(version, str) or not FULL_SHA.match(str(commit)):
            raise ReleaseError("VERSION_JSON_INVALID", "version.json 缺少合法 version / source_commit")
        names = release_attachment_names(version, commit)
    if not names:
        raise ReleaseError("ASSETS_EMPTY", "version.json 没有资产")
    if args.format == "upload":
        print(",".join(f"{args.dir.rstrip('/')}/{name}" for name in names))
    else:
        print(",".join(names))
    return EXIT_OK


# ---------------------------------------------------------------------------
# check-pipeline（F1：CI 必须覆盖完整 9 项附件与完整发布周期）
# ---------------------------------------------------------------------------


PIPELINE_REQUIRED_STAGES = (
    "create-pre-release",
    "upload-assets",
    "download-uploaded",
    "verify-uploaded",
    "promote-gate-pre",
    "promote-ga",
    "reupload-after-promote",
    "redownload-after-promote",
    "verify-after-promote",
    "promote-gate-post",
    "mark-latest",
    "redownload-after-latest",
    "verify-after-latest",
    "promote-gate-latest",
    "release-verification-report",
)

UPLOAD_STAGES = ("upload-assets", "reupload-after-promote")
DOWNLOAD_STAGES = ("download-uploaded", "redownload-after-promote", "redownload-after-latest")
VERIFY_UPLOADED_STAGES = ("verify-uploaded", "verify-after-promote", "verify-after-latest")
PROMOTE_GATE_STAGES = ("promote-gate-pre", "promote-gate-post", "promote-gate-latest")


def pipeline_stage_blocks(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    starts = [(index, match) for index, line in enumerate(lines) if (match := re.match(r"^\s*- name:\s*(\S+)", line))]
    blocks: list[dict[str, Any]] = []
    for position, (index, match) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        blocks.append({"name": match.group(1).strip("\"'"), "index": index, "lines": lines[index + 1 : end]})
    return blocks


def block_attachment_names(block: dict[str, Any]) -> tuple[list[str], bool]:
    """返回 (附件名列表, 是否 DOWNLOAD 阶段)。"""
    names: list[str] = []
    download = False
    lines = block["lines"]
    for offset, line in enumerate(lines):
        if re.match(r"^\s+type:\s*DOWNLOAD\s*$", line):
            download = True
        match = re.match(r"^\s+attachments:\s*(.*)$", line)
        if not match:
            continue
        inline = match.group(1).strip()
        if inline:
            names.extend(token.strip().strip("\"'") for token in inline.split(",") if token.strip())
            continue
        cursor = offset + 1
        while cursor < len(lines):
            item = re.match(r"^\s+-\s+(.+)$", lines[cursor])
            if not item:
                break
            names.append(item.group(1).strip().strip("\"'"))
            cursor += 1
    normalized = [name[len("./dist/") :] if name.startswith("./dist/") else name for name in names]
    return normalized, download


def cmd_check_pipeline(args: argparse.Namespace) -> int:
    config = Path(args.config)
    if not config.is_file():
        raise ReleaseError("PIPELINE_MISSING", f"找不到流水线配置: {config}", EXIT_PRECONDITION)
    text = config.read_text(encoding="utf-8")
    blocks = pipeline_stage_blocks(text)
    by_name = {}
    order: list[str] = []
    for block in blocks:
        by_name.setdefault(block["name"], block)
        order.append(block["name"])

    payload = read_json(Path(args.dir).resolve() / "version.json")
    version = payload.get("version")
    commit = payload.get("source_commit")
    if not isinstance(version, str) or not FULL_SHA.match(str(commit)):
        raise ReleaseError("VERSION_JSON_INVALID", "version.json 缺少合法 version / source_commit")
    expected = release_attachment_names(version, commit)

    failures: list[str] = []
    for stage in PIPELINE_REQUIRED_STAGES:
        if stage not in by_name:
            failures.append(f"流水线缺少必需阶段: {stage}")
    positions = [order.index(stage) for stage in PIPELINE_REQUIRED_STAGES if stage in order]
    if positions != sorted(positions):
        failures.append("流水线阶段顺序不符合发布周期（pre-release → 上传 → 复核 → promote → 重传 → 复核 → latest）")

    stage_summary: list[dict[str, Any]] = []
    for stage in UPLOAD_STAGES + DOWNLOAD_STAGES:
        block = by_name.get(stage)
        if block is None:
            continue
        names, download = block_attachment_names(block)
        if "*" in "".join(names):
            failures.append(f"{stage}: 附件名不得使用 glob（必须精确文件名）")
        if len(names) != ATTACHMENT_COUNT:
            failures.append(f"{stage}: 附件数量 {len(names)} != {ATTACHMENT_COUNT}")
        if sorted(names) != sorted(expected):
            failures.append(
                f"{stage}: 附件集合与 9 项正式附件不一致: 缺 {sorted(set(expected) - set(names))} "
                f"多 {sorted(set(names) - set(expected))}"
            )
        if stage in DOWNLOAD_STAGES and not download:
            failures.append(f"{stage}: 必须声明 type: DOWNLOAD")
        if stage in UPLOAD_STAGES and download:
            failures.append(f"{stage}: 上传阶段不得声明 type: DOWNLOAD")
        stage_summary.append(
            {
                "stage": stage,
                "type": "download" if download else "upload",
                "count": len(names),
                "attachments": names,
            }
        )

    for stage in VERIFY_UPLOADED_STAGES:
        block = by_name.get(stage)
        if block is None:
            continue
        body = "\n".join(block["lines"])
        if "--manifest" not in body:
            failures.append(f"{stage}: 必须使用上传前快照 manifest 复核 9 项附件")
        if "--commit" not in body:
            failures.append(f"{stage}: 必须传入独立 --commit（F3），不得只依赖 version.json")

    for stage in PROMOTE_GATE_STAGES:
        block = by_name.get(stage)
        if block is None:
            continue
        body = "\n".join(block["lines"])
        if "--expected-commit" not in body:
            failures.append(f"{stage}: 必须绑定独立 --expected-commit")

    pre = by_name.get("create-pre-release")
    if pre is not None:
        body = "\n".join(pre["lines"])
        if "preRelease: true" not in body or "latest: false" not in body:
            failures.append("create-pre-release 必须是 preRelease=true / latest=false")
    latest = by_name.get("mark-latest")
    if latest is not None:
        body = "\n".join(latest["lines"])
        if "latest: true" not in body or "preRelease: false" not in body:
            failures.append("mark-latest 必须是 preRelease=false / latest=true")

    report = {
        "status": "PASS" if not failures else "FAIL",
        "command": "check-pipeline",
        "config": str(config),
        "repository": payload.get("repository"),
        "release_tag": payload.get("release_tag") or payload.get("tag"),
        "version": version,
        "source_commit": commit,
        "expected_attachments": expected,
        "attachment_count": ATTACHMENT_COUNT,
        "stages": stage_summary,
        "failures": failures,
    }
    if args.report:
        write_json(Path(args.report), report)
    return emit(report, args.json, EXIT_OK if not failures else EXIT_FAIL)


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
        "repository": version_json.get("repository"),
        "repository_mode": version_json.get("repository_mode"),
        "release_tag": version_json.get("release_tag"),
        "tag": version_json.get("tag"),
        "version": version_json.get("version"),
        "source_commit": version_json.get("source_commit"),
        "attachment_count": ATTACHMENT_COUNT,
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
    """在两个独立克隆中构建同一 Tag，比较资产是否逐字节一致。

    P07 的 dist 要求工作区完全干净，因此复现构建不能复用带 dist/ 的工作区；
    这里为每次构建创建独立克隆，保证两次构建互不影响。
    """
    repo = Path(args.repo).resolve()
    guard = check_tag(repo, args.tag)
    commit = guard["source_commit"]
    version = guard["version"]
    # 复现构建沿用同一仓库来源，避免 clone 的本地路径 remote 被误当成发布仓库。
    repository = resolve_repository(repo, getattr(args, "repository", None), required=True)
    workdir = Path(args.workdir).resolve() if args.workdir else Path(tempfile.mkdtemp(prefix="workflow-repro-"))
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        snapshots: list[dict[str, str]] = []
        for label in ("a", "b"):
            clone = workdir / f"repro-{label}"
            if clone.exists():
                shutil.rmtree(clone)
            run(["git", "clone", "--quiet", "--no-hardlinks", str(repo), str(clone)])
            git(clone, "checkout", "--quiet", "--detach", commit)
            ns = argparse.Namespace(
                repo=str(clone),
                output=str(clone / "dist"),
                tag=args.tag,
                json=False,
                ignore=[],
                repository=repository["repository"],
            )
            # 子构建的人类可读输出不得混入 repro 的 JSON 结果。
            sink = io.StringIO()
            with contextlib.redirect_stdout(sink):
                code = cmd_build(ns)
            if code != EXIT_OK:
                raise ReleaseError(
                    "REPRO_BUILD_FAILED",
                    f"复现构建失败: {clone}",
                    details={"stdout": sink.getvalue()},
                )
            snapshots.append(
                {p.name: sha256_file(p) for p in sorted((clone / "dist").iterdir()) if p.is_file()}
            )

        a, b = snapshots
        failures: list[str] = []
        if sorted(a) != sorted(b):
            failures.append(f"两次构建资产集合不同: {sorted(set(a) ^ set(b))}")
        for name in sorted(set(a) & set(b)):
            if a[name] != b[name]:
                failures.append(f"{name}: 两次独立构建 SHA256 不同")
        business = [name for name in a if name not in ("SHA256SUMS", "version.json")]

        report = {
            "status": "PASS" if not failures else "FAIL",
            "command": "repro",
            "repository": repository["repository"],
            "repository_mode": repository["mode"],
            "release_tag": args.tag,
            "tag": args.tag,
            "version": version,
            "source_commit": commit,
            "artifacts": len(business),
            "attachments": len(a),
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
    parser = argparse.ArgumentParser(prog="release.py", description="Workflow V3.3.0 release engineering tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p_guard = sub.add_parser("guard", help="Tag / Annotated / Commit / VERSION 守卫")
    p_guard.add_argument("--repo", required=True)
    p_guard.add_argument("--tag", required=True)
    p_guard.add_argument("--repository", help="仓库来源（仅用于报告，不参与 Commit 判定）")
    p_guard.add_argument("--expected-commit", help="独立预期 Commit（CI 传入 CNB_COMMIT 或 Git Tag 解析值）")
    p_guard.add_argument("--expected-commit-source", help="预期 Commit 的来源标记（如 CNB_COMMIT）")
    p_guard.add_argument("--report")
    p_guard.add_argument("--require-head-at-tag", action="store_true", default=True)
    p_guard.add_argument("--json", action="store_true")
    p_guard.set_defaults(func=cmd_guard)

    p_repository = sub.add_parser("repository", help="解析并输出仓库来源（正式 / 独立 RC）")
    p_repository.add_argument("--repo", required=True)
    p_repository.add_argument("--repository")
    p_repository.add_argument("--tag")
    p_repository.add_argument("--commit")
    p_repository.add_argument("--optional", action="store_true", help="来源无法解析时不作为错误")
    p_repository.add_argument("--report")
    p_repository.add_argument("--json", action="store_true")
    p_repository.set_defaults(func=cmd_repository)

    p_build = sub.add_parser("build", help="构建全部 Release 资产")
    p_build.add_argument("--repo", required=True)
    p_build.add_argument("--output", required=True)
    p_build.add_argument("--tag", required=True)
    p_build.add_argument("--repository", help="显式仓库来源；缺省时从 CNB 环境 / Git remote 解析")
    p_build.add_argument(
        "--ignore",
        action="append",
        default=["dist"],
        help="构建前允许存在的未跟踪输出目录（默认 dist）",
    )
    p_build.add_argument("--json", action="store_true")
    p_build.set_defaults(func=cmd_build)

    p_verify = sub.add_parser("verify-assets", help="校验资产集合、SHA256SUMS、version.json 与 Binary")
    p_verify.add_argument("--dir", required=True)
    p_verify.add_argument("--repo", default=".")
    p_verify.add_argument("--version")
    p_verify.add_argument("--commit", help="独立预期 Commit（必填；不采信 version.json 自报值）")
    p_verify.add_argument("--tag", help="用于独立核验 Tag → Commit 绑定")
    p_verify.add_argument("--repository")
    p_verify.add_argument("--repository-required", action="store_true")
    p_verify.add_argument("--report")
    p_verify.add_argument("--json", action="store_true")
    p_verify.set_defaults(func=cmd_verify_assets)

    p_snapshot = sub.add_parser("snapshot", help="固定 9 项正式附件的上传前快照")
    p_snapshot.add_argument("--assets", required=True)
    p_snapshot.add_argument("--out", required=True)
    p_snapshot.add_argument("--manifest", required=True)
    p_snapshot.add_argument("--report")
    p_snapshot.add_argument("--json", action="store_true")
    p_snapshot.set_defaults(func=cmd_snapshot)

    p_downloaded = sub.add_parser("verify-uploaded", help="用平台侧下载副本核对 9 项附件")
    p_downloaded.add_argument("--assets", required=True)
    p_downloaded.add_argument("--downloads", required=True)
    p_downloaded.add_argument("--manifest", required=True, help="上传前快照 manifest（独立预期 SHA256）")
    p_downloaded.add_argument("--commit", help="独立预期 Commit（必填；不采信 version.json 自报值）")
    p_downloaded.add_argument("--report")
    p_downloaded.add_argument("--json", action="store_true")
    p_downloaded.set_defaults(func=cmd_verify_uploaded)

    p_gate = sub.add_parser("promote-gate", help="promote / latest 前置门槛")
    p_gate.add_argument("--stage", required=True, choices=("pre-promote", "post-promote", "latest"))
    p_gate.add_argument("--report", required=True)
    p_gate.add_argument("--assets")
    p_gate.add_argument("--expected-commit")
    p_gate.add_argument("--tag")
    p_gate.add_argument("--out")
    p_gate.add_argument("--json", action="store_true")
    p_gate.set_defaults(func=cmd_promote_gate)

    p_list = sub.add_parser("list", help="输出精确清单（供附件上传 / 下载）")
    p_list.add_argument("--dir", required=True)
    p_list.add_argument("--scope", choices=("attachments", "assets"), default="attachments")
    p_list.add_argument("--format", choices=("upload", "download"), default="upload")
    p_list.set_defaults(func=cmd_list)

    p_pipeline = sub.add_parser("check-pipeline", help="校验 .cnb.yml 覆盖完整 9 项附件与发布周期")
    p_pipeline.add_argument("--config", default=".cnb.yml")
    p_pipeline.add_argument("--dir", required=True)
    p_pipeline.add_argument("--report")
    p_pipeline.add_argument("--json", action="store_true")
    p_pipeline.set_defaults(func=cmd_check_pipeline)

    p_report = sub.add_parser("report", help="汇总各阶段报告，输出 Release Verification Report")
    p_report.add_argument("--dir", required=True)
    p_report.add_argument("--reports", required=True)
    p_report.add_argument("--require-all", action="store_true")
    p_report.add_argument("--json", action="store_true")
    p_report.set_defaults(func=cmd_report)

    p_repro = sub.add_parser("repro", help="两次独立构建并比较可复现性")
    p_repro.add_argument("--repo", required=True)
    p_repro.add_argument("--tag", required=True)
    p_repro.add_argument("--repository")
    p_repro.add_argument("--workdir")
    p_repro.add_argument("--ignore", action="append", default=["dist"])
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
