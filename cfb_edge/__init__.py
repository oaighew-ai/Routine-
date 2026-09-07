"""A college football betting edge model.

The design goal is not to predict games better than the market. It is to find
the small number of games where a disciplined power rating disagrees with the
market by enough to survive vig, and to size those bets so a bad month does not
end the season.

Everything here is built around three convictions:

1.  The closing line is the best public predictor of a college football game.
    A model that ignores it is not a model, it is an opinion. So the projection
    is always blended toward the market, and the blend weight starts near zero
    in September when the ratings know almost nothing.

2.  College football margins are not normally distributed. They pile up on 3,
    7, 10, 14 and 21. Converting a spread edge into a cover probability with a
    plain normal CDF misprices every number near a key number, which is where
    most bets live. `distribution.py` uses a discrete margin PMF instead.

3.  Win-loss record over a week, or a month, is noise. Closing line value is
    the scorecard. `clv.py` exists so the model can be judged before the
    sample size is large enough to judge it by profit.
"""

__version__ = "1.0.0"
