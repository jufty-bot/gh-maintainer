"""Sync contract parity across the async storage and source boundaries."""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select

from gh_maintainer.clock import now
from gh_maintainer.database import Database
from gh_maintainer.models import Observation, Setting, SyncRun
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.repositories import CatalogRepository, SyncLease
from gh_maintainer.schemas import ItemFilters, State
from gh_maintainer.sync import OVERLAP, SyncService

from .fakes import REPO, FakeSource

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
