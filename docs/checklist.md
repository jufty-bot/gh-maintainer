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

- [ ] Add migrations and validated records for issue comments, review bodies, and inline
  review comments with parent/reply/thread context.
- [ ] Backfill all conversation pages, including closed issues and merged PRs.
- [ ] Add incremental refresh/checkpoints that capture edits independently of parent timestamps.
- [ ] Reconcile availability and retain history without treating partial listings as deletion.
- [ ] Expose conversation coverage and preserve checkpoints on failed fetches.
- [ ] Test pagination, edits, replies, idempotence, partial failure, and reconciliation.
- [ ] Document working commands and update the roadmap status.

## 3. CLI queries

Acceptance criteria: [roadmap milestone 3](roadmap.md#3-query-the-index-through-the-cli).

- [ ] Expose repository, item, conversation, and sync coverage list/detail commands.
- [ ] Add SQL-backed filters and deterministic pagination, including triage disposition.
- [ ] Specify readable output, JSON contracts, identifiers, freshness, and exit codes.
- [ ] Test offline queries without credentials, empty results, and failures.
- [ ] Document working commands and update the roadmap status.

## 4. Persisted analysis and triage

Acceptance criteria: [roadmap milestone 4](roadmap.md#4-persist-summaries-classifications-rankings-and-triage).

- [ ] Add summary, classification, and ranking tables with source links and provenance.
- [ ] Define freshness for item/comment changes and time-dependent rankings.
- [ ] Expose CLI write/query operations for analysis results and ranked queues.
- [ ] Preserve source records and human triage decisions when recomputing analysis.
- [ ] Test upgrades, repeatable writes, provenance, freshness, and queries.
- [ ] Document working commands and update the roadmap status.

## Baseline completion and agent skills

- [ ] Validate the [baseline completion gate](roadmap.md#baseline-completion-gate) through
  the CLI, including interruption, partial coverage, reconciliation, and database upgrades.
- [ ] Document the complete workflow and its output contracts for humans and agents.
- [ ] After the baseline is complete, build skills for inspecting coverage, reading
  conversations, summarizing, classifying, ranking, and recording maintenance decisions.

## Documentation maintenance

- [x] Keep a concise README that states the purpose, current status, and quick start.
- [x] Provide separate usage, architecture, roadmap, and checklist documents.
- [x] Add AGENTS.md instructions for keeping planning documents current with implementation.

Documentation setup validation passed: `uv run --locked --group docs mkdocs build --strict`,
`git diff --check`, and Ruff lint/format checks for `docs/gen_pages.py`.
