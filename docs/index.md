<img src="assets/brand-mark.svg" width="48" height="48" alt="" />

# gh-maintainer

**Keep good projects moving.**

A CLI for maintaining GitHub repositories, with a local index that humans and agents can query.

Sync issues, pull requests, and their conversations into SQLite. Summarize and classify
items, rank the queue, record triage decisions, and track what changed since an item was
last processed. Incremental syncs retain history and flag earlier analysis when new
activity arrives. FastAPI serves a dashboard with Markdown descriptions, searchable
maintenance queues, and editable local triage decisions.
`repos` records each repository's latest default-branch commit, release, PR, and issue,
with timestamps, links, and sync coverage.

The [bundled skills](skills.md) teach agents to triage and prioritize work, review
PRs, diagnose CI, reproduce issues, and prepare fixes or maintenance digests. Agents can
read commit-bound code, checks, logs, and review threads through the CLI, while keeping
human decisions and repository policy in view.

## Get started

Install a wheel from [releases](releases.md), or work from this checkout. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)
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

Use `--config maintainer.yaml` to load JSON/YAML command defaults and a shared
`repositories` list. See [configuration files](configuration.md) for precedence and JSON Schema.

Use `classify`, `summarize`, `rank`, and `triage` to save decisions, then `mark-processed`
to acknowledge an inspected revision. `changes` shows observed pushes, comments, and
state changes since that checkpoint. All these commands support JSON output.

Use `skills --install PATH` to install the bundled agent instructions and `policy` to
validate repository rules. `context ITEM` fetches live technical evidence through `gh`.

The current CLI reads GitHub and writes local data. Analysis is supplied by a human or
agent; it does not invoke a model or post changes to GitHub. It reuses `gh` authentication
for async HTTPX sync and `gh api` context requests. See [CLI workflows](cli.md) for the full loop,
revision guards, and pagination.

MCP clients can use the same maintenance services over HTTP or stdio:

```bash
uv run gh-maintainer mcp                   # stdio (default)
uv run gh-maintainer serve                 # Dashboard, REST, MCP at :8765/mcp
uv run gh-maintainer mcp --transport http   # http://127.0.0.1:8766/mcp
```

See [MCP setup and tool contracts](mcp.md) for client configuration, background
watching, revision guards, and the CLI-to-tool mapping.

## Documentation

- [Installation and releases](releases.md), [agent skills](skills.md),
  [policy](policy.md), and [technical context](context.md).
- [Usage and deployment](usage.md) — authentication, sync coverage, dashboard, and Docker.
- [Roadmap](roadmap.md) and [agent skills plan](agent-skills.md) — what comes next.
- [Project context](project-context.md), [architecture](architecture.md), and
  [decisions](decisions.md) — how the project fits together and why.
- [Contributing](contributing.md) and [implementation checklist](checklist.md) —
  development and tracked work. Agents should start with [AGENTS.md](https://github.com/jufty-bot/gh-maintainer/blob/main/AGENTS.md).
