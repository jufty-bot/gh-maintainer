"""SQL-backed filtering, aggregation and pagination with typed read models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, String, and_, case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from gh_maintainer.clock import now, timestamp
from gh_maintainer.models import (
    Item,
    Observation,
    Repository,
    Setting,
    SyncRun,
    TriageDecision,
)
from gh_maintainer.policies import STALE_DAYS, present_item
from gh_maintainer.repositories import item_snapshot, repo_snapshot, triage_snapshot
from gh_maintainer.schemas import (
    Counts,
    ItemDetail,
    ItemFilters,
    ItemPage,
    ItemView,
    ObservationView,
    Overview,
    Portfolio,
    RepositoryView,
    RunView,
)


class QueueExpressions:
    """Query equivalents of the pure rules; parity is covered by policy tests."""

    def __init__(self, at: datetime) -> None:
        moment = literal(timestamp(at))
        extract = func.json_extract
        head = extract(Item.signals, "$.commits.nodes[0].commit.oid")
        stale = and_(
            Item.signals.is_not(None),
            or_(
                Repository.error.is_not(None),
                and_(
                    head.is_not(None),
                    or_(
                        extract(Item.pr, "$.head.sha").is_(None),
                        head != extract(Item.pr, "$.head.sha"),
                    ),
                ),
            ),
        )
        checks = case(
            (stale, "UNKNOWN"),
            else_=func.coalesce(
                extract(
                    Item.signals, "$.commits.nodes[0].commit.statusCheckRollup.state"
                ),
                "UNKNOWN",
            ),
        )
        review = case(
            (stale, "UNKNOWN"),
            else_=func.coalesce(extract(Item.signals, "$.reviewDecision"), "UNKNOWN"),
        )
        mergeable = case(
            (stale, "UNKNOWN"),
            else_=func.coalesce(extract(Item.signals, "$.mergeable"), "UNKNOWN"),
        )
        draft = func.coalesce(extract(Item.pr, "$.draft"), 0) == 1
        disposition = case(
            (
                and_(
                    TriageDecision.disposition == "snoozed",
                    func.julianday(TriageDecision.snoozed_until)
                    <= func.julianday(moment),
                ),
                "inbox",
            ),
            (
                and_(
                    TriageDecision.disposition == "done",
                    Item.updated_at > TriageDecision.updated_at,
                ),
                "inbox",
            ),
            else_=func.coalesce(TriageDecision.disposition, "inbox"),
        )
        self.open = and_(
            Item.state == "open", Item.available.is_(True), Repository.present.is_(True)
        )
        self.queue = and_(
            self.open,
            Repository.archived.is_(False),
            disposition.not_in(["snoozed", "done"]),
        )
        weights = [
            (checks.in_(["FAILURE", "ERROR"]), 80),
            (mergeable == "CONFLICTING", 70),
            (review == "CHANGES_REQUESTED", 60),
            (and_(Item.kind == "pr", ~draft, review == "REVIEW_REQUIRED"), 50),
            (
                and_(
                    Item.kind == "issue",
                    func.coalesce(
                        func.json_array_length(extract(Item.raw, "$.assignees")), 0
                    )
                    == 0,
                ),
                20,
            ),
            (
                func.julianday(moment) - func.julianday(Item.updated_at) >= STALE_DAYS,
                30,
            ),
        ]
        score: ColumnElement[int] = literal(0)
        for applies, weight in weights:
            score = score + case((applies, weight), else_=0)
        self.score = case((Item.state == "open", score), else_=0)
        self.attention = and_(
            self.queue,
            or_(self.score > 0, and_(Item.kind == "pr", checks == "UNKNOWN")),
        )


def count_if(condition: ColumnElement[bool]) -> ColumnElement[int]:
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


class CatalogQueries:
    def __init__(self, session: AsyncSession, at: datetime | None = None) -> None:
        self.session = session
        self.at = at or now()
        self.rules = QueueExpressions(self.at)

    def _items_statement(self) -> Select[tuple[Item, Repository, TriageDecision]]:
        return (
            select(Item, Repository, TriageDecision)
            .join(Repository)
            .outerjoin(TriageDecision, TriageDecision.item_id == Item.id)
        )

    def _view(
        self, item: Item, repo: Repository, triage: TriageDecision | None
    ) -> ItemView:
        return present_item(
            item_snapshot(item), repo_snapshot(repo), triage_snapshot(triage), self.at
        )

    async def overview(self) -> Overview:
        owner = await self.session.scalar(
            select(Setting.value).where(Setting.key == "owner")
        )
        run = await self.session.scalar(
            select(SyncRun).order_by(SyncRun.id.desc()).limit(1)
        )
        latest = (
            RunView.model_validate(
                {name: getattr(run, name) for name in RunView.model_fields}
            )
            if run
            else None
        )
        statement = (
            select(
                Repository,
                count_if(
                    and_(
                        Item.available.is_(True),
                        Item.state == "open",
                        Item.kind == "issue",
                    )
                ),
                count_if(
                    and_(
                        Item.available.is_(True),
                        Item.state == "open",
                        Item.kind == "pr",
                    )
                ),
                count_if(self.rules.attention),
            )
            .outerjoin(Item, Item.repo_id == Repository.id)
            .outerjoin(TriageDecision, TriageDecision.item_id == Item.id)
            .group_by(Repository.id)
            .order_by(Repository.name)
        )
        repos = [
            RepositoryView(
                **repo_snapshot(repo).model_dump(),
                open_issues=issues,
                open_prs=prs,
                attention=attention,
            )
            for repo, issues, prs, attention in (
                await self.session.execute(statement)
            ).all()
        ]
        item_counts = (
            await self.session.execute(
                select(
                    func.count(Item.id),
                    count_if(and_(self.rules.open, Item.kind == "issue")),
                    count_if(and_(self.rules.open, Item.kind == "pr")),
                    count_if(self.rules.attention),
                )
                .select_from(Item)
                .join(Repository)
                .outerjoin(TriageDecision, TriageDecision.item_id == Item.id)
            )
        ).one()
        counts = Counts(
            repositories=sum(repo.present for repo in repos),
            indexed_items=item_counts[0],
            open_issues=item_counts[1],
            open_prs=item_counts[2],
            attention=item_counts[3],
            incomplete_repositories=sum(
                repo.present and (bool(repo.error) or not repo.synced_at)
                for repo in repos
            ),
        )
        return Overview(
            owner=owner,
            generated_at=self.at,
            latest_run=latest,
            counts=counts,
            repos=repos,
        )

    async def page(self, filters: ItemFilters) -> ItemPage:
        statement = self._items_statement()
        if filters.q:
            haystack = (
                Item.title
                + " "
                + Repository.name
                + " "
                + cast(Item.number, String)
                + " "
                + func.coalesce(func.json_extract(Item.raw, "$.labels"), "")
            )
            statement = statement.where(
                func.lower(haystack).contains(filters.q.lower(), autoescape=True)
            )
        if filters.kind:
            statement = statement.where(Item.kind == filters.kind.value)
        if filters.state:
            statement = statement.where(Item.state == filters.state.value)
        if filters.repo:
            statement = statement.where(Repository.name == filters.repo)
        if filters.queue:
            statement = statement.where(self.rules.queue)
        total = await self.session.scalar(
            select(func.count()).select_from(statement.subquery())
        )
        statement = (
            statement.order_by(
                self.rules.score.desc(), Item.updated_at, Repository.name, Item.number
            )
            .limit(filters.limit)
            .offset(filters.offset)
        )
        return ItemPage(
            total=total or 0,
            items=[
                self._view(row[0], row[1], row[2])
                for row in (await self.session.execute(statement)).all()
            ],
        )

    async def detail(self, item_id: str) -> ItemDetail | None:
        row = (
            await self.session.execute(
                self._items_statement().where(Item.id == item_id)
            )
        ).one_or_none()
        if row is None:
            return None
        observations = await self.session.scalars(
            select(Observation)
            .where(Observation.item_id == item_id)
            .order_by(Observation.id.desc())
            .limit(50)
        )
        changes = [
            ObservationView.model_validate(
                {name: getattr(record, name) for name in ObservationView.model_fields}
            )
            for record in observations
        ]
        return ItemDetail(item=self._view(row[0], row[1], row[2]), changes=changes)

    async def portfolio(self) -> Portfolio:
        overview = await self.overview()
        statement = self._items_statement().order_by(
            self.rules.score.desc(), Item.updated_at, Repository.name, Item.number
        )
        items = [
            self._view(row[0], row[1], row[2])
            for row in (await self.session.execute(statement)).all()
        ]
        return Portfolio(**overview.model_dump(), items=items)
