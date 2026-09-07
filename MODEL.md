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
