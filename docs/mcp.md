# MCP server

MCP clients can call the CLI's maintenance services through the official `mcp` Python SDK
over Streamable HTTP or stdio. The FastAPI application serves the dashboard, REST API, and
MCP HTTP endpoint together.
Both transports register identical tools and share database initialization, migrations,
watcher cleanup, revision guards, policy validation, and sync leases.

## Start and connect

```bash
uv run gh-maintainer mcp                   # stdio (default)
uv run gh-maintainer serve                 # Dashboard, REST API, and MCP on port 8765
uv run gh-maintainer mcp --transport http
uv run gh-maintainer --db .data/demo.sqlite mcp
```

Stdio is the default. Select HTTP explicitly with `--transport http`, then connect to
**http://127.0.0.1:8766/mcp**, or use **http://127.0.0.1:8765/mcp** with the existing
`serve` command. Both HTTP commands run the same FastAPI app: the dashboard is at `/`,
REST routes at `/api/*`, API docs at `/api/docs`, and MCP at `/mcp`. They share a database,
startup migrations, and background watcher cleanup. `--host` and `--port` configure
the listener. Clients supporting HTTP URL configuration can use:

```json
{
  "mcpServers": {
    "gh-maintainer": {"url": "http://127.0.0.1:8766/mcp"}
  }
}
```

For stdio, the client launches the installed command as a subprocess:

```json
{
  "mcpServers": {
    "gh-maintainer": {
      "command": "gh-maintainer",
      "args": ["--config", "/absolute/path/maintainer.yaml", "mcp"]
    }
  }
}
```

Stdio starts no HTTP listener. Stdout carries only MCP messages; diagnostics and sync
logs go to stderr. Closing stdin shuts down the server and its background watcher.
`--host` and `--port` apply only to HTTP.

Database selection follows the [CLI](cli.md): explicit `--db`, config `db`,
`GH_MAINTAINER_DB`, then the platform default. A JSON/YAML configuration can include
`"mcp": {"transport": "stdio", "host": "127.0.0.1", "port": 8766}`. Tool calls use
the database selected at startup, without accepting arbitrary index paths.

The selected configuration also supplies operation defaults to MCP tools, for both
`mcp` transports and `serve`. Explicit tool arguments override those defaults;
partial `options` and `request` objects retain configured values for omitted fields.
Lists replace configured lists, and explicit empty strings, `false`, or `[]` remain
explicit overrides. `repositories` supplies sync/watch selections unless that
command has its own `repo` default. Sync and watch settings remain independent.
`triage` defaults to `planned`, matching the CLI. Discovery schemas advertise the
effective defaults, including configured positional arguments. Restart the server
to load configuration changes. Dashboard REST routes retain their own request defaults.
See [configuration](configuration.md) for the JSON/YAML schema.

GitHub credentials are resolved on the server through `GH_TOKEN`, `GITHUB_TOKEN`, or
`gh auth token` when syncing. Local reads and maintenance need no GitHub token.
Live context uses the existing authenticated `gh` integration.

## Tools and CLI mapping

| CLI | MCP tool | Options and behavior |
| --- | --- | --- |
| `sync` | `sync` | `full=false`, optional `repo` list or `owner` (mutually exclusive), `concurrency=4`, `requests_per_second=2`. Returns structured coverage/errors/source call counts. |
| `watch` | `watch`, `watch_status`, `stop_watch` | Sync options plus `interval=900` (minimum 60). Start returns immediately; status includes options, latest result and fatal error. Stop awaits cleanup and is safe to repeat. |
| `report` | `report` | `format="markdown"` or `"json"`; returns report text. Optional `output` writes a UTF-8 file on the server, overwriting an existing file. |
| `triage` | `triage` | `item_id`, `disposition="planned"`, `note`, optional `until`, `revision`, `priority`, `category`, `policy`. `finish_review=false`; when true, requires `revision` and atomically acknowledges it with `actor="manual"`. Snoozes require a future ISO timestamp including timezone. |
| `prs`, `issues`, `status` | Same names | Optional `options` object with the CLI's filters and pagination. PRs/issues default to open; status includes all PR states. |
| `show`, `repos`, `comments`, `changes` | Same names | Structured query results. Comments and changes accept `limit`/`offset`. Changes default to activity since processing; `all` and `since` are mutually exclusive. |
| `classify`, `summarize`, `rank` | Same names | Save supplied `labels`, `text`, or finite `score`, with `producer`, `reason`, optional `revision` and `policy`. Summary text is literal; supply multiline text directly instead of the CLI's stdin `-` convention. |
| `mark-processed` | `mark_processed` | `item_id`, optional `revision`, `actor`, `policy`. Requires complete item/comment coverage. |
| `policy` | `policy` | `repo`, `file`, `initialize=false`. Initialization creates a starter file and refuses overwrite. |
| `skills` | `skills` | List bundled skills or copy them to a server directory with `install`, without overwriting. |
| `context` | `context` | `item_id` and optional `request` object: section, pagination, path/ref, job ID, and evidence size limits. |
| `demo`, `migrate` | Same names | Seed an empty index; apply packaged migrations (also run at startup). |
| `prune` | `prune` | `keep=50`, optional `days`, `vacuum=false`; returns deleted observation count. |
| `db path` | `db_path` | Return the selected absolute index path. Stop MCP before using CLI `db delete --yes`; active-index deletion is not exposed as a tool. |
| `serve` | `overview`, `list_items`, `item_detail` | Dashboard query helpers. `serve` hosts the dashboard, REST API, and all MCP tools on the same port. |

