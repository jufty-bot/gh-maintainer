# Implementation checklist

Agents maintain this checklist alongside implementation changes. The [roadmap](roadmap.md)
defines scope and acceptance criteria; [AGENTS.md](https://github.com/jufty-bot/gh-maintainer/blob/main/AGENTS.md)
defines the maintenance workflow. The CLI baseline is implemented; skills and richer
context are the next work. Checked items describe verified work. Add code/test references
and validation evidence when closing a task.
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

## Database file management

- [x] Use `platformdirs` for the default user data location in the CLI and standalone Alembic.
- [x] Preserve `--db` / `GH_MAINTAINER_DB` overrides and leave existing indexes in place.
- [x] Add `db` / `db path` inspection and explicit `db delete --yes`, including SQLite sidecars.
- [x] Test path precedence, inspection without creation, deletion confirmation, sidecars,
  missing files, filesystem errors, and in-memory database rejection.

Evidence: `database.default_path`, `cli.py`, `migrations/env.py`, and
[database CLI tests](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_cli_db.py).
Validation: all 66 tests passed, including 11 database-management cases. Ruff lint/format,
mypy, strict MkDocs, and `git diff --check` passed.
See [database management](cli.md#manage-the-database) for usage and existing-index handling.

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
- [ ] Ship maintenance skills, tracked by stage below.

Baseline validation: `uv run pytest -q` passed all 55 tests, including offline CLI use,
interruption/retry, comment pagination and edits, processing freshness, and legacy upgrades.
Ruff lint/format, mypy, strict MkDocs, Alembic schema comparison, and package builds passed.

## Agent skills and context

Scope and acceptance criteria: [agent skills roadmap](agent-skills.md). All implementation
tasks below are future work; documenting them does not imply that skills or new context
commands exist. Keep evidence beside each completed task and split partial work explicitly.

### S1. Local maintenance skills

- [ ] Choose the first skill's directory/package layout and document the decision.
- [ ] Deliver coverage-inspection and local issue/PR triage instructions with runnable examples.
- [ ] Deliver grounded summarization/classification and prioritization workflows using local analysis commands.
- [ ] Deliver follow-up instructions for never-processed and changed items, including closed/merged items.
- [ ] Require pagination, source references, repository policy inputs, and inspected-revision guards in those workflows.
- [ ] Validate completion/waiting behavior against representative offline fixtures and document limitations.

### S2. Repository and change context

- [ ] Design the smallest public context contracts needed by a concrete skill.
- [ ] Retrieve repository guidance and identify its source/version and applicable scope.
- [ ] Retrieve changed files/diffs and relevant code bound to base/head SHAs.
- [ ] Retrieve individual CI check details/logs and distinguish old-head results.
- [ ] Retrieve outstanding review/thread state and related work/policy evidence as needed.
- [ ] Assemble a bounded context packet with provenance, freshness, pagination, and explicit missing coverage.
- [ ] Test changed heads/guidance, missing artifacts, incomplete credentials, and truncated output.

### S3. Technical maintenance skills

- [ ] Deliver PR review instructions with file/line evidence and a head-freshness check.
- [ ] Deliver CI diagnosis instructions with reproducible checks and useful next actions.
- [ ] Deliver issue reproduction/fix instructions with repository-specific validation and reviewable patches.
- [ ] Define authorized external-action contracts, outcome references, and retry/intervening-change handling for the workflows that need them.

### S4. Portfolio workflows

- [ ] Deliver backlog/dependency maintenance workflows with explicit scope and policy.
- [ ] Deliver release-preparation workflows grounded in the repository's release process.
- [ ] Deliver bounded recurring reviews and useful handoffs that resume from stored revisions.

### S5. Packaging and evaluation

- [ ] Document installation targets, required tool versions, and skill compatibility policy.
- [ ] Make the first shipped skill installable and runnable by another agent from repository docs.
- [ ] Evaluate relevant pagination, stale-write, missing-context, prior-decision, and untrusted-content cases for each skill.
- [ ] Add technical-review/action regression cases as S2–S4 capabilities land.
- [ ] Record reproducible evaluation evidence and keep skill instructions aligned with public CLI changes.

## Documentation maintenance

- [x] Keep a concise README that states the purpose, current status, and quick start.
- [x] Provide separate usage, architecture, roadmap, and checklist documents.
- [x] Add AGENTS.md instructions for keeping planning documents current with implementation.
- [x] Provide project orientation, design rationale, an architecture data map, and a staged skills/context roadmap.

Documentation validation passed: `uv run --locked --group docs mkdocs build --strict`
and `git diff --check`. Local Markdown file/heading links and README/home-page consistency
were checked; the credential-free orientation demo/status/detail examples were exercised.
