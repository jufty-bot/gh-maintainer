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

[Usage](docs/usage.md) · [Roadmap](docs/roadmap.md) ·
[Checklist](docs/checklist.md) · [Contributing](docs/contributing.md) ·
[Agent instructions](AGENTS.md)
