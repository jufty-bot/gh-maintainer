
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
