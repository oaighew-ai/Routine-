"""Recover enough venue metadata to disqualify late legacy opens safely.

Historical raw rows did not persist the exact Kalshi ladder contracts used to
derive their line. Recovery is therefore deliberately one-way: it may prove a
legacy row was too late to be a TRUE_OPEN, but it may never promote that row to
TRUE_OPEN after the fact.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .providers.kalshi import SERIES, board_quotes, fetch_markets
from .watch import _slate_games, _slate_kickoffs

CONTRACT = "CFB_EDGE_OPEN_TIME_BACKFILL_V2"
TRUE_OPEN_TOLERANCE_SECONDS = 900


def _time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def historical_open_proof(first_seen, event_open_times) -> dict:
    """Conservative one-way proof for a legacy row.

    Use the LATEST open_time of ANY rung in the event. That is the most generous
    possible venue-open timestamp for an unknown historical bracketing pair. If
    the system still arrived more than 15 minutes after that timestamp, no
    possible pair of then-listed event rungs could make the observation a
    TRUE_OPEN.

    If the lag is <= 15 minutes, the row remains UNVERIFIED. Recovery cannot
    promote it because the exact historical pair and two-sided quote state were
    not stored.
    """
    seen = _time(first_seen)
    opens = sorted(t for t in (_time(v) for v in event_open_times) if t is not None)
    if seen is None or not opens:
        return {
            "recoveredClassification": "unverified",
            "definitelyNotTrueOpen": False,
            "latestEventRungOpenTime": opens[-1].isoformat() if opens else None,
            "minimumPossibleLagSeconds": None,
        }
    latest = opens[-1]
    lag = (seen - latest).total_seconds()
    definitely_late = lag > TRUE_OPEN_TOLERANCE_SECONDS
    return {
        "recoveredClassification": "first_seen" if definitely_late else "unverified",
        "definitelyNotTrueOpen": definitely_late,
        "latestEventRungOpenTime": latest.isoformat(),
        "minimumPossibleLagSeconds": lag,
    }


def build(opens_path: str | Path, slate_path: str | Path) -> dict:
    opens_path, slate_path = Path(opens_path), Path(slate_path)
    with opens_path.open(newline="", encoding="utf-8") as fh:
        opens = {
            r["game"].strip(): r for r in csv.DictReader(fh)
            if (r.get("game") or "").strip()
        }

    games = _slate_games(str(slate_path))
    targets = {g: opens[g] for g in games if g in opens}
    recovered_at = datetime.now(timezone.utc).isoformat()

    # board_quotes identifies the Kalshi event for each fixture. fetch_markets
    # supplies every currently open rung so the proof can use the event-wide
    # latest open_time rather than pretending today's bracketing pair was the
    # same pair used by the historical line.
    live = board_quotes(
        games=games,
        kickoffs=_slate_kickoffs(str(slate_path)),
        seen_at=recovered_at,
    )
    by_game = {q.game: q for q in live}
    markets = fetch_markets(SERIES["spread"])
    event_open_times = {}
    for market in markets:
        event = str(market.get("event_ticker") or "")
        if not event:
            continue
        stamp = market.get("open_time") or market.get("openTime")
        if stamp:
            event_open_times.setdefault(event, []).append(stamp)

    rows = []
    for game, old in sorted(targets.items()):
        q = by_game.get(game)
        event = getattr(q, "event_ticker", None) if q else None
        first_seen = (old.get("first_seen") or "").strip()
        proof = historical_open_proof(
            first_seen,
            event_open_times.get(str(event or ""), []),
        )
        rows.append({
            "game": game,
            "openingLine": float(old["opening_line"]),
            "originalSource": old.get("source"),
            "firstSeen": first_seen or None,
            "eventTicker": event,
            "currentBracketingMarketTickers": list(
                getattr(q, "market_tickers", ()) or ()
            ) if q else [],
            "currentBracketingVenueOpenTime": (
                getattr(q, "venue_open_time", None) if q else None
            ),
            **proof,
            "historicalPromotionEligible": False,
            "recoveredAt": recovered_at,
            "recoveryMethod": "event_wide_latest_rung_open_time_upper_bound",
        })

    return {
        "schemaVersion": 2,
        "contract": CONTRACT,
        "generatedAt": recovered_at,
        "targetCount": len(rows),
        "eventOpenTimeRecoveredCount": sum(
            r["latestEventRungOpenTime"] is not None for r in rows
        ),
        "definitelyNotTrueOpenCount": sum(
            r["definitelyNotTrueOpen"] for r in rows
        ),
        "unresolvedCount": sum(
            not r["definitelyNotTrueOpen"] for r in rows
        ),
        "trueOpenCount": 0,
        "rows": rows,
        "limitations": [
            "Recovery is one-way: no legacy row can be promoted to true_open after the fact.",
            "The original bracketing contracts and historical two-sided quote state were not persisted.",
            "The latest open_time across every current rung in the event is used as a conservative upper bound; lag beyond 900 seconds proves the first observation was late.",
            "The immutable raw capture is never rewritten.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--opens", required=True)
    p.add_argument("--slate", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    report = build(args.opens, args.slate)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(
        f"{report['eventOpenTimeRecoveredCount']}/{report['targetCount']} event open bounds recovered; "
        f"{report['definitelyNotTrueOpenCount']} definitively not true_open; "
        f"{report['unresolvedCount']} unresolved."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
