"""Repository recency uses bounded source probes and explicit independent coverage."""

import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from click.testing import CliRunner
from sqlalchemy import func, select

from gh_maintainer import cli
from gh_maintainer.database import Database
from gh_maintainer.github import AsyncGitHubSource, GitHubError
from gh_maintainer.models import Observation
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.repositories import AnalysisService, CatalogRepository
from gh_maintainer.schemas import AnalysisInput, RepositoryActivity, RepositorySnapshot
from gh_maintainer.sync import SyncService

from .fakes import AT, REPO, FakeSource

pytestmark = pytest.mark.anyio


def activity_payload(repo_id="1") -> dict:
    url = "https://github.com/owner/project"
    return {
        "databaseId": int(repo_id),
        "defaultBranchRef": {
            "name": "main",
            "target": {
                "oid": "abcdef123456789",
                "messageHeadline": "Latest main commit",
                "committedDate": AT,
                "url": f"{url}/commit/abcdef123456789",
            },
        },
        "latestRelease": {
            "id": "REL_1",
            "name": "Version one",
            "tagName": "v1.0.0",
            "publishedAt": AT,
            "url": f"{url}/releases/tag/v1.0.0",
        },
        "pullRequests": {
            "nodes": [
                {
                    "id": "PR_8",
                    "number": 8,
                    "title": "Newest PR",
                    "state": "MERGED",
                    "createdAt": AT,
                    "updatedAt": AT,
                    "url": f"{url}/pull/8",
                }
            ]
        },
        "issues": {
            "nodes": [
                {
                    "id": "I_7",
                    "number": 7,
                    "title": "Newest issue",
                    "state": "CLOSED",
                    "createdAt": AT,
                    "updatedAt": AT,
                    "url": f"{url}/issues/7",
                }
            ]
        },
    }


def empty_payload(repo_id="1") -> dict:
    return {
        "databaseId": int(repo_id),
        "defaultBranchRef": None,
        "latestRelease": None,
        "pullRequests": {"nodes": []},
        "issues": {"nodes": []},
    }


async def test_activity_queries_batch_all_four_records_without_history_scans() -> None:
    batches = []

    def respond(request):
        body = json.loads(request.content)
        assert "states:[OPEN,CLOSED,MERGED]" in body["query"]
        assert "states:[OPEN,CLOSED]" in body["query"]
        assert body["query"].count("field:CREATED_AT,direction:DESC") == 2
        assert "defaultBranchRef" in body["query"] and "latestRelease" in body["query"]
        count = len(body["variables"]) // 2
        batches.append(count)
        return httpx.Response(
            200,
            json={
                "data": {
                    f"r{i}": activity_payload(
                        body["variables"][f"name{i}"].removeprefix("repo")
                    )
                    for i in range(count)
                }
            },
        )

    source = AsyncGitHubSource(
        "test", transport=httpx.MockTransport(respond), requests_per_second=1000000
    )
    repos = [
        RepositorySnapshot.from_github({**REPO, "id": n, "full_name": f"owner/repo{n}"})
        for n in range(1, 46)
    ]
    try:
        values = await source.repository_activity(repos)
        assert batches == [20, 20, 5]
        assert set(values) == {str(n) for n in range(1, 46)}
        assert values["1"].last_commit.sha == "abcdef123456789"
        assert values["1"].last_release.tag_name == "v1.0.0"
        assert values["1"].last_pr.state.value == "merged"
        assert values["1"].last_issue.state.value == "closed"
        assert await source.repository_activity([]) == {}
        assert source.calls == 3
    finally:
        await source.close()


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "wrong_id",
        "null_node",
        "missing_connection",
        "empty_branch",
        "partial_error",
    ],
)
async def test_one_incomplete_alias_does_not_discard_other_repositories(
    invalid: str,
) -> None:
    raw = activity_payload()
    response = {"data": {"r0": raw, "r1": empty_payload("2")}}
    if invalid == "missing":
        del response["data"]["r0"]
    elif invalid == "wrong_id":
        raw["databaseId"] = 999
    elif invalid == "null_node":
        raw["issues"]["nodes"] = [None]
    elif invalid == "missing_connection":
        del raw["issues"]
    elif invalid == "empty_branch":
        raw["defaultBranchRef"] = {}
    else:
        response["errors"] = [{"message": "private detail", "path": ["r0", "issues"]}]
    source = AsyncGitHubSource(
        "test",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)),
    )
    repos = [RepositorySnapshot.from_github({**REPO, "id": n}) for n in (1, 2)]
    try:
        values = await source.repository_activity(repos)
        assert set(values) == {"2"}
        assert values["2"].last_commit is None
        assert values["2"].last_release is None
        assert values["2"].last_pr is None
        assert values["2"].last_issue is None
    finally:
        await source.close()


