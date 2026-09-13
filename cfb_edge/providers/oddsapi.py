"""The Odds API adapter: a full college football board in one call.

Chosen over scraping because it returns every book at once and includes the
sportsbooks that matter for this strategy.

**It is not free at this polling rate, and an earlier version of this file
said it was.** Billing is one credit per region per market, so the defaults
here cost 3 credits a poll, and the schedule in `watch.py` makes 652 polls in
a regular-season week: about 1,956 credits a week and 8,400 a month. That was
never checked against the arithmetic until it was measured. Read your own
plan's allowance before leaving the capture running, and note that
`--regions us` costs a third as much at the price of the low-hold European
books the strategy was measured on.

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
    seen_at: str | None = None,
) -> list[Quote]:
    """One poll of the whole college football board.

    `regions` includes eu because the low-hold books this strategy needs are
    not all in the us region, and a board without them is a board where the
    edge does not clear.
    """
    key = api_key or os.environ.get("ODDS_API_KEY")
    if not key:
        raise OddsApiUnreachable(
            "no API key. Set ODDS_API_KEY in the environment.\n"
            "Check your plan's allowance first: the-odds-api.com bills one "
            "credit per region per market, so these defaults cost 3 a poll, "
            "and the schedule in watch.py makes 652 polls a week. That is "
            "about 1,956 credits a week and 8,400 a month. Dropping to "
            "--regions us cuts it to a third."
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
    return parse_board(payload, seen_at=seen_at)


def _checked_seen_at(seen_at: str | None) -> str:
    """The timestamp to stamp on this batch, or a loud failure.

    Only `None` means "this is a live poll, use the clock". Every other value
    is a caller saying it knows when the payload was fetched, and a caller that
    is wrong about that has to find out here rather than downstream.

    The two ways it used to go wrong were both silent. An empty string took the
    `or` branch and got today's clock, which is the exact substitution this
    argument exists to prevent. An unparseable string survived all the way to
    `Quote.before_kickoff`, which catches `ValueError` and returns `None`, and
    `OpeningBook._observe` counts `None` as usable: every quote in the batch
    would then be eligible to become the close, including one taken mid-game.
    The close would be wrong in the direction of whoever was winning, and
    nothing would have complained.

    Validation runs the same transformation `before_kickoff` does, so a value
    that passes here cannot fail there. The string is returned unchanged rather
    than normalised, so replaying an archived log rewrites nothing.
    """
    if seen_at is None:
        return datetime.now(timezone.utc).isoformat()
    if not isinstance(seen_at, str):
        raise ValueError(
            f"seen_at must be an ISO 8601 string, got {type(seen_at).__name__}. "
            f"A datetime is the natural thing to reach for and is not accepted, "
            f"because Quote.seen_at is serialised to the log as text."
        )
    if not seen_at.strip():
        raise ValueError(
            "seen_at is empty. Pass None for a live poll, or the time the "
            "payload was actually fetched. An empty string used to fall back "
            "to the current clock, which silently destroys the first-seen "
            "ordering the whole strategy is measured against."
        )
    try:
        datetime.fromisoformat(seen_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"seen_at is not an ISO 8601 timestamp: {seen_at!r}. It has to "
            f"parse, because the kickoff gate compares it against the game's "
            f"start time and an unparseable value silently disables that gate "
            f"for every quote in this batch."
        ) from exc
    return seen_at


def parse_board(payload: list[dict], *, seen_at: str | None = None) -> list[Quote]:
    """Flatten the API's nested response into quotes.

    Lines are recorded from the home team's perspective, matching every other
    convention in this package: negative means the home team lays points.

    `seen_at` defaults to now, which is right for a live poll and wrong for
    everything else. Re-parsing an archived payload without it stamps every
    quote with today's clock, which silently destroys the first-seen ordering
    the whole strategy is built on: the open stops being the open. Pass the
    time the payload was actually fetched. Anything that is neither `None` nor
    a parseable ISO 8601 string raises rather than falling back.
    """
    seen_at = _checked_seen_at(seen_at)
    out: list[Quote] = []
    for event in payload or []:
        home = (event.get("home_team") or "").strip()
        away = (event.get("away_team") or "").strip()
        if not home or not away:
            continue
        game = f"{away} @ {home}"
        # Carried on every quote so the close can be gated on kickoff. The
        # capture runs from Sunday into Tuesday and games start inside that
        # window, so without this a quote taken mid-game becomes the "close".
        commence = (event.get("commence_time") or "").strip() or None
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
                        seen_at=seen_at, commence_time=commence,
                    ))
    return out
