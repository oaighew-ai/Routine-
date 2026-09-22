"""Recover venue open_time metadata without rewriting the immutable raw log."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .providers.kalshi import board_quotes
from .watch import _slate_games, _slate_kickoffs, classify_open_provenance

CONTRACT = "CFB_EDGE_OPEN_TIME_BACKFILL_V1"


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

    live = board_quotes(
        games=games,
        kickoffs=_slate_kickoffs(str(slate_path)),
        seen_at=recovered_at,
    )
    by_game = {q.game: q for q in live}

    rows = []
    for game, old in sorted(targets.items()):
        q = by_game.get(game)
        venue_open = str(getattr(q, "venue_open_time", "") or "") if q else ""
        first_seen = (old.get("first_seen") or "").strip()
        classification, lag = classify_open_provenance(first_seen, venue_open)
        rows.append({
            "game": game,
            "openingLine": float(old["opening_line"]),
            "originalSource": old.get("source"),
            "firstSeen": first_seen or None,
            "recoveredVenueOpenTime": venue_open or None,
            "openLagSeconds": lag,
            "recoveredClassification": classification,
            "eventTicker": getattr(q, "event_ticker", None) if q else None,
            "marketTickers": list(getattr(q, "market_tickers", ()) or ()) if q else [],
            "recoveredAt": recovered_at,
            "recoveryMethod": "current_open_market_metadata",
        })

    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "generatedAt": recovered_at,
        "targetCount": len(rows),
        "recoveredOpenTimeCount": sum(r["recoveredVenueOpenTime"] is not None for r in rows),
        "trueOpenCount": sum(r["recoveredClassification"] == "true_open" for r in rows),
        "firstSeenCount": sum(r["recoveredClassification"] == "first_seen" for r in rows),
        "rows": rows,
        "limitations": [
            "Recovery attaches current venue metadata and never rewrites the immutable raw capture.",
            "Historical quote state is not reconstructed.",
            "Only true_open rows may enter future audit-grade evidence.",
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
        f"{report['recoveredOpenTimeCount']}/{report['targetCount']} venue open times recovered; "
        f"{report['trueOpenCount']} classify true_open."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
