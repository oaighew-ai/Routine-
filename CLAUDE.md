# CLAUDE.md

## EDGE OS

EDGE OS: CLV-first betting system. Phase: SHADOW.
At session start, confirm today's date and every source's age.
`spec/` wins over chat. Amendments live in `DECISIONS.md`. Graveyard ideas need new evidence.
No threshold without a derivation; otherwise tag it PRIOR (Law 6). Gate once (Law 5).
Recompute every aggregate from ledger rows; never increment a carried figure.
Logged decisions are frozen; never rewrite ledger history.
Never invent odds, results, injuries, or records. Missing data is a reason code, not a guess.
Automation writes only to the `capture-data` branch (D3). Today everything it writes is under `data/`; `ledger/` does not exist there yet. `main` changes by pull request.
Scripts print summaries; never read raw snapshots into the conversation.

`spec/EDGE_OS_v2_DERIVED.md` is a reconstruction, not the specification (D1).
Treat its PRIOR-tagged rules as the weakest part of the system.

## One operating path (D39)

One weekly card, from one engine, under one authority. Do not add a second
card, pick feed or "production" path. The runbook is
`docs/CFB_EDGE_OPERATIONS.md`: what runs when, what is frozen, what to do
when something stops, and the known gaps.

```bash
scripts/build_weekly_card.sh <capture-data checkout> <out dir> [AS_OF]
```

- The card is `cfb_edge/weekly_card.py` (`CFB_EDGE_WEEKLY_CARD_V1`). D40 says
  what its dispositions and numbers mean.
- BET needs `config/delivery_authority.json` to allow delivery. It does not,
  so the card cannot print BET. No automation edits that file.
- A challenger stays shadow, at zero stake, until it is promoted under D39.
- Cohorts are registered contracts under `config/cohorts/`, picked by date
  (D41). A registered contract is never rewritten.
- Opening capture is D38. Only `true_open` and `fill` rows grade.
- An exchange market belongs to a game only when its ticker is dated on that
  game's day (D43). Never match a market to a fixture on a team name alone.
- Closes come from the capture loop, which polls a game from twenty minutes
  before kickoff (D44).

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
