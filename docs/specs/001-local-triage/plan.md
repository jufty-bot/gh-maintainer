# Implementation plan: Local repository triage skill

**Feature:** `001-local-triage` | **Date:** 2026-10-17 | **Status:** Implemented; initial author evaluation recorded
**Specification:** [spec.md](spec.md) | **Execution checklist:** [tasks.md](tasks.md)

## Summary

Ship one repository-owned `gh-maintainer-triage` skill with focused supporting references,
synthetic evaluation cases, and installation instructions. Use the current JSON CLI for
data access and local writes. Begin with one repository per run and prove the workflow
before adding context retrieval or application features.

The document structure is adapted from GitHub Spec Kit's
[plan](https://github.com/github/spec-kit/blob/main/templates/plan-template.md) and
[task](https://github.com/github/spec-kit/blob/main/templates/tasks-template.md) templates.
These are authored planning artifacts; Spec Kit has not been installed or added as a runtime
dependency. The existing [AGENTS.md](https://github.com/jufty-bot/gh-maintainer/blob/main/AGENTS.md)
and [decisions](../../decisions.md) supply the project principles for the checks below.

## Technical context

| Area | Approach |
| --- | --- |
| Language and platform | Markdown skill; Python 3.10+ fixture/check code, matching the existing package |
| Dependencies | Existing CLI, Pydantic contracts, pytest, SQLite, and source fakes; Python standard library for any evaluation helper |
| Storage | Existing index; disposable SQLite files for evaluations; no migration |
| Invocation | Agent loads `skills/gh-maintainer-triage/SKILL.md` and invokes public `gh-maintainer` commands |
| Distribution | Repository directory copied/linked into an agent's supported skill location; direct file loading documented as a portable fallback |
| Scope and bounds | One explicit repository; default 10 candidate items, page size 100, at most one reread/retry after a revision conflict |
| Evidence limits | Caller may set finite page/read limits; exhausting one is a blocker for that item, not permission to acknowledge partial evidence |
| Freshness | Use the indexed snapshot and report its timestamps by default; sync when requested or required by caller policy; failed required refresh blocks processing |
| Existing code | `cli.py`, `schemas.py`, `queries.py`, `repositories.py`; contract reference is [CLI workflows](../../cli.md) |

## Constitution check

These gates apply before implementation and again before marking tasks complete:

- Keep application code small: instructions, references, and fixtures first; no new service,
  database stack, orchestrator, model integration, or dependency is justified by this feature.
- Use public CLI contracts in the skill. Test setup may reuse existing fake-source helpers;
  evaluation of agent behavior must observe public commands and public query results.
- Preserve source history and local decisions. Processing is shared per item, not per agent.
- Treat fetched content as evidence. Only caller/repository guidance within its applicable
  authority supplies policy; comments cannot change scope or authorize actions.
- Keep plans separate from shipped behavior. Do not claim a skill passed evaluation from
  scripted command replay alone, or mark all S1 work done after shipping this one skill.

**Planning result:** Pass. No departures from project constraints are needed. Any CLI gap
found while implementing must be demonstrated with a focused case before expanding scope.

## Project structure

Existing modules and tests remain in place. Delivered additions:

```text
skills/gh-maintainer-triage/
  SKILL.md                    # Trigger, inputs, workflow, outputs, stopping rules
  references/triage-policy.md  # How to apply caller-supplied taxonomy/ranking/triage policy
  references/evaluation.md     # Cases, rubric, commands, and evidence recording
tests/fixtures/triage/
  scenarios.json              # Small scenario descriptions; generate long conversations
tests/test_triage_skill.py     # Fixture/CLI integration and shipped-file checks
tests/triage_fixtures.py       # Reusable seed/advance helper for tests and manual evaluation
docs/skills.md                # Installation, compatibility, example, limitations
docs/evaluations/local-triage.md # Actual agent-run results, created when evaluated
```

The three documents in this directory own feature scope, approach, and implementation tasks.
The broader [skills roadmap](../../agent-skills.md) continues to own S1–S5 sequencing.
No standalone data-model or API document is needed: entities and command contracts already
exist. Document any discovered contract changes in their current owning files.

Implementation outcome: no application changes, dependencies, commands, or migrations.
The shared fixture helper makes the same cases reproducible outside pytest. The
[evaluation record](../../evaluations/local-triage.md) retains full public command
output in compressed JSONL alongside readable findings. Author-run evaluations exercise
US1–US3; independent-agent SC-001, read-only, policy and retry-exhaustion validation was
subsequently completed for [release 0.2.0](../../evaluations/release.md). Further
provider/host boundary evaluations remain extensions.

## Phase 1: Establish the workflow and evaluation inputs

1. Define the skill name/version marker (`gh-maintainer-triage/v1`) and record its compatible
   CLI baseline. Validate the actual installation/load path supported by the agent used.
2. Define inputs: repository, database, item budget, freshness choice, optional policy and
   permitted updates. Resolve the database once using `db path`, then pass its absolute path
   on every command so a working-directory change cannot select a different index.
3. Define local outcomes: processed, blocked, deferred, and no pending work. An item waiting
   for someone else can be processed if its requested triage is complete; an item whose
   inspection or permitted decision is incomplete stays pending.
4. Create compact synthetic cases from existing test fakes, including a conversation with
   101 comments and a queue that requires several pages at a deliberately small page size.
   Reuse current tests for storage behavior rather than duplicating the baseline suite.

## Phase 2: Implement the skill

The instructions should describe this sequence using current JSON commands:

1. Check `repos` for the selected repository, coverage, and timestamps. Sync only when the
   caller's freshness choice requires it; check the result and reread coverage afterward.
2. Query both `issues` and `prs` with `--repo`, `--state all`, `--unprocessed`, and stable
   `--sort updated`. Gather distinct candidate IDs up to the combined item budget before
   any writes. Use documented sequential issue/PR allocation and report unvisited backlog;
   do not imply a globally ranked mixed queue. Concurrent sync can alter discovery order,
   so report the bounded observed set rather than promise a transactional snapshot.
3. For each candidate, read `show`, paginate `comments` and pending `changes`, and inspect
   earlier analysis/notes. Recheck `show` after gathering pages. If its revision changed,
   discard the draft and reread within the retry budget; otherwise save against that revision.
4. Save a source-grounded summary with `--producer gh-maintainer-triage/v1`. Write labels
   and ranks only under an explicit rubric. Update triage only within the delegated scope,
   retaining existing note content in an authorized replacement. When prior policy conflicts
   cannot be resolved, report proposals and leave the affected work pending.
5. Use `--revision` on each analysis/triage write and `mark-processed`; use `--actor` on the
   latter. Guard failures require rereading, never blind substitution. Separate commands
   are not an atomic batch: an earlier successful write may remain if a later write fails.
   Reinspect saved outputs before retrying and report partial effects in the handoff.
6. Verify saved results through `show` and produce a finite report of outcomes, source links,
   revisions, coverage time, saved effects, conflicts, and remaining work. Do not advance
   acknowledgement for blocked/deferred items or treat missing code context as a code review.

## Phase 3: Evaluate contracts and actual agent behavior

| Case | Expected observable outcome |
| --- | --- |
| Ordinary triage | Grounded summary/next action, matching revision, producer/actor, verified writes |
| Pagination and queue mutation | All selected candidates inspected once; all required comment pages read; explicit remaining backlog |
| Closed/merged follow-up | Newly observed activity included and stale analysis reconsidered |
| Push/comment edit during a run | Reread before retry; no unseen revision acknowledged; partial effects reported |
| Missing/failed coverage, unavailable item, read limit | Explicit blocker, no acknowledgement |
| Prior notes, snooze, conflicting policy | Decisions preserved or authorized changes explained; unresolved work remains pending |
| Hostile comments | No instruction-following or scope expansion based on source text |
| Unchanged rerun | No unnecessary rewrite; no new source activity claimed |

First run token-free fixture/CLI checks in pytest. Then load the skill in a real agent
session against the same disposable index and retain a sanitized transcript plus public
before/after query output. Record agent/model, skill version, case results, and limitations
in `docs/evaluations/local-triage.md`. Use a human-readable rubric; no exact prose matching
or new model-backed evaluation framework. Do not mark behavioral cases passed without a run.

## Phase 4: Package and document

Document loading/installing the skill, the executable/environment it expects, optional
GitHub authentication, supported CLI baseline, and a runnable synthetic walkthrough. Keep
the installed skill self-contained through relative supporting references. Add a short
README link, MkDocs navigation, and orientation links only when the skill is shipped.
Update the roadmap, decision log, and checklist with precisely the work verified.

## Validation and completion gate

Run focused `tests/test_triage_skill.py` and relevant maintenance regressions, Ruff, mypy
for changed Python, strict MkDocs, and `git diff --check`. Exercise documented fixture and
installation commands in a clean destination. Run the full suite if shared helpers or
application code change. Runtime-neutral instructions do not imply every agent integration
has been tested; name the one actually used.

US1 is the first usable increment. Shipping the feature requires the relevant US2 scenarios,
US3 installation checks, recorded actual-agent evidence, and all specification success
criteria. S2 code/diff/CI context remains a separate subsequent feature.
