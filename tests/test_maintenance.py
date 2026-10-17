"""Offline maintenance workflows, conversation coverage, and processing freshness."""

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from gh_maintainer import cli
from gh_maintainer.clock import now
from gh_maintainer.database import Database
from gh_maintainer.models import Comment, Item, Repository
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.repositories import AnalysisService, CatalogRepository, TriageService
from gh_maintainer.schemas import (
    AnalysisInput,
    CommentSnapshot,
    Disposition,
    ItemFilters,
)
from gh_maintainer.sync import OVERLAP, SyncService

from .fakes import AT, FakeSource, issue

pytestmark = pytest.mark.anyio


def comment(
    identifier: str = "IC_1",
    number: int = 2,
    kind: str = "issue",
    body: str = "Please test this",
) -> CommentSnapshot:
    return CommentSnapshot.from_github(
        {
            "node_id": identifier,
            "body": body,
            "user": {"login": "reviewer"},
            "html_url": f"https://github.com/owner/project/pull/{number}#{identifier}",
            "created_at": AT,
            "updated_at": AT,
            "in_reply_to_id": 123 if kind == "inline" else None,
            "path": "src/example.py" if kind == "inline" else None,
        },
        "1",
        number,
        kind,
    )


async def test_processing_and_analysis_stale_on_comment_without_parent_update(
    database: Database,
) -> None:
    source = FakeSource()
    service = AnalysisService(database)
    await SyncService(database, source).run()
    target = "owner/project#2"
    await service.save(
        target, "summary", AnalysisInput(text="Needs review", producer="agent/v1"), 1
    )
    await service.save(
        target, "classification", AnalysisInput(labels=["bug", " bug ", "urgent"]), 1
    )
    await service.save(
        target, "ranking", AnalysisInput(score=80, reason="Customer impact"), 1
    )
    await TriageService(database).decide(target, Disposition.DONE, "Checked")
    await service.mark_processed(target, 1, "agent/v1")
    async with database.sessions() as session:
        query = CatalogQueries(session)
        assert (await query.show(target)).item.processing_state == "processed"
        assert (await query.changes(target, since_check=True)).total == 0
        assert (await query.page(ItemFilters(classification="bug"))).total == 1
    checkpoint = (await CatalogRepository(database).repo("1")).comments_synced_at
    source.items = []  # Parent issue timestamp did not advance.
    source.conversation = [comment()]
    await SyncService(database, source).run()
    assert source.comment_windows[-1] == checkpoint - OVERLAP
    async with database.sessions() as session:
        query = CatalogQueries(session)
        detail = await query.show(target)
        assert detail.item.updated_at.isoformat().startswith("2026-10-01")
        assert detail.item.revision == 2
        assert detail.item.processing_state == "changed"
        assert detail.item.disposition == Disposition.INBOX
        assert detail.item.note == "Checked"
        assert detail.item.summary.stale
        assert detail.item.classification.stale
        assert detail.item.ranking.stale
        changes = await query.changes(target, since_check=True)
        assert [change.kind for change in changes.changes] == ["commented"]
        assert (await query.page(ItemFilters(classification="bug"))).total == 0
        assert (await query.page(ItemFilters(processing="changed"))).items[
            0
        ].id == "I_2"
    with pytest.raises(ValueError, match="changed"):
        await service.mark_processed(target, 1)
    with pytest.raises(ValueError, match="changed"):
        await service.save(target, "summary", AnalysisInput(text="Old analysis"), 1)
    with pytest.raises(ValueError, match="changed"):
        await TriageService(database).decide(target, Disposition.PLANNED, revision=1)
    await service.mark_processed(target, 2)
    await SyncService(database, source).run()
    async with database.sessions() as session:
        query = CatalogQueries(session)
        assert (await query.show(target)).item.revision == 2
        assert (await query.changes(target, since_check=True)).total == 0


