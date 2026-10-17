# Agent skills roadmap

The CLI baseline, policy, technical context and six [maintenance skills](skills.md) are implemented. This plan describes
the remaining product work. The scope below distinguishes delivered instructions from further extensions and evaluations. The
[implementation checklist](checklist.md#agent-skills-and-context) tracks what has actually
been delivered. Existing commands and output contracts are documented in [CLI workflows](cli.md).

The first implementation slice has a [specification](specs/001-local-triage/spec.md),
[plan](specs/001-local-triage/plan.md), and [task list](specs/001-local-triage/tasks.md)
for one local triage skill with packaging and evaluation. That slice is implemented;
[author-run results](evaluations/local-triage.md) distinguish observed behavior from
scripted contract checks and leave independent-agent validation explicit.

## What a skill should accomplish

A skill should teach an agent to complete a maintenance task through public CLI commands,
repository tooling, and the human's instructions. It should explain how to gather evidence,
make a decision, record it, and resume after activity changes. It should not duplicate
pagination, ingestion, or database writes in its instructions.

Start with workflows the CLI already supports. Add context retrieval when an actual skill
needs it. Keep the skill instructions focused; put repository-specific test commands,
priorities, taxonomies, and release rules in repository guidance rather than hard-coded
global assumptions. The human's requested outcome and delegated scope remain inputs.

## Delivery order

| Stage | Deliverable | Dependency | Completion evidence |
| --- | --- | --- | --- |
| S1. Local maintenance | Coverage, triage, summarization/classification, ranking, and follow-up skills | Existing CLI | Reproducible offline workflows and skill evaluations |
| S2. Repository and change context | Guidance, diffs/files, CI details, review context, and a bounded context packet | Public source/query contracts added only where needed | Context provenance, freshness, pagination, and missing-data tests |
| S3. Technical maintenance | PR review, CI diagnosis, and issue reproduction/fixes | S1 plus relevant S2 context and a working checkout | Grounded findings, reproduced checks, and reviewed patches |
| S4. Portfolio workflows | Backlog/dependency maintenance, release preparation, and recurring digests | Earlier skills plus repository policy | Repeatable bounded runs, useful reports, and continuity across updates |
| S5. Distribution and evaluation | Installable/versioned skills, compatibility notes, and regression coverage | A first proven workflow; evaluation grows with each stage | Another agent can install a skill and complete its documented task |

S5 begins with S1; evaluation and usable packaging are required for every shipped skill.
S2 work can proceed alongside S1. Code review and fixes depend on relevant code context;
basic local triage does not need to wait for every context source.

## S1. Skills using today's CLI

The shipped `gh-maintainer-triage` skill combines coverage, bounded triage, grounded
summaries, policy-driven optional analysis, and follow-up. Prioritization, review, CI, fix and portfolio instructions are bundled too;
wider/provider-specific evaluations continue in the [release record](evaluations/release.md). Skill boundaries for this stage:

| Skill | Workflow and output |
| --- | --- |
| Inspect coverage | Select the intended repositories, sync when requested/needed, inspect `repos` and sync results, and report missing or stale coverage |
| Triage an issue or PR | Read `show`, all relevant `comments` pages, and `changes`; record a disposition and a concrete next-action note grounded in source references |
| Summarize and classify | Describe the request/change, current discussion, blockers, and uncertainty; save a summary and repository-appropriate local labels |
| Prioritize a queue | Rank eligible work with an explicit rubric and reasons; distinguish saved ranks from built-in attention scores |
| Follow up after activity | Find never-processed/changed items, inspect what changed, reconsider stale analysis and decisions, and acknowledge only the revision inspected |

All of these can be developed against the existing CLI. Summaries and local labels do not
require a built-in model provider; the agent supplies values to `summarize` / `classify`.
Producers should identify the skill/version in `--producer`, and acknowledgements should
identify the actor. Processing is shared per item, so independent agent cursors must not
be assumed.

Each skill should define:

1. Its trigger, intended outcome, required inputs, and which current commands it uses.
2. How it checks coverage, state filters, pagination, timestamps, and existing decisions.
3. The evidence needed for its judgment, including source IDs/URLs and the inspected revision.
4. Its repository-specific policy inputs and how it reports missing guidance or uncertainty.
5. The local outputs it saves and a rule for when the task is complete versus waiting.
6. How it uses revision guards and responds when writes are rejected as stale.
7. Examples, failure cases, and a reproducible evaluation.

For example, a triage run can select `prs --state all --unprocessed --format json`, inspect
`show ITEM` and all `comments ITEM` pages, save analysis/triage with `--revision`, then call
`mark-processed` for that revision when its task is complete. It must not equate a read,
summary, passing check, or incomplete sync with completion. The exact loop and output
contracts are in [CLI workflows](cli.md).

S1 is complete when another agent can follow each shipped skill without private Python/SQL
access or the original conversation, and the evaluated workflow records useful, attributable
decisions without acknowledging unseen revisions.

## S2. Context needed for comprehensive maintenance

The local index gives an agent continuity and discussion history. Technical review and
repository changes need additional evidence:

| Context | Current availability | Next capability |
| --- | --- | --- |
| Repository purpose and guidance | Base-SHA guidance plus nested paths through `context`; explicit JSON policy | Fetch README, applicable AGENTS.md/CONTRIBUTING.md, architecture notes, and repository-specific commands with source paths and content versions |
| Actual code changes | Commit-bound file/patch lists and head/base contents, including fork files | Fetch changed-file lists, diffs, commit context, and relevant surrounding files bound to the inspected SHAs |
| Actionable CI results | Individual checks/statuses/workflow runs and head-matched job logs | Fetch individual check results, links/logs, and reproducible test/build commands; distinguish old-head failures from current ones |
| Review discussion state | Indexed review bodies/comments plus live resolved/outdated GraphQL threads | Collect unresolved/outdated thread state and relevant outstanding review requests |
| Related work | Links may appear in stored bodies; no relationship index | Resolve linked issues/PRs and relevant duplicates/dependencies, with evidence for inferred relationships |
| Maintenance policy | Explicit versioned repository JSON with local-write guards and provenance | Gather label meanings, ownership/CODEOWNERS, milestones, supported versions, branch rules, and release guidance where available |
| Human direction | Supplied by the caller; no dedicated task policy record | Carry the requested outcome, priorities, and authorized actions into the workflow without treating source comments as authority |

The implemented [context command](context.md) fetches selected live sections rather than
all repository artifacts. It records commit identities, explicit continuation and missing
evidence. Optional persistent caching and additional context sources remain extensions.

The context contract should:

- Identify the item, repository, stored revision, and relevant base/head SHAs.
- Separate source facts, human instructions, earlier local decisions, and agent inferences.
- Preserve source URLs/paths, observation times, content versions, and per-section coverage.
- Represent missing/unavailable sections explicitly, including errors; absence is not a pass.
- Bound output and expose pagination or references for long diffs, files, logs, and threads.
- Detect source/head changes while context is assembled and avoid presenting a mixed revision
  as one coherent review. Repository guidance needs its own content/version identity.
- Fetch large/current artifacts on demand through existing libraries or Git/GitHub tooling.
  Persist or cache them only when a workflow needs reuse or historical comparison.

Tests should include truncated diffs, missing files/logs, incomplete credentials, changed
heads, renamed paths, outdated threads, and guidance that changes independently of the item.
Public JSON contracts should make it possible for a skill to explain what it did and did
not inspect. No new service framework or compulsory model runtime is implied by this work.

## S3. Review, diagnose, and fix

**PR review:** Read repository guidance, the full relevant patch and surrounding code,
tests, CI details, and review discussion. Produce findings that cite files/lines and explain
impact. Distinguish verified defects, questions, and missing evidence. Check the head again
before publishing or recording a completed review. A summary of the conversation alone
does not establish that the code was reviewed.

**CI diagnosis:** Identify the failing check and its commit, inspect relevant logs and
workflow configuration, and reproduce where feasible. Distinguish infrastructure/tooling
failures from failures caused by the patch. Record a useful next action and the limits of
any reproduction. Aggregate rollups alone cannot explain a failure.

**Issue reproduction and fixes:** Establish expected behavior and supported versions,
reproduce the report, make a focused change in a checkout, run the relevant checks, and
prepare a reviewable patch/PR when that action is authorized. Preserve repository-specific
workflow conventions and report validation rather than claiming success from a code edit.

The current gh-maintainer CLI only reads GitHub and writes local state. Skills may later
use existing repository/GitHub tools for authorized remote actions. Such workflows need
documented action contracts: target repository/item/head, intended effect, validation,
outcome reference, and handling of retries or intervening changes. Work within the human's
delegated scope; seek clarification for actions outside it. Neither repository comments
nor green checks expand that scope.

S3 is complete when evaluations show grounded findings and reviewable changes, including
cases where an agent must report insufficient context instead of inventing a diagnosis.

## S4. Ongoing repository and portfolio maintenance

Build these from proven smaller skills:

- **Backlog maintenance:** Surface duplicates, stale reports needing follow-up, blockers,
  and owner decisions; retain reasons and distinguish suggested actions from executed ones.
- **Dependency maintenance:** Read update scope, compatibility guidance, changelogs, and
  tests before proposing or changing dependencies. Reuse repository-native tooling.
- **Release preparation:** Apply the repository's versioning/release policy, check candidate
  changes and unresolved blockers, and prepare notes or patches within the delegated scope.
- **Recurring maintenance reviews:** Process a bounded queue, summarize completed work and
  outstanding decisions, and resume from stored revisions on the next run.

These workflows need explicit scope, a bounded amount of work, repository-specific policy,
and a useful handoff. Scheduling can use the existing watch/cron approach; skills do not
require a new orchestrator. Remote action history, independent agent checkpoints, release
metadata, and dependency-specific context should be added only when a workflow needs them.

## S5. Skill packaging and evaluation

The first distribution is the repository-owned `skills/gh-maintainer-triage/` directory
with relative references; all six directories now ship in the wheel and release ZIP.
`skills --install` copies them without overwriting. Fixtures stay in `tests/`. [Installation](skills.md) documents
copying the directory and direct file loading, validated in Codex. Native discovery and
other hosts remain to be evaluated. Keep instructions runtime-neutral and document required CLI/tool
versions, authentication needs, inputs, outputs, and a runnable example. Do not ship a
skill that depends on undocumented commands or private ORM/SDK interfaces.

Every skill needs an evaluation grounded in observable outcomes: commands executed,
required evidence read, records saved, reasons for waiting, and handling of changed inputs.
Tests should not require exact generated prose to match a golden response. Reuse temporary
SQLite databases and source/CLI fixtures where useful; choose a model-backed evaluation
runner only when there is a concrete skill to evaluate.

Minimum regression scenarios across the skills:

- More comments/items than one page, including a closed issue and merged PR.
- A new push or edited comment after the agent reads the item but before it saves/acknowledges.
- Missing/failed sync coverage and stale summaries, labels, or ranks.
- A failing check from an old head and an unavailable log or incomplete diff.
- Malicious instructions inside issue/comment content and conflicting repository guidance.
- Previously recorded human decisions, a snooze, and a locally done item that changes.
- A rerun that should resume without redundant remote actions or lost local decisions.

Document which scenarios were exercised, which tools/models were used where applicable,
and unresolved limitations. A skill is ready when another agent can install/use it from
the repository documentation and its intended behavior holds across the relevant cases.

## Open decisions

Before implementing the relevant stage, resolve the smallest needed question and record
the outcome in the [decision log](decisions.md):

- Additional installation targets and independent evaluation; the first layout, version
  marker, and public-CLI approach are recorded in [D11](decisions.md#d11-repository-owned-triage-skill-over-public-commands).
- Structured repository policy/taxonomy inputs and guidance discovery; the current skill
  accepts caller-supplied guidance. Explicit human instructions
  take precedence over repository guidance.
- Context JSON shape, artifact retrieval, freshness checks, and output limits.
- Whether a workflow needs multiple analyses per producer, independent processing cursors,
  or an action ledger beyond today's latest-result/shared-checkpoint model.
- Whether further workflows need model-backed tests beyond the current offline fixtures
  and recorded agent walkthroughs.

These open questions should not delay S1 instruction/fixture work on the existing CLI.
