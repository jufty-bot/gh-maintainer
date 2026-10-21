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
or stale PR signals are unknown. `show` includes the body, linked issues or PRs,
PR payload, source timestamps, latest observations, saved analysis, and triage.
Full PR payloads are available in JSON; text output includes the current head and any
linked closing references.

Item references accept `OWNER/REPO#42` or the stored **issue node ID**, including for PRs.
Repository names are case-insensitive. Unknown references fail instead of returning an
empty detail. List commands accept `--repo`, `--state`, `--search`, `--disposition`,
`--classification`, `--processing`, `--author`, `--since`, `--until`,
`--priority`, `--category`, `--triaged/--untriaged`, and `--sort priority|updated|rank`.

`comments` combines conversation comments, inline review comments, and PR review bodies.
Replies include indentation indicators (`↳ [reply to #...]`) referencing their parent comment.
JSON retains raw payloads, including reply IDs, paths, line context, and review state.
It reports when comments were last synced and any repository coverage error. Records no
longer visible after reconciliation are retained; use `--include-unavailable` to see them.
GraphQL review-thread resolution state is not currently indexed.

### PR files and commits

```bash
gh-maintainer files OWNER/REPO#42
gh-maintainer files OWNER/REPO#42 --format json
gh-maintainer commits OWNER/REPO#42
gh-maintainer commits OWNER/REPO#42 --limit 100 --offset 0 --format json
```

These are offline index queries; `context --section files` remains a separate live read.
`files` returns the latest synced unified git diff for the entire PR. Only one diff is
stored per PR, replaced when its head or base SHA changes; there is no per-commit diff archive.
JSON includes `head_sha`, `base_sha`, `fetched_at`, `stale`, `coverage_error`, and `diff`.
`diff: null` means unavailable or mismatched with the indexed head/base; an empty string
means a successfully fetched empty diff. Binary changes retain GitHub's binary diff notices.

`commits` returns `total`, `files` (the same freshness fields without diff content), and
`commits` in GitHub's source order. Each commit includes SHA, full message, author name,
committer timestamp (`committed_at`), GitHub URL, and raw payload. SQLite applies
`--limit` (1–500, default 100) and `--offset`. Force pushes replace current membership;
removed commits and historical diff versions are not separately archived.
Both commands' text output includes head/base identity, fetch time and freshness, with
the patch or full commit messages and links. JSON results match the dashboard's REST
responses and the `files`/`commits` MCP tools over HTTP and stdio; see
[MCP calls](mcp.md#read-pr-diffs-and-commits) for concrete arguments. Browser-local Viewed
checkboxes are presentation state; reading through any interface does not acknowledge work.

Both commands reject issues and unknown references. Missing coverage returns a structured
result with `stale: true`; reads still exit 0, so callers must inspect freshness.
Sync refreshes open PR metadata on every run, fetches code for new/changed head/base pairs,
backfills missing historical PR code after upgrade, and refreshes all PR code on full
reconciliation. Code is checked against head/base and commit count again after fetching.
A failed page, incomplete commit list, invalid diff, or concurrent source change retains
the previous repository snapshot/checkpoints and reports a partial sync. Previous code
remains explicitly stale. An indexed head mismatch hides the old diff.

Freshness describes the latest observed sync, with no automatic age expiry. Run `sync`
to discover later commits; opening a code tab does not fetch GitHub. The current
[GitHub commits endpoint](https://docs.github.com/en/rest/pulls/pulls#list-commits-on-a-pull-request)
caps listings at 250 commits. Count mismatches, including larger PRs, report incomplete
coverage instead of storing a truncated list. GitHub may also refuse very large diffs.
Reading code never acknowledges processing or authorizes executing source content.

REST exposes the same contracts at `GET /api/items/{item_id}/files` and
`GET /api/items/{item_id}/commits?limit=100&offset=0`; MCP exposes `files` and `commits`.

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
gh-maintainer triage OWNER/REPO#42 planned --note 'Reviewed; reproduce next' --revision 3 --finish-review --actor maintainer
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

`triage --finish-review --revision N` saves the decision and processing checkpoint in
one transaction. It requires the revision actually inspected and complete item/comment
coverage; a rejected or failed write saves neither. `--actor` identifies the reviewer
(default `manual`). When `--policy` is supplied, both `triage` and `mark-processed`
permissions are required. Ordinary triage continues to save a decision without processing.

## Find unprocessed items and new activity

```bash
gh-maintainer prs --unprocessed
gh-maintainer show OWNER/REPO#42 --format json
gh-maintainer changes OWNER/REPO#42
gh-maintainer mark-processed OWNER/REPO#42 --revision 3 --actor agent/v1
gh-maintainer prs --state all --processing changed
gh-maintainer changes --format json
```

Reading an item, saving analysis, and ordinary triage do not mark it processed. Use
`mark-processed` after inspecting the item and its conversation. It requires successful
item/comment coverage and saves a shared per-item checkpoint with the actor and time.
Alternatively, use `triage --finish-review --revision N` to save the decision together
with that acknowledgement, as the dashboard's **Finish review & next** action does.
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

Item JSON includes `saved_decision` (the latest stored disposition/note/timestamp) and
`follow_up`, an optional summary with `baseline` (`review` or `decision`), `since_revision`,
`since_at`, normalized activity `kinds`, `history_gap`, and `decision_at_review`.
Summary kinds include `pushed`, `author_replied`, and `checks_changed`; observation history
retains its existing source kinds. Lists aggregate these reasons in SQLite without
fetching conversations or issuing per-item queries.

`show` adds `follow_up_changes`, containing a total and the first 20 described entries
(`id`, `observed_at`, source `kind`, `label`, bounded plain-text `detail`, and `url`).
Use `changes ITEM` to paginate raw retained evidence through the existing CLI contract.
HTTP clients can request `/api/items/ID/follow-up?revision=N&through_revision=M&limit=20&offset=0`
to keep described pages within the inspected revision. The same `show` fields are
available through MCP. Text `show` prints the saved decision and activity preview.

`history_gap` means some intervening revisions have no retained observations. Its absence
does not establish a complete event archive: pruning can remove part of a revision and
polling cannot reconstruct unseen transitions. A `decision_at_review` value of `false`
explicitly identifies a latest local decision edited after acknowledgement; no local
decision-history table or historical note snapshot is implied.

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
