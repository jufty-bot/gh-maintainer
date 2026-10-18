"""Exercise MCP discovery and CLI parity through real HTTP and stdio clients."""

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import httpx2
import pytest
from anyio import fail_after
from click.testing import CliRunner
from fastapi import FastAPI
from mcp import Client as MCPClient
from mcp import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent
from sqlalchemy import func, select

from gh_maintainer.cli import main, run
from gh_maintainer.clock import now
from gh_maintainer.config import load_config
from gh_maintainer.context import ContextPacket
from gh_maintainer.database import Database
from gh_maintainer.models import Observation
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.repositories import SyncLease
from gh_maintainer.schemas import ItemFilters, Kind, State
from gh_maintainer.web import create_app
from tests.fakes import FakeSource

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def http_transport(app: FastAPI) -> AsyncIterator[tuple[Any, Any]]:
    async with (
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as http,
        streamable_http_client(
            "http://127.0.0.1:8766/mcp", http_client=http
        ) as streams,
    ):
        yield streams


@pytest.fixture
async def client(database: Database) -> AsyncIterator[MCPClient]:
    app = create_app("unused", database=database)
    async with (
        app.router.lifespan_context(app),
        MCPClient(http_transport(app)) as session,
    ):
        assert session.server_info.name == "GitHub Maintainer"
        yield session


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_combined_dashboard_rest_and_mcp(
    database: Database, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    close = AsyncMock(wraps=database.close)
    monkeypatch.setattr(database, "close", close)
    app = create_app("unused", database=database)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8766"
        ) as http,
        MCPClient(http_transport(app), mode=mode) as mcp,
    ):
        assert (await http.get("/")).status_code == 200
        assert (await http.get("/static/app.js")).status_code == 200
        assert (await http.get("/api/docs")).status_code == 200
        assert not (await mcp.call_tool("demo")).is_error
        assert (await http.get("/api/overview")).json()["counts"]["repositories"] == 3

        await mcp.call_tool(
            "triage",
            {"item_id": "example/orbit#39", "disposition": "planned", "note": "MCP"},
        )
        item = (await http.get("/api/items/DEMO_1_39")).json()["item"]
        assert item["disposition"] == "planned" and item["note"] == "MCP"
        updated = await http.put(
            "/api/items/DEMO_1_39/triage",
            headers={"x-maintainer-request": "1"},
            json={"disposition": "waiting", "note": "Dashboard"},
        )
        assert updated.status_code == 200
        detail = await mcp.call_tool("show", {"item_id": "example/orbit#39"})
        assert detail.structured_content["item"]["disposition"] == "waiting"
        assert detail.structured_content["item"]["note"] == "Dashboard"
        assert (await http.get("/api/items/missing")).status_code == 404
        assert not (await mcp.call_tool("overview")).is_error
    close.assert_not_awaited()  # An injected database remains owned by its caller.


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch) -> FakeSource:
    fake = FakeSource()
    monkeypatch.setattr(
        "gh_maintainer.mcp.resolve_token", AsyncMock(return_value="test-token")
    )
    monkeypatch.setattr(
        "gh_maintainer.mcp.AsyncGitHubSource", lambda _token, **_kw: fake
    )
    return fake


def text(result: CallToolResult) -> str:
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


async def test_tool_discovery_and_cli_coverage(client: MCPClient) -> None:
    discovered = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert {
        "sync",
        "watch",
        "watch_status",
        "stop_watch",
        "report",
        "triage",
        "demo",
        "migrate",
        "prune",
        "overview",
        "list_items",
        "item_detail",
    } <= set(discovered)
    assert {name.replace("-", "_") for name in main.commands} - {
        "db",
        "serve",
        "mcp",
    } <= set(discovered)
    assert discovered["list_items"].annotations.read_only_hint
    assert discovered["prune"].annotations.destructive_hint
    assert discovered["report"].annotations.destructive_hint
    assert discovered["sync"].annotations.open_world_hint
    assert discovered["watch"].input_schema["properties"]["interval"]["minimum"] == 60
    assert discovered["list_items"].output_schema
    for command in ("sync", "watch", "report", "triage", "demo", "migrate", "prune"):
        assert command in discovered


