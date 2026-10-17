"""Sync events and the CLI's human-readable stderr output."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from rich.logging import RichHandler

from gh_maintainer import cli
from gh_maintainer.database import Database
from gh_maintainer.logging import configure_logging
from gh_maintainer.sync import SyncService

from .fakes import FakeSource

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    logger = logging.getLogger("gh_maintainer")
    handlers, level, propagate = logger.handlers[:], logger.level, logger.propagate
    try:
        yield
    finally:
        for handler in logger.handlers:
            if handler not in handlers:
                handler.close()
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate


async def test_sync_logs_modes_counts_and_run_context(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    source = FakeSource()
    with caplog.at_level(logging.INFO, logger="gh_maintainer"):
        await SyncService(database, source).run()
        await SyncService(database, source).run()
    starts = [
        message for message in caplog.messages if "Repository sync started" in message
    ]
    assert len(starts) == 2
    assert "mode=full, since=None" in starts[0]
    assert f"mode=incremental, since={source.windows[-1]}" in starts[1]
    assert all(message.startswith("owner/project:") for message in starts)
    assert "Discovering repositories for owner" in caplog.messages
    completed = [
        message for message in caplog.messages if message.startswith("Sync finished")
    ]
    assert len(completed) == 2
    assert "run_id=1" in completed[0]
    assert "run_id=2" in completed[1]
    assert "status=complete" in completed[0]
    assert "synced=1" in completed[0]
    assert "source_calls=9" in completed[0]
    assert "duration=" in completed[0]


@pytest.mark.parametrize("signals", [False, True])
async def test_partial_sync_logs_failure_and_warning(
    database: Database, signals: bool, caplog: pytest.LogCaptureFixture
) -> None:
    source = FakeSource()
    source.fail_signals, source.fail = signals, not signals
    with caplog.at_level(logging.INFO, logger="gh_maintainer"):
        result = await SyncService(database, source).run()
    failure = next(
        record
        for record in caplog.records
        if ("Pull request signals incomplete" if signals else "Repository sync failed")
        in record.getMessage()
    )
    assert failure.getMessage().startswith("owner/project:")
    assert ("Incomplete GraphQL response" if signals else "503") in failure.getMessage()
    assert failure.levelno == (logging.WARNING if signals else logging.ERROR)
    finished = caplog.records[-1]
    assert finished.getMessage().startswith("Sync finished")
    assert result.status == "partial"
    assert "status=partial" in finished.getMessage()
    assert "errors=1" in finished.getMessage()
    assert finished.levelno == logging.WARNING
    repo_finishes = [
        message for message in caplog.messages if "Repository sync finished" in message
    ]
    assert len(repo_finishes) == (1 if signals else 0)


async def test_fatal_sync_logs_error_and_propagates(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = FakeSource()
    monkeypatch.setattr(
        source,
        "account",
        AsyncMock(side_effect=RuntimeError("Account discovery failed")),
    )
    with caplog.at_level(logging.INFO, logger="gh_maintainer"):
        with pytest.raises(RuntimeError, match="Account discovery failed"):
            await SyncService(database, source).run()
    assert caplog.records[-1].getMessage().startswith("Sync failed")
    assert caplog.records[-1].levelno == logging.ERROR
    assert caplog.records[-1].exc_info is not None
    assert "Account discovery failed" in caplog.records[-1].getMessage()
    assert not any(message.startswith("Sync finished") for message in caplog.messages)


async def test_cli_logs_text_to_stderr_and_result_to_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = FakeSource()
    monkeypatch.setattr(cli, "resolve_token", AsyncMock(return_value="test-token"))
    monkeypatch.setattr(cli, "PyGithubSource", lambda _token: source)
    close = AsyncMock()
    monkeypatch.setattr(source, "close", close, raising=False)
    args = SimpleNamespace(
        command="sync",
        db=str(tmp_path / "cli.sqlite"),
        full=False,
        repo=None,
        owner=None,
    )
    assert await cli.run(args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "complete"
    assert "Sync started" in captured.err
    assert "Repository sync finished" in captured.err
    assert "owner/project:" in captured.err
    assert "INFO" in captured.err
    assert "\x1b[" not in captured.err
    assert all(not line.startswith("{") for line in captured.err.splitlines())
    close.assert_awaited_once()


async def test_rich_handler_preserves_markup_and_tracebacks_without_duplicate_logs(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "160")
    root_handlers = logging.getLogger().handlers[:]
    configure_logging()
    configure_logging()
    logger = logging.getLogger("gh_maintainer.sync")
    assert isinstance(logging.getLogger("gh_maintainer").handlers[0], RichHandler)
    logger.info("Literal [red]text[/red]")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("Literal [red]text[/red]") == 1
    assert "\x1b[" not in captured.err
    try:
        raise ValueError("Example failure")
    except ValueError:
        logger.exception("Sync failed")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ValueError: Example failure" in captured.err
    assert "\x1b[" not in captured.err
    assert logging.getLogger().handlers == root_handlers
