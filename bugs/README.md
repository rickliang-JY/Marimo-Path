# Bug review log

One file per review round, recording what was found, what the evidence was,
and what happened to it. The point is that a later round can tell at a glance
which findings are settled and which are still open — and can re-check the
regressions a previous round claimed to have fixed.

## Files

| Round | Date | Findings | Status |
| --- | --- | --- | --- |
| [round-01](2026-08-09-round-01.md) | 2026-08-09 | 6 | in progress |

## Conventions

- **Filename**: `YYYY-MM-DD-round-NN.md`.
- **Branch**: each round gets its own branch, `bugs/YYYY-MM-DD-round-NN`, so
  the findings are recorded before any fix lands and the two are reviewable
  apart.
- **One entry per finding**, numbered within the round and referenced
  elsewhere as `R<round>-<n>` (e.g. `R01-1`). Numbers are never reused.
- **Every finding carries reproducible evidence** — a snippet that can be run
  and its actual output, not a description of what would happen. A claim
  without evidence goes in "Observations", not "Findings".
- **Severity**
  - `high` — silently wrong data, or a documented contract broken.
  - `medium` — a real failure users will hit, but visible when it happens.
  - `low` — latent; needs an uncommon configuration or input to trigger.
- **Status**: `open` → `fixed` (with the commit and the regression test that
  pins it) or `wontfix` (with the reason).
- **Observations** collect things that are not bugs but are worth knowing:
  performance smells, cosmetic glitches, deliberate trade-offs. They are not
  numbered and carry no status.
- Also record **what was checked and found clean**. A review that only lists
  problems does not tell the next round where to stop looking.
