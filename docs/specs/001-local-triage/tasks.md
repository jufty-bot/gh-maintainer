# Tasks: Local repository triage skill

**Inputs:** [Specification](spec.md), [implementation plan](plan.md).
**Status:** T001–T018 implemented and validated. Independent acceptance validation is recorded for T019 in the 0.2.0 release.
**Format:** `T### [US#] description`; tasks are ordered by dependency. Paths below are
implementation destinations relative to the repository root.

## Phase 1: Shared foundation

- [x] T001 Define the skill version, invocation inputs, defaults, local-write scope, outcome
  rules, and compatible CLI baseline in `skills/gh-maintainer-triage/SKILL.md`.
- [x] T002 Add scenario descriptions in `tests/fixtures/triage/scenarios.json` and temporary
  index setup in `tests/test_triage_skill.py`, reusing `tests/fakes.py` where suitable.
- [x] T003 Add the case matrix, observable assertions, and transcript/evidence format in
  `skills/gh-maintainer-triage/references/evaluation.md`. Separate contract checks from
  observed skill behavior; include every FR-012 scenario.

**Gate:** Inputs, expected outcomes, and reproducible synthetic setup are clear before
recording any agent success. No new production framework or schema is required.

## Phase 2: US1 — Bounded local triage (P1)

- [x] T004 [US1] Document repository coverage/freshness checks, fixed database selection,
  all-state discovery, and combined issue/PR item-budget allocation in
  `skills/gh-maintainer-triage/SKILL.md`.
- [x] T005 [US1] Document candidate collection before queue mutation, paginated item evidence,
  final revision recheck, guarded summary/triage writes, verification, and bounded handoff in
  `skills/gh-maintainer-triage/SKILL.md`.
- [x] T006 [US1] Explain optional caller taxonomies/ranking rubrics and retention of prior
  notes/snoozes in `skills/gh-maintainer-triage/references/triage-policy.md`; link it from the skill.
- [x] T007 [US1] Add focused fixture/CLI checks for ordinary triage, 101 comments, a multi-page
  pending queue that shrinks after processing, prior decisions, and missing policy in
  `tests/test_triage_skill.py`. Reuse existing storage/freshness tests.

**Independent checkpoint:** A skill session completes a bounded ordinary run using only
public commands, with source-grounded outputs, correct provenance, and no lost candidates.

## Phase 3: US2 — Activity and incomplete work (P1)

- [x] T008 [US2] Add changed closed/merged items and push/comment-edit race scenarios to
  `tests/fixtures/triage/scenarios.json`; exercise guarded rejection and rereading in
  `tests/test_triage_skill.py`.
- [x] T009 [US2] Add missing/failed coverage, unavailable items, evidence limits, partial
  writes, and unchanged-rerun cases in `tests/test_triage_skill.py`.
- [x] T010 [US2] Document the one-retry rule, non-atomic local writes, pending blocked work,
  shared processing semantics, and honest partial-effect reporting in
  `skills/gh-maintainer-triage/SKILL.md` and its evaluation reference.

**Independent checkpoint:** A previously processed fixture can be revisited safely after
activity, and incomplete/stale inspection never acknowledges unseen revisions.

## Phase 4: US3 — Installation and observed evaluation (P2)

- [x] T011 [US3] Write `docs/skills.md` with prerequisites, compatible CLI baseline, one
  verified agent installation/load method, a portable direct-load fallback, and a token-free walkthrough.
- [x] T012 [US3] Add shipped-file/reference checks in `tests/test_triage_skill.py` and verify
  copied skill references resolve without relying on this checkout's absolute paths.
- [x] T013 [US3] Add hostile-source and conflicting-guidance cases to
  `tests/fixtures/triage/scenarios.json` and their observable rubric to the evaluation reference.
- [x] T014 [US1] Run ordinary and pagination scenarios with an actual agent; record sanitized
  commands, public before/after output, outcomes, and unresolved limits in `docs/evaluations/local-triage.md`.
- [x] T015 [US2] Run activity, incomplete coverage, prior-decision, and rerun scenarios with
  an actual agent; record results in `docs/evaluations/local-triage.md`.
- [x] T016 [US3] Verify loading from a clean destination and run hostile-source cases with
  an actual agent; record the agent/model, skill version, and case verdicts in
  `docs/evaluations/local-triage.md`. Do not count scripted replay as behavioral success.

## Phase 5: Integration and release evidence

- [x] T017 Link shipped skill/install/evaluation docs from `README.md`, `docs/index.md`,
  `mkdocs.yaml`, `AGENTS.md`, and `docs/project-context.md`. Record accepted packaging/policy
  choices in `docs/decisions.md`; keep prospective capabilities clearly labeled.
- [x] T018 Run the plan's validation commands and walkthrough; record evidence in
  `docs/checklist.md`, update this task list, `docs/roadmap.md`, and `docs/agent-skills.md`.
  Keep unrelated S1–S5 work unchecked and report any failed or unevaluated cases.

## Validation follow-up

- [x] T019 Validate SC-001 with an independent agent/session loading only published
  installation/skill docs and synthetic inputs. See [independent acceptance](../../evaluations/release.md) for installed-skill
  triage, ranking, read-only behavior and actual retry exhaustion. Further native agent
  discovery remains an extension.

## Evidence

T001–T013: `skills/gh-maintainer-triage/`, `tests/fixtures/triage/scenarios.json`,
`tests/triage_fixtures.py`, `tests/test_triage_skill.py`, and [installation](../../skills.md).
The reusable fixture helper was added beside the planned test file so contributors can
seed/advance the same cases manually without a custom evaluation runner.

T014–T016: [Recorded author-run evaluations](../../evaluations/local-triage.md) cover
all 13 scenarios, a clean copied skill, source-grounded results and stopping decisions.
The full public command transcript is retained. All 25 focused skill/maintenance tests
and the skill-creator validator passed. Independent-agent success is recorded separately in the 0.2.0 acceptance report.

T017: README/navigation/orientation link the shipped skill, [D11](../../decisions.md#d11-repository-owned-triage-skill-over-public-commands)
records accepted choices, and the broader skills roadmap remains open.

T018: all 113 tests passed (including 14 new skill contract cases); Ruff lint/format,
mypy for application and fixture helper, strict MkDocs, copied-skill validation,
local links, and `git diff --check` passed. The walkthrough used a clean copied skill
and disposable indexes. No runtime code, dependencies, or schemas changed.

## Dependencies and delivery

T001–T003 establish the foundation. T004–T007 deliver the first usable US1 slice.
T008–T010 extend that slice for US2. T011–T013 establish distribution and the remaining
evaluation cases. T014 depends on US1 and the evaluation rubric; T015 depends on US2;
T016 depends on T011–T013. T017–T018 close the feature after recorded results.

Work sequentially through these checkpoints. If evaluation exposes a CLI defect, record
the smallest reproducible case, fix the existing module with a focused regression test,
and update the spec/plan/tasks before adding new interfaces or dependencies.
