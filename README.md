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
