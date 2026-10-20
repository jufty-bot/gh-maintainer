"""Review-relative activity, retained decisions, bounded reads and history gaps."""

import copy
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import delete

from gh_maintainer.clock import now
from gh_maintainer.database import Database
from gh_maintainer.models import Observation
from gh_maintainer.policies import describe_change
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.repositories import AnalysisService, TriageService
from gh_maintainer.schemas import (
    Disposition,
    ItemFilters,
    ItemSnapshot,
    ObservationView,
)
from gh_maintainer.sync import SyncService
from gh_maintainer.web import create_app
from tests.fakes import FakeSource
from tests.test_maintenance import comment

pytestmark = pytest.mark.anyio


async def prepare(database: Database) -> FakeSource:
    source = FakeSource()
    source.items[1]["user"] = {"login": "contributor"}
    await SyncService(database, source).run()
    await TriageService(database).decide(
        "I_2",
        Disposition.DONE,
        "Reviewed the retry logic",
        revision=1,
        finish_review=True,
    )
    return source


async def advance(database: Database, source: FakeSource, count: int = 1) -> None:
    source.head_sha = source.signal_head = "new-head"
    source.checks = "FAILURE"
    source.conversation = [
        comment(f"COMMENT_{index}", body="Updated timeout handling").model_copy(
            update={"author": "contributor"}
        )
        for index in range(count)
    ]
    await SyncService(database, source).run()


async def test_returning_item_explains_changes_beside_retained_done_decision(
    database: Database,
) -> None:
    source = await prepare(database)
    await advance(database, source)
    async with database.sessions() as session:
        query = CatalogQueries(session)
        detail = await query.show("owner/project#2")
        item = detail.item
        assert item.disposition == Disposition.INBOX
        assert item.saved_decision.disposition == Disposition.DONE
        assert item.saved_decision.note == "Reviewed the retry logic"
        follow = item.follow_up
        assert follow.baseline == "review" and follow.since_revision == 1
        assert follow.decision_at_review and not follow.history_gap
        assert set(follow.kinds) == {"pushed", "checks_changed", "author_replied"}
        assert detail.follow_up_changes.total == 3
        assert {entry.label for entry in detail.follow_up_changes.entries} == {
            "New code pushed",
            "Checks now failing",
            "Author replied",
        }
        checks = next(
            entry
            for entry in detail.follow_up_changes.entries
            if entry.kind == "signals_changed"
        )
        assert "success → failure" in checks.detail
        assert checks.url.endswith("/checks")
        page = await query.page(ItemFilters(repo="owner/project"))
        assert next(row for row in page.items if row.id == "I_2").follow_up == follow
    await AnalysisService(database).mark_processed("I_2", revision=2)
    async with database.sessions() as session:
        inspected = await CatalogQueries(session).show("I_2")
        assert not inspected.item.follow_up.kinds
        assert inspected.follow_up_changes.total == 0


async def test_decision_baseline_and_later_edits_are_distinct_from_review(
    database: Database,
) -> None:
    source = FakeSource()
    await SyncService(database, source).run()
    async with database.sessions() as session:
        before = await CatalogQueries(session).show("I_2")
        assert before.item.follow_up is None and before.follow_up_changes is None
    await TriageService(database).decide(
        "I_2", Disposition.DONE, "Earlier decision", revision=1
    )
    await advance(database, source)
    async with database.sessions() as session:
        fallback = await CatalogQueries(session).show("I_2")
        assert fallback.item.follow_up.baseline == "decision"
        assert fallback.follow_up_changes.total == 3
    await AnalysisService(database).mark_processed("I_2", revision=2)
    await TriageService(database).decide(
        "I_2",
        Disposition.WAITING,
        "A newer local note",
        revision=2,
        at=now() + timedelta(seconds=1),
    )
    async with database.sessions() as session:
        latest = (await CatalogQueries(session).show("I_2")).item
        assert latest.follow_up.decision_at_review is False
        assert latest.saved_decision.note == "A newer local note"


