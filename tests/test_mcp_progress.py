"""Exercise progress and peer cancellation over actual HTTP and stdio transports."""

import asyncio
import json
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from itertools import pairwise
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import uvicorn
from anyio import fail_after
from fastapi import FastAPI
from mcp import Client, StdioServerParameters
from sqlalchemy import select

from gh_maintainer import mcp as protocol
from gh_maintainer.database import Database
from gh_maintainer.models import Repository, SyncRun
from gh_maintainer.repositories import SyncLease
from gh_maintainer.web import create_app
from tests.test_sync import ParallelSource

pytestmark = pytest.mark.anyio


def prepare_source(directory: Path) -> ParallelSource:
    """Expose worker entry and completed cleanup across the subprocess boundary."""

    class Started(asyncio.Event):
        def set(self) -> None:
            super().set()
            (directory / "started").touch()

    class Release(asyncio.Event):
        async def wait(self) -> bool:
            while not self.is_set() and not (directory / "release").exists():
                await asyncio.sleep(0.01)
            return True

    source = ParallelSource()
    source.release = Release()
    source.started = Started()
    source.fail_activity = (directory / "partial").exists()
    if (directory / "release").exists():
        source.release.set()
    closes = 0

    async def close() -> None:
        nonlocal closes
        closes += 1
        temporary = directory / "closed.tmp"
        temporary.write_text(json.dumps({"active": source.active, "closes": closes}))
        temporary.replace(directory / "closed.json")
        # A subsequent sync can complete after cancellation without restarting MCP.
        source.release.set()

    source.close = close
    return source


@asynccontextmanager
async def http_server(app: FastAPI) -> AsyncIterator[str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical"))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            with fail_after(5):
                while not server.started:
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{listener.getsockname()[1]}/mcp"
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=5)


@pytest.fixture(params=["http", "stdio"])
def transport(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(params=["legacy", "auto"])
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


@asynccontextmanager
async def connect(
    transport: str,
    mode: str,
    database: Database,
    directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Client]:
    if transport == "stdio":
        script = f"""
from pathlib import Path
from unittest.mock import AsyncMock
from tests.test_mcp_progress import prepare_source
import gh_maintainer.mcp as protocol
from gh_maintainer.cli import main
source = prepare_source(Path({str(directory)!r}))
protocol.resolve_token = AsyncMock(return_value="test-token")
protocol.AsyncGitHubSource = lambda *args, **kwargs: source
main()
"""
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-c", script, "--db", str(database.engine.url.database), "mcp"],
        )
        async with Client(parameters, mode=mode) as client:
            yield client
    else:
        source = prepare_source(directory)
        monkeypatch.setattr(
            protocol, "resolve_token", AsyncMock(return_value="test-token")
        )
        monkeypatch.setattr(protocol, "AsyncGitHubSource", lambda *_a, **_kw: source)
        app = create_app("unused", database=database)
        async with http_server(app) as url, Client(url, mode=mode) as client:
            yield client


async def wait_for_file(path: Path) -> None:
    with fail_after(10):
        while not path.exists():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("partial", [False, True])
async def test_sync_reports_progress(
    partial: bool,
    transport: str,
    mode: str,
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if partial:
        (tmp_path / "partial").touch()
    updates: list[tuple[float, float | None, str | None]] = []
    notified = asyncio.Event()

    async def progress(value: float, total: float | None, message: str | None) -> None:
        updates.append((value, total, message))
        if message and message.startswith("Syncing "):
            notified.set()

    async with connect(transport, mode, database, tmp_path, monkeypatch) as client:
        syncing = asyncio.create_task(
            client.call_tool("sync", progress_callback=progress)
        )
        try:
            with fail_after(15):
                await notified.wait()
                assert not syncing.done()
                (tmp_path / "release").touch()
                result = await syncing
        finally:
            syncing.cancel()
            await asyncio.gather(syncing, return_exceptions=True)
        assert result.is_error is partial
        status = "partial" if partial else "complete"
        assert result.structured_content["status"] == status
        assert result.structured_content["synced"] == 6
        assert updates[0][2] == "Discovering repositories"
        assert updates[-1][2] == f"Sync {status}: 6 of 6 repositories synced"
        assert any(message.startswith("Synced owner/") for _, _, message in updates)
        assert all(total is None for _, total, _ in updates)
        values = [value for value, _, _ in updates]
        assert all(right > left for left, right in pairwise(values))
        count = len(updates)
        assert not (await client.call_tool("overview")).is_error
        assert len(updates) == count


async def test_client_cancellation_drains_sync_and_releases_resources(
    transport: str,
    mode: str,
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with connect(transport, mode, database, tmp_path, monkeypatch) as client:
        syncing = asyncio.create_task(client.call_tool("sync", {"concurrency": 2}))
        try:
            await wait_for_file(tmp_path / "started")
            syncing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await syncing
            await wait_for_file(tmp_path / "closed.json")
            assert json.loads((tmp_path / "closed.json").read_text()) == {
                "active": 0,
                "closes": 1,
            }
            async with SyncLease(database):
                pass
            async with database.sessions() as session:
                assert (await session.scalar(select(SyncRun.status))) == "failed"
                assert not any(await session.scalars(select(Repository.synced_at)))
            # The same client can retry immediately, with no stale lease/workers.
            with fail_after(15):
                retry = await client.call_tool("sync")
            assert not retry.is_error
            assert retry.structured_content["status"] == "complete"
            assert json.loads((tmp_path / "closed.json").read_text()) == {
                "active": 0,
                "closes": 2,
            }
            assert not (await client.call_tool("overview")).is_error
        finally:
            syncing.cancel()
            await asyncio.gather(syncing, return_exceptions=True)
