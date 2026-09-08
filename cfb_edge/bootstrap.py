"""Confidence intervals that respect how the observations are actually related.

855 contracts across 67 games are not 855 independent observations. Every
strike on one game shares that game's news, its weather, its officiating, and
whatever the market was collectively wrong about, so the residuals inside a
game are correlated. Treating each contract as independent shrinks the standard
error by roughly the square root of the number of contracts per game, which is
how a system convinces itself an edge is significant when it is not.

The fix is to resample whole games rather than individual contracts. A game is
either in a replicate or it is not, and when it is, all of its contracts come
along. That is a cluster bootstrap, and on a board like this it typically
widens the interval by a factor of two or more.

This matters directly for a promotion rule. A rule reading "positive mean CLV
with a bootstrap lower bound above zero at n >= 40" has to be counting games,
not contracts, or the bar is far lower than it looks.
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

DEFAULT_REPLICATES = 10_000
DEFAULT_ALPHA = 0.05


@dataclass(frozen=True)
class Interval:
    """A bootstrap estimate and the sample it rests on."""

    point: float
    low: float
    high: float
    clusters: int
    observations: int
    replicates: int
    alpha: float

    @property
    def excludes_zero(self) -> bool:
        return self.low > 0.0 or self.high < 0.0

    @property
    def width(self) -> float:
        return self.high - self.low

    def describe(self, unit: str = "c") -> str:
        verdict = "excludes zero" if self.excludes_zero else "includes zero"
        return (
            f"{self.point:+.3f}{unit} "
            f"[{self.low:+.3f}, {self.high:+.3f}] "
            f"({self.clusters} clusters, {self.observations} obs, {verdict})"
        )


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def cluster_bootstrap(
    values: Sequence[float],
    clusters: Sequence[str],
    *,
    statistic: Callable[[Sequence[float]], float] = _mean,
    replicates: int = DEFAULT_REPLICATES,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 17,
) -> Interval:
    """Percentile bootstrap over whole clusters.

    `values` and `clusters` are parallel: each observation carries the label of
    the group it belongs to. Clusters are resampled with replacement, and every
    observation in a drawn cluster is included.
    """
    if len(values) != len(clusters):
        raise ValueError("values and clusters must be the same length")
    if not values:
        raise ValueError("nothing to bootstrap")

    grouped: dict[str, list[float]] = defaultdict(list)
    for value, key in zip(values, clusters):
        grouped[key].append(value)
    keys = list(grouped)

    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(replicates):
        pooled: list[float] = []
        for _ in range(len(keys)):
            pooled.extend(grouped[keys[rng.randrange(len(keys))]])
        draws.append(statistic(pooled))
    draws.sort()

    lo_i = int((alpha / 2.0) * len(draws))
    hi_i = min(len(draws) - 1, int((1.0 - alpha / 2.0) * len(draws)))
    return Interval(
        point=statistic(values),
        low=draws[lo_i],
        high=draws[hi_i],
        clusters=len(keys),
        observations=len(values),
        replicates=replicates,
        alpha=alpha,
    )


def naive_bootstrap(
    values: Sequence[float],
    *,
    statistic: Callable[[Sequence[float]], float] = _mean,
    replicates: int = DEFAULT_REPLICATES,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 17,
) -> Interval:
    """Resample individual observations, ignoring clustering.

    Provided so the difference can be measured rather than argued about. Do not
    promote anything on the strength of this interval.
    """
    return cluster_bootstrap(
        values,
        [str(i) for i in range(len(values))],
        statistic=statistic,
        replicates=replicates,
        alpha=alpha,
        seed=seed,
    )


def inflation_factor(values: Sequence[float], clusters: Sequence[str],
                     **kw) -> float:
    """How much wider the honest interval is than the naive one.

    A factor near 1 means clustering barely matters for this sample. A factor
    of 2 means the naive interval was half the width it should have been, and
    anything promoted on it was promoted on a bar half as high as intended.
    """
    naive = naive_bootstrap(values, **kw)
    clustered = cluster_bootstrap(values, clusters, **kw)
    return clustered.width / naive.width if naive.width else float("inf")


def required_clusters(
    observed_mean: float, cluster_sd: float, *, alpha: float = DEFAULT_ALPHA
) -> int:
    """Rough number of games needed for a lower bound above zero.

    A planning figure, not a guarantee: it assumes the observed mean is the
    truth, which is exactly the assumption a real result has to earn. Useful
    for answering "is this season long enough to find out" before spending it.
    """
    if observed_mean <= 0 or cluster_sd <= 0:
        return 0
    z = 1.959963985 if abs(alpha - 0.05) < 1e-9 else 2.575829304
    return max(1, int((z * cluster_sd / observed_mean) ** 2) + 1)
