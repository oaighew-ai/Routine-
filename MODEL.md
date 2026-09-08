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
position is worth a few cents.

The numbers above came from the old priors and were too pessimistic. Refitting
the key numbers against 14,687 real games raised the mispricing on a
three-point margin from 0.94c to **3.7c**, roughly four times larger, so the
verdict has to be restated:

```
key-number mispricing on 3, fitted       3.70c
crossing two bid-ask spreads at 1c       2.00c
two fees on two mid-ladder legs          3.14c
                                        -------
net                                     -1.44c   (the old priors said -4.20c)
```

Still uneconomic, but by a cent and a half rather than by a mile, and an
earlier claim here that the fee alone exceeded the whole bucket's value is
simply wrong: breakeven costs are positive now, up to 2.94c on the three-point
bucket against about 4.2c of actual cost at the tick.

So the honest verdict is narrower than "dead on arrival". A taker crossing two
one-cent spreads cannot make it work. **A maker who posts both legs and never
crosses would be looking at a different sum**, and that is the one version of
this idea still worth testing. It is untested here.


## Where the constants came from

Everything below is fitted against 14,687 FBS-vs-FBS games from 2004 to 2024
(cfbfastR schedules), not assumed. Three of the numbers this model shipped with
were wrong, and one of them was wrong in a way that mattered.

**Home field was 2.2 and should be 3.2.** Regressing final margin on the Elo
gap with a home-field intercept gives a clear decline and a clear rebound:

| Era | HFA |
|---|---|
| 2004-2008 | 4.49 |
| 2009-2013 | 3.90 |
| 2014-2018 | 3.14 |
| 2019-2021 | 2.76 |
| 2022-2024 | 3.39 |

The trough is the empty-stadium seasons, and it rebounded. Reading the decline
as continuing to two, which is what this model did, was about a point and a
half too aggressive. A point and a half is most of a bet.

**Margin dispersion was right, nearly by accident.** The pooled spread of
margins is 21.1 points, and it is tempting to use that. It is the wrong number:
it mixes in how mismatched the games were. The per-game residual is **16.5**,
against the 16.0 this model already assumed. Using 21 would have flattened
every key number by roughly half.

**Key numbers were about half as strong as they should be.** Fitted against a
mixture of per-game normals centred on each game's Elo-implied margin:

| Margin | Prior | Fitted | Observed | Smooth curve says |
|---|---|---|---|---|
| 3 | 1.42 | **2.64** | 9.70% | 3.67% |
| 7 | 1.32 | **2.32** | 8.16% | 3.51% |
| 10 | 1.14 | 1.39 | 4.62% | 3.33% |
| 14 | 1.16 | 1.48 | 4.43% | 3.00% |
| 21 | 1.10 | 1.68 | 3.86% | 2.30% |

And the troughs, which the old table ignored entirely: a margin of **9** occurs
at 0.36x the smooth rate, **12** at 0.44x, **15** at 0.49x, **16** at 0.51x.
Football scores in threes and sevens, so the mass on the key numbers has to be
taken from somewhere. Modelling only the peaks and letting renormalisation
handle the troughs understates both ends.

Calibration after the refit, by predicted margin bucket:

| Predicted margin | Games | P(margin 3) observed | model |
|---|---|---|---|
| 0-2 | 1714 | 13.83% | 12.60% |
| 6-10 | 2843 | 10.80% | 11.34% |
| 16-24 | 2436 | 5.71% | 6.59% |
| whole sample | 14687 | 9.70% | 9.79% |

Zero of the 14,687 games ended level, which is the overtime rule showing up in
the data and confirms treating a margin of zero as impossible.

## Is the key-number structure drifting?

Worth asking, because a multiplier fitted across 2001-2025 is useless for 2026
if the underlying scoring has changed. Tested across five five-year eras with
the Elo scaling, home field and dispersion all refit *inside* each era, so a
change in scoring environment cannot show up as key-number drift.

| Margin | 01-05 | 06-10 | 11-15 | 16-20 | 21-25 | slope/yr | t |
|---|---|---|---|---|---|---|---|
| **3** | 2.77 | 2.49 | 2.63 | 2.60 | 2.76 | +0.003 | +0.41 |
| **7** | 2.02 | 2.08 | 2.46 | 2.48 | 2.39 | **+0.022** | **+2.46** |
| 10 | 1.38 | 1.33 | 1.39 | 1.38 | 1.38 | +0.001 | +0.68 |
| 14 | 1.21 | 1.57 | 1.61 | 1.50 | 1.38 | +0.005 | +0.47 |
| 21 | 1.73 | 1.57 | 1.59 | 1.84 | 1.58 | -0.001 | -0.12 |

