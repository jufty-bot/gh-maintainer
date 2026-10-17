# gh-maintainer

A CLI for maintaining GitHub repositories, with a local index that humans and agents can query.

Sync issues, pull requests, and their conversations into SQLite. Summarize and classify
items, rank the queue, record triage decisions, and track what changed since an item was
last processed. Incremental syncs retain history and flag earlier analysis when new
activity arrives.

The CLI is the foundation for agent skills that will teach agents to triage issues,
review PRs, diagnose failures, and maintain repositories on behalf of humans. The index
and local maintenance commands work today; those skills and richer code/CI context are
[planned](agent-skills.md).

## Get started

From this checkout, install [uv](https://docs.astral.sh/uv/getting-started/installation/)
and authenticate with `gh auth login` or set `GH_TOKEN` / `GITHUB_TOKEN`:

```bash
uv sync --locked
uv run gh-maintainer sync --owner OWNER
uv run gh-maintainer prs --unprocessed
uv run gh-maintainer show OWNER/REPO#42
uv run gh-maintainer comments OWNER/REPO#42 --format json
```

Omit `--owner` to sync your own repositories, or repeat `--repo OWNER/REPO` to select a
list across owners. Data lives in your platform’s user data directory; `gh-maintainer db`
shows the path. Use `--db PATH` for another index. Local queries and decisions work
offline after sync.

Use `classify`, `summarize`, `rank`, and `triage` to save decisions, then `mark-processed`
to acknowledge an inspected revision. `changes` shows observed pushes, comments, and
state changes since that checkpoint. All these commands support JSON output.

The current CLI reads GitHub and writes local data. Analysis is supplied by a human or
agent; it does not invoke a model or post changes to GitHub. It reuses `gh` authentication
and calls GitHub through PyGithub. See [CLI workflows](cli.md) for the full loop,
revision guards, and pagination.

## Documentation

- [Usage and deployment](usage.md) — authentication, sync coverage, dashboard, and Docker.
- [Roadmap](roadmap.md) and [agent skills plan](agent-skills.md) — what comes next.
- [Project context](project-context.md), [architecture](architecture.md), and
  [decisions](decisions.md) — how the project fits together and why.
- [Contributing](contributing.md) and [implementation checklist](checklist.md) —
  development and tracked work. Agents should start with [AGENTS.md](https://github.com/jufty-bot/gh-maintainer/blob/main/AGENTS.md).
