"""Focused persistence operations with sessions scoped to each transaction."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from types import TracebackType

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gh_maintainer.clock import now
from gh_maintainer.database import Database
from gh_maintainer.models import (
    Item,
    Observation,
    Repository,
    Setting,
    SyncRun,
    TriageDecision,
)
from gh_maintainer.schemas import (
    AccountSnapshot,
    Disposition,
    ItemSnapshot,
    RepositorySnapshot,
    RepositoryStatus,
    TriageSnapshot,
)

LEASE_SECONDS = 300


def item_snapshot(record: Item) -> ItemSnapshot:
    return ItemSnapshot.model_validate(record, from_attributes=True)


def repo_snapshot(record: Repository) -> RepositoryStatus:
    return RepositoryStatus.model_validate(record, from_attributes=True)


def triage_snapshot(record: TriageDecision | None) -> TriageSnapshot:
    return (
        TriageSnapshot.model_validate(record, from_attributes=True)
        if record
        else TriageSnapshot()
    )


class SyncLease:
    """Atomic claim and heartbeat; every catalog commit fences out expired workers."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.token = str(uuid.uuid4())
        self.task: asyncio.Task[None] | None = None

    def value(self) -> str:
        return json.dumps({"token": self.token, "expires": time.time() + LEASE_SECONDS})

    async def __aenter__(self) -> SyncLease:
        async with self.database.sessions.begin() as session:
            statement = insert(Setting).values(key="sync_lock", value=self.value())
            claim = statement.on_conflict_do_update(
                index_elements=[Setting.key],
                set_={"value": statement.excluded.value},
                where=func.json_extract(Setting.value, "$.expires") <= time.time(),
            ).returning(Setting.key)
            if (await session.execute(claim)).scalar_one_or_none() is None:
                raise RuntimeError(
                    "Another sync is running; retry after its lease expires."
                )
        self.task = asyncio.create_task(self._heartbeat())
        return self

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(30)
            async with self.database.sessions.begin() as session:
                await self.guard(session)

    async def guard(self, session: AsyncSession) -> None:
        statement = (
            update(Setting)
            .where(
                Setting.key == "sync_lock",
                func.json_extract(Setting.value, "$.token") == self.token,
            )
            .values(value=self.value())
            .returning(Setting.key)
        )
        if (await session.execute(statement)).scalar_one_or_none() is None:
            raise RuntimeError("Sync lease was lost.")

    async def __aexit__(
        self,
        _kind: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        try:
            if self.task:
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    if _error is None:
                        raise
        finally:
            async with self.database.sessions.begin() as session:
                await session.execute(
                    delete(Setting).where(
                        Setting.key == "sync_lock",
                        func.json_extract(Setting.value, "$.token") == self.token,
                    )
                )


class CatalogRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def discover(
        self,
        owner: AccountSnapshot,
        repos: list[RepositorySnapshot],
        lease: SyncLease | None = None,
    ) -> None:
        async with self.database.sessions.begin() as session:
            if lease:
                await lease.guard(session)
            pairs = await session.execute(
                select(Setting.key, Setting.value).where(
                    Setting.key.in_(["owner", "owner_id"])
                )
            )
            settings = dict(pairs.tuples().all())
            current, current_id = settings.get("owner"), settings.get("owner_id")
            same_id = current_id == owner.id
            if (current_id and not same_id) or (
                current and current.lower() != owner.login.lower() and not same_id
            ):
                raise ValueError(
                    "This database belongs to a different owner; use another --db."
                )
            for key, value in (("owner", owner.login), ("owner_id", owner.id)):
                statement = insert(Setting).values(key=key, value=value)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[Setting.key],
                        set_={"value": statement.excluded.value},
                    )
                )
            await session.execute(update(Repository).values(present=False))
            for repo in repos:
                statement = insert(Repository).values(**repo.model_dump(), present=True)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[Repository.id],
                        set_={
                            name: getattr(statement.excluded, name)
                            for name in ("name", "url", "archived", "raw", "present")
                        },
                    )
                )

    async def repo(self, repo_id: str) -> RepositoryStatus:
        async with self.database.sessions() as session:
            record = await session.get(Repository, repo_id)
            if record is None:
                raise ValueError("Unknown repository ID.")
            return repo_snapshot(record)

    async def items(self, repo_id: str) -> list[ItemSnapshot]:
        async with self.database.sessions() as session:
            records = await session.scalars(select(Item).where(Item.repo_id == repo_id))
            return [item_snapshot(record) for record in records]

    @staticmethod
    def _observe(
        session: AsyncSession,
        before: ItemSnapshot | None,
        after: ItemSnapshot,
        at: datetime,
    ) -> None:
        before_data = before.model_dump(mode="json") if before else None
        after_data = after.model_dump(mode="json")
        # Successful polling of identical signals changes freshness, not source history.
        before_comparable = {
            key: value
            for key, value in (before_data or {}).items()
            if key != "signals_at"
        }
        after_comparable = {
            key: value for key, value in after_data.items() if key != "signals_at"
        }
        if before is None or before_comparable != after_comparable:
            session.add(
                Observation(
                    item_id=after.id,
                    observed_at=at,
                    before_json=before_data,
                    after_json=after_data,
                )
            )

    async def commit_repo(
        self,
        repo_id: str,
        snapshots: list[ItemSnapshot],
        started_at: datetime,
        full: bool,
        signals_error: str | None,
        lease: SyncLease | None = None,
    ) -> None:
        async with self.database.sessions.begin() as session:
            if lease:
                await lease.guard(session)
            repo = await session.get(Repository, repo_id)
            if repo is None:
                raise ValueError("Unknown repository ID.")
            records = {
                record.id: record
                for record in await session.scalars(
                    select(Item).where(Item.repo_id == repo_id)
                )
            }
            seen = set()
            for snapshot in snapshots:
                if snapshot.repo_id != repo_id:
                    raise ValueError("Item belongs to a different repository.")
                seen.add(snapshot.id)
                record = records.get(snapshot.id) or await session.get(
                    Item, snapshot.id
                )
                self._observe(
                    session,
                    item_snapshot(record) if record else None,
                    snapshot,
                    started_at,
                )
                if record is None:
                    session.add(Item(**snapshot.model_dump()))
                else:
                    for key, value in snapshot.model_dump().items():
                        setattr(record, key, value)
            if full:
                for item_id, record in records.items():
                    if item_id not in seen and record.available:
                        before = item_snapshot(record)
                        record.available = False
                        self._observe(
                            session, before, item_snapshot(record), started_at
                        )
            repo.synced_at, repo.error = started_at, signals_error
            if full:
                repo.reconciled_at = started_at
            if signals_error is None:
                repo.signals_at = started_at

    async def fail_repo(self, repo_id: str, error: str, lease: SyncLease) -> None:
        async with self.database.sessions.begin() as session:
            await lease.guard(session)
            await session.execute(
                update(Repository).where(Repository.id == repo_id).values(error=error)
            )


