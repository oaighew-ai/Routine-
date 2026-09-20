"""Stage A: deciding whether a signal is evidence, and how much of it.

The whole job of this module is to stop a 3-1 record from outvoting a 194-129
one. It does that by never using a raw win rate. Stage A reports a lower
confidence bound, and the weight it carries grows with the sample behind it.

A1 (pooling) and A2 (the CLV kill) are implemented here; both are recorded in
`DECISIONS.md`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from . import reasons
from .config import Config

# Two-sided 95%. The same constant `bootstrap.required_clusters` uses.
Z95 = 1.959963985


@dataclass(frozen=True)
class SystemRecord:
    """One system's evidence, as supplied by the owner and as measured forward.

    Nothing in here is ever invented. `providerRecord` comes from the owner;
    the own-forward fields are computed from ledger rows.
    """

    system_id: str
    family: str
    flag: str = "system"
    flag_weight: float = 1.0

    wins_provider: int = 0
    losses_provider: int = 0
    wins_own: int = 0
    losses_own: int = 0

    # Own forward picks, for the market-relative form of §6.4. Each pick's
    # excess is (win - p_nv) at entry, so the mean is in probability points.
    excess_n: int = 0
    excess_mean: float = 0.0
    excess_sd: float = 0.0

    # A2 inputs, from this system's own graded BET rows.
    own_bet_grades: int = 0
    own_mean_clv: float = 0.0
    own_roi: float = 0.0

    @property
    def n_provider(self) -> int:
        return self.wins_provider + self.losses_provider

    @property
    def n_own(self) -> int:
        return self.wins_own + self.losses_own

    @property
    def n_pooled(self) -> int:
        """A1: provider and own forward records pool. No replacement at 30."""
        return self.n_provider + self.n_own

    @property
    def wins_pooled(self) -> int:
        return self.wins_provider + self.wins_own

    @property
    def p_hat(self) -> float | None:
        n = self.n_pooled
        return self.wins_pooled / n if n else None


def wilson_lower(wins: int, n: int, *, z: float = Z95) -> float:
    """Lower bound of the Wilson score interval.

    Chosen over the normal approximation because at small n or extreme p the
    normal bound leaves [0, 1] and would hand a tiny sample a usable floor.
    PRIOR, per `spec/EDGE_OS_v2_DERIVED.md` §2.2.
    """
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return max(0.0, (centre - half) / denom)


def excess_lower(mean: float, sd: float, n: int, *, z: float = Z95) -> float:
    """Lower 95% bound of a mean excess. §6.4's `L`.

    A normal bound on a mean is appropriate here in a way it is not on a
    proportion: the quantity is an average of bounded differences, not a rate,
    and it is legitimately allowed to be negative.
    """
    if n <= 0:
        return 0.0
    if n == 1 or sd <= 0.0:
        return mean
    return mean - z * sd / math.sqrt(n)


@dataclass(frozen=True)
class StageA:
    """What Stage A concluded about one signal on one candidate."""

    system_id: str
    family: str
    side: str
    p_signal: float | None
    w_sig: float
    n_provider: int
    n_own: int
    flags: tuple[str, ...] = ()
    method: str = ""

    @property
    def passed(self) -> bool:
        return self.p_signal is not None and self.w_sig > 0.0


def raw_weight(record: SystemRecord, cfg: Config) -> float:
    """`flagWeight * n / (n + N_HALF)`, before A2 can zero it.

    Monotone in n, continuous everywhere, zero at n = 0. Continuity is not
    decoration: it is what makes acceptance check 6 (no discontinuity at own
    n = 30) true by construction rather than by testing one point.
    """
    n = record.n_pooled
    if n <= 0:
        return 0.0
    return record.flag_weight * n / (n + cfg.n_half)


def clv_kill(record: SystemRecord, cfg: Config) -> tuple[bool, tuple[str, ...]]:
    """A2. Below the floor this is monitoring only and returns no flags.

    The floor exists because of the Graveyard entry "CLV as a sizing control at
    small n": a clustered interval on a few dozen rows cannot separate a real
    edge from zero, so acting on the point estimate is acting on noise.
    """
    if record.own_bet_grades < cfg.kill_min_own_grades:
        return False, ()
    if record.own_mean_clv > 0.0:
        return False, ()
    flags = [reasons.CLV_KILL]
    if record.own_roi > 0.0:
        # Money made on negative CLV is the signature of variance, not edge.
        flags.append(reasons.LUCK_RISK)
    return True, tuple(flags)


def stage_a(
    record: SystemRecord,
    *,
    p_nv: float,
    side: str = "for",
    cfg: Config,
) -> StageA:
    """Run Stage A for one signal against this candidate's no-vig price.

    `p_nv` is the no-vig probability of the side the *signal* is on, which is
    what makes the evidence market-relative (§6.4).
    """
    killed, kill_flags = clv_kill(record, cfg)
    w = 0.0 if killed else raw_weight(record, cfg)
    flags = list(kill_flags)

    lo, hi = cfg.near_even_band
    has_excess = record.excess_n >= cfg.own_n_for_variable_price

    if has_excess:
        # Market-relative, the primary form.
        p_signal = p_nv + excess_lower(
            record.excess_mean, record.excess_sd, record.excess_n
        )
        method = "excess"
    elif record.n_pooled <= 0:
        p_signal, method = None, "none"
        flags.append(reasons.NO_EVIDENCE)
        w = 0.0
    elif lo <= p_nv <= hi:
        # Near even, where §6.4 says the two forms coincide.
        p_signal = wilson_lower(record.wins_pooled, record.n_pooled)
        method = "floor"
    else:
        # Aggregates only, away from even. Display only until own n >= 30.
        p_signal, method = None, "aggregate_only"
        flags.append(reasons.AGGREGATE_ONLY)
        w = 0.0

    if p_signal is not None:
        p_signal = min(1.0 - 1e-9, max(1e-9, p_signal))

    return StageA(
        system_id=record.system_id,
        family=record.family,
        side=side,
        p_signal=p_signal,
        w_sig=0.0 if p_signal is None else w,
        n_provider=record.n_provider,
        n_own=record.n_own,
        flags=tuple(flags),
        method=method,
    )


def run_stage_a(
    records: Sequence[tuple[SystemRecord, str]],
    *,
    p_nv_for: float,
    cfg: Config,
) -> list[StageA]:
    """Stage A over every signal on one candidate.

    `records` pairs a system with the side it fired on, `"for"` or `"against"`.
    An against-signal is evaluated against the *other* side's no-vig price,
    because that is the market the system was betting into.
    """
    out: list[StageA] = []
    for record, side in records:
        p_nv = p_nv_for if side == "for" else 1.0 - p_nv_for
        out.append(stage_a(record, p_nv=p_nv, side=side, cfg=cfg))
    return out
