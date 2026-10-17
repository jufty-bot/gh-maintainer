"""Async engine/session lifecycle and transactional, packaged Alembic migrations."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

from aiosqlite import Connection as SQLiteConnection
from alembic import command
from alembic.config import Config
from platformdirs import user_data_path
from sqlalchemy import Connection, event
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def default_path() -> str:
    return os.getenv("GH_MAINTAINER_DB") or str(
        user_data_path("gh-maintainer", appauthor=False) / "maintainer.sqlite"
    )


class Database:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        url = URL.create("sqlite+aiosqlite", database=str(path))
        self.engine = create_async_engine(url, connect_args={"timeout": 5})
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self._ready = False
        self._initialize_lock = asyncio.Lock()
        self._configure_sqlite(self.engine)

    @staticmethod
    def _configure_sqlite(engine: AsyncEngine) -> None:
        @event.listens_for(engine.sync_engine, "connect")
        def configure(connection: object, _record: object) -> None:
            # SQLAlchemy's adapted DBAPI connection exposes these DBAPI attributes.
            connection.isolation_level = None  # type: ignore[attr-defined]
            cursor = connection.cursor()  # type: ignore[attr-defined]
            for pragma in (
                "PRAGMA foreign_keys=ON",
                "PRAGMA busy_timeout=5000",
            ):
                cursor.execute(pragma)
            cursor.close()
            connection.run_async(enable_wal)  # type: ignore[attr-defined]

        async def enable_wal(connection: SQLiteConnection) -> None:
            # Initial journal-mode changes can return SQLITE_BUSY immediately even
            # with busy_timeout, when independent processes open a fresh file.
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                try:
                    async with connection.execute("PRAGMA journal_mode=WAL"):
                        return
                except sqlite3.OperationalError as exc:
                    if (
                        "locked" not in str(exc).lower()
                        or asyncio.get_running_loop().time() >= deadline
                    ):
                        raise
                    await asyncio.sleep(0.05)

        @event.listens_for(engine.sync_engine, "begin")
        def begin(connection: Connection) -> None:
            # Explicit BEGIN also makes SQLite DDL rollback with failed migrations.
            mode = (
                "BEGIN IMMEDIATE"
                if connection.get_execution_options().get("migration")
                else "BEGIN"
            )
            connection.exec_driver_sql(mode)

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if self._ready:
                return
            async with self.engine.connect() as connection:
                await connection.execution_options(migration=True)
                async with connection.begin():
                    await connection.run_sync(self._upgrade)
            self._ready = True

    @staticmethod
    def _upgrade(connection: Connection) -> None:
        config = Config()
        config.set_main_option(
            "script_location", str(Path(__file__).parent / "migrations")
        )
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    async def close(self) -> None:
        await self.engine.dispose()
