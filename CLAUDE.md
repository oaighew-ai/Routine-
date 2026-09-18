# CLAUDE.md

## EDGE OS

EDGE OS: CLV-first betting system. Phase: SHADOW.
At session start, confirm today's date and every source's age.
`spec/` wins over chat. Amendments live in `DECISIONS.md`. Graveyard ideas need new evidence.
No threshold without a derivation; otherwise tag it PRIOR (Law 6). Gate once (Law 5).
Recompute every aggregate from ledger rows; never increment a carried figure.
Logged decisions are frozen; never rewrite ledger history.
Never invent odds, results, injuries, or records. Missing data is a reason code, not a guess.
Automation writes only to the `capture-data` branch under `ledger/` (D3). `main` changes by pull request.
Scripts print summaries; never read raw snapshots into the conversation.

`spec/EDGE_OS_v2_DERIVED.md` is a reconstruction, not the specification (D1).
Treat its PRIOR-tagged rules as the weakest part of the system.

## This repository

Standard library only. `.github/workflows/tests.yml` fails the build on any
third-party import under `cfb_edge/`, denies sockets to the test suite, and
asserts that `find_plays` still prices off the market rather than a projection.
Do not add a dependency; do not weaken those checks.

```bash
python3 -m unittest discover -s tests     # the whole suite
```

Two unrelated things also live here: `index.html` (a daily planner) and `web/`
(static pages). Neither is part of EDGE OS.