async def test_reports_and_local_decisions_match_cli(
    client: MCPClient,
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Report generation uses the same time so exports can be compared byte for byte.
    at = now()
    monkeypatch.setattr("gh_maintainer.queries.now", lambda: at)
    assert not (await client.call_tool("migrate")).is_error
    assert not (await client.call_tool("demo")).is_error
    failed = await client.call_tool("demo")
    assert failed.is_error and "empty database" in text(failed)

    for disposition in ("inbox", "planned", "waiting", "done", "snoozed"):
        arguments = {
            "item_id": "DEMO_1_39",
            "disposition": disposition,
            "note": "Reproduce with current release",
            "until": (at + timedelta(days=3)).isoformat(),
        }
        assert not (await client.call_tool("triage", arguments)).is_error
        detail = await client.call_tool("item_detail", {"item_id": "DEMO_1_39"})
        assert detail.structured_content["item"]["disposition"] == disposition
        assert detail.structured_content["item"]["note"] == arguments["note"]

    # The same file and content options as CLI report work for both formats.
    async with database.sessions() as session:
        expected = await CatalogQueries(session).portfolio()
    for format_name in ("json", "markdown"):
        destination = tmp_path / f"mcp.{format_name}"
        result = await client.call_tool(
            "report", {"format": format_name, "output": str(destination)}
        )
        assert not result.is_error
        assert destination.read_text(encoding="utf-8") == text(result)
        await run(
            SimpleNamespace(
                command="report",
                db=str(database.engine.url.database),
                format=format_name,
                output=None,
            )
        )
        assert capsys.readouterr().out == text(result) + "\n"
        if format_name == "json":
            assert json.loads(text(result)) == expected.model_dump(mode="json")


async def test_queries_match_dashboard(client: MCPClient, database: Database) -> None:
    await client.call_tool("demo")
    overview = await client.call_tool("overview")
    assert overview.structured_content["counts"]["repositories"] == 3
    arguments = {
        "q": "",
        "kind": "pr",
        "state": "open",
        "repo": "example/orbit",
        "queue": False,
        "limit": 1,
        "offset": 1,
    }
    result = await client.call_tool("list_items", arguments)
    async with database.sessions() as session:
        expected = await CatalogQueries(session).page(
            ItemFilters(
                kind=Kind.PR, state=State.OPEN, repo="example/orbit", limit=1, offset=1
            )
        )
    assert result.structured_content == expected.model_dump(mode="json")
    page = await client.call_tool("list_items", {"q": "HTTP", "queue": True})
    assert page.structured_content["total"] == 1


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("item_detail", {"item_id": "missing"}, "Unknown item"),
        ("triage", {"item_id": "missing", "disposition": "planned"}, "Unknown item"),
        ("triage", {"item_id": "missing", "disposition": "bogus"}, "disposition"),
        ("triage", {"item_id": "missing", "disposition": "snoozed"}, "deadline"),
        (
            "triage",
            {
                "item_id": "missing",
                "disposition": "snoozed",
                "until": "2030-01-01T00:00:00",
            },
            "timezone",
        ),
        (
            "triage",
            {
                "item_id": "missing",
                "disposition": "snoozed",
                "until": "2020-01-01T00:00:00Z",
            },
            "future",
        ),
        ("list_items", {"limit": 501}, "limit"),
        ("list_items", {"offset": -1}, "offset"),
        ("list_items", {"kind": "bogus"}, "kind"),
        ("report", {"format": "yaml"}, "format"),
        ("watch", {"interval": 59}, "interval"),
    ],
)
async def test_invalid_calls_are_tool_errors(
    client: MCPClient, name: str, arguments: dict[str, object], message: str
) -> None:
    result = await client.call_tool(name, arguments)
    assert result.is_error
    assert message in text(result)
    # A failed tool call must leave the MCP transport usable.
    assert not (await client.call_tool("overview")).is_error


