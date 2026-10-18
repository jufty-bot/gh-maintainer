"""Validated config files, Click precedence, and generated schema contracts."""

import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from gh_maintainer import cli
from gh_maintainer.config import config_schema


@pytest.fixture
def received(monkeypatch: pytest.MonkeyPatch) -> list[SimpleNamespace]:
    arguments: list[SimpleNamespace] = []

    async def run(args: SimpleNamespace) -> int:
        arguments.append(args)
        return 0

    monkeypatch.setattr(cli, "run", run)
    return arguments


@pytest.mark.parametrize("extension", ["json", "yaml", "yml"])
@pytest.mark.parametrize("placement", ["before", "after"])
@pytest.mark.parametrize("command", ["sync", "watch"])
def test_config_formats_and_shared_repositories(
    tmp_path: Path,
    extension: str,
    placement: str,
    command: str,
    received: list[SimpleNamespace],
) -> None:
    path = tmp_path / f"settings.{extension}"
    path.write_text(
        json.dumps(
            {
                "repositories": ["Owner/First", "owner/second"],
                "watch": {"interval": 120},
            }
        )
        if extension == "json"
        else (
            "repositories:\n  - Owner/First\n  - owner/second\n"
            "watch:\n  interval: 120\n"
        )
    )
    config = ["--config", str(path)]
    arguments = [*config, command] if placement == "before" else [command, *config]
    runner = CliRunner()
    result = runner.invoke(cli.main, arguments)
    assert result.exit_code == 0, result.output
    assert received[-1].repo == ("owner/first", "owner/second")
    if command == "watch":
        assert received[-1].interval == 120
    result = runner.invoke(cli.main, [*arguments, "--repo", "owner/cli"])
    assert result.exit_code == 0, result.output
    assert received[-1].repo == ("owner/cli",)


@pytest.mark.parametrize("override", [["owner/command"], None, []])
def test_command_defaults_override_shared_repositories(
    tmp_path: Path, override: list[str] | None, received: list[SimpleNamespace]
) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"repositories": ["owner/shared"], "sync": {"repo": override}})
    )
    runner = CliRunner()
    for command, expected in (
        ("sync", tuple(override or [])),
        ("watch", ("owner/shared",)),
    ):
        result = runner.invoke(cli.main, ["--config", str(path), command])
        assert result.exit_code == 0, result.output
        assert received[-1].repo == expected


@pytest.mark.parametrize("placement", ["before", "after"])
@pytest.mark.parametrize("db_option", ["none", "before", "after", "both"])
def test_database_precedence(
    tmp_path: Path, placement: str, db_option: str, received: list[SimpleNamespace]
) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"db": "configured.sqlite"}')
    arguments = ["--db", "before.sqlite"] if db_option in {"before", "both"} else []
    if placement == "before":
        arguments += ["--config", str(path)]
    arguments += ["migrate"]
    if placement == "after":
        arguments += ["--config", str(path)]
    if db_option in {"after", "both"}:
        arguments += ["--db", "after.sqlite"]
    result = CliRunner().invoke(
        cli.main, arguments, env={"GH_MAINTAINER_DB": "env.sqlite"}
    )
    assert result.exit_code == 0, result.output
    assert (
        received[-1].db
        == {
            "none": "configured.sqlite",
            "before": "before.sqlite",
            "after": "after.sqlite",
            "both": "after.sqlite",
        }[db_option]
    )


