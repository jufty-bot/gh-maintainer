"""Click help, argument validation, and application entry point contracts."""

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from sqlalchemy import func, select

from gh_maintainer import cli
from gh_maintainer.__about__ import __version__
from gh_maintainer.clock import now
from gh_maintainer.database import Database
from gh_maintainer.demo import seed_demo
from gh_maintainer.models import Observation


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
        ["triage", "I_1", "planned", "--priority", "invalid"],
        ["triage", "I_1", "planned", "--category", "invalid"],
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


@pytest.mark.parametrize("command", ["sync", "watch"])
def test_request_pacing_options(monkeypatch, command: str) -> None:
    received = []

    async def run(args):
        received.append(args)
        return 0

    monkeypatch.setattr(cli, "run", run)
    runner = CliRunner()
    assert runner.invoke(cli.main, [command]).exit_code == 0
    assert received[-1].concurrency == 4
    assert received[-1].requests_per_second == 2
    result = runner.invoke(
        cli.main,
        [
            command,
            "--concurrency",
            "4",
            "--requests-per-second",
            "0.5",
        ],
    )
    assert result.exit_code == 0, result.output
    assert received[-1].requests_per_second == 0.5


@pytest.mark.parametrize("rate", ["0", "-1", "nan", "inf", "invalid"])
def test_invalid_request_rate_is_rejected_before_authentication(
    tmp_path, rate: str
) -> None:
    path = tmp_path / "unused.sqlite"
    result = CliRunner().invoke(
        cli.main,
        [
            "--db",
            str(path),
            "sync",
            "--requests-per-second",
            rate,
        ],
    )
    assert result.exit_code == 2
    assert not path.exists()


def test_prune_help(tmp_path: Path) -> None:
    path = tmp_path / "unused.sqlite"
    result = CliRunner().invoke(cli.main, ["--db", str(path), "prune", "--help"])
    assert result.exit_code == 0
    assert "--keep" in result.stdout
    assert "--days" in result.stdout
    assert "--vacuum" in result.stdout
    assert not path.exists()


@pytest.mark.parametrize(
    "options",
    [
        ["prune", "--keep", "-1"],
        ["prune", "--days", "-1"],
    ],
)
def test_invalid_prune_options(tmp_path: Path, options: list[str]) -> None:
    path = tmp_path / "unused.sqlite"
    result = CliRunner().invoke(cli.main, ["--db", str(path), *options])
    assert result.exit_code == 2
    assert not path.exists()


def test_cli_prune_and_vacuum(tmp_path: Path) -> None:
    db_path = str(tmp_path / "prune_test.sqlite")

    async def setup_db() -> int:
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
            count = await session.scalar(select(func.count()).select_from(Observation))
        await db.close()
        assert count is not None
        return count

    count_before = asyncio.run(setup_db())

    runner = CliRunner()
    result = runner.invoke(
        cli.main, ["--db", db_path, "prune", "--keep", "2", "--days", "5", "--vacuum"]
    )
    assert result.exit_code == 0, result.output
    assert "Pruned" in result.output

    async def check_count() -> int:
        db = Database(db_path)
        async with db.sessions() as session:
            count = await session.scalar(select(func.count()).select_from(Observation))
        await db.close()
        assert count is not None
        return count

    count_after = asyncio.run(check_count())
    assert count_after < count_before


@pytest.mark.anyio
async def test_database_vacuum_direct(tmp_path: Path) -> None:
    db = Database(tmp_path / "vacuum_test.sqlite")
    await db.initialize()
    try:
        await db.vacuum()
    finally:
        await db.close()


def test_cli_triage_priority_and_category(tmp_path: Path) -> None:
    db_path = str(tmp_path / "triage_cli.sqlite")

    async def setup_db() -> None:
        db = Database(db_path)
        await db.initialize()
        await seed_demo(db)
        await db.close()

    asyncio.run(setup_db())

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "--db",
            db_path,
            "triage",
            "DEMO_1_39",
            "planned",
            "--priority",
            "urgent",
            "--category",
            "bug_fix",
            "--note",
            "Fix this ASAP",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Local triage decision saved" in result.output

    # Inspect item with show --format json
    result = runner.invoke(
        cli.main,
        ["--db", db_path, "show", "DEMO_1_39", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    item = data["item"]
    assert item["priority"] == "urgent"
    assert item["category"] == "bug_fix"
    assert item["triaged"] is True
    assert item["note"] == "Fix this ASAP"

    # Filter with issues
    result = runner.invoke(
        cli.main,
        ["--db", db_path, "issues", "--priority", "urgent"],
    )
    assert result.exit_code == 0, result.output
    assert "DEMO_1_39" in result.output or "39" in result.output
