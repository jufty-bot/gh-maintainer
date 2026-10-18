"""Sync contract parity across the async storage and source boundaries."""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import event, func, select

from gh_maintainer.clock import now
from gh_maintainer.database import Database
from gh_maintainer.github import GitHubError
from gh_maintainer.models import Observation, Setting, SyncRun
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.repositories import CatalogRepository, SyncLease
from gh_maintainer.schemas import ItemFilters, ItemSnapshot, State
from gh_maintainer.sync import OVERLAP, SyncService

from .fakes import REPO, FakeSource, issue

pytestmark = pytest.mark.anyio


async def test_backfill_idempotence_and_merged_state(database: Database) -> None:
    source, catalog = FakeSource(), CatalogRepository(database)
    assert (await SyncService(database, source).run()).status == "complete"
    items = await catalog.items("1")
    assert len(items) == 3
    assert next(item for item in items if item.id == "I_3").state == State.MERGED
    await SyncService(database, source).run(full=True)
    async with database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Observation)) == 3


async def test_incremental_ci_refresh_without_issue_timestamp_change(
    database: Database,
) -> None:
    source, catalog = FakeSource(), CatalogRepository(database)
    await SyncService(database, source).run()
    checkpoint = (await catalog.repo("1")).synced_at
    source.items, source.checks = [], "FAILURE"
    await SyncService(database, source).run()
    assert source.windows[-1] == checkpoint - OVERLAP
    async with database.sessions() as session:
        detail = await CatalogQueries(session).detail("I_2")
        assert detail.item.checks == "FAILURE"
        assert "Failing checks" in detail.item.reasons
        assert await session.scalar(select(func.count()).select_from(Observation)) == 4


@pytest.mark.parametrize("after_page", [False, True])
async def test_failed_fetch_keeps_atomic_checkpoint(
    database: Database, after_page: bool
) -> None:
    source, catalog = FakeSource(), CatalogRepository(database)
    await SyncService(database, source).run()
    checkpoint = (await catalog.repo("1")).synced_at
    source.fail, source.fail_after_page = not after_page, after_page
    result = await SyncService(database, source).run()
    assert result.status == "partial"
    assert (await catalog.repo("1")).synced_at == checkpoint
    assert len(await catalog.items("1")) == 3
    source.fail = source.fail_after_page = False
    assert (await SyncService(database, source).run()).status == "complete"
    assert (await catalog.repo("1")).error is None


async def test_failed_signals_keep_timestamp_and_become_unknown(
    database: Database,
) -> None:
    source, catalog = FakeSource(), CatalogRepository(database)
    await SyncService(database, source).run()
    before = (await catalog.repo("1")).signals_at
    source.fail_signals = True
    assert (await SyncService(database, source).run()).status == "partial"
    assert (await catalog.repo("1")).signals_at == before
    async with database.sessions() as session:
        detail = await CatalogQueries(session).detail("I_2")
        assert detail.item.signals_stale
        assert detail.item.checks == "UNKNOWN"


async def test_reconciliation_retains_history_and_missing_inventory(
    database: Database,
) -> None:
    source, catalog = FakeSource(), CatalogRepository(database)
    await SyncService(database, source).run()
    source.items = []
    await SyncService(database, source).run(full=True)
    assert all(not item.available for item in await catalog.items("1"))
    source.inventory = []
    await SyncService(database, source).run()
    assert not (await catalog.repo("1")).present
    async with database.sessions() as session:
        assert (await CatalogQueries(session).overview()).counts.repositories == 0


async def test_atomic_lease_claim_and_release(tmp_path: Path) -> None:
    first, second = (
        Database(tmp_path / "shared.sqlite"),
        Database(tmp_path / "shared.sqlite"),
    )
    try:
        await asyncio.gather(first.initialize(), second.initialize())
        async with SyncLease(first):
            with pytest.raises(RuntimeError, match="Another sync"):
                async with SyncLease(second):
                    pass
        async with SyncLease(second):
            pass
    finally:
        await first.close()
        await second.close()


async def test_expired_worker_is_fenced_out_of_commit(database: Database) -> None:
    source, catalog = FakeSource(), CatalogRepository(database)
    await SyncService(database, source).run()
    checkpoint = (await catalog.repo("1")).synced_at
    async with SyncLease(database) as old_lease:
        async with database.sessions.begin() as session:
            setting = await session.get(Setting, "sync_lock")
            setting.value = '{"token":"new-worker","expires":0}'
        with pytest.raises(RuntimeError, match="lease was lost"):
            await catalog.commit_repo("1", [], now(), True, None, old_lease)
    assert (await catalog.repo("1")).synced_at == checkpoint
    assert all(item.available for item in await catalog.items("1"))


async def test_owner_rename_preserves_identity(
    database: Database,
) -> None:
    source, catalog = FakeSource(), CatalogRepository(database)
    await SyncService(database, source).run()
    source.login = "other"
    source.inventory = [
        {**REPO, "full_name": "other/project", "owner": {"login": "other", "id": 77}}
    ]
    await SyncService(database, source).run()
    assert (await catalog.repo("1")).name == "other/project"
    async with database.sessions() as session:
        assert (await CatalogQueries(session).overview()).owner == "other"
        assert (await CatalogQueries(session).page(ItemFilters())).total == 3
        runs = (await session.scalars(select(SyncRun).order_by(SyncRun.id))).all()
        assert [run.status for run in runs] == ["complete", "complete"]


