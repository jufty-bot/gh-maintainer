"""Current PR code replaces atomically, remains queryable offline and exposes gaps."""

import json
from copy import deepcopy

import anyio
import httpx
import pytest
from click.testing import CliRunner
from sqlalchemy import delete, func, select

from gh_maintainer.cli import main
from gh_maintainer.clock import now
from gh_maintainer.github import AsyncGitHubSource, GitHubError
from gh_maintainer.models import Item, PullCode, PullCommitRecord, Repository
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.repositories import CatalogRepository
from gh_maintainer.schemas import PullCodeSnapshot, RepositorySnapshot
from gh_maintainer.sync import SyncService
from gh_maintainer.web import create_app

from .fakes import AT, REPO, FakeSource

pytestmark = pytest.mark.anyio
DIFF = "diff --git a/app.py b/app.py\n@@ -1 +1 @@\n-old\n+new\n"


def commit(sha):
    return {
        "sha": sha,
        "html_url": f"https://github.com/owner/project/commit/{sha}",
        "commit": {
            "message": f"Update {sha}\n\nDetails <script>alert(1)</script>",
            "author": {"name": "Author", "date": AT},
            "committer": {"name": "Committer", "date": AT},
        },
    }


@pytest.mark.parametrize("failure", [None, "head", "base", "count", "page", "diff"])
async def test_source_pagination_representation_and_races(failure):
    raw = {
        "node_id": "PR_2",
        "state": "open",
        "updated_at": AT,
        "head": {"sha": "two"},
        "base": {"sha": "base"},
        "commits": 2,
    }
    seen = []

    def respond(request):
        seen.append(request)
        if request.url.path.endswith("/commits"):
            if request.url.params.get("page") == "2":
                if failure == "page":
                    return httpx.Response(404)
                return httpx.Response(200, json=[commit("two")])
            return httpx.Response(
                200,
                json=[commit("one")],
                headers={
                    "Link": "<https://api.github.com/repos/owner/project/pulls/2/"
                    'commits?page=2>; rel="next"'
                },
            )
        if request.headers["accept"] == "application/vnd.github.diff":
            return httpx.Response(200, text="{}" if failure == "diff" else DIFF)
        after = deepcopy(raw)
        if failure in {"head", "base"}:
            after[failure]["sha"] = "changed"
        if failure == "count":
            after["commits"] = 3
        return httpx.Response(200, json=after)

    source = AsyncGitHubSource(
        "test", requests_per_second=100000, transport=httpx.MockTransport(respond)
    )
    try:
        if failure:
            with pytest.raises(GitHubError):
                await source.pull_code(RepositorySnapshot.from_github(REPO), 2, raw)
        else:
            result = await source.pull_code(
                RepositorySnapshot.from_github(REPO), 2, raw
            )
            assert [c.sha for c in result.commits] == ["one", "two"]
            assert result.diff == DIFF
            assert result.commits[0].author == "Author"
            assert seen[-1].headers["accept"] == "application/vnd.github+json"
    finally:
        await source.close()


async def test_incomplete_commit_listing_rejected():
    source = AsyncGitHubSource(
        "test",
        requests_per_second=100000,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[])),
    )
    try:
        with pytest.raises(GitHubError, match="incomplete"):
            await source.pull_code(
                RepositorySnapshot.from_github(REPO),
                2,
                {
                    "node_id": "PR_2",
                    "state": "open",
                    "updated_at": AT,
                    "head": {"sha": "head"},
                    "base": {"sha": "base"},
                    "commits": 251,
                },
            )
    finally:
        await source.close()


