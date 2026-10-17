# Design decisions

This log records decisions reflected in the current implementation and their rationale.
It is not a list of future features. Changes to these choices should update this page,
the [architecture](architecture.md), and affected contracts/tests in the same change.
Proposed work belongs in the [roadmap](roadmap.md) and [agent skills plan](agent-skills.md).

## D1. A CLI foundation with skills layered above it

**Decision:** The CLI owns ingestion, querying, local decisions, and processing state.
Skills will teach agents how to use those operations for maintenance. Dashboards and
reports consume the same index.

**Why:** A human can inspect and reproduce an agent's work through the same interface.
Workflow instructions can evolve without duplicating transport, pagination, or persistence.
Local query/analysis commands also work without a model service or a running web server.

**Tradeoff:** Agents still need instructions, repository guidance, code context, and
authorization for the requested work. Having indexed a PR does not constitute a code review.
Six bundled maintenance skills are available; the CLI does not run an agent or generate analysis itself.

Evidence: command entry point in `cli.py`, query/write boundaries in `queries.py` and
`repositories.py`, and the offline workflow in `tests/test_maintenance.py`.

## D2. Small project code, established libraries

**Decision:** Use Pydantic for validated source/output contracts, SQLAlchemy with aiosqlite
for typed SQLite persistence, Alembic for migrations, and PyGithub for GitHub access.
Click supplies command discovery, help, parameter validation, and version output;
FastAPI supplies the optional dashboard API. Click replaces the original argparse entry
point while preserving command names and output schemas, including database management.

**Why:** Libraries own validation, ORM mapping, pagination/transport, and schema upgrades
instead of duplicating them in project code. Prefer extending existing modules and
dependencies to adding general frameworks. Preserve the project's Python 3.10 support.
Click keeps parsing separate from the existing async services and reports invalid
parameters before database initialization. Boundary tests exercise the real command entry.

**Tradeoff:** SQLAlchemy models and Pydantic snapshots have distinct roles, and migrations
must still be reviewed. PyGithub is synchronous; the adapter runs all SDK work, including
lazy pagination, on one dedicated worker thread. Library behavior needs adapter tests.

Evidence: `pyproject.toml`, `models.py`, `schemas.py`, `github.py`, and the SDK thread and
pagination tests in `tests/test_github.py`.

## D3. Reuse gh credentials; keep ingestion in PyGithub

**Decision:** `resolve_token()` uses `GH_TOKEN` / `GITHUB_TOKEN` when supplied; otherwise
it invokes `gh auth token --hostname github.com`. The token is passed explicitly into
PyGithub. PyGithub does not discover gh authentication on its own. On-demand technical
context uses authenticated `gh api` read requests, with explicit github.com targeting,
timeouts and commit provenance; it never changes the stored index.

**Why:** Existing gh logins are useful without making every API operation a subprocess or
requiring users to copy credentials into another configuration file. Local reads and
analysis never need the credential lookup.

**Tradeoff:** This is a standalone `gh-maintainer` command, not a packaged `gh maintainer`
extension. Current authentication targets github.com. A future extension, GitHub App, or
enterprise-host configuration is separate work. The Docker worker receives credentials;
the dashboard serves the existing database without them.

Evidence: `github.py:resolve_token`, `cli.py:run`, and `compose.yaml`.

## D4. Poll into SQLite with explicit coverage and recoverable checkpoints

**Decision:** Store source snapshots locally, fetch outside database transactions, and
commit a repository's staged source records, observations, availability, and checkpoints
together. Use overlap and periodic full reconciliation. Do not infer deletion from a
failed or partial listing. A fenced lease prevents competing sync workers from committing.

**Why:** Queries remain useful offline; failed fetches can be retried without skipping data
or replacing committed history with a partial page. Scoped inventory reconciliation keeps
an explicit list or a public-only scan from removing unrelated/private inventory.

**Tradeoff:** This is a polling index, not a complete event archive. SQLite has one writer,
staging uses memory per repository, and interrupted backfills restart that repository.
Review bodies require a full listing per available PR on each sync. GraphQL signal failures
are a deliberate exception: usable source/comment data can commit while old signals retain
their observation time and the run reports partial coverage.

