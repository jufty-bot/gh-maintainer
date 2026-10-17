# gh-maintainer

Build a local GitHub maintenance database, then put agents to work with it.

gh-maintainer is a CLI with a simple goal:

- Ingest a list of repositories or all repositories belonging to an owner.
- Collect every issue, pull request, and their comments.
- Keep that data up to date incrementally in a queryable SQLite database.
- Store summaries, classifications, rankings, and triage in additional tables.

Once this baseline is complete, this repository will focus on agent skills that teach
agents to operate the CLI and maintain repositories on behalf of humans.

**Today:** index issues and PRs from a repository list or a user/organization owner,
refresh PR review and CI signals, record local triage, and explore reports and a dashboard.
Comments, CLI query commands, and persisted analysis remain on the
[roadmap](roadmap.md).

## Try it

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run from this checkout:

```bash
uv sync --locked
# Authenticate with gh auth login, or export GH_TOKEN / GITHUB_TOKEN.
uv run gh-maintainer sync
uv run gh-maintainer report
```

Use `sync --owner OWNER` for a named user or organization, or repeat
`sync --repo OWNER/REPO --repo OTHER/REPO` to select repositories across owners.

The index lives at `.data/maintainer.sqlite` by default; use `--db PATH` to choose another.
To explore the dashboard without GitHub credentials:

```bash
uv run gh-maintainer --db .data/demo.sqlite demo
uv run gh-maintainer --db .data/demo.sqlite serve
```

Open **http://127.0.0.1:8765**. The current CLI reads GitHub and saves local decisions;
it does not change your GitHub repositories.

## Help build it

Start with the [roadmap](roadmap.md) and [implementation checklist](checklist.md).
Agents should read [AGENTS.md](https://github.com/jufty-bot/gh-maintainer/blob/main/AGENTS.md) and keep those planning documents current as work lands.

- [Usage and deployment](usage.md)
- [Architecture and data contracts](architecture.md)
- [Contributing and development](contributing.md)

Built with Python, SQLAlchemy, and PyGithub, using the
[juftin/cookiecutter-python](https://github.com/juftin/cookiecutter-python) template.
