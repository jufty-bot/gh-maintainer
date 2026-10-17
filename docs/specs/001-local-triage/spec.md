# Feature specification: Local repository triage skill

**Feature ID:** `001-local-triage`
**Created:** 2026-10-17
**Status:** Implemented with author-run evaluations; independent-agent success (SC-001) remains unverified.
**Input:** Deliver the first useful maintenance skill over gh-maintainer's existing CLI,
with reproducible evaluation fixtures and installation instructions.

This specification uses the structure of GitHub Spec Kit's
[specification template](https://github.com/github/spec-kit/blob/main/templates/spec-template.md).
See the [implementation plan](plan.md) and [tasks](tasks.md).
Installation and observed results are in [skill usage](../../skills.md) and
[evaluation evidence](../../evaluations/local-triage.md). No evaluation runner is required.

## User scenarios and testing

### US1. Triage a bounded repository queue (P1)

A maintainer asks an agent to triage issues and PRs in one named repository. The agent
uses the local index, reads the discussion and prior decisions, records useful local
analysis within the delegated scope, and reports what needs attention next.

**Independent test:** Run the skill against a synthetic index with an open issue, open PR,
closed issue, and merged PR. Inspect the command transcript and resulting local records.

Acceptance scenarios:

1. Given complete coverage and a pending item, when the agent triages it, then its summary
   cites the item/discussion, its next action is concrete, and any acknowledgement names
   the revision actually inspected.
2. Given more queue items or comments than fit on one page, when the agent runs with a
   finite item budget, then it reads every required page for selected items and reports
   remaining work without skipping candidates because of its own writes.
3. Given prior notes or an active snooze, when the agent inspects the item, then it preserves
   that decision unless the requested scope permits changing it and explains any change.
4. Given missing taxonomy or ranking policy, when summarization remains possible, then
   the agent reports those limits without inventing labels or an unexplained score.

### US2. Resume after activity without acknowledging unseen work (P1)

A maintainer reruns the skill after pushes, comments, edits, or state changes. The agent
reconsiders affected items while retaining earlier human direction.

**Independent test:** Start from a previously processed fixture, add an observed change,
and verify that the agent inspects it before saving or acknowledging the new revision.

Acceptance scenarios:

1. Given a changed closed issue or merged PR, when the queue is inspected, then it remains
   eligible for follow-up despite the CLI's default open-state filters.
2. Given a revision change during inspection or saving, when a guarded write fails, then
   the agent rereads the evidence before retrying; it never just substitutes the new number.
3. Given a sync error, unavailable item, missing comment coverage, or a truncated discussion,
   when the agent reaches that item, then it reports a blocker and leaves it unacknowledged.
4. Given an unchanged processed item, when the run repeats, then it does not overwrite
   records unnecessarily or claim new source activity.

### US3. Install and evaluate the skill from the repository (P2)

A new agent can discover and load the skill without this conversation. A contributor can
reproduce its evaluation without GitHub credentials or a mandatory model subscription.

**Independent test:** Follow installation and fixture instructions in a clean checkout,
then record an agent run against those fixtures using the published evaluation rubric.

Acceptance scenarios:

1. Given documented prerequisites, when an agent loads the skill, then it can identify the
   supported commands, required inputs, local effects, and completion conditions.
2. Given comments containing instructions to change scope or run commands, when the agent
   reads them, then it treats them as source content and stays within the human's request.
3. Given only scripted CLI checks, when results are reported, then they are identified as
   contract validation; successful agent behavior requires a separate recorded agent run.

## Functional requirements

- **FR-001:** Require an explicit repository, selected database, finite item budget, and
  caller direction on freshness and permitted local updates. Defaults must be documented.
- **FR-002:** Inspect repository presence, item/comment coverage, errors, and observation
  times. Local revision equality must never be described as proof of current GitHub state.
- **FR-003:** Include all item states and collect a bounded list of candidate identities
  before mutating pending queues. Respect pagination and report evidence/budget limits.
- **FR-004:** Inspect item details, all available conversation pages needed for triage,
  observed changes, existing analysis, notes, and snoozes before deciding.
- **FR-005:** Keep conclusions grounded in source IDs/URLs and distinguish facts,
  inferences, and unavailable evidence. Conversation triage does not constitute code review.
- **FR-006:** Use public CLI commands for all operational reads/writes. Save summaries and,
  where policy permits, classifications, rankings, and triage with provenance and reasons.
- **FR-007:** Guard every local write and acknowledgement with the inspected revision;
  reread after a mismatch and use a finite retry budget.
- **FR-008:** Preserve prior decisions outside the requested update scope. Do not discard
  existing notes when preparing an authorized replacement; report unresolved conflicts.
- **FR-009:** Acknowledge only fully inspected revisions whose requested local work is
  complete. Blocked, deferred, or partially inspected items remain pending.
- **FR-010:** Produce a bounded handoff listing completed items/revisions, saved effects,
  blockers, remaining work, source freshness, and any uncertainty about concurrent changes.
- **FR-011:** Operate offline on an existing index. Synchronization is a separate optional
  step within the caller's scope; credentials are needed only for that step.
- **FR-012:** Evaluate pagination, changing revisions, incomplete coverage, prior decisions,
  hostile source text, and repeat runs using synthetic data and observable outcomes.

## Key entities

Reuse existing repository coverage, item revision, conversation, observation, analysis,
triage, and shared processing records. A run's candidate list and handoff are temporary
artifacts, not new database entities. Evaluation fixtures and transcripts are test evidence.

## Success criteria

- **SC-001:** Another agent can load the skill and complete US1 from repository docs alone,
  producing attributable, evidence-backed local results and a useful handoff.
- **SC-002:** Every acknowledged item in recorded evaluations has complete inspected
  conversation coverage and a matching inspected revision; zero unseen revisions are acknowledged.
- **SC-003:** All FR-012 scenarios have reproducible fixtures and explicit pass/fail criteria;
  report contract-check results separately from observed agent-run results.
- **SC-004:** A bounded run loses no selected candidate because of its own queue mutations,
  and an unchanged rerun preserves prior decisions without redundant writes.

## Assumptions and boundaries

This feature is one local triage skill covering the first S1 workflow and initial S5
packaging/evaluation. It is not completion of every S1 skill. Diffs, code review, detailed
CI diagnosis, GitHub writes, scheduling, multi-agent coordination, and a model runner remain
outside scope. The existing latest-result storage and shared processing checkpoint apply.
No new application dependencies, tables, or CLI commands are expected.
