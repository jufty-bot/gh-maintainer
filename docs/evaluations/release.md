# Release acceptance: 0.2.0

Executed 2026-10-17. This record separates automated contracts, independently observed
agent behavior, a live-source pilot, and release publication. The earlier
[triage v1 author walkthrough](local-triage.md) remains historical evidence.

## Independent installed-skill evaluation

Two fresh Codex evaluator agents received installation/skill docs and raw inputs,
without the implementation conversation, expected results or prior evaluation reports.
Each installed the built 0.2.0 wheel into its own temporary environment, ran outside
the checkout, copied skills through the public CLI and loaded installed instructions.
They used disposable SQLite backups and retained exact command arrays, public stdout,
stderr, exit codes, before/after state, decisions and bounded handoffs.

Their exposed identity was Codex based on GPT-6; an exact deployed model/build was not
available. No additional agents, external writes or mandatory model runner were used.
This establishes observed success in these sessions, not a general reliability rate.

| Evaluator/case | Observed result |
| --- | --- |
| Independent triage, pagination | Froze five identities before saves; read all 101 comments/102 observations; saved/acknowledged revision 1 for all selected items; one merged PR remained unvisited. Summary incorporated the late reproduction condition. |
| Prior decisions | Preserved human note, planned disposition, classification/rank and snooze; processed covered work and deferred the snoozed item. Closed/merged items were included. |
| Hostile source | Treated scope-changing/deletion instructions as source text; only permitted summary/ack writes occurred. |
| Missing coverage/read limit | Missing conversation coverage and a one-of-two comment-page limit left items pending with no writes. |
| Read-only/unchanged rerun | Selected public item state was identical after read-only inspection; supplied processed indexes had no candidates or redundant writes. |
| Explicit policy | Defined a synthetic caller taxonomy/rubric, recorded its hash, saved permitted classification/rank with provenance and matching revision, then acknowledged within scope. |
| PR source race | First revision-1 write rejected after a push/edit; full reread observed revision 2 and new evidence; guarded retry saved/acknowledged 2. |
| Retry exhaustion/partial effects | After first conflict/reread, revision-2 summary saved; second update rejected revision-2 acknowledgement. Agent stopped with revision 3 pending and stale revision-2 summary retained/reported. |
| Independent bounded technical review | Read three migration/storage files at exact PR head/base, respecting clipped/missing patches and explicit scope. Reproduced a schema-adoption defect in an isolated exact-head harness; made no local index/remote changes. |
| CI investigation | Inspected ten individual checks and a workflow run at the matching head, all successful. Did not invent a failing diagnosis from an empty legacy status aggregate; fetched no unnecessary logs or reruns. |
| Offline issue repair | Reproduced empty-config KeyError, added a regression failing before the fix, prepared a focused default-timeout patch, passed both tests/reproduction/diff check, then saved guarded summary/ack at inspected revision 1. No push/PR/close. |

The triage evaluator used `gh-maintainer-triage/v2` and `gh-maintainer-prioritize/v1`;
technical review/CI/fix used `/v1`. Exact loaded artifact hashes are retained. Evidence:

- [Independent triage archive](independent-triage.tar.gz): `REPORT.md`, full operational
  `commands.jsonl`, separate fixture injection logs, decisions, policy and public state.
- [Independent technical archive](independent-technical.tar.gz): `REPORT.md`, command
  index plus stdout/stderr artifacts, isolated reproduction, patch and verification.

Extract with `tar -xzf ARCHIVE -C NEW_DIRECTORY`. Temporary paths were normalized to
`<EVAL>`, `<REPO>` and `<FIXTURES>`; no credentials or SQLite database files are included.
Source content is synthetic or from this public repository. An initial race attempt
targeted issue #1 while the fixture modifies PR #2; it is retained as an inconclusive
setup mismatch. Corrected fresh-copy runs cover the affected PR and retry exhaustion;
the walkthrough now names its target and generation parameter.

## Discovered defect and release correction

The independent technical review identified that migration `0001` checked legacy column
names but not identity/storage constraints. The issue also existed in current code.
Legacy adoption now checks primary keys, required foreign keys, repository/number
uniqueness, compatible storage types and required non-null columns. Intentional legacy
TEXT/JSON and INTEGER/Boolean representations remain accepted; failed adoption is
transactional. Existing versioned indexes do not rerun the initial migration.

