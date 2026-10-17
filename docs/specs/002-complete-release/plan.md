# Plan: complete maintenance release

See [specification](spec.md) and [tasks](tasks.md). This extends feature 001 rather than
rewriting its historical evaluation evidence.

1. Add small policy and skill-resource modules. Use existing Pydantic/Click validation;
   keep policy/installation commands independent of database initialization. Optional
   write guards resolve the item's repository through existing public query services.
2. Add one on-demand source adapter for authenticated `gh api` calls. Keep synchronous
   transport on a worker thread, outside SQLite transactions. Reuse the indexed item
   identity; return a typed evidence packet, not speculative new persisted tables.
3. Package repository-owned `skills/` into the wheel through Hatch's existing force-include
   support. Generate a separate ZIP and checksums with standard-library release tooling.
4. Run installed-artifact checks outside the checkout with a fresh environment and Python
   isolated mode. Include the independent legacy SQL fixture and verify retained notes.
5. Build five focused workflows over triage's operating contracts. Use policy and technical
   context only when needed; keep each installed directory self-contained. Skills use
   existing Git/gh for checkout operations and any separately delegated remote writes.
6. Validate adapter/policy contracts with token-free fakes, then fresh-agent triage and
   technical walkthroughs from installed skills, plus a disposable real-source pilot.
   Preserve transcripts and distinguish limited review scope from a full PR review.
7. Align docs/decisions/checklists and publish versioned wheel/source/skills/checksums
   only after tests, builds, installed checks and main CI pass.

Application additions are `config.py`, `context.py`, `skillpack.py`, and existing CLI
registration/lifecycle integration. No dependency or migration additions are planned.
The six existing workstreams are the completion scope; not every conceivable maintenance
provider, portfolio scale or agent host is required to finish this release.
