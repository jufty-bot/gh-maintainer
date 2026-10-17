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
Run `gh-maintainer db path` to see the absolute path without creating a file.
See [database management](cli.md#manage-the-database) for deletion and existing indexes.
Run `uv run gh-maintainer --help` to discover commands and
`uv run gh-maintainer COMMAND --help` to see options and defaults. The Click CLI also
supports `-h` and `--version`; see [CLI help and discovery](cli.md#help-and-command-discovery).

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

The dashboard reads your local index. **Refresh** rereads SQLite; use `sync` or `watch`
to fetch new GitHub data. No credentials are needed to serve an already populated index.

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
uv run gh-maintainer triage I_EXAMPLE planned --note 'Reproduce with current release'
uv run gh-maintainer triage I_EXAMPLE waiting --note 'Waiting for author response'
uv run gh-maintainer triage I_EXAMPLE snoozed --until 2026-12-01T09:00:00Z
uv run gh-maintainer triage I_EXAMPLE done
```

`done` is a local triage decision, separate from GitHub's open/closed status. An item
returns to the inbox when new observed source activity advances its revision after that decision.
Snoozes expire automatically. Open an item in the dashboard to edit its local disposition,
next-action note, and snooze deadline, or use the CLI commands above. The app never merges
PRs, closes issues, sends messages, or changes GitHub labels.

### Dashboard workflow

- **Start triaging** opens the first item in your triage inbox. Review descriptions as
  rendered Markdown (including tables, task lists, and code blocks) or switch to source.
- **Save & next** records your decision and opens the next item on the current page.
  The triage inbox has separate lanes for inbox, planned, waiting, snoozed, and done.
  Snoozed and done items leave the maintenance queue; planned and waiting items remain.
- Search titles, labels, repository names, and item numbers. Filter by repository or
  attention level, and sort by priority, recent updates, or oldest updates.
- Item details show PR signals, assignees, labels, and recent local source observations.
  Copy an item ID for the CLI, or open the original item on GitHub.
- Press `/` to focus search, `j` / `k` to move between items while reviewing, and `Esc`
  to close details. Unsaved triage edits prompt before leaving the item.
- Switch between light and dark themes; the preference stays in your browser.

Markdown is sanitized before display. Renderer assets are bundled with the app, so
rendering works offline without a CDN or a frontend build step.

The local triage API is `PUT /api/items/{item_id}/triage`, with a JSON body containing
`disposition`, `note`, and optional timezone-aware `snoozed_until`. It requires the
`X-Maintainer-Request: 1` header and rejects cross-site browser requests. The dashboard
still needs the local or authenticated private deployment described below.

## What the index contains

- Repository identity, ownership, visibility, archive/fork flags, and raw API metadata.
- Every listed issue and PR, including open, closed, draft, and merged PR states.
- Titles, bodies, authors, labels, assignees, timestamps, URLs, and raw issue payloads.
- Conversation comments, inline review comments, and PR review bodies, including raw
  reply/path context, per-repository comment checkpoints, and retained availability history.
- PR detail payloads, current-head check rollups, review decisions, mergeability, and
  the separate time at which PR signals were successfully observed.
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
Incremental reads select incoming items, available PRs needed for review coverage, and
comment parents by number instead of loading all historical issues. Commit reads target
changed items and comment parents; full reconciliation still checks every stored item.

Repository-level comment feeds use a separate checkpoint and the same five-minute overlap;
the initial comment backfill and periodic full sync reconcile availability. Every known
available PR, including merged and closed PRs, has its review bodies listed on each sync
because that endpoint has no incremental timestamp filter. This adds one paginated review
listing per PR. Comment/review fetch failures roll back the repository's item and comment
updates together, retaining both checkpoints. Existing databases backfill comments on their
next successful sync. `sync --full` also reconciles missing comments immediately.

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
permissions should include metadata, issues, pull requests, checks, and commit statuses
for all intended repositories; additional GitHub permission policies can restrict signals.
Existing GitHub CLI credentials work without being copied into configuration files.

This is a polling index, not a complete GitHub event archive. It cannot reconstruct
transitions that happened entirely between syncs or before the first backfill. GitHub
pagination is not a snapshot; overlap and periodic reconciliation help recover concurrent
changes. Comments and review bodies are retained, but GraphQL review-thread resolution
state, commits, workflow history, releases, discussions, and security alerts are not
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
The dashboard has no user authentication; keep it local or put it behind an authenticated
private proxy. SQLite files and exports may contain private repository data. Back up using
SQLite's backup API or stop both processes before copying the volume.