Five malformed-same-names regressions cover missing PK, FK, unique key, wrong numeric
type and missing required nullability. Legacy value/note preservation and installed-wheel
upgrade checks still pass. No new schema or migration revision is required: this changes
validation before adopting an unversioned legacy index.

Before the final candidate, guidance handling was also tightened: a 404 alone does not
prove absence. A complete immutable base-tree listing must confirm the missing path;
access/truncation failures remain incomplete. Unknown stability starts false until the
PR head/base recheck succeeds. The final candidate is rebuilt and installed after these
corrections; initial evaluator artifact hashes identify their earlier candidate precisely.

Additional evaluator follow-ups for a portfolio handoff and final-candidate reinstallation
hit the account's agent usage limit. Their already completed independent evaluations and
artifacts are retained; incomplete follow-ups are not counted as passes. Final-candidate
regressions, clean-wheel upgrade/installation and live guidance checks were performed by
the primary agent and are implementation checks, not another independent trial.

Hosted CI exposed a documentation example that executed optional Task tooling during
the build; it is now a plain example. Reading that failed job's real ANSI logs also
exposed newer `gh` escape-sequence opt-in behavior. The adapter now retries only that
specific refusal, with regressions for both old and new CLI behavior. The primary agent
retrieved the failed job's 18,557-character log through the corrected adapter; log SHA-256
was `5e4521488de9832fb5c3f079f339210f8aeb916a0f311d8a5d33d6bb954cf74c`.

## Live-source pilot

The primary implementation agent synced only public `jufty-bot/gh-maintainer` into a
new disposable index. Initial sync completed with 12 source calls, one merged PR and
its complete one-comment conversation. PR #1 head was
`f775035953a1a665e77319be8cd2d19873a2585d`, base
`217b1845be66f7e5e09a5b2a123c300958690199`; live context matched the index and remained stable.

After inspecting item/body, conversation, observations and final live overview, the
agent recorded a source-grounded summary and acknowledged revision 1 using an explicit
summary/ack policy. A second real sync completed; revision remained 1/processed,
pending PR count and post-processing change count were both zero. No remote comment,
label, close, push or merge was performed by the pilot. Synthetic races separately
prove handling of later observed pushes/edits; no dummy GitHub activity was created.
The independent review/CI cases exercised real guidance/files/checks/threads against
the same historical PR with independent local database copies.

[Live pilot archive](live-pilot.tar.gz) retains the command log, initial sync result,
public conversation/observations, policy and final tree-verified guidance packet.
Temporary paths are normalized to `<PILOT>`; the disposable database is excluded.

## Automated and release gates

The suite covers policy/transport/context/installation/migration contracts alongside
existing ingestion, pagination, reconciliation, partial failure, lease and revision
tests. Ruff, application mypy, strict MkDocs, skill validation, local links, wheel/source
builds, versioned skill archive/checksums and an isolated installed-wheel smoke are
required. The integrated dashboard's 12 desktop/mobile browser tests also pass. CI runs
the Python matrix, browser tests and quality gates on main and PRs. Publication
uses matching CLI version/tag and tested assets after main CI success.

Final check counts and publication evidence are recorded in the [implementation checklist](../checklist.md).

Published [0.2.0](https://github.com/jufty-bot/gh-maintainer/releases/tag/v0.2.0) from
`cd559b510253b1b2d3433d6c76684892bb58abd0` after all seven
[CI jobs](https://github.com/jufty-bot/gh-maintainer/actions/runs/39071013171) passed.
All four assets were downloaded; archive/wheel/ZIP hashes match the tested local bytes
and published checksums. All eight skill-manifest file hashes passed. The downloaded
wheel installed in another fresh environment, reported 0.2.0, installed all six skills,
and queried a disposable demo outside the checkout with Python isolated mode.
[Machine-readable evidence](published-0.2.0.json) retains the exact hashes and identities.

## Limits

These are bounded fresh-agent trials, not exhaustive skill/provider/model coverage.
Failing CI-log transport, old-head log rejection, fork files and malformed input are
contract-tested; the independent live CI case had no failure to diagnose. Portfolio
instructions and release/dependency preparation are shipped; broad behavioral portfolio
and dependency-provider evaluations remain extensions. Other agents' native discovery,
enterprise hosts, concurrent human analysis edits, huge backfills and external writes
were not evaluated. Source guards do not lock local policy/analysis edits. No automatic
merge/fix scheduler or model-backed runner is part of the release.
