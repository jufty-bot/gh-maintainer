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
    Classification,
    Comment,
    Item,
    Observation,
    Owner,
    Processing,
    Ranking,
    Repository,
    RepositoryOwner,
    Setting,
    Summary,
    SyncRun,
    TriageDecision,
)
from gh_maintainer.schemas import (
    AccountSnapshot,
    AnalysisInput,
    AnalysisView,
    CommentSnapshot,
    Disposition,
    ItemSnapshot,
    JsonObject,
    ProcessingView,
    RepositoryPayload,
    RepositorySnapshot,
    RepositoryStatus,
    TriageSnapshot,
    repository_name,
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


async def resolve_item(session: AsyncSession, reference: str) -> Item:
    if "#" in reference:
        repo, _, number = reference.rpartition("#")
        name = repository_name(repo)
        if not number.isdigit() or int(number) < 1:
            raise ValueError("Expected owner/repository#number or an issue node ID.")
        item = await session.scalar(
            select(Item)
            .join(Repository)
            .where(func.lower(Repository.name) == name, Item.number == int(number))
        )
    else:
        item = await session.get(Item, reference)
    if item is None:
        raise ValueError("Unknown item; sync its repository first.")
    return item


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
        owner: AccountSnapshot | None,
        repos: list[RepositorySnapshot],
        lease: SyncLease | None = None,
        *,
        public_only: bool = False,
    ) -> None:
        """Reconcile only a complete owner inventory; explicit lists only upsert."""
        async with self.database.sessions.begin() as session:
            if lease:
                await lease.guard(session)
            owners = {owner.id: owner} if owner else {}
            memberships: dict[str, str] = {}
            for repo in repos:
                payload = RepositoryPayload.model_validate(repo.raw)
                source_owner = payload.owner
                if owner and source_owner.id != owner.id:
                    raise ValueError(
                        "Repository does not belong to the selected owner."
                    )
                memberships[repo.id] = source_owner.id
                owners.setdefault(
                    source_owner.id,
                    AccountSnapshot(
                        id=source_owner.id,
                        login=source_owner.login,
                        kind="Organization"
                        if source_owner.type == "Organization"
                        else "User",
                    ),
                )
            for account in owners.values():
                statement = insert(Owner).values(**account.model_dump())
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[Owner.id],
                        set_={
                            "login": statement.excluded.login,
                            "kind": statement.excluded.kind,
                        },
                    )
                )
            if owner:
                reconcile = update(Repository).where(
                    Repository.id.in_(
                        select(RepositoryOwner.repo_id).where(
                            RepositoryOwner.owner_id == owner.id
                        )
                    )
                )
                if public_only:
                    # Public listings cannot reconcile previously indexed private repos.
                    reconcile = reconcile.where(
                        func.json_extract(Repository.raw, "$.private") == 0
                    )
                await session.execute(reconcile.values(present=False))
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
                membership = insert(RepositoryOwner).values(
                    repo_id=repo.id, owner_id=memberships[repo.id]
                )
                await session.execute(
                    membership.on_conflict_do_update(
                        index_elements=[RepositoryOwner.repo_id],
                        set_={"owner_id": membership.excluded.owner_id},
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
    def _source_data(snapshot: ItemSnapshot) -> JsonObject:
        return snapshot.model_dump(mode="json", exclude={"signals_at", "revision"})

    @staticmethod
    def _observe(
        session: AsyncSession,
        before: ItemSnapshot | None,
        after: ItemSnapshot,
        at: datetime,
    ) -> None:
        kinds = []
        if before is None:
            kinds.append("indexed")
        else:
            if before.state != after.state:
                kinds.append("reopened" if after.state == "open" else after.state.value)
            old_head = (before.pr or {}).get("head")
            new_head = (after.pr or {}).get("head")
            if (
                isinstance(old_head, dict)
                and isinstance(new_head, dict)
                and old_head.get("sha") != new_head.get("sha")
            ):
                kinds.append("pushed")
            if before.available != after.available:
                kinds.append("available" if after.available else "unavailable")
            if before.signals != after.signals:
                kinds.append("signals_changed")
        for kind in kinds or ["updated"]:
            session.add(
                Observation(
                    item_id=after.id,
                    observed_at=at,
                    kind=kind,
                    revision=after.revision,
                    before_json=before.model_dump(mode="json") if before else None,
                    after_json=after.model_dump(mode="json"),
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
        *,
        comments: list[CommentSnapshot] | None = None,
        comments_full: bool = False,
        review_numbers: set[int] | None = None,
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
            seen: set[str] = set()
            touched: set[str] = set()
            for incoming_snapshot in snapshots:
                snapshot = incoming_snapshot
                if snapshot.repo_id != repo_id:
                    raise ValueError("Item belongs to a different repository.")
                seen.add(snapshot.id)
                record = records.get(snapshot.id) or await session.get(
                    Item, snapshot.id
                )
                before = item_snapshot(record) if record else None
                changed = before is None or self._source_data(
                    before
                ) != self._source_data(snapshot)
                revision = (before.revision + int(changed)) if before else 1
                snapshot = snapshot.model_copy(update={"revision": revision})
                if changed:
                    touched.add(snapshot.id)
                    self._observe(session, before, snapshot, started_at)
                if record is None:
                    record = Item(**snapshot.model_dump())
                    session.add(record)
                else:
                    for key, value in snapshot.model_dump().items():
                        setattr(record, key, value)
                records[snapshot.id] = record
            if full:
                for item_id, record in records.items():
                    if item_id not in seen and record.available:
                        before = item_snapshot(record)
                        record.available = False
                        if item_id not in touched:
                            record.revision += 1
                            touched.add(item_id)
                        self._observe(
                            session, before, item_snapshot(record), started_at
                        )
            # Parent rows must exist before inserting comments with their composite FK.
            await session.flush()
            if comments is not None:
                parents = {item.number: item for item in records.values()}
                existing = {
                    record.id: record
                    for record in await session.scalars(
                        select(Comment).where(Comment.repo_id == repo_id)
                    )
                }
                incoming = {comment.id: comment for comment in comments}
                for comment_id, comment_record in existing.items():
                    complete = comments_full or (
                        comment_record.kind == "review"
                        and comment_record.number in (review_numbers or set())
                    )
                    if (
                        complete
                        and comment_id not in incoming
                        and comment_record.available
                    ):
                        incoming[comment_id] = CommentSnapshot.model_validate(
                            comment_record, from_attributes=True
                        ).model_copy(update={"available": False})
                for comment in incoming.values():
                    if comment.repo_id != repo_id or comment.number not in parents:
                        raise ValueError("Comment belongs to an unknown parent item.")
                    old = existing.get(comment.id)
                    before_data = (
                        (
                            CommentSnapshot.model_validate(
                                old, from_attributes=True
                            ).model_dump(mode="json")
                        )
                        if old
                        else None
                    )
                    after_data = comment.model_dump(mode="json")
                    if before_data == after_data:
                        continue
                    parent = parents[comment.number]
                    if parent.id not in touched:
                        parent.revision += 1
                        touched.add(parent.id)
                    kind = "commented" if old is None else "comment_edited"
                    if comment.kind == "review":
                        kind = "reviewed"
                    if not comment.available:
                        kind = "comment_unavailable"
                    session.add(
                        Observation(
                            item_id=parent.id,
                            revision=parent.revision,
                            kind=kind,
                            observed_at=started_at,
                            before_json=before_data,
                            after_json=after_data,
                        )
                    )
                    if old is None:
                        session.add(Comment(**comment.model_dump()))
                    else:
                        for key, value in comment.model_dump().items():
                            setattr(old, key, value)
                repo.comments_synced_at = started_at
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

    async def fail_selection(self, name: str, error: str, lease: SyncLease) -> None:
        async with self.database.sessions.begin() as session:
            await lease.guard(session)
            await session.execute(
                update(Repository)
                .where(func.lower(Repository.name) == name.lower())
                .values(error=error)
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
        revision: int | None = None,
    ) -> None:
        at = at or now()
        if disposition == Disposition.SNOOZED:
            if until is None or until.tzinfo is None or until <= at:
                raise ValueError("Snoozing requires a future timezone-aware deadline.")
        else:
            until = None
        async with self.database.sessions.begin() as session:
            item = await resolve_item(session, item_id)
            if revision is not None and item.revision != revision:
                raise ValueError("Item changed; read it again before saving triage.")
            statement = insert(TriageDecision).values(
                item_id=item.id,
                source_revision=item.revision,
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
                            "source_revision",
                            "disposition",
                            "note",
                            "snoozed_until",
                            "updated_at",
                        )
                    },
                )
            )


class AnalysisService:
    """Store caller-supplied analysis and explicit processing acknowledgements."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def save(
        self,
        reference: str,
        kind: str,
        data: AnalysisInput,
        revision: int | None = None,
    ) -> AnalysisView:
        models: dict[
            str, tuple[type[Summary] | type[Classification] | type[Ranking], str]
        ] = {
            "summary": (Summary, "text"),
            "classification": (Classification, "labels"),
            "ranking": (Ranking, "score"),
        }
        model, field = models[kind]
        value = getattr(data, field)
        if value is None or (field == "text" and not value.strip()):
            raise ValueError("Analysis requires a nonempty value.")
        if field == "labels":
            value = list(
                dict.fromkeys(label.strip() for label in value if label.strip())
            )
            if not value:
                raise ValueError("Classification requires at least one nonempty label.")
        at = now()
        async with self.database.sessions.begin() as session:
            item = await resolve_item(session, reference)
            if revision is not None and revision != item.revision:
                raise ValueError("Item changed; read it again before saving analysis.")
            values = {
                "item_id": item.id,
                "source_revision": item.revision,
                "producer": data.producer,
                "reason": data.reason,
                "updated_at": at,
                field: value,
            }
            statement = insert(model).values(**values)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[model.item_id],
                    set_={
                        key: getattr(statement.excluded, key)
                        for key in values
                        if key != "item_id"
                    },
                )
            )
            return AnalysisView(
                **data.model_copy(update={field: value}).model_dump(),
                source_revision=item.revision,
                updated_at=at,
            )

    async def mark_processed(
        self,
        reference: str,
        revision: int | None = None,
        actor: str = "manual",
    ) -> ProcessingView:
        async with self.database.sessions.begin() as session:
            item = await resolve_item(session, reference)
            if revision is not None and revision != item.revision:
                raise ValueError(
                    "Item changed; read it again before marking it processed."
                )
            repo = await session.get(Repository, item.repo_id)
            if repo is None or repo.error or repo.comments_synced_at is None:
                raise ValueError(
                    "Sync complete item and comment coverage before marking processed."
                )
            result = ProcessingView(
                item_id=item.id, revision=item.revision, checked_at=now(), actor=actor
            )
            statement = insert(Processing).values(**result.model_dump())
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[Processing.item_id],
                    set_={
                        key: getattr(statement.excluded, key)
                        for key in result.model_dump()
                        if key != "item_id"
                    },
                )
            )
            return result
