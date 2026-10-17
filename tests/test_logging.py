"""Sync events and the CLI's human-readable stderr output."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import structlog
from structlog.testing import capture_logs

from gh_maintainer import cli
from gh_maintainer.database import Database
from gh_maintainer.sync import SyncService

from .fakes import FakeSource

pytestmark = pytest.mark.anyio


async def test_sync_logs_modes_counts_and_run_context(database: Database) -> None:
    source = FakeSource()
    with capture_logs() as logs:
        await SyncService(database, source).run()
        await SyncService(database, source).run()
    starts = [entry for entry in logs if entry["event"] == "Repository sync started"]
    assert [entry["mode"] for entry in starts] == ["full", "incremental"]
    assert starts[0]["since"] is None
    assert starts[1]["since"] == source.windows[-1]
    for entry in starts:
        assert entry["owner"] == "owner"
        assert entry["repository"] == "owner/project"
        assert entry["repository_id"] == "1"
        assert entry["run_id"] > 0
    completed = [entry for entry in logs if entry["event"] == "Sync finished"]
    assert len(completed) == 2
    assert completed[0]["run_id"] != completed[1]["run_id"]
    assert completed[0]["synced"] == 1
    assert completed[0]["source_calls"] == 9
    assert completed[0]["status"] == "complete"
    assert completed[0]["duration_seconds"] >= 0


@pytest.mark.parametrize("signals", [False, True])
async def test_partial_sync_logs_failure_and_warning(
    database: Database, signals: bool
) -> None:
    source = FakeSource()
    source.fail_signals, source.fail = signals, not signals
    with capture_logs() as logs:
        result = await SyncService(database, source).run()
    failure = next(
        entry
        for entry in logs
        if entry["event"]
        == ("Pull request signals incomplete" if signals else "Repository sync failed")
    )
    assert failure["repository"] == "owner/project"
    assert failure["error"]
    assert failure["log_level"] == ("warning" if signals else "error")
    finished = logs[-1]
    assert finished["event"] == "Sync finished"
    assert finished["status"] == result.status == "partial"
    assert finished["errors"] == 1
    assert finished["log_level"] == "warning"
    repo_finishes = [
        entry for entry in logs if entry["event"] == "Repository sync finished"
    ]
    assert len(repo_finishes) == (1 if signals else 0)


async def test_fatal_sync_logs_error_and_propagates(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeSource()
    monkeypatch.setattr(
        source,
        "account",
        AsyncMock(side_effect=RuntimeError("Account discovery failed")),
    )
    with capture_logs() as logs:
        with pytest.raises(RuntimeError, match="Account discovery failed"):
            await SyncService(database, source).run()
    assert logs[-1]["event"] == "Sync failed"
    assert logs[-1]["log_level"] == "error"
    assert "Account discovery failed" in logs[-1]["error"]
    assert not any(entry["event"] == "Sync finished" for entry in logs)


async def test_cli_logs_text_to_stderr_and_result_to_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = FakeSource()
    monkeypatch.setattr(cli, "resolve_token", AsyncMock(return_value="test-token"))
    monkeypatch.setattr(cli, "PyGithubSource", lambda _token: source)
    close = AsyncMock()
    monkeypatch.setattr(source, "close", close, raising=False)
    config = structlog.get_config()
    try:
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
        assert "repository=owner/project" in captured.err
        assert "[info" in captured.err
        assert "\x1b[" not in captured.err
        assert all(not line.startswith("{") for line in captured.err.splitlines())
        close.assert_awaited_once()
    finally:
        structlog.configure(**config)
