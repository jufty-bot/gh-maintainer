# gh-maintainer

A self-hosted Python index and maintenance queue for repositories owned by one GitHub
account. SQLite stores source records, observed changes, successful sync checkpoints,
and local triage decisions. FastAPI serves a read-only dashboard.

```bash
uv sync --locked
# Authenticate using gh auth login or export GH_TOKEN.
uv run gh-maintainer sync
uv run gh-maintainer serve
```

Open http://127.0.0.1:8765. Run `gh-maintainer watch --interval 900` for regular updates,
`gh-maintainer report` for a Markdown digest, and `gh-maintainer triage --help` for local
decisions. Use `--db PATH` for a separate owner or an offline demo database.

The index follows repository listing pagination, retains closed and merged items,
refreshes open PR signals independently, and shows incomplete synchronization explicitly.
Its coverage is limited to repositories visible to the credential. This version indexes
personal ownership, item bodies and PR signal summaries; it does not yet ingest comments,
review threads, organizations, or webhooks.

See the [architecture and data contracts](architecture.md), the generated Python API
reference, and the [contributor guide](contributing.md).