async def test_follow_up_paginates_beyond_preview_with_fixed_revision_window(
    database: Database,
) -> None:
    source = await prepare(database)
    await advance(database, source, 61)
    async with database.sessions() as session:
        detail = await CatalogQueries(session).show("I_2")
        assert len(detail.changes) == 50
        assert len(detail.follow_up_changes.entries) == 20
        assert detail.follow_up_changes.total == 63
        assert "pushed" in detail.item.follow_up.kinds
    source.checks = "SUCCESS"
    await SyncService(
        database, source
    ).run()  # Revision 3 must stay outside revision-2 pages.
    app = create_app("unused", database)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        entries = []
        for offset in (0, 20, 40, 60):
            response = await client.get(
                f"/api/items/I_2/follow-up?revision=1&through_revision=2&offset={offset}"
            )
            assert response.status_code == 200
            page = response.json()
            assert page["total"] == 63
            entries.extend(page["entries"])
        assert len({entry["id"] for entry in entries}) == 63
        assert any(entry["label"] == "New code pushed" for entry in entries)
        for suffix in (
            "revision=0",
            "revision=2&through_revision=1",
            "revision=1&limit=501",
            "revision=1&offset=-1",
        ):
            assert (
                await client.get(f"/api/items/I_2/follow-up?{suffix}")
            ).status_code == 422
        assert (
            await client.get("/api/items/missing/follow-up?revision=1")
        ).status_code == 404


async def test_pruned_or_legacy_history_is_not_reported_as_no_changes(
    database: Database,
) -> None:
    source = await prepare(database)
    await advance(database, source)
    async with database.sessions.begin() as session:
        await session.execute(
            delete(Observation).where(
                Observation.item_id == "I_2", Observation.revision > 1
            )
        )
        session.add(
            Observation(
                item_id="I_2",
                observed_at=now(),
                revision=None,
                kind="updated",
                before_json=None,
                after_json={},
            )
        )
    async with database.sessions() as session:
        detail = await CatalogQueries(session).show("I_2")
        assert detail.item.processing_state == "changed"
        assert detail.item.follow_up.history_gap
        assert not detail.item.follow_up.kinds
        assert detail.follow_up_changes.total == 0


async def test_unknown_check_head_and_review_only_signals_do_not_claim_new_failures(
    database: Database,
) -> None:
    source = await prepare(database)
    source.signal_head = "another-head"
    source.checks = "FAILURE"
    await SyncService(database, source).run()
    async with database.sessions() as session:
        query = CatalogQueries(session)
        detail = await query.show("I_2")
        checks = next(
            entry
            for entry in detail.follow_up_changes.entries
            if entry.kind == "signals_changed"
        )
        assert checks.label == "Checks changed"
        assert "unknown" in checks.detail and "failure" not in checks.detail
        item = await query.show("I_2")
        before = item.item.model_dump(mode="json")
        after = copy.deepcopy(before)
        after["signals"]["reviewDecision"] = "APPROVED"
        described = describe_change(
            ObservationView(
                id=999,
                item_id="I_2",
                observed_at=now(),
                before_json=before,
                after_json=after,
                kind="signals_changed",
                revision=3,
            ),
            ItemSnapshot.model_validate(
                item.item.model_dump(include=set(ItemSnapshot.model_fields))
            ),
        )
        assert described.label == "PR status changed"
        assert "Review:" in described.detail


async def test_excerpts_are_bounded_and_untrusted_text_remains_data(
    database: Database,
) -> None:
    source = await prepare(database)
    await advance(database, source)
    source.conversation[0] = source.conversation[0].model_copy(
        update={"body": "<script>unsafe()</script> " + "x" * 1000}
    )
    await SyncService(database, source).run()
    async with database.sessions() as session:
        detail = await CatalogQueries(session).show("I_2")
        edited = next(
            entry
            for entry in detail.follow_up_changes.entries
            if entry.kind == "comment_edited"
        )
        assert len(edited.detail) < 280
        assert "<script>" in edited.detail and edited.detail.endswith("…")
