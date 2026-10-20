# Using gh-maintainer

This guide covers the CLI, local dashboard, storage guarantees, and deployment.
For packaged installation see [releases](releases.md); agents should read [skills](skills.md),
[policy](policy.md), and [technical context](context.md).
See the [roadmap](roadmap.md) for planned capabilities and the [contributor guide](contributing.md) for development.

## Start locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and authenticate with
`gh auth login`, or export `GH_TOKEN` / `GITHUB_TOKEN`.

```bash
uv sync --locked
uv run gh-maintainer sync
uv run gh-maintainer serve
```

Open **http://127.0.0.1:8765**. The first sync backfills historical items. Later syncs are
incremental; repository discovery runs every time. The default database is
`maintainer.sqlite` in the platform user data directory chosen by `platformdirs`
(for example, `~/.local/share/gh-maintainer/maintainer.sqlite` on Linux, respecting
`XDG_DATA_HOME`). `--db PATH` or `GH_MAINTAINER_DB` selects another index.

You can also start with just `uv run gh-maintainer serve`. An empty dashboard offers
**Set up repositories**; **Sync now** opens the same setup at any time. The setup checks
whether the server has a credential, explains how to authenticate on that server, and
lets you choose your repositories, an owner/organization, or explicit names across owners.
Credentials are never sent to the browser. Credential detection is not a permission check;
the sync verifies actual access and reports failures. Start an incremental sync or request
full reconciliation, inspect live stage messages and per-repository errors, cancel, or retry.
Closing the setup leaves the sync running; reopening the page resumes progress polling.
Only one dashboard sync runs per server process, and shutdown cancels it with source/lease
cleanup. Other sync processes are still fenced by the database lease. Use one HTTP worker.
Setup starts with the configured sync defaults, including `repositories`; selections are
retained for that server session. Use `--config` to retain defaults across server restarts.
Run `gh-maintainer db path` to see the absolute path without creating a file.
See [database management](cli.md#manage-the-database) for deletion and existing indexes.
The dashboard formats titles, descriptions, repository descriptions and note previews as
sanitized Markdown with reference autolinking. Item details include linked pull requests
and issues, threaded comments with parent-child reply context, reviews and inline code
context, with sync coverage and retry controls. Repository cards include stars and forks
linked to GitHub. Refresh rereads SQLite; run `sync` to update source data, or `sync --full`
to backfill conversation coverage in an older index.
Run `uv run gh-maintainer --help` to discover commands and
`uv run gh-maintainer COMMAND --help` to see options and defaults. The Click CLI also
supports `-h` and `--version`; see [CLI help and discovery](cli.md#help-and-command-discovery).

`gh-maintainer repos --format json` also shows each repository's last default-branch
commit, designated latest release, newest-created PR, and newest-created issue, including
dates and links. The [repository activity contract](cli.md#repository-activity) explains
selection, known absence, and stale/failed lookups. Activity refreshes in batches of up to
20 repositories per GraphQL request on each sync, independently of conversation ingestion.

Use `--config PATH` for JSON/YAML defaults, including a shared `repositories` list.
See [configuration files](configuration.md) for examples and schema generation.

### Select repositories

`sync` and `watch` accept the same mutually exclusive selection options:

```bash
# Default: repositories owned by the authenticated user
uv run gh-maintainer sync

# All visible repositories belonging to a user or organization
uv run gh-maintainer sync --owner YOUR_LOGIN_OR_ORGANIZATION

# Resolve a list directly, including repositories from different owners
uv run gh-maintainer sync --repo OWNER/PROJECT --repo OTHER/PROJECT
uv run gh-maintainer watch --owner YOUR_ORGANIZATION --interval 900
```

Names are case-insensitive. `--repo` is repeatable and cannot be combined with `--owner`.
The default and organization inventories include private repositories visible to the
credential. A different user's named inventory uses GitHub's public listing; select
accessible private repositories explicitly with `--repo`.

One database can retain multiple owners. A successful owner inventory reconciles only
that owner's repositories; public-only inventories preserve known-private records and
records with unknown visibility. Explicit lists update only the resolved repositories.
Redirects are resolved to stable repository IDs, preserving data across renames/transfers.
An inaccessible selected repository produces a partial result; other selections continue,
and existing data and checkpoints for the failed selection are retained.

Sync JSON includes `scope` (`owner` or `repositories`) and `coverage` (`credential-visible`,
`public-only`, or `explicit`). `owner` is null for an explicit list. Complete syncs exit 0;
partial syncs and failed discovery exit nonzero. The portfolio JSON includes an `owners`
array; its legacy `owner` field is populated only when exactly one owner is known.

`sync` and `watch` write human-readable structlog logs to stderr, including repository
progress, full or incremental mode, item/comment counts, elapsed time, and failures.
Logs use text output, including when redirected or viewed through Docker. The final sync
result remains JSON on stdout for scripts.

The dashboard reads your local index. **Refresh** rereads SQLite; **Sync now**, CLI `sync`,
or `watch` fetches GitHub data. No credentials are needed to serve an already populated index.

To explore without GitHub access:

```bash
uv run gh-maintainer --db .data/demo.sqlite demo
uv run gh-maintainer --db .data/demo.sqlite serve
```

## A maintenance process

See [CLI workflows](cli.md) for item/status queries, comments, analysis, processing queues,
and changes since the last check.

1. **Discover and index.** Select repositories directly or discover a user/organization
   inventory, including visible forks, private repositories, and archives within its coverage.
2. **Refresh regularly.** Run `uv run gh-maintainer watch --interval 900` in a worker,
   or invoke `sync` from cron. The worker runs immediately, then waits 15 minutes.
3. **Review the queue.** Failing checks, conflicts, requested changes, review requests,
   stale items, and unassigned issues get explicit reasons. Drafts remain visible;
   archived repositories retain history and are excluded from the active queue.
4. **Record a decision.** Mark an item planned, waiting, snoozed, or locally done, with
   a next-action note. Local decisions survive synchronization.
5. **Export a digest.** Produce Markdown for daily review or JSON for other tools.
6. **Reconcile.** Every seven days, each repository gets a full item reconciliation.
   `sync --full` forces one immediately. Items missing from a successful full listing
   remain in history with `available=false`; missing repositories are retained as
   `present=false`. Neither flag claims that an item was deleted.

```bash
# Backfill just one repository
uv run gh-maintainer sync --repo YOUR_LOGIN/YOUR_REPOSITORY

# Tune parallel ingestion and HTTP pacing (defaults: 4 workers, 2 requests/sec)
uv run gh-maintainer sync --concurrency 4 --requests-per-second 5

# Generate reports
uv run gh-maintainer report --output maintenance.md
uv run gh-maintainer report --format json --output portfolio.json

# Use an item's node ID from its dashboard details or JSON export
uv run gh-maintainer triage I_EXAMPLE planned --priority high --category bug_report --note 'Reproduce with current release'
uv run gh-maintainer triage I_EXAMPLE waiting --note 'Waiting for author response'
uv run gh-maintainer triage I_EXAMPLE snoozed --until 2026-12-01T09:00:00Z
uv run gh-maintainer triage I_EXAMPLE done
```

`done` is a local triage decision, separate from GitHub's open/closed status. An item
returns to the inbox when new observed source activity advances its revision after that decision.
Snoozes expire automatically. Open an item in the dashboard to edit its local disposition,
priority, category, next-action note, and snooze deadline, or use the CLI commands above. The app never merges
PRs, closes issues, sends messages, or changes GitHub labels.

### Dashboard workflow

- PR details have **Conversation**, **Files Changed**, and **Commits** views.
  Files Changed displays the latest synced PR diff with head/base identity, file navigation,
  line numbers and word-level changes. Desktop opens **Side by side** with aligned
  before/after panes and synchronized horizontal scrolling; mobile opens **Unified**.
  Switch layouts at any time, or choose **Raw diff** for the original patch. The chosen
  layout lasts until page reload and both dashboard themes are supported.
  Commits shows the current ordered list, full messages, authors, dates, and
  links to inspect individual commits on GitHub. Both views report missing or stale data
  and fetch errors can be retried. Use **Sync now** to discover newer commits; the dashboard
  reads the local index. See [PR code contracts and limits](cli.md#pr-files-and-commits).
- **Start triaging** opens the first item in your triage inbox. Review descriptions as
  rendered Markdown (including tables, task lists, and code blocks) or switch to source.
- **Save & next** records your decision and opens the next item on the current page.
  The triage inbox has separate lanes for inbox, planned, waiting, snoozed, and done.
  Snoozed and done items leave the maintenance queue; planned and waiting items remain.
- **Finish review & next** saves the decision and acknowledges the displayed source revision
  in one transaction. Load all conversation pages before finishing. Missing coverage disables
  completion; a source change rejects the entire save. Your edits remain available, and
  **Reload source, keep edits** refreshes the evidence for another review. The last item can
  still be finished; completion advances to the next candidate or closes the details.
  **Save decision** and **Save & next** remain available without acknowledging processing.
- Search titles, labels, repository names, and item numbers. Filter by repository,
  attention level, author, date range (`since`/`until`), priority, category, or
  triaged status, and sort by priority, recent updates, or oldest updates.
- Item details show PR signals, assignees, labels, and recent local source observations.
  Copy an item ID for the CLI, or open the original item on GitHub.
- Press `/` to focus search, `j` / `k` to move between items while reviewing, and `Esc`
  to close details. Unsaved triage edits prompt before leaving the item.
- Switch between light and dark themes; the preference stays in your browser.

Markdown is sanitized before display. Renderer assets are bundled with the app, so
rendering works offline without a CDN or a frontend build step.

### Review returning items

Board cards show brief reasons such as **New code**, **Author replied**, and **Checks
changed** when activity has been observed since the last completed review. Open an item
for **Since your last review**: the saved decision and note sit beside dated changes,
comment excerpts, head transitions and check/review-state transitions, with GitHub links.
**Load more activity** pages through retained evidence without discarding triage edits;
failed activity loads can be retried independently.

Completing a review advances the shared checkpoint, so later activity becomes the next
comparison window. If only a revision-bound decision exists, the view instead says
**Since your saved decision**. Without either baseline it reports that no comparison
checkpoint exists. Expired snoozes get a **Snooze ended** reason, including when there
is no new source activity.

The saved decision is the latest retained local decision, with its original disposition
and timestamp. A locally done item may therefore show **Done** beside a current **Inbox**
form value. Decisions edited after the review are explicitly labeled **Latest saved
decision**; the application does not invent a historical copy of an overwritten note.

Only retained observations after the baseline and through the inspected revision appear.
The preview includes 20 entries, with separate pagination for older entries. Missing
source revisions and unavailable retained details are explicit; they are not reported as
"no changes." Polling can miss activity between syncs, and pruning can remove individual
events even when every revision has some retained evidence. Check source coverage and
timestamps alongside this view. Unknown or old-head check signals are never shown as
new passing/failing evidence.

### Work board

Open **Work board** for Inbox, Planned, Waiting, Snoozed, and Done columns. The board
includes all GitHub states by default, so closed/merged follow-up is visible. Each column
shows its matching total and has independent SQL pagination (10 cards per page). Search,
repository, source-state, attention, category, priority, author, date and triage filters
apply across columns. The board scrolls horizontally on narrow screens.

Open a card to inspect the same item workspace. Drag it into a column or use its accessible
disposition selector to save a local move, preserving its note, priority and category.
Cards show a grip and grab cursor, with a subtle lift on pointer hover. While dragging,
the source card dims and the destination column highlights with a drop hint; Snoozed
explains that a date will be requested. Feedback clears on leaving a column, dropping,
or cancelling the drag. Reduced-motion preferences disable the lift animation.
Moving to Snoozed opens the item workspace to choose a future deadline. Moves pass the
displayed source revision and reject stale cards; they do not acknowledge processing or
change GitHub. Expired snoozes and done items with later activity use the same effective
inbox rules as CLI queries. Refresh the index view to see newly observed changes.

The dashboard REST contracts include `GET /api/setup` (credential availability and current
selection), `POST /api/sync` (202 with status), `GET /api/sync` (state, latest message,
stage count and result), and `POST /api/sync/cancel`. Mutating sync requests require
`X-Maintainer-Request: 1` and reject cross-site browser requests. Progress counts stages;
it does not estimate a completion percentage. A partial sync retains its structured errors
for retry. These routes use the same maintenance sync service as CLI/MCP.

The local triage API is `/api/items/{item_id}/triage` (accepting `PUT` or `POST`), with a JSON body
containing `disposition`, `note`, optional `priority`, optional `category`, and optional timezone-aware
`snoozed_until`. Optional `revision` rejects stale writes with HTTP 409. `finish_review: true`
requires `revision` and atomically saves triage plus processing with `actor` (default
`dashboard`); incomplete coverage returns 422 without saving either. It requires the
`X-Maintainer-Request: 1` header on PUT and rejects cross-site browser requests.
The dashboard still needs the local or authenticated private deployment described below.

## What the index contains

- Repository identity, ownership, visibility, archive/fork flags, and raw API metadata.
- Every listed issue and PR, including open, closed, draft, and merged PR states.
- Titles, bodies, authors, labels, assignees, timestamps, URLs, and raw issue payloads.
- Conversation comments, inline review comments, and PR review bodies, including raw
  reply/path context, per-repository comment checkpoints, and retained availability history.
- PR detail payloads, current-head check rollups, review decisions, mergeability, and
  the separate time at which PR signals were successfully observed.
- Latest repository commit/release/PR/issue activity with raw source evidence and its
  independent observation time/error; full commit/release history is not indexed.
- An append-only log of observed source changes, per-repository checkpoints and errors,
  run history, and local triage notes.
- Source revisions, processing checkpoints, summaries, classifications, and ranks supplied
  by humans or agents, with producer, timestamp, and freshness.

Issue IDs and PR IDs are different in GitHub. Items use the **issue node ID** as their
local identity; the PR's own node ID is retained separately for GraphQL enrichment.
Repository IDs survive renames. Raw payloads preserve fields for future analysis.

### Correctness and limits

REST listing follows `Link` pagination to exhaustion and avoids the search API's
1,000-result ceiling. Incremental issue queries use `state=all`, a checkpoint taken
**before** fetching, and a five-minute overlap. Every previously open PR is refreshed
on every sync: CI and review changes need not advance the issue timestamp. Unchanged
closed PRs in the overlap window reuse their cached details, including merged state.
Incremental reads select incoming items and open PR snapshots; historical review coverage
loads PR numbers instead of raw PR payloads. Comment reads target incoming IDs and the PR
review scopes being reconciled. Full reconciliation still checks every stored item.
Unchanged discovery does not rewrite repository metadata or ownership memberships.
Explicit repository selections resolve through the same bounded concurrency limit.

Repositories share signal requests (up to 20 PR nodes per request) within windows bounded
by `--concurrency`. Each window commits repositories sequentially and releases its staged
data before starting the next; a slow repository can delay its window. For 201 repositories
with one open PR each, offline tests use 51 signal requests at default concurrency 4,
or 11 at concurrency 20, instead of one request per repository. These are request-count
regressions, not live throughput benchmarks; REST requests still use the shared rate limit.
If a signal batch fails, its represented repositories retain all their previous signals
and signal timestamps and report partial coverage; other batches can succeed.

Repository-level comment feeds use a separate checkpoint and the same five-minute overlap;
the initial comment backfill and periodic full sync reconcile availability. Every known
available PR, including merged and closed PRs, has its review bodies listed on each sync
because that endpoint has no incremental timestamp filter. This adds one paginated review
listing per PR. Comment/review fetch failures roll back the repository's item and comment
updates together, retaining both checkpoints. Existing databases backfill comments on their
next successful sync. `sync --full` also reconciles missing comments immediately.

Embedded account profile changes (such as avatar URLs) and PR head/base repository
statistics update the retained raw payload without advancing the source revision or
invalidating processing/analysis. Account identities, repository identities, ref SHAs,
item/comment content, review states and other source fields still count as activity.
See the exact [revision comparison contract](architecture.md#querying-and-triage)
and [remaining portfolio efficiency work](roadmap.md#portfolio-sync-efficiency).

A repository's item updates, observations, and checkpoint commit in one SQLite
transaction. Failed repositories keep their previous checkpoint and data. Failed
GraphQL enrichment preserves the old signals and their timestamp, marks the run partial,
and displays effective signals as unknown. Retries are bounded, respect rate-limit
headers, and stop rather than sleep beyond 30 seconds. Repository, PR-detail and review
workers are bounded by `--concurrency` (default 4), with the same global cap on in-flight
HTTP requests. One shared `aiolimiter` spaces all HTTP attempts, including pagination,
redirects and retries, without an initial burst. `--requests-per-second` sets the maximum
start rate (default 2), independently of concurrency; 5 is an optional faster short-run
setting, subject to the same GitHub budgets and cooldowns. When REST or GraphQL has at
most 10% of its primary allowance left, additional pacing spreads that resource's remaining
allowance until reset. GraphQL rate-limit errors participate in the shared cooldown even
with HTTP 200. Queued workers recheck server feedback after permit waits and resume paced.
Failed or cancelled runs cancel and await their outstanding workers before lease release.
Pacing is local to one source instance; other tools can still consume the same allowance.
Server cooldowns and budget pacing waits over 30 seconds defer work to the next sync.
A renewable database lease prevents concurrent syncs; after a crashed process, the lease
expires within five minutes. Sync exits nonzero on partial coverage so a scheduler can alert.

Coverage always means **repositories visible to the credential**. A fine-grained token
limited to selected repositories cannot reveal repositories it was not granted. Use a
credential covering the owner's full inventory when completeness matters. Fine-grained
permissions should include metadata, contents (for commits/releases), issues, pull requests, checks, and commit statuses
for all intended repositories; additional GitHub permission policies can restrict signals.
Existing GitHub CLI credentials work without being copied into configuration files.

This is a polling index, not a complete GitHub event archive. It cannot reconstruct
transitions that happened entirely between syncs or before the first backfill. GitHub
pagination is not a snapshot; overlap and periodic reconciliation help recover concurrent
changes. Comments and review bodies are retained, but GraphQL review-thread resolution
state, full commit/release histories, workflow history, discussions, and security alerts are not
backfilled. A null or unavailable check result is **unknown**, never a passing check.
Green checks and approval do not establish that branch rules permit merging.

The first backfill fetches details for every PR and can take time on large repositories.
The dashboard filters, counts, and paginates in SQL. Full report exports still load the
portfolio, and ingestion stages one repository before committing it atomically. This
release targets portfolios rather than a company-wide data warehouse. Incremental
checkpoints are per repository; interrupted backfills restart that repository.

## Database migrations and compatibility

Commands that open an index initialize it through packaged Alembic migrations;
database path/deletion commands do not open it. The baseline
validates and adopts the original schema without rewriting source records, observations,
triage notes, or checkpoints. Unknown schemas are rejected. Back up the SQLite file using
its backup API before upgrading an existing deployment.

```bash
uv run gh-maintainer migrate
# Developers can inspect/generate later schema changes:
uv run alembic check
uv run alembic revision --autogenerate -m "Describe schema change"
```

`GH_MAINTAINER_DB` selects the database for both CLI and Alembic commands. Migrations
are serialized with a SQLite write lock. Compatible legacy TEXT/JSON, INTEGER/Boolean,
and primary-key nullability declarations are retained during schema autogeneration.
SQLAlchemy 2.0 is pinned to preserve the template's Python 3.10 support.

Sync JSON reports `source_calls`, now counting HTTP attempts including pagination,
redirects and retries. Historical SDK-operation counts retain their original meaning.
Item, repository, triage, and dashboard data retain their existing meaning.

## Self-host with Docker Compose

```bash
cp .env.example .env
# Set GH_TOKEN in .env, then:
docker compose up -d --build
```

The dashboard binds to **127.0.0.1:8765** on the host. A separate worker syncs every
15 minutes and both share a named SQLite volume. Only the worker receives a GitHub
credential. Use `docker compose logs -f worker` to inspect synchronization.
The default dashboard container has no credential, so browser-triggered sync is unavailable
there; CLI/worker polling continues normally. To use browser sync, run the server in an
environment with GitHub authentication available.
The dashboard has no user authentication; keep it local or put it behind an authenticated
private proxy. SQLite files and exports may contain private repository data. Back up using
SQLite's backup API or stop both processes before copying the volume.
