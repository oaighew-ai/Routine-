"""CLV as EV at the closing no-vig price (A3), lagged CLV (A4), Gate 2 (A5).

The definition matters more than it sounds. A conventional implied-probability
move is not CLV here: +1.5 points of it at a -110 entry is still -1.8% EV
against the fair close, so a system can beat the close on that measure every
week and lose money doing it.

This module reconciles with `cfb_edge.clv`, which A3 requires before anything is
graded. The existing `LoggedBet.price_clv` is `closeFairProb - 1/entryDecimal`;
multiply by the decimal odds and it is exactly this module's `clvPct`. The two
differ by a factor of 1.909 at -110, so the reconciliation is not cosmetic, and
`tests/test_engine.py` holds the identity to 1e-12.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from ..bootstrap import Interval, cluster_bootstrap
from ..distribution import (
    cover_probability,
    margin_pmf,
    over_probability,
    sigma_for_total,
    total_pmf,
)
from ..market import american_to_decimal, devig_multiplicative, devig_power

EXACT = "exact"
PMF = "pmf"

SPREAD_MARKETS = frozenset({"spreads", "spread"})
TOTAL_MARKETS = frozenset({"totals", "total"})
MONEYLINE_MARKETS = frozenset({"h2h", "moneyline", "ml"})


class MarketMismatch(ValueError):
    """Raised rather than converting a total through the margin distribution.

    A3 is explicit: totals need their own total-points distribution, never the
    margin PMF. The two have different supports, different dispersion and
    different key numbers, and silently using one for the other produces a
    number that looks entirely reasonable and is wrong.
    """


def clv_pct(close_fair_prob: float, entry_price: float) -> float:
    """A3. `closeFairProb_at_entry_line * entryDecimal - 1`."""
    return close_fair_prob * american_to_decimal(entry_price) - 1.0


def fair_prob(
    close_price: float, close_opposite_price: float, *, method: str = "proportional"
) -> float:
    """De-vigged closing probability of our side, at the closing line.

    Proportional by default, matching `p_baseline`, so entry and close are
    measured on the same scale. This is a real choice: `cfb_edge.clv` defaults
    to Shin, and on a -108/-112 close the two fair probabilities differ by
    0.0002, which is 0.04 percentage points of `clvPct`. Small, but it is the
    kind of small that accumulates in one direction across a whole ledger, so
    the method is pinned rather than inherited.
    """
    fn = devig_power if method == "power" else devig_multiplicative
    return fn([close_price, close_opposite_price])[0]


def _calibrate(make_pmf, evaluate, anchor: float, target: float) -> dict:
    """Shift a distribution's centre until it reproduces the market's opinion.

    Centring the PMF on the closing line assumes the close is a coin flip at
    that number, which it is not once the closing price is anything but even
    money. Solving for the centre that reproduces the actual closing fair
    probability keeps the conversion anchored to what the market said rather
    than to a convenient assumption.
    """
    lo, hi = anchor - 40.0, anchor + 40.0
    pmf = make_pmf(anchor)
    if abs(evaluate(pmf, anchor) - target) < 1e-9:
        return pmf
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        pmf = make_pmf(mid)
        if evaluate(pmf, anchor) < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-9:
            break
    return make_pmf(0.5 * (lo + hi))


def close_fair_prob_at_entry(
    *,
    market: str,
    side_is_over: bool | None = None,
    entry_line: float | None,
    close_line: float | None,
    close_fair: float,
    market_total: float = 52.0,
) -> tuple[float, str]:
    """The closing market's probability for the number we actually took.

    Returns the probability and the method tag, `exact` when the line did not
    move and `pmf` when it did. A5 requires the gate to pass with and without
    `pmf` rows, which is why the tag is carried on every grade.
    """
    if market in MONEYLINE_MARKETS or entry_line is None or close_line is None:
        return close_fair, EXACT
    if abs(entry_line - close_line) < 1e-12:
        return close_fair, EXACT

    if market in SPREAD_MARKETS:
        sigma = sigma_for_total(market_total)
        pmf = _calibrate(
            lambda centre: margin_pmf(centre, sigma),
            lambda p, line: cover_probability(p, line).win_excluding_push,
            close_line,
            close_fair,
        )
        return cover_probability(pmf, entry_line).win_excluding_push, PMF

    if market in TOTAL_MARKETS:
        if side_is_over is None:
            raise MarketMismatch("a totals row must say which side it is on")

        def evaluate(p, line):
            o = over_probability(p, line)
            return o.win_excluding_push if side_is_over else 1.0 - o.win_excluding_push

        pmf = _calibrate(lambda centre: total_pmf(centre), evaluate, close_line, close_fair)
        return evaluate(pmf, entry_line), PMF

    raise MarketMismatch(
        f"no conversion for market {market!r}. Adding one means choosing a "
        f"distribution, and A3 forbids reusing the margin PMF for anything "
        f"that is not a margin."
    )


@dataclass(frozen=True)
class Grade:
    """One graded candidate. `grades.jsonl` row."""

    candidate_id: str
    close_ref: str
    close_line: float | None
    close_price: float | None
    close_fair_prob: float
    clv_pct: float
    clv_lag_pct: float | None
    clv_method: str
    raw_move: float | None
    result: str | None
    units: float
    graded_at: str


def grade(
    *,
    candidate_id: str,
    market: str,
    entry_line: float | None,
    entry_price: float,
    close_line: float | None,
    close_price: float | None,
    close_opposite_price: float | None,
    close_ref: str,
    graded_at: str,
    lag_price: float | None = None,
    lag_opposite_price: float | None = None,
    lag_line: float | None = None,
    side_is_over: bool | None = None,
    market_total: float = 52.0,
    result: str | None = None,
    stake_units: float = 0.0,
) -> Grade:
    """Grade one candidate against the close and against the lag price."""
    if close_price is None or close_opposite_price is None:
        raise ValueError(
            "both sides of the close are needed to de-vig it. A one-sided close "
            "is a quote, and grading against it reports the vig as edge."
        )
    close_fair = fair_prob(close_price, close_opposite_price)
    p_entry, method = close_fair_prob_at_entry(
        market=market,
        side_is_over=side_is_over,
        entry_line=entry_line,
        close_line=close_line,
        close_fair=close_fair,
        market_total=market_total,
    )

    lag = None
    if lag_price is not None and lag_opposite_price is not None:
        lag_fair = fair_prob(lag_price, lag_opposite_price)
        p_lag, _ = close_fair_prob_at_entry(
            market=market,
            side_is_over=side_is_over,
            entry_line=entry_line,
            close_line=lag_line if lag_line is not None else close_line,
            close_fair=lag_fair,
            market_total=market_total,
        )
        lag = clv_pct(p_lag, entry_price)

    raw = None
    if entry_line is not None and close_line is not None:
        raw = entry_line - close_line

    units = 0.0
    if result == "win":
        units = stake_units * (american_to_decimal(entry_price) - 1.0)
    elif result == "loss":
        units = -stake_units

    return Grade(
        candidate_id=candidate_id,
        close_ref=close_ref,
        close_line=close_line,
        close_price=close_price,
        close_fair_prob=p_entry,
        clv_pct=clv_pct(p_entry, entry_price),
        clv_lag_pct=lag,
        clv_method=method,
        raw_move=raw,
        result=result,
        units=units,
        graded_at=graded_at,
    )


# --------------------------------------------------------------------------
# A5: Gate 2
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    """Gate 2's answer, and everything needed to argue with it."""

    label: str
    n_rows: int
    n_clusters: int
    interval: Interval | None
    min_rows: int
    min_clusters: int

    @property
    def passed(self) -> bool:
        if self.interval is None:
            return False
        return (
            self.n_rows >= self.min_rows
            and self.n_clusters >= self.min_clusters
            and self.interval.low > 0.0
        )

    def describe(self) -> str:
        if self.interval is None:
            return f"{self.label}: no graded BET rows"
        verdict = "PASS" if self.passed else "not yet"
        return (
            f"{self.label}: mean {self.interval.point:+.4%} "
            f"[{self.interval.low:+.4%}, {self.interval.high:+.4%}] "
            f"n={self.n_rows}/{self.min_rows} rows, "
            f"{self.n_clusters}/{self.min_clusters} slates -> {verdict}"
        )


