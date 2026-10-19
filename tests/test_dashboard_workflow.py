"""Dashboard setup, recoverable sync, and atomic revision-bound review completion."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from anyio import fail_after
from sqlalchemy import event

from gh_maintainer import repositories
from gh_maintainer.clock import now
from gh_maintainer.config import Config
from gh_maintainer.database import Database
from gh_maintainer.demo import seed_demo
from gh_maintainer.github import GitHubError
from gh_maintainer.models import Item, Processing, Repository, TriageDecision
from gh_maintainer.repositories import CatalogRepository, SyncLease, TriageService
from gh_maintainer.schemas import Category, Disposition, Priority
from gh_maintainer.web import create_app
from tests.fakes import FakeSource

pytestmark = pytest.mark.anyio
HEADERS = {"X-Maintainer-Request": "1"}
ITEM = "DEMO_1_42"


async def finished(client: httpx.AsyncClient) -> dict:
    with fail_after(5):
        while True:
            status = (await client.get("/api/sync")).json()
            if status["state"] != "running":
                return status
            await asyncio.sleep(0.01)


async def test_setup_detects_server_credentials_without_exposing_them(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = AsyncMock(side_effect=GitHubError("Missing credential"))
    monkeypatch.setattr("gh_maintainer.web.resolve_token", token)
    config = Config.model_validate({"repositories": ["owner/project"]})
    app = create_app("unused", database, config)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        setup = (await client.get("/api/setup")).json()
        assert not setup["credential_available"]
        assert "gh auth login" in setup["message"]
        assert setup["sync"]["options"]["repo"] == ["owner/project"]
        assert (await client.get("/api/overview")).status_code == 200
        token.side_effect = None
        token.return_value = "secret-never-sent-to-browser"
        response = await client.get("/api/setup")
        assert response.json()["credential_available"]
        assert token.return_value not in response.text


async def test_dashboard_sync_progress_partial_retry_and_validation(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeSource()
    source.fail_comments = True
    monkeypatch.setattr(
        "gh_maintainer.mcp.resolve_token", AsyncMock(return_value="fake")
    )
    factory = lambda *_args, **_kwargs: source  # noqa: E731
    monkeypatch.setattr("gh_maintainer.mcp.AsyncGitHubSource", factory)
    app = create_app("unused", database)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        for body in (
            {"repo": []},
            {"owner": "owner", "repo": ["a/b"]},
            {"repo": ["bad"]},
            {"concurrency": 0},
        ):
            assert (
                await client.post("/api/sync", headers=HEADERS, json=body)
            ).status_code == 422
        assert (await client.post("/api/sync", json={})).status_code == 403
        assert (
            await client.post(
                "/api/sync",
                headers={**HEADERS, "Sec-Fetch-Site": "cross-site"},
                json={},
            )
        ).status_code == 403
        assert (await client.get("/api/sync")).json()["state"] == "idle"
        response = await client.post(
            "/api/sync",
            headers=HEADERS,
            json={"repo": ["OWNER/PROJECT", "owner/project"]},
        )
        assert response.status_code == 202
        assert response.json()["options"]["repo"] == ["owner/project"]
        partial = await finished(client)
        assert partial["state"] == "partial"
        assert partial["stages"] > 0 and partial["result"]["errors"]
        assert (await CatalogRepository(database).repo("1")).synced_at is None
        source.fail_comments = False
        await client.post(
            "/api/sync", headers=HEADERS, json={"repo": ["owner/project"]}
        )
        complete = await finished(client)
        assert complete["state"] == "complete"
        assert complete["result"]["synced"] == 1
        assert (await client.get("/api/items")).json()["total"] == 3
        assert (await CatalogRepository(database).repo("1")).synced_at


async def test_background_sync_cancellation_busy_and_shutdown_cleanup(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    class BlockedSource(FakeSource):
        async def issues(self, repo, since):
            entered.set()
            await release.wait()
            async for rows in super().issues(repo, since):
                yield rows

    source = BlockedSource()
    close = AsyncMock()
    source.close = close
    monkeypatch.setattr(
        "gh_maintainer.mcp.resolve_token", AsyncMock(return_value="fake")
    )
    monkeypatch.setattr(
        "gh_maintainer.mcp.AsyncGitHubSource", lambda *_a, **_kw: source
    )
    app = create_app("unused", database)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        await client.post("/api/sync", headers=HEADERS, json={})
        with fail_after(3):
            await entered.wait()
        assert (await client.get("/api/sync")).json()["stages"] > 0
        assert (
            await client.post("/api/sync", headers=HEADERS, json={})
        ).status_code == 409
        assert (await client.post("/api/sync/cancel")).status_code == 403
        assert (await client.post("/api/sync/cancel", headers=HEADERS)).json()[
            "state"
        ] == "cancelled"
        close.assert_awaited_once()
        async with SyncLease(database):
            pass
        entered.clear()
        await client.post("/api/sync", headers=HEADERS, json={})
        with fail_after(3):
            await entered.wait()
        # Leaving the app lifespan cancels a disconnected browser's running sync.
    assert close.await_count == 2
    async with SyncLease(database):
        pass


async def test_finish_review_saves_decision_and_acknowledgement_and_reopens(
    database: Database,
) -> None:
    await seed_demo(database)
    app = create_app("unused", database)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        item = (await client.get(f"/api/items/{ITEM}")).json()["item"]
        body = {
            "disposition": "done",
            "note": "Reviewed the conversation",
            "priority": "high",
            "category": "bug_fix",
            "revision": item["revision"],
            "finish_review": True,
        }
        missing = await client.put(
            f"/api/items/{ITEM}/triage",
            headers=HEADERS,
            json={**body, "revision": None},
        )
        assert missing.status_code == 422
        response = await client.put(
            f"/api/items/{ITEM}/triage", headers=HEADERS, json=body
        )
        assert response.status_code == 200
        saved = response.json()["item"]
        assert saved["disposition"] == "done" and saved["state"] == "open"
        assert saved["processing_state"] == "processed"
        assert saved["processed_revision"] == item["revision"]
        assert saved["processed_by"] == "dashboard"
        catalog = CatalogRepository(database)
        snapshot = next(row for row in await catalog.items("1") if row.id == ITEM)
        await catalog.commit_repo(
            "1",
            [snapshot.model_copy(update={"title": "New activity"})],
            now() + timedelta(seconds=1),
            False,
            None,
        )
        stale = await client.put(
            f"/api/items/{ITEM}/triage",
            headers=HEADERS,
            json={**body, "note": "Must not save"},
        )
        assert stale.status_code == 409
        changed = (await client.get(f"/api/items/{ITEM}")).json()["item"]
        assert changed["disposition"] == "inbox"
        assert changed["processing_state"] == "changed"
        assert changed["note"] == body["note"]


@pytest.mark.parametrize("gap", ["comments", "error", "unavailable", "absent"])
async def test_finish_review_rejects_incomplete_coverage_without_saving_triage(
    database: Database, gap: str
) -> None:
    await seed_demo(database)
    await TriageService(database).decide(ITEM, Disposition.WAITING, "Keep this note")
    async with database.sessions.begin() as session:
        item = await session.get(Item, ITEM)
        repo = await session.get(Repository, "1")
        if gap == "comments":
            repo.comments_synced_at = None
        elif gap == "error":
            repo.error = "Comment page failed"
        elif gap == "absent":
            repo.present = False
        else:
            item.available = False
    with pytest.raises(ValueError):
        await TriageService(database).decide(
            ITEM, Disposition.DONE, "Must roll back", revision=1, finish_review=True
        )
    async with database.sessions() as session:
        assert await session.get(Processing, ITEM) is None
        decision = await session.get(TriageDecision, ITEM)
        assert decision.note == "Keep this note" and decision.disposition == "waiting"


async def test_finish_review_rolls_back_acknowledgement_if_triage_write_fails(
    database: Database,
) -> None:
    await seed_demo(database)

    def fail_triage(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO triage"):
            raise RuntimeError("Interrupted decision save")

    event.listen(database.engine.sync_engine, "before_cursor_execute", fail_triage)
    try:
        with pytest.raises(RuntimeError, match="Interrupted"):
            await TriageService(database).decide(
                ITEM, Disposition.DONE, revision=1, finish_review=True
            )
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", fail_triage)
    async with database.sessions() as session:
        assert await session.get(Processing, ITEM) is None
        assert await session.get(TriageDecision, ITEM) is None


async def test_sync_racing_review_save_rejects_both_writes(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_demo(database)
    entered, release = asyncio.Event(), asyncio.Event()
    record = repositories.record_processing

    async def after_read(session, item, actor, at):
        entered.set()
        await release.wait()
        return await record(session, item, actor, at)

    monkeypatch.setattr(repositories, "record_processing", after_read)
    app = create_app("unused", database)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        save = asyncio.create_task(
            client.put(
                f"/api/items/{ITEM}/triage",
                headers=HEADERS,
                json={"disposition": "done", "revision": 1, "finish_review": True},
            )
        )
        with fail_after(3):
            await entered.wait()
        async with database.sessions.begin() as session:
            item = await session.get(Item, ITEM)
            item.revision += 1
        release.set()
        assert (await save).status_code == 409
    async with database.sessions() as session:
        assert await session.get(Processing, ITEM) is None
        assert await session.get(TriageDecision, ITEM) is None


async def test_kanban_pages_share_effective_triage_and_keep_fields_on_move(
    database: Database,
) -> None:
    await seed_demo(database)
    await TriageService(database).decide(
        ITEM,
        Disposition.PLANNED,
        "Keep the next action",
        priority=Priority.HIGH,
        category=Category.BUG_FIX,
    )
    app = create_app("unused", database)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for lane in Disposition:
            response = await client.get(
                f"/api/items?disposition={lane.value}&limit=1&offset=0"
            )
            assert response.status_code == 200
            assert all(
                item["disposition"] == lane.value for item in response.json()["items"]
            )
        item = (await client.get(f"/api/items/{ITEM}")).json()["item"]
        moved = (
            await client.put(
                f"/api/items/{ITEM}/triage",
                headers=HEADERS,
                json={
                    "disposition": "waiting",
                    "note": item["note"],
                    "priority": item["priority"],
                    "category": item["category"],
                    "revision": item["revision"],
                },
            )
        ).json()["item"]
        assert moved["note"] == "Keep the next action"
        assert moved["priority"] == "high" and moved["category"] == "bug_fix"
        assert moved["processing_state"] == "unprocessed"
        assert (
            await client.get(
                "/api/items?repo=example/orbit&disposition=waiting&priority=high"
            )
        ).json()["total"] == 1
