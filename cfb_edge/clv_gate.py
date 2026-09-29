"""An anytime-valid gate on the claim the strategy actually makes.

The delivery authority in `config/delivery_authority.json` tests whether the
model forecasts outcomes better than the market: log loss, Brier, a sequential
e-value on those. Measured over 48 forecasts it does not, and it is not close —
log-loss advantage -0.0055 where +0.003 is required, Brier worse by 0.0028.

But that was never this strategy's claim. What has ever measured positive here
is +0.44 points of closing line value against the **opening** number, which is
an assertion about the market being slow rather than about the model being
accurate. A model can be worse than the close at predicting football and still
take value from a stale opening line; those are independent propositions, and
the accuracy gates test only the first. So the authority as configured can
never green-light this strategy even if the strategy works.

This module tests the second proposition directly.

## The null is not zero

Beating zero closing line value does not make money, so testing against zero
would pass a strategy that loses. The null is the fee-adjusted breakeven, and
it is computed here rather than asserted:

    Kalshi charges 0.07 * P * (1 - P) per contract, which peaks at 0.0175 at
    P = 0.5 — exactly where a spread contract sits. Converting a fee in
    probability into a fee in points needs the slope of cover probability
    against the line. For margin residual sd s, that slope at the money is
    phi(0) / s, so a point is worth phi(0)/s of probability and a unit of
    probability costs s/phi(0) points:

        s = 15.39 (CLOSING_LINE_RESIDUAL_SD, measured over 6,398 games)
        s / phi(0)            = 38.58 points per unit probability
        0.0175 * 38.58        = 0.675 points

That number deserves attention. **The breakeven is 0.675 points and the
strategy's own headline claim is +0.44.** Two independent routes agree on it:
the -110 sportsbook figure this project has quoted from the start is 0.67. So
even if the +0.44 were real and perfectly captured, it does not clear the fee.
The gate below will say so rather than leaving it to be noticed later.

## The test

An e-process, so it can be read at any time without alpha spending, matching
the accuracy gate's `minimumAnytimeEValue: 20.0` (alpha = 0.05) so the two are
directly comparable.

Construction is a uniform mixture over a grid of positive tilts:

    E_n = mean over k of exp(lambda_k * S_n - lambda_k^2 * sigma^2 * n / 2)

where S_n is the running sum of (clv - null) and sigma is a sub-Gaussian
proxy. Each term is an e-process whenever the true mean is at or below the
null, because E[exp(lambda * Y)] <= exp(lambda^2 sigma^2 / 2) there, and a
convex mixture of e-processes is an e-process. Every lambda is positive, so the
statistic accumulates only on evidence that the mean is *above* the null and
cannot be triggered by a strongly negative run. `test_clv_gate.py` verifies
the guarantee by simulation rather than trusting this paragraph.

Nothing here is a closed-form confidence sequence with special functions,
because the mixture above needs only `exp` and is provably valid by
construction. Simplicity is the point: a gate nobody can check is not a gate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .clv import GRADEABLE

CONTRACT = "CFB_EDGE_CLV_GATE_V1"

# --- inputs to the breakeven, each with its provenance -----------------------

KALSHI_FEE_COEFFICIENT = 0.07
"""Kalshi's spread-market fee is `coefficient * P * (1 - P)` per contract."""

CLOSING_LINE_RESIDUAL_SD = 15.39
"""Measured over 6,398 games; the same figure `requirement.py` derives from."""

NORMAL_PDF_AT_ZERO = 1.0 / math.sqrt(2.0 * math.pi)
"""phi(0) = 0.39894..., the slope constant at the money."""

# --- inputs to the test ------------------------------------------------------

SUB_GAUSSIAN_SD = 2.0
"""Sub-Gaussian proxy for per-game CLV, in points.

Measured 2.00 across the 155 games of `data/early-season-learning.json`. The
paper log's nine graded entries gave 0.61, so 2.00 is the conservative of the
two. `evaluate` uses `max(this, observed sd)` regardless: understating sigma
would inflate the e-value, and the gate must never fail in that direction.
"""

