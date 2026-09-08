# College football edge model

A spread-betting model that is built to disagree with the market rarely, and to
say so out loud when it has nothing.

```bash
python3 -m cfb_edge rate --games data/example_games.csv --priors data/example_priors.csv
python3 -m cfb_edge card --games data/example_games.csv --priors data/example_priors.csv \
                         --slate data/example_slate.csv --show-all
python3 -m cfb_edge clv  --bets data/example_bets.csv
python3 -m cfb_edge.backtest
python3 -m unittest discover -s tests
```

No dependencies beyond the Python standard library. No API keys. Data goes in
and out as CSV, so a week's card can be committed and audited later against
what actually happened.

## The result you should read first

`python3 -m cfb_edge.backtest` simulates a league with known true ratings and
asks how accurate the market has to be before the model stops making money.

| Weeks played | Market error | Bets | Win rate | ROI |
|---|---|---|---|---|
| 1 | any | 0 | n/a | n/a |
| 4 | 1.0 pts | 1681 | 52.12% | -0.52% |
| 4 | 3.0 pts | 1894 | 56.99% | +9.77% |
| 10 | 1.0 pts | 2396 | 49.15% | -5.53% |
| 10 | 2.0 pts | 2463 | 52.44% | +0.60% |
| 10 | 3.0 pts | 2622 | 55.24% | +6.33% |

The break-even win rate at -110 is 52.38%. Against a market that prices games
within a point of the truth, this model does not clear it. It needs the market
to be two to three points wrong.

That is the whole strategy, and it is a constraint rather than a disappointment.
A major book's closing line on a televised game is close to the one-point
column, so pointing this model at the marquee matchup is a way to lose money
slowly. The two-and-three-point columns exist early in the week, on games with
no television audience, and at books that copy their numbers late. That is where
to point it.

## Why the pieces are built the way they are

**The model only gets a minority vote.** The projection that gets bet is a
weighted average of the model's fair line and the market's line, and the weight
starts near 0.09 and never exceeds 0.45. In week two a ten-point disagreement
with the market produces less than a point of edge and does not clear the
betting threshold. This is deliberate. The closing line is a better forecast
than a power rating built on one game, and most models like this lose money
because their author could not stand looking at an empty card and turned the
weight up.

**Margins are not normal.** College football games end on 3 and 7 far more often
than a smooth curve says, and they never end on 0, because a regulation tie goes
to overtime. `distribution.py` uses an explicit distribution over integer
margins, so a half point from -3.0 to -2.5 correctly prices as roughly twice the
value of -8.5 to -9.0, and a pick'em correctly cannot push. A model using a
normal CDF misprices every number near a key number, which is where most bets
sit.

**Ratings are shrunk hard and margins are capped.** A team that beat an FCS
opponent by 50 in week one is not a title contender. Every rating is pulled
toward a preseason prior worth four games, and margins are capped at 28 so one
blowout cannot drag a rating for two months. Preseason priors are best taken
from market win totals, which is the market's opinion and better than a poll.

**Sizing is a quarter of Kelly with hard caps.** Kelly is optimal for a known
edge. This edge is estimated, and betting full Kelly on an edge overestimated by
half puts you past the point where growth turns negative. Simulated growth peaks
at 1.0x Kelly and hits zero at 2.0x; the tests pin both. Single bets cap at 2% of
bankroll and a week's card at 10%, because Saturdays are correlated by weather,
by conference, and by whatever the model has got systematically wrong.

**Pushes are handled properly.** The closed-form Kelly formula assumes no
pushes, which is wrong for whole-number spreads. The optimum is solved
numerically instead.

**The scorecard is closing line value, not record.** A four-bet week at a true
55% edge loses money about a third of the time, so a week's record says nothing.
`clv.py` measures whether you consistently got a better number than the market
settled on, which is a question a small sample can actually answer. It reports
CLV in win probability as well as points, because a point of CLV across the 3 is
worth far more than a point of CLV across the 12, and points alone do not add up.

## What it deliberately does not do

- **No totals projection.** Predicting points scored needs pace and efficiency
  data that adjusted margin does not contain. The market total is used only to
  set how variable a game is likely to be. A total projected from margin ratings
  would be a guess with a decimal point on it.