**Three does not move.** Flat across a quarter century, and the most recent era
sits slightly *above* the full-sample fit. The number this model carries is not
going stale, and because the trend is nothing, the larger sample wins: chasing
the most recent era here would be fitting noise.

**Seven does move, and it is not marginal.** Splitting at the midpoint rather
than reading a slope off five points:

| Period | Multiplier | P(margin 7) | Games |
|---|---|---|---|
| 2001-2010 | 2.05 ± 0.09 | 7.12% | 6654 |
| 2011-2025 | 2.44 ± 0.08 | 8.59% | 10818 |

A difference of +0.39 against a standard error of 0.12, so **z = 3.25**. The
model carries 2.44, the modern figure, rather than the 2.32 the full sample
gives. It looks plateaued since 2011 rather than still climbing, so this is a
level shift to adopt, not a trend to extrapolate.

Why it moved is not established. The rising scoring of the 2010s, the growth in
two-point attempts, and the overtime format changes are all candidates and none
of them is tested here. Only the drift is.

The other numbers read differently across eras but none of them significantly:
14 and 21 both look lower in 2021-2025, and both trends are noise (t = 0.47 and
-0.12). They keep their full-sample fits, deliberately.

## Where the seven actually moved: regulation, not overtime

The natural suspect for a rising seven was the overtime rule. An overtime game
was tied after regulation, so its final margin *is* its overtime margin, and
those land on a field goal or a touchdown most of the time. Confirmed directly:

| Era | OT games | end on 3 | end on 7 | combined |
|---|---|---|---|---|
| 2004-2010 | 180 | 53% | 20% | 73% |
| 2011-2015 | 149 | 45% | 33% | 78% |
| 2016-2021 | 151 | 41% | 26% | 68% |

So overtime really is a key-number machine, and its mix really did shift from
threes toward sevens. But it is only about 4% of games, and the hypothesis
fails anyway.

Splitting 11,542 games by period number from play-by-play:

| Era | OT rate | P(margin 7) all | regulation only | OT only |
|---|---|---|---|---|
| 2004-2010 | 4.4% | 7.12% | **6.52%** | 20.0% |
| 2011-2015 | 4.3% | 8.54% | **7.44%** | 32.9% |
| 2016-2021 | 3.7% | 8.42% | **7.72%** | 26.5% |

Regulation-only games move on their own: 6.52% to 7.72%, z = +2.05, essentially
the same as the z = +2.19 with overtime included. Decomposing the full change:

| Component | Contribution | Share |
|---|---|---|
| Regulation scoring | +1.14pp | **88%** |
| Overtime composition | +0.24pp | 19% |
| Overtime frequency | -0.09pp | -7% |

**Overtime is not the mechanism.** It got less frequent over the period, which
pushed the seven down, and the regulation game moved enough to overwhelm that.
This matters for whether to trust the 2.44: a shift driven by how regulation
football is played is far more likely to persist than one driven by a tiebreak
rule that can be rewritten in an offseason.

The three tells the opposite story, and it is why its overall trend is flat.
Its small overall decline is entirely an overtime effect (composition -93%,
frequency +64% of a negative change) while regulation threes actually rose
slightly. The two forces cancel.

Two limits. Play-by-play in this source stops at 2021, so the overtime format
introduced that year, where the third overtime becomes two-point conversions,
is covered by one season and is not tested here. And 8 of 570 overtime games
show a regulation score that is not tied, about 1.4%, which is a data-quality
floor on all of the above rather than something the analysis can fix.

## The real backtest: 6,398 games against actual closing lines

Everything above this section was validated against a simulator. This section
replaces it with real closing lines from 20 seasons of sportsbook data, a median
of 14 books per game, 2006 to 2025.

**First, the market.** Over 7,818 games with a closing spread:

| | |
|---|---|
| mean error of the closing line | **-0.114 pts** (t = -0.66) |
| home team covers | 49.43% |
| favourite covers | 49.14% |
| closing line residual sd | **15.39** |
| Elo projection residual sd, same games | 16.33 |

The closing line is unbiased, and it is a full point of standard deviation
sharper than a public Elo rating. Nothing here was a surprise, but it had never
been checked.