def test_boolean_list_and_positional_overrides(
    tmp_path: Path, received: list[SimpleNamespace]
) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(
        "sync:\n  full: true\n  concurrency: 2\n  requests_per_second: 0.5\n"
        "triage:\n  item_id: I_CONFIG\n  disposition: snoozed\n"
        "  note: Follow up\n  until: 2026-12-01T09:00:00Z\n"
        "prs:\n  limit: 5\n  state: all\n"
        "classify:\n  item_id: I_CONFIG\n  labels: [bug, urgent]\n"
        "mark-processed:\n  item_id: I_CONFIG\n  actor: config\n"
    )
    runner = CliRunner()
    prefix = ["--config", str(path)]
    result = runner.invoke(cli.main, [*prefix, "sync", "--no-full"])
    assert result.exit_code == 0, result.output
    assert received[-1].full is False
    assert received[-1].concurrency == 2
    assert received[-1].requests_per_second == 0.5
    result = runner.invoke(cli.main, [*prefix, "triage"])
    assert result.exit_code == 0, result.output
    assert received[-1].item_id == "I_CONFIG"
    assert received[-1].disposition == "snoozed"
    assert received[-1].until.tzinfo is not None
    result = runner.invoke(cli.main, [*prefix, "triage", "I_CLI", "done", "--note", ""])
    assert result.exit_code == 0, result.output
    assert received[-1].item_id == "I_CLI"
    assert received[-1].note == ""
    result = runner.invoke(cli.main, [*prefix, "prs", "--limit", "10"])
    assert result.exit_code == 0, result.output
    assert received[-1].limit == 10
    assert received[-1].state == "all"
    result = runner.invoke(cli.main, [*prefix, "classify"])
    assert result.exit_code == 0, result.output
    assert received[-1].labels == ("bug", "urgent")
    result = runner.invoke(cli.main, [*prefix, "mark-processed"])
    assert result.exit_code == 0, result.output
    assert received[-1].actor == "config"


@pytest.mark.parametrize(
    "content",
    [
        "{",
        "watch: [",
        "null",
        "[]",
        "",
        "unknown: true",
        "repositories: owner/project",
        "repositories: [123]",
        "sync:\n  full: 'false'",
        "watch:\n  interval: 59",
        "watch:\n  interval: true",
        "report:\n  format: html",
        "serve:\n  port: 65536",
        "triage:\n  until: '2026-12-01T09:00:00'",
        "db: !!python/object:builtins.object {}",
    ],
)
def test_invalid_config_fails_before_work(
    tmp_path: Path, content: str, received: list[SimpleNamespace]
) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(content)
    result = CliRunner().invoke(cli.main, ["--config", str(path), "demo"])
    assert result.exit_code == 2, result.output
    assert "Error:" in result.stderr
    assert not received


@pytest.mark.parametrize("filename", ["missing.yaml", "settings.toml"])
def test_missing_or_unsupported_config(tmp_path: Path, filename: str) -> None:
    result = CliRunner().invoke(
        cli.main, ["demo", "--config", str(tmp_path / filename)]
    )
    assert result.exit_code == 2, result.output
    assert "Error:" in result.stderr


def test_schema_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = Path(__file__).parents[1]
    published = project / "docs" / "config.schema.json"
    assert json.loads(published.read_text()) == config_schema()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    runpy.run_path(
        str(project / "src" / "gh_maintainer" / "config.py"), run_name="__main__"
    )
    assert (
        tmp_path / "docs" / "config.schema.json"
    ).read_text() == published.read_text()


def test_configured_commands_run(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    database = tmp_path / "demo.sqlite"
    report = tmp_path / "portfolio.json"
    path.write_text(
        json.dumps(
            {
                "$schema": config_schema()["$id"],
                "db": str(database),
                "report": {"format": "json", "output": str(report)},
            }
        )
    )
    for command in ("demo", "report"):
        result = CliRunner().invoke(cli.main, ["--config", str(path), command])
        assert result.exit_code == 0, result.output
    assert database.exists()
    assert json.loads(report.read_text())["counts"]["repositories"] > 0


@pytest.mark.parametrize(
    "arguments",
    [
        ["db"],
        ["db", "path"],
        ["db", "--config"],
        ["db", "path", "--config"],
    ],
)
def test_database_group_with_config(tmp_path: Path, arguments: list[str]) -> None:
    path = tmp_path / "settings.json"
    database = tmp_path / "configured.sqlite"
    path.write_text(json.dumps({"db": str(database)}))
    options = (
        [*arguments, str(path)]
        if arguments[-1] == "--config"
        else ["--config", str(path), *arguments]
    )
    result = CliRunner().invoke(cli.main, options)
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == str(database)
    assert not database.exists()
