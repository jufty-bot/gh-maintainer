"""Bounded live evidence through the installed gh CLI; never writes GitHub or SQLite."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import subprocess
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import quote

from pydantic import ConfigDict, Field

from gh_maintainer.clock import now
from gh_maintainer.schemas import Contract, ItemView, repository_name

Section = Literal["overview", "guidance", "files", "file", "checks", "threads", "logs"]

THREADS_QUERY = """query(
 $owner:String!,$repo:String!,$number:Int!,$limit:Int!,$cursor:String){
 repository(owner:$owner,name:$repo){pullRequest(number:$number){
 reviewThreads(first:$limit,after:$cursor){totalCount pageInfo{hasNextPage endCursor}
 nodes{id isResolved isOutdated path line startLine diffSide
 comments(first:100){pageInfo{hasNextPage endCursor}
 nodes{id url body author{login} originalCommit{oid} commit{oid}}}}}}}}"""


class ContextRequest(Contract):
    section: Section = "overview"
    limit: int = Field(default=100, ge=1, le=100)
    page: int = Field(default=1, ge=1)
    cursor: str | None = None
    path: str | None = None
    ref: Literal["head", "base"] = "head"
    job_id: int | None = Field(default=None, ge=1)
    max_chars: int = Field(default=30000, ge=1, le=1000000)


class ContextPacket(Contract):
    model_config = ConfigDict(frozen=False, validate_assignment=True)
    item: str
    repo: str
    indexed_revision: int
    observed_at: str
    section: Section
    page: int = 1
    head_sha: str | None = None
    base_sha: str | None = None
    indexed_head_sha: str | None = None
    head_changed: bool = False
    stable: bool = False
    complete: bool = True
    truncated: bool = False
    next_page: int | None = None
    next_cursor: str | None = None
    missing: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ContextError(ValueError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"GitHub context request failed ({status}).")


def gh_api(endpoint: str, payload: dict[str, Any] | None = None) -> Any:
    command = ["gh", "api", "--hostname", "github.com", endpoint]
    if payload is None:
        command += ["--method", "GET"]
    else:
        command += ["--input", "-"]
    try:
        result = subprocess.run(  # noqa: S603
            command,
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError(
            "GitHub context requires gh authentication and a working connection."
        ) from None
    if result.returncode:
        # API errors can echo private source text. Do not copy stderr into reports.
        match = re.search(r"HTTP (\d{3})", result.stderr)
        raise ContextError(match[1] if match else f"gh exit {result.returncode}")
    if endpoint.endswith("/logs"):
        return result.stdout
    return json.loads(result.stdout)


def source_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("Use a repository-relative path without parent traversal.")
    return quote(str(path), safe="/")


def collect_context(item: ItemView, request: ContextRequest) -> ContextPacket:
    indexed_head = (item.pr or {}).get("head")
    indexed_sha = indexed_head.get("sha") if isinstance(indexed_head, dict) else None
    packet = ContextPacket(
        item=item.id,
        repo=item.repo,
        indexed_revision=item.revision,
        observed_at=now().isoformat(),
        section=request.section,
        page=request.page,
        indexed_head_sha=indexed_sha if isinstance(indexed_sha, str) else None,
    )
    root = f"repos/{item.repo}"
    is_pr = item.kind.value == "pr"

    def pull() -> dict[str, Any]:
        result: dict[str, Any] = gh_api(f"{root}/pulls/{item.number}")
        return result

    try:
        remote = pull() if is_pr else gh_api(root)
        if is_pr:
            packet.head_sha, packet.base_sha = (
                remote["head"]["sha"],
                remote["base"]["sha"],
            )
            packet.head_changed = packet.head_sha != packet.indexed_head_sha
        else:
            commit = gh_api(
                f"{root}/commits/{quote(remote['default_branch'], safe='')}"
            )
            packet.head_sha = packet.base_sha = commit["sha"]
            packet.stable = True  # Issue evidence is pinned to this immutable commit.
        sha = packet.head_sha if request.ref == "head" else packet.base_sha

        def contents(path: str, ref: str | None) -> dict[str, Any]:
            content_root = root
            if is_pr and ref == packet.head_sha and ref != packet.base_sha:
                head_repo = (remote["head"].get("repo") or {}).get("full_name")
                if not head_repo:
                    raise ValueError("PR fork is unavailable; cannot fetch head file.")
                content_root = f"repos/{repository_name(head_repo)}"
            data = gh_api(
                f"{content_root}/contents/{source_path(path)}"
                f"?ref={quote(ref or '', safe='')}"
            )
            if not isinstance(data, dict) or data.get("encoding") != "base64":
                raise ValueError("Content is unavailable, a directory, or too large.")
            return {
                "path": path,
                "ref": ref,
                "blob_sha": data["sha"],
                "url": data["html_url"],
                "text": base64.b64decode(data["content"]).decode(
                    "utf-8", errors="replace"
                ),
            }

        if request.section == "overview":
            packet.evidence = {
                "url": remote["html_url"],
                "state": remote.get("state"),
                "draft": remote.get("draft"),
                "mergeable": remote.get("mergeable"),
                "changed_files": remote.get("changed_files"),
                "head_repo": (remote.get("head", {}).get("repo") or {}).get(
                    "full_name"
                ),
            }
        elif request.section == "guidance":
            paths = ["AGENTS.md", "CONTRIBUTING.md", ".github/CONTRIBUTING.md"]
            if request.path:
                source_path(request.path)
                paths += [
                    str(parent / "AGENTS.md")
                    for parent in reversed(PurePosixPath(request.path).parents)
                    if str(parent) != "."
                ]
            if len(paths) > 20:  # noqa: PLR2004
                raise ValueError("Guidance path exceeds 20 ancestor documents.")
            documents = []
            absent = []
            trees: dict[str, dict[str, Any]] = {}

            def verified_absent(path: str) -> bool:
                tree_sha = packet.base_sha or ""
                parts = PurePosixPath(path).parts
                for index, part in enumerate(parts):
                    if tree_sha not in trees:
                        trees[tree_sha] = gh_api(f"{root}/git/trees/{tree_sha}")
                    tree = trees[tree_sha]
                    if tree.get("truncated"):
                        raise ValueError("Guidance tree listing is truncated.")
                    entry = next(
                        (node for node in tree["tree"] if node["path"] == part), None
                    )
                    if entry is None:
                        return True
                    if index == len(parts) - 1:
                        return False
                    if entry["type"] != "tree":
                        return True
                    tree_sha = entry["sha"]
                return False

            for path in dict.fromkeys(paths):
                try:
                    documents.append(contents(path, packet.base_sha))
                except ContextError as error:
                    if error.status == "404" and verified_absent(path):
                        absent.append(path)
                    else:
                        packet.missing.append(f"Guidance unavailable: {path}")
                except ValueError:
                    packet.missing.append(
                        f"Guidance unavailable: {path} at {packet.base_sha}"
                    )
            packet.evidence = {
                "documents": documents,
                "absent_at_base": absent,
                "authority": "base revision; caller scope takes precedence",
            }
        elif request.section == "file":
            if not request.path:
                raise ValueError("--path is required for file context.")
            packet.evidence = contents(request.path, sha)
        elif not is_pr:
            message = f"{request.section} requires an indexed pull request."
            raise ValueError(message)
        elif request.section == "files":
            files = gh_api(
                f"{root}/pulls/{item.number}/files?per_page={request.limit}&page={request.page}"
            )
            packet.evidence = {"total": remote["changed_files"], "files": files}
            if request.page * request.limit < remote["changed_files"]:
                packet.next_page = request.page + 1
            for file in files:
                if file.get("status") != "removed" and not file.get("patch"):
                    packet.missing.append(
                        f"Patch unavailable: {file['filename']}; fetch file/base"
                    )
        elif request.section == "checks":
            checks = gh_api(
                f"{root}/commits/{sha}/check-runs?per_page={request.limit}&page={request.page}"
            )
            statuses = gh_api(
                f"{root}/commits/{sha}/status?per_page={request.limit}&page={request.page}"
            )
            jobs = gh_api(
                f"{root}/actions/runs?head_sha={sha}&per_page={request.limit}&page={request.page}"
            )
            packet.evidence = {
                "ref": sha,
                "checks": checks,
                "statuses": statuses,
                "workflow_runs": jobs,
            }
            if any(
                request.page * request.limit < data["total_count"]
                for data in [checks, statuses, jobs]
            ):
                packet.next_page = request.page + 1
        elif request.section == "threads":
            owner, repo = item.repo.split("/")
            response = gh_api(
                "graphql",
                {
                    "query": THREADS_QUERY,
                    "variables": {
                        "owner": owner,
                        "repo": repo,
                        "number": item.number,
                        "limit": request.limit,
                        "cursor": request.cursor,
                    },
                },
            )
            if response.get("errors"):
                raise ValueError(
                    "Review-thread query returned incomplete GraphQL data."
                )
            threads = response["data"]["repository"]["pullRequest"]["reviewThreads"]
            packet.evidence = threads
            if threads["pageInfo"]["hasNextPage"]:
                packet.next_cursor = threads["pageInfo"]["endCursor"]
            for thread in threads["nodes"]:
                if thread["comments"]["pageInfo"]["hasNextPage"]:
                    packet.missing.append(
                        f"Thread {thread['id']} has more replies; "
                        "read them separately with gh api graphql"
                    )
        elif request.section == "logs":
            if request.job_id is None:
                raise ValueError(
                    "--job-id is required; find jobs with gh run view RUN --json jobs."
                )
            job = gh_api(f"{root}/actions/jobs/{request.job_id}")
            packet.evidence = {"job": job}
            if job["head_sha"] != packet.head_sha:
                packet.missing.append(
                    "Job belongs to a different head; logs were not fetched."
                )
            else:
                packet.evidence["text"] = gh_api(
                    f"{root}/actions/jobs/{request.job_id}/logs"
                )
        if is_pr:
            after = pull()
            packet.stable = (after["head"]["sha"], after["base"]["sha"]) == (
                packet.head_sha,
                packet.base_sha,
            )
            if not packet.stable:
                packet.missing.append(
                    "PR head/base changed during retrieval; reread context."
                )
    except (ValueError, KeyError, TypeError) as error:
        packet.missing.append(str(error))

    # Bound content, not JSON syntax. Always report clipped evidence as incomplete.
    remaining = request.max_chars

    def bounded(value: Any) -> Any:
        nonlocal remaining
        if isinstance(value, str):
            text = value[:remaining]
            remaining -= len(text)
            packet.truncated |= len(text) < len(value)
            return text
        if isinstance(value, dict):
            return {key: bounded(child) for key, child in value.items()}
        if isinstance(value, list):
            return [bounded(child) for child in value]
        return value

    packet.evidence = bounded(packet.evidence)
    packet.complete = not (
        packet.missing
        or packet.truncated
        or packet.next_page
        or packet.next_cursor
        or not packet.stable
    )
    return packet


async def context_for(item: ItemView, request: ContextRequest) -> ContextPacket:
    return await asyncio.to_thread(collect_context, item, request)