Evidence: `sync.py`, `repositories.py:SyncLease`, and sync, selection, interruption, and
reconciliation tests. Timing and transaction details live in [architecture](architecture.md).

## D5. Stable identity plus raw snapshots

**Decision:** Use repository IDs and issue node IDs as item identities. Retain the PR's
distinct node ID for GraphQL enrichment. Store raw API payloads alongside normalized fields;
comments link to the repository/issue number pair and retain their source node IDs.

**Why:** Repository renames and transfers should preserve local item history and decisions.
Raw payloads preserve evidence and fields needed for later analysis without speculative
normalization of everything GitHub returns.

**Tradeoff:** Names and URLs are display/reference attributes, not primary identities.
Fields in raw payloads are not all normalized or indexed. A saved API payload is untrusted
source material, not instructions for the agent. References in analysis text are supplied
by the caller; the CLI does not validate their reasoning.

Evidence: `models.py`, snapshot conversion in `schemas.py`, and rename/transfer tests in
`tests/test_selection.py`.

## D6. Source, analysis, triage, and processing are different state

**Decision:** Keep source records separate from summaries, classifications, ranks, triage,
and the processing checkpoint. Analysis tables store one latest result per item/type,
including producer, reason, source revision, and save time. Processing is a shared per-item
acknowledgement with an actor and time. Reads and analysis saves do not acknowledge work.

**Why:** An item can be read without being processed, classified without being complete,
locally done while still open on GitHub, or ready for reconsideration after new activity.
Those distinctions are essential to resuming maintenance reliably.

**Tradeoff:** Saving another result replaces the previous result of that type, even when
the producer differs. There is no per-agent analysis history or independent per-agent
processing cursor. Source observations are retained, but they are not an audit log of all
local decisions. Add those capabilities only when a workflow needs them.

Evidence: `AnalysisService`, `TriageService`, and processing tests in `tests/test_maintenance.py`.

## D7. Revisions make freshness and acknowledgement explicit

**Decision:** Meaningful observed source changes advance an item's local revision once per
sync; identical polling does not. Analysis retains the revision it used, and optional
revision guards reject writes against an item that changed. Local done decisions return
to the inbox after later source activity. Marking processed requires successful item/comment
coverage and no recorded repository error.

**Why:** Parent timestamps alone miss activity such as edited comments or PR check changes.
An agent must be able to distinguish the revision it inspected from later source updates.

**Tradeoff:** A revision is not a Git SHA, a global clock, or proof of live remote freshness.
Guards are optional at the CLI; skill instructions should pass the inspected revision.
Incomplete repository coverage also makes saved analysis effectively stale. Fixed saved
ranks have no automatic age expiry; producers explicitly refresh time-dependent scores.

Evidence: `CatalogRepository.commit_repo`, freshness/rank filters in `queries.py`, and
comment-without-parent-update and stale-write tests in `tests/test_maintenance.py`.

## D8. Incremental schema upgrades preserve existing data

**Decision:** Commands that open an index initialize through packaged Alembic migrations;
database path/deletion commands do not open it. The initial
migration validates/adopts a known legacy schema; subsequent revisions add owner scopes,
conversations, analysis, and processing without rewriting the original source values.

**Why:** A user's accumulated notes and index should survive feature work. Migration
history must travel in wheels and containers, not depend on the source checkout.

**Tradeoff:** Unknown schemas are refused. Some downgrades are deliberately unavailable
because they would discard data. Legacy observations have no invented source revision.
Schema changes need upgrade/preservation tests and an Alembic comparison check.

Evidence: `database.py`, `migrations/versions/`, and `tests/test_migrations.py`.

## D9. Fetch richer maintenance context when a workflow needs it

**Current boundary:** The CLI stores item/conversation context and aggregate PR signals.
The `context` command now fetches guidance, diffs/files, individual checks/job logs and
review threads on demand. Branch rules and external CI-provider integrations remain
extensions; see [technical context](context.md).

**Implemented direction:** Public context operations are driven by skill needs. Reuse
GitHub/Git tooling and fetch large or rapidly changing artifacts on demand; retain source
references, relevant SHAs/revisions, timestamps, and missing coverage. The public
`context ITEM --format json` command is implemented with explicit section selection.

**Why:** Avoid turning SQLite into a speculative mirror of every repository artifact or
building a general agent platform before the workflows exist. Basic triage skills can
start with the public commands already available.

