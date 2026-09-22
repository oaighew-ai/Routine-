# Routine-

Two unrelated things share this repository.

## `index.html` — Work Routine

A single-file daily planner. Open it in a browser; there is no build step.

## `cfb_edge/` — a college football betting model

A dependency-free model for college football spreads, with the measurement
apparatus to tell you whether it works. Standard library only, no API keys.

```bash
python3 -m unittest discover -s tests     # 172 tests
python3 -m cfb_edge play --slate data/example_play.csv --book-price -105
```

## `web/` — the pages

Static single-file pages, no build step.

- `week2-card.html` — the Week 2 2026 card: the plays the model found, how much
  of the closing-line-value budget the market had already spent before you could
  act, and what the rating-scale correction changed.
- `cfb-edge-net.html` — a fee-adjusted board for pricing an exchange strike by
  hand.

## Recording every signal, which is what makes the stop rule answerable

The stop rule needs about a hundred graded observations and a card produces two
bets a week, so on bets alone a dead strategy goes unnoticed for three seasons.
The signal fires far more often than the card does, and nothing has to be at
risk to measure closing line value:

```
python -m cfb_edge signals --slate data/week3_2026_slate.csv --opens data/opens.csv
python -m cfb_edge grade   --signals data/signals.csv --log data/opens.jsonl.gz
python -m cfb_edge clv     --bets data/signals.csv
```

`grade` takes the close out of the same append-only capture that gave the open,
so there is no second data source and no way for the two to disagree. A close is
only as late as the capture ran.

Paper rows carry a stake of zero and live in their own file, so they can never
be read as realised profit, and they carry no price, so only line CLV is
computed. That distinction matters: a paper signal measures the line and says
nothing about what it could have been filled at, which is exactly the open
question about whether the edge survives on an exchange.

## Logging bets, which is the only way to know if any of this works

The scorecard is closing line value, not win-loss record, and CLV needs the
price you actually got at the moment you got it. It cannot be reconstructed
later. A bet that is not logged never enters the measurement.

```
python -m cfb_edge log --game "Missouri @ Kansas" --side Kansas \
                       --strike 3 --cents 26 --stake 0.0037
```

`--strike 3` is the number the card prints: a contract paying if your side wins
by more than three. It is stored as *laying* three, because that is what it is,
and the command echoes back which it meant so a sign error is visible
immediately rather than at the end of the season.

Use `--line` and `--price` instead for a sportsbook bet, where the number is
already in that convention.

Then, once the market has closed:

```
python -m cfb_edge settle --game "Missouri @ Kansas" --closing-strike 4.5 \
                          --closing-cents 31 --closing-opposite-cents 71 \
                          --result win
python -m cfb_edge clv --bets data/bets.csv
```

Both closing prices are needed for price CLV, which devigs the two-way close.
Line CLV needs only the closing number.

## Capturing opening lines without being there

The edge is 0.44 points of closing line value measured against the *opening*
number, so the capture has to be running before books post. On Windows:

```
setx ODDS_API_KEY "your-key"                                  :: once, then a NEW terminal
powershell -ExecutionPolicy Bypass -File scripts\install_capture_task.ps1
```

That registers a weekly Sunday task that runs whether or not you are logged on,
restarts if it dies, and logs to `data\capture.log`. It starts six hours before
the release window rather than at it, because `watch.py` already polls hourly
when nothing is expected and every five minutes once anything opens: six hours
early costs six requests, and being late costs the week.


## `cfb_edge/engine/` and `cfb_edge/ledger/` — EDGE OS

The execution and validation layer: an append-only shadow ledger that records
every candidate with frozen inputs, its decision and its closing-line value.
Phase is SHADOW, so stakes are paper until Gate 2 clears.

```bash
python3 -m cfb_edge.edgeos --ledger ledger scan --dry-run   # decide, write nothing
python3 -m cfb_edge.edgeos --ledger ledger grade            # Gate 2 from rows
```

- `spec/BUILD_PROMPT.md` is the build's source of truth.
- `spec/EDGE_OS_v2_DERIVED.md` is a **reconstruction** of the real specification,
  which was never supplied. Read `DECISIONS.md` D1 before trusting any constant
  in it; the ones it invented are tagged PRIOR.
- `DECISIONS.md` is append-only and every entry carries a reversal criterion.
- `GRAVEYARD.md` holds what died. New evidence or nothing.

