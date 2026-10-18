"""Test CLI entry points, argument parsing, error handling and command execution."""

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from gh_maintainer.cli import main, parser, run
from gh_maintainer.clock import now, parse_date, timestamp
from gh_maintainer.database import Database
from gh_maintainer.demo import seed_demo
from gh_maintainer.github import GitHubError, resolve_token
from gh_maintainer.models import Observation
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.report import markdown
from gh_maintainer.repositories import CatalogRepository, TriageService
from gh_maintainer.schemas import (
    Counts,
    Disposition,
    ItemFilters,
    Portfolio,
    RepositoryView,
)
from gh_maintainer.sync import SyncService
from tests.fakes import FakeSource

pytestmark = pytest.mark.anyio


def test_parser_defaults_and_subcommands() -> None:
    p = parser()
    args = p.parse_args(["sync", "--full"])
    assert args.command == "sync"
    assert args.full is True
    assert args.repo is None

    args = p.parse_args(
        ["--db", "test.sqlite", "watch", "--interval", "120", "--repo", "a/b"]
    )
    assert args.db == "test.sqlite"
    assert args.command == "watch"
    assert args.interval == 120
    assert args.repo == ["a/b"]

    args = p.parse_args(["report", "--format", "json", "--output", "report.json"])
    assert args.command == "report"
    assert args.format == "json"
    assert args.output == Path("report.json")

    args = p.parse_args(
        ["triage", "I_123", "snoozed", "--until", "2026-12-01T00:00:00Z"]
    )
    assert args.command == "triage"
    assert args.item_id == "I_123"
    assert args.disposition == "snoozed"
    assert args.until == "2026-12-01T00:00:00Z"


async def test_cli_demo_and_migrate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = str(tmp_path / "test.sqlite")

    # Test demo command
    args = argparse.Namespace(command="demo", db=db_path)
    exit_code = await run(args)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Demo data created" in captured.out

    # Test migrate command
    args = argparse.Namespace(command="migrate", db=db_path)
    exit_code = await run(args)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Database schema is up to date" in captured.out


async def test_cli_triage_and_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = str(tmp_path / "test.sqlite")
    await run(argparse.Namespace(command="demo", db=db_path))
    capsys.readouterr()

    # Triage an item
    args = argparse.Namespace(
        command="triage",
        db=db_path,
        item_id="DEMO_1_39",
        disposition="planned",
        note="Prioritize next week",
        until=None,
    )
    assert await run(args) == 0
    assert "Local triage decision saved" in capsys.readouterr().out

    # Report to stdout (markdown)
    args = argparse.Namespace(
        command="report", db=db_path, format="markdown", output=None
    )
    assert await run(args) == 0
    out = capsys.readouterr().out
    assert "# Maintenance report: example" in out
    assert "Prioritize next week" not in out or "planned" in out

    # Report to file (json)
    out_file = tmp_path / "portfolio.json"
    args = argparse.Namespace(
        command="report", db=db_path, format="json", output=out_file
    )
    assert await run(args) == 0
    assert out_file.exists()
    assert '"counts"' in out_file.read_text(encoding="utf-8")