The [context contract](context.md) records the implemented collection/schema boundaries.
Caching, external providers and further action history remain extensions in the
[agent skills plan](agent-skills.md).


## D10. Platform user data directory and explicit deletion

**Decision:** Use `platformdirs` for the default SQLite location, with no version or author
subdirectory. Preserve `--db` and `GH_MAINTAINER_DB` overrides; share the default with
standalone Alembic. Add `db path` (also bare `db`) and `db delete --yes` without opening SQLite.

**Why:** A maintenance index should be usable from any checkout or working directory.
The library handles operating-system conventions and environment settings without custom
path logic. Inspection should not create an index, and deletion must also work for a
file that cannot be migrated. An explicit deletion flag makes scripted intent clear.

**Tradeoff:** Existing working-directory indexes do not move automatically, since they
can contain local decisions. Users select them explicitly. Deletion includes local state
and SQLite sidecars; callers must stop processes using the file first.

Evidence: `database.default_path`, `cli.py`, `migrations/env.py`, and
`tests/test_cli_db.py`. See [database management](cli.md#manage-the-database).

## D11. Repository-owned triage skill over public commands

**Decision:** Ship `skills/gh-maintainer-triage/SKILL.md` with relative policy/evaluation
references. Version its instructions/provenance as `gh-maintainer-triage/v1` and record
both CLI package version and checkout baseline. Copy the complete directory for agent
installation; direct file loading is the validated portable method. Python wheels now bundle resources; `skills --install` copies complete directories
without overwriting existing skills (see D12). The initial triage delivery needed no
workflow runner, dependency, table, or new CLI command.

**Why:** The current CLI already supports bounded local triage. Instructions can teach
coverage checks, all-state pagination, guarded writes, and grounded handoffs without
duplicating ingestion or persistence. Default to indexed freshness and summary/processing
updates; taxonomy, rank, and disposition changes require delegated policy. Freeze candidate
IDs before writes shrink pending queues. Preserve notes and snoozes by default.

**Tradeoff:** Processing is shared and writes are not an atomic batch. Revision guards
protect source revisions, not concurrent local-analysis changes. Guidance is supplied by
the caller; richer repository/code context remains separate work. Synthetic CLI tests and
author-run agent walkthroughs are recorded separately; neither establishes independent
agent reliability. There is no mandatory model evaluation service.

Evidence: [skill installation](skills.md), `tests/test_triage_skill.py`, and the
[evaluation report and public transcript](evaluations/local-triage.md).

## D12. Small policy, context and distribution additions

**Decision:** Version-1 JSON policy is explicit per repository and validated by Pydantic.
`policy` reports its absolute path/hash; optional `--policy` guards local writes using
exact label vocabularies, rank bands, dispositions and allowed commands. Budgets and
freshness are skill inputs. Manual writes without policy remain supported.

Technical context is fetched on demand through authenticated `gh api` in `context.py`,
outside SQLite transactions, with a bounded subprocess timeout. Packets retain observed
time, indexed revision, immutable head/base and blob identities, continuation, missing
evidence, truncation and a final head/base stability check. File reads handle forks;
guidance comes from the base revision. Context does not update processing or source data.

The existing Hatch build bundles all six skill directories. `skills --install` copies
them without overwriting; a release ZIP includes hashes/version provenance. CI validates
the built wheel outside the checkout, including a legacy upgrade, on every main push/PR.
Publication follows the same validation gates.

**Why:** These are the capabilities required by the requested end-to-end maintenance
workflows. Existing Click, Pydantic, Git/gh and standard-library packaging tools suffice;
there are no new runtime dependencies, schema tables or model/orchestration services.

**Tradeoff:** Context requires installed/authenticated gh and live access; offline index
commands remain available. String budgets bound returned evidence, not network transfer.
GitHub listing/size limits and nested reply gaps remain explicit. Source guards do not
lock concurrent local-analysis or policy-file edits. Policy is not a sandbox, and a
skill is not permission to publish remote changes.

Evidence: [policy](policy.md), [context](context.md), [releases](releases.md),
`tests/test_policy.py`, `tests/test_context.py`, `tests/test_skillpack.py`, and
[release validation](evaluations/release.md).
