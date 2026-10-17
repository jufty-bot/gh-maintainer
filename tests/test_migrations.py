"""Adopt an independent legacy fixture without changing any stored source records."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from gh_maintainer.clock import parse_date
from gh_maintainer.database import Database
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.schemas import Disposition

from .fakes import AT, REPO, issue

pytestmark = pytest.mark.anyio
TABLES = ("metadata", "repos", "items", "changes", "triage", "runs")


def legacy_database(path: Path) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            (Path(__file__).parent / "fixtures/legacy.sql").read_text()
        )
        connection.executemany(
            "INSERT INTO metadata VALUES(?,?)", [("owner", "owner"), ("owner_id", "77")]
        )
        connection.execute(
            "INSERT INTO repos(id,name,url,archived,raw,synced_at,reconciled_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                "1",
                "owner/project",
                REPO["html_url"],
                0,
                json.dumps({**REPO, "owner": {"login": "owner"}}),
                AT,
                AT,
            ),
        )
        source = json.dumps(issue())
        connection.execute(
            "INSERT INTO items(id,repo_id,number,kind,state,title,url,updated_at,raw) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("I_1", "1", 1, "issue", "open", "Item 1", issue()["html_url"], AT, source),
        )
        connection.execute(
            "INSERT INTO triage VALUES(?,?,?,?,?)",
            ("I_1", "planned", "Preserve this note ✓", None, AT),
        )
        connection.execute(
            "INSERT INTO changes(item_id,observed_at,before_json,after_json) "
            "VALUES(?,?,?,?)",
            ("I_1", AT, None, json.dumps({"id": "I_1", "raw": source})),
        )
        connection.execute(
            "INSERT INTO runs(started_at,finished_at,status,requests) VALUES(?,?,?,?)",
            (AT, AT, "complete", 21),
        )
        return {
            table: connection.execute(
                f"SELECT * FROM {table}"  # noqa: S608 - frozen fixture table names
            ).fetchall()
            for table in TABLES
        }


async def test_adopt_legacy_preserves_every_value_and_is_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.sqlite"
    before = legacy_database(path)
    for _ in range(2):
        database = Database(path)
        try:
            await database.initialize()
            async with database.sessions() as session:
                data = await CatalogQueries(session, parse_date(AT)).portfolio()
            assert data.owner == "owner"
            assert data.items[0].disposition == Disposition.PLANNED
            assert data.items[0].note == "Preserve this note ✓"
            assert data.latest_run.source_calls == 21
        finally:
            await database.close()
        with closing(sqlite3.connect(path)) as connection, connection:
            assert before == {
                table: [
                    row[: len(before[table][0])]
                    for row in connection.execute(
                        f"SELECT * FROM {table}"  # noqa: S608 - frozen fixture table names
                    ).fetchall()
                ]
                for table in TABLES
            }
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("0003",)
            assert connection.execute("SELECT * FROM owners").fetchall() == [
                ("77", "owner", "User")
            ]
            assert connection.execute("SELECT * FROM repo_owners").fetchall() == [
                ("1", "77")
            ]


async def test_unknown_schema_is_rejected_without_partial_changes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unrelated.sqlite"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.execute("INSERT INTO unrelated VALUES('keep me')")
    database = Database(path)
    try:
        with pytest.raises(RuntimeError, match="Unrecognized"):
            await database.initialize()
    finally:
        await database.close()
    with closing(sqlite3.connect(path)) as connection, connection:
        assert connection.execute("SELECT value FROM unrelated").fetchone() == (
            "keep me",
        )
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("unrelated",)]
