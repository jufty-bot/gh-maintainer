# Configuration files

Pass `--config PATH` before or after the command to read a JSON (`.json`) or YAML
(`.yaml` / `.yml`) file. Choose the command on the CLI; the file supplies defaults
for any commands you use. For example, `maintainer.yaml`:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/jufty-bot/gh-maintainer/main/docs/config.schema.json
db: .data/maintainer.sqlite
repositories:
  - YOUR_LOGIN/YOUR_REPOSITORY
sync:
  full: false
watch:
  interval: 300
report:
  format: json
  output: portfolio.json
serve:
  host: 127.0.0.1
  port: 8765
```

```bash
uv run gh-maintainer --config maintainer.yaml watch
uv run gh-maintainer report --config maintainer.yaml --output another-report.json
```

JSON uses the same keys. A `$schema` property enables editor validation:

```json
{
  "$schema": "https://raw.githubusercontent.com/jufty-bot/gh-maintainer/main/docs/config.schema.json",
  "db": ".data/maintainer.sqlite",
  "repositories": ["YOUR_LOGIN/YOUR_REPOSITORY"],
  "watch": {"interval": 300}
}
```

Explicit CLI arguments override file values. For the database, precedence is
`--db`, config `db`, `GH_MAINTAINER_DB`, then the platform user data directory.
Paths are relative to the current working directory. Top-level `repositories` supplies
the default repository list for both `sync` and `watch`. A command's `repo` setting
overrides that list; CLI `--repo` values override both. Lists are replaced, rather than
combined. Set a command's `repo` to `null` or `[]` to sync all repositories for that
command. Other command settings are independent: `sync` settings do not apply to
`watch`. `--no-full` overrides `full: true`.

The optional `triage` section accepts `item_id`, `disposition` (default `planned`),
`note`, `until`, `priority`, `category`, `policy`, and `revision`;
its positional arguments can be supplied in the file or on the CLI. Quote ISO
timestamps such as `until: "2026-12-01T09:00:00Z"`. `demo` and `migrate` use the shared
`db` setting. Omitted values keep their usual defaults. Unknown keys, invalid types,
and invalid values are rejected before a command starts.

Configuration is validated by the `gh_maintainer.config.Config` Pydantic model. The generated
[JSON Schema](config.schema.json) is published with the documentation.
Regenerate `docs/config.schema.json` from the model by running
`uv run python -m gh_maintainer.config` from the repository root.

Command sections also support the indexed query, analysis, context, policy, and skills
commands. Use snake_case for option names, such as `requests_per_second`, `max_chars`,
and `policy_file`. Use `mark-processed` for that command's section. Required positional
arguments can be supplied in a section, such as `show: {item_id: "owner/repo#42"}`.
Click validates and converts selected command defaults using the same types as CLI input.
`--owner` and configured repository lists remain mutually exclusive.

CLI configuration and [repository policy](policy.md) are separate documents. CLI defaults
select inputs; a policy file defines the optional guards on local writes. Loading a CLI
config does not change or replace repository policy.


MCP tools inherit these command defaults when starting `mcp` or `serve` with `--config`.
Tool arguments override configured values, including fields inside partial query
`options` and context `request` objects. MCP discovery shows the effective defaults.
Configuration is loaded at startup; restart the server after changing the file.
Output-format defaults apply to `report`; other MCP results remain structured JSON.
Query sections include author/date/priority/category/triaged filters, and `prune`
accepts `keep`, `days`, and `vacuum`. See [MCP contracts](mcp.md).
