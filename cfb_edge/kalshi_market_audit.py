"""Audit whether each slate game is actually represented and priceable on Kalshi.

This is diagnostic evidence only. It separates three failure modes that the raw
capture cannot distinguish on its own:

- PRICEABLE: a uniquely resolved event produces an executable-width implied line.
- EVENT_MATCHED_UNPRICEABLE: the event resolves to the slate, but its ladder does
  not yet contain enough <= MAX_SPREAD two-sided quotes to derive a line.
- NO_MATCHED_EVENT: no current Kalshi event resolves uniquely to the slate game.

No fuzzy matching is permitted. Provider names go through cfb_edge.teams.resolve,
the same exact/explicit alias registry used by the capture adapter.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .providers.kalshi import (
    MAX_SPREAD,
    SERIES,
    KalshiUnreachable,
    _team_and_strike,
    fetch_markets,
    implied_line_evidence,
)
from .teams import resolve

CONTRACT = "CFB_EDGE_KALSHI_MARKET_MATCH_AUDIT_V1"


def _fixtures(games: Sequence[str]) -> tuple[list[tuple[str, str]], set[str]]:
    fixtures: list[tuple[str, str]] = []
    known: set[str] = set()
    for game in games:
        if "@" not in game:
            continue
        away, home = (part.strip() for part in game.split("@", 1))
        if away and home:
            fixtures.append((away, home))
            known.update((away, home))
    return fixtures, known


def _fixture_for_event(
    raw_teams: set[str],
    fixtures: Sequence[tuple[str, str]],
    known: set[str],
) -> tuple[tuple[str, str] | None, list[str]]:
    resolved: set[str] = set()
    unresolved: list[str] = []
    for raw in sorted(raw_teams):
        team = resolve(raw, known)
        if team is None:
            unresolved.append(raw)
        else:
            resolved.add(team)
    if unresolved or not resolved:
        return None, unresolved
    if len(resolved) == 1:
        solo = next(iter(resolved))
        hits = [f for f in fixtures if solo in f]
        return (hits[0] if len(hits) == 1 else None), []
    if len(resolved) == 2:
        hits = [f for f in fixtures if frozenset(f) == frozenset(resolved)]
        return (hits[0] if len(hits) == 1 else None), []
    return None, []


def build(
    games: Sequence[str],
    markets: Sequence[Mapping[str, Any]],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    now = generated_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    fixtures, known = _fixtures(games)
    by_event: dict[str, list[dict[str, Any]]] = {}
    for market in markets:
        event = str(market.get("event_ticker") or "").strip()
        if event:
            by_event.setdefault(event, []).append(dict(market))

    event_rows: list[dict[str, Any]] = []
    matched_by_game: dict[str, list[dict[str, Any]]] = {}
    unresolved_names: set[str] = set()
    for event, event_markets in sorted(by_event.items()):
        raw_teams = {
            parsed[0]
            for parsed in (_team_and_strike(m) for m in event_markets)
            if parsed is not None
        }
        fixture, unresolved = _fixture_for_event(raw_teams, fixtures, known)
        unresolved_names.update(unresolved)
        row: dict[str, Any] = {
            "eventTicker": event,
            "rawTeams": sorted(raw_teams),
            "unresolvedRawTeams": unresolved,
            "marketCount": len(event_markets),
            "game": None,
            "priceable": False,
            "derivedHomeLine": None,
            "bracketingMarketTickers": [],
        }
        if fixture is not None:
            away, home = fixture
            game = f"{away} @ {home}"
            line, used = implied_line_evidence(event_markets, home=home, away=away)
            row.update({
                "game": game,
                "priceable": line is not None,
                "derivedHomeLine": round(float(line), 3) if line is not None else None,
                "bracketingMarketTickers": [
                    str(item.get("ticker") or "") for item in used if item.get("ticker")
                ],
            })
            matched_by_game.setdefault(game, []).append(row)
        event_rows.append(row)

    rows: list[dict[str, Any]] = []
    counts = {
        "PRICEABLE": 0,
        "EVENT_MATCHED_UNPRICEABLE": 0,
        "AMBIGUOUS_EVENT_MATCH": 0,
        "NO_MATCHED_EVENT": 0,
    }
    for game in games:
        matches = matched_by_game.get(game, [])
        priceable = [row for row in matches if row["priceable"]]
        if len(priceable) == 1:
            status = "PRICEABLE"
            chosen = priceable[0]
        elif len(priceable) > 1 or len(matches) > 1:
            status = "AMBIGUOUS_EVENT_MATCH"
            chosen = None
        elif len(matches) == 1:
            status = "EVENT_MATCHED_UNPRICEABLE"
            chosen = matches[0]
        else:
            status = "NO_MATCHED_EVENT"
            chosen = None
        counts[status] += 1
        rows.append({
            "game": game,
            "status": status,
            "eventTicker": chosen.get("eventTicker") if chosen else None,
            "derivedHomeLine": chosen.get("derivedHomeLine") if chosen else None,
            "bracketingMarketTickers": (
                chosen.get("bracketingMarketTickers", []) if chosen else []
            ),
            "matchedEventCount": len(matches),
        })

    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "generatedAt": now.isoformat(),
        "status": "OK",
        "policy": {
            "fuzzyMatchingAllowed": False,
            "maximumQuotedSpread": MAX_SPREAD,
            "decisionEffect": "NONE",
            "stakingEffect": "NONE",
            "deliveryEffect": "NONE",
        },
        "summary": {
            "slateRows": len(games),
            "kalshiEvents": len(by_event),
            "priceableRows": counts["PRICEABLE"],
            "matchedButUnpriceableRows": counts["EVENT_MATCHED_UNPRICEABLE"],
            "ambiguousRows": counts["AMBIGUOUS_EVENT_MATCH"],
            "noMatchedEventRows": counts["NO_MATCHED_EVENT"],
            "unresolvedRawNameCount": len(unresolved_names),
        },
        "unresolvedRawNames": sorted(unresolved_names),
        "rows": rows,
        "events": event_rows,
    }


def read_games(path: str | Path) -> list[str]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return [
            str(row.get("game") or "").strip()
            for row in csv.DictReader(fh)
            if str(row.get("game") or "").strip()
        ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slate", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    games = read_games(args.slate)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = build(games, fetch_markets(SERIES["spread"]))
    except KalshiUnreachable as exc:
        report = {
            "schemaVersion": 1,
            "contract": CONTRACT,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "status": "SOURCE_UNAVAILABLE",
            "error": str(exc),
            "policy": {
                "decisionEffect": "NONE",
                "stakingEffect": "NONE",
                "deliveryEffect": "NONE",
            },
            "summary": {"slateRows": len(games)},
            "rows": [],
            "events": [],
        }
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = report.get("summary") or {}
    print(
        f"{report['status']} | priceable {summary.get('priceableRows', 0)}/"
        f"{summary.get('slateRows', len(games))} | matched-unpriceable "
        f"{summary.get('matchedButUnpriceableRows', 0)} | unmatched "
        f"{summary.get('noMatchedEventRows', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
