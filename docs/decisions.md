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
for typed SQLite persistence, Alembic for migrations, HTTPX for async GitHub access, and aiolimiter for request pacing.
Click supplies command discovery, help, parameter validation, and version output;
FastAPI supplies the optional dashboard API. Click replaces the original argparse entry
point while preserving command names and output schemas, including database management.

**Why:** Libraries own validation, ORM mapping, HTTP transport, rate pacing, and schema upgrades
instead of duplicating them in project code. Prefer extending existing modules and
dependencies to adding general frameworks. Preserve the project's Python 3.10 support.
Click keeps parsing separate from the existing async services and reports invalid
parameters before database initialization. Boundary tests exercise the real command entry.

**Tradeoff:** SQLAlchemy models and Pydantic snapshots have distinct roles, and migrations
must still be reviewed. HTTPX replaces the synchronous PyGithub worker to allow bounded
parallel ingestion. The adapter owns GitHub pagination, payload validation, cooldowns and
budget feedback; library behavior needs contract tests.

Evidence: `pyproject.toml`, `models.py`, `schemas.py`, `github.py`, and the async transport, pacing and
pagination tests in `tests/test_github.py`.

## D3. Reuse gh credentials; keep ingestion in the async source adapter

**Decision:** `resolve_token()` uses `GH_TOKEN` / `GITHUB_TOKEN` when supplied; otherwise
it invokes `gh auth token --hostname github.com`. The token is passed explicitly into
the shared HTTPX client. On-demand technical
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


## D13. Bounded parallel ingestion with shared request pacing

**Decision:** Ingest repositories, PR details and review bodies with bounded async workers.
Use one pooled HTTPX client, a shared in-flight request cap, and aiolimiter with capacity
one to prevent bursts. Expose concurrency and requests per second separately. Track REST
and GraphQL primary budgets and slow resource-specific traffic at 10% remaining; honor
server cooldowns and GraphQL rate-limit errors in HTTP 200 responses.

**Why:** A single SDK thread serialized independent repository I/O. Incremental syncs
also reread all historical issues and refetched unchanged closed PR details. Targeted reads
and cached closed PR details reduce work while keeping review coverage, open PR freshness,
comment revisions and atomic repository checkpoints intact.

**Tradeoff:** Rate coordination is process-local. Initial backfills still stage a repository
in memory and restart it after interruption. Review bodies still require complete listings;
parallel requests remain subject to GitHub's primary and secondary limits. Requested waits
over 30 seconds defer work to a later sync. HTTP telemetry now counts attempts; older
SDK-operation counts retain their historical meaning. This supersedes D2/D3's original
PyGithub transport choice; authentication and local storage contracts are unchanged.

Evidence: `github.py`, `concurrency.py`, `sync.py`, `repositories.py`, and transport,
conversation, selection, cancellation and checkpoint tests.

## D14. Reduce portfolio work without reducing coverage

**Decision:** Reuse the existing source/catalog boundaries. Fetch repositories in bounded
concurrency windows, share signal requests of at most 20 PR nodes, and commit each
repository sequentially. Query the PR's head/rollup directly while retaining the stored
signal shape. Compare inventory in SQL batches, write only changed metadata/memberships,
and reconcile only scoped missing IDs. Load full snapshots for incoming/open items, PR
numbers for historical review coverage, and comments only for incoming IDs or fully
refreshed review scopes. Avoid repeated full-payload serialization on unchanged items.
Resolve explicit repository selections with bounded workers and record selection failures
in sorted inventory order after the read requests complete.

**Why:** A portfolio with one open PR per repository previously underfilled every GraphQL
request. Historical PR payloads and unrelated comments also scaled with accumulated data,
while owner discovery rewrote unchanged inventory. Raw embedded account profiles and
repository statistics could invalidate otherwise current analysis after incidental changes.
Revision comparisons now reduce those embedded profiles to identity fields while retaining
the latest full raw documents; other source fields, including unknown fields, stay significant.

**Tradeoff:** A slow repository holds up its bounded window; staging never spans the whole
portfolio. A failed shared signal batch can make all repositories represented in that batch
partial. Those repositories retain all their old signals/timestamps, including successful
parts of a multi-batch repository; other batches proceed. No schema migration, dependency,
automatic polling delay or weakened reconciliation is introduced. Existing history remains
intact; the comparison policy applies to future observations. Review bodies and open PR
details are still fetched each sync. Conditional caching, per-node error recovery and more
complex scheduling need their own coverage evidence before replacing this simple approach.

