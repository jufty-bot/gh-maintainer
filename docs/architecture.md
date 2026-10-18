# Architecture and data contracts

This page describes the current implementation. The [scope and roadmap](roadmap.md)
defines the intended CLI baseline, remaining ingestion and analysis work, and the later
focus on agent skills.
For orientation, read [project context](project-context.md); for rationale and tradeoffs,
read the [decision log](decisions.md). Proposed context/skill work is described in the
[agent skills roadmap](agent-skills.md). The live context adapter and bundled workflows
are described in [technical context](context.md) and [skill usage](skills.md).

```mermaid
flowchart LR
    G[GitHub] --> P[HTTPX async adapter / bounded requests]
    P --> S[Async sync service]
    S --> R[Catalog repository + journal]
    R --> D[(SQLAlchemy + aiosqlite)]
    D --> Q[SQL-backed catalog queries]
    Q --> C[CLI lists / details / conversations / activity]
    Q --> A[Async FastAPI dashboard]
    Q --> E[Typed Markdown / JSON export]
    T[Triage service] --> D
```

## Boundaries

The official `mcp` Python SDK exposes the CLI services over Streamable HTTP or stdio.
Stdio is the default for `mcp`; `serve` hosts the dashboard, REST API, and MCP at `/mcp` together.
`mcp --transport http` starts the same combined app on the configured MCP port.
`mcp.py` owns transport setup, tool registration, database lifecycle, and a single background
watcher; `mcp_commands.py` exposes query, analysis, policy, skill, and live context contracts.
Both transports use the SDK's `MCPServer` and identical tool schemas. `web.py` routes
`/mcp` to the MCP HTTP ASGI app after dashboard/REST routes, running the SDK session
manager alongside the shared runtime's database lifecycle. Stdio reserves stdout for protocol traffic
and sends logs to stderr. Shutdown cancels the watcher, waits for source/lease cleanup,
then disposes its owned database. No GitHub credential is resolved until a sync runs.

`mcp_contracts.py` applies the selected CLI command defaults to SDK signatures and
nested request models, keeping tool validation and discovery in agreement. Explicit
tool arguments take precedence. Triage defaults to `planned`, matching the CLI.
Explicit tool definitions retain CLI operations without requiring matching REST routes.
The project depends directly on `mcp>=2.3,<3` using the tested release in `uv.lock`.
Transport tests use the SDK's clients and HTTP/stdio transports, including
legacy and current HTTP connections against the same combined application. HTTP
streams SSE responses and retains legacy SDK sessions for progress and cancellation.
Sync progress uses an awaited observer and the request's SDK context; notifications
count completed stages without claiming a percentage. Cancellation shields worker
drain, failed-run journaling, lease release, and source closure from interruption.

