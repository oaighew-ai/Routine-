# Routine-

Two unrelated things share this repository.

## `index.html` — Work Routine

A single-file daily planner. Open it in a browser; there is no build step.

## `cfb_edge/` — a college football betting model

A dependency-free model for college football spreads, with the measurement
apparatus to tell you whether it works. Standard library only, no API keys.

```bash
python3 -m unittest discover -s tests     # 141 tests
python3 -m cfb_edge play --slate data/example_play.csv --book-price -105
```

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
