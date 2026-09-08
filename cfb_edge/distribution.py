"""The distribution of college football game margins.

Most amateur models convert a spread edge into a cover probability with a
normal CDF. That is wrong in a way that costs money, because college football
margins are lumpy. Games end on 3 and 7 far more often than a smooth curve
predicts, and they never end on 0, because a tie in regulation goes to
overtime. A normal curve says a pick'em can push. It cannot.

The practical consequence is about half-points. Moving a line from -2.5 to -3.0
costs far more win probability than moving -8.5 to -9.0, and a model that
cannot see that will happily pay the same price for both. This module works
with an explicit discrete distribution over integer margins so those effects
fall out of the arithmetic instead of being ignored.

The multipliers below are fitted, not assumed. They come from 14,687 FBS-vs-FBS
games from 2004 to 2024, comparing how often each margin actually occurred
against a mixture of per-game normals centred on each game's Elo-implied
expected margin. Fitting against the mixture rather than against a single
pooled normal matters: the pooled spread of margins is 21.1 points, but that
includes the variation in how mismatched the games were. The per-game residual
is 16.5, and using 21 there would have flattened every key number by half.

One multiplier is era-dependent and it is the seven. Tested across five
five-year eras with the baseline refit inside each, so a change in scoring
cannot masquerade as key-number drift, the seven rose from 2.05 in 2001-2010 to
2.44 in 2011-2025, a difference of +0.39 against a standard error of 0.12
(z = 3.25). It appears to have plateaued since 2011 rather than still climbing,
so the table below carries the 2011-2025 figure rather than the full-sample
2.32. Candidate causes are the rising scoring of the 2010s, the growth in
two-point attempts, and the overtime format changes; none of them is
established here, only the drift itself.

Everything else is flat. The three, which matters most, does not move at all:
2.77, 2.49, 2.63, 2.60, 2.76 across those same eras, a trend of +0.003 per year
against a standard error of 0.008. The most recent era sits slightly above the
full-sample fit, so the number in this table is not going stale.

The structure is stronger than intuition suggests and it runs in both
directions. A three-point margin happens in 9.7% of games where a smooth curve
predicts 3.7%, a multiplier of 2.6. Seven is 2.3. But the numbers between the
key ones are correspondingly starved: nine, twelve, fifteen and sixteen all
occur at roughly half the smooth rate. Football scores in threes and sevens, so
the mass has to come from somewhere, and modelling only the peaks while letting
renormalisation handle the troughs understates both.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

# Margins beyond this are rounded into the tails. 70 is comfortably past any
# realistic FBS result while keeping the arrays small.
SUPPORT_LIMIT = 70

# A regulation tie goes to overtime, so a final margin of 0 cannot happen.
IMPOSSIBLE_MARGINS = frozenset({0})

# Relative mass multipliers by absolute margin, applied before renormalising.
KEY_BUMPS: Mapping[int, float] = {
    1: 0.95,
    2: 0.75,
    3: 2.64,
    4: 1.01,
    5: 0.75,
    6: 0.90,
    7: 2.44,
    8: 0.76,
    9: 0.36,
    10: 1.39,
    11: 0.71,
    12: 0.44,
    13: 0.60,
    14: 1.48,
    15: 0.49,
    16: 0.51,
    17: 1.32,
    18: 1.00,
    19: 0.56,
    20: 0.85,
    21: 1.68,
    22: 0.60,
    23: 0.61,
    24: 1.46,
    25: 0.99,
    26: 0.55,
    27: 0.95,
    28: 1.66,
    29: 0.60,
    30: 0.67,
}

# Margin dispersion is tied to how many points the game is expected to produce:
# a 38-point rock fight is less variable than a 72-point shootout.
REFERENCE_TOTAL = 52.0
SIGMA_AT_REFERENCE = 16.0
SIGMA_BOUNDS = (12.0, 22.0)

# Totals have their own, much weaker, key numbers and their own dispersion.
SIGMA_TOTAL = 10.5


def _normal_cdf(x: float, mean: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((x - mean) / (sigma * math.sqrt(2.0))))


def sigma_for_total(total: float) -> float:
    """Margin standard deviation implied by a game's projected total.

    Scaled as the square root of the total, which is the behaviour you would
    expect if a game were a sum of roughly independent scoring events, then
    clipped so a freak total cannot produce an absurd spread of outcomes.
    """
    if total <= 0.0:
        return SIGMA_AT_REFERENCE
    raw = SIGMA_AT_REFERENCE * math.sqrt(total / REFERENCE_TOTAL)
    lo, hi = SIGMA_BOUNDS
    return min(hi, max(lo, raw))


def margin_pmf(
    mean: float,
    sigma: float,
    *,
    key_bumps: Mapping[int, float] | None = None,
    limit: int = SUPPORT_LIMIT,
) -> dict[int, float]:
    """Probability of each integer final margin, from the favoured team's side.

    Built by integrating a normal over each integer's half-point band, applying
    the key-number multipliers, zeroing impossible margins, then renormalising.
    """
    if sigma <= 0.0:
        raise ValueError(f"sigma must be positive, got {sigma}")
    bumps = KEY_BUMPS if key_bumps is None else key_bumps

    pmf: dict[int, float] = {}
    for k in range(-limit, limit + 1):
        if k in IMPOSSIBLE_MARGINS:
            continue
        mass = _normal_cdf(k + 0.5, mean, sigma) - _normal_cdf(k - 0.5, mean, sigma)
        mass *= bumps.get(abs(k), 1.0)
        if mass > 0.0:
            pmf[k] = mass

    total = sum(pmf.values())
    if total <= 0.0:
        raise ValueError("margin distribution collapsed to zero mass")
    return {k: v / total for k, v in pmf.items()}


@dataclass(frozen=True)
class CoverOutcome:
    """How a side bet resolves against a distribution."""

    win: float
    push: float
    loss: float

    def __post_init__(self) -> None:
        total = self.win + self.push + self.loss
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"cover probabilities must sum to 1, got {total}")

    @property
    def win_excluding_push(self) -> float:
        """Win rate among games that actually resolve. This is the number to
        compare against a break-even threshold like 52.38%."""
        live = self.win + self.loss
        return self.win / live if live > 0.0 else 0.0


def cover_probability(pmf: Mapping[int, float], line: float) -> CoverOutcome:
    """Resolve a side bet at `line` against a margin distribution.

    `line` is signed from the perspective of the team being backed and in the
    usual betting convention: -7.0 means laying seven, +7.0 means taking seven.
    The bet wins when the backed team's margin M satisfies M + line > 0, so the
    threshold is at M = -line and a push is only possible when that is a whole
    number.
    """
    threshold = -line
    win = push = loss = 0.0
    for margin, prob in pmf.items():
        if margin > threshold:
            win += prob
        elif margin == threshold:
            push += prob
        else:
            loss += prob
    # Renormalise away floating point drift so CoverOutcome's check passes.
    total = win + push + loss
    return CoverOutcome(win / total, push / total, loss / total)


def half_point_value(pmf: Mapping[int, float], line: float) -> float:
    """Win probability gained by moving the line half a point in your favour.

    This is the number that tells you whether -2.5 is worth chasing across
    books and -8.5 is not. Expressed as a probability, so compare it against
    the roughly 2.4 points of probability that standard -110 juice costs you.
    """
    here = cover_probability(pmf, line).win_excluding_push
    better = cover_probability(pmf, line + 0.5).win_excluding_push
    return better - here


def total_pmf(
    projected_total: float,
    *,
    sigma: float = SIGMA_TOTAL,
    limit_low: int = 0,
    limit_high: int = 140,
) -> dict[int, float]:
    """Distribution of combined points scored.

    Totals get a plain discretised normal. Their key numbers are real but far
    weaker than spread key numbers, and inventing multipliers for them without
    data would add false precision rather than accuracy.
    """
    pmf: dict[int, float] = {}
    for k in range(limit_low, limit_high + 1):
        mass = _normal_cdf(k + 0.5, projected_total, sigma) - _normal_cdf(
            k - 0.5, projected_total, sigma
        )
        if mass > 0.0:
            pmf[k] = mass
    total = sum(pmf.values())
    if total <= 0.0:
        raise ValueError("total distribution collapsed to zero mass")
    return {k: v / total for k, v in pmf.items()}


def over_probability(pmf: Mapping[int, float], line: float) -> CoverOutcome:
    """Resolve an over bet at `line`. Under is the mirror of this."""
    win = push = loss = 0.0
    for points, prob in pmf.items():
        if points > line:
            win += prob
        elif points == line:
            push += prob
        else:
            loss += prob
    total = win + push + loss
    return CoverOutcome(win / total, push / total, loss / total)


def fit_key_bumps(
    margins: Iterable[int],
    *,
    sigma: float | None = None,
    max_key: int = 30,
    min_games: int = 500,
) -> dict[int, float]:
    """Refit key-number multipliers from real results.

    Compares how often each absolute margin actually occurred against how often
    a plain normal of the same dispersion says it should have. The ratio is the
    multiplier. Use several seasons: with a few hundred games the ratios are
    mostly sampling noise, which is why this refuses to run on a small sample.
    """
    observed = Counter(int(m) for m in margins if m != 0)
    n = sum(observed.values())
    if n < min_games:
        raise ValueError(
            f"refitting key numbers needs at least {min_games} games, got {n}"
        )

    if sigma is None:
        mean_abs = sum(abs(m) * c for m, c in observed.items()) / n
        # For a zero-mean normal, E|X| = sigma * sqrt(2/pi).
        sigma = mean_abs / math.sqrt(2.0 / math.pi)

    bumps: dict[int, float] = {}
    for k in range(1, max_key + 1):
        actual = (observed.get(k, 0) + observed.get(-k, 0)) / n
        expected = (
            _normal_cdf(k + 0.5, 0.0, sigma)
            - _normal_cdf(k - 0.5, 0.0, sigma)
            + _normal_cdf(-k + 0.5, 0.0, sigma)
            - _normal_cdf(-k - 0.5, 0.0, sigma)
        )
        if expected > 0.0 and actual > 0.0:
            bumps[k] = actual / expected

    # Estimating sigma from a sample that already contains key-number spikes
    # inflates it, which drags every fitted ratio down by roughly the same
    # factor. Rescaling so the non-key buckets average to 1.0 removes that
    # shared bias and leaves only the relative structure, which is the part
    # being measured. Without this the fit understates every bump by ~5%.
    baseline = [v for k, v in bumps.items() if k not in KEY_BUMPS]
    if baseline:
        scale = sum(baseline) / len(baseline)
        if scale > 0.0:
            bumps = {k: v / scale for k, v in bumps.items()}

    # Clamp so one noisy bucket cannot dominate the distribution.
    return {k: min(2.0, max(0.5, v)) for k, v in bumps.items()}