E_VALUE_THRESHOLD = 20.0
"""alpha = 0.05, matching `minimumAnytimeEValue` in the accuracy authority."""

MINIMUM_WEEK_CLUSTERS = 4
"""PRIOR (Law 6). No derivation, and it is not a power calculation.

One week of this season measured +0.539 at t = 2.26 while the week before it
measured -0.510 at t = -1.91. A gate satisfied by a single favourable week
would have passed on week three alone. Four is the smallest number that makes
that impossible; it is a judgment about week-to-week instability, so it is
tagged rather than dressed up as arithmetic.
"""

DETECTABLE_EXCESS = 0.5
"""The excess over breakeven the minimum sample is sized to detect, in points.

Chosen because it is roughly what the strategy would need to be worth to be
worth running at all: 0.675 breakeven plus 0.5 is 1.18 points of CLV per game,
against a claimed 0.44 and a measured 0.05.
"""


def points_per_unit_probability(margin_sd: float = CLOSING_LINE_RESIDUAL_SD) -> float:
    """How many points of line one unit of cover probability buys, at the money.

    A half point is worth far more at 3 than at 12, and this is the conversion
    at the only place a spread contract actually trades.
    """
    if margin_sd <= 0.0:
        raise ValueError("margin_sd must be positive")
    return margin_sd / NORMAL_PDF_AT_ZERO


def breakeven_points(
    fee_coefficient: float = KALSHI_FEE_COEFFICIENT,
    margin_sd: float = CLOSING_LINE_RESIDUAL_SD,
) -> float:
    """Points of CLV a bet must beat before it earns anything.

    The fee is evaluated at P = 0.5, where `coefficient * P * (1 - P)` is at
    its maximum. That is not pessimism: it is where the contracts this
    strategy buys are priced.
    """
    worst_fee = fee_coefficient * 0.25
    return worst_fee * points_per_unit_probability(margin_sd)


def observations_required(
    excess: float = DETECTABLE_EXCESS,
    *,
    sigma: float = SUB_GAUSSIAN_SD,
    threshold: float = E_VALUE_THRESHOLD,
) -> int:
    """Games needed to reach the e-value threshold at a given true excess.

    At the tilt best suited to an excess d, evidence accrues at d^2 / (2
    sigma^2) nats per observation, and the threshold is ln(threshold) nats.
    This is what makes the minimum sample a derivation rather than a habit.
    """
    if excess <= 0.0:
        raise ValueError("excess must be positive to be detectable")
    if sigma <= 0.0:
        raise ValueError("sigma must be positive")
    rate = (excess * excess) / (2.0 * sigma * sigma)
    return int(math.ceil(math.log(threshold) / rate))


def tilt_grid(
    *, sigma: float = SUB_GAUSSIAN_SD, low: float = 0.1, high: float = 2.0,
    count: int = 12,
) -> tuple[float, ...]:
    """Positive tilts spanning the excesses worth detecting.

    The tilt best suited to a true excess d is d / sigma^2, so a geometric
    sweep of d from a tenth of a point to two points covers everything between
    "not worth running" and "implausibly good" without favouring one.
    """
    if count < 1:
        raise ValueError("count must be at least 1")
    if not 0.0 < low <= high:
        raise ValueError("require 0 < low <= high")
    if count == 1:
        return (low / (sigma * sigma),)
    step = (high / low) ** (1.0 / (count - 1))
    return tuple((low * step ** i) / (sigma * sigma) for i in range(count))


def e_value(
    values: list[float],
    *,
    null_mean: float,
    sigma: float = SUB_GAUSSIAN_SD,
    tilts: tuple[float, ...] | None = None,
) -> tuple[float, float]:
    """The e-process at the last observation, and its running maximum.

    Returns both because the guarantee is about the maximum: Ville's
    inequality bounds the probability that the process *ever* crosses a
    threshold, so a gate that reads only the final value throws away the
    anytime property it was built for.
    """
    if sigma <= 0.0:
        raise ValueError("sigma must be positive")
    lambdas = tilts if tilts is not None else tilt_grid(sigma=sigma)
    if not lambdas:
        raise ValueError("no tilts")
    if any(lam <= 0.0 for lam in lambdas):
        raise ValueError("every tilt must be positive; the test is one-sided")

    running = 0.0
    current = 1.0
    peak = 1.0
    for n, value in enumerate(values, start=1):
        running += value - null_mean
        half = 0.5 * sigma * sigma * n
        total = 0.0
        for lam in lambdas:
            total += math.exp(lam * running - lam * lam * half)
        current = total / len(lambdas)
        peak = max(peak, current)
    return current, peak


