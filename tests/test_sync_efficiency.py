"""Resource regressions and freshness contracts for large offline portfolios."""

import asyncio
from copy import deepcopy
from math import ceil
from weakref import ref

import pytest
from sqlalchemy import event, func, select

from gh_maintainer.database import Database
from gh_maintainer.github import GitHubError
from gh_maintainer.models import Observation, Repository
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.repositories import AnalysisService, CatalogRepository, TriageService
from gh_maintainer.schemas import (
    AccountSnapshot,
    AnalysisInput,
    Disposition,
    ItemSnapshot,
    RepositorySnapshot,
)
from gh_maintainer.sync import SyncService

from .fakes import REPO, FakeSource, issue
from .test_maintenance import comment

pytestmark = pytest.mark.anyio


async def test_profile_changes_preserve_processing_and_analysis(
    database: Database,
) -> None:
    source = FakeSource()
    source.items[0]["user"] = {"id": 99, "login": "author", "avatar_url": "old"}
    source.conversation = [comment(number=1)]
    await SyncService(database, source).run()
    catalog = CatalogRepository(database)
    revision = (await catalog.parents("1", {1}))[0].revision
    analysis = AnalysisService(database)
    await analysis.save("I_1", "summary", AnalysisInput(text="Reviewed"), revision)
    await TriageService(database).decide(
        "I_1", Disposition.DONE, "Keep note", revision=revision
    )
    await analysis.mark_processed("I_1", revision)
    async with database.sessions() as session:
        observations = await session.scalar(
            select(func.count()).select_from(Observation)
        )

    source.items[0]["user"] = {"id": 99, "login": "author", "avatar_url": "new"}
    raw = {
        **source.conversation[0].raw,
        "user": {"login": "reviewer", "avatar_url": "new"},
    }
    source.conversation[0] = source.conversation[0].model_copy(update={"raw": raw})
    await SyncService(database, source).run()
    async with database.sessions() as session:
        queries = CatalogQueries(session)
        detail = (await queries.show("I_1")).item
        assert detail.revision == revision
        assert detail.processing_state == "processed"
        assert detail.disposition == Disposition.DONE
        assert not detail.summary.stale
        assert detail.raw["user"]["avatar_url"] == "new"
        assert (await queries.comments("I_1")).comments[0].raw["user"][
            "avatar_url"
        ] == "new"
        assert (
            await session.scalar(select(func.count()).select_from(Observation))
            == observations
        )


@pytest.mark.parametrize(
    "change",
    [
        {"body": "Edited"},
        {"labels": [{"name": "bug"}]},
        {"assignees": [{"login": "new-person"}]},
        {"state": "closed"},
        {"new_source_field": "Preserve unknown meaningful changes"},
    ],
)
async def test_content_changes_still_advance_revisions(
    database: Database, change: dict
) -> None:
    source = FakeSource()
    await SyncService(database, source).run()
    source.items[0] = {**source.items[0], **change}
    await SyncService(database, source).run()
    assert (await CatalogRepository(database).parents("1", {1}))[0].revision == 2


async def test_embedded_repository_statistics_do_not_invalidate_pr(
    database: Database,
) -> None:
    class MetadataSource(FakeSource):
        stars = 1
        base_sha = "base"

        async def pull(self, repo, number):
            raw = await super().pull(repo, number)
            raw["base"] = {
                "sha": self.base_sha,
                "ref": "main",
                "repo": {
                    "id": 1,
                    "full_name": "owner/project",
                    "stargazers_count": self.stars,
                },
            }
            return raw

    source = MetadataSource()
    await SyncService(database, source).run()
    source.stars = 2
    await SyncService(database, source).run()
    item = (await CatalogRepository(database).parents("1", {2}))[0]
    assert item.revision == 1
    assert item.pr["base"]["repo"]["stargazers_count"] == 2
    source.base_sha = "new-base"
    await SyncService(database, source).run()
    assert (await CatalogRepository(database).parents("1", {2}))[0].revision == 2


async def test_unchanged_large_inventory_does_not_rewrite_metadata(
    database: Database,
) -> None:
    catalog = CatalogRepository(database)
    owner = AccountSnapshot(id="77", login="owner")
    repos = [
        RepositorySnapshot.from_github(
            {**REPO, "id": number, "full_name": f"owner/project{number}"}
        )
        for number in range(1, 1202)
    ]
    await catalog.discover(owner, repos)
    writes = []

    def capture(_conn, cursor, statement, _parameters, _context, _many):
        if statement.startswith(("INSERT", "UPDATE")):
            writes.append(cursor.rowcount)

    event.listen(database.engine.sync_engine, "after_cursor_execute", capture)
    try:
        await catalog.discover(owner, repos)
    finally:
        event.remove(database.engine.sync_engine, "after_cursor_execute", capture)
    assert sum(writes) == 0
    async with database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Repository).where(Repository.present)
            )
            == 1201
        )
    await catalog.discover(owner, [])
    async with database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Repository).where(Repository.present)
            )
            == 0
        )
    await catalog.discover(owner, repos)
    assert (await catalog.repo("1201")).present


async def test_incremental_comment_reads_follow_incoming_ids(
    database: Database,
) -> None:
    source = FakeSource()
    source.items = [issue()]
    source.conversation = [comment(f"IC_{n}", number=1) for n in range(601)]
    await SyncService(database, source).run()
    source.items, source.conversation = [], []
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("SELECT comments."):
            statements.append(statement)

    event.listen(database.engine.sync_engine, "before_cursor_execute", capture)
    try:
        await SyncService(database, source).run()
        assert statements == []
        source.conversation = [
            comment(f"IC_{n}", number=1, body="Edited") for n in range(601)
        ]
        await SyncService(database, source).run()
        assert len(statements) == 2
        assert all("comments.id IN" in statement for statement in statements)
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", capture)
    async with database.sessions() as session:
        queries = CatalogQueries(session)
        assert (await queries.comments("I_1")).total == 601
        assert (await queries.show("I_1")).item.revision == 2