async def test_lease_cleans_up_after_heartbeat_failure(database: Database) -> None:
    async def fail() -> None:
        raise RuntimeError("Heartbeat failed")

    with pytest.raises(RuntimeError, match="Heartbeat failed"):
        async with SyncLease(database) as lease:
            lease.task.cancel()
            try:
                await lease.task
            except asyncio.CancelledError:
                pass
            lease.task = asyncio.create_task(fail())
            await asyncio.sleep(0)
    async with SyncLease(database):
        pass


async def test_incremental_reuses_closed_pr_details_and_preserves_merged_state(
    database: Database,
) -> None:
    source, catalog = FakeSource(), CatalogRepository(database)
    await SyncService(database, source).run()
    source.pull_requests.clear()
    await SyncService(database, source).run()
    assert source.pull_requests == [("1", 2)]
    merged = next(item for item in await catalog.items("1") if item.number == 3)
    assert merged.state == State.MERGED
    source.items[-1]["title"] = "Edited closed pull request"
    source.pull_requests.clear()
    await SyncService(database, source).run()
    assert set(source.pull_requests) == {("1", 2), ("1", 3)}
    source.pull_requests.clear()
    await SyncService(database, source).run(full=True)
    assert set(source.pull_requests) == {("1", 2), ("1", 3)}


async def test_open_pr_refreshes_head_without_an_issue_update(
    database: Database,
) -> None:
    source, catalog = FakeSource(), CatalogRepository(database)
    await SyncService(database, source).run()
    source.items = []
    source.head_sha = source.signal_head = "new-head"
    await SyncService(database, source).run()
    item = next(item for item in await catalog.items("1") if item.number == 2)
    assert item.pr["head"]["sha"] == "new-head"
    assert item.signals["commits"]["nodes"][0]["commit"]["oid"] == "new-head"


async def test_incremental_reads_only_incoming_items_and_open_prs(
    database: Database,
) -> None:
    source = FakeSource()
    await SyncService(database, source).run()
    source.items = []
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("SELECT items.id,"):
            statements.append(statement)

    event.listen(database.engine.sync_engine, "before_cursor_execute", capture)
    try:
        await SyncService(database, source).run()
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", capture)
    assert len(statements) == 2
    assert "items.kind =" in statements[0]
    assert "items.state =" in statements[0]
    assert "items.id IN" in statements[1]


class ParallelSource(FakeSource):
    def __init__(self) -> None:
        super().__init__()
        self.inventory = [
            {**REPO, "id": number, "full_name": f"owner/project{number}"}
            for number in range(1, 7)
        ]
        self.active = self.peak = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.fail_repo: str | None = None
        self.crash = False

    async def issues(self, repo, _since):
        self.active += 1
        self.peak = max(self.peak, self.active)
        if self.active == 2:
            self.started.set()
        try:
            await self.release.wait()
            if self.crash:
                raise RuntimeError("Unexpected source failure")
            if repo.id == self.fail_repo:
                raise GitHubError("Repository unavailable")
            raw = {**issue(), "node_id": f"I_repo{repo.id}"}
            yield [ItemSnapshot.from_github(raw, repo.id)]
        finally:
            self.active -= 1


async def test_repositories_ingest_in_parallel_with_isolated_failures(
    database: Database,
) -> None:
    source = ParallelSource()
    source.fail_repo = "2"
    task = asyncio.create_task(SyncService(database, source, concurrency=2).run())
    try:
        await asyncio.wait_for(source.started.wait(), timeout=2)
        assert source.peak == 2
        source.release.set()
        result = await task
    finally:
        source.release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert result.status == "partial"
    assert result.synced == 5
    assert source.peak == 2
    assert result.errors == ["owner/project2: Repository unavailable"]
    catalog = CatalogRepository(database)
    assert (await catalog.repo("1")).synced_at is not None
    assert (await catalog.repo("2")).synced_at is None
    assert (await catalog.repo("6")).synced_at is not None


async def test_parallel_workers_are_drained_before_failed_run_finishes(
    database: Database,
) -> None:
    source = ParallelSource()
    source.crash = True
    task = asyncio.create_task(SyncService(database, source, concurrency=2).run())
    try:
        await asyncio.wait_for(source.started.wait(), timeout=2)
        source.release.set()
        with pytest.raises(RuntimeError, match="Unexpected source failure"):
            await task
        assert source.active == 0
        async with database.sessions() as session:
            assert (await session.scalar(select(SyncRun.status))) == "failed"
        async with SyncLease(database):
            pass
    finally:
        source.release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_cancelling_sync_drains_workers_and_releases_lease(
    database: Database,
) -> None:
    source = ParallelSource()
    task = asyncio.create_task(SyncService(database, source, concurrency=2).run())
    try:
        await asyncio.wait_for(source.started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert source.active == 0
        async with SyncLease(database):
            pass
        async with database.sessions() as session:
            assert (await session.scalar(select(SyncRun.status))) == "failed"
    finally:
        source.release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_repo_filter_limits_parallel_ingestion(database: Database) -> None:
    source = ParallelSource()
    source.release.set()
    result = await SyncService(database, source).run(only={"owner/project3"})
    assert result.discovered == 1
    assert result.synced == 1
    catalog = CatalogRepository(database)
    with pytest.raises(ValueError, match="Unknown repository"):
        await catalog.repo("1")
    assert (await catalog.repo("3")).synced_at is not None
