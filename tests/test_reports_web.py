"""Local decisions, report integrity and read-only dashboard API."""

import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from gh_maintainer.demo import seed_demo
from gh_maintainer.report import markdown, snapshot
from gh_maintainer.store import Store
from gh_maintainer.sync import normalize
from gh_maintainer.web import create_app


def test_triage_survives_refresh_snooze_expires_and_counts_are_independent(
    store: Store,
) -> None:
    seed_demo(store)
    data = snapshot(store)
    target = next(item for item in data["items"] if item["kind"] == "issue")
    at = "2026-10-20T00:00:00Z"
    until = "2026-10-22T03:00:00+03:00"
    store.triage(target["id"], "snoozed", "Waiting for reproduction", until, at)
    before = snapshot(store, "2026-10-21T00:00:00Z")
    assert (
        next(item for item in before["items"] if item["id"] == target["id"])[
            "actionable"
        ]
        is False
    )
    assert before["counts"]["open_issues"] == data["counts"]["open_issues"]
    after = snapshot(store, "2026-10-22T00:01:00Z")
    assert (
        next(item for item in after["items"] if item["id"] == target["id"])[
            "disposition"
        ]
        == "inbox"
    )
    assert "Maintenance report" in markdown(after)


def test_done_item_returns_to_inbox_after_new_activity(store: Store) -> None:
    seed_demo(store)
    target = next(row for row in store.items("1") if row["kind"] == "issue")
    store.triage(target["id"], "done", "Resolved locally", None, "2026-10-20T00:00:00Z")
    issue = json.loads(target["raw"])
    issue["updated_at"] = "2026-10-21T00:00:00Z"
    row = normalize(issue, "1", target)
    store.commit_repo("1", [row], "2026-10-21T00:01:00Z", False, None)
    item = next(
        item
        for item in snapshot(store, "2026-10-22T00:00:00Z")["items"]
        if item["id"] == target["id"]
    )
    assert item["disposition"] == "inbox"
    assert item["note"] == "Resolved locally"


@pytest.mark.anyio
async def test_api_filters_pagination_and_no_remote_write_routes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "demo.db"
    store = Store(path)
    seed_demo(store)
    store.close()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(str(path))),
        base_url="http://test",
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
        assert (await client.get("/api/items?limit=9999")).status_code == 422
        detail = (await client.get(f"/api/items/{page['items'][0]['id']}")).json()
        assert detail["changes"]
        assert "Detailed" not in (await client.get("/api/report")).text
        assert (await client.get("/api/items/missing")).status_code == 404
        assert (
            await client.post("/api/items", json={"state": "closed"})
        ).status_code == 405


def test_atomic_commit_rolls_back_on_invalid_row(store: Store) -> None:
    seed_demo(store)
    before = store.repo("1")["synced_at"]
    row = dict(store.items("1")[0])
    row["repo_id"] = "missing"
    with pytest.raises(sqlite3.IntegrityError):
        store.commit_repo("missing", [row], "2026-11-01T00:00:00Z", False, None)
    assert store.repo("1")["synced_at"] == before
    assert len(store.items("1")) == 3
