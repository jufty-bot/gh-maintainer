# Maintenance skills

`gh-maintainer-triage` teaches an agent to inspect a bounded issue/PR queue, save
source-grounded local summaries, apply delegated policy, and acknowledge only inspected
revisions. It includes changed closed/merged items, prior decisions, pagination, and
incomplete coverage. The bundle also supplies prioritization, technical review, CI
diagnosis, issue repair and portfolio workflows:

| Skill | Task |
| --- | --- |
| `gh-maintainer-triage` | Bounded conversation triage and activity follow-up |
| `gh-maintainer-prioritize` | Policy-grounded classification and ranking |
| `gh-maintainer-review` | Commit-bound PR code review |
| `gh-maintainer-ci` | Individual CI/check/log diagnosis |
| `gh-maintainer-fix` | Issue reproduction and validated local fixes |
| `gh-maintainer-maintain` | Portfolio follow-up, digests, backlog/release preparation |

Instructions do not prove behavior: see the [release validation](evaluations/release.md)
for cases actually exercised and remaining limits. The [roadmap](agent-skills.md)
continues to track richer scenarios and future extensions.

## Prerequisites and compatibility

Use Python 3.10+ and `uv sync --locked` from this checkout, or an installed
`gh-maintainer` executable. The current bundle requires CLI `0.2.0+`: triage is
`gh-maintainer-triage/v2`; other workflows use `/v1`. The earlier triage v1 evaluation
remains a historical CLI 0.1.0 result. Record skill marker, installed version, checkout
commit when available, and policy hash. No new runtime dependency or migration is added.

The agent needs a shell and permission to read the selected index and perform the requested
local writes. Existing indexes work offline without GitHub credentials. A separately
requested sync uses `GH_TOKEN` / `GITHUB_TOKEN` or `gh auth login` as described in
[usage](usage.md). The skill does not start an agent, call a model, or post to GitHub.

## Load or install

The wheel bundles all skills. Follow [installation](releases.md), then copy the complete
directories into a **new** destination (existing skills are never overwritten):

```bash
gh-maintainer skills --format json
gh-maintainer skills --install /absolute/new/agent-skills
```

Reload skill discovery in the agent before invoking `$gh-maintainer-triage`. Automatic
discovery varies between hosts. The validated method is **direct loading in Codex**:
ask it to read the copied `SKILL.md` by its absolute path, follow it, and read its relative
references as needed. This portable method also works in agents with file/shell access;
native discovery in other agents has not been evaluated. Installing the wheel makes
the resources available; `skills --install` copies them to the agent's selected location.

Example request (replace paths/repository):

```text
Read /absolute/path/gh-maintainer-triage/SKILL.md and follow it to triage
owner/project in /absolute/path/index.sqlite. Use the existing indexed snapshot,
at most 5 items. Save summaries and acknowledge completed triage. Preserve notes,
snoozes, classifications and ranks. Report blockers and remaining work.
```

Default scope is one repository, 10 candidate items, issues before PRs, 100 records/page,
indexed freshness, summary writes plus completed-triage acknowledgements, and one full
reread/retry per item after revision conflict. A read-only request writes nothing.
For labels, ranks, or disposition changes, provide the selected [policy file](policy.md)
and permitted updates. Skills read its hash and pass `--policy` on writes. Technical
skills use [context packets](context.md), a matching checkout, and project checks.
No taxonomy is invented when policy is missing. Processing is shared
per item; it is not a private checkpoint for each agent.

## Token-free walkthrough

Run from the checkout. The fixture helper is development tooling, separate from the CLI:

```bash
uv sync --locked
case_dir=$(mktemp -d)
uv run --locked python -m tests.triage_fixtures seed "$case_dir/index.sqlite" --scenario pagination
uv run --locked gh-maintainer --db "$case_dir/index.sqlite" db path
uv run --locked gh-maintainer --db "$case_dir/index.sqlite" repos --format json
```

Give the agent that absolute index path and the example request above. Use repository
`owner/project`, budget 5, queue page size 2, and comment page size 100. The fixture has
six items (including a closed issue and merged PR) and 101 comments on issue #1. The
last comment adds a condition needed in the summary. Candidate IDs must be collected
before processing shrinks the queue. Expect five completed items and one unvisited PR;
the merged PR may remain unvisited under this allocation, so evaluate it separately with
the `closed-merged` fixture. `seed` refuses an existing file and never uses the default DB.

Inspect the result with public commands:

```bash
uv run --locked gh-maintainer --db "$case_dir/index.sqlite" show owner/project#1 --format json
uv run --locked gh-maintainer --db "$case_dir/index.sqlite" issues --state all --unprocessed --format json
uv run --locked gh-maintainer --db "$case_dir/index.sqlite" prs --state all --unprocessed --format json
uv run --locked pytest tests/test_triage_skill.py tests/test_maintenance.py -q
```

Other scenarios and their pass criteria are in the skill's
[evaluation reference](https://github.com/jufty-bot/gh-maintainer/blob/main/skills/gh-maintainer-triage/references/evaluation.md).
For source races on **PR #2**, use `python -m tests.triage_fixtures advance PATH --scenario race`
between reads and writes. `--generation 2` injects a second head/comment change on that
PR for retry-budget evaluation. Issue #1 is unchanged by this fixture action.
See the [recorded evaluation](evaluations/local-triage.md)
for actual observed behavior, contract results, and limitations. Scripted tests alone
do not validate agent decisions.

Local writes use the inspected revision and are separate transactions. Earlier saves
can remain after a later failure. Source guards do not lock concurrent local analysis
edits, and the indexed snapshot cannot prove current remote state. The skill reports
these limits and leaves blocked/deferred items pending.
