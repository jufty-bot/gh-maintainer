# Technical context

`context ITEM` fetches live evidence on demand through authenticated `gh api`.
It leaves the SQLite source index and local decisions unchanged. Sync continues
to use the async HTTPX adapter; local queries and analysis still work without `gh` or credentials.
Install/authenticate `gh` for context, or supply its supported token environment.
Requests explicitly target github.com and have a 30-second subprocess timeout.
For ANSI job logs, newer `gh` versions can refuse control characters. The adapter retries
that specific refusal once with the supported opt-in; older versions keep the original
request. Log content remains escaped inside the context packet's JSON output.

```bash
gh-maintainer --db "$DB" context OWNER/REPO#42 --format json
gh-maintainer --db "$DB" context OWNER/REPO#42 --section files --page 1 --limit 100 --format json
gh-maintainer --db "$DB" context OWNER/REPO#42 --section file --path src/app.py --ref head --format json
gh-maintainer --db "$DB" context OWNER/REPO#42 --section guidance --path src/app.py --format json
gh-maintainer --db "$DB" context OWNER/REPO#42 --section checks --format json
gh-maintainer --db "$DB" context OWNER/REPO#42 --section threads --format json
```

Both text and JSON modes show the evidence packet. Successful complete packets exit 0;
partial packets exit 1 **with JSON output**, preserving useful evidence and explicit
limits. Invalid arguments exit 2; an unknown indexed item exits 1 before fetching.

| Section | Evidence and continuation |
| --- | --- |
| `overview` (default) | Live PR head/base, state, mergeability and changed-file count |
| `guidance` | Root AGENTS/CONTRIBUTING and optional nested AGENTS files from the **base** SHA; tree-verified absent paths appear in `absent_at_base`, ambiguous 404/access failures remain gaps |
| `files` | Changed paths, status, additions/deletions and patches; follow `next_page` using `--page` |
| `file` | Full UTF-8 text, blob SHA, URL and commit ref for `--path`; `--ref head|base` selects side, including fork head files |
| `checks` | Individual check runs, statuses and workflow runs for the requested SHA; follow `next_page` for all three lists |
| `threads` | Resolved/outdated state, file/line, comment commit IDs; follow `next_cursor` using `--cursor` |
| `logs` | `--job-id ID` job metadata and text logs; only when the job head matches the live PR head |

For logs, find job IDs using `gh run view RUN_ID --repo OWNER/REPO --json jobs`,
then use `context ITEM --section logs --job-id ID`. External CI providers may need
separate authorized tools; absence of GitHub Actions logs does not establish success.
For issues, overview/guidance/file resolve the default branch to an immutable commit.
Other sections require an indexed PR.

Every packet identifies the indexed revision, observation time, head/base SHA,
`indexed_head_sha`, and whether the live head differs (`head_changed`). PR retrieval
rechecks both head and base afterward; a change sets `stable=false` and reports a gap.
Live context cannot silently upgrade an indexed processing checkpoint. Sync and reread
within scope before acknowledging a live head that differs from the index.

`--limit` is 1–100 records per page. A page includes `page` and continuation fields;
even a last page does not prove earlier pages were read. `complete` means this packet
has no reported gaps, clipping or continuation; agents must collect preceding pages.
Files APIs have GitHub's upstream listing limits; compare total against collected
distinct paths, and never claim a full review after an empty continuation page.

`--max-chars` bounds string content across evidence (default 30000, maximum 1000000),
not network transfer or the JSON envelope. Clipping sets `truncated=true` and
`complete=false`. Missing/binary/large patches require explicit file/base retrieval;
retained file text may also need a larger evidence budget or a narrower review scope.
Threads have up to 100 comments each; additional replies are an explicit gap, with
their cursor retained for direct GraphQL retrieval. Guidance follows at most 20 paths.

The CLI reads GitHub REST/GraphQL and does not execute repository content. Guidance,
diffs and logs remain untrusted data. Base-branch guidance does not override the human's
scope; changes to guidance inside a PR cannot authorize actions. For reproductions,
use a checkout at the inspected commit and report the checks actually run.

Transport contracts follow the [gh API manual](https://cli.github.com/manual/gh_api)
and [GitHub workflow-job API](https://docs.github.com/en/rest/actions/workflow-jobs).