async def test_cli_sync(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = str(tmp_path / "test.sqlite")
    fake = FakeSource()
    args = argparse.Namespace(
        command="sync",
        db=db_path,
        full=False,
        repo=None,
    )
    with (
        patch("gh_maintainer.cli.resolve_token", AsyncMock(return_value="dummy_token")),
        patch("gh_maintainer.cli.PyGithubSource", return_value=fake),
    ):
        code = await run(args)
        assert code == 0
        out = capsys.readouterr().out
        assert '"status": "complete"' in out


async def test_cli_watch_interval_validation(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test.sqlite")
    args = argparse.Namespace(
        command="watch",
        db=db_path,
        full=False,
        repo=None,
        interval=30,  # Below minimum 60
    )
    with pytest.raises(ValueError, match="at least 60 seconds"):
        await run(args)


def test_cli_main_keyboard_interrupt() -> None:
    with patch("gh_maintainer.cli.parser") as mock_parser:
        mock_parser.return_value.parse_args.side_effect = KeyboardInterrupt
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 130


def test_cli_main_handled_error(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("gh_maintainer.cli.parser") as mock_parser:
        mock_parser.return_value.parse_args.side_effect = ValueError("Invalid flag")
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert "Error: Invalid flag" in capsys.readouterr().err


async def test_clock_validation_errors() -> None:
    with pytest.raises(ValueError, match="including a timezone"):
        parse_date("2026-10-18T12:00:00")

    with pytest.raises(ValueError, match="must include a timezone"):
        # Explicitly testing naive datetime rejection
        timestamp(datetime(2026, 10, 18, 12, 0, 0))  # noqa: DTZ001


async def test_report_edge_cases() -> None:
    now_dt = parse_date("2026-10-18T12:00:00Z")
    portfolio = Portfolio(
        owner="test-owner",
        generated_at=now_dt,
        latest_run=None,
        counts=Counts(
            repositories=1,
            indexed_items=0,
            open_issues=0,
            open_prs=0,
            attention=0,
            incomplete_repositories=1,
        ),
        repos=[
            RepositoryView(
                id="1",
                name="test/repo",
                url="https://github.com/test/repo",
                archived=False,
                present=False,
                raw={},
                error="Sync failed",
            )
        ],
        items=[],
    )
    md = markdown(portfolio)
    assert "No active items in the maintenance queue." in md
    assert "Coverage incomplete for 1 repositories." in md
    assert "No longer visible in owner inventory; retained locally" in md


async def test_resolve_token_env_and_missing() -> None:
    with patch.dict(os.environ, {"GH_TOKEN": "token_123"}, clear=True):
        assert await resolve_token() == "token_123"

    with patch.dict(os.environ, {"GITHUB_TOKEN": "token_456"}, clear=True):
        assert await resolve_token() == "token_456"

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("subprocess.run", side_effect=OSError("gh not found")),
        pytest.raises(GitHubError, match="Set GH_TOKEN or GITHUB_TOKEN"),
    ):
        await resolve_token()


async def test_sync_repository_filter(tmp_path: Path) -> None:
    db = Database(tmp_path / "sync_filter.sqlite")
    await db.initialize()
    try:
        fake = FakeSource()
        # Non-matching repo raises ValueError
        with pytest.raises(ValueError, match="Requested repositories are not visible"):
            await SyncService(db, fake).run(only={"non-existent/repo"})

        # Matching repo succeeds
        res = await SyncService(db, fake).run(only={"owner/project"})
        assert res.status == "complete"
        assert res.synced == 1
    finally:
        await db.close()


async def test_demo_non_empty_database(tmp_path: Path) -> None:
    db = Database(tmp_path / "demo_non_empty.sqlite")
    await db.initialize()
    try:
        await seed_demo(db)
        with pytest.raises(ValueError, match="empty database"):
            await seed_demo(db)
    finally:
        await db.close()


async def test_queries_repo_filter(tmp_path: Path) -> None:
    db = Database(tmp_path / "query_repo.sqlite")
    await db.initialize()
    try:
        await seed_demo(db)
        async with db.sessions() as session:
            queries = CatalogQueries(session)
            page = await queries.page(ItemFilters(repo="example/orbit"))
            assert page.total > 0
            assert all(item.repo == "example/orbit" for item in page.items)
    finally:
        await db.close()


async def test_repositories_error_cases(tmp_path: Path) -> None:
    db = Database(tmp_path / "repo_errors.sqlite")
    await db.initialize()
    try:
        cat = CatalogRepository(db)
        with pytest.raises(ValueError, match="Unknown repository ID"):
            await cat.repo("nonexistent")
        with pytest.raises(ValueError, match="Unknown repository ID"):
            await cat.commit_repo(
                "nonexistent", [], parse_date("2026-10-18T00:00:00Z"), False, None
            )

        triage = TriageService(db)
        with pytest.raises(ValueError, match="Unknown item ID"):
            await triage.decide("nonexistent", Disposition.PLANNED)
    finally:
        await db.close()


async def test_cli_prune_and_vacuum(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = str(tmp_path / "prune_test.sqlite")
    db = Database(db_path)
    await db.initialize()
    await seed_demo(db)

    # Insert extra observations for item DEMO_1_39
    at = now()
    async with db.sessions.begin() as session:
        for i in range(10):
            session.add(
                Observation(
                    item_id="DEMO_1_39",
                    observed_at=at - timedelta(days=i),
                    before_json=None,
                    after_json={},
                )
            )

    async with db.sessions() as session:
        count_before = await session.scalar(
            select(func.count()).select_from(Observation)
        )

    # Run prune CLI with keep=2 and vacuum=True
    args = argparse.Namespace(command="prune", db=db_path, keep=2, days=5, vacuum=True)
    assert await run(args) == 0
    out = capsys.readouterr().out
    assert "Pruned" in out

    async with db.sessions() as session:
        count_after = await session.scalar(
            select(func.count()).select_from(Observation)
        )
    assert count_after is not None and count_before is not None
    assert count_after < count_before
    await db.close()


async def test_database_vacuum_direct(tmp_path: Path) -> None:
    db = Database(tmp_path / "vacuum_test.sqlite")
    await db.initialize()
    try:
        await db.vacuum()
    finally:
        await db.close()
