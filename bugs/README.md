# Bug review log

One file per review round, recording what was found, what the evidence was,
and what happened to it. The point is that a later round can tell at a glance
which findings are settled and which are still open — and can re-check the
regressions a previous round claimed to have fixed.

## Files

**Start here: [SUMMARY.md](SUMMARY.md)** — all six rounds by area and severity,
the bug classes that recurred, and what is still open.

| Round | Date | Findings | Status |
| --- | --- | --- | --- |
| [round-01](2026-08-09-round-01.md) | 2026-08-09 | 7 | fixed |
| [round-02](2026-08-09-round-02.md) | 2026-08-09 | 2 | fixed (+1 wontfix) |
| [round-03](2026-08-09-round-03.md) | 2026-08-09 | 4 | fixed |
| [round-04](2026-08-09-round-04.md) | 2026-08-09 | 6 | fixed |
| [round-05](2026-08-09-round-05.md) | 2026-08-09 | 7 | fixed — **record reconstructed 2026-08-10**, see its header for what is and is not recoverable |
| [round-06](2026-08-09-round-06.md) | 2026-08-09 | 5 | fixed (+2 refuted) |

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
- Record **corrections in place**, as a marked note rather than a silent edit,
  whenever a finding's evidence or scope turns out to be wrong. Round 01 had
  one of each: evidence built from inputs the code cannot receive, and a
  deprecation found at one call site out of three.
- **Line numbers are as-of the round that wrote them.** These rounds all land in
  one uncommitted working tree, so a later round editing the same file shifts
  every number above its edit (round 06 moved round 04's `_on_run_heatmap` by
  87 lines). Cite the function or the cell name as the durable anchor and treat
  the number as a hint. The same applies to `hescope/` modules.
- **Say which invocation a regression test needs.** Two of round 01's fixes are
  pinned only by `-W "error::FutureWarning"` and pass under a plain `pytest -q`
  with the bug reintroduced. A record that says "regression coverage: the
  existing suite" without naming the invocation overstates the guard.
- **Do not cite a finding number that no record defines.** Round 06 dismissed a
  live doc gap as "the already-recorded R02-4"; no round ever wrote an R02-4, so
  the gap went unrecorded for two rounds.

## Start every round with

```bash
pytest -q -W "error::FutureWarning"   # deprecations the toolchain already knows about
python app.py                         # every notebook cell, once
```

Reading for a pattern misses call sites; the first command found three of round
01's issues that a careful read had not.