async def test_global_activity_failure_is_not_known_absence() -> None:
    source = AsyncGitHubSource(
        "test",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"data": None, "errors": [{"message": "private detail"}]}
            )
        ),
    )
    try:
        with pytest.raises(GitHubError, match="incomplete repository activity"):
            await source.repository_activity([RepositorySnapshot.from_github(REPO)])
    finally:
        await source.close()


async def test_activity_round_trip_and_default_branch_force_push(
    database: Database,
) -> None:
    source = FakeSource()
    raw = activity_payload()
    source.activity_values["1"] = RepositoryActivity.from_github(raw, "1")
    await SyncService(database, source).run()
    catalog = CatalogRepository(database)
    before = await catalog.repo("1")
    assert before.activity.raw == raw
    assert before.activity_synced_at and not before.activity_stale
    assert before.activity.last_pr.number == 8
    assert before.activity.last_issue.number == 7
    # Latest means the observed branch tip, even if its committer date goes back.
    changed = deepcopy(raw)
    changed["defaultBranchRef"]["name"] = "trunk"
    changed["defaultBranchRef"]["target"].update(
        oid="older-tip", committedDate="2020-01-01T00:00:00Z"
    )
    source.activity_values["1"] = RepositoryActivity.from_github(changed, "1")
    async with database.sessions() as session:
        observations = await session.scalar(
            select(func.count()).select_from(Observation)
        )
    await SyncService(database, source).run()
    after = await catalog.repo("1")
    assert after.activity.default_branch == "trunk"
    assert after.activity.last_commit.sha == "older-tip"
    assert after.activity_synced_at > before.activity_synced_at
    async with database.sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(Observation))
            == observations
        )


async def test_failure_retains_snapshot_and_timestamp_then_empty_success_clears_it(
    database: Database,
) -> None:
    source = FakeSource()
    source.activity_values["1"] = RepositoryActivity.from_github(
        activity_payload(), "1"
    )
    await SyncService(database, source).run()
    catalog = CatalogRepository(database)
    before = await catalog.repo("1")
    analysis = AnalysisService(database)
    await analysis.save("I_1", "summary", AnalysisInput(text="Reviewed"), 1)
    await analysis.mark_processed("I_1", 1)
    source.fail_activity = True
    result = await SyncService(database, source).run()
    assert result.status == "partial" and result.synced == 1
    stale = await catalog.repo("1")
    assert stale.activity == before.activity
    assert stale.activity_synced_at == before.activity_synced_at
    assert stale.activity_stale and stale.activity_error
    assert stale.synced_at > before.synced_at and stale.error is None
    async with database.sessions() as session:
        query = CatalogQueries(session)
        assert (await query.show("I_1")).item.processing_state == "processed"
        assert not (await query.show("I_1")).item.summary.stale
        assert (await query.overview()).counts.incomplete_repositories == 1
    source.fail_activity = False
    source.activity_values["1"] = RepositoryActivity.from_github(empty_payload(), "1")
    assert (await SyncService(database, source).run()).status == "complete"
    fresh = await catalog.repo("1")
    assert not fresh.activity_stale and fresh.activity_error is None
    assert fresh.activity.last_commit is None and fresh.activity.last_release is None
    assert fresh.activity.last_pr is None and fresh.activity.last_issue is None


async def test_activity_refreshes_even_when_conversation_fetch_fails(
    database: Database,
) -> None:
    source = FakeSource()
    source.activity_values["1"] = RepositoryActivity.from_github(
        activity_payload(), "1"
    )
    source.fail = True
    assert (await SyncService(database, source).run()).status == "partial"
    repo = await CatalogRepository(database).repo("1")
    assert repo.synced_at is None
    assert repo.activity.last_commit and not repo.activity_stale
    assert repo.activity_synced_at is not None


async def test_repos_cli_exposes_summary_dates_links_and_coverage(
    database: Database,
) -> None:
    source = FakeSource()
    source.activity_values["1"] = RepositoryActivity.from_github(
        activity_payload(), "1"
    )
    await SyncService(database, source).run()
    runner = CliRunner()
    args = ["--db", database.engine.url.database, "repos"]
    result = await asyncio.to_thread(
        runner.invoke, cli.main, [*args, "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    repo = json.loads(result.stdout)["repos"][0]
    assert repo["activity"]["last_release"]["url"].endswith("releases/tag/v1.0.0")
    assert repo["activity"]["last_pr"]["created_at"].startswith("2026-10-01")
    assert repo["activity_synced_at"] and not repo["activity_stale"]
    text = await asyncio.to_thread(runner.invoke, cli.main, args)
    assert text.exit_code == 0, text.output
    assert "LAST COMMIT" in text.stdout and "v1.0.0" in text.stdout
    assert "#8" in text.stdout and "#7" in text.stdout
