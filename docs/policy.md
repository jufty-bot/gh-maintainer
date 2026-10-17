# Repository policy

Policy is an explicit JSON file, validated by Pydantic with unknown fields rejected.
It records repository-specific vocabulary, priorities, local-update permissions and
workflow defaults. It never grants remote GitHub permissions or runs test commands.

```bash
gh-maintainer policy --repo OWNER/REPO --file policy.json --init
gh-maintainer policy --repo OWNER/REPO --file policy.json --format json
```

`--init` refuses existing files. Without `--file`, the path is `.gh-maintainer.json`
in the working directory; there is no parent-directory or network discovery. `policy`
does not open SQLite. The output includes the absolute file path, SHA-256 hash,
normalized repository name and effective policy. A missing named repository fails.
Use absolute paths in agent runs and record the policy hash with analysis reasons.

The [example file](https://github.com/jufty-bot/gh-maintainer/blob/main/examples/policy.json)
shows all fields. Replace `owner/project` with the intended repository:

- `version`: currently `1`.
- `repositories`: exact repository names, normalized case-insensitively.
- `permissions`: allowed local commands: `summarize`, `classify`, `rank`, `triage`,
  `mark-processed`. Starter policy permits only summaries and acknowledgement.
- `labels`: label-to-definition mapping. No vocabulary is invented by the CLI.
- `ranking`: finite numeric `score` and nonempty `meaning` for each allowed band.
- `triage`: permitted dispositions and their meanings.
- `item_budget`: 1–500 candidates, default 10; a skill run shares its total across queues.
- `refresh`: default false; whether a skill must sync before processing.
- `test_commands`: suggested repository checks; agents inspect them before execution.

Pass the policy on each local write to enforce its permissions/vocabulary:

```bash
gh-maintainer --db "$DB" classify "$ITEM" bug --policy "$POLICY" --revision "$R" --producer agent/v1
gh-maintainer --db "$DB" rank "$ITEM" 3 --policy "$POLICY" --revision "$R" --reason 'Evidence and policy hash'
```

Commands reject labels, scores and dispositions outside the supplied policy before
saving. Source revision guards still apply. Existing commands without `--policy`
remain manual operations; this is an optional guard, not an access-control sandbox.
Budgets, freshness and policy meaning are workflow inputs interpreted by skills,
not automatic ingestion or analysis. Caller instructions may narrow the permitted
scope. Resolving incompatible policy requires human direction, not guessed precedence.
Changing a policy does not advance item revisions or invalidate stored analysis
automatically; explicitly request reconsideration and record the new policy hash.
