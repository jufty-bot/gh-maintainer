"""Durable source records, checkpoints, observations and local triage."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

Json = dict[str, Any]
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(
 id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
 status TEXT NOT NULL, error TEXT, requests INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS repos(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, archived INTEGER NOT NULL,
 present INTEGER NOT NULL DEFAULT 1, raw TEXT NOT NULL, synced_at TEXT,
 reconciled_at TEXT, signals_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS items(
 id TEXT PRIMARY KEY, repo_id TEXT NOT NULL REFERENCES repos(id),
 number INTEGER NOT NULL,
 kind TEXT NOT NULL, state TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
 updated_at TEXT NOT NULL, available INTEGER NOT NULL DEFAULT 1,
 raw TEXT NOT NULL, pr TEXT, signals TEXT, signals_at TEXT, UNIQUE(repo_id,number)
);
CREATE INDEX IF NOT EXISTS items_queue ON items(state,kind,updated_at);
CREATE TABLE IF NOT EXISTS changes(
 id INTEGER PRIMARY KEY, item_id TEXT NOT NULL, observed_at TEXT NOT NULL,
 before_json TEXT, after_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS triage(
 item_id TEXT PRIMARY KEY REFERENCES items(id), disposition TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '', snoozed_until TEXT, updated_at TEXT NOT NULL
);
"""
FIELDS = (
    "id",
    "repo_id",
    "number",
    "kind",
    "state",
    "title",
    "url",
    "updated_at",
    "available",
    "raw",
    "pr",
    "signals",
    "signals_at",
)


