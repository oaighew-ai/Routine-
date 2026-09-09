"""The Odds API adapter: a full college football board in one call.

Chosen over scraping because it returns every book at once, includes the
sportsbooks that matter for this strategy, and costs one request per poll. The
free tier is 500 requests a month, which at the polling schedule in `watch.py`
covers a season with room to spare: dense polling only runs inside the release
window, and outside it an hourly poll is cheap.

Set the key in the environment rather than passing it around:

    export ODDS_API_KEY=...          # or setx on Windows

The adapter deliberately does no filtering, deduplication or averaging. It
turns one HTTP response into quotes and stops. Everything else is a pure
function of the raw log, which is the only part that cannot be recomputed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from ..watch import Quote

API_ROOT = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_ncaaf"

Opener = Callable[[str], bytes]


class OddsApiUnreachable(RuntimeError):
    """Raised when the board cannot be fetched, naming the host.

    An empty board and an unreachable one mean opposite things, and a capture
    that silently records nothing is worse than one that stops loudly.
    """


def _default_opener(url: str, *, timeout: float = 30.0) -> bytes:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "cfb-edge/1.0"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_board(
    *,
    api_key: str | None = None,
    markets: str = "spreads",
    regions: str = "us,us2,eu",
    opener: Opener | None = None,
) -> list[Quote]:
    """One poll of the whole college football board.

    `regions` includes eu because the low-hold books this strategy needs are
    not all in the us region, and a board without them is a board where the
    edge does not clear.
    """
    key = api_key or os.environ.get("ODDS_API_KEY")
    if not key:
        raise OddsApiUnreachable(
            "no API key. Set ODDS_API_KEY in the environment; the free tier at "
            "the-odds-api.com covers a season at this polling rate."
        )
    url = (
        f"{API_ROOT}/sports/{SPORT}/odds/?apiKey={key}&regions={regions}"
        f"&markets={markets}&oddsFormat=american"
    )
    try:
        payload = json.loads((opener or _default_opener)(url))
    except urllib.error.URLError as exc:
        raise OddsApiUnreachable(
            f"could not reach api.the-odds-api.com: {exc}. If this is a network "
            f"policy denial the host has to be allowed; the client cannot work "
            f"around it."
        ) from exc
    return parse_board(payload)


def parse_board(payload: list[dict]) -> list[Quote]:
    """Flatten the API's nested response into quotes.

    Lines are recorded from the home team's perspective, matching every other
    convention in this package: negative means the home team lays points.
    """
    seen_at = datetime.now(timezone.utc).isoformat()
    out: list[Quote] = []
    for event in payload or []:
        home = (event.get("home_team") or "").strip()
        away = (event.get("away_team") or "").strip()
        if not home or not away:
            continue
        game = f"{away} @ {home}"
        for bookmaker in event.get("bookmakers") or []:
            book = (bookmaker.get("title") or bookmaker.get("key") or "").strip()
            for market in bookmaker.get("markets") or []:
                kind = market.get("key")
                for outcome in market.get("outcomes") or []:
                    # Only the home side is stored; the away line is its
                    # negative, and storing both would double-count the book.
                    if (outcome.get("name") or "").strip() != home:
                        continue
                    point = outcome.get("point")
                    if point is None:
                        continue
                    out.append(Quote(
                        game=game, book=book,
                        market="spread" if kind == "spreads" else str(kind),
                        line=float(point), price=outcome.get("price"),
                        seen_at=seen_at,
                    ))
    return out