class SyncJournal:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def start(self, lease: SyncLease) -> int:
        async with self.database.sessions.begin() as session:
            await lease.guard(session)
            await session.execute(
                update(SyncRun)
                .where(SyncRun.status == "running")
                .values(status="interrupted", finished_at=now())
            )
            record = SyncRun(started_at=now(), status="running", source_calls=0)
            session.add(record)
            await session.flush()
            return record.id

    async def finish(
        self, run_id: int, status: str, calls: int, error: str | None, lease: SyncLease
    ) -> None:
        async with self.database.sessions.begin() as session:
            await lease.guard(session)
            await session.execute(
                update(SyncRun)
                .where(SyncRun.id == run_id)
                .values(
                    status=status, finished_at=now(), source_calls=calls, error=error
                )
            )


class TriageService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def decide(
        self,
        item_id: str,
        disposition: Disposition,
        note: str = "",
        until: datetime | None = None,
        at: datetime | None = None,
    ) -> None:
        at = at or now()
        if disposition == Disposition.SNOOZED:
            if until is None or until.tzinfo is None or until <= at:
                raise ValueError("Snoozing requires a future timezone-aware deadline.")
        else:
            until = None
        async with self.database.sessions.begin() as session:
            if await session.get(Item, item_id) is None:
                raise ValueError("Unknown item ID.")
            statement = insert(TriageDecision).values(
                item_id=item_id,
                disposition=disposition.value,
                note=note,
                snoozed_until=until,
                updated_at=at,
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[TriageDecision.item_id],
                    set_={
                        name: getattr(statement.excluded, name)
                        for name in (
                            "disposition",
                            "note",
                            "snoozed_until",
                            "updated_at",
                        )
                    },
                )
            )