**Then the model.** Walk-forward: for each week, ratings are fit only on games
already played that season, with the previous season's ratings regressed halfway
as the prior. Graded at -110.

| min edge | from week | bets | win% | ROI |
|---|---|---|---|---|
| 1.0 | 4 | 2785 | 48.87% | **-6.60%** |
| 1.5 | 4 | 1648 | 47.78% | **-8.65%** |
| 2.0 | 4 | 938 | 49.08% | **-6.22%** |
| 3.0 | 4 | 282 | 52.14% | -0.45% |
| 1.0 | 6 | 2386 | 48.47% | **-7.35%** |
| 3.0 | 6 | 262 | 52.69% | +0.59% |

Break-even is 52.38%. The two rows that reach it are the two smallest samples,
standard errors around 6%, selected as the best of eight configurations. That is
what noise looks like, not an edge.

**Then the test that actually settles it.** Regress what happened on both
projections at once. If the market's coefficient is one and the model's is zero,
the closing line already contains everything the model knows:

```
closing line   +1.0353   (se 0.0421, t = +24.6)
this model     -0.0192   (se 0.0627, t = -0.31)
residual sd     15.41
```

The implied optimal weight on the model is **-0.019**. Zero.

So `blend.MAX_MODEL_WEIGHT` now ships at **0.0**, and the model bets nothing.
An earlier version of this document called 0.45 conservative. Against real
closing lines it was 0.45 too high. The schedule and the machinery are kept, and
`DEMONSTRATED_EDGE_WEIGHT` exists for a model that has earned a vote, but this
one has not.

`encompassing.py` runs that test on any pair of projections. It is the right
first question to ask of any model, and it answers at a sample size one season
can supply, which a win-loss record cannot.

## H1: is line movement predictable? Yes. Is it enough? Almost.

The one hypothesis this repository had not tested was propagation: not whether a
model predicts games better than the market, which it does not, but whether it
predicts where the market is *going*. Those are different questions and the
second survives the first.

Tested on 5,298 games that carry both a consensus opening and closing spread.
Signal: back whichever side an Elo fair value prefers relative to the opening
number. Graded against the actual result at -110.

| min edge | books at open | bets | win% | ROI | CLV | CLV t |
|---|---|---|---|---|---|---|
| 0.0 | 1+ | 5298 | 51.22% | -2.18% | +0.29 | +4.4 |
| 2.0 | 1+ | 3558 | 51.72% | -1.24% | +0.43 | +4.6 |
| 6.0 | 1+ | 1231 | 50.12% | -4.25% | +0.83 | +3.2 |
| 2.0 | 2+ | 1766 | 53.55% | +2.19% | +0.35 | **+5.5** |
| 4.0 | 2+ | 1059 | 54.33% | +3.65% | +0.44 | +4.7 |

**The movement is predictable.** Closing line value is positive in every
configuration, with t-statistics from 3.2 to 5.5. That is not a marginal
result, and it is the first positive finding anywhere in this project.

**It is also not big enough.** A point of line is worth about 0.036 of win
probability near a pick'em, so -110 needs 0.67 points of CLV to break even.
The strategy earns 0.22 to 0.83 depending on selectivity, and the
configurations that clear the bar are the ones with the fewest bets.

| price | CLV needed | 0.44 achieved |
|---|---|---|
| -120 | 1.28 | no |
| -115 | 0.98 | no |
| **-110** | **0.67** | **no** |
| -108 | 0.54 | no |
| **-105** | **0.34** | **yes** |
| -103 | 0.21 | yes |

So H1 is alive, and it is a question about **where you bet, not whether the
signal is real**. Half a point of CLV is a losing strategy at -110 and a
winning one at -105. That makes reduced juice, and any venue whose cost sits
below about -106, the entire ballgame.

Two cautions. The realised win rates do not confirm the CLV: the most selective
single-book configuration earns the most CLV and still lost money over 1,231
bets, which at a standard error of 2.87% proves nothing either way and is
precisely why CLV is the scoreboard. And eight configurations were tried, so the
54.33% cell should be read as the noisiest number in the table rather than the
most impressive one.

`line_movement.py` ships the arithmetic: `clv_required(price)` for the bar and
`break_even_price(clv)` for its inverse.

## Which books actually clear the threshold

The CLV result needs a price better than about -106, so the venue question is
not a detail. Measured from 416,185 real two-sided spread quotes, 2006-2019:

