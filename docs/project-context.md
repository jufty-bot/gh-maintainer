# Project context

This is the starting point for contributors and agents joining gh-maintainer without the
conversation that created it. Read [AGENTS.md](https://github.com/jufty-bot/gh-maintainer/blob/main/AGENTS.md)
for working instructions; this page explains the product, its current state, and where
to make changes.

## Purpose

Maintaining repositories requires remembering what needs attention, what has already been
reviewed, and what changed afterward. gh-maintainer provides that durable context through
a CLI backed by SQLite. Humans and agents can inspect the same source records, save
analysis and triage, and resume work after new activity.

The intended product has two parts: a small CLI that owns data access and local state,
and agent skills that teach maintenance workflows using its public commands. The CLI
baseline, [policy](policy.md), [technical context](context.md), and six
[maintenance skills](skills.md) are implemented. A dashboard and reports are additional clients
of the index. They are useful, but completing agent workflows takes priority over UI work.

## Current capabilities and boundaries

| Area | Available now | Boundary |
| --- | --- | --- |
| Selection | Explicit repository lists across owners; user/organization inventories | Coverage is limited by the credential and selected listing mode |
| Ingestion | Open/closed issues, open/closed/merged PRs, comments, inline review comments, review bodies; repository last commit/release/PR/issue snapshots | Activity coverage is independent; polling cannot reconstruct everything between syncs or provide full commit/release history |
| Queries | Inventory, item details, comments, PR status, activity; SQL filters/pagination and JSON | Technical context is fetched live on demand, separately from indexed conversations |
| Local analysis | Summaries, classification labels, ranks, and triage notes | Callers supply the content; there is no model invocation or built-in taxonomy |
| Continuity | Source revisions, stale analysis, processing checkpoints, changes since processing | Acknowledgement is shared per item, not tracked independently per agent |
| GitHub integration | async HTTPX sync and authenticated `gh api` technical-context reads | The command is `gh-maintainer`, not an installed `gh` extension; no GitHub write commands |
| Agent skills | Six bundled skills, self-contained references, offline fixtures and recorded evaluations | Detailed evaluated scope and remaining provider/host limits are recorded per release |

The remaining context and skill dependencies are detailed in the [agent skills plan](agent-skills.md).
The first skill follows the [local triage plan](specs/001-local-triage/plan.md), with its
specification and completed task list alongside it. Read the [release evaluation](evaluations/release.md)
for independent cases and limits; initial author-run evidence is distinct from independent validation.
Use the [context contract](context.md) for live evidence and explicit pagination/gaps.

## Essential concepts

**Source and local state are separate.** GitHub state describes open, closed, and merged
items. Local triage describes inbox, planned, waiting, snoozed, or done. Local labels and
ranks do not change GitHub. Analysis writes do not replace source data or advance processing.

**A revision represents observed source changes.** Meaningful item, PR signal, or comment
changes advance the item's integer revision. Polling identical data does not. Revisions
are local to an item; they are not Git commit numbers or an event count. The head SHA is
the separate identity for code content.

**Processing is explicit.** Reading an item does not acknowledge it. `mark-processed`
records the current revision, actor, and time after inspection. New observed activity
makes that item pending again, including closed/merged items if the query includes them.
An agent should pass the revision it actually inspected when writing or acknowledging.

**Stale and incomplete mean different things.** An analysis result is stale if its source
revision differs or item/conversation coverage is incomplete. Repository activity summaries
have independent observation times/errors. A sync error may mean new data was
not observed at all. Check source timestamps and errors; a current local revision is not
proof that the remote repository has not changed since the last poll. No automatic age
limit expires manually saved analysis.

**The index retains observations.** Missing records are marked unavailable rather than
assumed deleted. Observations record source changes, not every human/agent action or every
GitHub event. Analysis tables retain one latest result per item/type, with provenance.

## Reading order

| Need | Read |
| --- | --- |
| Understand the product and constraints | This page, then [design decisions](decisions.md) |
| Operate the tool or write a maintenance skill | [CLI workflows](cli.md) and [usage](usage.md) |
| Change implementation | [Architecture](architecture.md), [contributing](contributing.md), and relevant tests |
| Choose the next task | [Roadmap](roadmap.md), [agent skills plan](agent-skills.md), and [checklist](checklist.md) |

The roadmap owns scope and delivery order. The checklist owns completion status and
validation evidence. The decision log owns rationale and tradeoffs. Update them with
relevant changes rather than relying on conversation history.

## Run and inspect the project

Set up the checkout with `uv sync --locked`. To explore without credentials, choose a fresh
database and seed synthetic records:

```bash
demo_dir=$(mktemp -d)
uv run gh-maintainer --db "$demo_dir/index.sqlite" demo
uv run gh-maintainer --db "$demo_dir/index.sqlite" status --format json
uv run gh-maintainer --db "$demo_dir/index.sqlite" show example/orbit#42 --format json
```

The demo is synthetic and requires an empty database. For real data, follow
[selection and authentication](usage.md). The CLI workflow guide explains revision-guarded
writes, processing queues, and JSON contracts. A local web dashboard is optional.

## Where to make changes

Code lives in `src/gh_maintainer/`. Keep orchestration, persistence, and presentation in
their existing modules; a new feature does not normally need a new service framework.

| Change | Main code | Contract tests |
| --- | --- | --- |
| GitHub data or pagination | `github.py`, source protocol and Pydantic snapshots in `schemas.py` | `tests/test_github.py` uses real SDK handling with mocked HTTP |
| Backfill, incremental windows, or coverage | `sync.py`, `CatalogRepository` in `repositories.py` | `tests/test_sync.py`, `tests/test_selection.py`, `tests/test_maintenance.py` |
| Storage or migration | `models.py`, a new packaged Alembic revision | `tests/test_migrations.py` and relevant SQLite integration tests |
| Query/filter/priority behavior | `queries.py`, `policies.py` | `tests/test_reports_web.py` checks SQL/pure-policy parity; maintenance tests cover freshness |
| Database location/file management | `database.default_path`, `cli.py`, `migrations/env.py` | `tests/test_cli_db.py` covers defaults, overrides, and deletion |
| CLI or JSON behavior | `cli.py`, `schemas.py`, the query/write services | `tests/test_cli.py` and `tests/test_cli_db.py` cover the command boundary; `tests/test_maintenance.py` covers an offline workflow |
| Policy/context/skill resources | `config.py`, `context.py`, `skillpack.py`, `skills/`; public CLI calls | `tests/test_policy.py`, `tests/test_context.py`, `tests/test_skillpack.py`, and [release evaluation](evaluations/release.md) |

`tests/fakes.py` is the token-free source implementation for sync tests. `demo.py` supplies
an offline portfolio. `database.py` owns engine/session lifecycle and migrations. Follow
[contributing](contributing.md#validation) for focused tests and required checks. Python
3.10–3.14 compatibility is reflected in package metadata and CI.

At the root, `pyproject.toml` defines dependencies, the executable, and tool configuration;
`uv.lock` pins the environment. `Taskfile.yaml` wraps common development commands.
`mkdocs.yaml` and `docs/gen_pages.py` build the docs; the home page is generated from the
README, with `docs/index.md` kept aligned as a fallback. `.github/workflows/` and
`.releaserc.json` define CI/release behavior. Docker/Compose are optional deployment paths.
The project began from the juftin/cookiecutter-python template; retained template answers
and source revision live in `.github/.copier-answers.yaml`.

## Implementation preferences

Keep project code small. Use existing Pydantic, SQLAlchemy/Alembic, HTTPX, and CLI
capabilities before building custom infrastructure. Established packages are welcome when
they remove duplication; introducing another layer requires a concrete benefit. The
project uses one persistence stack; a proposed wrapper should remove concrete project
code rather than duplicate existing ORM responsibilities.

Skills should call the public CLI and use repository tooling for code changes. Avoid
embedding SDK calls or private database manipulation in skill instructions. Add only the
missing CLI context demonstrated by a workflow. A generic agent runner, compulsory model
provider, automatic GitHub writes, and a larger hosted application are not current goals.

## Common investigation paths

| Symptom | What to inspect |
| --- | --- |
| An expected PR is absent | Selected scope, credential visibility, default open-state filter, `repos`, and latest sync errors |
| Comments are empty | `comments_synced_at`, coverage error, pagination, and whether the database has been synced since migration `0003` |
| Marking processed fails | Repository errors/comment coverage, then whether the inspected revision still matches |
| A guarded write is rejected | Read the new item/activity/conversation before revising the decision; do not only substitute a newer revision number |
| Analysis appears stale or no longer affects a filter/rank | Source revision mismatch or incomplete item/conversation coverage |
| A locally done item returns to inbox | New observed source activity; the note remains and the decision needs reconsideration |
| A sync is slow | Number of available PR review listings, per-repository staging size, rate limits, and source-call/run telemetry |
| Schema initialization is refused | Database selection and recognized migration history; preserve the file and investigate rather than deleting data to bypass validation |

The index and exports may contain private repository content. The optional dashboard has
no built-in authentication. Operational deployment and SQLite backup guidance live in
[usage](usage.md), alongside the current limits.
