# Scope and roadmap

gh-maintainer's foundation is a CLI that builds and maintains a local GitHub index.
It accepts a list of repositories or an owner, ingests all issues, pull requests, and
their comments incrementally into SQLite, and stores derived summaries, classifications,
rankings, and triage in additional tables. After this baseline is complete, the repository's
focus shifts to agent skills for operating the CLI and maintaining repositories on behalf
of humans.

This document tracks baseline scope, completion criteria, and the next focus on skills.
The status table distinguishes implemented functionality from future work.
Agents track task status and validation evidence in the [implementation checklist](checklist.md)
and update this roadmap's scope and current-status table as relevant changes land.
New contributors should start with [project context](project-context.md) and
[design decisions](decisions.md). The detailed next-stage plan is [Agent skills](agent-skills.md).

## Current implementation

| Baseline capability | Status | Remaining work |
| --- | --- | --- |
| Select an explicit repository list | Implemented: repeatable `sync --repo` / `watch --repo` resolves repositories directly across owners | Coverage remains limited to accessible repositories |
| Select all repositories belonging to an owner | Implemented: `--owner` accepts users and organizations; default is the authenticated user | Other users' inventories are public-only; use explicit lists for accessible private repositories |
| Ingest all issues and PRs | Implemented within the selected scope, including closed and merged items | Credential-limited polling coverage |
| Repository recency | Implemented: last default-branch commit, designated latest release, newest-created PR/issue, dates, links and independent activity coverage through `repos` | Latest snapshots only; full commit/release history remains outside scope |
| Ingest their comments | Implemented: issue conversations, inline review comments, and review bodies, including replies and raw line context | GraphQL thread resolution is available through live context; sync does not persist thread state |
| Database location and management | Implemented: platform user data default, explicit overrides, `db path`, and `db delete --yes` | Existing working-directory indexes remain selectable; no automatic move |
| Incremental SQLite storage | Implemented for items and comments: checkpoints, overlap, reconciliation, atomic commits, migrations | Review bodies require a full per-PR listing each sync |
| Parallel incremental ingestion | Implemented: async HTTPX, bounded repository windows with shared PR signal batches, targeted item/comment reads, cached closed PR details and shared aiolimiter pacing | Per-process budgets; window barriers, full review listings and interrupted per-repository backfills remain |
| Sync logging | Implemented: readable structlog events on stderr for discovery, repository progress, counts, timing, and failures; incremental windows use ISO UTC timestamps | JSON results remain on stdout |
| Efficient querying through the CLI | Implemented: Click commands with descriptive help, validated options, repositories, PRs/issues, details, comments, status, SQL pagination, and JSON | Keep public contracts stable as skills are added |
| Additional tables for analysis and triage | Implemented: persisted summaries, classifications, rankings, triage, and processing checkpoints | Analysis is supplied by humans/agents; no built-in model provider |
| Track processing and later activity | Implemented: source revisions, pending/changed filters, activity kinds, guarded acknowledgements; embedded profile/statistics updates retain raw data without invalidating analysis | Only observed activity can be recorded |
| Repository policy | Implemented: explicit validated JSON, hash provenance, optional local-write guards | Changes to policy require explicit reconsideration; not an access sandbox |
| Technical context | Implemented: live gh guidance/code/CI/thread sections with SHA identity and bounded evidence | Upstream API limits, nested replies and external providers remain explicit gaps |
| Agent skills for repository maintenance | Six skills bundled with policy/context and recorded evaluations | Broader adversarial/provider/host coverage remains incremental |
| Distribution | [0.2.0 released](releases.md): wheel/source, skill bundle and verified checksums; installed-artifact and main CI gates | No PyPI publication or automatic agent runner |

The current tables are `metadata`, `owners`, `repo_owners`, `repos`, `items`, `comments`,
`changes`, `triage`, `runs`, `processing`, `summaries`, `classifications`, and `rankings`,
plus Alembic's schema version table. The baseline is usable entirely through the CLI;
the dashboard and reports also consume the index. See [CLI workflows](cli.md).

## Portfolio sync efficiency