@dataclass(frozen=True)
class Observation:
    """One game's closing line value, and whether it is allowed to count."""

    game: str
    week: int
    open_line: float
    close_line: float | None
    source: str

    @property
    def gradeable(self) -> bool:
        """Only a venue-proven open or a real fill may enter the measurement.

        `GRADEABLE` in `clv.py` is the single authority on this, so widening
        the gate cannot be done here by accident.
        """
        return self.source in GRADEABLE and self.close_line is not None

    @property
    def clv(self) -> float | None:
        """Points of line beaten. Positive means the market came to us."""
        if self.close_line is None:
            return None
        return self.open_line - self.close_line


@dataclass(frozen=True)
class CLVGateReport:
    """What the gate found, and what it refuses to conclude."""

    contract: str
    observations: int
    gradeable: int
    excluded_provenance: int
    excluded_unsettled: int
    week_clusters: int
    null_mean: float
    mean_clv: float | None
    sd_clv: float | None
    sigma_used: float
    e_value: float
    max_e_value: float
    threshold: float
    minimum_observations: int
    minimum_week_clusters: int
    verdict: str
    failed_gates: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passing(self) -> bool:
        return self.verdict == "PASS"

    def summary(self) -> str:
        lines = [
            f"{self.contract}: {self.verdict}",
            f"  null (fee-adjusted breakeven) : {self.null_mean:+.3f} pts",
            f"  gradeable observations        : {self.gradeable} of "
            f"{self.observations} (need {self.minimum_observations})",
            f"  week clusters                 : {self.week_clusters} "
            f"(need {self.minimum_week_clusters})",
        ]
        if self.mean_clv is None:
            lines.append("  mean CLV                      : no gradeable rows")
        else:
            lines.append(f"  mean CLV                      : {self.mean_clv:+.3f} pts"
                         f"  (excess {self.mean_clv - self.null_mean:+.3f})")
            lines.append(f"  sd / sigma used               : "
                         f"{self.sd_clv:.3f} / {self.sigma_used:.3f}")
        lines.append(f"  e-value (max)                 : {self.e_value:.3f} "
                     f"({self.max_e_value:.3f}) vs {self.threshold:.1f}")
        if self.excluded_provenance:
            lines.append(f"  refused, provenance           : "
                         f"{self.excluded_provenance}")
        if self.excluded_unsettled:
            lines.append(f"  refused, no closing line      : "
                         f"{self.excluded_unsettled}")
        if self.failed_gates:
            lines.append(f"  failed                        : "
                         f"{', '.join(self.failed_gates)}")
        return "\n".join(lines)