class PortfolioSource(FakeSource):
    def __init__(self, size=201):
        super().__init__()
        self.inventory = [
            {**REPO, "id": n, "full_name": f"owner/project{n}"}
            for n in range(1, size + 1)
        ]
        self.batches = []
        self.incremental = False
        self.fail_node = None

    async def issues(self, repo, since):
        self.calls += 1
        self.windows.append(since)
        raw = {**issue(2, True), "node_id": f"I_repo{repo.id}"}
        yield [] if self.incremental else [ItemSnapshot.from_github(raw, repo.id)]

    async def pull(self, repo, number):
        raw = await super().pull(repo, number)
        raw["node_id"] = f"PR_repo{repo.id}"
        return raw

    async def signals(self, ids):
        self.batches.append(ids)
        if self.fail_node in ids:
            raise GitHubError("One batch failed")
        return await super().signals(ids)


@pytest.mark.parametrize("concurrency", [4, 20])
async def test_hundreds_of_repositories_share_bounded_signal_batches(
    database: Database, concurrency: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = PortfolioSource()
    service = SyncService(database, source, concurrency=concurrency)
    original = service._repository
    references = []
    peak = 0

    async def track(*args):
        nonlocal peak
        batch = await original(*args)
        references.append(ref(batch))
        peak = max(peak, sum(reference() is not None for reference in references))
        return batch

    monkeypatch.setattr(service, "_repository", track)
    assert (await service.run()).synced == 201
    assert [len(batch) for batch in source.activity_batches] == [20] * 10 + [1]
    assert peak <= concurrency
    assert len(source.batches) == ceil(201 / concurrency)
    assert max(map(len, source.batches)) == concurrency
    assert len({node for batch in source.batches for node in batch}) == 201
    source.incremental = True
    source.batches.clear()
    source.checks = "FAILURE"
    assert (await service.run()).status == "complete"
    assert len(source.batches) == ceil(201 / concurrency)
    async with database.sessions() as session:
        detail = await CatalogQueries(session).detail("I_repo201")
        assert detail.item.checks == "FAILURE"


async def test_failed_shared_batch_preserves_only_affected_signal_coverage(
    database: Database,
) -> None:
    source = PortfolioSource(25)
    service = SyncService(database, source, concurrency=25)
    await service.run()
    catalog = CatalogRepository(database)
    before = await catalog.repo("1")
    source.incremental = True
    source.fail_node = "PR_repo1"
    source.checks = "FAILURE"
    result = await service.run()
    assert result.status == "partial" and result.synced == 25
    assert len(result.errors) == 20
    assert (await catalog.repo("1")).signals_at == before.signals_at
    assert (await catalog.repo("1")).synced_at > before.synced_at
    assert (await catalog.repo("21")).error is None
    async with database.sessions() as session:
        queries = CatalogQueries(session)
        assert (await queries.detail("I_repo1")).item.signals_stale
        assert (await queries.detail("I_repo21")).item.checks == "FAILURE"


async def test_partial_batches_for_one_repository_keep_all_old_signals(
    database: Database,
) -> None:
    class ManyPullsSource(FakeSource):
        async def signals(self, ids):
            if self.fail_signals and "PR_30" in ids:
                raise GitHubError("Last batch failed")
            # Successful earlier batches must not replace this repo's old signals.
            failing = self.fail_signals
            self.fail_signals = False
            try:
                return await super().signals(ids)
            finally:
                self.fail_signals = failing

    source = ManyPullsSource()
    source.items = [issue(n, True) for n in range(10, 31)]
    await SyncService(database, source).run()
    catalog = CatalogRepository(database)
    before = deepcopy(await catalog.items("1"))
    source.fail_signals = True
    source.checks = "FAILURE"
    assert (await SyncService(database, source).run()).status == "partial"
    after = await catalog.items("1")
    assert {item.id: (item.signals, item.signals_at) for item in after} == {
        item.id: (item.signals, item.signals_at) for item in before
    }


async def test_explicit_portfolio_discovery_uses_bounded_parallel_requests(
    database: Database,
) -> None:
    class DirectPortfolio(PortfolioSource):
        active = 0
        peak = 0

        async def repository(self, name):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0)
                return await super().repository(name)
            finally:
                self.active -= 1

    source = DirectPortfolio(100)
    names = {repo["full_name"] for repo in source.inventory}
    result = await SyncService(database, source, concurrency=4).run(only=names)
    assert result.status == "complete" and result.synced == 100
    assert source.peak == 4 and source.active == 0


async def test_review_dismissal_without_body_or_parent_change_is_activity(
    database: Database,
) -> None:
    source = FakeSource()
    review = comment("RV_3", number=3, kind="review", body="Reviewed")
    source.conversation = [
        review.model_copy(update={"raw": {**review.raw, "state": "APPROVED"}})
    ]
    await SyncService(database, source).run()
    await AnalysisService(database).mark_processed("I_3")
    source.items = []
    source.conversation = [
        review.model_copy(update={"raw": {**review.raw, "state": "DISMISSED"}})
    ]
    await SyncService(database, source).run()
    async with database.sessions() as session:
        query = CatalogQueries(session)
        assert (await query.show("I_3")).item.processing_state == "changed"
        assert (await query.changes("I_3", since_check=True)).changes[
            0
        ].kind == "reviewed"
