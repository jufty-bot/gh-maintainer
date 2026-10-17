"""Offline sample portfolio for trying the dashboard without GitHub credentials."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from gh_maintainer.clock import now
from gh_maintainer.database import Database
from gh_maintainer.models import Repository
from gh_maintainer.repositories import CatalogRepository, SyncJournal, SyncLease
from gh_maintainer.schemas import (
    AccountSnapshot,
    ItemSnapshot,
    JsonObject,
    RepositorySnapshot,
    State,
)

MERGED_PR = 36


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
        }
        for index, (name, description, language) in enumerate(
            [
                ("orbit", "An API for small, connected services", "Python"),
                ("field-notes", "A shared notebook for the internet", "TypeScript"),
                ("infrastructure", "Configuration for the things that run", "HCL"),
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
                "html_url": (
                    f"{repo['html_url']}/"
                    f"{'pull' if kind == 'pr' else 'issues'}/{number}"
                ),
                "labels": [{"name": "bug" if kind == "issue" else "maintenance"}],
                "user": {"login": "contributor"},
                "assignees": [],
                "body": "Demo item. All data in this database is synthetic.",
            }
            if kind == "pr":
                issue["pull_request"] = {}
            row = ItemSnapshot.from_github(issue, str(repo_id))
            if kind == "pr":
                pr = {"node_id": f"PR_{repo_id}_{number}", "draft": False}
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
        await catalog.commit_repo(str(repo["id"]), rows, at, True, None)

    async with SyncLease(database) as lease:
        journal = SyncJournal(database)
        run_id = await journal.start(lease)
        await journal.finish(run_id, "complete", 0, None, lease)