def evaluate(
    observations: list[Observation],
    *,
    null_mean: float | None = None,
    sigma: float = SUB_GAUSSIAN_SD,
    threshold: float = E_VALUE_THRESHOLD,
    minimum_observations: int | None = None,
    minimum_week_clusters: int = MINIMUM_WEEK_CLUSTERS,
) -> CLVGateReport:
    """Run the gate. Fails closed: `PASS` requires evidence, never absence.

    The default verdict is `INSUFFICIENT`, and it takes the e-value crossing
    the threshold *with* the sample minimums met to move off it. An empty
    input is `INSUFFICIENT`, not `PASS`, which is the one property a gate on a
    system that has produced zero gradeable rows most needs.
    """
    import statistics

    null = breakeven_points() if null_mean is None else null_mean
    need = (observations_required(sigma=sigma, threshold=threshold)
            if minimum_observations is None else minimum_observations)

    graded = [o for o in observations if o.gradeable]
    refused_provenance = sum(
        1 for o in observations if o.source not in GRADEABLE)
    refused_unsettled = sum(
        1 for o in observations
        if o.source in GRADEABLE and o.close_line is None)

    values = [o.clv for o in graded]
    values = [v for v in values if v is not None]
    weeks = len({o.week for o in graded})

    mean = statistics.mean(values) if values else None
    sd = statistics.stdev(values) if len(values) > 1 else None

    # Never let an understated sigma inflate the evidence.
    sigma_used = max(sigma, sd) if sd is not None else sigma
    current, peak = e_value(
        values, null_mean=null, sigma=sigma_used) if values else (1.0, 1.0)

    failed: list[str] = []
    if len(values) < need:
        failed.append("MINIMUM_OBSERVATIONS")
    if weeks < minimum_week_clusters:
        failed.append("MINIMUM_WEEK_CLUSTERS")
    if peak < threshold:
        failed.append("ANYTIME_E_VALUE")

    verdict = "PASS" if not failed else "INSUFFICIENT"
    # A large, settled sample whose mean sits below the null is not merely
    # inconclusive. Say so, because "insufficient" invites another season.
    if failed and len(values) >= need and weeks >= minimum_week_clusters:
        if mean is not None and mean < null:
            verdict = "FAIL"

    return CLVGateReport(
        contract=CONTRACT,
        observations=len(observations),
        gradeable=len(values),
        excluded_provenance=refused_provenance,
        excluded_unsettled=refused_unsettled,
        week_clusters=weeks,
        null_mean=null,
        mean_clv=mean,
        sd_clv=sd,
        sigma_used=sigma_used,
        e_value=current,
        max_e_value=peak,
        threshold=threshold,
        minimum_observations=need,
        minimum_week_clusters=minimum_week_clusters,
        verdict=verdict,
        failed_gates=tuple(failed),
    )


def load_observations(path: str) -> list[Observation]:
    """Read observations from CSV: game, week, open_line, close_line, source.

    A row with an unreadable number is skipped rather than guessed at, and a
    blank `close_line` is carried through as None so the report can count it
    as unsettled instead of quietly dropping it.
    """
    import csv
    from pathlib import Path

    out: list[Observation] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                week = int(float(row["week"]))
                open_line = float(row["open_line"])
            except (KeyError, TypeError, ValueError):
                continue
            close_raw = (row.get("close_line") or "").strip()
            try:
                close_line = float(close_raw) if close_raw else None
            except ValueError:
                close_line = None
            out.append(Observation(
                game=(row.get("game") or "").strip(),
                week=week,
                open_line=open_line,
                close_line=close_line,
                source=(row.get("source") or "").strip(),
            ))
    return out


def main(argv: list[str] | None = None) -> int:
    """python3 -m cfb_edge.clv_gate --observations clv_rows.csv"""
    import argparse
    import json

    p = argparse.ArgumentParser(prog="cfb_edge.clv_gate", description=main.__doc__)
    p.add_argument("--observations", help="CSV of game,week,open_line,close_line,source")
    p.add_argument("--json", action="store_true", help="emit the report as JSON")
    p.add_argument("--breakeven", action="store_true",
                   help="print the fee-adjusted breakeven derivation and exit")
    args = p.parse_args(argv)

    if args.breakeven:
        print(f"fee at P=0.5                 : "
              f"{KALSHI_FEE_COEFFICIENT * 0.25:.4f} of a contract")
        print(f"points per unit probability  : "
              f"{points_per_unit_probability():.2f}  "
              f"(sd {CLOSING_LINE_RESIDUAL_SD} / phi(0))")
        print(f"breakeven                    : {breakeven_points():+.3f} points")
        print(f"strategy's headline claim    : +0.440 points")
        print(f"observations to detect +{DETECTABLE_EXCESS} over it: "
              f"{observations_required()}")
        return 0

    if not args.observations:
        p.error("--observations is required unless --breakeven is given")

    report = evaluate(load_observations(args.observations))
    if args.json:
        from dataclasses import asdict
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
    else:
        print(report.summary())
    # Exit 0 whether or not it passes: this reports, it does not gate a build.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