async def test_comments_edits_replies_reviews_and_reconciliation(
    database: Database,
) -> None:
    source = FakeSource()
    source.conversation = [
        comment(),
        comment("RC_2", kind="inline"),
        comment("RV_3", number=3, kind="review", body="Approved"),
    ]
    source.items.append(issue(4, state="closed"))
    source.conversation.append(comment("IC_4", number=4))
    await SyncService(database, source).run()
    async with database.sessions() as session:
        query = CatalogQueries(session)
        page = await query.comments("I_2", limit=1, offset=1)
        assert page.total == 2 and page.comments[0].id == "RC_2"
        assert page.comments[0].raw["in_reply_to_id"] == 123
        merged = await query.comments("I_3")
        assert merged.comments[0].body == "Approved"
        assert merged.comments_synced_at and merged.coverage_error is None
        assert (await query.comments("I_4")).comments[0].body == "Please test this"
    source.items = []
    source.conversation = [
        comment(body="Edited"),
        comment("RV_3", 3, "review", "Dismissed"),
    ]
    await SyncService(database, source).run()
    async with database.sessions() as session:
        assert (
            await CatalogQueries(session).comments("I_2")
        ).total == 2  # Incremental listing cannot infer deletion.
        assert "reviewed" in {
            event.kind
            for event in (await CatalogQueries(session).changes("I_3")).changes
        }
    source.items = [issue(), issue(2, True), issue(3, True, "closed")]
    await SyncService(database, source).run(full=True)
    async with database.sessions() as session:
        query = CatalogQueries(session)
        assert (await query.comments("I_2")).total == 1
        assert (await query.comments("I_2", include_unavailable=True)).total == 2
        assert "comment_unavailable" in {
            change.kind for change in (await query.changes("I_2")).changes
        }
        existing = (await session.get(Item, "I_2")).revision
    await SyncService(database, source).run(full=True)
    async with database.sessions() as session:
        assert (await session.get(Item, "I_2")).revision == existing


@pytest.mark.parametrize("failure", ["fail_comments", "fail_reviews"])
async def test_conversation_failure_keeps_repo_and_comment_checkpoints(
    database: Database,
    failure: str,
) -> None:
    source = FakeSource()
    await SyncService(database, source).run()
    catalog = CatalogRepository(database)
    before = await catalog.repo("1")
    source.conversation = [comment()]
    source.items[0] = {**source.items[0], "title": "Should not commit"}
    setattr(source, failure, True)
    result = await SyncService(database, source).run()
    assert result.status == "partial"
    after = await catalog.repo("1")
    assert (after.synced_at, after.comments_synced_at) == (
        before.synced_at,
        before.comments_synced_at,
    )
    assert (await catalog.items("1"))[0].title != "Should not commit"
    async with database.sessions() as session:
        assert list(await session.scalars(select(Comment))) == []
    with pytest.raises(ValueError, match="coverage"):
        await AnalysisService(database).mark_processed("I_2")


async def test_push_close_and_reopen_since_processing(database: Database) -> None:
    source = FakeSource()
    await SyncService(database, source).run()
    await AnalysisService(database).mark_processed("I_2")
    source.head_sha = source.signal_head = "new-sha"
    await SyncService(database, source).run()
    async with database.sessions() as session:
        assert "pushed" in {
            change.kind
            for change in (
                await CatalogQueries(session).changes("I_2", since_check=True)
            ).changes
        }
    await AnalysisService(database).mark_processed("I_1")
    source.items[0] = {**source.items[0], "state": "closed"}
    await SyncService(database, source).run()
    source.items[0] = {**source.items[0], "state": "open"}
    await SyncService(database, source).run()
    async with database.sessions() as session:
        page = await CatalogQueries(session).changes("I_1", since_check=True)
        assert [change.kind for change in page.changes] == ["reopened", "closed"]
        assert (
            await CatalogQueries(session).changes(since=now() + timedelta(days=1))
        ).total == 0


async def test_unknown_comment_parent_is_fetched(database: Database) -> None:
    source = FakeSource()
    source.items, source.conversation = [], [comment(number=9)]
    await SyncService(database, source).run()
    async with database.sessions() as session:
        assert (await CatalogQueries(session).show("owner/project#9")).item.number == 9
        assert (await CatalogQueries(session).comments("I_9")).total == 1


async def test_cancelled_fetch_restarts_without_partial_data(
    database: Database,
) -> None:
    source = FakeSource()
    await SyncService(database, source).run()
    catalog = CatalogRepository(database)
    checkpoint = (await catalog.repo("1")).comments_synced_at

    class InterruptedSource(FakeSource):
        async def comments(self, _repo, _since):
            yield [comment()]
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await SyncService(database, InterruptedSource()).run()
    assert (await catalog.repo("1")).comments_synced_at == checkpoint
    async with database.sessions() as session:
        assert list(await session.scalars(select(Comment))) == []
    source.conversation = [comment()]
    assert (await SyncService(database, source).run()).status == "complete"
    async with database.sessions() as session:
        assert (await CatalogQueries(session).comments("I_2")).total == 1


