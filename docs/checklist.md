# Implementation checklist

Agents maintain this checklist alongside implementation changes. The [roadmap](roadmap.md)
defines scope and acceptance criteria; [AGENTS.md](https://github.com/jufty-bot/gh-maintainer/blob/main/AGENTS.md)
defines the maintenance workflow. The CLI baseline, six skills, policy and technical context are
implemented; further provider/host coverage and scale extensions remain. Checked items describe verified work. Add code/test references
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

## Dashboard Markdown, conversations and repository statistics

- [x] Render titles, repository descriptions and note previews with the existing sanitized
  Markdown renderer; preserve the editable note and local triage controls.
- [x] Expose paginated indexed comments through the HTTP API and display conversation
  comments, review state, author/date/source links and inline path/line/diff context.
- [x] Show conversation coverage and independent retry/load-more controls without losing
  item details or draft decisions.
- [x] Show repository stars/forks with GitHub links, formatted counts, explicit zeros and
  unknown-value placeholders; seed representative synthetic statistics and discussions.

Evidence: `tests/test_reports_web.py` validates the comments API, pagination, source
context and invalid requests. `tests/frontend/workspace.spec.cjs` covers Markdown
sanitization, comments, inline context, retries, note previews and statistics on desktop
and mobile. Validation on integrated main: all 266 Python tests and 16 browser tests
passed; Ruff lint/format, mypy, strict MkDocs, source/wheel builds and `git diff --check`
passed. The existing conversation tables and ingestion contracts are unchanged.

## CLI configuration

- [x] Accept explicit JSON/YAML files before or after commands with Pydantic validation.
- [x] Share top-level `repositories` across sync/watch, with command and CLI overrides.
- [x] Preserve Click commands, repository policy, environment defaults and database management.
- [x] Generate the published JSON Schema with `python -m gh_maintainer.config`.

Evidence: `tests/test_config.py` covers formats, precedence, positionals, flags,
invalid input, schema generation and a real offline demo/report workflow.
See [configuration](configuration.md) for public contracts and schema regeneration.
Validation on integrated main: all 265 tests passed with `uv run --locked pytest -q`;
Ruff lint/format, mypy, strict MkDocs, `uv build`, and `git diff --check` passed.

## Sync logging

- [x] Use structlog with readable text on stderr for `sync` and `watch`, preserving
  JSON results on stdout. Include run/repository context, fetch progress, counts,
  elapsed time, and explicit warnings/errors for partial or failed syncs.
- [x] Format issue and comment sync `since` values as ISO UTC strings, preserving
  microseconds and keeping full-sync values as `None`. Covered by the logging contract tests.
  Validation: 15 logging/sync tests, Ruff lint/format, mypy, and strict MkDocs passed.

Evidence: `logging.py`, `sync.py`, `cli.py`, and
[logging contract tests](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_logging.py).
Validation after integrating current main: all 99 tests passed, including five logging
cases. Ruff lint/format, mypy, strict MkDocs, `uv build`, and `git diff --check` passed.
See [sync usage](usage.md#select-repositories) for output streams and coverage.

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

- [x] Replace argparse with Click, add descriptive help/defaults and version output,
  validate arguments before storage initialization, and preserve command/output contracts.
  Evidence: [Click entry point](https://github.com/jufty-bot/gh-maintainer/blob/main/src/gh_maintainer/cli.py),
  [CLI boundary tests](https://github.com/jufty-bot/gh-maintainer/blob/main/tests/test_cli.py),
  and the offline workflow in the maintenance tests now invoke the real Click commands.
  Focused validation: `uv run pytest tests/test_cli.py tests/test_cli_db.py tests/test_selection.py tests/test_maintenance.py -q`
  passed all 60 tests. Full validation after integrating current main:
  `uv run --locked pytest -q` passed all 94 tests;
  Ruff lint/format, mypy, strict MkDocs, `uv build`, and `git diff --check` passed.

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
- [x] Ship the first local triage skill, tracked below.
- [ ] Complete the remaining maintenance skills and context stages.

Baseline validation: `uv run pytest -q` passed all 55 tests, including offline CLI use,
interruption/retry, comment pagination and edits, processing freshness, and legacy upgrades.
Ruff lint/format, mypy, strict MkDocs, Alembic schema comparison, and package builds passed.

## Agent skills and context

Scope and acceptance criteria: [agent skills roadmap](agent-skills.md). Checked tasks
describe shipped work; unchecked tasks remain open. Keep evidence beside each completed
task and split partial work explicitly. Current contracts are documented in [context](context.md).

### S1. Local maintenance skills

- [x] Define the first triage skill's specification, implementation approach, and ordered
  tasks using Spec Kit template structure. See the [plan](specs/001-local-triage/plan.md)
  and [feature task list](specs/001-local-triage/tasks.md).
  Planning validation: strict MkDocs, local Markdown link checks, unique/ordered task IDs,
  and `git diff --check` passed. No runtime code changed.
- [x] Choose the first skill's directory/package layout and document the decision in [D11](decisions.md#d11-repository-owned-triage-skill-over-public-commands).
- [x] Deliver coverage-inspection and local issue/PR triage instructions with [runnable examples](skills.md).
- [x] Deliver grounded summaries and policy-driven optional classification/rank instructions in the first skill.
- [x] Extend/evaluate standalone prioritization and classification workflows with repository-specific policies.
- [x] Deliver follow-up instructions for never-processed and changed items, including closed/merged items.
- [x] Require pagination, source references, repository policy inputs, and inspected-revision guards in those workflows.
- [x] Validate initial completion/waiting behavior against offline fixtures and document author-run limitations.
- [x] Validate SC-001 in an independent agent session without implementation-session context.

Evidence: `skills/gh-maintainer-triage/`, `tests/fixtures/triage/scenarios.json`,
`tests/triage_fixtures.py`, `tests/test_triage_skill.py`, and
[13 author-run evaluation cases](evaluations/local-triage.md) with a retained public
transcript. All 113 tests passed, including the 25 focused skill/maintenance checks.
Ruff lint/format, application and fixture-helper mypy, strict MkDocs, the skill-creator
validator, local links, and `git diff --check` passed. These are distinct forms of evidence;
these first-skill results preceded the independent release evaluation below.
No runtime code or dependency changed in that initial triage delivery.

### S2. Repository and change context

- [x] Design the smallest public context contracts needed by a concrete skill.
- [x] Retrieve repository guidance and identify its source/version and applicable scope.
- [x] Retrieve changed files/diffs and relevant code bound to base/head SHAs.
- [x] Retrieve individual CI check details/logs and distinguish old-head results.
- [x] Retrieve outstanding review/thread state and related work/policy evidence as needed.
- [x] Assemble a bounded context packet with provenance, freshness, pagination, and explicit missing coverage.
- [x] Test changed heads/guidance, missing artifacts, incomplete credentials, and truncated output.

### S3. Technical maintenance skills

- [x] Deliver PR review instructions with file/line evidence and a head-freshness check.
- [x] Deliver CI diagnosis instructions with reproducible checks and useful next actions.
- [x] Deliver issue reproduction/fix instructions with repository-specific validation and reviewable patches.
- [x] Define authorized external-action contracts, outcome references, and retry/intervening-change handling for the workflows that need them.

### S4. Portfolio workflows

- [x] Deliver backlog/dependency maintenance workflows with explicit scope and policy.
- [x] Deliver release-preparation workflows grounded in the repository's release process.
- [x] Deliver bounded recurring reviews and useful handoffs that resume from stored revisions.

### S5. Packaging and evaluation

- [x] Document first-skill installation, tool baseline, provenance version, and direct-loading method in [skill usage](skills.md).
- [x] Package the first skill with self-contained references; validate a clean copy and documented offline inputs.
- [x] Independently validate installation/direct loading and task completion in fresh sessions.
- [ ] Evaluate native automatic discovery across additional agent hosts.
- [x] Evaluate initial pagination, stale-write, missing-context, prior-decision, and hostile-source cases for the first skill.
- [x] Add bounded technical-review and local-repair evaluations; preserve exact-head and before/after evidence.
- [ ] Extend failing-provider, remote-action and dependency/release-preparation behavioral coverage.
- [x] Record reproducible initial evidence and public CLI compatibility in the [evaluation report](evaluations/local-triage.md).
- [ ] Grow independent and boundary-case evaluations as skills and public CLI contracts change.

## Complete maintenance release (0.2.0)

Scope: [specification](specs/002-complete-release/spec.md), [tasks](specs/002-complete-release/tasks.md).
Evidence: [release acceptance](evaluations/release.md), including independent evaluator
transcripts and real-source pilot results. These checks cover the requested release scope;
provider/host and broad remote-action evaluations above remain explicit extensions.

- [x] Ship six self-contained skills, wheel resources, public installation and a versioned ZIP/manifest/checksums.
- [x] Add explicit policy provenance and optional guarded label/rank/disposition/permission checks.
- [x] Add live commit-bound guidance, patches/files, checks/job logs and review-thread context with explicit limits.
- [x] Validate installed-wheel query/analysis/processing and legacy upgrade outside the checkout.
- [x] Configure Python matrix, desktop/mobile browser and quality/docs/build/install gates on PRs and direct main pushes.
- [x] Independently evaluate bounded triage, policy ranking, no-write runs, preservation, hostile content and actual retry exhaustion.
- [x] Independently exercise bounded technical review, CI evidence and reproduced local repair with red/green regression.
- [x] Complete real public-source sync, guarded local summary/ack and unchanged-resume pilot in a disposable index.
- [x] Fix the independently discovered legacy-adoption validation defect with five malformed-schema regressions.
- [x] Confirm hosted main CI, including Docker bundle smoke.
- [x] Publish matching versioned wheel/source/skills/checksums and verify downloaded assets.

Local validation: all 137 Python tests and 12 desktop/mobile browser tests passed;
Ruff lint/format, application mypy, strict documentation build, local links, six skill
validators, source/wheel build, skill ZIP/checksums and isolated installed-wheel smoke
passed. Chromium used local extracted libraries and font configuration because the
development machine lacks system packages; CI installs standard browser dependencies.
Hosted [release-commit CI](https://github.com/jufty-bot/gh-maintainer/actions/runs/39071013171)
passed all seven jobs, including Python 3.10–3.14, browser tests and Docker.
[Release 0.2.0](https://github.com/jufty-bot/gh-maintainer/releases/tag/v0.2.0) targets
`cd559b510253b1b2d3433d6c76684892bb58abd0`. Downloaded artifacts match the tested bytes;
checksums, skill manifest and a new isolated install/demo succeeded. Exact hashes and
results are in [publication evidence](evaluations/published-0.2.0.json). No new runtime
dependency or database schema was introduced. The initial migration's legacy-adoption
validation now rejects incompatible keys, types and required nullability before adoption.

## Documentation maintenance

- [x] Keep a concise README that states the purpose, current status, and quick start.
- [x] Provide separate usage, architecture, roadmap, and checklist documents.
- [x] Add AGENTS.md instructions for keeping planning documents current with implementation.
- [x] Provide project orientation, design rationale, an architecture data map, and a staged skills/context roadmap.

Documentation validation passed: `uv run --locked --group docs mkdocs build --strict`
and `git diff --check`. Local Markdown file/heading links and README/home-page consistency
were checked; the credential-free orientation demo/status/detail examples were exercised.


## Async incremental sync and request pacing

- [x] Replace the serialized SDK worker with pooled async HTTPX requests for owner inventories, direct repository/issue reads, conversations, reviews and PR signals.
- [x] Bound repository, PR-detail and review workers while preserving deterministic errors, logs, atomic checkpoints, conversation coverage and local revisions.
- [x] Target incremental reads to incoming items, available PRs needed for review coverage and comment parents; reuse unchanged closed PR details and avoid new-item per-row reads.
- [x] Add shared aiolimiter pacing, separate concurrency/rate options, primary-budget feedback, GraphQL HTTP 200 rate-limit handling and cancellation/cooldown regressions.
- [x] Integrate with the current Click CLI, owner selection, installed skills, release packaging and schema without discarding the intervening main changes.

Evidence: `github.py`, `concurrency.py`, `sync.py`, `repositories.py`,
`tests/test_github.py`, `tests/test_sync.py`, `tests/test_selection.py`,
`tests/test_maintenance.py`, `tests/test_logging.py` and `tests/test_cli.py`.
All 186 Python tests passed on Python 3.14 and 3.10. Ruff lint/format, application mypy,
strict documentation build, source/wheel build and isolated installed-wheel smoke
(including legacy migration) passed. Browser behavior and GitHub write operations are
unchanged. See [D13](decisions.md#d13-bounded-parallel-ingestion-with-shared-request-pacing)
for rationale and [Usage](usage.md#correctness-and-limits) for operating limits.

## Portfolio sync efficiency

- [x] Batch open-PR signals across bounded repository windows, query the head directly,
  and preserve the public stored signal shape; explicit repository lookups use bounded workers.
- [x] Preserve repository source atomicity, lease/cancellation cleanup and prior signals
  when any batch for that repository fails; allow unaffected signal batches to commit.
- [x] Skip unchanged inventory metadata/membership writes and reconcile only missing
  scoped IDs, with bounded SQLite parameter lists and bulk writes.
- [x] Load full snapshots for incoming/open PRs once, historical review candidates as
  numbers, and comments by incoming IDs or fully refreshed review scope; reuse loaded parents.
- [x] Retain full raw payloads while excluding embedded account profiles and PR head/base
  repository statistics from revision invalidation; preserve meaningful and unknown fields.
- [x] Avoid repeated full item serialization and assigning unchanged fields; serialize
  observation snapshots once even when multiple event kinds are recorded.
- [x] Exercise 201-repository backfill/incremental runs, 1,201-repository discovery and
  disappearance/reappearance, 601-comment incremental edits, bounded staged-object lifetime,
  profile-only changes, review dismissal, partial signal batches and unchanged-head CI changes.

Evidence: `tests/test_sync_efficiency.py`, `tests/test_sync.py`, `tests/test_github.py`,
`tests/test_selection.py`, `tests/test_maintenance.py` and `tests/test_logging.py`.
All 205 Python tests passed on Python 3.14 and 3.10. Ruff lint/format, application mypy,
strict MkDocs, `git diff --check`, source/wheel build and the isolated installed-wheel
smoke (including legacy migration) passed. Offline signal-count fixtures reduced 201 requests to 51 at concurrency 4
and 11 at concurrency 20; these are request counts, not live GitHub throughput benchmarks.
The database schema and public CLI/JSON shapes are unchanged. See
[D14](decisions.md#d14-reduce-portfolio-work-without-reducing-coverage) and the
[roadmap](roadmap.md#portfolio-sync-efficiency) for semantics, tradeoffs and remaining work.

- [ ] Endpoint-level request/byte/change telemetry and heterogeneous portfolio measurements.
- [ ] Persistent conditional REST requests and fully covered batched review probes.
- [ ] Reduce open-PR detail requests with complete invalidation/freshness evidence.
- [ ] Evaluate rolling scheduling, staggered reconciliation and explicit polling cadence.

Completion criteria and investigation order belong to the
[portfolio roadmap](roadmap.md#portfolio-sync-efficiency). Full historical review listings,
the three-feed-per-repository request floor and interrupted backfill restarts remain explicit.

## Repository activity

- [x] Record each repository's latest default-branch tip, designated latest release,
  newest-created PR and newest-created issue, with identities, dates, links and raw evidence.
- [x] Fetch all four records in GraphQL batches of up to 20 repositories; validate stable
  repository IDs and required nullable fields; isolate partial alias errors.
- [x] Keep independent activity observation times/errors, retain prior snapshots on failure,
  mark failed runs partial and distinguish never observed, known absence and stale coverage.
- [x] Add packaged migration `0004`; preserve existing source/history, revisions,
  analysis and processing in legacy and explicit `0003` upgrades.
- [x] Expose typed activity and coverage in `repos` JSON and identities/dates in text;
  seed representative synthetic demo data and document the public selection contract.
- [x] Validate bounded 201-repository activity requests, empty repositories, partial/malformed
  responses, force pushes/default-branch changes, failure retention/recovery, independent
  conversation coverage, local analysis freshness and offline CLI output.

Evidence: `tests/test_repository_activity.py`, `tests/test_sync_efficiency.py`,
`tests/test_migrations.py`, the HTTP-backed sync test and `scripts/check_install.py`.
All 218 Python tests passed on Python 3.14 and 3.10. Ruff lint/format, application mypy,
strict MkDocs, `git diff --check`, source/wheel builds and the isolated installed-wheel
smoke (including demo activity and legacy migration) passed. See the
[activity contract](cli.md#repository-activity),
[architecture](architecture.md#repository-activity-snapshots) and
[D15](decisions.md#d15-repository-recency-with-independent-coverage).
This implements latest snapshots; full commit/release history and an activity event archive
remain outside scope. The source/item observation history and local decisions are retained.
