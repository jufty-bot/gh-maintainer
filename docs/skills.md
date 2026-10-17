# Local triage skill

`gh-maintainer-triage` teaches an agent to inspect a bounded issue/PR queue, save
source-grounded local summaries, apply delegated policy, and acknowledge only inspected
revisions. It includes changed closed/merged items, prior decisions, pagination, and
incomplete coverage. This is conversation triage; code review and CI diagnosis remain
in the [skills roadmap](agent-skills.md).

## Prerequisites and compatibility

Use Python 3.10+ and `uv sync --locked` from this checkout, or an installed
`gh-maintainer` executable. Skill version `gh-maintainer-triage/v1` was validated against
CLI `0.1.0`, baseline commit `d8f1b8b`, including Click help and revision-guarded writes.
Because package versions may not identify unreleased changes, record the checkout commit
as well as `gh-maintainer --version`. No application dependency or migration is added.

The agent needs a shell and permission to read the selected index and perform the requested
local writes. Existing indexes work offline without GitHub credentials. A separately
requested sync uses `GH_TOKEN` / `GITHUB_TOKEN` or `gh auth login` as described in
[usage](usage.md). The skill does not start an agent, call a model, or post to GitHub.

## Load or install

The repository owns the entire self-contained directory
[`skills/gh-maintainer-triage`](https://github.com/jufty-bot/gh-maintainer/tree/main/skills/gh-maintainer-triage).
Copy the directory and its `references/` together. For a Codex setup using a user skills
directory, run from the checkout (choose a new destination; do not overwrite an existing
customized skill):

```bash
skill_root="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$skill_root"
test ! -e "$skill_root/gh-maintainer-triage" && cp -R skills/gh-maintainer-triage "$skill_root/"
```

Reload skill discovery in the agent before invoking `$gh-maintainer-triage`. Automatic
discovery varies between hosts. The validated method is **direct loading in Codex**:
ask it to read the copied `SKILL.md` by its absolute path, follow it, and read its relative
references as needed. This portable method also works in agents with file/shell access;
native discovery in other agents has not been evaluated. Installing the Python wheel
alone does not install the skill; copy it from a checkout of the desired revision.

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
For labels, ranks, or disposition changes, provide the permitted updates and applicable
taxonomy/rubric. No taxonomy is invented when policy is missing. Processing is shared
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
For source races, use `python -m tests.triage_fixtures advance PATH --scenario race`
between reads and writes. See the [recorded evaluation](evaluations/local-triage.md)
for actual observed behavior, contract results, and limitations. Scripted tests alone
do not validate agent decisions.

Local writes use the inspected revision and are separate transactions. Earlier saves
can remain after a later failure. Source guards do not lock concurrent local analysis
edits, and the indexed snapshot cannot prove current remote state. The skill reports
these limits and leaves blocked/deferred items pending.
