# Implementation checklist

Agents maintain this checklist alongside implementation changes. The [roadmap](roadmap.md)
defines scope and acceptance criteria; [AGENTS.md](https://github.com/jufty-bot/gh-maintainer/blob/main/AGENTS.md)
defines the maintenance workflow. Checked items describe existing work, not completion of
the whole baseline. Add code/test references and validation evidence when closing a task.
Split partial work into separate tasks instead of marking an entire milestone complete.

## Existing foundation

- [x] Discover owner inventories or resolve explicit repositories with repeatable `--repo`.
  See the [source adapter](https://github.com/jufty-bot/gh-maintainer/blob/main/src/gh_maintainer/github.py)
  and [CLI](https://github.com/jufty-bot/gh-maintainer/blob/main/src/gh_maintainer/cli.py).
- [x] Backfill open/closed issues and open/closed/merged PRs, then refresh incrementally
  with overlap and periodic reconciliation. See [sync](https://github.com/jufty-bot/gh-maintainer/blob/main/src/gh_maintainer/sync.py) and
  [sync contract tests](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_sync.py).
- [x] Persist source records, observations, checkpoints, sync runs, and local triage in
  SQLite with versioned migrations. See [models](https://github.com/jufty-bot/gh-maintainer/blob/main/src/gh_maintainer/models.py) and
  [migration tests](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_migrations.py).
- [x] Refresh open PR signals independently of issue timestamps and expose partial coverage.
  See [sync contract tests](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_sync.py).
- [x] Provide SQL-backed dashboard queries, computed queue ranking, local triage, and report
  exports. See [queries](https://github.com/jufty-bot/gh-maintainer/blob/main/src/gh_maintainer/queries.py) and
  [query/report tests](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_reports_web.py).

These references establish the starting inventory; they do not record a fresh test run.

## 1. Repository selection and coverage

Acceptance criteria: [roadmap milestone 1](roadmap.md#1-repository-selection-and-coverage).

- [x] Specify repository-list and named-owner CLI selection, including validation.
- [x] Migrate the single-owner storage contract to support explicit mixed-owner lists.
- [x] Resolve selected repositories directly and discover named user/organization owners.
- [x] Scope presence reconciliation so subset syncs retain unrelated repository coverage.
- [x] Test pagination, access failures, renames/transfers, scope changes, and legacy upgrades.
- [x] Document working commands and update the roadmap status.

Evidence: [selection and CLI tests](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_selection.py),
[SDK pagination/routing tests](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_github.py),
[migration preservation tests](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_migrations.py),
and [selection usage](usage.md#select-repositories). Validation: `uv run pytest -q`
passed all 41 tests; Ruff lint/format and `uv run mypy src` passed.
Strict MkDocs build, `uv build`, and Alembic's schema comparison also passed.

## 2. Issue and PR conversations

Acceptance criteria: [roadmap milestone 2](roadmap.md#2-complete-issue-and-pr-conversations).

- [x] Add migrations and validated records for issue comments, review bodies, and inline
  review comments with parent/reply/thread context.
- [x] Backfill all conversation pages, including closed issues and merged PRs.
- [x] Add incremental refresh/checkpoints that capture edits independently of parent timestamps.
- [x] Reconcile availability and retain history without treating partial listings as deletion.
- [x] Expose conversation coverage and preserve checkpoints on failed fetches.
- [x] Test pagination, edits, replies, idempotence, partial failure, and reconciliation.
- [x] Document working commands and update the roadmap status.

Evidence: migration `0003`, [SDK conversation tests](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_github.py),
and [maintenance contracts](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_maintenance.py).
Raw REST reply/path context is retained; GraphQL thread resolution remains a later extension.

## 3. CLI queries

Acceptance criteria: [roadmap milestone 3](roadmap.md#3-query-the-index-through-the-cli).

- [x] Expose repository, item, conversation, and sync coverage list/detail commands.
- [x] Add SQL-backed filters and deterministic pagination, including triage disposition.
- [x] Specify readable output, JSON contracts, identifiers, freshness, and exit codes.
- [x] Test offline queries without credentials, empty results, and failures.
- [x] Document working commands and update the roadmap status.

Evidence: [CLI workflows and output contracts](cli.md), the offline workflow in
[maintenance tests](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_maintenance.py),
and existing SQL pagination/parity tests.

## 4. Persisted analysis and triage

Acceptance criteria: [roadmap milestone 4](roadmap.md#4-persist-summaries-classifications-rankings-and-triage).

- [x] Add summary, classification, and ranking tables with source links and provenance.
- [x] Define freshness for item/comment changes and time-dependent rankings.
- [x] Expose CLI write/query operations for analysis results and ranked queues.
- [x] Preserve source records and human triage decisions when recomputing analysis.
- [x] Test upgrades, repeatable writes, provenance, freshness, and queries.
- [x] Document working commands and update the roadmap status.

Evidence: migration `0003`, [maintenance tests](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_maintenance.py),
and [analysis/processing contracts](cli.md#save-analysis-and-triage). Scores supplied by callers
are fixed until refreshed; source changes invalidate their freshness.

- [x] Track shared processing checkpoints separately from reads, analysis, and local triage.
- [x] Show never-processed and changed-since-processing queues, including closed items.
- [x] Record pushes, comments/edits, reviews, closures, merges, and reopenings as observations.
- [x] Reject stale analysis, triage, and acknowledgement writes when a revision guard is supplied.

## Baseline completion and agent skills

- [x] Validate the [baseline completion gate](roadmap.md#baseline-completion-gate) through
  the CLI, including interruption, partial coverage, reconciliation, and database upgrades.
- [x] Document the complete workflow and its output contracts for humans and agents.
- [ ] After the baseline is complete, build skills for inspecting coverage, reading
  conversations, summarizing, classifying, ranking, and recording maintenance decisions.

Baseline validation: `uv run pytest -q` passed all 55 tests, including offline CLI use,
interruption/retry, comment pagination and edits, processing freshness, and legacy upgrades.
Ruff lint/format, mypy, strict MkDocs, Alembic schema comparison, and package builds passed.

## Documentation maintenance

- [x] Keep a concise README that states the purpose, current status, and quick start.
- [x] Provide separate usage, architecture, roadmap, and checklist documents.
- [x] Add AGENTS.md instructions for keeping planning documents current with implementation.

Documentation setup validation passed: `uv run --locked --group docs mkdocs build --strict`,
`git diff --check`, and Ruff lint/format checks for `docs/gen_pages.py`.