class Store:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(str(path), isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    @contextmanager
    def sync_lock(self) -> Iterator[None]:
        """A renewable lease prevents overlapping syncs, including across containers."""
        token = str(uuid.uuid4())
        with self.transaction():
            row = self.db.execute(
                "SELECT value FROM metadata WHERE key='sync_lock'"
            ).fetchone()
            if row and json.loads(row[0])["expires"] > time.time():
                raise RuntimeError(
                    "Another sync is running; retry after its lease expires."
                )
            self.db.execute(
                "INSERT OR REPLACE INTO metadata VALUES('sync_lock',?)",
                (json.dumps({"token": token, "expires": time.time() + 300}),),
            )
        self._lease = token
        try:
            yield
        finally:
            with self.transaction():
                row = self.db.execute(
                    "SELECT value FROM metadata WHERE key='sync_lock'"
                ).fetchone()
                if row and json.loads(row[0])["token"] == token:
                    self.db.execute("DELETE FROM metadata WHERE key='sync_lock'")

    def renew_lease(self) -> None:
        with self.transaction():
            row = self.db.execute(
                "SELECT value FROM metadata WHERE key='sync_lock'"
            ).fetchone()
            if not row or json.loads(row[0])["token"] != self._lease:
                raise RuntimeError("Sync lease was lost.")
            self.db.execute(
                "UPDATE metadata SET value=? WHERE key='sync_lock'",
                (json.dumps({"token": self._lease, "expires": time.time() + 300}),),
            )

    def owner(self) -> str | None:
        row = self.db.execute("SELECT value FROM metadata WHERE key='owner'").fetchone()
        return str(row[0]) if row else None

    def set_owner(self, owner: str, owner_id: str | None = None) -> None:
        current = self.owner()
        row = self.db.execute(
            "SELECT value FROM metadata WHERE key='owner_id'"
        ).fetchone()
        same_identity = row and owner_id and row[0] == owner_id
        if (row and owner_id and row[0] != owner_id) or (
            current and current.lower() != owner.lower() and not same_identity
        ):
            raise ValueError(
                "This database belongs to a different owner; use another --db."
            )
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES('owner',?)", (owner,))
        if owner_id:
            self.db.execute(
                "INSERT OR REPLACE INTO metadata VALUES('owner_id',?)", (owner_id,)
            )

    def discover(self, repos: list[Json]) -> None:
        with self.transaction():
            self.db.execute("UPDATE repos SET present=0")
            for repo in repos:
                if (
                    not repo.get("id")
                    or not repo.get("full_name")
                    or not repo.get("html_url")
                ):
                    raise ValueError("Malformed GitHub repository.")
                self.db.execute(
                    """INSERT INTO repos(id,name,url,archived,raw) VALUES(?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,url=excluded.url,
                    archived=excluded.archived,raw=excluded.raw,present=1""",
                    (
                        str(repo["id"]),
                        repo["full_name"],
                        repo["html_url"],
                        bool(repo.get("archived")),
                        json.dumps(repo),
                    ),
                )

    def repo(self, repo_id: str) -> Json:
        return dict(
            self.db.execute("SELECT * FROM repos WHERE id=?", (repo_id,)).fetchone()
        )

    def items(self, repo_id: str) -> list[Json]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM items WHERE repo_id=?", (repo_id,)
            )
        ]

    def commit_repo(
        self,
        repo_id: str,
        rows: list[Json],
        started_at: str,
        full: bool,
        signals_error: str | None,
    ) -> None:
        """Commit the whole repository and its checkpoint together."""
        with self.transaction():
            previous = {item["id"]: item for item in self.items(repo_id)}
            if full:
                self.db.execute(
                    "UPDATE items SET available=0 WHERE repo_id=?", (repo_id,)
                )
            for row in rows:
                old = self.db.execute(
                    "SELECT * FROM items WHERE id=?", (row["id"],)
                ).fetchone()
                before = previous.get(row["id"], dict(old) if old else None)
                compare = [field for field in FIELDS if field != "signals_at"]
                if not before or any(before[key] != row[key] for key in compare):
                    self.db.execute(
                        "INSERT INTO changes"
                        "(item_id,observed_at,before_json,after_json) VALUES(?,?,?,?)",
                        (
                            row["id"],
                            started_at,
                            json.dumps(before) if before else None,
                            json.dumps(row),
                        ),
                    )
                # FIELDS is a fixed internal constant, never user input.
                self.db.execute(
                    f"INSERT INTO items({','.join(FIELDS)}) "
                    f"VALUES({','.join('?' for _ in FIELDS)}) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    + ",".join(
                        f"{key}=excluded.{key}" for key in FIELDS if key != "id"
                    ),
                    tuple(row[key] for key in FIELDS),
                )
            self.db.execute(
                """UPDATE repos SET synced_at=?,
                reconciled_at=CASE WHEN ? THEN ? ELSE reconciled_at END,
                signals_at=CASE WHEN ? IS NULL THEN ? ELSE signals_at END,
                error=? WHERE id=?""",
                (
                    started_at,
                    full,
                    started_at,
                    signals_error,
                    started_at,
                    signals_error,
                    repo_id,
                ),
            )

    def fail_repo(self, repo_id: str, error: str) -> None:
        self.db.execute("UPDATE repos SET error=? WHERE id=?", (error, repo_id))

    def start_run(self, at: str) -> int:
        cursor = self.db.execute(
            "INSERT INTO runs(started_at,status) VALUES(?,'running')", (at,)
        )
        return int(cursor.lastrowid or 0)

    def finish_run(
        self, run_id: int, status: str, at: str, requests: int, error: str | None = None
    ) -> None:
        self.db.execute(
            "UPDATE runs SET finished_at=?,status=?,requests=?,error=? WHERE id=?",
            (at, status, requests, error, run_id),
        )

    def triage(
        self, item_id: str, disposition: str, note: str, until: str | None, at: str
    ) -> None:
        if disposition not in {"inbox", "planned", "waiting", "snoozed", "done"}:
            raise ValueError("Invalid triage disposition.")
        if not self.db.execute(
            "SELECT id FROM items WHERE id=?", (item_id,)
        ).fetchone():
            raise ValueError("Unknown item ID.")
        self.db.execute(
            """INSERT INTO triage VALUES(?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET
            disposition=excluded.disposition,note=excluded.note,
            snoozed_until=excluded.snoozed_until,updated_at=excluded.updated_at""",
            (item_id, disposition, note, until, at),
        )
