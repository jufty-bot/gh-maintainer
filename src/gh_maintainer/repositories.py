"""Focused persistence operations with sessions scoped to each transaction."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from types import TracebackType

from sqlalchemy import CursorResult, delete, func, or_, select, update
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
    Kind,
    ProcessingView,
    RepositoryActivity,
    RepositoryPayload,
    RepositorySnapshot,
    RepositoryStatus,
    State,
    TriageSnapshot,
    repository_name,
    source_content,
)

LEASE_SECONDS = 300


def item_snapshot(record: Item) -> ItemSnapshot:
    return ItemSnapshot.model_validate(record, from_attributes=True)


def repo_snapshot(record: Repository) -> RepositoryStatus:
    return RepositoryStatus.model_validate(record, from_attributes=True).model_copy(
        update={
            "activity_stale": not record.present
            or record.activity_synced_at is None
            or bool(record.activity_error)
        }
    )


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
            if owners:
                statement = insert(Owner)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[Owner.id],
                        set_={
                            "login": statement.excluded.login,
                            "kind": statement.excluded.kind,
                        },
                        where=or_(
                            Owner.login != statement.excluded.login,
                            Owner.kind != statement.excluded.kind,
                        ),
                    ),
                    [account.model_dump() for account in owners.values()],
                )
            if owner:
                reconcile = select(Repository.id).where(
                    Repository.present.is_(True),
                    Repository.id.in_(
                        select(RepositoryOwner.repo_id).where(
                            RepositoryOwner.owner_id == owner.id
                        )
                    ),
                )
                if public_only:
                    # Public listings cannot reconcile previously indexed private repos.
                    reconcile = reconcile.where(
                        func.json_extract(Repository.raw, "$.private") == 0
                    )
                present = set(await session.scalars(reconcile))
                missing = list(present - memberships.keys())
                for offset in range(0, len(missing), 500):
                    await session.execute(
                        update(Repository)
                        .where(
                            Repository.id.in_(missing[offset : offset + 500]),
                            Repository.present.is_(True),
                        )
                        .values(present=False)
                    )
            existing: dict[str, Repository] = {}
            existing_memberships: dict[str, str] = {}
            repo_ids = list(memberships)
            for offset in range(0, len(repo_ids), 500):
                batch = repo_ids[offset : offset + 500]
                existing.update(
                    (record.id, record)
                    for record in await session.scalars(
                        select(Repository).where(Repository.id.in_(batch))
                    )
                )
                existing_memberships.update(
                    (repo_id, owner_id)
                    for repo_id, owner_id in await session.execute(
                        select(RepositoryOwner.repo_id, RepositoryOwner.owner_id).where(
                            RepositoryOwner.repo_id.in_(batch)
                        )
                    )
                )
            changed_repos = [
                {**repo.model_dump(), "present": True}
                for repo in repos
                if (old := existing.get(repo.id)) is None
                or not old.present
                or any(
                    getattr(old, key) != getattr(repo, key)
                    for key in ("name", "url", "archived", "raw")
                )
            ]
            if changed_repos:
                statement = insert(Repository)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[Repository.id],
                        set_={
                            name: getattr(statement.excluded, name)
                            for name in ("name", "url", "archived", "raw", "present")
                        },
                    ),
                    changed_repos,
                )
            changed_memberships = [
                {"repo_id": repo_id, "owner_id": owner_id}
                for repo_id, owner_id in memberships.items()
                if existing_memberships.get(repo_id) != owner_id
            ]
            if changed_memberships:
                membership = insert(RepositoryOwner)
                await session.execute(
                    membership.on_conflict_do_update(
                        index_elements=[RepositoryOwner.repo_id],
                        set_={"owner_id": membership.excluded.owner_id},
                    ),
                    changed_memberships,
                )

    async def repo(self, repo_id: str) -> RepositoryStatus:
        async with self.database.sessions() as session:
            record = await session.get(Repository, repo_id)
            if record is None:
                raise ValueError("Unknown repository ID.")
            return repo_snapshot(record)

    async def commit_activity(
        self,
        snapshots: dict[str, RepositoryActivity],
        errors: dict[str, str],
        observed_at: datetime,
        lease: SyncLease | None = None,
    ) -> None:
        """Activity coverage is independent of item/comment checkpoints."""
        async with self.database.sessions.begin() as session:
            if lease:
                await lease.guard(session)
            for record in await session.scalars(
                select(Repository).where(
                    Repository.id.in_(snapshots.keys() | errors.keys())
                )
            ):
                record.activity_error = errors.get(record.id)
                if record.id in snapshots:
                    payload = snapshots[record.id].model_dump(mode="json")
                    if record.activity != payload:
                        record.activity = payload
                    record.activity_synced_at = observed_at

    @staticmethod
    async def _items(
        session: AsyncSession,
        repo_id: str,
        ids: set[str] | None = None,
        include_prs: bool = False,
    ) -> dict[str, Item]:
        query = select(Item).where(Item.repo_id == repo_id)
        if ids is None:
            return {record.id: record for record in await session.scalars(query)}
        records: dict[str, Item] = {}
        if include_prs:
            records.update(
                (record.id, record)
                for record in await session.scalars(
                    query.where(
                        Item.kind == Kind.PR,
                        Item.available.is_(True),
                        Item.state == State.OPEN,
                    )
                )
            )
        # Stay below SQLite's variable limit; do not load incoming open PRs twice.
        item_ids = list(ids - records.keys())
        for offset in range(0, len(item_ids), 500):
            batch = item_ids[offset : offset + 500]
            records.update(
                (record.id, record)
                for record in await session.scalars(query.where(Item.id.in_(batch)))
            )
        return records

    async def items(
        self,
        repo_id: str,
        ids: set[str] | None = None,
        include_prs: bool = False,
    ) -> list[ItemSnapshot]:
        async with self.database.sessions() as session:
            records = await self._items(session, repo_id, ids, include_prs)
            return [item_snapshot(record) for record in records.values()]

    @staticmethod
    async def _parents(
        session: AsyncSession, repo_id: str, numbers: set[int]
    ) -> dict[str, Item]:
        records: dict[str, Item] = {}
        values = list(numbers)
        for offset in range(0, len(values), 500):
            records.update(
                (record.id, record)
                for record in await session.scalars(
                    select(Item).where(
                        Item.repo_id == repo_id,
                        Item.number.in_(values[offset : offset + 500]),
                    )
                )
            )
        return records

    async def parents(self, repo_id: str, numbers: set[int]) -> list[ItemSnapshot]:
        async with self.database.sessions() as session:
            records = await self._parents(session, repo_id, numbers)
            return [item_snapshot(record) for record in records.values()]

    async def pull_numbers(self, repo_id: str) -> list[int]:
        """Review coverage needs PR numbers, not historical raw payloads."""
        async with self.database.sessions() as session:
            return list(
                await session.scalars(
                    select(Item.number).where(
                        Item.repo_id == repo_id,
                        Item.kind == Kind.PR,
                        Item.available.is_(True),
                    )
                )
            )

    @staticmethod
    def _source_data(snapshot: ItemSnapshot) -> dict[str, object]:
        data = snapshot.model_dump(
            exclude={"raw", "pr", "signals", "signals_at", "revision"}
        )
        data.update(
            raw=source_content(snapshot.raw),
            pr=source_content(snapshot.pr) if snapshot.pr is not None else None,
            signals=snapshot.signals,
        )
        return data

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
        before_json = before.model_dump(mode="json") if before else None
        after_json = after.model_dump(mode="json")
        for kind in kinds or ["updated"]:
            session.add(
                Observation(
                    item_id=after.id,
                    observed_at=at,
                    kind=kind,
                    revision=after.revision,
                    before_json=before_json,
                    after_json=after_json,
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
            records = await self._items(
                session, repo_id, None if full else {item.id for item in snapshots}
            )
            seen: set[str] = set()
            touched: set[str] = set()
            for incoming_snapshot in snapshots:
                snapshot = incoming_snapshot
                if snapshot.repo_id != repo_id:
                    raise ValueError("Item belongs to a different repository.")
                seen.add(snapshot.id)
                record = records.get(snapshot.id)
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
                    record = Item(
                        **{
                            key: getattr(snapshot, key)
                            for key in ItemSnapshot.model_fields
                        }
                    )
                    session.add(record)
                else:
                    for key in ItemSnapshot.model_fields:
                        value = getattr(snapshot, key)
                        if getattr(record, key) != value:
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
                query = select(Comment).where(Comment.repo_id == repo_id)
                existing_comments: dict[str, Comment] = {}
                if comments_full:
                    existing_comments.update(
                        (record.id, record) for record in await session.scalars(query)
                    )
                else:
                    comment_ids = list({comment.id for comment in comments})
                    for offset in range(0, len(comment_ids), 500):
                        existing_comments.update(
                            (record.id, record)
                            for record in await session.scalars(
                                query.where(
                                    Comment.id.in_(comment_ids[offset : offset + 500])
                                )
                            )
                        )
                    numbers = list(review_numbers or ())
                    for offset in range(0, len(numbers), 500):
                        existing_comments.update(
                            (record.id, record)
                            for record in await session.scalars(
                                query.where(
                                    Comment.kind == "review",
                                    Comment.available.is_(True),
                                    Comment.number.in_(numbers[offset : offset + 500]),
                                )
                            )
                        )
                existing = existing_comments
                # Comment-only activity can touch a closed issue absent from the
                # incremental issue listing. Load those parents by their numbers.
                parent_numbers = {comment.number for comment in comments}
                parent_numbers.update(
                    comment.number
                    for comment in existing.values()
                    if comments_full
                    or (
                        comment.kind == "review"
                        and comment.number in (review_numbers or set())
                    )
                )
                known_numbers = {item.number for item in records.values()}
                records.update(
                    await self._parents(
                        session, repo_id, parent_numbers - known_numbers
                    )
                )
                parents = {item.number: item for item in records.values()}
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
                    meaningful = before_data is None or {
                        **before_data,
                        "raw": source_content(old.raw if old else {}),
                    } != {**after_data, "raw": source_content(comment.raw)}
                    parent = parents[comment.number]
                    if meaningful and parent.id not in touched:
                        parent.revision += 1
                        touched.add(parent.id)
                    kind = "commented" if old is None else "comment_edited"
                    if comment.kind == "review":
                        kind = "reviewed"
                    if not comment.available:
                        kind = "comment_unavailable"
                    if meaningful:
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
                .values(
                    error=error, activity_error=f"Repository selection failed: {error}"
                )
            )

    async def prune_observations(
        self,
        keep_per_item: int | None = 50,
        older_than: datetime | None = None,
        lease: SyncLease | None = None,
    ) -> int:
        async with self.database.sessions.begin() as session:
            if lease:
                await lease.guard(session)
            deleted = 0
            if older_than is not None:
                stmt_time = delete(Observation).where(
                    Observation.observed_at < older_than
                )
                res_time = await session.execute(stmt_time)
                if isinstance(res_time, CursorResult):
                    deleted += res_time.rowcount

            if keep_per_item is not None and keep_per_item >= 0:
                subq = (
                    select(
                        Observation.id,
                        func.row_number()
                        .over(
                            partition_by=Observation.item_id,
                            order_by=Observation.id.desc(),
                        )
                        .label("rn"),
                    )
                ).subquery()
                stmt_keep = delete(Observation).where(
                    Observation.id.in_(
                        select(subq.c.id).where(subq.c.rn > keep_per_item)
                    )
                )
                res_keep = await session.execute(stmt_keep)
                if isinstance(res_keep, CursorResult):
                    deleted += res_keep.rowcount

            return deleted


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