def _eligible(rows: Iterable[Mapping], *, include_pmf: bool) -> list[Mapping]:
    """Gate 2 counts only graded BET rows with a gradeable price.

    PASS rows are graded too, for filter diagnostics, and must never reach this
    list: a filter that is working will have passed on the bad ones, so counting
    them measures the filter's rejects rather than the system's picks.
    """
    out = []
    for r in rows:
        if r.get("decision") != "BET":
            continue
        if r.get("clvLagPct") is None:
            continue
        if not r.get("gradeable", True):
            continue
        if not include_pmf and r.get("clvMethod") == PMF:
            continue
        out.append(r)
    return out


def gate2(
    rows: Sequence[Mapping],
    *,
    min_rows: int = 60,
    min_clusters: int = 12,
    alpha: float = 0.05,
    include_pmf: bool = True,
    label: str = "all",
) -> GateResult:
    """A5, extended by D5 to gate on slate-date clusters as well as rows.

    Standard errors are clustered by slate date because rows on one Saturday
    share that Saturday's weather, officiating and news. `bootstrap.py` measured
    what ignoring that costs on this project's own data: an interval 3.5x too
    narrow, promoting a signal the honest one cannot separate from zero.
    """
    eligible = _eligible(rows, include_pmf=include_pmf)
    if not eligible:
        return GateResult(label, 0, 0, None, min_rows, min_clusters)
    values = [float(r["clvLagPct"]) for r in eligible]
    clusters = [str(r.get("slateDate", r.get("startsAt", "?"))[:10]) for r in eligible]
    interval = cluster_bootstrap(values, clusters, alpha=alpha)
    return GateResult(
        label=label,
        n_rows=len(values),
        n_clusters=interval.clusters,
        interval=interval,
        min_rows=min_rows,
        min_clusters=min_clusters,
    )


def gate2_report(rows: Sequence[Mapping], **kw) -> list[GateResult]:
    """Gate 2 with and without `pmf` rows, then split by sport (A5)."""
    out = [
        gate2(rows, include_pmf=True, label="all", **kw),
        gate2(rows, include_pmf=False, label="exact only", **kw),
    ]
    for sport in sorted({str(r.get("sport", "?")) for r in rows}):
        subset = [r for r in rows if str(r.get("sport", "?")) == sport]
        out.append(gate2(subset, include_pmf=True, label=sport, **kw))
    return out