- **No games involving an unrated team.** Almost always an FCS opponent, where a
  large model-market disagreement means the model is wrong.
- **No injury, weather, or lookahead adjustments.** All real, none modelled. The
  model does not know your quarterback is out, so do not bet a card without
  checking.
- **No live odds fetching.** CSV in, CSV out, on purpose.

## Calibration status

The constants in `distribution.py` and `ratings.py` are priors with the right
shape and roughly the right size for FBS football. They have not been fit to
real results, because this repository ships no historical data.

`distribution.fit_key_bumps` refits the key-number multipliers from real
margins, and refuses samples under 500 games. Refitting it against several real
seasons is the single highest-value change available, and until that is done the
probabilities are directionally right rather than calibrated.

## Kalshi fees, for the cross-market engine

`cfb_edge/kalshi_fees.py` exists for a different system: a gate that compares a
sportsbook's de-vigged fair value against a Kalshi fill. It is here because that
comparison has a trap in it.

Kalshi charges `round_up(0.07 * C * P * (1 - P))`. The `P * (1 - P)` term peaks
at 50c, so the fee is largest exactly on coin-flip markets and smallest in the
tails:

| Price | Fee per contract | Gross edge needed for +1c net |
|---|---|---|
| 10c | 0.63c | 1.63c |
| 21c | 1.16c | 2.16c |
| 47c | 1.74c | 2.74c |
| 50c | 1.75c | 2.75c |
| 84c | 0.94c | 1.94c |

A cross-market gate finds most of its apparent edges near 50c, because that is
where a book posting -110 both ways de-vigs to exactly 50 and where the
comparison is easiest to make. Those are the same markets where the fee is at
its maximum. So the edges the gate finds most often are the ones the fee eats
most of.

The consequence for an entry rule: **a flat bar in cents is the wrong shape.**
Clearing one cent of net edge takes 2.75c of gross edge at a coin flip and
2.16c at 21c. A flat 3c bar passes both, but delivers 1.25c of real edge in one
case and 1.84c in the other, and a 1c gross edge near 50c is outright negative.
`required_gross_edge_cents` returns the bar that actually holds net edge
constant.

Fee schedules vary by product and maker orders price differently, so read the
real coefficient off the account before staking anything.

## Pulling a week's board

```bash
python3 -m cfb_edge.board --week 2 --book lines.csv --show-all
python3 -m cfb_edge.board --week 2 --json week2.json   # then paste into the page
python3 -m cfb_edge.board --offline tests/fixtures/board.json \
                          --book tests/fixtures/lines.csv --show-all
```

Kalshi market data needs no key, so the Kalshi leg runs anywhere the network
allows `api.elections.kalshi.com`. Where an egress policy blocks that host the
run exits with a message naming it, because an empty board and a blocked board
mean opposite things and must never look alike.

Two details in `providers/kalshi.py` are worth knowing before trusting a price.

Kalshi publishes two arrays of **bids**, one for YES and one for NO, and neither
is an ask. A NO bid at q is a YES offer at `100 - q`. Reading the YES array as
an offer book reports a price better than anything fillable, which surfaces
downstream as a large edge that does not exist.

And a midpoint is not a fill. Every entry price is a VWAP over the resting book
for the size you actually want, and a book that cannot fill that size returns
nothing rather than extrapolating.

`--book` takes a CSV of `ticker,fair_cents` from whatever de-vigs your
sportsbook prices. Without it every fair value falls back to an assumed 50c,
which the gate rejects by default; `--allow-assumed` shows them, marked.

## Measuring whether any of it is real

Two modules exist for the promotion decision, not the betting decision.

`clv_extract.py` rebuilds closing line value from a raw capture series. It
measures each entry at several offsets before kickoff as well as at the close,
because an edge that decays in five minutes and one that holds to kickoff imply
opposite things about how fast you have to act. It also separates markets that
moved from markets that never did: a log dominated by flat contracts reports a
mean CLV near zero and a beat rate near zero, and neither figure says anything
about skill, because the market never gave an opinion.

