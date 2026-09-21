"""Market-quality diagnostics for CFB Edge challengers.

This module implements only the artifact logic that can be measured from the
data the repository already captures: exact-line depth, quote freshness,
dispersion, movement state, key-number crossings and conservative executable EV.

It is deliberately non-directional. A good market-quality score cannot create a
football opinion and cannot promote any model. S04_ES1 remains frozen for Week 4;
these diagnostics are attached for research and preregistration of later
challengers.
"""

from __future__ import annotations

import gzip
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .distribution import KEY_BUMPS
from .engine.pricing import decide_at_price, executable
from .market import (
    american_to_probability,
    devig_multiplicative,
    devig_power,
)
from .shop import ShopRow

DEFAULT_KEY_NUMBERS = (3, 7, 10, 14)


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _market(event: Mapping[str, Any], book: str, key: str = "spreads") -> Mapping[str, Any] | None:
    for bookmaker in event.get("bookmakers") or []:
        if bookmaker.get("key") != book:
            continue
        for market in bookmaker.get("markets") or []:
            if market.get("key") == key:
                return market
    return None


def _pair(
    event: Mapping[str, Any],
    book: str,
    side: str,
    *,
    market_key: str = "spreads",
) -> dict[str, Any] | None:
    market = _market(event, book, market_key)
    if market is None:
        return None
    outcomes = list(market.get("outcomes") or [])
    mine = next((o for o in outcomes if o.get("name") == side), None)
    other = next((o for o in outcomes if o.get("name") != side), None)
    if mine is None or mine.get("price") is None or mine.get("point") is None:
        return None
    return {
        "line": float(mine["point"]),
        "price": float(mine["price"]),
        "otherPrice": (
            None if other is None or other.get("price") is None
            else float(other["price"])
        ),
        "lastUpdate": market.get("lastUpdate"),
    }


def _event(pull: Mapping[str, Any], event_id: str) -> Mapping[str, Any] | None:
    return next(
        (e for e in pull.get("events") or [] if str(e.get("id")) == str(event_id)),
        None,
    )


def _age_seconds(last_update: Any, fetched_at: datetime) -> float | None:
    stamp = _time(last_update)
    if stamp is None:
        return None
    return (fetched_at - stamp).total_seconds()


def _fresh(
    state: Mapping[str, Any] | None,
    fetched_at: datetime,
    max_age_seconds: int,
) -> bool:
    if state is None:
        return False
    age = _age_seconds(state.get("lastUpdate"), fetched_at)
    return age is not None and 0.0 <= age <= max_age_seconds


