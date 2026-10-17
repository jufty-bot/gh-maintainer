"""Typed query and triage parity, SQL pagination, and the read-only async API."""

import copy
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import event, select

from gh_maintainer.clock import now, parse_date
from gh_maintainer.database import Database
from gh_maintainer.demo import seed_demo
from gh_maintainer.models import Item, Repository, TriageDecision
from gh_maintainer.queries import CatalogQueries, QueueExpressions
from gh_maintainer.report import markdown
from gh_maintainer.repositories import CatalogRepository, TriageService
from gh_maintainer.schemas import Disposition, ItemFilters, Kind
from gh_maintainer.web import create_app

pytestmark = pytest.mark.anyio


async def test_triage_is_persistent_and_counts_ignore_snoozes(
    database: Database,
) -> None:
    await seed_demo(database)
    at = now()
    async with database.sessions() as session:
        data = await CatalogQueries(session, at).portfolio()
    target = next(item for item in data.items if item.kind == Kind.ISSUE)
    await TriageService(database).decide(
        target.id,
        Disposition.SNOOZED,
        "Waiting for reproduction",
        until=at + timedelta(days=2),
        at=at,
    )
    async with database.sessions() as session:
        before = await CatalogQueries(session, at + timedelta(days=1)).portfolio()
        after = await CatalogQueries(session, at + timedelta(days=3)).portfolio()
    assert not next(item for item in before.items if item.id == target.id).actionable
    assert before.counts.open_issues == data.counts.open_issues
    assert (
        next(item for item in after.items if item.id == target.id).disposition
        == Disposition.INBOX
    )
    assert "Maintenance report" in markdown(after)


async def test_done_returns_to_inbox_after_activity(database: Database) -> None:
    await seed_demo(database)
    catalog, at = CatalogRepository(database), now()
    target = next(item for item in await catalog.items("1") if item.kind == Kind.ISSUE)
    await TriageService(database).decide(
        target.id, Disposition.DONE, "Resolved locally", at=at
    )
    updated = target.model_copy(update={"updated_at": at + timedelta(days=1)})
    await catalog.commit_repo("1", [updated], at + timedelta(days=1), False, None)
    async with database.sessions() as session:
        detail = await CatalogQueries(session, at + timedelta(days=2)).detail(target.id)
        assert detail.item.disposition == Disposition.INBOX
        assert detail.item.note == "Resolved locally"
        queue = await CatalogQueries(session, at + timedelta(days=2)).page(
            ItemFilters(queue=True)
        )
        assert target.id in {item.id for item in queue.items}


async def test_database_transaction_rolls_back_all_changes(database: Database) -> None:
    await seed_demo(database)
    catalog = CatalogRepository(database)
    before = (await catalog.repo("1")).synced_at
    first, second = (await catalog.items("1"))[:2]
    first = first.model_copy(update={"title": "Should roll back"})
    bad = second.model_copy(update={"repo_id": "missing"})
    with pytest.raises(ValueError, match="different repository"):
        await catalog.commit_repo("1", [first, bad], now(), False, None)
    assert (await catalog.repo("1")).synced_at == before
    assert all(item.title != "Should roll back" for item in await catalog.items("1"))


async def test_api_filters_pagination_and_no_write_routes(database: Database) -> None:
    await seed_demo(database)
    app = create_app("unused", database=database)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/static/app.js")).status_code == 200
        assert (await client.get("/api/overview")).json()["counts"]["repositories"] == 3
        assert "items" not in (await client.get("/api/overview")).json()
        page = (await client.get("/api/items?kind=pr&state=open&limit=2")).json()
        assert page["total"] == 3
        assert len(page["items"]) == 2
        assert (
            len(
                (
                    await client.get("/api/items?kind=pr&state=open&limit=2&offset=2")
                ).json()["items"]
            )
            == 1
        )
        assert (await client.get("/api/items?state=merged")).json()["total"] == 1
        assert (await client.get("/api/items?q=HTTP")).json()["total"] == 1
        assert (await client.get("/api/items?q=%25")).json()["total"] == 0
        assert (await client.get("/api/items?limit=9999")).status_code == 422
        assert (await client.get("/api/items?kind=invalid")).status_code == 422
        assert (await client.get(f"/api/items/{page['items'][0]['id']}")).json()[
            "changes"
        ]
        assert (await client.get("/api/items/missing")).status_code == 404
        assert "Maintenance report" in (await client.get("/api/report")).text
        assert (await client.post("/api/items", json={})).status_code == 405


async def test_api_uses_sql_limit_and_count(database: Database) -> None:
    await seed_demo(database)
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement.upper())

    event.listen(database.engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with database.sessions() as session:
            page = await CatalogQueries(session).page(ItemFilters(limit=1, offset=1))
        assert len(page.items) == 1
        assert page.total == 6
        assert len(statements) == 2
        assert "COUNT(" in statements[0]
        assert "LIMIT" in statements[1] and "OFFSET" in statements[1]
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", capture)


async def test_sql_rank_and_queue_match_pure_policies(database: Database) -> None:
    await seed_demo(database)
    at = now() + timedelta(days=40)
    async with database.sessions.begin() as session:
        records = list(await session.scalars(select(Item)))
        # Exercise missing signals, old-head signals, drafts, conflicts and reviews.
        for index, record in enumerate(records):
            if record.kind != "pr":
                continue
            signals = copy.deepcopy(record.signals)
            if index % 3 == 0:
                record.signals = None
            elif index % 3 == 1:
                signals["mergeable"] = "CONFLICTING"
                record.signals = signals
            else:
                record.pr = {**record.pr, "head": {"sha": "different"}, "draft": True}
                signals["commits"]["nodes"][0]["commit"]["oid"] = "previous"
                record.signals = signals
    async with database.sessions() as session:
        data = await CatalogQueries(session, at).portfolio()
        rules = QueueExpressions(at)
        # The SQL expression uses the same joined entities as the query layer.
        scores = dict(
            (
                await session.execute(
                    select(Item.id, rules.score)
                    .join(Repository)
                    .outerjoin(TriageDecision, TriageDecision.item_id == Item.id)
                )
            ).all()
        )
        queue = await CatalogQueries(session, at).page(ItemFilters(queue=True))
        assert all(scores[item.id] == item.score for item in data.items)
        assert {item.id for item in queue.items} == {
            item.id for item in data.items if item.actionable
        }
        assert data.counts.attention == sum(
            item.actionable and bool(item.reasons) for item in data.items
        )


async def test_snooze_requires_a_future_deadline(database: Database) -> None:
    await seed_demo(database)
    with pytest.raises(ValueError, match="future"):
        await TriageService(database).decide(
            "DEMO_1_39", Disposition.SNOOZED, until=parse_date("2020-01-01T00:00:00Z")
        )