Tools call the existing query/write services, preserving revision and policy guards.
Partial sync or context coverage returns MCP `isError=true` with the full structured result.
Watcher state belongs to the server process, survives HTTP disconnects, and stops at server
shutdown or stdin closure. Use one HTTP worker for watcher control. Database deletion
requires stopping MCP and using the CLI. See [tool contracts](mcp.md) and
[D19](decisions.md#d19-share-maintenance-services-across-mcp-transports).

| Module              | Responsibility                                                                                   |
| ------------------- | ------------------------------------------------------------------------------------------------ |
| `schemas.py`        | Validated source snapshots, state/disposition enums, explicit API/export contracts               |
| `models.py`         | Typed SQLAlchemy records, JSON payloads, aware UTC timestamps                                    |
| `database.py`       | Engine/session lifecycle, SQLite pragmas, serialized Alembic upgrades                            |
| `migrations/`       | Packaged, versioned schema changes and legacy adoption                                           |
| `github.py`         | Source protocol and HTTPX adapter; bounded async requests, retries, authentication and pagination |
| `sync.py`           | Discovery, backfill windows, reconciliation, PR refresh and error policy                         |
| `repositories.py`   | Catalog writes, fenced sync lease, run journal, observation pruning, local analysis and processing decisions |
| `queries.py`        | SQL filtering, totals, queue ranking, pagination and targeted detail loading                     |
| `policies.py`       | Pure priority and queue rules over typed snapshots                                               |
| `config.py`         | Typed JSON/YAML CLI defaults and JSON Schema; repository policy validation and local-write guards |
| `context.py`        | On-demand read-only gh source adapter; commit-bound evidence, continuation and limits |
| `skillpack.py`      | Locate packaged resources and copy complete skills without overwriting |
| `report.py`         | Markdown rendering over a typed portfolio; no SQL or SDK access                                  |
| `web.py` / `cli.py` | Thin FastAPI presentation and Click command entry points                                         |
| `clock.py`          | Shared timezone-aware clock and conversion helpers                                               |

Pydantic validates data at the source boundary. Application code receives typed snapshots;
ORM objects remain within persistence/query modules. JSON columns retain source documents
without manual JSON string encoding across services. State and triage disposition are
separate enums. Item and repository URLs and names are display attributes, not identities.

One database can contain repositories from multiple user and organization owners. `owners`
stores stable account IDs and display logins; `repo_owners` records current repository
membership. Successful complete inventories reconcile presence only within that owner's
scope. Public-only inventories reconcile only known-public records; explicit lists only
upsert their resolved repositories. Login changes and repository transfers retain IDs,
source records, and local decisions. PR-specific node IDs come from PR details for GraphQL
enrichment. Legacy owner metadata remains stored for compatibility.
Discovery compares incoming metadata and memberships in bounded SQL batches. Unchanged
owners/repositories/memberships are not rewritten. A complete scoped inventory marks only
previously present repositories missing from that inventory absent; it does not
temporarily mark every repository absent.
Explicit repository names resolve through bounded workers with inventory-order error
reporting; failed selections are recorded after those read requests finish.

## Database location and management

`database.default_path()` selects `GH_MAINTAINER_DB` when nonempty, otherwise
`platformdirs.user_data_path("gh-maintainer", appauthor=False) / "maintainer.sqlite"`.
Both the application CLI and standalone Alembic commands use it; CLI `--db` takes
precedence. Resolving the default does not create directories. `Database` creates the
parent when a data command opens the index.

`db`/`db path` prints the absolute selected path, and `db delete --yes` removes that file
and its SQLite sidecars without initializing the engine or applying migrations. Stop
other processes using the file first. This is file management, not a schema migration:
old working-directory indexes stay in place and can still be selected explicitly.
See [database commands](cli.md#manage-the-database) and [D10](decisions.md#d10-platform-user-data-directory-and-explicit-deletion).

## Data model

`models.py` describes the current ORM schema; packaged migrations define its creation and
upgrades. `schemas.py` defines validated source and output contracts. This table explains
the roles; it does not replace those definitions.

| Table             | Identity/link                                         | Purpose                                                                                                            |
| ----------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `metadata`        | String key                                            | Legacy owner settings and the renewable sync lease                                                                 |
| `owners`          | Stable account ID                                     | User/organization display login and kind                                                                           |
| `repo_owners`     | Repository ID → owner ID                              | Current membership for scoped inventory reconciliation                                                             |
| `repos`           | Stable repository ID                                  | Raw metadata, presence, archive flag, item/comment/signal checkpoints, reconciliation time, and latest error; latest repository activity with independent observation time/error |
| `items`           | Issue node ID; unique repository ID + number          | Normalized issue/PR identity and state, raw issue/PR payloads, signal snapshot/time, availability, source revision |
| `comments`        | Comment/review node ID; parent repository ID + number | Conversation/inline/review content, author, timestamps, raw reply/line context, availability                       |
| `changes`         | Increasing local observation ID; item ID              | Event kind, local revision when known, observation time, before/after source snapshots                             |
| `triage`          | One row per item                                      | Disposition, note, snooze deadline, decision time, and inspected revision                                          |
| `summaries`       | One row per item                                      | Latest caller-supplied summary and analysis provenance                                                             |
| `classifications` | One row per item                                      | Latest local label set and analysis provenance                                                                     |
| `rankings`        | One row per item                                      | Latest fixed numeric rank and analysis provenance                                                                  |
| `processing`      | One row per item                                      | Shared acknowledged revision, actor, and check time                                                                |
| `runs`            | Increasing local run ID                               | Start/finish, complete/partial/failed status, errors, and source-call telemetry                                    |

Analysis provenance is `source_revision`, `producer`, `reason`, and `updated_at`. A new
save replaces the previous result of that type; producer is not part of the primary key.
Item/comment snapshots retain history through observations, but analysis/triage writes do not
currently have a separate append-only action history.

Comments have a composite foreign key to their parent item's repository/number pair and
an index for conversation ordering. Item availability and repository presence are retained
flags, not deletion assertions. `changes` payloads can describe either an item or a comment;
consumers must inspect the event kind rather than assume every snapshot has item fields.
Legacy observations have no inferred revision. Pending review timestamps can be null.

JSON columns store parsed source documents. Normalized dates use `UTCDateTime`, which
persists canonical UTC text for compatibility with the original SQLite schema and returns
aware datetimes. Source URLs and display names can change without becoming identities.

### Repository activity snapshots

Migration `0004` adds nullable `activity` JSON, `activity_synced_at` UTC text and
`activity_error` text to `repos`, preserving existing raw payloads, revisions, analysis,
processing and history. `RepositoryActivity` validates default-branch name/tip, designated
latest release, and newest-created PR/issue summaries; the
[CLI contract](cli.md#repository-activity) defines fields and selection semantics.
`repo_snapshot` validates stored JSON and derives `activity_stale` from missing successful
coverage, an activity error, or repository absence. It has no implicit age TTL.

After discovery, the source fetches aliases for up to 20 repositories per GraphQL request.
Each alias selects a default-branch commit and latest release plus one PR and one issue,
ordered by creation time across all states. Names are GraphQL variables and returned
numeric repository identities must match the stable indexed IDs. Required nullable fields
distinguish an empty source from a missing/partial payload. Errors with known alias paths
invalidate only that repository's summary; global errors invalidate the batch.

Activity fetches occur outside database transactions and commit in fenced bounded batches
before conversation ingestion. Success replaces the latest summary and advances its own
observation time, including observed null entries. Failure retains prior data/time and
records an error, making the run partial; it never clears last-known values or advances
item/comment checkpoints. Activity errors contribute to the overview's incomplete count.
They do not invalidate item analysis, whose dependencies remain indexed item/conversation
coverage. Repository activity snapshots do not create item revisions or item observations.
This retains one latest snapshot, with raw evidence, rather than an activity event history.

## Async lifecycle and transactions

The application owns one async engine/session factory, with a fresh `AsyncSession` per
operation or request. Sessions are never shared across concurrent tasks. API startup and
CLI initialization run Alembic once per engine; request handlers do not initialize schemas.

One HTTPX `AsyncClient` pools connections across bounded repository workers. PR details
and review bodies also use bounded workers, with a shared semaphore limiting all HTTP
requests. A capacity-one `aiolimiter.AsyncLimiter` spaces every HTTP attempt (including
pagination, redirects and retries) without an initial burst. Defaults are four workers
and two request starts per second, configured independently through `--concurrency` and
`--requests-per-second` on `sync` and `watch`. Only same-origin HTTPS pagination and
redirects are followed. Network I/O never holds a database transaction.

An admission lock serializes permits, with network I/O outside that lock. Workers recheck
cooldowns and primary budgets after permit waits. REST and GraphQL budgets are tracked
separately; at 10% remaining or below, additional pacing spreads the remaining allowance
until reset. Out-of-order responses cannot replenish a window's allowance. GraphQL HTTP
200 rate-limit errors trigger the shared cooldown; ordinary permission errors do not.
Budget and cooldown coordination remains local to this source instance.

Incremental catalog reads select incoming item IDs and available open PR snapshots,
without loading overlapping IDs twice. Review coverage loads only the numbers of other
available PRs. Commits read incoming comment IDs and existing available reviews in the
fully refreshed PR scopes, plus missing comment parents; full reconciliation still loads
all historical records. An empty incremental comment scope needs no comment query.
Unchanged closed PRs reuse
cached details, preserving merged state. Review bodies continue to be fully listed each
sync because GitHub offers no incremental review window. Unexpected failures and
cancellation cancel and await worker tasks before journal completion and lease release.
Expected repository errors remain isolated, with deterministic inventory-order reporting.

Repositories are fetched in windows of at most `--concurrency` entries. Each window shares
GraphQL signal requests of at most 20 PR nodes across repositories, then commits repositories
sequentially because SQLite has one writer. The window's staged snapshots are released
before fetching the next window; memory does not grow with the whole selected portfolio.
A slow repository can delay the rest of its window. This bounded approach trades that
latency for fewer requests and avoids a background batching service.

The signal query reads `headRefOid` and `statusCheckRollup` directly from `PullRequest`;
the adapter preserves the existing stored `commits.nodes[].commit` signal shape. Each
failed signal batch marks only repositories represented in that batch partial. If any
batch for one repository fails, all of that repository's previous signals and observation
times are retained, including PRs from successful batches. Other repositories can commit
fresh signals. A failed item/comment/review/detail fetch excludes its repository from
signal batching and leaves its source checkpoints unchanged.

GitHub batches are staged as validated snapshots. The catalog commits one repository's
items, comments, observations, availability and checkpoints in one short transaction. A failed page
or detail fetch leaves that repository's checkpoint unchanged. Failed GraphQL enrichment
can commit usable item data while retaining the original signal timestamp and marking
coverage incomplete. Effective signals become unknown when their head or repository
freshness is invalid.

Issue/inline comment feeds use their own overlapped checkpoint. Review bodies are listed
for every known available PR on each sync, including closed and merged PRs; their endpoint
has no incremental filter. Full reconciliation retains missing comments as unavailable.
Raw comment payloads preserve reply IDs and line context. Failed conversation fetches do
not advance either repository checkpoint. GraphQL thread resolution is outside this index.

The SQLite lease uses an atomic conditional upsert, a heartbeat in an independent session,
and a token check inside catalog write transactions. An expired worker cannot overwrite a
new worker's results. Interrupted run records are marked on the next successful lease
acquisition. Request sessions can read while the worker fetches GitHub data. SQLite remains
a single-writer database; aiosqlite provides event-loop integration, not parallel writers.

## One sync through the system

1. `cli.py` resolves credentials only for `sync` / `watch`, then creates the source and
   service. The service claims a lease and starts a run record.
2. The source resolves explicit repository names or finishes the selected owner's inventory.
   Discovery commits owner/repository metadata with scoped presence reconciliation. Explicit
   lookup failures are recorded per selection; an incomplete owner inventory is not applied.
   Bounded GraphQL activity queries then commit the latest commit/release/PR/issue summaries
   with their independent observation times/errors.
3. For each repository, `SyncService._repository` chooses full or incremental item/comment
   windows from the last successful checkpoints. It stages validated source snapshots.
4. It fetches comment feeds, resolves previously unknown parents, and refreshes review
   bodies. Open PRs are also refreshed even when no issue timestamp changed. PR detail
   requests provide the PR node IDs and head context used for signal enrichment shared
   across the current bounded repository window.
5. The catalog commits items, comments, observed changes, availability, source revisions,
   and checkpoints atomically. Missing source records are reconciled only from the relevant
   successful complete listing. Local analysis, triage, and processing rows are preserved.
6. The journal finishes the run with coverage and errors; CLI JSON reports complete or
   partial status. `watch` sleeps and repeats; each run rediscovers its selection.

| Failure                                     | Result                                                                                                        |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Owner inventory fails midway                | Run fails; the staged inventory is not applied                                                                |
| Item/comment/review page or PR detail fails | Repository source updates/checkpoints roll back, error is recorded, other repositories can continue           |
| GraphQL signals fail                        | Usable item/comment data commits; prior signal data/time is retained and effectively unknown; run is partial  |
| Explicit repository is inaccessible         | Existing source data/checkpoint is retained, matching repository error is recorded, other selections continue |
| Worker loses its lease                      | Fenced catalog writes fail; an expired worker cannot overwrite a new worker                                   |
| Process stops during fetch                  | Staged data is uncommitted; the next run can retry from stored checkpoints                                    |

Defaults are declared in code: five-minute overlap and seven-day reconciliation in
`sync.py`; a five-minute lease with 30-second heartbeat in `repositories.py`; 900-second
watch interval (minimum 60) in `cli.py`. Server cooldowns and budget waits beyond 30 seconds
are deferred to a later sync. `source_calls` counts HTTP attempts, including pagination,
redirects and retries; historical SDK-operation counts retain their original meaning.

The sync service emits structlog events with run and repository context, fetch progress,
counts, elapsed time, and errors. The CLI configures human-readable console rendering on
stderr for `sync` and `watch`; JSON command results remain on stdout. Repository completion
events follow successful commits, and partial runs emit a warning.

## Querying and triage

The item endpoint executes a count and a SELECT with SQL filtering, ordering, LIMIT and
OFFSET. Overview counts are SQL aggregates; item details only load the requested item and
its latest observations. Full JSON/Markdown exports intentionally read the whole portfolio.

The pure policy adds explicit reasons and scores: urgent priority 100, high priority 50,
medium priority 25, failing checks 80, conflicts 70, changes requested 60, required review 50,
30-day inactivity 30, and unassigned issues 20. Missing checks are unknown. Categories are
inferred from labels, Conventional Commits titles, bot authors, and issue/PR kind, and can be
overridden during triage. SQL expressions implement the same rules for ranking and category
filtering; parity tests compare SQL results with pure policy evaluations.

Local triage notes, priority, and category survive synchronization. A snooze requires a future aware timestamp and
expires in both SQL and presentation rules. An item marked locally done returns to the
inbox when its source update time advances. Results can be filtered by author, date range,
category, priority, and triaged status. GitHub state is never changed by local triage.

Each item also has a monotonic source revision. Meaningful item/signal changes and comment
changes increment it once per sync; freshness-only polling does not. Observations retain
their event kind, revision, and before/after snapshots. A local done decision records its
source revision and returns to the inbox on later source activity, including a push or
comment edit without a parent timestamp change. Legacy triage keeps its timestamp fallback.

Revision comparison reduces embedded account profiles (`user`, `assignee`, `merged_by`,
`closed_by`, `editor`, `assignees`, and `requested_reviewers`) to ID/node ID/login/type.
PR head/base repository profiles compare ID/node ID/full name; ref names and SHAs remain
significant. Avatar URLs, profile links, and embedded repository statistics therefore do
not invalidate processing or analysis. The latest complete raw payloads are still stored.
All other source fields, including unknown fields, timestamps, comment bodies, review
states, labels, assignments, head/base SHAs and signals remain significant. Existing
observation history and revisions are preserved; this comparison applies to future syncs.

`summaries`, `classifications`, and `rankings` each store one current caller-supplied result
per item with producer, reason, source revision, and save time. Stale results remain visible
in item details; stale ranks and classifications do not drive current ranking/filtering.
Fixed caller-supplied scores have no implicit time decay. Pure priority rules still evaluate
elapsed time on each query. `processing` stores a shared per-item acknowledged revision,
actor, and time. Reads and analysis writes do not advance it. Optional revision guards reject
stale writes; marking processed requires complete item/conversation coverage.

The CLI reuses the SQL query layer for lists, details, comments, and activity history.
Click owns command discovery, help, parameter conversion, and argument errors; shared
command options dispatch to the existing async lifecycle. Help and version output never
initialize storage. Database selection works before or after commands and through
`GH_MAINTAINER_DB`; command-level paths override root-level paths and the environment.
Processing, triage, and classification filters and rank sorting execute in SQLite with
deterministic pagination. All local query and maintenance commands work without credentials.

### Example: reconsidering an inspected PR

| Step                                                | Source revision | Processing checkpoint | Consequence                                                              |
| --------------------------------------------------- | --------------- | --------------------- | ------------------------------------------------------------------------ |
| Agent inspects and acknowledges a PR                | 4               | 4                     | Processing state is `processed`                                          |
| A sync observes a new head or edited comment        | 5               | 4                     | Analysis for revision 4 is stale; processing state is `changed`          |
| An unchanged poll succeeds                          | 5               | 4                     | No additional revision is created                                        |
| Agent tries to save/acknowledge with `--revision 4` | 5               | 4                     | Write fails; the agent must inspect the new context                      |
| Agent saves analysis for revision 5                 | 5               | 4                     | Analysis is current; work remains pending until explicit acknowledgement |
| Agent acknowledges revision 5                       | 5               | 5                     | Processing state returns to `processed`                                  |

Never-processed items have no processing row. `pending` includes never-processed and
changed items; a state filter still applies, so callers need `--state all` when looking
for closures/merges. Acknowledgement checks stored repository error/comment coverage;
it does not contact GitHub or impose an automatic maximum age on the last successful sync.
The revision guard is optional in the CLI and should be supplied by skills using an
earlier inspection. Full user-facing contracts are in [CLI workflows](cli.md).

### Dashboard architecture

The dashboard writes decisions through `PUT /api/items/{item_id}/triage`, using the same
`TriageService` as the CLI. Payloads validate dispositions, note length, and timezone-aware
snooze deadlines. Writes require a custom request header and reject cross-site browser
requests; no CORS access is granted. The service remains intended for local use or an
authenticated private proxy.

Disposition filters use the effective SQL disposition, including expired snoozes and
items reopened after a done decision. Priority, update order, and attention filters apply
before SQL pagination. The vanilla JavaScript frontend renders Markdown with vendored
Marked and DOMPurify, with no CDN or runtime Node.js dependency.
Titles, repository descriptions and editable-note previews use the same sanitized renderer.
`GET /api/items/{item_id}/comments` reuses the existing paginated conversation query;
comments and reviews load independently of item details, retain coverage metadata, and
can be retried without losing a draft decision. Inline comments expose stored path/line
and diff context. Repository cards show stored star/fork counts from inventory payloads;
unknown counts appear as a dash rather than zero. No additional ingestion or storage is
needed for these views.

## Migration contract

The baseline recognizes the original six-table schema, checks column names, storage
types, primary/foreign keys, required nullability and repository/number uniqueness, and
adopts it without copying or rewriting records. Unknown or partially matching schemas are
rejected transactionally. New databases are created with the versioned baseline. Both
paths are tested, including concurrent startup, idempotent upgrades and exact preservation
of every legacy table value.

Migration `0002` adds `owners` and `repo_owners` and backfills membership from raw repository
ownership or the legacy owner metadata. Existing tables and their values are preserved.

Migration `0003` adds conversations, analysis, processing, source revisions, and comment
checkpoints. Added columns preserve original values and legacy observations; old activity
has no inferred revision. Original notes, source documents, and checkpoints are retained.
Migration `0004` adds the independent repository activity snapshot and coverage fields;
existing indexes start with unknown activity until the next successful observation.
Migration `0005` adds an index on `changes(item_id, id)` for accelerated observation history
queries and retention pruning.

SQLite permits legacy TEXT JSON, integer booleans, and primary-key declarations that do
not explicitly repeat NOT NULL. Alembic comparison hooks recognize those existing storage
declarations without proposing unrelated table rebuilds. Changed/missing keys and changed
key types remain visible to autogeneration. Review each future migration before applying it.

Migration files and the generation template ship inside the wheel and container. Use
`gh-maintainer migrate` to upgrade, or `alembic revision --autogenerate` and `alembic check`
from the source checkout. The `--db` option and `GH_MAINTAINER_DB` retain the index selection
behavior. Sync telemetry uses `source_calls` for HTTP attempts, including pagination,
redirects and retries; historical SDK-operation counts retain their original meaning
in the original column.

## Retained limitations

Coverage is limited to repositories visible to the credential. This is a polling index;
observations do not reconstruct transitions between polls or events before the first sync.
Other users' named inventories use GitHub's public listing; explicit repository lists
can index accessible private repositories across owners. Webhooks, full release history and security
findings are later extensions. Initial staging is
still per repository; resumable disk-backed staging should precede very large backfills.
Stored observations can be pruned and compacted on schedule via `gh-maintainer prune`
(`--keep` and `--days`). GitHub bodies remain untrusted and are rendered as plain text.

## References

- [SQLAlchemy async sessions](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [SQLite async dialect](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#module-sqlalchemy.dialects.sqlite.aiosqlite)
- [Alembic asyncio migrations](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)
- [HTTPX async support](https://www.python-httpx.org/async/)
- [aiolimiter pacing and burst behavior](https://aiolimiter.readthedocs.io/en/stable/)