Item references accept issue node IDs or `OWNER/REPO#NUMBER`, including PRs.
`list_items` additionally provides flat dashboard filters (`q`, `kind`, `state`, `repo`,
`queue`, `limit`, `offset`). Use `prs`/`issues`/`status` with an `options` object for the
CLI's full filtering contract, including author/date/priority/category/processing.
Lists paginate in SQL, with `limit` 1–500 and nonnegative `offset`.

Structured JSON replaces CLI `--format text|json` for query and analysis tools;
`report` retains its Markdown/JSON choice. Reading, analysis, and triage do not mark an
item processed: call `mark_processed` explicitly after inspecting its current revision.
The explicit `triage(finish_review=true, revision=N)` alternative saves the decision
and acknowledgement together; policy must permit both triage and processing.
Policy, installation, and report paths refer to the server filesystem.

## Sync progress and cancellation

Clients can request [MCP progress notifications](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/progress)
by including a `progressToken` in a sync request's metadata. The official SDK client
also supports `client.call_tool("sync", progress_callback=callback)`. Notifications
report discovery, repository activity, fetched item/comment batches, PR refreshes,
repository commits, and the final
complete or partial outcome. Progress values count stages and increase strictly;
there is no percentage or fixed total. No notifications are sent without a requested
token, and notifications stop when the request ends.

HTTP uses streaming SSE responses and SDK-managed legacy sessions so progress and
peer cancellation work while a sync is in flight. Both HTTP and stdio clients can
cancel a sync. Cancellation drains source workers, records a failed run, releases
the sync lease, and closes the source, leaving the server ready for another sync. Previously
committed repositories remain; incomplete repositories do not advance checkpoints.
A cancelled request has no successful sync result. Cancelling a `watch` start request
after it has returned does not stop the background watcher; use `stop_watch`.

## Failures and lifecycle

Invalid arguments, unknown items, stale revisions, policy violations, and runtime
failures return MCP tool errors. Partial sync coverage sets `isError=true` with the
full sync result retained in `structuredContent`. Incomplete live context does the
same with its evidence packet. Inspect coverage instead of treating missing data as
success or deletion.

One watcher runs per server process. Duplicate starts fail. Partial syncs continue
polling; fatal errors stop the watcher. The watcher survives HTTP client disconnects
and stops at server shutdown. Use a single HTTP worker for watcher management.
Sync and pruning share the CLI's database lease: overlapping writers fail explicitly.

Tool definitions use the official SDK's `MCPServer` and call the existing maintenance
services.
They are mounted into FastAPI rather than generated from its HTTP routes, preserving
CLI workflows such as processing, policy, and analysis that have no REST endpoint.
The project depends directly on `mcp>=2.3,<3`; both current and legacy HTTP protocol
connections are exercised with SDK clients against the combined app. Contract tests
compare CLI options/defaults/constraints with discovery and compare results against
separate copies of the same index. Intentional differences include structured MCP
results, nested query/context inputs, literal summary text, background watcher control,
and selecting the database once at startup. Database deletion remains a CLI operation.

No tool changes GitHub state. The HTTP server has no user authentication and can
modify local decisions/history and write files. Keep it local or behind an authenticated
private proxy, as with the dashboard.