Hundreds of repositories make per-repository request and persistence costs material.
The current implementation batches signals across a bounded repository window, queries
the PR head directly, avoids unchanged inventory writes, and targets historical PR/comment
reads. Embedded profile changes no longer create revision/analysis churn. Scope and
freshness remain unchanged; [D14](decisions.md#d14-reduce-portfolio-work-without-reducing-coverage)
records the tradeoffs, and the [checklist](checklist.md#portfolio-sync-efficiency) owns
validation evidence.

The remaining request floor is significant: every repository still polls three REST feeds
(issues, conversation comments, inline comments), plus review pages for every available PR
and details for open PRs. Even 300 repositories with empty feeds and no PRs require about
900 feed requests. At the default two request starts per second, pacing alone is roughly
7.5 minutes, before inventory, retries, or response latency. This is a cost calculation,
not a live benchmark. Increasing concurrency does not bypass the shared request-start rate.
Repository activity adds one GraphQL request per up to 20 repositories (15 for 300 repos)
to capture all four summaries; these probes are separate from open-PR signal requests.

Next candidates are **planned**, in the following investigation order:

| Candidate | Intended reduction | Completion criteria / limits |
| --- | --- | --- |
| Endpoint-level telemetry and reproducible portfolio fixtures | Identify whether feeds, review pages, details, retries, or persistence dominate | Count attempts, statuses, response bytes, durations and changed records by endpoint family without logging credentials/content; retain aggregate run contracts and test hundreds of mixed active/quiet repos |
| Persistent conditional REST requests | Avoid unchanged response bodies and primary-budget charges | Store ETag/Last-Modified by exact URL/query, API representation and credential scope; handle `304`, cached pagination, failed commits and forced full reconciliation safely; bound storage; a `304` still takes a paced request |
| Batch review metadata through GraphQL | Replace one review-body REST listing per historical PR with smaller shared probes | Cover new reviews, edits, dismissals, pending reviews, pagination and unavailable records; `updatedAt` exists on reviews, but the review connection has no `since` filter, so metadata enumeration remains necessary |
| Combine open-PR metadata with the signal query | Reduce repeated PR detail requests | Prove invalidation for head/base refs, state, draft, merge and review requests; preserve retained REST payload/provenance and head-change detection; do not gate CI/mergeability on issue timestamps |
| Bounded rolling scheduling and staggered reconciliation | Reduce window stragglers and periodic full-sync spikes | Preserve bounded staged memory, deterministic errors, fenced atomic commits and complete seven-day reconciliation; compare with the simpler current windows before adding coordination |
| Explicit per-repository polling cadence | Reduce the three-feed request floor for quiet repositories | Define observable next-due/last-success/age semantics and an explicit refresh override; retain comment-only activity on closed items and do not claim skipped repositories were refreshed; this changes freshness and is not enabled now |

GitHub documents [conditional requests](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api#use-conditional-requests)
and the [PR/review GraphQL fields](https://docs.github.com/en/graphql/reference/pulls).
Repository `updated_at`/`pushed_at`, the PR issue timestamp, and an unchanged head SHA are
not complete descendant-change watermarks. Webhooks and resumable staging remain
[later extensions](#later-extensions); neither is required for these local improvements.

## Baseline delivery sequence

### 1. Repository selection and coverage

Support two clear selection modes: an explicit list of `owner/repository` names, or all
repositories belonging to a named user or organization. Preserve the existing default
of syncing the authenticated user's repositories. An explicit list must work across owners
when the credential can access those repositories.

This milestone is implemented. Databases now support multiple stable owner IDs and retain
repository membership separately from source records. Migration `0002` adopts legacy owner
metadata without rewriting existing records or local decisions. Repository IDs retain
identity across renames and transfers.

Completion criteria:

- CLI selection and validation are documented for both modes, including how conflicting
  selection options are handled.
- Owner discovery follows all pages and reports credential-limited coverage explicitly.
- Syncing a subset does not mark unrelated repositories absent. Only a successful complete
  inventory can reconcile presence within its own scope.
- Inaccessible repositories and failed discovery produce visible errors without advancing
  checkpoints or discarding existing history.

### 2. Complete issue and PR conversations

For planning purposes, "comments" includes issue conversation comments on both issues
and PRs, PR review bodies, and inline PR review comments with their reply relationships.
Retain thread identity and resolution context where the source exposes it. These are
distinct source record types, even when presented together as one conversation.

Add versioned migrations and source adapters for these records. Store stable source IDs,
parent item links, authors, bodies, creation/update times, URLs, and raw payloads. Include
open, closed, and merged items in historical backfills.

Implemented in migration `0003`. Repository comment feeds use an independent overlapped
checkpoint. Review bodies are refreshed for all available PRs on every sync, since their
endpoint has no incremental timestamp filter. Comment failures roll back the repository's
item/comment changes and preserve both checkpoints. REST reply and line context is retained;
GraphQL thread identity/resolution is deferred.

Completion criteria:

- Listing follows pagination to exhaustion, and repeat syncs upsert records without
  duplicates. Edits update the stored record.
- Each source stream has an appropriate checkpoint or refresh strategy. Do not rely on
  the parent item's timestamp alone to discover comment edits or review changes.
- Periodic reconciliation detects records no longer returned by a successful complete
  listing and retains their history with availability metadata. Failed or partial listings
  do not imply deletion.
- Failed fetches preserve the affected stream's prior checkpoint and committed data;
  incomplete conversation coverage is queryable and visible in sync results.
- Contract tests cover pagination, edits, replies, repeated syncs, partial failure, and
  reconciliation, including conversations on closed issues and merged PRs.

### 3. Query the index through the CLI

Expose repository inventory, item lists, item details, comments, and synchronization
coverage through CLI commands. The implemented `repos`, `prs`, `issues`, `show`, `comments`,
`status`, and `changes` commands reuse the SQL query layer and provide JSON and text output.
The Click interface adds command descriptions, `-h`/`--help`, `--version`, visible defaults,
and validation before database initialization. Command names and output schemas remain
stable; invalid parameter bounds and timestamp syntax now consistently exit 2.

Completion criteria:

- A caller can filter by repository, item kind, state, and triage disposition, and inspect
  a complete conversation with pagination and deterministic ordering.
- Filtering, ordering, and pagination execute in SQLite; routine queries do not require
  loading the full portfolio into memory.
- Query commands work offline against a populated database without GitHub credentials.
- IDs, freshness, missing coverage, exit codes, and JSON contracts are documented so an
  agent can distinguish no results from a failed operation.

### 4. Persist summaries, classifications, rankings, and triage

Keep ingested source records separate from derived analysis. Add tables for summaries,
classifications, and rankings; extend the existing `triage` table only where needed.
Each result should link to its item and record its producer/version, generation time,
and the source revision or fingerprint it used. Freshness must account for conversation
changes as well as item changes. Rankings that depend on elapsed time also need an
explicit evaluation time and refresh policy.

The initial schema should support CLI-written results from rules or agents. A built-in
model provider is not required to establish the storage and query contract.

Implemented through `classify`, `summarize`, `rank`, `triage`, and `mark-processed`.
Records retain producer, reason, source revision, and save time. Comment/item changes mark
analysis stale and make previously processed items pending again. Fixed ranks have no time
decay; producers explicitly refresh any time-dependent score. Built-in priority rules still
evaluate elapsed time on each query. See [analysis and processing contracts](cli.md).

Completion criteria:

- CLI operations can write and query summaries, classifications, and ranked queues.
- Results have inspectable reasons or supporting source references where appropriate,
  and stale results are distinguishable from current ones.
- Recomputing analysis is repeatable and does not overwrite source data or human triage
  decisions. Existing local decisions continue to survive synchronization.
- Migrations preserve existing indexes, and tests cover provenance, freshness,
  idempotent writes, and query behavior.

## Baseline completion gate

The baseline is complete when a human or agent can use only the CLI to select repositories
or an owner, backfill their issues, PRs, and conversations, run a safe incremental refresh,
query that data efficiently, and write/read persisted analysis and local triage. That path
must have documented output contracts and tests for interruption, partial coverage,
reconciliation, and upgrades from existing databases.

The automated baseline path is implemented and validated by the offline CLI workflow,
SDK pagination, processing freshness, interruption/retry, and legacy migration tests.
The [checklist](checklist.md) records validation. The first local triage skill builds on this baseline.

## Agent skills after the baseline

The baseline and [six maintenance skills](skills.md) are implemented. Repository policy,
live technical context, packaging and validation follow the
[complete-release specification](specs/002-complete-release/spec.md). The
[agent skills roadmap](agent-skills.md) owns the detailed scope and acceptance criteria.

The first delivery is scoped in the [local triage specification](specs/001-local-triage/spec.md),
[implementation plan](specs/001-local-triage/plan.md), and
[ordered tasks](specs/001-local-triage/tasks.md). Those artifacts cover one S1 triage workflow
and initial S5 packaging/evaluation; they do not mark those stages complete. The
[evaluation report](evaluations/local-triage.md) records 13 author-run cases and limits.

| Stage | Work | Prerequisite |
| --- | --- | --- |
| S1 | Coverage inspection, triage, summaries/classifications, prioritization, and activity follow-up | Current public CLI |
| S2 | Repository guidance, code/diff context, actionable CI, review state, and a bounded context packet | Small additions driven by the skills |
| S3 | Code review, failure diagnosis, issue reproduction, and reviewable fixes | Relevant S2 context and a checkout |
| S4 | Backlog/dependency maintenance, release preparation, and recurring portfolio reviews | Proven earlier workflows and repository policy |
| S5 | Installable/versioned instructions and regression evaluations | Starts with S1 and grows with each stage |

Skills should call public commands rather than duplicate ingestion or use private Python/SQL
interfaces. They need evidence, repository-specific policy, explicit outcomes, source/revision
references, and evaluation cases. The implemented `context ITEM --format json` command fetches
commit-bound guidance/code/CI/thread evidence with explicit limits; see [context](context.md).

Local analysis and triage are distinct from GitHub actions. Future workflows that comment,
label, close, or merge need explicit action contracts and human authorization boundaries.
The current CLI makes no such GitHub changes.

Open extensions include additional agent installation targets, context caching,
external CI-provider integrations, broader behavioral evaluations, and whether workflows need an action ledger or
per-agent checkpoints. Decide these when the relevant workflow requires them, record the
rationale in [decisions](decisions.md), and track completion in the [checklist](checklist.md).

## Later extensions

Webhooks, a GitHub App, resumable staging for very large backfills, and additional source
domains such as full release history, discussions, and security alerts can follow the baseline.
Dashboard expansion is secondary to completing the CLI and its data contracts. Polling
cannot reconstruct events that occurred entirely between observations, and coverage always
depends on what the credential can access.