def _same(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= 1e-9


def _opening_side_line(candidate: Mapping[str, Any]) -> float | None:
    opening = candidate.get("openingHomeLine")
    try:
        opening = float(opening)
    except (TypeError, ValueError):
        return None
    game = str(candidate.get("game") or "")
    side = str(candidate.get("side") or "")
    if "@" not in game:
        return None
    _away, home = (x.strip() for x in game.split("@", 1))
    return opening if side == home else -opening


def key_number_crossings(
    opening_side_line: float | None,
    current_side_line: float | None,
    *,
    key_numbers: Sequence[int] = DEFAULT_KEY_NUMBERS,
) -> list[int]:
    """Absolute spread keys crossed between open and current reference.

    This is diagnostic only. Crossing a key number can change contract value,
    but direction is not assumed to be predictive.
    """
    if opening_side_line is None or current_side_line is None:
        return []
    lo = min(abs(opening_side_line), abs(current_side_line))
    hi = max(abs(opening_side_line), abs(current_side_line))
    return [int(k) for k in key_numbers if lo < float(k) <= hi]


def _line_stats(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "max": None, "median": None, "range": None}
    rows = sorted(float(x) for x in values)
    n = len(rows)
    median = rows[n // 2] if n % 2 else 0.5 * (rows[n // 2 - 1] + rows[n // 2])
    return {
        "min": rows[0],
        "max": rows[-1],
        "median": median,
        "range": rows[-1] - rows[0],
    }


def _devig_pair(state: Mapping[str, Any]) -> dict[str, float] | None:
    other = state.get("otherPrice")
    if other is None:
        return None
    prices = [float(state["price"]), float(other)]
    return {
        "proportional": devig_multiplicative(prices)[0],
        "power": devig_power(prices)[0],
    }


def _movement_points(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    event_id: str,
    side: str,
    reference_book: str,
) -> list[tuple[datetime, float]]:
    points: list[tuple[datetime, float]] = []
    for snap in snapshots:
        stamp = _time(snap.get("fetched_at") or snap.get("fetchedAt"))
        if stamp is None:
            continue
        event = _event(snap, event_id)
        if event is None:
            continue
        state = _pair(event, reference_book, side)
        if state is None:
            continue
        points.append((stamp, float(state["line"])))
    points.sort(key=lambda x: x[0])

    # A rerun at the same timestamp should not count as a new market move.
    deduped: list[tuple[datetime, float]] = []
    for item in points:
        if deduped and item[0] == deduped[-1][0]:
            deduped[-1] = item
        else:
            deduped.append(item)
    return deduped


def movement_metrics(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    event_id: str,
    side: str,
    reference_book: str,
) -> dict[str, Any]:
    points = _movement_points(
        snapshots, event_id=event_id, side=side, reference_book=reference_book
    )
    out: dict[str, Any] = {
        "observations": len(points),
        "hoursCovered": None,
        "latestVelocityPointsPerHour": None,
        "previousVelocityPointsPerHour": None,
        "reversal": None,
    }
    if len(points) >= 2:
        hours = max(0.0, (points[-1][0] - points[0][0]).total_seconds() / 3600.0)
        out["hoursCovered"] = hours

        def velocity(a, b) -> float | None:
            dt = (b[0] - a[0]).total_seconds() / 3600.0
            return None if dt <= 0 else (b[1] - a[1]) / dt

        latest = velocity(points[-2], points[-1])
        out["latestVelocityPointsPerHour"] = latest
        if len(points) >= 3:
            previous = velocity(points[-3], points[-2])
            out["previousVelocityPointsPerHour"] = previous
            if latest is not None and previous is not None:
                if abs(latest) <= 1e-12 or abs(previous) <= 1e-12:
                    out["reversal"] = False
                else:
                    out["reversal"] = (latest > 0) != (previous > 0)
    return out


def load_snapshot_history(directory: Path | str) -> list[dict[str, Any]]:
    """Load archived live snapshots in timestamp order.

    Corrupt or non-JSON files fail loudly: a partial movement history should not
    silently look like a quiet market.
    """
    root = Path(directory)
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.iterdir()):
        if path.name.endswith(".json.gz"):
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                rows.append(json.load(fh))
        elif path.suffix == ".json":
            rows.append(json.loads(path.read_text(encoding="utf-8")))
    rows.sort(key=lambda x: str(x.get("fetched_at") or x.get("fetchedAt") or ""))
    return rows


def quality_for_row(
    *,
    row: ShopRow,
    candidate: Mapping[str, Any],
    pull: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
    edge_cfg,
) -> dict[str, Any]:
    """Measure market quality around one S04 candidate without changing it."""
    event = _event(pull, row.event_id)
    reference_book = str(cfg.get("referenceBook") or "pinnacle")
    max_age = int(cfg.get("maximumQuoteAgeSeconds", 900))
    minimum_depth = int(cfg.get("minimumFreshSameLineBooks", 3))
    key_numbers = tuple(int(x) for x in cfg.get("keyNumbers") or DEFAULT_KEY_NUMBERS)

    if event is None:
        return {
            "status": "BLOCKED",
            "reasons": ["EVENT_NOT_IN_PULL"],
            "directionalAuthority": False,
        }

    fetched_at = _time(pull.get("fetched_at") or pull.get("fetchedAt"))
    if fetched_at is None:
        raise ValueError("pull has no parseable fetched_at")

    ref = _pair(event, reference_book, row.side)
    venue = _pair(event, row.venue, row.side)
    ref_line = None if ref is None else float(ref["line"])
    venue_line = None if venue is None else float(venue["line"])

    books = [
        str(b.get("key"))
        for b in event.get("bookmakers") or []
        if b.get("key")
    ]
    states: dict[str, dict[str, Any]] = {}
    for book in books:
        state = _pair(event, book, row.side)
        if state is not None and _fresh(state, fetched_at, max_age):
            states[book] = state

    fresh_lines = [float(s["line"]) for s in states.values()]
    same_line_books = sorted(
        book for book, state in states.items()
        if _same(float(state["line"]), ref_line)
    )
    same_line_states = {
        book: state for book, state in states.items() if book in same_line_books
    }

    probability_by_book: dict[str, dict[str, float]] = {}
    for book, state in same_line_states.items():
        pair = _devig_pair(state)
        if pair is not None:
            probability_by_book[book] = pair

    # How much the available same-line references disagree about the side after
    # removing vig. This is a diagnostic, not a new directional vote.
    prop = [p["proportional"] for p in probability_by_book.values()]
    power = [p["power"] for p in probability_by_book.values()]

    ref_age = None if ref is None else _age_seconds(ref.get("lastUpdate"), fetched_at)
    venue_age = None if venue is None else _age_seconds(venue.get("lastUpdate"), fetched_at)

    reasons: list[str] = []
    if ref is None:
        reasons.append("REFERENCE_QUOTE_MISSING")
    elif not _fresh(ref, fetched_at, max_age):
        reasons.append("REFERENCE_QUOTE_STALE")
    if venue is None:
        reasons.append("VENUE_QUOTE_MISSING")
    elif not _fresh(venue, fetched_at, max_age):
        reasons.append("VENUE_QUOTE_STALE")
    if not _same(venue_line, ref_line):
        reasons.append("LINE_MISMATCH")
    if len(same_line_books) < minimum_depth:
        reasons.append("INSUFFICIENT_FRESH_SAME_LINE_BOOKS")

    # Existing engine already checks de-vig sign sensitivity. Here we retain the
    # actual EV under both methods and take the minimum as the conservative
    # executable reading for the next challenger.
    devig_evs: dict[str, float] = {}
    px = executable(
        row.venue_price,
        kind="american",
        venue=row.venue,
        venue_config=edge_cfg.venue(row.venue),
    )
    for method, probability in (row.decision.devig or {}).items():
        devig_evs[str(method)] = decide_at_price(float(probability), px).ev
    conservative_ev = min(devig_evs.values()) if devig_evs else None

    opening_side_line = _opening_side_line(candidate)
    crossings = key_number_crossings(
        opening_side_line,
        ref_line,
        key_numbers=key_numbers,
    )

    snapshots = list(history) + [dict(pull)]
    movement = movement_metrics(
        snapshots,
        event_id=row.event_id,
        side=row.side,
        reference_book=reference_book,
    )

    def span(values: Sequence[float]) -> float | None:
        return max(values) - min(values) if values else None

    return {
        "status": "PASS" if not reasons else "BLOCKED",
        "reasons": sorted(set(reasons)),
        "directionalAuthority": False,
        "freshSameLineBookCount": len(same_line_books),
        "freshSameLineBooks": same_line_books,
        "referenceQuoteAgeSeconds": ref_age,
        "venueQuoteAgeSeconds": venue_age,
        "lineDispersion": _line_stats(fresh_lines),
        "sameLineNoVigProbabilityRange": {
            "proportional": span(prop),
            "power": span(power),
        },
        "sameLineNoVigProbabilities": probability_by_book,
        "devigExecutableEv": devig_evs,
        "conservativeExecutableEv": conservative_ev,
        "openingSideLine": opening_side_line,
        "currentReferenceSideLine": ref_line,
        "keyNumberCrossings": crossings,
        "keyNumberMassTracked": {
            str(k): KEY_BUMPS.get(int(k)) for k in key_numbers
        },
        "movement": movement,
    }
