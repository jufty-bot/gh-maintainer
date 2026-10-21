"""Offline sample portfolio for trying the dashboard without GitHub credentials."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from sqlalchemy import func, select

from gh_maintainer.clock import now
from gh_maintainer.database import Database
from gh_maintainer.models import Repository
from gh_maintainer.repositories import CatalogRepository, SyncJournal, SyncLease
from gh_maintainer.schemas import (
    AccountSnapshot,
    CommentSnapshot,
    ItemSnapshot,
    JsonObject,
    Kind,
    PullCodeSnapshot,
    PullCommit,
    RepositoryActivity,
    RepositoryCommit,
    RepositoryItem,
    RepositoryRelease,
    RepositorySnapshot,
    State,
)

MERGED_PR = 36
LINKED_PR = 42
LINKED_ISSUE = 39


async def seed_demo(database: Database) -> None:
    async with database.sessions() as session:
        count = await session.scalar(select(func.count()).select_from(Repository))
    if count:
        raise ValueError("Demo requires an empty database; use --db .data/demo.sqlite.")
    at = now()
    catalog = CatalogRepository(database)
    repos: list[JsonObject] = [
        {
            "id": index,
            "full_name": f"example/{name}",
            "html_url": f"https://github.com/example/{name}",
            "description": description,
            "archived": False,
            "private": name == "infrastructure",
            "owner": {"login": "example", "id": "demo"},
            "language": language,
            "stargazers_count": stars,
            "forks_count": forks,
        }
        for index, (name, description, language, stars, forks) in enumerate(
            [
                ("orbit", "An API for small, connected services", "Python", 1284, 96),
                (
                    "field-notes",
                    "A shared notebook for the internet",
                    "TypeScript",
                    342,
                    28,
                ),
                (
                    "infrastructure",
                    "Configuration for the things that run",
                    "HCL",
                    0,
                    0,
                ),
            ],
            1,
        )
    ]
    await catalog.discover(
        AccountSnapshot(id="demo", login="example"),
        [RepositorySnapshot.from_github(repo) for repo in repos],
    )
    samples = [
        (
            1,
            42,
            "pr",
            "Upgrade HTTP client and retry transient failures",
            2,
            "FAILURE",
            "APPROVED",
        ),
        (
            1,
            39,
            "issue",
            "Requests hang when the upstream connection closes",
            34,
            None,
            None,
        ),
        (
            2,
            18,
            "pr",
            "Add keyboard shortcuts to the editor",
            4,
            "SUCCESS",
            "REVIEW_REQUIRED",
        ),
        (2, 15, "issue", "Support importing Markdown notebooks", 8, None, None),
        (
            3,
            7,
            "pr",
            "Rotate service account permissions",
            1,
            "PENDING",
            "CHANGES_REQUESTED",
        ),
        (1, 36, "pr", "Document authentication options", 7, "SUCCESS", "APPROVED"),
    ]
    for repo in repos:
        rows = []
        comments = []
        for repo_id, number, kind, title, days, checks, review in samples:
            if repo_id != repo["id"]:
                continue
            updated = (at - timedelta(days=days)).isoformat().replace("+00:00", "Z")
            issue: JsonObject = {
                "node_id": f"DEMO_{repo_id}_{number}",
                "number": number,
                "title": title,
                "state": "closed" if number == MERGED_PR else "open",
                "updated_at": updated,
                "created_at": updated,
                "html_url": (
                    f"{repo['html_url']}/"
                    f"{'pull' if kind == 'pr' else 'issues'}/{number}"
                ),
                "labels": [{"name": "bug" if kind == "issue" else "maintenance"}],
                "user": {"login": "contributor"},
                "assignees": [],
                "body": (
                    "Upgrade HTTP client to retry transient failures.\n\n"
                    f"Closes #{LINKED_ISSUE}."
                    if number == LINKED_PR
                    else "Demo item. All data in this database is synthetic."
                ),
            }
            if kind == "pr":
                issue["pull_request"] = {}
            row = ItemSnapshot.from_github(issue, str(repo_id))
            if kind == "pr":
                comment_samples: list[
                    tuple[Literal["issue", "inline", "review"], str, JsonObject]
                ] = [
                    ("issue", "Ready for **review**.", {}),
                    (
                        "review",
                        "Please check the `retry` behavior.",
                        {"state": "COMMENTED"},
                    ),
                    (
                        "inline",
                        "Could we add a test here?",
                        {
                            "id": 1001,
                            "path": "src/retry.py",
                            "line": 5,
                            "diff_hunk": "@@ -1 +1 @@\n+retry()",
                        },
                    ),
                ]
                if number != LINKED_PR:
                    comment_samples.append(
                        (
                            "inline",
                            "Added test coverage in latest commit.",
                            {
                                "id": 1002,
                                "in_reply_to_id": 1001,
                                "path": "src/retry.py",
                                "line": 5,
                                "diff_hunk": "@@ -1 +1 @@\n+retry()",
                            },
                        )
                    )
                for comment_kind, body, extra in comment_samples:
                    identifier = f"DEMO_COMMENT_{repo_id}_{number}_{comment_kind}"
                    comments.append(
                        CommentSnapshot.from_github(
                            {
                                "node_id": identifier,
                                "body": body,
                                "user": {"login": "reviewer"},
                                "html_url": f"{issue['html_url']}#{identifier}",
                                "created_at": updated,
                                "updated_at": updated,
                                **extra,
                            },
                            str(repo_id),
                            number,
                            comment_kind,
                        )
                    )
                pr = {
                    "node_id": f"PR_{repo_id}_{number}",
                    "draft": False,
                    "state": "closed" if number == MERGED_PR else "open",
                    "updated_at": updated,
                    "head": {"sha": f"demo-head-{repo_id}-{number}"},
                    "base": {"sha": f"demo-base-{repo_id}"},
                    "commits": 1,
                }
                signals = {
                    "id": f"PR_{repo_id}_{number}",
                    "reviewDecision": review,
                    "mergeable": "MERGEABLE",
                    "commits": {
                        "nodes": [{"commit": {"statusCheckRollup": {"state": checks}}}]
                    },
                }
                row = row.model_copy(
                    update={"pr": pr, "signals": signals, "signals_at": at}
                )
                if number == MERGED_PR:
                    row = row.model_copy(update={"state": State.MERGED})
            rows.append(row)
        await catalog.commit_repo(
            str(repo["id"]),
            rows,
            at,
            True,
            None,
            comments=comments,
            code={
                row.id: PullCodeSnapshot(
                    head_sha=f"demo-head-{repo['id']}-{row.number}",
                    base_sha=f"demo-base-{repo['id']}",
                    fetched_at=at,
                    diff=(
                        "diff --git a/src/client.py b/src/client.py\n"
                        "--- a/src/client.py\n+++ b/src/client.py\n"
                        "@@ -1 +1 @@\n-timeout = 10\n+timeout = 30\n"
                    ),
                    commits=[
                        PullCommit(
                            sha=f"demo-head-{repo['id']}-{row.number}",
                            message=f"{row.title}\n\nSynthetic demo commit.",
                            author="contributor",
                            committed_at=row.updated_at,
                            url=f"{repo['html_url']}/commit/demo-head-{repo['id']}-{row.number}",
                            raw={"synthetic": True},
                        )
                    ],
                )
                for row in rows
                if row.kind == Kind.PR
            },
        )
        latest_items = {}
        for kind in (Kind.PR, Kind.ISSUE):
            candidates = [item for item in rows if item.kind == kind]
            latest = (
                max(candidates, key=lambda item: item.updated_at)
                if candidates
                else None
            )
            latest_items[kind] = (
                RepositoryItem(
                    id=str((latest.pr or {}).get("node_id", latest.id)),
                    number=latest.number,
                    title=latest.title,
                    state=latest.state,
                    created_at=latest.updated_at,
                    updated_at=latest.updated_at,
                    url=latest.url,
                )
                if latest
                else None
            )
        sha = "a" * 39 + str(repo["id"])
        activity = RepositoryActivity(
            default_branch="main",
            last_commit=RepositoryCommit(
                sha=sha,
                message="Synthetic default branch tip",
                committed_at=at - timedelta(days=1),
                url=f"{repo['html_url']}/commit/{sha}",
            ),
            last_release=RepositoryRelease(
                id="DEMO_RELEASE_1",
                name="Synthetic release",
                tag_name="v0.2.0",
                published_at=at - timedelta(days=3),
                url=f"{repo['html_url']}/releases/tag/v0.2.0",
            )
            if repo["id"] == 1
            else None,
            last_pr=latest_items[Kind.PR],
            last_issue=latest_items[Kind.ISSUE],
            raw={"synthetic": True},
        )
        await catalog.commit_activity({str(repo["id"]): activity}, {}, at)

    async with SyncLease(database) as lease:
        journal = SyncJournal(database)
        run_id = await journal.start(lease)
        await journal.finish(run_id, "complete", 0, None, lease)
