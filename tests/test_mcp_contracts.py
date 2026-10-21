"""Compare public CLI parameters, MCP discovery, configuration and results."""

import asyncio
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import click
import pytest
from click.testing import CliRunner
from mcp import Client, StdioServerParameters

from gh_maintainer.cli import main
from gh_maintainer.clock import now
from gh_maintainer.config import Config, load_config
from gh_maintainer.context import ContextPacket
from gh_maintainer.database import Database
from gh_maintainer.mcp import MaintenanceTools, MCPRuntime
from gh_maintainer.web import create_app
from tests.fakes import FakeSource
from tests.test_mcp import http_transport, text

pytestmark = pytest.mark.anyio

# Presentation and process lifecycle are the intentional interface differences.
PROCESS_COMMANDS = {"db", "mcp", "serve"}
NESTED = {
    "prs": "options",
    "issues": "options",
    "status": "options",
    "context": "request",
}


def resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        return resolve(root["$defs"][schema["$ref"].split("/")[-1]], root)
    if "anyOf" in schema:
        return next(
            resolve(part, root)
            for part in schema["anyOf"]
            if part.get("type") != "null"
        )
    return schema


def parameters(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema["properties"].copy()
    if name in NESTED:
        nested = resolve(properties.pop(NESTED[name]), schema)
        properties.update(nested["properties"])
    if name == "policy":
        properties["policy_file"] = properties.pop("file")
    return {
        key: value
        for key, value in properties.items()
        if key != "format" or name == "report"
    }


def normalize(value: Any) -> Any:
    if isinstance(value, Path | datetime):
        return str(value) if isinstance(value, Path) else value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return list(value)
    return value


async def test_all_cli_options_defaults_and_constraints_match_mcp() -> None:
    runtime = MCPRuntime(":memory:")
    async with Client(runtime.server) as client:
        discovered = {
            tool.name: tool.input_schema for tool in (await client.list_tools()).tools
        }
    for name, command in main.commands.items():
        if name in PROCESS_COMMANDS:
            continue
        schema = discovered[name.replace("-", "_")]
        properties = parameters(name, schema)
        cli_params = {
            param.name: param
            for param in command.params
            if param.name not in {"config", "db"}
            and (param.name != "format" or name == "report")
        }
        assert properties.keys() == cli_params.keys(), name
        context = command.make_context(name, [], resilient_parsing=True)
        for key, param in cli_params.items():
            field = properties[key]
            if not param.required:
                expected = normalize(context.params[key])
                if getattr(param, "multiple", False) and expected == []:
                    expected = None
                assert field.get("default") == expected, (name, key)
            else:
                assert key in schema.get("required", []), (name, key)
            resolved = resolve(field, schema)
            if isinstance(param.type, click.Choice):
                assert set(resolved["enum"]) == set(param.type.choices), (name, key)
            elif isinstance(param.type, click.IntRange):
                assert resolved.get("minimum") == param.type.min, (name, key)
                assert resolved.get("maximum") == param.type.max, (name, key)


def settings(tmp_path: Path) -> dict[str, Any]:
    policy = tmp_path / "policy.json"
    policy.write_text("{}")
    item = {"item_id": "example/orbit#39"}
    return {
        "repositories": ["owner/project"],
        "sync": {"full": True, "concurrency": 2, "requests_per_second": 1.5},
        "watch": {"repo": [], "owner": "owner", "interval": 60},
        "report": {"format": "json", "output": str(tmp_path / "report.json")},
        "prune": {"keep": 0, "days": 1, "vacuum": True},
        "triage": {
            **item,
            "note": "configured",
            "priority": "high",
            "category": "bug_report",
            "policy": str(policy),
        },
        "prs": {
            "state": "all",
            "limit": 2,
            "offset": 1,
            "repo": "example/orbit",
            "author": "bot",
            "since": "2026-01-01T00:00:00Z",
            "priority": "high",
            "category": "bug_report",
            "triaged": False,
        },
        "issues": {"limit": 1, "search": "HTTP"},
        "status": {"state": "closed", "sort": "updated"},
        "show": item,
        "files": {"item_id": "example/orbit#42"},
        "commits": {"item_id": "example/orbit#42", "limit": 1, "offset": 1},
        "comments": {**item, "limit": 1, "offset": 1, "include_unavailable": True},
        "changes": {**item, "all": True, "limit": 1},
        "classify": {
            **item,
            "labels": ["bug"],
            "producer": "configured",
            "reason": "evidence",
        },
        "summarize": {**item, "text": "Configured summary"},
        "rank": {**item, "score": 3.0},
        "mark-processed": {**item, "actor": "configured"},
        "context": {
            **item,
            "section": "file",
            "path": "src/app.py",
            "ref": "base",
            "max_chars": 500,
        },
        "policy": {"repo": "example/orbit", "policy_file": str(policy)},
        "skills": {"install": str(tmp_path / "skills")},
    }


async def test_configured_defaults_match_click_and_mcp_discovery(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(settings(tmp_path)))
    config = load_config(path)
    runtime = MCPRuntime(":memory:", config=config)
    async with Client(runtime.server) as client:
        tools = {
            tool.name: tool.input_schema for tool in (await client.list_tools()).tools
        }
    with main.make_context("gh-maintainer", ["--config", str(path)]) as root:
        for name, configured in config.command_defaults().items():
            command = main.commands[name]
            with command.make_context(name, [], parent=root) as context:
                properties = parameters(name, tools[name.replace("-", "_")])
                for key in configured:
                    if key == "format" and name != "report":
                        continue
                    expected = normalize(context.params[key])
                    actual = properties[key].get("default")
                    if isinstance(context.params[key], datetime):
                        actual = actual.replace("Z", "+00:00")
                    assert expected == actual, (name, key)


@pytest.mark.parametrize(
    ("name", "cli_args", "arguments"),
    [
        ("prs", [], {}),
        (
            "issues",
            ["--search", "HTTP", "--limit", "1"],
            {"options": {"search": "HTTP", "limit": 1}},
        ),
        ("status", ["--state", "closed"], {"options": {"state": "closed"}}),
        ("show", ["example/orbit#39"], {"item_id": "example/orbit#39"}),
        ("files", ["example/orbit#42"], {"item_id": "example/orbit#42"}),
        ("commits", ["example/orbit#42"], {"item_id": "example/orbit#42"}),
        (
            "commits",
            ["example/orbit#42", "--limit", "1", "--offset", "1"],
            {"item_id": "example/orbit#42", "limit": 1, "offset": 1},
        ),
        ("repos", [], {}),
        (
            "comments",
            ["example/orbit#39", "--limit", "1"],
            {"item_id": "example/orbit#39", "limit": 1},
        ),
        ("changes", ["--all", "--limit", "2"], {"all": True, "limit": 2}),
        (
            "classify",
            ["example/orbit#39", "bug", "--producer", "agent"],
            {"item_id": "example/orbit#39", "labels": ["bug"], "producer": "agent"},
        ),
        (
            "summarize",
            ["example/orbit#39", "Summary"],
            {"item_id": "example/orbit#39", "text": "Summary"},
        ),
        (
            "rank",
            ["example/orbit#39", "3"],
            {"item_id": "example/orbit#39", "score": 3},
        ),
        ("mark-processed", ["example/orbit#39"], {"item_id": "example/orbit#39"}),
        ("triage", ["example/orbit#39"], {"item_id": "example/orbit#39"}),
        ("skills", [], {}),
        ("context", ["example/orbit#39"], {"item_id": "example/orbit#39"}),
        ("policy", ["--repo", "example/orbit"], {"repo": "example/orbit"}),
        ("sync", ["--repo", "owner/project"], {"repo": ["owner/project"]}),
        ("prune", ["--keep", "1"], {"keep": 1}),
    ],
)
async def test_cli_and_mcp_result_contracts(
    name: str,
    cli_args: list[str],
    arguments: dict[str, Any],
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = now()
    monkeypatch.setattr("gh_maintainer.repositories.now", lambda: at)
    monkeypatch.setattr("gh_maintainer.queries.now", lambda: at)
    packet = ContextPacket(
        item="DEMO_1_39",
        repo="example/orbit",
        indexed_revision=1,
        observed_at=at.isoformat(),
        section="overview",
    )
    for module in ("gh_maintainer.cli", "gh_maintainer.mcp_commands"):
        monkeypatch.setattr(f"{module}.context_for", AsyncMock(return_value=packet))
    for module in ("gh_maintainer.cli", "gh_maintainer.mcp"):
        monkeypatch.setattr(
            f"{module}.resolve_token", AsyncMock(return_value="test-token")
        )
        monkeypatch.setattr(
            f"{module}.AsyncGitHubSource", lambda *_a, **_kw: FakeSource()
        )
    if name == "policy":
        path = tmp_path / "policy.json"
        path.write_text('{"version": 1, "repositories": {"example/orbit": {}}}')
        cli_args += ["--file", str(path)]
        arguments["file"] = str(path)
    app = create_app("unused", database=database)
    async with app.router.lifespan_context(app), Client(http_transport(app)) as client:
        if name != "sync":
            assert not (await client.call_tool("demo")).is_error
        # Give each interface the same starting index so a no-op write cannot pass.
        cli_path = tmp_path / "cli.sqlite"
        with (
            closing(sqlite3.connect(database.engine.url.database)) as origin,
            closing(sqlite3.connect(cli_path)) as destination,
        ):
            origin.backup(destination)
        result = await asyncio.to_thread(
            CliRunner().invoke,
            main,
            [
                "--db",
                str(cli_path),
                name,
                *cli_args,
                *([] if name == "sync" else ["--format", "json"]),
            ],
        )
        assert result.exit_code == 0, result.output
        mcp = await client.call_tool(name.replace("-", "_"), arguments)
        assert not mcp.is_error, text(mcp)
        if name == "triage":
            saved = await client.call_tool("show", {"item_id": "example/orbit#39"})
            assert saved.structured_content == json.loads(result.stdout)
            assert saved.structured_content["item"]["disposition"] == "planned"
        elif name == "prune":
            assert mcp.structured_content["result"] == int(result.stdout.split()[1])
        else:
            assert mcp.structured_content == json.loads(result.stdout)


async def test_configured_calls_merge_nested_overrides_and_preserve_explicit_values(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config.model_validate(
        {
            "repositories": ["owner/default"],
            "sync": {"full": True, "concurrency": 2},
            "watch": {"interval": 60},
            "triage": {
                "item_id": "example/orbit#39",
                "note": "configured",
                "priority": "high",
            },
            "prs": {"limit": 1, "state": "all", "repo": "Example/Orbit"},
            "context": {"item_id": "example/orbit#39", "section": "files", "limit": 2},
            "report": {"format": "json", "output": str(tmp_path / "portfolio.json")},
        }
    )
    app = create_app("unused", database=database, config=config)
    captured: list[tuple[Any, ...]] = []
    original = MaintenanceTools.sync

    async def sync(self, *args):
        captured.append(args)
        return await original(self, *args)

    monkeypatch.setattr(MaintenanceTools, "sync", sync)
    monkeypatch.setattr(
        "gh_maintainer.mcp.resolve_token",
        AsyncMock(side_effect=RuntimeError("no token")),
    )
    packet = ContextPacket(
        item="DEMO_1_39",
        repo="example/orbit",
        indexed_revision=1,
        observed_at=now().isoformat(),
        section="files",
    )
    context = AsyncMock(return_value=packet)
    monkeypatch.setattr("gh_maintainer.mcp_commands.context_for", context)
    async with app.router.lifespan_context(app), Client(http_transport(app)) as client:
        await client.call_tool("demo")
        assert not (await client.call_tool("triage")).is_error
        item = (
            await client.call_tool("show", {"item_id": "example/orbit#39"})
        ).structured_content["item"]
        assert (item["disposition"], item["note"], item["priority"]) == (
            "planned",
            "configured",
            "high",
        )
        assert not (
            await client.call_tool("triage", {"note": "", "disposition": "waiting"})
        ).is_error
        item = (
            await client.call_tool("show", {"item_id": "example/orbit#39"})
        ).structured_content["item"]
        assert item["note"] == "" and item["disposition"] == "waiting"
        for options in ({}, {"options": {}}, {"options": None}):
            page = await client.call_tool("prs", options)
            assert len(page.structured_content["items"]) == 1
            assert page.structured_content["total"] > 1
        page = await client.call_tool(
            "prs", {"options": {"limit": 2, "state": "open", "repo": None}}
        )
        assert len(page.structured_content["items"]) == 2
        assert not (
            await client.call_tool("context", {"request": {"page": 2}})
        ).is_error
        request = context.await_args.args[1]
        assert (request.section, request.limit, request.page) == ("files", 2, 2)
        assert not (await client.call_tool("report")).is_error
        assert (
            json.loads((tmp_path / "portfolio.json").read_text())["counts"][
                "repositories"
            ]
            == 3
        )
        assert (
            await client.call_tool(
                "sync", {"full": False, "repo": [], "concurrency": 1}
            )
        ).is_error
        assert captured[-1][:5] == (False, [], None, 1, 2.0)
        await client.call_tool("watch", {"full": False})
        status = (await client.call_tool("watch_status")).structured_content
        assert status["repo"] == ["owner/default"] and status["interval"] == 60
        await client.call_tool("stop_watch")


@pytest.mark.parametrize("placement", ["before", "after"])
@pytest.mark.parametrize("extension", ["json", "yaml"])
async def test_stdio_inherits_operation_config(
    tmp_path: Path, placement: str, extension: str
) -> None:
    config = tmp_path / f"config.{extension}"
    config.write_text(
        json.dumps(
            {
                "db": str(tmp_path / "stdio.sqlite"),
                "triage": {
                    "item_id": "example/orbit#39",
                    "disposition": "waiting",
                    "note": "configured",
                },
                "prs": {"limit": 1, "state": "all"},
            }
        )
    )  # JSON is also valid YAML.
    arguments = ["--config", str(config)]
    arguments = [*arguments, "mcp"] if placement == "before" else ["mcp", *arguments]
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "gh_maintainer", *arguments]
    )
    async with Client(server) as client:
        assert not (await client.call_tool("demo")).is_error
        assert not (await client.call_tool("triage")).is_error
        item = (
            await client.call_tool("show", {"item_id": "example/orbit#39"})
        ).structured_content["item"]
        assert item["disposition"] == "waiting" and item["note"] == "configured"
        page = await client.call_tool("prs", {"options": {"offset": 1}})
        assert len(page.structured_content["items"]) == 1
        whole = await client.call_tool("prs", {"options": {"limit": 500, "offset": 0}})
        assert (
            page.structured_content["items"] == whole.structured_content["items"][1:2]
        )


def test_configuration_covers_all_cli_operation_options() -> None:
    config = Config()
    for name, command in main.commands.items():
        if name in {"db", "demo", "migrate"}:
            continue
        model = getattr(config, name.replace("-", "_"))
        assert model.__class__.model_fields.keys() == {
            param.name for param in command.params if param.name not in {"config", "db"}
        }, name