| Book | Games | Median hold | Equivalent price | |
|---|---|---|---|---|
| MATCHBOOK | 8591 | 1.47% | **-103** | clears |
| 5Dimes & sportbet | 9644 | 2.44% | **-105** | clears |
| HERITAGE | 6619 | 2.44% | **-105** | clears |
| PINNACLE | 9408 | 2.88% | -106 | borderline |
| BETMANIA | 2423 | 3.38% | -107 | no |
| bet365, Bovada, BetCRIS, BetOnline, | | | | |
| JAZZ, JUSTBET, Intertops, SBR, +8 more | ~9000 each | 4.76% | -110 | no |

**Three books out of twenty-two.** Everything mainstream sits at -110 to the
cent, and the distribution is bimodal rather than continuous: books are either
at 4.76% or they are running a different business.

Two hard limits on that table. It stops in 2019, because **every spread row
from 2020 onward carries an empty odds column** in this source: the modern data
has lines but no prices. And of the three that cleared, one is an exchange
rather than a book and the other two are offshore, which is not a coincidence.
A book paying for US market access and television advertising does not price at
2.44%.

So the practical answer is uncomfortable. The venues that historically priced
where this edge needs them to be are largely not available to a US bettor
today, and the mainstream US market that replaced them prices at -110, where a
half point of CLV loses money.

`market.realized_hold` ships the measurement. Point it at any book's own quotes
and it reports the median hold and the equivalent symmetric price, because a
book advertising reduced juice may post it on marquee games and -110 everywhere
else, and the median is the price you actually meet. That distinction decides
whether this edge is a business or a slow loss, and it is not a claim worth
taking on trust from anyone, including a book's own marketing.

## Kalshi's tail prices versus a -105 book

Worth testing because the two costs have different shapes. A book charges a flat
vig; an exchange charges `0.07 x P x (1-P)`, which peaks at a coin flip and
falls away toward both ends. The obvious inference is that tail strikes are
cheap and therefore good.

That inference is wrong, and it is wrong for an instructive reason. A CLV edge
is denominated in points of line, and points convert to probability at the local
density of the margin distribution. The fee falls in the tails, but the density
falls too, so the same half point of line buys less probability out there. The
two effects nearly cancel.

| Strike | Price | Density | 0.44 pts buys | Exchange fee | Net |
|---|---|---|---|---|---|
| 0 | 50.0% | 0.0236 | 1.04% | 1.75% | -0.71% |
| **3** | 39.3% | 0.0646 | **2.84%** | 1.67% | **+1.17%** |
| **7** | 27.5% | 0.0552 | **2.43%** | 1.40% | **+1.03%** |
| 9 | 25.1% | 0.0076 | 0.34% | 1.32% | -0.98% |
| 20 | 10.0% | 0.0097 | 0.43% | 0.63% | -0.20% |
| 28 | 3.4% | 0.0089 | 0.39% | 0.23% | +0.16% |

(`venue.py` defaults to a 5-95% price band, which excludes the 28 row; widen
`price_bounds` to see the deepest strikes.)

Deep tails scrape by. Key numbers win by a mile, and they win at *every*
projected margin, not just in pick'ems.

**So the exchange's real advantage is not its fee. It is strike selection.** A
book sells the one line it has posted, which sits near the game's median. An
exchange sells the whole ladder, so the edge can always be expressed at three or
seven where the density is two and a half times higher.

That gives a clean rule, and it cuts both ways:

| Situation | Best venue | Net edge |
|---|---|---|
| Book's line lands on 3 or 7 | **the book**, at -105 or better | +1.43% |
| Book's line is an ordinary number | **the exchange**, at the 3 or 7 strike | +0.9% to +1.2% |
| Book's line is ordinary, only -110 available | neither | negative |

At -110 nothing works unless the posted line is already a key number. At -105 a
key-number line is the best bet on the board, better than any exchange strike,
because 1.22% of vig beats 1.75% of fee on the same proposition. Away from key
numbers the exchange is the only venue that clears at all.

`venue.py` ranks the options for a given projected margin and CLV.

One circularity to keep in view: the 0.44 points of CLV was measured on
sportsbook line movement. Whether it transfers to an exchange strike depends on
the exchange tracking the sportsbook, which is precisely the propagation
hypothesis that remains untested. This section says where the edge is worth
most **if** it transfers, not that it does.