async def test_sync_replaces_current_code_and_commit_membership(database):
    class Source(FakeSource):
        code_calls = 0
        base = "base"
        fail_code = False

        async def pull(self, repo, number):
            raw = await super().pull(repo, number)
            raw["base"] = {"sha": self.base}
            return raw

        async def pull_code(self, repo, number, raw):
            self.code_calls += 1
            if self.fail_code:
                raise GitHubError("Diff unavailable")
            snapshot = await super().pull_code(repo, number, raw)
            return snapshot.model_copy(update={"base_sha": self.base})

    source = Source()
    service = SyncService(database, source)
    assert (await service.run()).status == "complete"
    assert source.code_calls == 2
    async with database.sessions() as session:
        query = CatalogQueries(session)
        files = await query.files("owner/project#2")
        assert not files.stale and files.head_sha == "abc"
        assert (await query.commits("I_2", limit=1, offset=1)).commits == []
        assert (await query.commits("I_2")).commits[0].sha == "abc"
        first_fetch = files.fetched_at
    await service.run()
    assert source.code_calls == 2
    source.head_sha = source.signal_head = "force-pushed"
    await service.run()
    assert source.code_calls == 3  # unchanged historical PR uses its saved snapshot
    source.base = "new-base"
    await service.run()
    assert source.code_calls == 4
    async with database.sessions() as session:
        files = await CatalogQueries(session).files("I_2")
        assert files.head_sha == "force-pushed" and files.base_sha == "new-base"
        assert files.fetched_at >= first_fetch
        assert await session.scalar(select(func.count()).select_from(PullCode)) == 2
        assert (await CatalogQueries(session).commits("I_2")).commits[
            0
        ].sha == "force-pushed"
        repo = await session.get(Repository, "1")
        checkpoint = repo.synced_at
    source.fail_code = True
    source.head_sha = source.signal_head = "newer"
    assert (await service.run()).status == "partial"
    async with database.sessions() as session:
        assert (await session.get(Repository, "1")).synced_at == checkpoint
        files = await CatalogQueries(session).files("I_2")
        assert files.stale and files.head_sha == "force-pushed"
        assert (await CatalogQueries(session).commits("I_2")).commits[
            0
        ].sha == "force-pushed"
    source.fail_code = False
    await service.run()
    async with database.sessions() as session:
        assert not (await CatalogQueries(session).files("I_2")).stale


async def test_upgrade_backfills_closed_prs_without_losing_history(database):
    source = FakeSource()
    await SyncService(database, source).run()
    async with database.sessions.begin() as session:
        await session.execute(delete(PullCommitRecord))
        await session.execute(delete(PullCode))
    source.items = []  # closed PR isn't in the incremental issues feed
    await SyncService(database, source).run()
    async with database.sessions() as session:
        assert (await CatalogQueries(session).commits("I_3")).total == 1


async def test_missing_mismatched_code_and_offline_interfaces(database):
    source = FakeSource()
    await SyncService(database, source).run()
    app = create_app(str(database.engine.url.database), database=database)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/items/I_2/files")).json()["diff"]
        page = (await client.get("/api/items/I_2/commits?limit=1&offset=0")).json()
        assert page["total"] == 1 and page["files"]["diff"] is None
        assert (await client.get("/api/items/I_1/files")).status_code == 422
        assert (await client.get("/api/items/missing/files")).status_code == 404
    runner = CliRunner()
    for command in ("files", "commits"):
        result = await anyio.to_thread.run_sync(
            lambda command=command: runner.invoke(
                main,
                [
                    "--db",
                    str(database.engine.url.database),
                    command,
                    "I_2",
                    "--format",
                    "json",
                ],
            )
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)
    async with database.sessions.begin() as session:
        item = await session.get(Item, "I_2")
        item.pr = {**item.pr, "head": {"sha": "unfetched"}}
    async with database.sessions() as session:
        files = await CatalogQueries(session).files("I_2")
        assert files.stale and files.diff is None
    async with database.sessions.begin() as session:
        await session.execute(delete(PullCode).where(PullCode.item_id == "I_2"))
    async with database.sessions() as session:
        assert (await CatalogQueries(session).files("I_2")).fetched_at is None


async def test_mismatched_snapshot_cannot_commit(database):
    source = FakeSource()
    await SyncService(database, source).run()
    catalog = CatalogRepository(database)
    items = await catalog.items("1")
    with pytest.raises(ValueError, match="does not match"):
        await catalog.commit_repo(
            "1",
            items,
            now(),
            False,
            None,
            code={
                "I_2": PullCodeSnapshot(
                    head_sha="wrong",
                    base_sha="base",
                    diff=DIFF,
                    fetched_at=now(),
                    commits=[],
                ),
            },
        )
    async with database.sessions() as session:
        assert (await CatalogQueries(session).files("I_2")).head_sha == "abc"
