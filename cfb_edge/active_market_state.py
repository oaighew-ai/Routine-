"""Active-cohort market provenance and point-in-time information capture.

Research-only. This module never rewrites the live opening log. It builds a
separate BR2 evidence plane from:
- Kalshi current spread ladders,
- Kalshi one-minute historical bid/ask candles around each listed contract's
  exchange-reported open_time, and
- one current multi-book sportsbook spread pull.

A recovered open is audit-grade only when an actual two-sided ladder produces
a valid implied line within the existing [-60, +900] second true-open window.
No tolerance is widened and no Week 5 artifact is changed.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .information_state import build_state
from .market import american_to_decimal
from .point_in_time import digest, instant, resolve_game
from .providers.kalshi import (
    API_ROOT as KALSHI_API_ROOT,
    SERIES,
    _team_and_strike,
    implied_line_evidence,
)
from .providers.oddsapi import OddsApiUnreachable, fetch_pull
from .teams import normalize, resolve

CONTRACT = "CFB_EDGE_ACTIVE_MARKET_STATE_V1"
OPEN_CONTRACT = "CFB_EDGE_KALSHI_CANDLE_OPEN_V1"
MAX_OPEN_LAG_SECONDS = 900
MIN_OPEN_LAG_SECONDS = -60
MAX_FRESH_SECONDS = 900
CANDLE_CARRY_SECONDS = 60
BOOKS = ("pinnacle", "draftkings", "fanduel", "betmgm", "betrivers")


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _archive(raw_dir: str | Path, raw: bytes, *, suffix: str = ".json") -> dict[str, Any]:
    root = Path(raw_dir)
    root.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(raw).hexdigest()
    path = root / f"{sha}{suffix}.gz"
    if not path.exists():
        with path.open("wb") as fh:
            with gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as z:
                z.write(raw)
    try:
        visible_path = path.relative_to("capture-data")
    except ValueError:
        visible_path = path
    return {"sha256": sha, "bytes": len(raw), "path": str(visible_path)}


def _fetch_bytes(url: str, *, timeout: float = 45.0) -> bytes:
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "cfb-edge/1.0"}
    )
    last = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code != 429 and exc.code < 500:
                raise
            retry = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = float(retry) if retry is not None else 2.0 ** attempt
            except (TypeError, ValueError):
                delay = 2.0 ** attempt
            time.sleep(min(30.0, max(1.0, delay)))
        except urllib.error.URLError as exc:
            last = exc
            time.sleep(min(16.0, 2.0 ** attempt))
    if last is not None:
        raise last
    raise RuntimeError("market source fetch failed without an exception")


def _read_slate(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if str(r.get("game") or "").strip()]


def _load_list(path: str | Path) -> list[dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"{path}: expected JSON array")
    return value


def _slate_identity(
    slate: Sequence[Mapping[str, Any]],
    week_games: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    rows: dict[str, dict[str, Any]] = {}
    teams: set[str] = set()
    for raw in slate:
        game = str(raw.get("game") or "").strip()
        if "@" not in game:
            continue
        away, home = (x.strip() for x in game.split("@", 1))
        teams.update((away, home))
        identity = resolve_game(raw, week_games)
        if not identity:
            continue
        rows[game] = {
            "game": game,
            "away": away,
            "home": home,
            "kickoff": identity["kickoff"],
            "canonicalGameId": identity["canonicalGameId"],
        }
    return rows, teams


def _provider_team_aliases(
    team_catalog: Sequence[Mapping[str, Any]],
    known_teams: set[str],
) -> dict[str, str]:
    """Build exact provider aliases from the CFBD team catalog.

    This is intentionally not fuzzy matching. A catalog row may contribute its
    school name, alternate names, abbreviation, and exact school+mascot forms.
    Any normalized alias that maps to more than one active-slate team is
    discarded rather than resolved by input order.
    """
    candidates: dict[str, set[str]] = {}
    for row in team_catalog:
        school = str(row.get("school") or "").strip()
        canonical = resolve(school, known_teams) if school else None
        if canonical is None and school in known_teams:
            canonical = school
        if canonical is None:
            for alt in row.get("alternateNames") or []:
                canonical = resolve(str(alt), known_teams)
                if canonical:
                    break
        if canonical is None:
            continue
        mascot = str(row.get("mascot") or "").strip()
        bases = [
            school,
            str(row.get("abbreviation") or "").strip(),
            *(str(x).strip() for x in (row.get("alternateNames") or [])),
        ]
        names = [x for x in bases if x]
        if mascot:
            names.extend(f"{base} {mascot}" for base in bases if base)
        for name in names:
            key = normalize(name)
            if key:
                candidates.setdefault(key, set()).add(canonical)
    return {
        key: next(iter(values))
        for key, values in candidates.items()
        if len(values) == 1
    }


def _resolve_external_team(
    name: str,
    known_teams: set[str],
    provider_aliases: Mapping[str, str],
) -> str | None:
    return resolve(name, known_teams) or provider_aliases.get(normalize(name))


def _fixture_for_event(
    markets: Sequence[Mapping[str, Any]],
    identities: Mapping[str, Mapping[str, Any]],
    known_teams: set[str],
) -> Mapping[str, Any] | None:
    raw_teams = {
        parsed[0]
        for parsed in (_team_and_strike(dict(m)) for m in markets)
        if parsed is not None
    }
    if not raw_teams:
        return None
    resolved = {resolve(name, known_teams) for name in raw_teams}
    if None in resolved:
        return None
    candidates = []
    for row in identities.values():
        pair = {row["away"], row["home"]}
        if len(resolved) == 2 and resolved == pair:
            candidates.append(row)
        elif len(resolved) == 1 and next(iter(resolved)) in pair:
            candidates.append(row)
    return candidates[0] if len(candidates) == 1 else None


def _fetch_kalshi_markets(
    raw_dir: str | Path,
    *,
    opener: Callable[[str], bytes] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    opener = opener or _fetch_bytes
    cursor = ""
    pages: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    retrieved = _iso(datetime.now(timezone.utc))
    seen: set[str] = set()
    for _ in range(25):
        params = {
            "limit": "200",
            "status": "open",
            "series_ticker": SERIES["spread"],
        }
        if cursor:
            params["cursor"] = cursor
        url = KALSHI_API_ROOT + "/markets?" + urllib.parse.urlencode(params)
        raw = opener(url)
        stored = _archive(raw_dir, raw)
        obj = json.loads(raw)
        page = obj.get("markets") or []
        pages.extend(page)
        manifest.append({
            "kind": "kalshi_current_markets_page",
            "source": "kalshi",
            "endpoint": "/markets",
            "retrievedAt": retrieved,
            **stored,
        })
        next_cursor = str(obj.get("cursor") or "")
        if not next_cursor or next_cursor in seen or not page:
            break
        seen.add(next_cursor)
        cursor = next_cursor
    combined = _json_bytes(pages)
    stored = _archive(raw_dir, combined)
    manifest.append({
        "kind": "kalshi_current_markets",
        "source": "kalshi",
        "endpoint": "/markets",
        "retrievedAt": retrieved,
        **stored,
    })
    return pages, manifest, retrieved


def _market_open(market: Mapping[str, Any]) -> datetime | None:
    return _time(market.get("open_time") or market.get("openTime"))


def _candle_close(block: Any) -> float | None:
    if not isinstance(block, Mapping):
        return None
    for key in ("close_dollars", "close"):
        raw = block.get(key)
        value = _number(raw)
        if value is None:
            continue
        if key == "close" and value > 1.0:
            value /= 100.0
        if 0.0 < value < 1.0:
            return value
    return None


def _candles_by_ticker(payload: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    markets = payload.get("markets")
    if isinstance(markets, list):
        for row in markets:
            if not isinstance(row, Mapping):
                continue
            ticker = str(
                row.get("market_ticker") or row.get("marketTicker") or row.get("ticker") or ""
            ).strip()
            candles = row.get("candlesticks") or []
            if ticker and isinstance(candles, list):
                out[ticker] = [dict(x) for x in candles if isinstance(x, Mapping)]
    # Tolerate a keyed response in case the API serializer changes shape.
    if not out and isinstance(markets, Mapping):
        for ticker, candles in markets.items():
            if isinstance(candles, list):
                out[str(ticker)] = [dict(x) for x in candles if isinstance(x, Mapping)]
    return out


def _candle_time(candle: Mapping[str, Any]) -> datetime | None:
    raw = candle.get("end_period_ts") or candle.get("endPeriodTs")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    return _time(raw)


def _state_at(
    market: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    at: datetime,
) -> dict[str, Any] | None:
    selected: tuple[datetime, Mapping[str, Any]] | None = None
    for candle in candles:
        stamp = _candle_time(candle)
        if stamp is None or stamp > at:
            continue
        if selected is None or stamp > selected[0]:
            selected = (stamp, candle)
    if selected is None or (at - selected[0]).total_seconds() > CANDLE_CARRY_SECONDS:
        return None
    bid = _candle_close(selected[1].get("yes_bid"))
    ask = _candle_close(selected[1].get("yes_ask"))
    if bid is None or ask is None or ask <= bid:
        return None
    row = dict(market)
    row["yes_bid_dollars"] = bid
    row["yes_ask_dollars"] = ask
    return row


def recover_event_open(
    event_markets: Sequence[Mapping[str, Any]],
    candle_map: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    home: str,
    away: str,
    retrieved_at: str,
    source_hashes: Sequence[str],
) -> dict[str, Any]:
    candidates: set[datetime] = set()
    by_ticker: dict[str, Mapping[str, Any]] = {}
    for market in event_markets:
        ticker = str(market.get("ticker") or "").strip()
        opened = _market_open(market)
        if not ticker or opened is None:
            continue
        by_ticker[ticker] = market
        for candle in candle_map.get(ticker) or []:
            stamp = _candle_time(candle)
            if stamp is not None:
                candidates.add(stamp)

    first_valid = None
    for stamp in sorted(candidates):
        synthetic = []
        for ticker, market in by_ticker.items():
            state = _state_at(market, candle_map.get(ticker) or [], stamp)
            if state is not None:
                synthetic.append(state)
        line, used = implied_line_evidence(synthetic, home=home, away=away)
        if line is None or len(used) < 2:
            continue
        used_opens = [_time(x.get("open_time")) for x in used]
        if any(x is None for x in used_opens):
            continue
        venue_open = max(x for x in used_opens if x is not None)
        lag = (stamp - venue_open).total_seconds()
        tickers = [str(x.get("ticker") or "") for x in used if x.get("ticker")]
        if lag < MIN_OPEN_LAG_SECONDS:
            # A candle that predates a selected rung's own open_time cannot
            # prove the event-level opening. Keep scanning rather than letting
            # an impossible early state block a later valid one.
            continue
        first_valid = {
            "openingLine": float(line),
            "observedAt": _iso(stamp),
            "venueOpenTime": _iso(venue_open),
            "openLagSeconds": lag,
            "marketTickers": tickers,
            "quoteInputs": [dict(x) for x in used],
            "openAuditGrade": (
                lag <= MAX_OPEN_LAG_SECONDS
                and len(set(tickers)) >= 2
            ),
        }
        break

    if first_valid is None:
        return {
            "contract": OPEN_CONTRACT,
            "auditGrade": False,
            "reason": "NO_TWO_SIDED_IMPLIED_LINE_IN_OPEN_WINDOW",
            "retrievedAt": retrieved_at,
            "sourceHashes": sorted(set(source_hashes)),
        }

    first_valid.update({
        "contract": OPEN_CONTRACT,
        "auditGrade": bool(first_valid["openAuditGrade"]),
        "reason": (
            None if first_valid["openAuditGrade"]
            else "FIRST_VALID_LINE_OUTSIDE_TRUE_OPEN_TOLERANCE"
        ),
        "retrievedAt": retrieved_at,
        "sourceHashes": sorted(set(source_hashes)),
    })
    return first_valid


def _fetch_candles(
    markets: Sequence[Mapping[str, Any]],
    raw_dir: str | Path,
    *,
    opener: Callable[[str], bytes] | None = None,
) -> tuple[
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    dict[str, list[dict[str, str]]],
]:
    """Fetch opening-window candles in globally batched, rate-safe requests.

    Markets are bucketed by their own open_time in ten-minute windows, then
    chunked at the public endpoint's 100-ticker ceiling. This keeps a request
    near the 15-minute true-open window and far below the 10k-candle cap while
    avoiding the old one-request-per-game burst that Kalshi rate-limited.
    """
    opener = opener or _fetch_bytes
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    seen: set[str] = set()
    for market in markets:
        ticker = str(market.get("ticker") or "").strip()
        opened = _market_open(market)
        if not ticker or opened is None or ticker in seen:
            continue
        seen.add(ticker)
        bucket = int(opened.timestamp()) // 600
        grouped.setdefault(bucket, []).append(market)

    merged: dict[str, list[dict[str, Any]]] = {}
    manifest: list[dict[str, Any]] = []
    ticker_sources: dict[str, list[dict[str, str]]] = {}
    for bucket in sorted(grouped):
        rows = grouped[bucket]
        for offset in range(0, len(rows), 100):
            chunk = rows[offset:offset + 100]
            tickers = [str(x["ticker"]) for x in chunk]
            opens = [_market_open(x) for x in chunk]
            opens = [x for x in opens if x is not None]
            if not opens:
                continue
            params = {
                "market_tickers": ",".join(tickers),
                "start_ts": int(min(opens).timestamp()) - 60,
                "end_ts": int(max(opens).timestamp()) + MAX_OPEN_LAG_SECONDS,
                "period_interval": 1,
            }
            url = KALSHI_API_ROOT + "/markets/candlesticks?" + urllib.parse.urlencode(params)
            raw = opener(url)
            retrieved = _iso(datetime.now(timezone.utc))
            stored = _archive(raw_dir, raw)
            payload = json.loads(raw)
            for ticker, candles in _candles_by_ticker(payload).items():
                merged.setdefault(ticker, []).extend(candles)
            item = {
                "kind": "kalshi_open_candles",
                "source": "kalshi",
                "endpoint": "/markets/candlesticks",
                "retrievedAt": retrieved,
                "marketTickers": tickers,
                **stored,
            }
            manifest.append(item)
            for ticker in tickers:
                ticker_sources.setdefault(ticker, []).append({
                    "sha256": stored["sha256"],
                    "retrievedAt": retrieved,
                })
            # Even successful calls are paced. The historical endpoint has a
            # lower practical burst ceiling than the ordinary market listing.
            time.sleep(0.35)
    return merged, manifest, ticker_sources


def _odds_quotes(
    *,
    identities: Mapping[str, Mapping[str, Any]],
    known_teams: set[str],
    team_catalog: Sequence[Mapping[str, Any]] = (),
    raw_dir: str | Path,
    bookmakers: Sequence[str] = BOOKS,
    fetcher=fetch_pull,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    pull = fetcher(
        sport="americanfootball_ncaaf",
        bookmakers=bookmakers,
        markets=("spreads",),
    )
    payload = asdict(pull)
    raw = _json_bytes(payload)
    stored = _archive(raw_dir, raw)
    manifest = [{
        "kind": "sportsbook_spreads",
        "source": "the-odds-api",
        "endpoint": "/v4/sports/americanfootball_ncaaf/odds",
        "retrievedAt": pull.fetched_at,
        "bookSet": list(pull.book_set),
        **stored,
    }]

    aliases = _provider_team_aliases(team_catalog, known_teams)
    quotes: list[dict[str, Any]] = []
    matched_events = 0
    unresolved_events: list[str] = []
    kickoff_mismatch_events: list[str] = []
    for event in pull.events:
        away_raw = str(event.get("away") or "")
        home_raw = str(event.get("home") or "")
        away = _resolve_external_team(away_raw, known_teams, aliases)
        home = _resolve_external_team(home_raw, known_teams, aliases)
        if not away or not home:
            unresolved_events.append(f"{away_raw} @ {home_raw}")
            continue
        row = identities.get(f"{away} @ {home}")
        if not row:
            unresolved_events.append(f"{away_raw} @ {home_raw}")
            continue
        event_time = instant(event.get("commenceTime"))
        fixture_time = instant(row["kickoff"])
        if not event_time or not fixture_time:
            kickoff_mismatch_events.append(f"{away_raw} @ {home_raw}")
            continue
        kickoff_delta = abs((event_time - fixture_time).total_seconds())
        if kickoff_delta > 3600:
            kickoff_mismatch_events.append(
                f"{away_raw} @ {home_raw}:{int(kickoff_delta)}s"
            )
            continue
        matched_events += 1
        for bookmaker in event.get("bookmakers") or []:
            book = str(bookmaker.get("key") or "").strip().lower()
            if not book:
                continue
            for market in bookmaker.get("markets") or []:
                if market.get("key") != "spreads":
                    continue
                observed = str(market.get("lastUpdate") or "").strip()
                if not _time(observed):
                    continue
                outcome = next(
                    (x for x in market.get("outcomes") or [] if x.get("name") == event.get("home")),
                    None,
                )
                if not outcome:
                    continue
                spread = _number(outcome.get("point"))
                american = _number(outcome.get("price"))
                if spread is None or american is None:
                    continue
                try:
                    decimal = american_to_decimal(american)
                except ValueError:
                    continue
                quotes.append({
                    "canonicalGameId": row["canonicalGameId"],
                    "game": row["game"],
                    "book": book,
                    "market": "spread",
                    "period": "full_game",
                    "side": "home",
                    "quoteKind": "book_quote",
                    "sourceSha256": stored["sha256"],
                    "observedAt": observed,
                    "retrievedAt": pull.fetched_at,
                    "spread": spread,
                    "decimalPrice": decimal,
                    "provenance": "current",
                    "openAuditGrade": False,
                    "role": "decision",
                })
    health = {
        "fetchedAt": pull.fetched_at,
        "bookSet": list(pull.book_set),
        "events": len(pull.events),
        "creditsRemaining": pull.credits_remaining,
        "creditsUsed": pull.credits_used,
        "lastCost": pull.last_cost,
        "matchedEvents": matched_events,
        "unresolvedEvents": sorted(set(unresolved_events)),
        "kickoffMismatchEvents": sorted(set(kickoff_mismatch_events)),
    }
    return quotes, manifest, health


def capture(
    *,
    slate: Sequence[Mapping[str, Any]],
    week_games: Sequence[Mapping[str, Any]],
    team_catalog: Sequence[Mapping[str, Any]] = (),
    raw_dir: str | Path,
    kalshi_opener: Callable[[str], bytes] | None = None,
    odds_fetcher=fetch_pull,
) -> dict[str, Any]:
    identities, known_teams = _slate_identity(slate, week_games)
    kalshi_markets, manifest, kalshi_retrieved = _fetch_kalshi_markets(
        raw_dir, opener=kalshi_opener
    )
    current_manifest = next(
        x for x in reversed(manifest) if x.get("kind") == "kalshi_current_markets"
    )
    by_event: dict[str, list[dict[str, Any]]] = {}
    for market in kalshi_markets:
        event = str(market.get("event_ticker") or "").strip()
        if event:
            by_event.setdefault(event, []).append(dict(market))

    current_quotes: list[dict[str, Any]] = []
    open_quotes: list[dict[str, Any]] = []
    open_rows: list[dict[str, Any]] = []

    # Resolve every current event first, then fail closed on duplicate/ambiguous
    # representations of the same football game. Input ordering must never pick
    # which exchange event becomes authoritative.
    event_candidates: dict[str, list[tuple[str, list[dict[str, Any]], float, list[dict[str, Any]], Mapping[str, Any]]]] = {}
    for event, event_markets in sorted(by_event.items()):
        fixture = _fixture_for_event(event_markets, identities, known_teams)
        if not fixture:
            continue
        line, used = implied_line_evidence(
            event_markets, home=fixture["home"], away=fixture["away"]
        )
        if line is None or len(used) < 2:
            continue
        event_candidates.setdefault(fixture["game"], []).append(
            (event, event_markets, float(line), used, fixture)
        )

    ambiguous_games = sorted(
        game for game, rows in event_candidates.items() if len(rows) != 1
    )

    # Fetch every candidate event's candles once, globally batched. Ambiguous
    # games are intentionally excluded because their market identity is not
    # authoritative enough to justify archive recovery.
    candle_markets = [
        market
        for candidates in event_candidates.values()
        if len(candidates) == 1
        for market in candidates[0][1]
    ]
    candle_map, candle_manifest, ticker_sources = _fetch_candles(
        candle_markets, raw_dir, opener=kalshi_opener
    )
    manifest.extend(candle_manifest)

    for game, candidates in sorted(event_candidates.items()):
        if len(candidates) != 1:
            open_rows.append({
                "game": game,
                "contract": OPEN_CONTRACT,
                "auditGrade": False,
                "reason": "AMBIGUOUS_KALSHI_EVENT_MATCH",
                "matchedEvents": [x[0] for x in candidates],
            })
            continue
        event, event_markets, line, used, fixture = candidates[0]
        current_quotes.append({
            "canonicalGameId": fixture["canonicalGameId"],
            "game": fixture["game"],
            "book": "kalshi",
            "market": "spread",
            "period": "full_game",
            "side": "home",
            "quoteKind": "derived_line",
            "sourceSha256": current_manifest["sha256"],
            "observedAt": kalshi_retrieved,
            "retrievedAt": kalshi_retrieved,
            "spread": line,
            "provenance": "current",
            "openAuditGrade": False,
            "role": "decision",
            "eventTicker": event,
            "marketTickers": [str(x.get("ticker") or "") for x in used],
        })

        event_sources = [
            source
            for market in event_markets
            for source in ticker_sources.get(str(market.get("ticker") or ""), ())
        ]
        candle_retrieved = max(
            (source["retrievedAt"] for source in event_sources),
            default=kalshi_retrieved,
        )
        evidence = recover_event_open(
            event_markets,
            candle_map,
            home=fixture["home"],
            away=fixture["away"],
            retrieved_at=candle_retrieved,
            source_hashes=[source["sha256"] for source in event_sources],
        )
        evidence_payload = {
            "game": fixture["game"],
            "canonicalGameId": fixture["canonicalGameId"],
            "eventTicker": event,
            **evidence,
        }
        evidence_raw = _json_bytes(evidence_payload)
        stored = _archive(raw_dir, evidence_raw)
        manifest.append({
            "kind": "kalshi_open_evidence",
            "source": "kalshi-derived-from-candles",
            "endpoint": OPEN_CONTRACT,
            "retrievedAt": candle_retrieved,
            "game": fixture["game"],
            **stored,
        })
        evidence_payload["evidenceSha256"] = stored["sha256"]
        evidence_payload["evidencePath"] = stored["path"]
        open_rows.append(evidence_payload)
        if evidence.get("auditGrade") is True:
            open_quotes.append({
                "canonicalGameId": fixture["canonicalGameId"],
                "game": fixture["game"],
                "book": "kalshi",
                "market": "spread",
                "period": "full_game",
                "side": "home",
                "quoteKind": "derived_line",
                "sourceSha256": stored["sha256"],
                "observedAt": evidence["observedAt"],
                "retrievedAt": candle_retrieved,
                "spread": evidence["openingLine"],
                "provenance": "true_open",
                "openAuditGrade": True,
                "role": "opening",
                "provenanceContract": OPEN_CONTRACT,
                "venueOpenTime": evidence["venueOpenTime"],
                "openLagSeconds": evidence["openLagSeconds"],
                "eventTicker": event,
                "marketTickers": evidence["marketTickers"],
            })

    odds_error = None
    sportsbook_quotes: list[dict[str, Any]] = []
    odds_health: dict[str, Any] = {}
    try:
        sportsbook_quotes, odds_manifest, odds_health = _odds_quotes(
            identities=identities,
            known_teams=known_teams,
            team_catalog=team_catalog,
            raw_dir=raw_dir,
            fetcher=odds_fetcher,
        )
        manifest.extend(odds_manifest)
    except OddsApiUnreachable as exc:
        odds_error = str(exc)

    quotes = open_quotes + current_quotes + sportsbook_quotes
    decision = datetime.now(timezone.utc)
    states = []
    for row in identities.values():
        state = build_state(
            canonical_game_id=row["canonicalGameId"],
            decision_time=_iso(decision),
            kickoff=row["kickoff"],
            quotes=quotes,
            max_age_seconds=MAX_FRESH_SECONDS,
        )
        states.append({"game": row["game"], **state})

    audit_open = sum(1 for x in open_rows if x.get("auditGrade") is True)
    fresh_rows = sum(
        1 for x in states
        if "FRESH_MARKET_EVIDENCE_MISSING" not in (x.get("exclusions") or [])
    )
    open_state_rows = sum(
        1 for x in states
        if "AUDIT_GRADE_OPEN_MISSING" not in (x.get("exclusions") or [])
    )
    open_to_decision_rows = sum(
        1 for x in states
        if (x.get("features") or {}).get("openToDecisionSpread") is not None
    )
    validated_information_rows = sum(
        1 for x in states
        if "FRESH_MARKET_EVIDENCE_MISSING" not in (x.get("exclusions") or [])
        and "AUDIT_GRADE_OPEN_MISSING" not in (x.get("exclusions") or [])
        and (x.get("features") or {}).get("openToDecisionSpread") is not None
        and int((x.get("features") or {}).get("freshBookCount") or 0) >= 1
    )
    three_plus_fresh_book_rows = sum(
        1 for x in states
        if int((x.get("features") or {}).get("freshBookCount") or 0) >= 3
    )
    book_counts = [
        int((x.get("features") or {}).get("freshBookCount") or 0)
        for x in states
    ]
    manifest_complete = bool(manifest) and all(
        x.get("sha256") and x.get("path") and x.get("retrievedAt")
        for x in manifest
    )
    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "status": "DATA_COLLECTION_ONLY",
        "generatedAt": _iso(decision),
        "decisionEffect": "NONE",
        "stakingEffect": "NONE",
        "deliveryEffect": "NONE",
        "policy": {
            "liveOpenLogRewritten": False,
            "week5ArtifactsChanged": False,
            "trueOpenToleranceSeconds": MAX_OPEN_LAG_SECONDS,
            "clockSkewSeconds": MIN_OPEN_LAG_SECONDS,
            "historicalOpenSource": OPEN_CONTRACT,
            "freshMarketMaxAgeSeconds": MAX_FRESH_SECONDS,
        },
        "summary": {
            "slateRows": len(slate),
            "canonicalRows": len(identities),
            "kalshiCurrentMatchedRows": len(current_quotes),
            "auditGradeRecoveredOpenRows": audit_open,
            "freshMarketRows": fresh_rows,
            "auditGradeOpenStateRows": open_state_rows,
            "openToDecisionRows": open_to_decision_rows,
            "validatedInformationRows": validated_information_rows,
            "threePlusFreshBookRows": three_plus_fresh_book_rows,
            "sportsbookQuoteRows": len(sportsbook_quotes),
            "meanFreshSportsbookCount": (
                sum(book_counts) / len(book_counts) if book_counts else 0.0
            ),
            "sourceManifestComplete": manifest_complete,
            "ambiguousKalshiGameRows": len(ambiguous_games),
        },
        "oddsApi": {**odds_health, "error": odds_error},
        "openRows": open_rows,
        "informationStates": states,
        "quotes": quotes,
        "sourceManifest": manifest,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slate", required=True)
    p.add_argument("--week-games", required=True)
    p.add_argument("--teams")
    p.add_argument("--raw-dir", required=True)
    p.add_argument("--quotes-out", required=True)
    p.add_argument("--status-out", required=True)
    p.add_argument("--manifest-out", required=True)
    args = p.parse_args(argv)

    report = capture(
        slate=_read_slate(args.slate),
        week_games=_load_list(args.week_games),
        team_catalog=_load_list(args.teams) if args.teams else (),
        raw_dir=args.raw_dir,
    )
    Path(args.quotes_out).write_text(
        json.dumps(report["quotes"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    Path(args.manifest_out).write_text(
        json.dumps(report["sourceManifest"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    clean = {k: v for k, v in report.items() if k not in ("quotes", "sourceManifest")}
    Path(args.status_out).write_text(
        json.dumps(clean, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