The decision is made on EV at the executable price net of venue fees, gated once
on `f_full > 0`, and sized at quarter Kelly with a correlation haircut, rounded
down. CLV is EV at the closing no-vig price, not a move in implied probability:
+1.5 points of the latter at a -110 entry is still -1.8% EV, which is why the
distinction gets its own amendment and its own test.

## Operating control plane

The repository now has a machine-readable operating-health layer in addition to
the decision engine:

```bash
python3 -m cfb_edge.ops_health \
  --capture-report capture-data/data/capture-report.json \
  --out /tmp/ops-health.json --stage manual
```

`.github/workflows/cfb-operating-review.yml` materializes that contract on the
Monday review, Tuesday opening board, Thursday refresh, Friday pre-final and
Saturday final-board cadence. It resolves those windows in
`America/New_York`, so the season's DST change does not silently move a review
by an hour.

`config/model_implementation_registry.json` separates model identity from
implementation provenance. In particular, S02 is explicitly recorded as an
external private-Site implementation: this repository must not reconstruct it
from a model name or validation snapshot.

S04 is a quarantined football-context challenger. Its harness is intentionally
market-residual and walk-forward:

```bash
python3 -m cfb_edge.challenger \
  --data historical_features.csv \
  --config config/s04_challenger.json \
  --out /tmp/s04-report.json
```

A historical S04 report can reject the challenger. It cannot promote it or alter
the card; prospective shadow validation is required.

The Bayesian/regime architecture is also implemented as a rejection-first
shadow harness. It keeps the opening market as the prior, uses Week 1 to fit,
Week 2 to select registered hyperparameters, and Week 3 as an untouched
holdout:

```bash
python3 -m cfb_edge.bayesian_regime \
  --learning-report data/early-season-learning.json \
  --out /tmp/s04-br1.json
```

The preserved 2026 Week 3 result rejects every tested Bayesian outcome-residual,
fixed-regime, dynamic-team-state and ensemble variant versus the market
baseline. `data/s04-br1-oos-2026.json` records that negative result. The
richer EPA/QB/weather feature set remains disabled until point-in-time history
exists.

## One source of truth for picks

The private Site's [`/api/picks`](https://cfb-edge-research.oaighew.chatgpt.site/api/picks)
is the only live delivery contract. Its dashboard reads that same endpoint.
The Site owns immutable S02 captures, validation and saved cards in D1/R2.
GitHub's former `capture-data/data/picks.json` is a legacy, non-authoritative
snapshot and must not be used for decisions. The GitHub dashboard links to the
private Site and does not display a competing pick feed. The dashboard,
raw capture exports, shop scans, preview candidates, historical cards and
shadow models are diagnostic inputs. None of them is an alternate pick feed.

The local diagnostic is built by `python3 -m cfb_edge.source_of_truth` and is
marked `CFB_EDGE_DIAGNOSTIC_V1`, `authoritative: false`. It fails closed
unless the current capture is fresh, the registered validation is complete and
replay-verified, every evidence gate recomputes as passed, and the candidate's
S02 version, model SHA-256 and protocol match exactly. It then emits at most one
paper action per game and market and at most five actions total. Any conflict,
missing provenance, stale quote or `NO_EVIDENCE` row produces `NO_BET`.

```bash
python3 -m cfb_edge.source_of_truth \
  --authority config/delivery_authority.json \
  --registry config/model_registry.json \
  --capture-report data/capture-report.json \
  --candidates ledger/candidates.jsonl \
  --previous data/picks-diagnostic.json \
  --out data/picks-diagnostic.json
```

`config/model_registry.json` owns model roles. S02 is the sole possible paper
delivery candidate. S01 is quarantined, S03 is input-integrity only, S04 is
unfitted, S05 is monitor-only, and F03 plus ROUTINE_SHOP cannot deliver.
Delivery is paper research only, records zero actual exposure, and never places
a wager.

Full documentation, including every result and every result that did not
survive, is in [MODEL.md](MODEL.md).

**The short version.** The model cannot predict games better than the closing
line, and it is not close: tested against 6,398 real closing lines its
incremental coefficient is -0.02 with a t of -0.31. It can predict where the
line is *going*, which is a different and easier problem, earning 0.22 to 0.83
points of closing line value with t-statistics from 3.2 to 5.5. That edge is
about half of what -110 demands, so it only pays at key numbers, where three and
seven carry roughly two and a half times the mass of an ordinary margin, and
only at a venue costing less than about -106.

So the model bets nothing by default, and the one strategy it does support fires
on a small fraction of the board. That is the finding, not a limitation of the
implementation.