`bootstrap.py` is the one that changes conclusions. Contracts on the same game
share that game's news, weather and officiating, so their errors are correlated
and they are not independent observations. Resampling contracts instead of
games understates the standard error badly. On a board shaped like a real one,
67 games and about 13 contracts each:

```
naive       +0.303c [+0.187, +0.419]   871 "independent" observations -> PROMOTE
clustered   +0.303c [-0.091, +0.716]   67 games                       -> hold
```

Same data, same point estimate. The naive interval is **3.5x too narrow**, and
it promotes a signal the honest interval cannot distinguish from zero.

The planning consequence is harsher than the inference one. At the per-game
dispersion in that sample, roughly **500 games** are needed before a true
+0.15c edge shows a lower bound above zero, and that assumes the edge is real
and constant. A capture running 67 games a week reaches that around the end of
a season. Any promotion rule counting contracts rather than games is set at a
small fraction of its intended bar.

### Running the promotion check

```bash
python3 -m cfb_edge.measure --runs "data/raw/*.jsonl.gz" --entries entries.csv
```

Rebuilds CLV from the capture and reports the interval both ways, so the size
of the clustering correction is measured rather than assumed. How much it
matters depends entirely on the shared game effect in your own data:

| Shared game effect | Interval widens | Verdict flips | Games needed |
|---|---|---|---|
| 0.0c | 0.95x | no | 3 |
| 0.2c | 1.65x | no | 12 |
| 0.5c | 2.61x | no | 53 |
| 0.8c | 2.96x | no | 129 |
| 1.2c | 3.27x | **yes** | 283 |
| 1.6c | 3.36x | **yes** | 498 |
| 2.4c | 3.41x | **yes** | 1111 |

Below roughly 0.2c the correction is negligible and nothing changes. Above
roughly 0.8c it decides promotions. The figure quoted earlier in this document
assumed 1.6c, which was a guess; the command above replaces it with the number
your capture actually contains.

A mismatched `--price-field` returns nothing rather than something wrong, and
entries with no game label are reported rather than silently treated as
independent, since that would remove the correction without saying so.

## Strike ladders, and a structural result about Kalshi

A Kalshi spread market is `P(margin > N)`, so a game's ladder is a picture of
the market's whole margin distribution. `ladder.py` reads one as a single
object. Two ideas motivated it, and only one survived.

**Coherence still holds.** `{margin > 7.5}` is a subset of `{margin > 2.5}`, so
the higher strike can never be worth more. When tradeable prices invert, buying
the low strike and selling the high one pays at least 100 in every outcome.
`find_arbitrage` looks for those on bid and ask rather than midpoints, and
prices them net of both legs' fees. The bar is higher than it looks:

| Strikes near | Inversion needed to clear fees |
|---|---|
| 20c | 2.24c |
| 30c | 2.94c |
| 50c | 3.50c |
| 80c | 2.24c |

A one-cent inversion is not an opportunity. A three-cent inversion at a coin
flip is still not one.

**The key-number idea does not survive, and the reason generalises.** Half-point
strikes a point apart isolate a single margin, so a ladder states what the
market thinks the chance of a three-point game is. A smoothly priced ladder
underprices it badly, and the detector finds that cleanly: ratio 0.70 on a
margin of 3, 0.79 on 7, and no false positives against a correctly priced
control.

It is untradeable anyway. Harvesting one margin means a vertical spread, and:

```
key-number mispricing on 3 worth       0.94c
crossing two bid-ask spreads at 1c     2.00c
two fees on two mid-ladder legs        3.14c
                                      -------
net                                   -4.20c
```

**Kalshi charges each leg on that leg's own notional.** A spread between two
65-cent strikes pays fees as though you traded two 65-cent contracts, while the
position is worth about three cents. For margins of 7, 10 and 14 the fee alone
exceeds the entire fair value, so the breakeven cost is *negative*: no bid-ask
spread, however tight, makes it work.

That kills the idea for a reason that has nothing to do with football, and it
generalises past this one strategy. **Any Kalshi position built by differencing
two mid-ladder strikes is dead on arrival.** Only trades whose fee is
proportional to the exposure you actually want survive, which is why a single
outright in the tail behaves so differently from a spread in the middle.
