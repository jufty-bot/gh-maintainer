"""SQL-backed filtering, aggregation and pagination with typed read models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, String, and_, case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from gh_maintainer.clock import now, timestamp
from gh_maintainer.models import (
    Classification,
    Comment,
    Item,
    Observation,
    Owner,
    Processing,
    Ranking,
    Repository,
    Summary,
    SyncRun,
    TriageDecision,
)
from gh_maintainer.policies import STALE_DAYS, present_item
from gh_maintainer.repositories import (
    item_snapshot,
    repo_snapshot,
    resolve_item,
    triage_snapshot,
)
from gh_maintainer.schemas import (
    AccountSnapshot,
    AnalysisView,
    ChangePage,
    CommentPage,
    CommentSnapshot,
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
                    or_(
                        Item.updated_at > TriageDecision.updated_at,
                        Item.revision > TriageDecision.source_revision,
                    ),
                ),
                "inbox",
            ),
            else_=func.coalesce(TriageDecision.disposition, "inbox"),
        )
        self.open = and_(
            Item.state == "open", Item.available.is_(True), Repository.present.is_(True)
        )
        self.disposition = disposition
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

    def _items_statement(
        self,
    ) -> Select[
        tuple[
            Item,
            Repository,
            TriageDecision,
            Processing,
            Summary,
            Classification,
            Ranking,
        ]
    ]:
        return (
            select(
                Item,
                Repository,
                TriageDecision,
                Processing,
                Summary,
                Classification,
                Ranking,
            )
            .join(Repository, Repository.id == Item.repo_id)
            .outerjoin(TriageDecision, TriageDecision.item_id == Item.id)
            .outerjoin(Processing, Processing.item_id == Item.id)
            .outerjoin(Summary, Summary.item_id == Item.id)
            .outerjoin(Classification, Classification.item_id == Item.id)
            .outerjoin(Ranking, Ranking.item_id == Item.id)
        )

    def _view(
        self,
        item: Item,
        repo: Repository,
        triage: TriageDecision | None,
        processed: Processing | None,
        summary: Summary | None,
        classification: Classification | None,
        ranking: Ranking | None,
    ) -> ItemView:
        view = present_item(
            item_snapshot(item), repo_snapshot(repo), triage_snapshot(triage), self.at
        )
        analysis = {}
        for key, record in (
            ("summary", summary),
            ("classification", classification),
            ("ranking", ranking),
        ):
            if record:
                value = AnalysisView.model_validate(record, from_attributes=True)
                analysis[key] = value.model_copy(
                    update={
                        "stale": record.source_revision != item.revision
                        or bool(repo.error)
                        or repo.comments_synced_at is None
                    }
                )
        return view.model_copy(
            update={
                **analysis,
                "processing_state": "unprocessed"
                if not processed
                else (
                    "processed" if processed.revision == item.revision else "changed"
                ),
                "processed_revision": processed.revision if processed else None,
                "processed_by": processed.actor if processed else None,
                "checked_at": processed.checked_at if processed else None,
                "comments_synced_at": repo.comments_synced_at,
            }
        )

    async def overview(self) -> Overview:
        owners = [
            AccountSnapshot.model_validate(record, from_attributes=True)
            for record in await self.session.scalars(
                select(Owner).order_by(Owner.login)
            )
        ]
        owner = owners[0].login if len(owners) == 1 else None
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
            owners=owners,
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
            statement = statement.where(
                func.lower(Repository.name) == filters.repo.lower()
            )
        if filters.queue:
            statement = statement.where(self.rules.queue)
        if filters.disposition:
            statement = statement.where(
                self.rules.disposition == filters.disposition.value
            )
        if filters.processing:
            conditions = {
                "unprocessed": Processing.item_id.is_(None),
                "changed": Processing.revision < Item.revision,
                "processed": Processing.revision == Item.revision,
                "pending": or_(
                    Processing.item_id.is_(None), Processing.revision < Item.revision
                ),
            }
            statement = statement.where(conditions[filters.processing])
        if filters.classification:
            labels = func.json_each(Classification.labels).table_valued("value")
            statement = statement.where(
                Classification.source_revision == Item.revision,
                Repository.error.is_(None),
                Repository.comments_synced_at.is_not(None),
                select(labels.c.value)
                .where(labels.c.value == filters.classification)
                .exists(),
            )
        total = await self.session.scalar(
            select(func.count()).select_from(statement.subquery())
        )
        ordering = [
            self.rules.score.desc(),
            Item.updated_at,
            Repository.name,
            Item.number,
        ]
        if filters.sort == "updated":
            ordering = [Item.updated_at.desc(), Repository.name, Item.number]
        elif filters.sort == "rank":
            current_rank = case(
                (
                    and_(
                        Ranking.source_revision == Item.revision,
                        Repository.error.is_(None),
                        Repository.comments_synced_at.is_not(None),
                    ),
                    Ranking.score,
                ),
                else_=None,
            )
            ordering = [
                current_rank.desc().nulls_last(),
                self.rules.score.desc(),
                Repository.name,
                Item.number,
            ]
        statement = (
            statement.order_by(*ordering).limit(filters.limit).offset(filters.offset)
        )
        return ItemPage(
            total=total or 0,
            items=[
                self._view(*row)
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
        return ItemDetail(item=self._view(*row), changes=changes)

    async def portfolio(self) -> Portfolio:
        overview = await self.overview()
        statement = self._items_statement().order_by(
            self.rules.score.desc(), Item.updated_at, Repository.name, Item.number
        )
        items = [
            self._view(*row) for row in (await self.session.execute(statement)).all()
        ]
        return Portfolio(**overview.model_dump(), items=items)

    async def comments(
        self,
        reference: str,
        limit: int = 100,
        offset: int = 0,
        include_unavailable: bool = False,
    ) -> CommentPage:
        ItemFilters(limit=limit, offset=offset)
        item = await resolve_item(self.session, reference)
        repo = await self.session.get(Repository, item.repo_id)
        statement = select(Comment).where(
            Comment.repo_id == item.repo_id, Comment.number == item.number
        )
        if not include_unavailable:
            statement = statement.where(Comment.available.is_(True))
        total = await self.session.scalar(
            select(func.count()).select_from(statement.subquery())
        )
        records = await self.session.scalars(
            statement.order_by(Comment.created_at, Comment.id)
            .limit(limit)
            .offset(offset)
        )
        return CommentPage(
            total=total or 0,
            comments_synced_at=repo.comments_synced_at if repo else None,
            coverage_error=repo.error if repo else "Repository unavailable",
            comments=[
                CommentSnapshot.model_validate(row, from_attributes=True)
                for row in records
            ],
        )

    async def changes(
        self,
        reference: str | None = None,
        since: datetime | None = None,
        since_check: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> ChangePage:
        ItemFilters(limit=limit, offset=offset)
        statement = select(Observation).outerjoin(
            Processing, Processing.item_id == Observation.item_id
        )
        if reference:
            item = await resolve_item(self.session, reference)
            statement = statement.where(Observation.item_id == item.id)
        if since:
            statement = statement.where(Observation.observed_at > since)
        if since_check:
            statement = statement.where(
                or_(
                    Processing.item_id.is_(None),
                    Observation.revision > Processing.revision,
                )
            )
        total = await self.session.scalar(
            select(func.count()).select_from(statement.subquery())
        )
        records = await self.session.scalars(
            statement.order_by(Observation.id.desc()).limit(limit).offset(offset)
        )
        return ChangePage(
            total=total or 0,
            changes=[
                ObservationView.model_validate(row, from_attributes=True)
                for row in records
            ],
        )

    async def show(self, reference: str) -> ItemDetail:
        item = await resolve_item(self.session, reference)
        detail = await self.detail(item.id)
        if detail is None:
            raise ValueError("Unknown item.")
        return detail
