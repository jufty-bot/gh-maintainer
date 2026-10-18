# CLI workflows

Run `gh-maintainer sync` to fetch GitHub data. The commands below read or write the local
SQLite index and work without credentials. They do not change GitHub labels, comments,
or item state. Prefix commands with `uv run` when working from this checkout.
For packaged installation and bundled workflows see [releases](releases.md) and [skills](skills.md).
`policy` validates explicit [repository rules](policy.md); `context ITEM` fetches
[live technical evidence](context.md) through gh, outside the indexed-query loop.
`skills --install PATH` copies bundled instructions without opening SQLite.

## Help and command discovery

The CLI uses Click. Run `gh-maintainer --help` (or `-h`) to see commands and their
descriptions; add `--help` to any command for its arguments, options, and defaults.
`gh-maintainer --version` prints the installed version. Running without a command
shows help and exits 2. Help and version checks do not open the database or use credentials.

```bash
gh-maintainer --help
gh-maintainer sync --help
gh-maintainer prs -h
gh-maintainer --version
```

`--db PATH` works before or after the command. A command-level path takes precedence
over a root-level path, then config `db`, then `GH_MAINTAINER_DB`, then the platform user data directory (see [database management](#manage-the-database)).

Pass `--config PATH` before or after a command to load validated JSON/YAML defaults.
Top-level `repositories` applies to `sync` and `watch`; command `repo` settings and
explicit `--repo` arguments override it. See [configuration files](configuration.md).

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
`--classification`, `--processing`, `--author`, `--since`, `--until`,
`--priority`, `--category`, `--triaged/--untriaged`, and `--sort priority|updated|rank`.

`comments` combines conversation comments, inline review comments, and PR review bodies.
JSON retains raw payloads, including reply IDs, paths, line context, and review state.
It reports when comments were last synced and any repository coverage error. Records no
longer visible after reconciliation are retained; use `--include-unavailable` to see them.
GraphQL review-thread resolution state is not currently indexed.

### Repository activity

`gh-maintainer repos --format json` includes `activity` for each repository:

| Field | Meaning |
| --- | --- |
| `default_branch` | Name of the observed default branch |
| `last_commit` | Default-branch tip: SHA, message headline, committer timestamp (`committed_at`), and URL |
| `last_release` | GitHub's designated latest release: source ID, name, tag, publication timestamp (`published_at`), and URL |
| `last_pr` | Most recently **created** PR across open/closed/merged states: source PR node ID, number, title, state, creation/update timestamps, and URL |
| `last_issue` | Most recently **created** issue across open/closed states, excluding PRs: source issue node ID, number, title, state, creation/update timestamps, and URL |
| `raw` | The source GraphQL activity payload retained with the normalized summary |

Text output shows the four identities and dates. JSON includes full links and timestamps;
`last_pr` / `last_issue` selection uses creation time, even when an older item was updated
more recently. A commit denotes the current default-branch tip; its date can go backwards
after a force push. A release follows GitHub's `latestRelease` selection rather than sorting
tag names or semantic versions. Full commit/release histories are outside this snapshot.

Repository-level `activity_synced_at`, `activity_error` and `activity_stale` describe the
summary's coverage. `activity: null` with no observation time means it has not been fetched.
After a successful observation, a null entry means that no such record was visible (for
example an empty repository or no release). Text uses `unknown` before observation and
`-` for observed absence. A failed or incomplete lookup keeps the previous snapshot/time
and sets an error; unavailable repositories also display stale activity. No automatic age
expiry is applied: inspect the observation time when freshness matters.
Failed explicit repository selection also marks retained activity stale.

Activity coverage is independent of item/comment coverage. A summary lookup failure makes
the sync run partial without invalidating item analysis; item/conversation failures can
retain an independently successful activity observation. `synced` still counts repositories
whose item/comment ingestion committed. Existing indexes acquire the additive fields via
migration `0004`; the next sync fills them. Local `repos` reads never need credentials.

## Save analysis and triage

```bash
gh-maintainer classify OWNER/REPO#42 bug urgent --producer agent/v1 --revision 3
gh-maintainer summarize OWNER/REPO#42 'Fixes a crash during startup' --revision 3
gh-maintainer rank OWNER/REPO#42 90 --reason 'Affects new installations' --revision 3
gh-maintainer triage OWNER/REPO#42 planned --priority high --category bug_report --note 'Reproduce before review' --revision 3
gh-maintainer prs --classification bug --sort rank --format json
```

These commands save values supplied by a human or agent. They do not invoke a model.
`summarize ITEM -` reads text from stdin, allowing multiline summaries or file redirection.
Classifications are local labels; repeating the command replaces the saved label set.
Higher ranks sort first. Stale ranks are excluded from rank ordering, and stale
classifications are excluded from classification filters. The default priority order
still uses the built-in maintenance rules.
For a negative score, place options before `--`, for example
`gh-maintainer rank OWNER/REPO#42 --reason 'Low impact' -- -10`.

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
Embedded account profile changes and PR head/base repository statistics update retained raw
data without advancing that revision. Content, identities, refs, review states and signals
remain significant; see the [comparison contract](architecture.md#querying-and-triage).

`--processing unprocessed` selects never-processed items; `changed` selects items changed
since processing; `processed` selects acknowledged revisions. `pending` and the
`--unprocessed` shortcut include both never-processed and changed items. Use `--state all`
to include newly closed or merged items.
`--unprocessed` and `--processing` are mutually exclusive.

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

Successful commands, including empty lists, exit 0. Invalid arguments, option conflicts,
pagination bounds, watch intervals below 60 seconds, and malformed timestamps exit 2
before opening the database. Unknown items, invalid analysis or triage decisions,
stale writes, and runtime failures exit 1 with an error on stderr. Interrupting a running
command exits 130. Sync retains its complete/partial JSON contract and exits nonzero on
partial coverage; progress goes to stderr, keeping stdout available for JSON.
`context` also exits 1 with a useful JSON packet when evidence is incomplete, clipped,
unavailable or has further pages; inspect its explicit coverage fields.


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
