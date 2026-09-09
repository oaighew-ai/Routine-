"""The cross-market gate, with the fee where it belongs.

The gate asks one question per market: can I buy this contract on Kalshi for
meaningfully less than a sportsbook says it is worth, after Kalshi's fee and
after actually filling the size I want?

Every check here exists because skipping it produces a specific false positive:

- MAP    a Kalshi strike with no matching book line is not comparable at all.
- BOOK   a book line with no usable per-side prices gives a fair value that is
         assumed rather than observed, which is the weakest input in the system.
- DEPTH  a midpoint is not a fill. Sizing off one turns thin books into edges.
- QUOTE  a one-sided or very wide book is a quote, not a market.
- EDGE   the gap has to survive the fee, which is largest at a coin flip.
- BAR    a surviving edge still has to be big enough to be worth the risk.

The EDGE check is the one that differs most from a naive implementation. A flat
entry bar in cents is loosest exactly where the fee is highest, so the bar is
computed from the price instead of fixed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .kalshi_fees import fee_cents_per_contract, net_edge, required_gross_edge_cents

# Contracts the gate insists it can fill before calling a price real.
DEFAULT_SIZE = 200

# Widest YES spread still treated as a market rather than a quote.
MAX_SPREAD_CENTS = 4.0

# Minimum net expected value, against the entry price.
MIN_NET_EV = 0.02


@dataclass(frozen=True)
class Candidate:
    """One Kalshi market lined up against a book price."""

    ticker: str
    label: str
    game: str
    market: str
    entry_cents: float
    fair_cents: float | None
    fair_is_assumed: bool
    depth: int
    spread: float | None


@dataclass
class GateResult:
    candidate: Candidate
    checks: dict[str, bool] = field(default_factory=dict)
    gross_cents: float = 0.0
    fee_cents: float = 0.0
    net_cents: float = 0.0
    net_ev: float = 0.0
    required_gross: float = 0.0
    stake_fraction: float = 0.0
    reason: str = ""

    @property
    def passed(self) -> int:
        return sum(1 for ok in self.checks.values() if ok)

    @property
    def cleared(self) -> bool:
        return all(self.checks.values())

    def line(self) -> str:
        mark = "BET " if self.cleared else "pass"
        assumed = " [assumed 50c]" if self.candidate.fair_is_assumed else ""
        return (
            f"{mark} {self.candidate.label:<34} {self.candidate.entry_cents:5.1f}c "
            f"gross {self.gross_cents:+5.2f} fee {self.fee_cents:4.2f} "
            f"net {self.net_cents:+5.2f} ({self.net_ev:+6.2%}) "
            f"{self.passed}/6{assumed}  {self.reason}"
        )


def quarter_kelly(entry_cents: float, net_cents: float, *, cap: float = 0.02) -> float:
    """Stake as a fraction of bankroll.

    For a contract bought at P cents that settles at 0 or 100, full Kelly
    reduces exactly to net / (100 - P). Taken at a quarter and capped, because
    the edge is an estimate.
    """
    if net_cents <= 0 or not 0 < entry_cents < 100:
        return 0.0
    return min(cap, 0.25 * net_cents / (100.0 - entry_cents))


def evaluate(
    candidate: Candidate,
    *,
    size: int = DEFAULT_SIZE,
    min_net_ev: float = MIN_NET_EV,
    max_spread: float = MAX_SPREAD_CENTS,
    allow_assumed_fair: bool = False,
) -> GateResult:
    """Run one market through all six checks."""
    result = GateResult(candidate=candidate)
    c = candidate

    result.checks["MAP"] = bool(c.ticker and c.game)
    result.checks["BOOK"] = c.fair_cents is not None and (
        allow_assumed_fair or not c.fair_is_assumed
    )
    result.checks["DEPTH"] = c.depth >= size
    result.checks["QUOTE"] = c.spread is not None and c.spread <= max_spread

    if c.fair_cents is None or not 0 < c.entry_cents < 100:
        result.reason = "no comparable price"
        result.checks["EDGE"] = False
        result.checks["BAR"] = False
        return result

    gross = c.fair_cents - c.entry_cents
    edge = net_edge(c.entry_cents / 100.0, gross)
    result.gross_cents = gross
    result.fee_cents = edge.fee_cents
    result.net_cents = edge.net_cents
    result.net_ev = edge.net_ev
    result.required_gross = required_gross_edge_cents(c.entry_cents / 100.0)

    result.checks["EDGE"] = edge.net_cents > 0.0
    result.checks["BAR"] = edge.net_ev >= min_net_ev
    result.stake_fraction = quarter_kelly(c.entry_cents, edge.net_cents)

    if not result.checks["EDGE"]:
        result.reason = (
            f"fee {edge.fee_cents:.2f}c exceeds the {gross:+.2f}c gap; "
            f"needed {result.required_gross:.2f}c gross"
        )
    elif not result.checks["BAR"]:
        result.reason = f"net EV {edge.net_ev:+.2%} below {min_net_ev:.0%} bar"
    elif not result.checks["DEPTH"]:
        result.reason = f"only {c.depth} resting, wanted {size}"
    elif not result.checks["QUOTE"]:
        result.reason = "spread too wide to be a market"
    elif not result.checks["BOOK"]:
        result.reason = "fair value assumed, not observed"
    else:
        result.reason = f"stake {result.stake_fraction:.2%} of bankroll"
    return result


def rank(results: list[GateResult]) -> list[GateResult]:
    """Cleared markets, best net expected value first."""
    return sorted(
        (r for r in results if r.cleared), key=lambda r: r.net_ev, reverse=True
    )