Evidence: `tests/test_sync_efficiency.py`, transport, selection, maintenance, cancellation
and checkpoint tests. See [portfolio efficiency](roadmap.md#portfolio-sync-efficiency) for
next candidates and [checklist](checklist.md#portfolio-sync-efficiency) for validation.

## D15. Repository recency with independent coverage

**Decision:** Store one typed latest commit/release/PR/issue snapshot, with raw source data,
on each repository. Migration `0004` adds its observation time/error independently from
item/comment/signal checkpoints. Fetch up to 20 repositories per GraphQL request after
discovery, including repositories whose later conversation ingestion fails. Expose the
snapshot and derived stale flag through the existing `repos`/overview contracts.

**Why:** Maintainers need repository recency even for repositories without issues or PRs.
Existing item update timestamps do not identify the newest-created item, and repository
push times cannot identify a default-branch commit. One shared probe per 20 repositories
avoids four extra REST requests per repository. Explicit selection semantics use the
default-branch tip, GitHub's designated latest release and newest-created PR/issue across
all states. Source IDs, links and timestamps remain inspectable.

**Tradeoff:** The summary is a latest snapshot, not a full commit/release event archive.
Missing/partial aliases preserve old data/time and mark activity stale; valid nulls record
known credential-visible absence. Global errors affect a batch; alias errors isolate the
affected repository. Activity failures make the run partial without making item analysis
stale, and successful activity reads survive failed item/conversation fetches. Observation
times are explicit; no automatic age expiry or repository-skip heuristic is introduced.

Evidence and validation: [repository activity checklist](checklist.md#repository-activity),
`tests/test_repository_activity.py`, `tests/test_migrations.py` and the portfolio scale tests.
The [CLI activity contract](cli.md#repository-activity) owns public field semantics.

## D16. Explicit typed command defaults

**Decision:** Load JSON/YAML only through `--config PATH`, validate with Pydantic,
and supply command defaults through Click's existing default map. Shared `repositories`
feeds sync/watch unless a command or explicit CLI argument overrides it. Resolve the
database separately so `--db` and the `db` group retain their existing behavior.
Generate the public JSON Schema by running `python -m gh_maintainer.config`.

**Tradeoff:** Command option models must stay aligned with Click's public contracts.
Files provide defaults rather than selecting commands or running a sequence of commands.
CLI configuration remains separate from repository policy and its local-write guards.

Evidence: [configuration contracts](configuration.md) and `tests/test_config.py`.

## D17. Triage categorization, priority assignment, and multi-field filtering

**Decision:** Extend triage decisions with assignable priority (`urgent`, `high`, `medium`, `low`)
and explicit category classifications (including feature request/contribution, bug fix/report,
dependency upgrade, documentation, maintenance, security, question, other). When no explicit
category has been chosen during triage, infer a fallback category deterministically from item
type, labels, and title conventions. Incorporate assigned priority into computed queue ranking.
Add filtering by author, date range (`since`/`until`), priority, category, and triaged status
to CLI item queries, report generation, and dashboard API and UI.

**Why:** Maintainers triage incoming work by evaluating disposition, priority, and category together.
Deterministic fallback classification categorizes untriaged items immediately without requiring an LLM
call or manual tagging, while explicit triage choices override inference. Filtering by author and date
allows targeted review of contributor history or time-bounded sprints.

**Tradeoff:** Category inference relies on common conventions (e.g. `feat:`, `fix:`, `deps:` prefixes
and issue/PR labels) and may classify ambiguous items as `other` until a maintainer explicitly categorizes
them. Priority weights (+100 for urgent, +50 for high, +25 for medium) directly affect rank ordering
alongside disposition adjustments.

Evidence: `tests/test_cli.py`, `tests/test_migrations.py`, `tests/test_reports_web.py`, and migration `0006`.

## D18. Link PRs and issues, and thread conversation replies

**Decision:** Detect relationships between pull requests and issues using closing keyword patterns
(`Closes #...`, `Fixes ...`, `Resolves ...`) and GraphQL `closingIssuesReferences` on PRs, and
associate issues with closing PRs in the same repository during catalog queries. Expose `linked_issues`
on pull requests and `linked_prs` on issues across CLI `show`, the HTTP item detail endpoint, and
interactive navigation pills in the web dashboard. In conversations, preserve parent-child reply
relationships (`in_reply_to_id`) on comments, displaying nested reply threads in the web UI and
indentation indicators (`↳ [reply to #...]`) in the CLI. Autolink `#123`, `owner/repo#123`, and
`@user` references within markdown and item descriptions.

**Why:** Maintainers frequently navigate between related pull requests and issues to review context,
reproductions, and closing intent. Pull requests close issues via standardized GitHub keywords and
GraphQL references, but this relationship was previously only visible in raw unstructured text.
Similarly, review conversations and discussion replies form threaded parent-child hierarchies
(via `in_reply_to_id`), and rendering comments linearly obscured reply context.

**Tradeoff:** Linking is discovered in-memory during item detail presentation and queries based on
indexed PR signals and bodies within the same repository, avoiding schema migrations or write-time
coupling between independent repository sync checkpoints. Conversation replies utilize the existing
`Comment.raw` JSON payload with typed model properties for backward compatibility.

Evidence: `tests/test_links_and_threads.py`, `src/gh_maintainer/policies.py`,
`src/gh_maintainer/queries.py`, `src/gh_maintainer/cli.py`, and `src/gh_maintainer/static/app.js`.

## D19. Share maintenance services across MCP transports

**Decision:** Use the official `mcp` Python SDK and its `MCPServer` API directly.
The existing FastAPI app hosts the dashboard, REST API, and Streamable HTTP at `/mcp` together; stdio uses the same
registration and resource lifecycle. `serve` and `mcp --transport http` start that
combined app, with their existing port defaults.
Stdio is the default for subprocess clients; HTTP requires `--transport http` or an
explicit configuration default.
Tools call the existing query, sync, triage, analysis, policy, and context services.
The CLI remains usable without a server, and the index is selected once at startup.
Use the selected CLI operation defaults in tool signatures and nested request models;
explicit tool values take precedence and triage defaults to `planned`. Contract tests
compare CLI parameters, defaults, constraints, and results against MCP.
Use streamed HTTP responses and legacy SDK sessions to support request-scoped progress
and cancellation. Progress is an awaited sync observer; shield cleanup so cancelled
clients cannot leave workers, source clients, or database leases behind.

**Why:** MCP clients need the same indexed evidence and guarded decisions as CLI users.
Sharing domain services preserves pagination, revision checks, processing semantics,
policy validation, and partial coverage without duplicating ingestion or persistence.
The SDK supplies protocol validation, discovery, structured output, and transports
without an additional framework dependency. The parent FastAPI lifespan runs the SDK
session manager alongside the shared database/watch lifecycle. Explicit tools
keep CLI operations available without requiring duplicate REST endpoints or automatically
converting dashboard routes. Require `mcp>=2.3,<3` and lock the tested SDK release.

**Tradeoff:** The server adds an optional local control surface, with no authentication
for HTTP. Watcher state is process-local, so use a single HTTP worker. A background
watcher returns immediately and has explicit status/stop tools; it is cancelled at
shutdown. Stop the server before deleting its database through the CLI. Server-side
policy, report, and skill paths remain filesystem operations. See [MCP contracts](mcp.md).

Evidence: `src/gh_maintainer/mcp.py`, `src/gh_maintainer/mcp_commands.py`,
`tests/test_mcp.py`, `tests/test_mcp_contracts.py`, `tests/test_mcp_progress.py`,
and the generated configuration schema.

## D20. Dashboard setup, atomic review completion, and shared Kanban state

**Decision:** Expose credential detection, repository selection, cancellable sync and
progress polling in the existing FastAPI dashboard, reusing the MCP maintenance sync
service and its fenced database writes. Keep credentials on the server and report
availability separately from actual access verified by sync. Preserve CLI configuration
defaults; browser selections last for the server session.

Add an explicit finish-review option to triage in the CLI, MCP and dashboard. Require
the inspected revision and complete source/conversation coverage, and commit the decision
and processing checkpoint atomically. Supplied policy must authorize both actions.
The dashboard loads every conversation page before enabling completion; ordinary saving
and board moves continue without acknowledging processing.

Represent the existing five local dispositions as Kanban columns, with independent
SQL pagination and shared filters. Card moves retain local decision fields and require
the displayed source revision. Snoozing opens the deadline form. Source GitHub state
remains separate from every local lane.

**Why:** Maintainers can set up, refresh, decide and resume work from one workspace while
CLI and agent clients retain reproducible operations over the same data contracts.

**Tradeoff:** Sync tasks and selections are process-local; use one HTTP worker and config
for durable defaults. A dashboard without server credentials still supports offline
review. The default Docker web container keeps credentials with its separate worker.
Processing acknowledges an indexed revision, not live remote state or a technical code
review. Source guards do not provide concurrent local-editor conflict detection. No
new tables, frontend framework, model runner, or GitHub write permissions are introduced.

Evidence: `tests/test_dashboard_workflow.py`, `tests/test_cli.py`, `tests/test_mcp.py`,
and `tests/frontend/workflow.spec.cjs`; operating contracts are in [Usage](usage.md)
and [CLI workflows](cli.md). Validation status is tracked in the
[dashboard checklist](checklist.md#dashboard-workflow-and-kanban).

## D21. Explain returning items from existing review and observation state

**Decision:** Surface review-relative reasons on board cards and paginated evidence in
item details beside the latest saved decision. Reuse processing revisions, raw source
observations and the stored triage decision. Prefer the review checkpoint; fall back to
a revision-bound saved decision and label that baseline explicitly. Add these read models
to existing CLI/MCP/HTTP results and provide bounded described activity pagination.

**Why:** Maintainers can focus on observed new code, comments and checks while retaining
their earlier reasoning. Correlated SQLite aggregates keep card queries paginated and
avoid per-item source/history loads. Fixed revision windows keep later activity outside
an already inspected detail view; guarded review completion still detects source races.

**Tradeoff:** The index keeps the latest local decision, not a decision audit trail.
Display its timestamp and label edits after review rather than reconstructing an earlier
overwritten note. Observation pruning and polling can omit history; missing retained
revisions and unavailable details remain explicit. Check summaries distinguish unknown
and mismatched-head signals. No model invocation, new source fetch, migration or historical
decision snapshot is added. See [follow-up contracts](cli.md#output-and-pagination)
and [validation](checklist.md#review-follow-up).

## D22. One current PR diff and ordered commit membership

**Decision:** Sync stores one unified diff per PR at a verified head/base pair and the
ordered commits currently belonging to that PR. A changed head/base or full reconciliation
replaces this snapshot atomically with the repository source/checkpoints. CLI, HTTP, MCP
and dashboard code views share the offline query contracts. Individual commit links open
GitHub; full messages and metadata remain available locally.

**Why:** Reviewing current changes needs a coherent diff and commit membership. Keeping
every historical diff or building a local Git object store is unnecessary for this workflow.
Both head and base affect the diff, and force pushes can remove commits from membership.

**Tradeoff:** Opening a view reads the latest successful sync, with its SHA and time;
it does not guarantee live GitHub freshness. Failed/racing fetches preserve prior records
and checkpoints, report partial sync and mark code stale. Mismatches hide the old diff.
Upstream limits, including the 250-commit REST cap and large diff failures, remain explicit
coverage errors. No per-commit diff history, detached commit archive or new processing
checkpoint is introduced. Existing live context remains separately available.

The dashboard uses locally vendored diff2html (base UI bundle) for split/unified layouts,
line matching, word changes, file navigation and synchronized scrolling. DOMPurify sanitizes
its generated HTML. Reusing this renderer avoids maintaining a custom git-diff parser;
the base bundle avoids pulling in a syntax-highlighting package. Desktop defaults to split
and narrow screens to unified, with explicit layout controls and original raw patch access.
Assets and licenses ship with the Python package; rendering requires no CDN or Node.js.

File Viewed checks are browser-local review aids, stored as one latest head/base and path
set per stable PR item ID. A new successfully synced head or base clears every mark,
including files with unchanged patches. This conservative reset prevents old checks from
implying review of a newer commit. Failed/stale reads preserve saved marks and cannot be
marked viewed. Checks collapse files, with an independent accessible expand control.
Browser storage supplies reload continuity without introducing a second item-processing
contract or schema; blocked storage retains checks only in memory. Checks are not shared
across browsers and never acknowledge processing, finish review, or change GitHub.

Evidence: `github.py:pull_code`, `CatalogRepository.commit_repo`, migration `0007`,
`CatalogQueries.files/commits`, `tests/test_pr_code.py` and `tests/frontend/pr-code.spec.cjs`.
See [architecture](architecture.md#pr-code-snapshots),
[CLI contracts](cli.md#pr-files-and-commits) and the
[checklist](checklist.md#pr-files-and-commits).
