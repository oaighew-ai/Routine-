"""Stage B: one posterior, one decision, one row.

Everything upstream produces beliefs. This module turns them into exactly one
of BET, PASS, NO_BET or STALE, with the reason codes that say why, and it does
it once (Law 5). There is no second threshold anywhere below this line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..market import devig_multiplicative, devig_power, american_to_probability
from . import reasons
from .config import Config
from .evidence import StageA, SystemRecord, run_stage_a
from .pricing import Executable, decide_at_price, executable, max_playable_price
from .sizing import Stake, size

GRADEABLE_PRICE_SOURCES = frozenset({"capture", "fill"})


@dataclass(frozen=True)
class Quote:
    """The market as it stands for one side of one event, at one venue."""

    event_id: str
    sport: str
    market: str            # "spreads", "h2h", "totals"
    side: str
    line: float | None
    price: float           # the executable price on our side, at `venue`
    other_price: float | None = None   # the opposite side at the same venue

    # The consensus two-way market, which is what `p_baseline` is de-vigged
    # from. Law 4 separates these: the consensus is the belief and the venue
    # price is what you can be filled at, and they are usually different
    # numbers at different books. Pairing one side of the venue with the other
    # side of the consensus de-vigs a market that does not exist, and the error
    # runs one way, so it biases every row rather than adding noise. Falls back
    # to the venue pair when no consensus was supplied.
    consensus_price: float | None = None
    consensus_other_price: float | None = None
    venue: str = "book_default"
    book_set: tuple[str, ...] = ()
    starts_at: str | None = None
    price_source: str = "capture"
    price_kind: str = "american"
    push: float = 0.0

    @property
    def gradeable(self) -> bool:
        return self.price_source in GRADEABLE_PRICE_SOURCES

    @property
    def baseline_pair(self) -> tuple[float, float | None]:
        """The two prices `p_baseline` is de-vigged from."""
        if self.consensus_price is not None:
            return self.consensus_price, self.consensus_other_price
        return self.price, self.other_price


@dataclass(frozen=True)
class Blend:
    """The posterior and the arithmetic behind it, for one de-vig method."""

    method: str
    p_baseline: float
    p_model: float | None
    w_mod: float
    p_post: float
    kept: tuple[StageA, ...] = ()
    dropped: tuple[StageA, ...] = ()

    @property
    def opposed(self) -> bool:
        return any(s.side == "against" for s in self.kept)


@dataclass
class Decision:
    """One candidate, decided. This is what becomes a ledger row."""

    quote: Quote
    decision: str
    reason_codes: list[str] = field(default_factory=list)
    p_baseline: float | None = None
    devig: dict[str, float] = field(default_factory=dict)
    p_model: float | None = None
    w_mod: float = 0.0
    p_post: float | None = None
    ev: float = 0.0
    f_full: float = 0.0
    c: int = 1
    portfolio_scale: float = 1.0
    stake_units: float = 0.0
    max_playable_price: float | None = None
    signals: list[StageA] = field(default_factory=list)

    def add(self, code: str) -> None:
        if code not in self.reason_codes:
            self.reason_codes.append(code)


def dedupe_families(signals: Sequence[StageA]) -> tuple[list[StageA], list[StageA]]:
    """§6.5: one family, one piece of evidence.

    Keyed by (family, side) rather than family alone. Keying on family alone
    would let a supporting and an opposing fire in the same family cancel
    silently, by dropping one of them before §6.6 ever sees it, which makes the
    opposing rule unreachable in exactly the case it exists for. Ties break on
    system id so the same inputs always produce the same row.
    """
    best: dict[tuple[str, str], StageA] = {}
    for s in sorted(signals, key=lambda s: (-s.w_sig, s.system_id)):
        if not s.passed:
            continue
        best.setdefault((s.family, s.side), s)
    kept = sorted(best.values(), key=lambda s: s.system_id)
    kept_ids = {s.system_id for s in kept}
    dropped = [s for s in signals if s.passed and s.system_id not in kept_ids]
    return kept, dropped


def blend(
    p_baseline: float,
    signals: Sequence[StageA],
    *,
    cfg: Config,
    method: str = "proportional",
) -> Blend:
    """The posterior: market prior at `w0`, evidence at its pooled weight."""
    kept, dropped = dedupe_families(signals)
    w_mod = sum(s.w_sig for s in kept)
    if w_mod <= 0.0:
        return Blend(method, p_baseline, None, 0.0, p_baseline, tuple(kept), tuple(dropped))

    total = 0.0
    for s in kept:
        # §6.6: a signal fired on the other side is evidence against this one.
        p = s.p_signal if s.side == "for" else 1.0 - (s.p_signal or 0.0)
        total += s.w_sig * p
    p_model = total / w_mod
    p_post = (cfg.w0 * p_baseline + w_mod * p_model) / (cfg.w0 + w_mod)
    return Blend(method, p_baseline, p_model, w_mod, p_post, tuple(kept), tuple(dropped))


def baselines(quote: Quote) -> dict[str, float]:
    """Proportional and power de-vig of the two-way market (§6.10).

    With only one side quoted there is nothing to de-vig, so the raw implied
    probability is returned under both keys and the sensitivity check becomes a
    no-op rather than a false pass.
    """
    mine, theirs = quote.baseline_pair
    if theirs is None:
        p = american_to_probability(mine)
        return {"proportional": p, "power": p}
    pair = [mine, theirs]
    return {
        "proportional": devig_multiplicative(pair)[0],
        "power": devig_power(pair)[0],
    }


def decide(
    quote: Quote,
    signals: Sequence[tuple[SystemRecord, str]] = (),
    *,
    cfg: Config,
    c: int = 1,
    venue_config: Mapping[str, Any] | None = None,
    stale_sources: Sequence[str] = (),
    integrity_flags: Sequence[str] = (),
) -> Decision:
    """The single decision path. Scan, backtest and dashboard all call this.

    Order matters. Freshness and integrity come first, because a decision made
    on a stale or self-inconsistent market is not a decision, it is a number
    with a timestamp. Then de-vig sensitivity, then the posterior, then the
    gate, then sizing.
    """
    out = Decision(quote=quote, decision=reasons.PASS, c=c)
    venue_cfg = venue_config if venue_config is not None else cfg.venue(quote.venue)

    # 1. Freshness and integrity (§6.9). A stale source is not a PASS: it is an
    # absence of information, and the row says so with stake zero.
    if stale_sources:
        out.decision = reasons.STALE_DECISION
        out.add(reasons.STALE)
        return out
    for flag in integrity_flags:
        out.add(flag)
    if reasons.UNMAPPED in out.reason_codes:
        out.decision = reasons.PASS
        return out

    if not quote.gradeable:
        # Still decided and still logged; just never counted toward Gate 2.
        out.add(reasons.UNGRADEABLE_PRICE)

    # 2. De-vig, both methods.
    devig = baselines(quote)
    out.devig = dict(devig)
    mine, theirs = quote.baseline_pair
    if theirs is not None:
        implied = american_to_probability(mine) + american_to_probability(theirs)
        lo, hi = cfg.implied_sum_band
        if not lo - 1e-9 <= implied <= hi + 1e-9:
            out.add(reasons.DEVIG_IMPLAUSIBLE)
            out.decision = reasons.PASS
            return out

    price = executable(quote.price, kind=quote.price_kind,
                       venue=quote.venue, venue_config=venue_cfg)

    # 3. Stage A and Stage B under each de-vig, so §6.10 can compare decisions.
    results: dict[str, tuple[list, Blend, Any]] = {}
    for method, p_base in devig.items():
        stage = run_stage_a(list(signals), p_nv_for=p_base, cfg=cfg)
        b = blend(p_base, stage, cfg=cfg, method=method)
        results[method] = (stage, b, decide_at_price(b.p_post, price, push=quote.push))

    stage_primary, primary, primary_price = results["proportional"]
    _, other, other_price_decision = results["power"]

    out.p_baseline = primary.p_baseline
    out.p_model = primary.p_model
    out.w_mod = primary.w_mod
    out.p_post = primary.p_post
    out.ev = primary_price.ev
    out.f_full = primary_price.f_full
    # Every Stage A verdict belongs on the row, including the ones that did not
    # survive: a signal excluded for AGGREGATE_ONLY is information about the
    # system, and a row that silently omits it looks like a system that never
    # fired.
    out.signals = list(stage_primary)
    if primary.dropped:
        out.add(reasons.FAMILY_DUP)
    for sig in stage_primary:
        for flag in sig.flags:
            out.add(flag)

    # 4. §6.10: if the two de-vigs disagree about whether to bet, do not bet.
    if primary_price.playable != other_price_decision.playable:
        out.add(reasons.DEVIG_SENSITIVE)
        out.decision = reasons.PASS
        return out

    if primary.w_mod <= 0.0:
        out.add(reasons.NO_EVIDENCE)

    # 5. Gate once (Law 5).
    if not primary_price.playable:
        if primary.opposed:
            out.decision = reasons.NO_BET
            out.add(reasons.OPPOSED)
        else:
            out.decision = reasons.PASS
            out.add(reasons.NEG_EV)
        return out

    # 6. Size it.
    stake: Stake = size(
        primary_price.f_full,
        cfg=cfg,
        c=c,
        min_stake_units=float(venue_cfg.get("min_stake_units", 0.0)),
    )
    out.portfolio_scale = stake.portfolio_scale
    out.stake_units = stake.units
    for flag in stake.flags:
        out.add(flag)
    out.max_playable_price = max_playable_price(
        primary.p_post,
        venue=quote.venue,
        venue_config=venue_cfg,
        push=quote.push,
        grid=cfg.price_grid,
    )

    out.decision = reasons.PASS if stake.is_zero else reasons.BET
    return out