async def test_current_rank_order_and_analysis_writes_are_local(
    database: Database,
) -> None:
    source = FakeSource()
    await SyncService(database, source).run()
    service = AnalysisService(database)
    await TriageService(database).decide("I_2", Disposition.PLANNED, "Preserve note")
    await service.save("I_2", "ranking", AnalysisInput(score=20))
    await service.save("I_1", "ranking", AnalysisInput(score=10))
    await service.save("I_2", "ranking", AnalysisInput(score=30))
    async with database.sessions() as session:
        page = await CatalogQueries(session).page(ItemFilters(sort="rank"))
        assert [item.id for item in page.items] == ["I_2", "I_1", "I_3"]
        assert page.items[0].ranking.score == 30
        assert page.items[0].note == "Preserve note"
        assert page.items[0].processing_state == "unprocessed"
    for data in (AnalysisInput(text=" "), AnalysisInput(labels=[" "])):
        with pytest.raises(ValueError, match="nonempty"):
            await service.save(
                "I_1", "summary" if data.text else "classification", data
            )
    with pytest.raises(ValueError):
        AnalysisInput(score=float("nan"))


async def test_complete_cli_workflow_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "workflow.sqlite"
    database = Database(path)
    try:
        await database.initialize()
        source = FakeSource()
        source.conversation = [comment()]
        await SyncService(database, source).run()
    finally:
        await database.close()

    async def no_credentials() -> str:
        raise AssertionError("Local maintenance commands must work offline.")

    monkeypatch.setattr(cli, "resolve_token", no_credentials)

    async def command(*options: str, input_text: str | None = None) -> dict:
        result = await asyncio.to_thread(
            CliRunner().invoke,
            cli.main,
            ["--db", str(path), *options, "--format", "json"],
            input=input_text,
        )
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    assert (await command("prs"))["total"] == 1
    assert (await command("status"))["total"] == 2
    assert (await command("issues", "--state", "all"))["total"] == 1
    assert len((await command("repos"))["repos"]) == 1
    item = (await command("show", "owner/project#2"))["item"]
    assert (await command("comments", "I_2"))["comments"][0][
        "body"
    ] == "Please test this"
    await command(
        "classify", "I_2", "bug", "urgent", "--revision", str(item["revision"])
    )
    await command(
        "summarize",
        "I_2",
        "-",
        "--producer",
        "agent/v1",
        input_text="A useful summary\nwith details",
    )
    await command("rank", "I_2", "90", "--reason", "High impact")
    await command(
        "triage", "I_2", "snoozed", "--until", (now() + timedelta(days=1)).isoformat()
    )
    await command("triage", "owner/project#2", "planned", "--note", "Reproduce")
    assert (
        await command("prs", "--classification", "bug", "--disposition", "planned")
    )["total"] == 1
    await command("mark-processed", "I_2", "--revision", str(item["revision"]))
    assert (await command("prs", "--unprocessed"))["total"] == 0
    assert (await command("changes", "I_2"))["total"] == 0
    assert (await command("changes", "I_2", "--all"))["total"] > 0
    assert (
        await command("changes", "--since", (now() + timedelta(days=1)).isoformat())
    )["total"] == 0
    detail = (await command("show", "I_2"))["item"]
    assert detail["summary"]["producer"] == "agent/v1"
    assert detail["ranking"]["score"] == 90
    assert detail["note"] == "Reproduce"
    assert detail["summary"]["text"] == "A useful summary\nwith details"
    for options, code, message in (
        (["show", "owner/project#999"], 1, "Unknown item"),
        (["comments", "I_2", "--limit", "0"], 2, "--limit"),
        (["triage", "I_2", "done", "--revision", "999"], 1, "Item changed"),
    ):
        result = await asyncio.to_thread(
            CliRunner().invoke, cli.main, ["--db", str(path), *options]
        )
        assert result.exit_code == code, result.output
        assert message in result.stderr
        assert not result.stdout


@pytest.mark.parametrize("coverage", ["missing", "failed"])
async def test_incomplete_coverage_excludes_analysis_from_filters_and_ranking(
    database: Database,
    coverage: str,
) -> None:
    await SyncService(database, FakeSource()).run()
    service = AnalysisService(database)
    await service.save("I_3", "ranking", AnalysisInput(score=90))
    await service.save("I_1", "ranking", AnalysisInput(score=10))
    await service.save("I_1", "classification", AnalysisInput(labels=["bug"]))
    async with database.sessions() as session:
        assert (await CatalogQueries(session).page(ItemFilters(sort="rank"))).items[
            0
        ].id == "I_3"
    async with database.sessions.begin() as session:
        repo = await session.get(Repository, "1")
        if coverage == "failed":
            repo.error = "Comment fetch failed"
        else:
            repo.comments_synced_at = None
    async with database.sessions() as session:
        query = CatalogQueries(session)
        page = await query.page(ItemFilters(sort="rank"))
        assert page.items[0].id != "I_3"  # Fall back to current priority rules.
        assert next(item for item in page.items if item.id == "I_3").ranking.stale
        assert (await query.page(ItemFilters(classification="bug"))).total == 0
