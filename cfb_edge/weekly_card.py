"""The one weekly card: every game, one disposition, every number traced.

``CFB_EDGE_WEEKLY_CARD_V1`` is the single human-facing weekly output of CFB
Edge (DECISIONS.md D39 and D40). It is a pure function of committed configuration,
evidence-plane artifacts and an explicit ``--as-of`` time, so a scheduled run
and an interactive run cannot disagree, and a card can be rebuilt byte for
byte from the files it cites.

It decides nothing on its own. Every side of every game goes through the one
decision path, :func:`cfb_edge.engine.decide`, and the card only classifies
what came back:

``BET``
    The engine returned BET with a stake, the committed delivery authority
    allows paper delivery, the executable quote is fresh, and no critical gate
    failed. With the authority as registered today this cannot happen, and the
    card says so rather than inventing a way round it.
``LEAN``
    A price edge exists under the calibrated market posterior (a book beats
    the sharp reference's no-vig price at the *same* number, under both
    de-vig methods), but it is blocked: no registered evidence (D15), no
    delivery authority, or a stale quote. Zero stake. A watchlist, logged so
    its closing-line value can be measured.
``PASS``
    Everything else, with the reason codes that say why.

Confidence is the calibrated posterior probability that the pick covers at its
executable number. With no registered evidence the posterior *is* the sharp
market's no-vig probability (the projection's weight is zero: its incremental
coefficient over the close is -0.012 at t = -0.43 across 9,113 walk-forward
games, ``docs/research/walkforward_2026.json``). A spread pick therefore sits
near 50%, and the card prints what the posterior says instead of rounding it
up because several signals happen to agree.

Rank is a heuristic, stated as one: positive conservative EV times an evidence
factor times an execution factor. It orders a watchlist. It is not validated.

Standard library only. No network: every input is a file.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import teams
from .distribution import cover_probability, margin_pmf, sigma_for_total, REFERENCE_TOTAL
from .engine import config as engine_config
from .engine import reasons
from .engine.posterior import Quote, decide
from .engine.pricing import (
    american_from_probability,
    decide_at_price,
    executable,
    max_playable_price,
)
from .engine.version import ENGINE_VERSION, spec_hash
from .market import american_to_probability

CONTRACT = "CFB_EDGE_WEEKLY_CARD_V1"
CARD_VERSION = "weekly-card-1"

BET, LEAN, PASS = "BET", "LEAN", "PASS"
GATE_PASS, GATE_DEGRADED, GATE_FAIL, GATE_NA = (
    "PASS", "DEGRADED", "FAIL", "NOT_APPLICABLE")

# Card-level reason codes. Engine codes pass through unchanged.
NOT_ON_BOARD = "NOT_ON_BOARD"
NO_REFERENCE = "NO_REFERENCE"
AUTHORITY_BLOCKED = "AUTHORITY_BLOCKED"
QUOTE_STALE = "QUOTE_STALE"
NO_PRICE_EDGE = "NO_PRICE_EDGE"
STARTED = "STARTED"

# S03_M1's registered market-quality minimum (config/s03_m1.json): three fresh
# books at the reference's exact number. Read from the frozen config when it is
# supplied; this is only the fallback.
MIN_SAME_LINE_BOOKS = 3

# A board is matched to the slate by team pair AND kickoff. One hour is the
# tolerance `active_market_state` already uses for the same join.
KICKOFF_TOLERANCE_SECONDS = 3600

# Heuristic ranking factors (not validated; printed on the card as such).
EVIDENCE_FACTOR = {GATE_PASS: 1.0, GATE_DEGRADED: 0.85, GATE_FAIL: 0.5, GATE_NA: 1.0}
EXECUTION_HALF_LIFE_HOURS = 4.0

ROW_GATES = ("marketCapture", "execution", "openProvenance", "informationState",
             "qbContinuity", "epa", "weather", "cohortIdentity")


# ---------------------------------------------------------------------------
# small helpers

def _instant(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 10:
        text += "T00:00:00+00:00"
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _iso(when: datetime | None) -> str | None:
    return None if when is None else when.astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z")


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def load_json(path: str | Path | None) -> Any:
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    opener = gzip.open if p.suffix == ".gz" else open
    with opener(p, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def sha256_file(path: str | Path | None) -> str | None:
    if path is None or not Path(path).exists():
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _round(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(float(x), nd)


# ---------------------------------------------------------------------------
# inputs

@dataclass(frozen=True)
class SlateGame:
    game: str
    away: str
    home: str
    projected_margin: float | None
    kickoff: datetime | None


def read_slate(path: str | Path) -> list[SlateGame]:
    out: list[SlateGame] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            game = (r.get("game") or "").strip()
            if "@" not in game:
                continue
            away, home = (x.strip() for x in game.split("@", 1))
            out.append(SlateGame(game, away, home, _num(r.get("projected_margin")),
                                 _instant(r.get("kickoff"))))
    return out


@dataclass
class BookSpread:
    book: str
    home_line: float
    home_price: float
    away_line: float
    away_price: float
    last_update: datetime | None


@dataclass
class BoardEvent:
    away_raw: str
    home_raw: str
    commence: datetime | None
    books: dict[str, BookSpread] = field(default_factory=dict)


def read_board(payload: Mapping[str, Any] | None) -> tuple[list[BoardEvent], datetime | None]:
    """The Odds API spreads payload as archived on the evidence plane."""
    if not payload:
        return [], None
    events: list[BoardEvent] = []
    for ev in payload.get("events") or []:
        home_raw, away_raw = str(ev.get("home") or ""), str(ev.get("away") or "")
        e = BoardEvent(away_raw, home_raw, _instant(ev.get("commenceTime")))
        for bm in ev.get("bookmakers") or []:
            key = str(bm.get("key") or "").strip().lower()
            for mk in bm.get("markets") or []:
                if mk.get("key") != "spreads":
                    continue
                outs = {str(o.get("name")): o for o in mk.get("outcomes") or []}
                h, a = outs.get(home_raw), outs.get(away_raw)
                if not h or not a:
                    continue
                hl, hp = _num(h.get("point")), _num(h.get("price"))
                al, ap = _num(a.get("point")), _num(a.get("price"))
                if None in (hl, hp, al, ap):
                    continue
                e.books[key] = BookSpread(key, hl, hp, al, ap, _instant(mk.get("lastUpdate")))
        events.append(e)
    return events, _instant(payload.get("fetched_at"))


def _name_candidates(raw: str, known: set[str]) -> set[str]:
    """Every slate team a provider label could name: exact, alias, or the label
    with one to three trailing mascot words removed. Never fuzzy."""
    out: set[str] = set()
    direct = teams.resolve(raw, known)
    if direct:
        out.add(direct)
    words = raw.split()
    for cut in (1, 2, 3):
        if len(words) > cut:
            r = teams.resolve(" ".join(words[:-cut]), known)
            if r:
                out.add(r)
    return out


def map_board(events: Sequence[BoardEvent], slate: Sequence[SlateGame]
              ) -> tuple[dict[str, BoardEvent], list[str]]:
    """Join board events to slate games by team pair *and* kickoff.

    Mascot stripping alone can turn "Georgia State Panthers" into "Georgia".
    Requiring the resolved pair to be an actual slate fixture whose kickoff
    agrees within the tolerance makes that mistake unreachable, and anything
    ambiguous is left unmapped rather than guessed.
    """
    known = {g.away for g in slate} | {g.home for g in slate}
    by_pair = {(g.away, g.home): g for g in slate}
    mapped: dict[str, BoardEvent] = {}
    unmapped: list[str] = []
    for ev in events:
        hits = []
        for a in _name_candidates(ev.away_raw, known):
            for h in _name_candidates(ev.home_raw, known):
                g = by_pair.get((a, h))
                if g is None:
                    continue
                if g.kickoff and ev.commence:
                    if abs((g.kickoff - ev.commence).total_seconds()) > KICKOFF_TOLERANCE_SECONDS:
                        continue
                hits.append(g)
        if len(hits) == 1 and hits[0].game not in mapped:
            mapped[hits[0].game] = ev
        else:
            unmapped.append(f"{ev.away_raw} @ {ev.home_raw}")
    return mapped, sorted(unmapped)


def kalshi_latest(log_path: str | Path | None) -> dict[str, dict[str, Any]]:
    """Latest Kalshi-derived home line per game from the append-only log."""
    out: dict[str, dict[str, Any]] = {}
    if log_path is None or not Path(log_path).exists():
        return out
    with gzip.open(log_path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            for q in rec.get("quotes") or []:
                if q.get("book") != "kalshi" or q.get("line") is None:
                    continue
                seen = _instant(q.get("seen_at") or rec.get("polled_at"))
                prev = out.get(q.get("game"))
                if prev is None or (seen and seen >= prev["_seen"]):
                    out[str(q.get("game"))] = {"line": float(q["line"]), "_seen": seen,
                                               "eventTicker": q.get("event_ticker")}
    return out


def index_rows(rows: Iterable[Mapping[str, Any]] | None, key: str = "game") -> dict[str, Mapping[str, Any]]:
    return {str(r.get(key)): r for r in (rows or []) if r.get(key)}


# ---------------------------------------------------------------------------
# market evaluation

def _reference(event: BoardEvent) -> tuple[str, float, float, float] | None:
    """(name, home_line, home_price, away_price) of the sharp reference.

    Pinnacle when it quotes the game. Otherwise the most common home number
    among the books, with the median of each side's price at that number:
    the US-median fallback D14 describes, recorded on the row, never blended
    in silently.
    """
    pin = event.books.get("pinnacle")
    if pin is not None:
        return "pinnacle", pin.home_line, pin.home_price, pin.away_price
    by_line: dict[float, list[BookSpread]] = {}
    for b in event.books.values():
        by_line.setdefault(b.home_line, []).append(b)
    if not by_line:
        return None
    line, books = max(by_line.items(), key=lambda kv: (len(kv[1]), -abs(kv[0])))
    if len(books) < 2:
        return None
    hp = sorted(b.home_price for b in books)[len(books) // 2]
    ap = sorted(b.away_price for b in books)[len(books) // 2]
    return "us_median", line, hp, ap


def _side_pmf(ref_home_line: float, side: str) -> dict[int, float]:
    """The fitted margin distribution for the backed side, centred on the
    sharp reference number (sigma at the reference total: D40 records that the
    slate's total column is a constant, so no game-specific total is used)."""
    mean_home = -ref_home_line
    return margin_pmf(mean_home if side == "home" else -mean_home,
                      sigma_for_total(REFERENCE_TOTAL))


def _push_probability(home_line: float) -> float:
    """Chance a whole-number spread lands exactly on the number."""
    if abs(home_line - round(home_line)) > 1e-9:
        return 0.0
    return float(cover_probability(_side_pmf(home_line, "home"), home_line).push)


def evaluate_side(*, game: SlateGame, side: str, event: BoardEvent, ref: tuple,
                  cfg: engine_config.Config) -> dict[str, Any]:
    """Best same-number price for one side against the reference, through the
    one decision engine."""
    ref_name, ref_line, ref_hp, ref_ap = ref
    team = game.home if side == "home" else game.away
    line_for = (lambda b: b.home_line) if side == "home" else (lambda b: b.away_line)
    price_for = (lambda b: b.home_price) if side == "home" else (lambda b: b.away_price)
    other_for = (lambda b: b.away_price) if side == "home" else (lambda b: b.home_price)
    ref_side_line = ref_line if side == "home" else -ref_line
    ref_side_price = ref_hp if side == "home" else ref_ap
    ref_other_price = ref_ap if side == "home" else ref_hp

    same = [b for b in event.books.values()
            if b.book != ref_name and abs(line_for(b) - ref_side_line) < 1e-9]
    other_lines = sorted({line_for(b) for b in event.books.values()
                          if b.book != ref_name and abs(line_for(b) - ref_side_line) >= 1e-9})
    out: dict[str, Any] = {
        "side": side, "team": team, "referenceLine": ref_side_line,
        "sameLineBooks": sorted(b.book for b in same), "otherLines": other_lines,
    }
    if not same:
        out.update({"decision": reasons.PASS, "reasonCodes": [reasons.LINE_MISMATCH]})
        return out

    best = max(same, key=lambda b: executable(price_for(b)).payout)
    push = _push_probability(ref_line)
    quote = Quote(
        event_id=game.game, sport="americanfootball_ncaaf", market="spreads",
        side=team, line=ref_side_line, price=price_for(best),
        other_price=other_for(best), consensus_price=ref_side_price,
        consensus_other_price=ref_other_price, venue=best.book,
        book_set=tuple(sorted(event.books)),
        starts_at=_iso(game.kickoff), price_source="capture", push=push,
    )
    d = decide(quote, (), cfg=cfg)
    p_prop = d.devig.get("proportional")
    p_pow = d.devig.get("power")
    ex = executable(price_for(best), venue=best.book, venue_config=cfg.venue(best.book))
    ev_pow = decide_at_price(p_pow, ex, push=push).ev if p_pow is not None else None
    ev_prop = d.ev if p_prop is not None else None
    evs = [x for x in (ev_prop, ev_pow) if x is not None]
    out.update({
        "bestBook": best.book, "line": ref_side_line, "price": price_for(best),
        "bestBookUpdatedAt": _iso(best.last_update),
        "pushProbability": _round(push),
        "pFairProportional": _round(p_prop), "pFairPower": _round(p_pow),
        "pPost": _round(d.p_post), "wMod": d.w_mod,
        "evProportional": _round(ev_prop), "evPower": _round(ev_pow),
        "evConservative": _round(min(evs)) if evs else None,
        "fairAmerican": (round(american_from_probability(d.p_post))
                         if d.p_post and 0 < d.p_post < 1 else None),
        # §6.3: the worst price that still clears, swept, never inverted.
        "minimumAcceptablePrice": (max_playable_price(
            d.p_post, venue=best.book, venue_config=cfg.venue(best.book), push=push,
            grid=cfg.price_grid) if d.p_post else None),
        "decision": d.decision, "reasonCodes": list(d.reason_codes),
        "stakeUnits": d.stake_units,
    })
    return out


# ---------------------------------------------------------------------------
# gates

def _gate(state: str, detail: str, **evidence: Any) -> dict[str, Any]:
    g = {"state": state, "detail": detail}
    if evidence:
        g["evidence"] = evidence
    return g


def row_gates(*, game: SlateGame, event: BoardEvent | None, ref: tuple | None,
              as_of: datetime, board_fetched: datetime | None, br2: Mapping | None,
              recovered: Mapping | None, prospective: Mapping | None,
              in_cohort: bool, max_quote_age: float, odds_max_age: float,
              min_books: int) -> dict[str, dict[str, Any]]:
    g: dict[str, dict[str, Any]] = {}
    # market capture
    if event is None:
        g["marketCapture"] = _gate(GATE_FAIL, "Game not on the captured sportsbook board.")
    elif ref is None:
        g["marketCapture"] = _gate(GATE_FAIL, "No sharp reference and no two-book fallback.")
    else:
        same = sum(1 for b in event.books.values() if abs(b.home_line - ref[1]) < 1e-9)
        state = GATE_PASS if (ref[0] == "pinnacle" and same >= min_books) else GATE_DEGRADED
        g["marketCapture"] = _gate(
            state, f"{same} books at the reference number {ref[1]:+g} "
                   f"(reference {ref[0]}; minimum {min_books}).",
            books=sorted(event.books), reference=ref[0], sameLineBooks=same)
    # execution freshness
    if board_fetched is None:
        g["execution"] = _gate(GATE_FAIL, "No board timestamp.")
    else:
        age = (as_of - board_fetched).total_seconds()
        if age <= max_quote_age:
            state = GATE_PASS
        elif age <= odds_max_age:
            state = GATE_DEGRADED
        else:
            state = GATE_FAIL
        g["execution"] = _gate(
            state, f"Quotes {age / 3600:.1f} h old at decision time; executable "
                   f"freshness limit {max_quote_age / 60:.0f} min.",
            boardFetchedAt=_iso(board_fetched), ageSeconds=round(age))
    # opening provenance
    if recovered is not None:
        g["openProvenance"] = _gate(
            GATE_PASS, f"Venue-proven open {float(recovered['openingHomeLine']):+.1f} "
                       f"(lag {int(recovered.get('openLagSeconds') or 0)} s, recovered pre-outcome, "
                       f"frozen in config/week6_clv_freeze.json).",
            openingHomeLine=recovered.get("openingHomeLine"),
            venueOpenTime=recovered.get("venueOpenTime"))
    elif prospective is not None and prospective.get("state"):
        g["openProvenance"] = _gate(
            GATE_FAIL, f"Prospective open state {prospective.get('state')}; "
                       f"first live sighting was not inside the 900 s window.",
            openState=prospective.get("state"))
    else:
        g["openProvenance"] = _gate(GATE_FAIL, "No audit-grade opening line for this game.")
    # information state
    if br2 is not None:
        ok = bool(br2.get("pregameEligible"))
        decision_time = _instant(br2.get("decisionTime"))
        future = decision_time is not None and decision_time > as_of
        state = GATE_PASS if ok and not future else GATE_FAIL
        g["informationState"] = _gate(
            state, "Point-in-time features precede the decision and kickoff."
            if state == GATE_PASS else "Feature row not pre-game eligible or post-dates the card.",
            decisionTime=br2.get("decisionTime"), rowSha256=br2.get("rowSha256"))
    else:
        g["informationState"] = _gate(
            GATE_DEGRADED, "No point-in-time feature row; card uses market data only.")
    feats = (br2 or {}).get("features") or {}
    # QB continuity
    qb = _num(feats.get("qbContinuityDiff"))
    g["qbContinuity"] = (_gate(GATE_PASS, f"QB continuity difference {qb:+.2f} "
                                          f"(home minus away, last-3-game attempt share).",
                               qbContinuityDiff=qb)
                         if qb is not None else
                         _gate(GATE_DEGRADED, "QB continuity not captured for this game."))
    epa = _num(feats.get("epaDiff"))
    g["epa"] = _gate(
        GATE_DEGRADED,
        (f"Opponent-adjusted EPA difference {epa:+.3f} from the versioned cfbfastR "
         f"fallback; preferred CFBD WEPA is authorization-blocked.")
        if epa is not None else "Opponent-adjusted EPA not captured.",
        epaDiff=epa)
    wind = _num(feats.get("windMph"))
    if wind is None:
        g["weather"] = _gate(GATE_DEGRADED, "No pre-game forecast captured.")
    else:
        snap = _instant((br2 or {}).get("snapshotAt"))
        lead = ((game.kickoff - snap).total_seconds() / 3600.0
                if (snap and game.kickoff) else None)
        state = GATE_PASS if (lead is not None and lead <= 72) else GATE_DEGRADED
        g["weather"] = _gate(state, f"Forecast wind {wind:.0f} mph"
                             + (f", captured {lead:.0f} h before kickoff." if lead is not None else "."),
                             windMph=wind, forecastLeadHours=_round(lead, 1))
    g["cohortIdentity"] = (_gate(GATE_PASS, "Inside the frozen cohort's game window.")
                           if in_cohort else
                           _gate(GATE_DEGRADED, "Outside the frozen cohort's game window "
                                                "(a Thursday kickoff the window omits); market data only."))
    return g


# ---------------------------------------------------------------------------
# the card

def _ev_text(ev: float | None) -> str:
    return "n/a" if ev is None else f"{ev * 100:+.1f}%"


def evaluate_game(*, game: SlateGame, event: BoardEvent | None, board_fetched: datetime | None,
                  as_of: datetime, cfg: engine_config.Config, authority: Mapping[str, Any],
                  br2: Mapping | None, recovered: Mapping | None, prospective: Mapping | None,
                  shadow: Mapping | None, kalshi: Mapping | None, in_cohort: bool,
                  min_books: int) -> dict[str, Any]:
    max_quote_age = float((authority.get("requirements") or {}).get("maximumQuoteAgeSeconds", 900))
    odds_max_age = float(cfg.max_age_seconds("odds") or 5400)
    ref = _reference(event) if event is not None else None
    gates = row_gates(game=game, event=event, ref=ref, as_of=as_of, board_fetched=board_fetched,
                      br2=br2, recovered=recovered, prospective=prospective, in_cohort=in_cohort,
                      max_quote_age=max_quote_age, odds_max_age=odds_max_age, min_books=min_books)
    row: dict[str, Any] = {
        "game": game.game, "away": game.away, "home": game.home,
        "kickoff": _iso(game.kickoff),
        "canonicalGameId": (br2 or {}).get("canonicalGameId") or (recovered or {}).get("canonicalGameId"),
        "inFrozenCohort": in_cohort,
        "projection": {
            "homeMargin": game.projected_margin, "weight": 0.0,
            "note": "Shown for context. Weight zero: no incremental information over "
                    "the closing line in walk-forward testing.",
        },
        "gates": gates,
    }
    codes: list[str] = []
    started = game.kickoff is not None and game.kickoff <= as_of
    if event is None or ref is None:
        codes.append(NOT_ON_BOARD if event is None else NO_REFERENCE)
        row.update({"market": None, "sides": [],
                    "decision": {"disposition": PASS, "pick": None, "reasonCodes": codes,
                                 "confidence": None, "edge": None}})
        row["kalshi"] = _kalshi_block(kalshi)
        row["shadow"] = _shadow_block(shadow)
        row["projection"]["gapVsReference"] = None
        row["drivers"], row["risks"] = [], ["No captured price for this game."]
        return row

    ref_name, ref_line, ref_hp, ref_ap = ref
    sides = [evaluate_side(game=game, side=s, event=event, ref=ref, cfg=cfg) for s in ("home", "away")]
    row["market"] = {
        "reference": ref_name, "referenceHomeLine": ref_line,
        "referenceHomePrice": ref_hp, "referenceAwayPrice": ref_ap,
        "noVigHome": _round(next((s.get("pFairProportional") for s in sides if s["side"] == "home"), None)),
        "boardFetchedAt": _iso(board_fetched),
        "books": {k: {"homeLine": b.home_line, "homePrice": b.home_price,
                      "awayLine": b.away_line, "awayPrice": b.away_price,
                      "updatedAt": _iso(b.last_update)}
                  for k, b in sorted(event.books.items())},
    }
    row["kalshi"] = _kalshi_block(kalshi)
    row["shadow"] = _shadow_block(shadow)
    gap = (None if game.projected_margin is None
           else round(game.projected_margin - (-ref_line), 2))
    row["projection"]["gapVsReference"] = gap
    row["sides"] = sides

    # Disposition.
    exec_state = gates["execution"]["state"]
    authority_ok = bool(authority.get("allowPaperDelivery"))
    candidates = [s for s in sides if s.get("evConservative") is not None]
    best = max(candidates, key=lambda s: s["evConservative"], default=None)
    disposition, pick = PASS, None
    if started:
        codes.append(STARTED)
    elif best is not None:
        both_positive = (best.get("evProportional") or 0) > 0 and (best.get("evPower") or 0) > 0
        engine_bet = best.get("decision") == reasons.BET and (best.get("stakeUnits") or 0) > 0
        critical_fail = any(gates[k]["state"] == GATE_FAIL
                            for k in ("marketCapture", "informationState"))
        if engine_bet and authority_ok and exec_state == GATE_PASS and not critical_fail:
            disposition = BET
        elif both_positive and reasons.DEVIG_SENSITIVE not in best.get("reasonCodes", []):
            disposition = LEAN
        codes.extend(c for c in best.get("reasonCodes", []) if c not in codes)
        if disposition != BET:
            if not authority_ok:
                codes.append(AUTHORITY_BLOCKED)
            if exec_state != GATE_PASS:
                codes.append(QUOTE_STALE)
            if disposition == PASS and not both_positive:
                codes.append(NO_PRICE_EDGE)
        if disposition in (BET, LEAN):
            pick = {
                "side": best["side"], "team": best["team"], "market": "spread",
                "line": best["line"], "price": best["price"], "book": best["bestBook"],
                "fairAmerican": best.get("fairAmerican"),
                "minimumAcceptablePrice": best.get("minimumAcceptablePrice"),
                "halfPointWorse": _half_point_worse(best, ref_line),
            }
    else:
        codes.append(reasons.LINE_MISMATCH)

    conf = best.get("pPost") if (pick and best) else None
    row["decision"] = {
        "disposition": disposition, "pick": pick, "reasonCodes": codes,
        "confidence": conf, "edge": best.get("evConservative") if best else None,
        "engineDecision": best.get("decision") if best else None,
        "engineReasonCodes": best.get("reasonCodes") if best else [],
        "stakeUnits": 0.0 if disposition != BET else best.get("stakeUnits", 0.0),
    }
    row["drivers"], row["risks"] = _narrative(row, best, ref)
    return row


def _half_point_worse(side: Mapping[str, Any], ref_home_line: float) -> dict[str, Any] | None:
    """What the pick is worth if its number moves half a point against it,
    using the fitted margin distribution for the local value of a half point."""
    p = side.get("pPost")
    if p is None:
        return None
    pmf = _side_pmf(ref_home_line, side["side"])
    line = float(side["line"])
    here = cover_probability(pmf, line).win_excluding_push
    worse_line = line - 0.5
    worse = cover_probability(pmf, worse_line)
    p_worse = max(0.0, min(1.0, p - (here - worse.win_excluding_push)))
    ev = decide_at_price(p_worse, executable(side["price"]), push=worse.push).ev
    return {"line": worse_line, "pPost": _round(p_worse), "evAtSamePrice": _round(ev)}


def _kalshi_block(k: Mapping | None) -> dict[str, Any] | None:
    if not k:
        return None
    return {"derivedHomeLine": _round(k.get("line"), 2), "observedAt": _iso(k.get("_seen")),
            "eventTicker": k.get("eventTicker"),
            "note": "50% crossing of the exchange ladder; market context, not an executable price."}


def _shadow_block(s: Mapping | None) -> dict[str, Any] | None:
    if not s:
        return None
    return {k: s.get(k) for k in ("model", "side", "auditGrade", "openingHomeLine",
                                  "projectionGapVsOpen", "status", "exclusions",
                                  "bestConservativeEv", "movementSoFar")}


def _narrative(row: Mapping[str, Any], best: Mapping | None, ref: tuple) -> tuple[list[str], list[str]]:
    drivers: list[str] = []
    risks: list[str] = []
    if best is None:
        return drivers, ["No side has a same-number price to compare with the reference."]
    ref_name, ref_line = ref[0], ref[1]
    side_price = best.get("price")
    fair = best.get("fairAmerican")
    if best.get("evConservative") is not None:
        drivers.append(
            f"{best['bestBook']} {best['team']} {best['line']:+g} at {side_price:+.0f} vs "
            f"{ref_name} no-vig {fair:+d} at the same number: EV {_ev_text(best['evConservative'])} "
            f"(worse of two de-vig methods).")
    n_same = len(best.get("sameLineBooks") or [])
    drivers.append(f"{n_same} book(s) besides the reference quote {best['team']} {best['line']:+g}.")
    gap = row["projection"].get("gapVsReference")
    if gap is not None:
        lean_team = row["home"] if gap > 0 else row["away"]
        drivers.append(f"Projection disagrees by {abs(gap):.1f} pts toward {lean_team} "
                       f"(weight zero; context only).")
    sh = row.get("shadow")
    if sh and sh.get("auditGrade"):
        drivers.append(f"S04_ES2 audit-grade shadow signal on {sh.get('side')} "
                       f"(status {sh.get('status')}).")
    gates = row["gates"]
    if gates["execution"]["state"] != GATE_PASS:
        risks.append(gates["execution"]["detail"] + " Re-price before acting.")
    risks.append("No registered forward evidence that sharp-reference price gaps earn "
                 "closing-line value (D14/D15); the edge rests on the reference being right.")
    qb = (gates["qbContinuity"].get("evidence") or {}).get("qbContinuityDiff")
    if qb is not None and abs(qb) >= 0.5:
        risks.append(f"Large QB-continuity difference ({qb:+.2f}); a starter change the "
                     f"market has not priced would move this number.")
    wind = (gates["weather"].get("evidence") or {}).get("windMph")
    if wind is not None and wind >= 15:
        risks.append(f"Forecast wind {wind:.0f} mph.")
    return drivers, risks


def _evidence_factor(gates: Mapping[str, Mapping[str, Any]]) -> float:
    f = 1.0
    for key in ROW_GATES:
        f *= EVIDENCE_FACTOR.get(gates.get(key, {}).get("state", GATE_NA), 1.0)
    return f


def _execution_factor(gates: Mapping[str, Mapping[str, Any]]) -> float:
    age = ((gates.get("execution") or {}).get("evidence") or {}).get("ageSeconds")
    if age is None:
        return 0.0
    return 0.5 ** (float(age) / 3600.0 / EXECUTION_HALF_LIFE_HOURS)


def rank(rows: list[dict[str, Any]]) -> None:
    """Order BET and LEAN rows by the stated heuristic; mutates in place."""
    scored = []
    for r in rows:
        d = r["decision"]
        if d["disposition"] not in (BET, LEAN) or d.get("edge") is None:
            d["rank"], d["rankScore"] = None, None
            continue
        score = max(0.0, d["edge"]) * _evidence_factor(r["gates"]) * _execution_factor(r["gates"])
        d["rankScore"] = round(score, 8)
        d["rankFactors"] = {"edge": d["edge"], "evidence": round(_evidence_factor(r["gates"]), 4),
                            "execution": round(_execution_factor(r["gates"]), 4)}
        scored.append(r)
    order = sorted(scored, key=lambda r: (0 if r["decision"]["disposition"] == BET else 1,
                                          -r["decision"]["rankScore"], r["game"]))
    for i, r in enumerate(order, 1):
        r["decision"]["rank"] = i


# ---------------------------------------------------------------------------
# system health

def system_health(*, as_of: datetime, rows: Sequence[Mapping[str, Any]], board_fetched: datetime | None,
                  kalshi: Mapping[str, Mapping], capture_status: str | None,
                  prospective_status: Mapping | None, recovered_rows: int, cohort_rows: int,
                  br2_status: Mapping | None, br2_health: Mapping | None,
                  authority: Mapping[str, Any], clv_gate: Mapping | None,
                  grades: Mapping | None, scheduler: Mapping | None,
                  registry_sha: str | None, calibration: Mapping | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(key: str, label: str, state: str, detail: str, **evidence: Any) -> None:
        item = {"key": key, "label": label, "state": state, "detail": detail}
        if evidence:
            item["evidence"] = evidence
        out.append(item)

    n = len(rows)
    on_board = sum(1 for r in rows if r.get("market"))
    age_h = None if board_fetched is None else (as_of - board_fetched).total_seconds() / 3600
    k_seen = [v.get("_seen") for v in kalshi.values() if v.get("_seen")]
    k_age = None if not k_seen else (as_of - max(k_seen)).total_seconds() / 3600
    mc_state = GATE_PASS if (age_h is not None and age_h <= 0.25 and on_board == n) else (
        GATE_FAIL if on_board == 0 else GATE_DEGRADED)
    add("marketCapture", "Market capture", mc_state,
        f"Sportsbook board covers {on_board}/{n} games, fetched "
        f"{'—' if age_h is None else f'{age_h:.1f} h'} before this card; latest Kalshi poll "
        f"{'—' if k_age is None else f'{k_age:.1f} h'} old.",
        boardFetchedAt=_iso(board_fetched), gamesOnBoard=on_board, games=n,
        kalshiGames=len(kalshi), captureStatus=capture_status)

    summ = (prospective_status or {}).get("summary") or {}
    captured = int(summ.get("capturedTrueOpenRows") or 0)
    missed = int(summ.get("missedTrueOpenRows") or 0)
    add("openingProvenance", "Opening provenance",
        GATE_PASS if captured >= cohort_rows and cohort_rows else GATE_DEGRADED,
        f"Prospective true opens captured live: {captured}/{summ.get('slateRows', n)} "
        f"({missed} missed the 900 s window). Venue-proven recovered opens frozen "
        f"for this cohort: {recovered_rows}/{cohort_rows}. Recovery is one-way; no late row is upgraded.",
        capturedTrueOpenRows=captured, missedTrueOpenRows=missed,
        recoveredTrueOpenRows=recovered_rows)

    br2_rows = (br2_status or {}).get("rows") or []
    eligible = sum(1 for r in br2_rows if r.get("pregameEligible"))
    add("informationState", "Information-state integrity",
        GATE_PASS if br2_rows and eligible == len(br2_rows) else GATE_DEGRADED,
        f"{eligible}/{len(br2_rows)} point-in-time rows pre-game eligible; one decision time "
        f"per row, sources precede it; no closing labels in any decision input.",
        br2GeneratedAt=(br2_status or {}).get("generatedAt"))

    cov = ((br2_status or {}).get("summary") or {}).get("featureCoverageRows") or {}
    for key, label, feat, extra in (
            ("qbContinuity", "QB continuity", "qbContinuityDiff",
             "CFB_EDGE_BR2_QB_CONTINUITY_V4 (last-3 completed games, attempt share)."),
            ("opponentAdjustedEpa", "Opponent-adjusted EPA", "epaDiff",
             "Preferred CFBD WEPA is authorization-blocked (HTTP 401); versioned "
             "cfbfastR fallback CFB_EDGE_OA_EPA_V1 in use through the prior week."),
            ("weather", "Weather", "windMph",
             "Open-Meteo pre-game forecast; refresh inside 72 h of kickoff.")):
        c = int(cov.get(feat) or 0)
        add(key, label, GATE_PASS if c == n and key == "qbContinuity" else GATE_DEGRADED,
            f"{c}/{n} slate games covered. {extra}", covered=c, games=n)

    add("injuries", "Injury availability", GATE_FAIL,
        "No injury or availability feed is integrated. The production posterior inherits "
        "whatever the market has priced; a late scratch is a risk for any stale quote.")

    sched_state = (scheduler or {}).get("state") or GATE_DEGRADED
    add("scheduler", "Scheduler", sched_state,
        (scheduler or {}).get("detail") or "Scheduler state not supplied.",
        **({"missed": scheduler.get("missed")} if scheduler and scheduler.get("missed") else {}))

    g_state, g_detail = GATE_DEGRADED, "No grading report supplied."
    if clv_gate is not None or grades is not None:
        verdict = (clv_gate or {}).get("verdict") or "NOT_RUN"
        gradeable = (clv_gate or {}).get("gradeable") or 0
        gsum = (grades or {}).get("summary") or {}
        g_detail = (f"CLV gate {verdict}: {gradeable} gradeable of "
                    f"{(clv_gate or {}).get('observations', 0)} captured rows "
                    f"(null {((clv_gate or {}).get('nullMeanPoints') or 0):+.3f} pts). "
                    f"Shadow signal grades: {gsum.get('gradeableRows', 0)} gradeable of "
                    f"{gsum.get('signalRows', 0)} signal rows. This week's frozen cohort "
                    f"grades after kickoff from the live Kalshi close.")
    add("grading", "Grading", g_state, g_detail)

    add("modelVersion", "Model version", GATE_PASS,
        f"Engine {ENGINE_VERSION}, card {CARD_VERSION}; production posterior = sharp "
        f"no-vig market (w_mod 0). Registry sha256 {str(registry_sha)[:12]}.",
        engineVersion=ENGINE_VERSION, cardVersion=CARD_VERSION, specHash=spec_hash(),
        registrySha256=registry_sha)

    failed = authority.get("failedGates") or []
    add("deliveryAuthority", "Delivery authority",
        GATE_PASS if authority.get("allowPaperDelivery") else GATE_FAIL,
        f"{authority.get('modelId')} {authority.get('status')}; paper delivery "
        f"{'allowed' if authority.get('allowPaperDelivery') else 'blocked'}; failed gates: "
        f"{', '.join(failed) if failed else 'none'}. Snapshot as of {authority.get('asOf')}.",
        failedGates=failed)
    return out


# ---------------------------------------------------------------------------
# computed evidence: CLV gate and scheduler state

def clv_gate_report(opens_csv: str | Path | None) -> dict[str, Any] | None:
    """Run CFB_EDGE_CLV_GATE_V1 over the capture's own rows.

    The capture log carries no closing line in ``opens.csv``, so a row is
    gradeable only once the close-grading workflow has attached one. Rows whose
    provenance is not in ``GRADEABLE`` are refused by the gate itself.
    """
    if opens_csv is None or not Path(opens_csv).exists():
        return None
    from .clv_gate import Observation, evaluate
    obs = []
    with open(opens_csv, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            line = _num(r.get("opening_line"))
            if line is None:
                continue
            obs.append(Observation(game=str(r.get("game")), week=0, open_line=line,
                                   close_line=_num(r.get("close_line")),
                                   source=str(r.get("source") or "")))
    rep = evaluate(obs)
    sources: dict[str, int] = {}
    for o in obs:
        sources[o.source] = sources.get(o.source, 0) + 1
    return {"contract": rep.contract, "verdict": rep.verdict, "observations": rep.observations,
            "gradeable": rep.gradeable, "nullMeanPoints": round(rep.null_mean, 4),
            "minimumObservations": rep.minimum_observations,
            "minimumWeekClusters": rep.minimum_week_clusters,
            "failedGates": list(rep.failed_gates), "sources": dict(sorted(sources.items()))}


def in_season_clv(learning: Mapping[str, Any] | None, *, min_week: int = 3,
                  min_gap: float = 4.0) -> dict[str, Any] | None:
    """Directional CLV the signal has earned this season, from the frozen
    early-season learning artifact (CFBD reference opens; research-grade)."""
    if not learning:
        return None
    import statistics
    rows = [r for r in learning.get("rows") or [] if r.get("directional_clv") is not None]

    def stats(xs: list[float]) -> dict[str, Any]:
        if not xs:
            return {"n": 0, "mean": None, "t": None}
        m = statistics.mean(xs)
        sd = statistics.stdev(xs) if len(xs) > 1 else None
        t = (m / (sd / math.sqrt(len(xs)))) if sd else None
        return {"n": len(xs), "mean": round(m, 4), "sd": None if sd is None else round(sd, 4),
                "t": None if t is None else round(t, 2)}

    weeks: dict[int, list[float]] = {}
    for r in rows:
        weeks.setdefault(int(r.get("week") or 0), []).append(float(r["directional_clv"]))
    rule = [float(r["directional_clv"]) for r in rows
            if int(r.get("week") or 0) >= min_week
            and abs(float(r.get("projection_gap_vs_open") or 0.0)) >= min_gap]
    return {"source": learning.get("contract"), "season": learning.get("season"),
            "openPolicy": (learning.get("sourcePolicy") or {}).get("cfbdLines"),
            "allGames": stats([float(r["directional_clv"]) for r in rows]),
            "byWeek": {str(w): stats(x) for w, x in sorted(weeks.items())},
            "registeredRule": stats(rule)}


def _in_release_window(when: datetime) -> bool:
    from .watch import in_release_window
    return bool(in_release_window(when))


def scheduler_state(*, as_of: datetime, br2_generated: datetime | None,
                    kalshi_last: datetime | None, prospective_window: Mapping | None
                    ) -> dict[str, Any]:
    """Did the scheduled jobs that feed this card actually run when due?

    Two checks the evidence plane can answer on its own. The BR2 active capture
    is registered for 13:30 UTC on weekdays while the cohort is prospective; a
    slot more than two hours past with no newer snapshot is a missed run. The
    Kalshi poller must be fresh inside the release window; outside it, idle is
    the expected state. A green workflow elsewhere is not evidence here.
    """
    missed: list[str] = []
    notes: list[str] = []
    win = prospective_window or {}
    w_start, w_end = _instant(win.get("startsAt")), _instant(win.get("endsAt"))
    slot = as_of.replace(hour=13, minute=30, second=0, microsecond=0)
    while slot.weekday() > 4 or slot > as_of - timedelta(hours=2):
        slot -= timedelta(days=1)
    if w_start and w_end and w_start <= slot <= w_end:
        if br2_generated is None or br2_generated < slot:
            missed.append(f"BR2 active capture due {_iso(slot)} (latest snapshot "
                          f"{_iso(br2_generated) or 'none'})")
    if _in_release_window(as_of):
        if kalshi_last is None or (as_of - kalshi_last).total_seconds() > 1800:
            missed.append(f"Kalshi poll inside the release window (latest {_iso(kalshi_last)})")
    else:
        notes.append("Kalshi release window closed; poller idle as scheduled "
                     f"(latest poll {_iso(kalshi_last)}).")
    state = GATE_PASS if not missed else GATE_DEGRADED
    detail = ("All checked scheduled inputs landed when due. " if not missed else
              "Missed: " + "; ".join(missed) + ". ") + " ".join(notes)
    return {"state": state, "detail": detail.strip(), "missed": missed}


# ---------------------------------------------------------------------------
# build

@dataclass
class Inputs:
    slate: str
    board: str | None = None
    kalshi_log: str | None = None
    prospective: str | None = None
    recovered_freeze: str | None = None
    shadow_decision: str | None = None
    br2_status: str | None = None
    br2_health: str | None = None
    capture_status: str | None = None
    authority: str = "config/delivery_authority.json"
    registry: str = "config/model_registry.json"
    edge_config: str = "config/edge_os.json"
    market_quality: str | None = "config/s03_m1.json"
    cohort: str | None = None
    calibration: str | None = None
    clv_gate: str | None = None
    grades: str | None = None
    scheduler: str | None = None
    opens_csv: str | None = None
    early_season: str | None = None


def _shadow_index(decision: Mapping | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not decision:
        return out
    live: dict[str, list[Mapping]] = {}
    for r in decision.get("inspectedLiveRows") or []:
        live.setdefault(str(r.get("game")), []).append(r)
    for r in decision.get("candidateAuditRows") or []:
        if r.get("source") != "true_open":
            continue
        g = str(r.get("game"))
        rows = live.get(g, [])
        best_ev = max((x.get("conservativeExecutableEv") for x in rows
                       if x.get("conservativeExecutableEv") is not None), default=None)
        status = "QUALIFIED" if any(x.get("status") == "QUALIFIED" for x in rows) else (
            "BLOCKED" if rows else ("NOT_SIGNAL" if not r.get("auditGrade") else "NO_LIVE_PRICE"))
        excl = sorted({e for x in rows for e in (x.get("exclusions") or [])} |
                      set(r.get("exclusions") or []))
        moved = None
        if rows and r.get("openingHomeLine") is not None:
            cur = rows[0].get("pinnacleReferenceLine")
            if cur is not None:
                home = g.split("@", 1)[1].strip()
                side_home = r.get("side") == home
                side_open = float(r["openingHomeLine"]) if side_home else -float(r["openingHomeLine"])
                moved = round(side_open - float(cur), 2)
        out[g] = {"model": decision.get("modelId"), "side": r.get("side"),
                  "auditGrade": bool(r.get("auditGrade")),
                  "openingHomeLine": r.get("openingHomeLine"),
                  "projectionGapVsOpen": _round(r.get("projectionGapVsOpen"), 2),
                  "status": status, "exclusions": excl,
                  "bestConservativeEv": _round(best_ev), "movementSoFar": moved}
    return out


def _display_path(path: str, *, evidence_root: str | None, evidence_revision: str | None) -> str:
    """Name an input the way a reader can find it: ``capture-data@rev:path``
    for evidence-plane files, repository-relative for everything else."""
    if evidence_root:
        root = Path(evidence_root).resolve()
        try:
            rel = Path(path).resolve().relative_to(root)
            rev = (evidence_revision or "")[:7]
            return f"capture-data{'@' + rev if rev else ''}:{rel.as_posix()}"
        except ValueError:
            pass
    return Path(path).as_posix()


def build(inputs: Inputs, *, as_of: datetime, label: Mapping[str, Any] | None = None,
          evidence_root: str | None = None, evidence_revision: str | None = None,
          code_revision: str | None = None) -> dict[str, Any]:
    cfg = engine_config.load(inputs.edge_config)
    authority = load_json(inputs.authority) or {}
    slate = read_slate(inputs.slate)
    board_payload = load_json(inputs.board)
    events, board_fetched = read_board(board_payload)
    mapped, unmapped = map_board(events, slate)
    kalshi = kalshi_latest(inputs.kalshi_log)
    prospective = load_json(inputs.prospective) or {}
    pros_index = index_rows(prospective.get("rows"))
    freeze = load_json(inputs.recovered_freeze) or {}
    recovered_index = index_rows(freeze.get("rows"))
    shadow_index = _shadow_index(load_json(inputs.shadow_decision))
    br2 = load_json(inputs.br2_status) or {}
    br2_index = index_rows(br2.get("rows"))
    cohort = load_json(inputs.cohort) or {}
    window = cohort.get("gameWindow") or {}
    w_start, w_end = _instant(window.get("startsAt")), _instant(window.get("endsAt"))
    mq = load_json(inputs.market_quality) or {}
    min_books = int(mq.get("minimumFreshSameLineBooks") or MIN_SAME_LINE_BOOKS)

    rows = []
    for g in sorted(slate, key=lambda s: (s.kickoff or datetime.max.replace(tzinfo=timezone.utc), s.game)):
        in_cohort = bool(w_start and w_end and g.kickoff and w_start <= g.kickoff <= w_end)
        rows.append(evaluate_game(
            game=g, event=mapped.get(g.game), board_fetched=board_fetched, as_of=as_of, cfg=cfg,
            authority=authority, br2=br2_index.get(g.game), recovered=recovered_index.get(g.game),
            prospective=pros_index.get(g.game), shadow=shadow_index.get(g.game),
            kalshi=kalshi.get(g.game), in_cohort=in_cohort, min_books=min_books))
    rank(rows)

    capture_status = None
    if inputs.capture_status and Path(inputs.capture_status).exists():
        capture_status = Path(inputs.capture_status).read_text(encoding="utf-8").strip()
    clv_report = load_json(inputs.clv_gate) if inputs.clv_gate else clv_gate_report(inputs.opens_csv)
    k_seen = [v.get("_seen") for v in kalshi.values() if v.get("_seen")]
    sched = (load_json(inputs.scheduler) if inputs.scheduler else scheduler_state(
        as_of=as_of, br2_generated=_instant(br2.get("generatedAt")),
        kalshi_last=max(k_seen) if k_seen else None,
        prospective_window=cohort.get("prospectiveWindow")))
    health = system_health(
        as_of=as_of, rows=rows, board_fetched=board_fetched, kalshi=kalshi,
        capture_status=capture_status, prospective_status=prospective,
        recovered_rows=len(recovered_index), cohort_rows=len(br2_index) or len(slate),
        br2_status=br2, br2_health=load_json(inputs.br2_health), authority=authority,
        clv_gate=clv_report, grades=load_json(inputs.grades),
        scheduler=sched, registry_sha=sha256_file(inputs.registry),
        calibration=load_json(inputs.calibration))

    counts = {d: sum(1 for r in rows if r["decision"]["disposition"] == d) for d in (BET, LEAN, PASS)}
    plays = sorted([r for r in rows if r["decision"]["disposition"] in (BET, LEAN)],
                   key=lambda r: r["decision"]["rank"])
    passes = [r for r in rows if r["decision"]["disposition"] == PASS]
    shadow_rows = [r for r in rows if (r.get("shadow") or {}).get("auditGrade")]

    input_paths = {k: v for k, v in vars(inputs).items() if isinstance(v, str)}
    manifest = [{"name": k,
                 "path": _display_path(v, evidence_root=evidence_root, evidence_revision=evidence_revision),
                 "sha256": sha256_file(v)} for k, v in sorted(input_paths.items())]
    verdict = _verdict(counts, authority, health)
    card = {
        "contract": CONTRACT,
        "cardVersion": CARD_VERSION,
        "engineVersion": ENGINE_VERSION,
        "specHash": spec_hash(),
        "codeRevision": code_revision,
        "evidenceRevision": evidence_revision,
        "asOf": _iso(as_of),
        "label": dict(label or {}),
        "summary": {"games": len(rows), **{k.lower(): v for k, v in counts.items()},
                    "verdict": verdict,
                    "boardMapping": {"events": len(events), "mapped": len(mapped),
                                     "unmappedEvents": unmapped}},
        "authority": {k: authority.get(k) for k in (
            "authorityId", "modelId", "modelVersion", "status", "allowPaperDelivery",
            "failedGates", "asOf")},
        "decisionRule": {
            "engine": "cfb_edge.engine.decide (Stage A evidence, Stage B posterior, gate once)",
            "BET": "engine BET with stake AND delivery authority allows paper delivery AND "
                   "quote <= maximumQuoteAgeSeconds AND no critical gate FAIL",
            "LEAN": "same-number price beats the sharp reference's no-vig under both de-vig "
                    "methods, but evidence, authority or freshness blocks it; zero stake",
            "PASS": "everything else, with reason codes",
            "confidence": "calibrated posterior P(pick covers at its number); with no registered "
                          "evidence this equals the sharp no-vig probability",
        },
        "rankingRule": {
            "formula": "max(conservative EV, 0) x evidence factor x execution factor",
            "evidenceFactor": {k: v for k, v in EVIDENCE_FACTOR.items()},
            "executionHalfLifeHours": EXECUTION_HALF_LIFE_HOURS,
            "validated": False,
        },
        "plays": plays,
        "shadowSignals": [{"game": r["game"], **(r.get("shadow") or {})} for r in shadow_rows],
        "passList": passes,
        "systemHealth": health,
        "clvGate": clv_report,
        "inSeasonClv": in_season_clv(load_json(inputs.early_season)),
        "scheduler": sched,
        "inputs": manifest,
    }
    card["cardSha256"] = hashlib.sha256(
        json.dumps({k: v for k, v in card.items() if k != "cardSha256"},
                   sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return card


def _verdict(counts: Mapping[str, int], authority: Mapping, health: Sequence[Mapping]) -> str:
    fails = [h["label"] for h in health if h["state"] == GATE_FAIL]
    if counts.get(BET):
        return f"{counts[BET]} bet(s) clear every gate."
    parts = ["No bet clears the gates this week."]
    if not authority.get("allowPaperDelivery"):
        parts.append("Delivery authority is blocked, so BET is unavailable by construction.")
    if counts.get(LEAN):
        parts.append(f"{counts[LEAN]} lean(s) on price only, zero stake.")
    if fails:
        parts.append("Failing: " + ", ".join(fails) + ".")
    return " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="cfb_edge.weekly_card", description=__doc__.split("\n\n")[0])
    p.add_argument("--slate", required=True)
    p.add_argument("--board")
    p.add_argument("--kalshi-log")
    p.add_argument("--prospective")
    p.add_argument("--recovered-freeze")
    p.add_argument("--shadow-decision")
    p.add_argument("--br2-status")
    p.add_argument("--br2-health")
    p.add_argument("--capture-status")
    p.add_argument("--authority", default="config/delivery_authority.json")
    p.add_argument("--registry", default="config/model_registry.json")
    p.add_argument("--edge-config", default="config/edge_os.json")
    p.add_argument("--market-quality", default="config/s03_m1.json")
    p.add_argument("--cohort")
    p.add_argument("--calibration")
    p.add_argument("--clv-gate")
    p.add_argument("--grades")
    p.add_argument("--scheduler")
    p.add_argument("--opens-csv", help="capture opens.csv for the CLV gate")
    p.add_argument("--early-season", help="early-season learning artifact for in-season CLV")
    p.add_argument("--as-of", required=True, help="decision time, ISO 8601 UTC")
    p.add_argument("--label", default="{}", help="JSON object: season, productWeek, providerWeek, title")
    p.add_argument("--evidence-root", help="checkout of capture-data, for readable input paths")
    p.add_argument("--evidence-revision", help="capture-data commit the inputs were read from")
    p.add_argument("--code-revision", help="main commit this card was built with")
    p.add_argument("--out", required=True)
    p.add_argument("--html", help="also render the card page here")
    a = p.parse_args(argv)
    as_of = _instant(a.as_of)
    if as_of is None:
        p.error(f"cannot read --as-of {a.as_of!r}")
    inputs = Inputs(
        slate=a.slate, board=a.board, kalshi_log=a.kalshi_log, prospective=a.prospective,
        recovered_freeze=a.recovered_freeze, shadow_decision=a.shadow_decision,
        br2_status=a.br2_status, br2_health=a.br2_health, capture_status=a.capture_status,
        authority=a.authority, registry=a.registry, edge_config=a.edge_config,
        market_quality=a.market_quality, cohort=a.cohort, calibration=a.calibration,
        clv_gate=a.clv_gate, grades=a.grades, scheduler=a.scheduler, opens_csv=a.opens_csv,
        early_season=a.early_season)
    card = build(inputs, as_of=as_of, label=json.loads(a.label), evidence_root=a.evidence_root,
                 evidence_revision=a.evidence_revision, code_revision=a.code_revision)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(card, indent=1, sort_keys=True, default=str) + "\n",
                           encoding="utf-8")
    s = card["summary"]
    print(f"{s['games']} games: BET {s['bet']}  LEAN {s['lean']}  PASS {s['pass']}")
    print(s["verdict"])
    print(f"board: {s['boardMapping']['mapped']}/{s['boardMapping']['events']} events mapped")
    print(f"wrote {a.out}")
    if a.html:
        from .card_render import render
        cal = load_json(a.calibration)
        Path(a.html).write_text(render(card, calibration=cal), encoding="utf-8")
        print(f"wrote {a.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