async def test_sync_full_filters_partial_and_cleanup(
    client: MCPClient, source: FakeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    close = AsyncMock()
    monkeypatch.setattr(source, "close", close)
    arguments = {"full": True, "repo": ["owner/project"]}
    result = await client.call_tool("sync", arguments)
    assert not result.is_error
    assert result.structured_content["status"] == "complete"
    assert result.structured_content["synced"] == 1
    assert source.windows == [None]
    await client.call_tool("sync", {"repo": ["owner/project"]})
    assert source.windows[-1] is not None
    await client.call_tool("sync", arguments)
    assert source.windows[-1] is None

    source.fail_signals = True
    partial = await client.call_tool("sync")
    assert partial.is_error
    assert partial.structured_content["status"] == "partial"
    assert partial.structured_content["errors"]
    missing = await client.call_tool("sync", {"repo": ["missing/repo"]})
    assert missing.is_error and missing.structured_content["errors"]
    assert close.await_count == 5


async def test_credentials_are_lazy_and_fail_as_tool_errors(
    client: MCPClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = AsyncMock(side_effect=RuntimeError("Set GH_TOKEN or GITHUB_TOKEN"))
    monkeypatch.setattr("gh_maintainer.mcp.resolve_token", token)
    assert not (await client.call_tool("overview")).is_error
    assert not (await client.call_tool("report")).is_error
    token.assert_not_awaited()
    failed = await client.call_tool("sync")
    assert failed.is_error and "GH_TOKEN" in text(failed)


async def test_prune_retention_vacuum_and_lease(
    client: MCPClient, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await client.call_tool("demo")
    at = now()
    async with database.sessions.begin() as session:
        session.add_all(
            Observation(
                item_id="DEMO_1_39",
                observed_at=at - timedelta(days=index),
                before_json=None,
                after_json={},
            )
            for index in range(10)
        )
    async with SyncLease(database):
        failed = await client.call_tool("prune", {"keep": 2})
        assert failed.is_error and "Another sync" in text(failed)
    vacuum = AsyncMock(wraps=database.vacuum)
    monkeypatch.setattr(database, "vacuum", vacuum)
    result = await client.call_tool("prune", {"keep": 2, "days": 5, "vacuum": True})
    assert not result.is_error
    assert result.structured_content["result"] > 0
    vacuum.assert_awaited_once()
    async with database.sessions() as session:
        remaining = await session.scalar(
            select(func.count())
            .select_from(Observation)
            .where(Observation.item_id == "DEMO_1_39")
        )
    assert remaining == 2
    assert not (
        await client.call_tool("item_detail", {"item_id": "DEMO_1_39"})
    ).is_error


async def wait_for_watch(client: MCPClient, *, failed: bool = False) -> dict:
    # Yield to the background task without real watch intervals or unbounded polling.
    with fail_after(5):
        while True:
            result = (await client.call_tool("watch_status")).structured_content
            if result["error"] if failed else result["last_result"]:
                return result
            await asyncio.sleep(0.01)


async def test_watch_runs_immediately_rejects_duplicates_and_stops(
    client: MCPClient,
    source: FakeSource,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = await client.call_tool(
        "watch", {"interval": 60, "full": True, "repo": ["owner/project"]}
    )
    assert not started.is_error
    assert started.structured_content["running"]
    duplicate = await client.call_tool("watch")
    assert duplicate.is_error and "already running" in text(duplicate)
    status = await wait_for_watch(client)
    assert status["last_result"]["status"] == "complete"
    assert status["interval"] == 60 and status["full"]
    assert status["repo"] == ["owner/project"]
    assert source.windows == [None]
    stopped = await client.call_tool("stop_watch")
    assert not stopped.structured_content["running"]
    assert not (await client.call_tool("stop_watch")).is_error
    async with SyncLease(database):
        pass  # stop_watch leaves the database lease available.
    # A stopped watcher can be restarted with fresh options and state.
    monkeypatch.setattr(
        source, "account", AsyncMock(side_effect=RuntimeError("offline"))
    )
    assert not (await client.call_tool("watch")).is_error
    failed = await wait_for_watch(client, failed=True)
    assert not failed["running"]
    assert "offline" in failed["error"]


async def test_shutdown_cancels_active_sync_and_closes_source(
    database: Database, source: FakeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    closed = AsyncMock()

    async def blocked_account(_owner=None):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(source, "account", blocked_account)
    monkeypatch.setattr(source, "close", closed)
    app = create_app("unused", database=database)
    async with app.router.lifespan_context(app):
        async with MCPClient(http_transport(app)) as session:
            await session.call_tool("watch")
            await asyncio.wait_for(entered.wait(), timeout=5)
    closed.assert_awaited_once()
    async with SyncLease(database):
        pass


def test_mcp_cli_launch_and_database_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_MAINTAINER_DB", "environment.sqlite")
    arguments = [
        "--db",
        "before.sqlite",
        "mcp",
        "--transport",
        "http",
        "--db",
        "after.sqlite",
        "--host",
        "127.0.0.1",
        "--port",
        "9000",
    ]
    with (
        patch("gh_maintainer.cli.create_app") as factory,
        patch("gh_maintainer.cli.uvicorn.run") as serve,
    ):
        result = CliRunner().invoke(main, arguments)
    assert result.exit_code == 0, result.output
    factory.assert_called_once_with("after.sqlite", config=None)
    serve.assert_called_once_with(factory.return_value, host="127.0.0.1", port=9000)


async def test_stdio_process_has_http_tool_parity_and_shared_index(
    client: MCPClient, database: Database
) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "gh_maintainer",
            "mcp",
            "--db",
            str(database.engine.url.database),
        ],
    )
    with fail_after(15):
        async with MCPClient(parameters) as stdio:
            assert stdio.server_info.name == "GitHub Maintainer"
            assert (await stdio.list_tools()).tools == (await client.list_tools()).tools
            assert not (await stdio.call_tool("demo")).is_error
            assert not (
                await stdio.call_tool(
                    "triage",
                    {"item_id": "DEMO_1_39", "disposition": "planned", "note": "stdio"},
                )
            ).is_error
            detail = await client.call_tool("item_detail", {"item_id": "DEMO_1_39"})
            assert detail.structured_content["item"]["disposition"] == "planned"
            assert detail.structured_content["item"]["note"] == "stdio"
            report = await stdio.call_tool("report", {"format": "json"})
            assert json.loads(text(report))["counts"]["repositories"] == 3
            invalid = await stdio.call_tool("list_items", {"limit": 0})
            assert invalid.is_error
            assert not (await stdio.call_tool("overview")).is_error
    # Closing stdin must leave the shared database usable by HTTP and CLI clients.
    assert not (await client.call_tool("overview")).is_error


def test_stdio_cli_avoids_http_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_MAINTAINER_DB", "environment.sqlite")
    with (
        patch("gh_maintainer.cli.run_stdio", new_callable=AsyncMock) as stdio,
        patch("gh_maintainer.cli.uvicorn.run") as http,
    ):
        result = CliRunner().invoke(main, ["mcp", "--db", "stdio.sqlite"])
    assert result.exit_code == 0, result.output
    stdio.assert_awaited_once_with("stdio.sqlite", config=None)
    http.assert_not_called()


async def test_analysis_processing_and_policy_guards(
    client: MCPClient, tmp_path: Path
) -> None:
    await client.call_tool("demo")
    reference = "example/orbit#39"
    before = await client.call_tool("show", {"item_id": reference})
    revision = before.structured_content["item"]["revision"]
    for name, value in (
        ("summarize", {"text": "Investigate the hang"}),
        ("classify", {"labels": ["bug", "urgent"]}),
        ("rank", {"score": 90}),
    ):
        saved = await client.call_tool(
            name,
            {
                "item_id": reference,
                "revision": revision,
                "producer": "agent/v1",
                **value,
            },
        )
        assert not saved.is_error, text(saved)
        assert saved.structured_content["source_revision"] == revision
        stale = await client.call_tool(
            name, {"item_id": reference, "revision": revision + 1, **value}
        )
        assert stale.is_error and "changed" in text(stale)

    triaged = await client.call_tool(
        "triage",
        {
            "item_id": reference,
            "disposition": "planned",
            "revision": revision,
            "priority": "high",
            "category": "bug_report",
        },
    )
    assert not triaged.is_error
    items = await client.call_tool(
        "issues",
        {
            "options": {
                "priority": "high",
                "category": "bug_report",
                "triaged": True,
                "classification": "bug",
            }
        },
    )
    assert not items.is_error, text(items)
    assert items.structured_content["total"] == 1
    for name in ("prs", "issues", "status"):
        assert not (await client.call_tool(name)).is_error
    assert not (await client.call_tool("repos")).is_error
    assert (
        await client.call_tool("comments", {"item_id": "example/orbit#42"})
    ).structured_content["total"]
    assert (
        await client.call_tool("changes", {"item_id": reference})
    ).structured_content["total"]
    processed = await client.call_tool(
        "mark_processed",
        {"item_id": reference, "revision": revision, "actor": "agent/v1"},
    )
    assert not processed.is_error
    assert (
        await client.call_tool("changes", {"item_id": reference})
    ).structured_content["total"] == 0
    assert (
        await client.call_tool("changes", {"item_id": reference, "all": True})
    ).structured_content["total"]

    path = str(tmp_path / "policy.json")
    created = await client.call_tool(
        "policy", {"repo": "example/orbit", "file": path, "initialize": True}
    )
    assert not created.is_error
    assert created.structured_content["sha256"]
    assert (
        await client.call_tool(
            "policy", {"repo": "example/orbit", "file": path, "initialize": True}
        )
    ).is_error
    denied = await client.call_tool(
        "rank", {"item_id": reference, "score": 1, "policy": path}
    )
    assert denied.is_error and "does not permit" in text(denied)
    permitted = await client.call_tool(
        "summarize", {"item_id": reference, "text": "Reviewed", "policy": path}
    )
    assert not permitted.is_error


async def test_context_partial_coverage_and_skill_installation(
    client: MCPClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await client.call_tool("demo")
    packet = ContextPacket(
        item="DEMO_1_39",
        repo="example/orbit",
        indexed_revision=1,
        observed_at=now().isoformat(),
        section="overview",
        complete=False,
        missing=["Evidence unavailable"],
    )
    fetch = AsyncMock(return_value=packet)
    monkeypatch.setattr("gh_maintainer.mcp_commands.context_for", fetch)
    result = await client.call_tool("context", {"item_id": "example/orbit#39"})
    assert result.is_error and result.structured_content["missing"]
    packet.complete = True
    assert not (
        await client.call_tool("context", {"item_id": "example/orbit#39"})
    ).is_error
    destination = tmp_path / "skills"
    installed = await client.call_tool("skills", {"install": str(destination)})
    assert not installed.is_error
    assert "gh-maintainer-triage" in installed.structured_content["skills"]
    assert (destination / "gh-maintainer-triage" / "SKILL.md").is_file()
    assert (await client.call_tool("skills", {"install": str(destination)})).is_error
    assert not (await client.call_tool("db_path")).is_error


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("sync", {"repo": ["owner/repo"], "owner": "owner"}),
        ("sync", {"repo": ["bad/name/extra"]}),
        ("sync", {"concurrency": 0}),
        ("sync", {"requests_per_second": 0}),
        ("changes", {"all": True, "since": "2026-10-01T00:00:00Z"}),
        ("changes", {"since": "2026-10-01T00:00:00"}),
        ("prs", {"options": {"unprocessed": True, "processing": "processed"}}),
        ("prune", {"keep": -1}),
        ("rank", {"item_id": "missing", "score": 0, "revision": 0}),
    ],
)
async def test_current_cli_option_validation(
    client: MCPClient, name: str, arguments: dict[str, object]
) -> None:
    assert (await client.call_tool(name, arguments)).is_error


@pytest.mark.parametrize("configured", ["http", "stdio"])
@pytest.mark.parametrize("override", [None, "stdio"])
def test_mcp_config_defaults(
    tmp_path: Path, configured: str, override: str | None
) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"db": "configured.sqlite", "mcp": {"transport": configured}})
    )
    arguments = ["--config", str(config), "mcp"]
    if override:
        arguments.extend(["--transport", override])
    with (
        patch("gh_maintainer.cli.run_stdio", new_callable=AsyncMock) as stdio,
        patch("gh_maintainer.cli.create_app") as factory,
        patch("gh_maintainer.cli.uvicorn.run") as http,
    ):
        result = CliRunner().invoke(main, arguments)
    assert result.exit_code == 0, result.output
    if (override or configured) == "stdio":
        stdio.assert_awaited_once_with("configured.sqlite", config=load_config(config))
        http.assert_not_called()
    else:
        stdio.assert_not_called()
        factory.assert_called_once_with("configured.sqlite", config=load_config(config))
        http.assert_called_once_with(factory.return_value, host="127.0.0.1", port=8766)


async def test_stdio_sync_logs_do_not_corrupt_protocol(tmp_path: Path) -> None:
    script = """
from unittest.mock import AsyncMock
from tests.fakes import FakeSource
import gh_maintainer.mcp as protocol
from gh_maintainer.cli import main
protocol.resolve_token = AsyncMock(return_value="test-token")
protocol.AsyncGitHubSource = lambda *args, **kwargs: FakeSource()
main()
"""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-c",
            script,
            "--db",
            str(tmp_path / "stdio.sqlite"),
            "mcp",
            "--transport",
            "stdio",
        ],
    )
    with fail_after(15):
        async with MCPClient(parameters) as session:
            synced = await session.call_tool("sync", {"owner": "owner"})
            assert not synced.is_error
            assert synced.structured_content["status"] == "complete"
            assert not (await session.call_tool("overview")).is_error
