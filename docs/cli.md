# CLI workflows

Run `gh-maintainer sync` to fetch GitHub data. The commands below read or write the local
SQLite index and work without credentials. They do not change GitHub labels, comments,
or item state. Prefix commands with `uv run` when working from this checkout.

## Inspect repositories, PRs, and issues

```bash
gh-maintainer repos
gh-maintainer prs --repo OWNER/REPO
gh-maintainer issues --state all
gh-maintainer status --format json
gh-maintainer show OWNER/REPO#42
gh-maintainer comments OWNER/REPO#42 --format json
```

`prs` and `issues` default to open items. `status` lists PRs in all states, with checks,
review decision, mergeability, rank, processing status, revision, and coverage. Missing
or stale PR signals are unknown. `show` includes the body, PR payload, source timestamps,
latest observations, saved analysis, and triage. Full PR payloads are available in JSON;
text output includes the current head.

Item references accept `OWNER/REPO#42` or the stored **issue node ID**, including for PRs.
Repository names are case-insensitive. Unknown references fail instead of returning an
empty detail. List commands accept `--repo`, `--state`, `--search`, `--disposition`,
`--classification`, `--processing`, and `--sort priority|updated|rank`.

`comments` combines conversation comments, inline review comments, and PR review bodies.
JSON retains raw payloads, including reply IDs, paths, line context, and review state.
It reports when comments were last synced and any repository coverage error. Records no
longer visible after reconciliation are retained; use `--include-unavailable` to see them.
GraphQL review-thread resolution state is not currently indexed.

## Save analysis and triage

```bash
gh-maintainer classify OWNER/REPO#42 bug urgent --producer agent/v1 --revision 3
gh-maintainer summarize OWNER/REPO#42 'Fixes a crash during startup' --revision 3
gh-maintainer rank OWNER/REPO#42 90 --reason 'Affects new installations' --revision 3
gh-maintainer triage OWNER/REPO#42 planned --note 'Reproduce before review' --revision 3
gh-maintainer prs --classification bug --sort rank --format json
```

These commands save values supplied by a human or agent. They do not invoke a model.
`summarize ITEM -` reads text from stdin, allowing multiline summaries or file redirection.
Classifications are local labels; repeating the command replaces the saved label set.
Higher ranks sort first. Stale ranks are excluded from rank ordering, and stale
classifications are excluded from classification filters. The default priority order
still uses the built-in maintenance rules.

Each analysis record stores its producer, reason, source revision, and save time. Use
`--producer` to identify an agent, rule, or model version. A source revision change makes
previous analysis stale. Incomplete sync coverage also makes analysis effectively stale.
Saved ranks are fixed scores; producers should explicitly refresh time-dependent scores.
Analysis writes preserve source records, triage, and the processing checkpoint.

Read the current `revision` from `show --format json` and pass `--revision` when writing.
A mismatched revision rejects the write so an agent cannot silently acknowledge changes
that arrived while it was working. Omitting the option writes against the current stored
revision. `triage` also accepts references and this revision guard; existing dispositions,
snoozes, and notes keep their meaning. Locally done items return to the inbox on new source
activity, including comments or pushes that do not advance the parent issue timestamp.

## Find unprocessed items and new activity

```bash
gh-maintainer prs --unprocessed
gh-maintainer show OWNER/REPO#42 --format json
gh-maintainer changes OWNER/REPO#42
gh-maintainer mark-processed OWNER/REPO#42 --revision 3 --actor agent/v1
gh-maintainer prs --state all --processing changed
gh-maintainer changes --format json
```

Reading an item, saving analysis, and triaging it do not mark it processed. Use
`mark-processed` after inspecting the item and its conversation. It requires successful
item/comment coverage and saves a shared per-item checkpoint with the actor and time.
New observed source activity advances the item revision and makes it pending again.

`--processing unprocessed` selects never-processed items; `changed` selects items changed
since processing; `processed` selects acknowledged revisions. `pending` and the
`--unprocessed` shortcut include both never-processed and changed items. Use `--state all`
to include newly closed or merged items.

`changes [ITEM]` defaults to observations after each item's processing checkpoint.
It includes initial indexing for never-processed items. Use `--all` for retained history
or `--since 2026-10-01T00:00:00Z` for a timestamp window. Events distinguish pushes
(observed head SHA changes), comments, edits, reviews, closures, merges, reopenings,
availability, and signal changes. JSON includes before/after snapshots and the revision.
This is a polling log: activity becomes visible after sync, and transitions entirely
between polls cannot be reconstructed. Migrated historical events retain their original
snapshots and have no assigned revision; they remain available through `--all` / `--since`.

## Output and pagination

Read and analysis commands support `--format text|json`; text is the default. JSON is a
single object. Item lists have `total` and `items`, comments have `total`, coverage, and
`comments`, and activity lists have `total` and `changes`. Each item includes its revision,
processing checkpoint, source freshness, analysis with `stale` flags, and local triage.

Lists of items, comments, and changes accept `--limit` (1–500, default 100) and `--offset`
(nonnegative, default 0). SQLite performs filtering, sorting, counting, and pagination.
Ordering has deterministic tie breakers. Advance the offset to retrieve the remaining
records; `total` always describes the matching set. `repos` returns the small inventory
overview rather than a paginated item list. `show` includes the latest 50 observations;
use `changes` to paginate the full history.

Successful commands, including empty lists, exit 0. Argument parsing errors exit 2; unknown
items, validation failures, stale writes, and runtime failures exit 1 with an error on
stderr. Sync retains its complete/partial JSON contract and exits nonzero on partial
coverage. Use `--db PATH` or `GH_MAINTAINER_DB` to choose the index.


## Manage the database

```bash
uv run gh-maintainer db                 # Print the selected absolute path
uv run gh-maintainer db path            # Same as above
uv run gh-maintainer db delete --yes    # Permanently delete the selected index
uv run gh-maintainer --db .data/maintainer.sqlite db path
```

The default is `maintainer.sqlite` in the platform user data directory supplied by
[platformdirs](https://platformdirs.readthedocs.io/en/latest/api.html#user-data-directory).
Selection precedence is `--db PATH`, then a nonempty `GH_MAINTAINER_DB`, then the platform
default. `--db` works before the command, after `db`, or after `path`/`delete`.
The bare `db` command defaults to `path`; output is one absolute path line for scripts.
These commands need no credentials and never open, create, or migrate SQLite.
`:memory:` has no file to manage and returns an error.

Stop any sync/watch/dashboard processes using the index before deletion. `delete` requires
`--yes` and removes the database plus its SQLite `-wal`, `-shm`, and `-journal` sidecars.
This also removes local summaries, classifications, rankings, triage, and processing
checkpoints. It leaves the parent directory and other indexes in place. Missing files
are ignored; filesystem errors return exit 1. The next data command creates a fresh index.

Existing `.data/maintainer.sqlite` files are not moved or deleted automatically. To keep
using one, pass `--db .data/maintainer.sqlite` or set `GH_MAINTAINER_DB` to its absolute
path. Switching to the new default starts a separate index; local decisions remain only
in the original file.
