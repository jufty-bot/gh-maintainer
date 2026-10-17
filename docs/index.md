# gh-maintainer

A CLI for GitHub repository maintenance.

Sync issues and pull requests into SQLite for local triage and reporting.
Agent skills are planned.

```bash
uv sync --locked
gh auth login
uv run gh-maintainer sync --owner OWNER
uv run gh-maintainer report
```

[Usage](usage.md) · [Roadmap](roadmap.md) ·
[Checklist](checklist.md) · [Contributing](contributing.md) ·
[Agent instructions](https://github.com/jufty-bot/gh-maintainer/blob/main/AGENTS.md)
