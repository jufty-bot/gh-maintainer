# Specification: complete maintenance release

Status: implementation and acceptance validation in progress.

Deliver the six workstreams requested after feature 001: versioned distribution,
continuous validation, repository policy, technical evidence, maintenance skills,
and independent/live acceptance. Keep the application small and reuse existing
storage, Pydantic, Click, Git and gh. No new service, model provider or schema is needed.

## User scenarios

1. A new user installs a wheel outside the checkout and installs the matching skill
   bundle. Existing and legacy indexes remain queryable with local notes preserved.
2. An agent loads one repository's explicit policy and can save only permitted local
   labels/scores/dispositions when `--policy` is supplied; missing policy never licenses
   invented rules. Manual commands retain their existing behavior.
3. An agent reviews a PR or diagnoses CI using named commits, source files, guidance,
   checks/logs and review threads, with pagination and missing evidence explicit.
4. An agent performs triage, ranking, review, diagnosis, repair or portfolio handoff
   using public commands, preserving human direction and acting only within scope.
5. Maintainers can distinguish scripted contract checks, independently observed agent
   behavior, real-source pilot results and actual published artifacts.

## Requirements

- R1: Package migrations and self-contained skills in the wheel, plus a versioned skill
  ZIP with file manifest/checksums; installation must refuse existing skill destinations.
- R2: Run Python 3.10–3.14 tests plus lint/format/types/docs/build/installed checks on PRs
  and direct main pushes. Publishing follows validated artifacts and matching version/tag.
- R3: Validate explicit version-1 JSON policies, exact repository selection, allowed local
  commands, label meanings, rank bands, dispositions, finite budget and freshness defaults.
  Expose policy provenance. Policy is not a remote-action authorization or sandbox.
- R4: Fetch technical context on demand without changing the source index. Include
  observation time, indexed revision, head/base SHAs, indexed-head mismatch and a final
  head/base stability check. Fetch fork head content and base guidance correctly.
- R5: Bound pages and evidence strings; report missing patches/content/logs, stale-head
  jobs, access errors, thread/reply continuation and truncation without fabricating completeness.
- R6: Ship focused skills for policy ranking, PR review, CI diagnosis, issue reproduction/
  repair and portfolio follow-up alongside triage. Guard local writes and acknowledgements;
  external operations require corresponding caller delegation and outcome verification.
- R7: Evaluate clean installation and fresh-agent behavior, including read-only scope,
  authorized optional updates, source races, missing evidence and unchanged reruns.
  Run a read-only real GitHub ingestion/context pilot with disposable local decisions.

## Acceptance

Installed-wheel smoke and contract regressions pass; another agent can load the installed
skills and complete a bounded workflow from repository docs; a live source pilot exercises
ingestion, inspection, local decisions and resumption; release assets match the tested main
commit/version. Record partial/unevaluated cases explicitly. Shipping instructions alone
does not complete agent acceptance, and mocked data is not a live-source claim.

## Boundaries

GitHub ingestion remains read-only. Skills may use existing Git/gh for separately delegated
remote actions; no automatic merge/fix/publishing runner is introduced. CI tests require
no credentials. Enterprise hosts, exhaustive provider integrations, resumable huge backfills,
independent processing cursors and a local-action audit ledger remain later extensions.
