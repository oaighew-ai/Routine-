"""How much, once the engine has decided there is something to bet.

Quarter Kelly with a correlation haircut, rounded down. The rounding direction
is not a detail: rounding up is how a 2% cap becomes a 2.5% cap on every ticket,
and it compounds in exactly the direction that hurts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import reasons
from .config import Config


def portfolio_scale(c: int, *, rho: float) -> float:
    """Haircut for `c` correlated positions on one slate.

    `1 / sqrt(1 + rho*(c-1))`: equal to 1 at a single position and decaying
    slowly rather than cliffing. PRIOR on the functional form; rho = 0.3 is
    BUILD_PROMPT §6.7's stated PRIOR.

    College Saturdays are correlated by weather, by conference officiating and
    by whatever the model has got systematically wrong, which is why the sum of
    a card is capped well below the sum of its parts.
    """
    if c <= 1:
        return 1.0
    if rho <= 0.0:
        return 1.0
    return 1.0 / math.sqrt(1.0 + rho * (c - 1))


def round_down(units: float, step: float) -> float:
    """Round a stake down to the nearest step. Never up.

    Uses an integer floor on a scaled value rather than `math.floor(u/step)`,
    so a stake that is exactly on a step does not fall to the one below it
    through floating point. 0.15 / 0.05 is 2.9999999999999996 in binary.
    """
    if step <= 0.0:
        return units
    n = math.floor(units / step + 1e-9)
    # Re-rounded because n*step reintroduces the binary error the floor just
    # removed: 3 * 0.05 is 0.15000000000000002, which would print on a ticket.
    return max(0.0, round(n * step, 10))


@dataclass(frozen=True)
class Stake:
    """A sizing decision, and every number that produced it."""

    f_full: float
    c: int
    portfolio_scale: float
    raw_units: float
    units: float
    dollars: float
    flags: tuple[str, ...] = ()

    @property
    def is_zero(self) -> bool:
        return self.units <= 0.0


def size(
    f_full: float,
    *,
    cfg: Config,
    c: int = 1,
    min_stake_units: float = 0.0,
) -> Stake:
    """`stake = 0.25 * f_full * bankroll * portfolioScale`, in units.

    A unit is 1% of bankroll, so dividing the dollar stake by the unit gives
    units directly. The 2.0u ceiling is a bug guard and nothing else: it is not
    a risk control and it should never bind, so every time it does the row says
    so.
    """
    scale = portfolio_scale(c, rho=cfg.rho)
    fraction = cfg.kelly_fraction * max(0.0, f_full) * scale
    raw_units = fraction * cfg.bankroll / cfg.unit if cfg.unit else 0.0

    flags: list[str] = []
    capped = raw_units
    if capped > cfg.ceiling_units:
        capped = cfg.ceiling_units
        flags.append(reasons.CEILING_HIT)

    units = round_down(capped, cfg.round_to_units)
    if units < min_stake_units:
        units = 0.0
        flags.append(reasons.SUB_MIN)

    return Stake(
        f_full=f_full,
        c=c,
        portfolio_scale=scale,
        raw_units=raw_units,
        units=units,
        dollars=units * cfg.unit,
        flags=tuple(flags),
    )
