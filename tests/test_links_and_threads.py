"""Tests for linking PRs and Issues, and conversation parent-child threads."""

import asyncio

import httpx
import pytest
from click.testing import CliRunner

from gh_maintainer.cli import main
from gh_maintainer.clock import now
from gh_maintainer.database import Database
from gh_maintainer.demo import seed_demo
from gh_maintainer.models import Comment
from gh_maintainer.policies import extract_linked_issues
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.schemas import CommentSnapshot
from gh_maintainer.web import create_app

pytestmark = pytest.mark.anyio


def test_extract_linked_issues_keywords_and_signals() -> None:
    body = (
        "This resolves #42 and closes owner/project#43.\n"
        "Also fixes https://github.com/owner/project/issues/44.\n"
        "Duplicate mention fixes #42."
    )
    signals = {
        "closingIssuesReferences": {
            "nodes": [{"number": 45}, {"number": 42}, {"number": "invalid"}]
        }
    }
    linked = extract_linked_issues(body, signals)
    assert linked == [45, 42, 43, 44]


def test_comment_snapshot_and_model_in_reply_to_id() -> None:
    raw = {
        "node_id": "RC_10",
        "id": 10,
        "in_reply_to_id": 9,
        "body": "Reply comment",
        "html_url": "https://github.com/owner/project/pull/1#RC_10",
    }
    snapshot = CommentSnapshot.from_github(raw, "1", 1, "inline")
    assert snapshot.in_reply_to_id == 9

    # Test ORM model property and setter
    model = Comment(**snapshot.model_dump())
    assert model.in_reply_to_id == 9

    # Re-validate from attributes
    restored = CommentSnapshot.model_validate(model, from_attributes=True)
    assert restored.in_reply_to_id == 9


async def test_linked_issues_and_prs_in_detail_and_cli(database: Database) -> None:
    await seed_demo(database)
    at = now()

    async with database.sessions() as session:
        query = CatalogQueries(session, at)
        # In demo: PR 42 closes Issue 39
        pr_detail = await query.detail("DEMO_1_42")
        assert pr_detail is not None
        assert 39 in pr_detail.item.linked_issues

        issue_detail = await query.detail("DEMO_1_39")
        assert issue_detail is not None
        assert 42 in issue_detail.item.linked_prs

    # Test CLI output for show and comments
    runner = CliRunner()
    db_path = database.engine.url.database
    assert db_path is not None

    show_pr = await asyncio.to_thread(
        runner.invoke, main, ["--db", db_path, "show", "DEMO_1_42"]
    )
    assert show_pr.exit_code == 0, show_pr.output
    assert "Linked issues: #39" in show_pr.output

    show_issue = await asyncio.to_thread(
        runner.invoke, main, ["--db", db_path, "show", "DEMO_1_39"]
    )
    assert show_issue.exit_code == 0, show_issue.output
    assert "Linked pull requests: #42" in show_issue.output

    comments_result = await asyncio.to_thread(
        runner.invoke, main, ["--db", db_path, "comments", "DEMO_2_18"]
    )
    assert comments_result.exit_code == 0, comments_result.output
    assert "↳ [reply to #1001]" in comments_result.output


async def test_web_api_links_and_threads(database: Database) -> None:
    await seed_demo(database)
    app = create_app("unused", database=database)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Check item detail includes linked_issues / linked_prs
        pr = (await client.get("/api/items/DEMO_1_42")).json()
        assert 39 in pr["item"]["linked_issues"]

        issue_item = (await client.get("/api/items/DEMO_1_39")).json()
        assert 42 in issue_item["item"]["linked_prs"]

        # Check comments API includes in_reply_to_id
        comments_data = (await client.get("/api/items/DEMO_2_18/comments")).json()
        replies = [
            c for c in comments_data["comments"] if c.get("in_reply_to_id") is not None
        ]
        assert len(replies) == 1
        assert replies[0]["in_reply_to_id"] == 1001
