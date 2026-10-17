"""Click help, argument validation, and application entry point contracts."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from gh_maintainer import cli
from gh_maintainer.__about__ import __version__


def test_help_and_version_do_not_open_database(tmp_path: Path) -> None:
    runner = CliRunner()
    path = tmp_path / "unused.sqlite"
    for options, code in (([], 2), (["--help"], 0), (["-h"], 0)):
        result = runner.invoke(cli.main, options, env={"GH_MAINTAINER_DB": str(path)})
        assert result.exit_code == code, result.output
        assert "Commands:" in result.output
        assert "Fetch issues, PRs" in result.output
        assert "GH_MAINTAINER_DB" in result.output
    result = runner.invoke(cli.main, ["--db", str(path), "--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
    for name in cli.main.commands:
        result = runner.invoke(cli.main, ["--db", str(path), name, "-h"])
        assert result.exit_code == 0, result.output
        assert "Override the SQLite index path" in result.stdout
        assert "--help" in result.stdout
    assert "--limit" in runner.invoke(cli.main, ["prs", "--help"]).stdout
    assert "default: 100" in runner.invoke(cli.main, ["prs", "--help"]).stdout
    assert not path.exists()


@pytest.mark.parametrize(
    "options",
    [
        ["sync", "--repo", "owner/project", "--owner", "owner"],
        ["watch", "--owner", "owner", "--repo", "owner/project"],
        ["sync", "--repo", "https://github.com/owner/project"],
        ["sync", "--repo", "owner/.."],
        ["sync", "--repo", "owner/project/extra"],
        ["watch", "--owner", "owner/project"],
        ["watch", "--interval", "59"],
        ["prs", "--limit", "0"],
        ["issues", "--limit", "501"],
        ["comments", "I_1", "--offset", "-1"],
        ["prs", "--processing", "processed", "--unprocessed"],
        ["changes", "--since", "2026-10-01T00:00:00Z", "--all"],
        ["changes", "--since", "yesterday"],
        ["changes", "--since", "2026-10-01T00:00:00"],
        ["triage", "I_1", "snoozed", "--until", "not-a-date"],
        ["serve", "--port", "65536"],
        ["rank", "I_1", "bad-score"],
        ["classify", "I_1"],
        ["show"],
        ["unknown-command"],
    ],
)
def test_invalid_arguments_fail_before_database_work(
    tmp_path: Path, options: list[str]
) -> None:
    path = tmp_path / "unused.sqlite"
    result = CliRunner().invoke(cli.main, ["--db", str(path), *options])
    assert result.exit_code == 2, result.output
    assert "Error:" in result.stderr
    assert "Traceback" not in result.stderr
    assert not result.stdout
    assert not path.exists()


@pytest.mark.parametrize("command", ["sync", "watch"])
def test_selection_normalization(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    received = []

    async def run(args: SimpleNamespace) -> int:
        received.append(args)
        return 0

    monkeypatch.setattr(cli, "run", run)
    runner = CliRunner()
    result = runner.invoke(
        cli.main, [command, "--repo", "Owner/Project", "--repo", "TEAM/Another"]
    )
    assert result.exit_code == 0, result.output
    assert received[-1].repo == ("owner/project", "team/another")
    result = runner.invoke(cli.main, [command, "--owner", "TEAM"])
    assert result.exit_code == 0, result.output
    assert received[-1].owner == "team"


@pytest.mark.parametrize("placement", ["environment", "before", "after", "both"])
def test_database_selection_precedence(tmp_path: Path, placement: str) -> None:
    environment = tmp_path / "env.sqlite"
    before = tmp_path / "before.sqlite"
    after = tmp_path / "after.sqlite"
    options = []
    if placement in {"before", "both"}:
        options += ["--db", str(before)]
    options += ["migrate"]
    if placement in {"after", "both"}:
        options += ["--db", str(after)]
    result = CliRunner().invoke(
        cli.main, options, env={"GH_MAINTAINER_DB": str(environment)}
    )
    assert result.exit_code == 0, result.output
    selected = {
        "environment": environment,
        "before": before,
        "after": after,
        "both": after,
    }[placement]
    assert selected.exists()
    assert all(not path.exists() for path in {environment, before, after} - {selected})


def test_demo_reports_and_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "demo.sqlite"
    runner = CliRunner()
    options = ["--db", str(path)]
    assert runner.invoke(cli.main, [*options, "demo"]).exit_code == 0
    result = runner.invoke(cli.main, [*options, "report", "--format", "json"])
    assert result.exit_code == 0, result.output
    items = json.loads(result.stdout)["items"]
    assert items
    result = runner.invoke(
        cli.main, [*options, "rank", items[0]["id"], "--format", "json", "--", "-10"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["score"] == -10
    report = tmp_path / "report.md"
    result = runner.invoke(cli.main, [*options, "report", "--output", str(report)])
    assert result.exit_code == 0, result.output
    assert not result.stdout
    assert "Maintenance report" in report.read_text()

    received = []
    monkeypatch.setattr(cli, "create_app", lambda db: db)
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda app, **kwargs: received.append((app, kwargs))
    )
    result = runner.invoke(
        cli.main, [*options, "serve", "--host", "127.0.0.2", "--port", "9000"]
    )
    assert result.exit_code == 0, result.output
    assert received == [(str(path), {"host": "127.0.0.2", "port": 9000})]


def test_interruption_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    async def interrupted(_args: SimpleNamespace) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run", interrupted)
    result = CliRunner().invoke(cli.main, ["sync"])
    assert result.exit_code == 130
    assert "Traceback" not in result.output
