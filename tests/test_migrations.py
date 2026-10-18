"""Adopt an independent legacy fixture without changing any stored source records."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection

from gh_maintainer.clock import parse_date
from gh_maintainer.database import Database
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.schemas import Disposition

from .fakes import AT, REPO, issue

pytestmark = pytest.mark.anyio
TABLES = ("metadata", "repos", "items", "changes", "triage", "runs")


@pytest.mark.parametrize(
    "old,new",
    [
        ("PRIMARY KEY", ""),
        (", UNIQUE(repo_id,number)", ""),
        (" REFERENCES repos(id)", ""),
        ("number INTEGER NOT NULL", "number TEXT NOT NULL"),
        ("repo_id TEXT NOT NULL", "repo_id TEXT"),
    ],
)
async def test_same_names_with_invalid_constraints_or_types_are_refused(
    tmp_path, old, new
):
    path = tmp_path / "malformed.sqlite"
    schema = (Path(__file__).parent / "fixtures/legacy.sql").read_text()
    assert old in schema
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(schema.replace(old, new))
        before = connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    database = Database(path)
    try:
        with pytest.raises(RuntimeError, match="Unrecognized"):
            await database.initialize()
    finally:
        await database.close()
    with closing(sqlite3.connect(path)) as connection:
        assert (
            connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            == before
        )


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
            ).fetchone() == ("0005",)
            indexes = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='changes'"
            ).fetchall()
            assert ("changes_item_id",) in indexes
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


async def test_activity_upgrade_from_0003_retains_processing_and_analysis(
    tmp_path: Path,
) -> None:
    path = tmp_path / "existing.sqlite"
    legacy_database(path)
    database = Database(path)

    def previous_schema(connection: Connection) -> None:
        config = Config()
        config.set_main_option("script_location", "src/gh_maintainer/migrations")
        config.attributes["connection"] = connection
        command.upgrade(config, "0003")

    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(previous_schema)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE items SET revision=7")
            connection.execute(
                "INSERT INTO summaries VALUES(?,?,?,?,?,?)",
                ("I_1", 7, "agent", "Keep analysis", AT, "Preserve this summary"),
            )
            connection.execute(
                "INSERT INTO processing VALUES(?,?,?,?)", ("I_1", 7, AT, "agent")
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(repos)")}
            assert "activity" not in columns
        await database.initialize()
        async with database.sessions() as session:
            queries = CatalogQueries(session)
            item = (await queries.show("I_1")).item
            assert item.revision == 7 and item.processed_revision == 7
            assert item.summary.text == "Preserve this summary"
            repo = (await queries.overview()).repos[0]
            assert repo.activity is None and repo.activity_synced_at is None
            assert repo.activity_error is None and repo.activity_stale
            assert repo.raw["full_name"] == "owner/project"
    finally:
        await database.close()


async def test_changes_index_upgrade_from_0004(tmp_path: Path) -> None:
    path = tmp_path / "index_upgrade.sqlite"
    legacy_database(path)
    database = Database(path)

    def schema_0004(connection: Connection) -> None:
        config = Config()
        config.set_main_option("script_location", "src/gh_maintainer/migrations")
        config.attributes["connection"] = connection
        command.upgrade(config, "0004")

    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(schema_0004)
        with closing(sqlite3.connect(path)) as connection, connection:
            indexes = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='changes'"
            ).fetchall()
            assert ("changes_item_id",) not in indexes
        await database.initialize()
        with closing(sqlite3.connect(path)) as connection, connection:
            indexes = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='changes'"
            ).fetchall()
            assert ("changes_item_id",) in indexes
    finally:
        await database.close()
