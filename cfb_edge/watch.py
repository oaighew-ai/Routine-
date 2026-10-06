"""Capture opening lines automatically, because by the time you look they are gone.

The single most expensive lesson in this project came from Oklahoma at Michigan
in week 2 of 2026. The line opened Michigan -2.5, the model said Michigan was
overvalued, and the number flipped nine points to Oklahoma -6.5. The model was
right and the edge was unbettable, because nobody was watching when it opened.

That is the whole problem this module solves. The strategy is worth 0.44 points
of closing line value against the *opening* number and nothing at all against
the current one, so a card built on Wednesday is a card built on numbers whose
value has already been taken. Capture has to be automatic and it has to be
early.

Three rules follow, and they are what the code enforces.

**First seen wins, permanently.** The opening line is the first price the market
showed, so the first observation of a (game, book, market) is recorded and never
overwritten. A poller that keeps the latest price is a poller that destroys the
only number the strategy needs.

**Raw before derived.** Every poll is appended to an immutable gzipped log. Any
opens file, any card, any analysis is a pure function of that log and can be
rebuilt. A week captured under a broken schema is gone forever; a week of raw
JSON can be re-parsed.

**Poll on ignorance, not on schedule.** Books do not announce releases and they
do not all move together. Rather than guessing the minute, poll often through
the window when lines are expected and rarely outside it, and let first-seen do
the work. Missing the exact moment costs nothing if the next poll is minutes
away, and a poll that finds nothing new is nearly free.

The strongest form of that rule is not a calendar at all. A poll that just
recorded a first-seen price is direct evidence the board is opening *now*, so
the next poll is five minutes away regardless of the hour. The clock only sets
the baseline for when to look at all. This matters most in bowl season, where
numbers post from early December and drift until January: a calendar rule would
have to choose between five-minute polling for six weeks and missing the drop,
and this one does neither.
"""

from __future__ import annotations

import gzip
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

# When the board for the coming cohort actually opens, in UTC. Measured, not
# assumed: `data/audit/week4-open-time-backfill.json` recovered Kalshi's own
# `open_time` for all 35 events of the Sep 25-26 cohort.
#
#     Sat 2026-09-19 16:00Z   1 event
#     Sun 2026-09-20 01:00Z   2
#     Sun 2026-09-20 04:00Z   5
#     Sun 2026-09-20 07:00Z  13
#     Sun 2026-09-20 10:00Z  14
#
# Every one of them opened before the window this constant used to describe.
# It began Sunday at 22:00 UTC, on the sportsbook folklore that look-ahead
# numbers post Sunday evening US time. Kalshi does not work that way: it opens
# the next cohort's board while the current one is still being played, from
# Saturday afternoon UTC. The old window began roughly twelve hours after the
# last of those opens, so the capture could observe a price but never an
# opening price. All 35 rows graded `first_seen`, none `true_open`, at a
# median of 18 hours and a minimum of 15 hours behind the venue.
#
# The times are upper bounds (`event_wide_latest_rung_open_time_upper_bound`):
# a true open can only be earlier than the number above, never later, so the
# window needs margin on that side and none on the other.
#
# The next cohort then showed the window was still too late and too short.
# `data/prospective-open-status.json` carries the venue's own open time for 55
# of the 56 games of the Oct 1-3 slate:
#
#     Sat 2026-09-26 01:06Z   2 events   <- Friday evening US time
#     Sat 2026-09-26 16:07Z   1
#     Sun 2026-09-27 01:06Z   4
#     Sun 2026-09-27 04:06Z   5
#     Sun 2026-09-27 07:06Z   9
#     Sun 2026-09-27 10:06Z  19
#     Sun 2026-09-27 16:06Z   4
#     Mon 2026-09-28 22:05Z  11
#
# Two of the 55 opened eleven hours before a window that began Saturday at
# 12:00, so no poll could have graded them however reliably it ran. Across
# both cohorts every open sits a few minutes past an hour on a three-hour
# step (01, 04, 07, 10, 16, 22). The window therefore opens Friday at 18:00:
# the earliest measured open, Saturday 01:06, less two of those steps and
# rounded down to the hour. Two steps of margin is a PRIOR (Law 6), not a
# measurement; a cohort that opens earlier than Friday 18:00 moves it again.
#
# Friday evening is only useful once the week being played has kicked off,
# because `slate.opening_week` names the earliest week with no game started.
# Every week of October and November has a Tuesday-to-Friday game. In a week
# that does not, these hours poll the current slate and record nothing new.
RELEASE_WINDOW_UTC = {
    4: range(18, 24),   # Friday evening: 2 of 55 measured opens were Sat 01:06
    5: range(0, 24),    # Saturday, all day
    6: range(0, 24),    # Sunday, all day: most measured opens land here
    0: range(0, 24),    # Monday, all day: 11 of 55 opened Monday 22:05
    1: range(0, 18),    # Tuesday, until the market has settled
}

# Bowl and playoff numbers do not follow that rhythm. They post from early
# December and do not close until late December or January, so the board fills
# in over weeks rather than over one Sunday night. A weekday-and-hour rule
# cannot express that, and the honest interval through it is neither five
# minutes for six weeks nor an hour.
POSTSEASON_MONTHS = {12: range(1, 32), 1: range(1, 21)}

DENSE_INTERVAL_SECONDS = 300        # five minutes while numbers are landing
POSTSEASON_INTERVAL_SECONDS = 1800  # half-hourly through the bowl trickle
SPARSE_INTERVAL_SECONDS = 3600      # hourly when nothing is expected

TRUE_OPEN_MAX_LAG_SECONDS = 15 * 60
TRUE_OPEN_CLOCK_SKEW_SECONDS = 60

# A close is the last price seen before kickoff, and a grader accepts it only
# when it is at most this old at kickoff.
CLOSE_MAX_AGE_SECONDS = 15 * 60
# How long before kickoff a game of the week being played joins the poll. The
# tolerance above is what has to be covered; the extra five minutes are five
# polls of margin and are a PRIOR (Law 6), chosen and not derived.
CLOSING_WINDOW_SECONDS = CLOSE_MAX_AGE_SECONDS + 5 * 60


def _as_utc(when: datetime | None) -> datetime:
    """The moment, in UTC, whatever timezone it arrived in.

    Both schedules below are defined in UTC and are read by pulling `.weekday()`
    and `.hour` straight off the value. Those attributes are wall-clock fields,
    not instants, so an aware datetime in another zone used to be read at face
    value: Sunday 18:30-04:00 is 22:30 UTC and inside the release window, and it
    was answered as a quiet Sunday evening. A caller on US Eastern passing local
    time polled hourly through the entire release window, which is precisely the
    failure this module exists to prevent.

    A naive datetime is taken as UTC. That is what the old code did implicitly
    and it is the only reading consistent with the constants' names, but it is
    now a stated choice rather than an accident of attribute access.
    """
    when = when or datetime.now(timezone.utc)
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def _parse_time(stamp: str) -> datetime | None:
    """An ISO timestamp as an aware UTC datetime, or None if it is not one.

    None rather than a fallback to the current clock. That substitution is
    exactly the `seen_at` bug: a blank stamp silently became "now", and the
    closing line value it produced was +23.5 points against a true +1.0, in
    the direction of confirming the strategy.
    """
    if not stamp:
        return None
    try:
        return _as_utc(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")))
    except (ValueError, AttributeError, TypeError):
        return None


def classify_open_provenance(
    first_seen: str | None,
    venue_open_time: str | None,
) -> tuple[str, float | None]:
    """Fail closed unless venue time proves the first valid quote was near open."""
    from .clv import FIRST_SEEN, LATE, TRUE_OPEN, UNVERIFIED
    seen = _parse_time(first_seen or "")
    if seen is None:
        return UNVERIFIED, None
    opened = _parse_time(venue_open_time or "")
    if opened is not None:
        lag = (seen - opened).total_seconds()
        if lag < -TRUE_OPEN_CLOCK_SKEW_SECONDS:
            return UNVERIFIED, lag
        if lag <= TRUE_OPEN_MAX_LAG_SECONDS:
            return TRUE_OPEN, lag
        return FIRST_SEEN, lag
    return (FIRST_SEEN if in_release_window(seen) else LATE), None


# An exchange event ticker names its game day: KXNCAAFSPREAD-26OCT03VANUGA is
# Vanderbilt at Georgia on 3 October 2026.
_EVENT_TICKER_DATE = re.compile(r"^[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})")
_EVENT_MONTHS = {
    name: number for number, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
         "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)
}
# The ticker carries the US game day. A kickoff in UTC falls on that day, or
# on the next one for an evening game. Nothing else has been observed (D43).
EVENT_DAY_OFFSETS = (0, 1)


def event_ticker_date(ticker: str | None) -> date | None:
    """The game day an exchange event ticker names, or None if it names none."""
    found = _EVENT_TICKER_DATE.match(str(ticker or "").strip().upper())
    if not found:
        return None
    month = _EVENT_MONTHS.get(found.group(2))
    if month is None:
        return None
    try:
        return date(2000 + int(found.group(1)), month, int(found.group(3)))
    except ValueError:
        return None


def event_matches_kickoff(ticker: str | None, kickoff: str | None) -> bool | None:
    """Whether an exchange event is the game that kicks off at ``kickoff``.

    A team plays every week, and the exchange lists next week's market while
    this week's is still open. A market matched on a team name alone can
    therefore belong to another week: on 3 October 2026 the Vanderbilt at
    Georgia market was recorded as the opening line of Georgia at Alabama, a
    game seven days later, and locked that game as a missed open (D43).

    True or False when both the ticker's day and the kickoff are readable,
    None when either is not, which the caller has to decide about.
    """
    day = event_ticker_date(ticker)
    start = _parse_time(kickoff or "")
    if day is None or start is None:
        return None
    return (start.date() - day).days in EVENT_DAY_OFFSETS


def in_release_window(when: datetime | None = None) -> bool:
    """Whether now is when a regular week's opening lines are likely to appear."""
    when = _as_utc(when)
    hours = RELEASE_WINDOW_UTC.get(when.weekday())
    return hours is not None and when.hour in hours


def in_postseason_window(when: datetime | None = None) -> bool:
    """Whether now is bowl and playoff season, when the board fills in slowly."""
    when = _as_utc(when)
    days = POSTSEASON_MONTHS.get(when.month)
    return days is not None and when.day in days


def poll_interval(when: datetime | None = None, *, opened_last_poll: int = 0) -> int:
    """How long to wait before the next poll.

    The calendar is a prior, not the signal. The signal is whether the board is
    actually opening: a poll that recorded a first-seen price means the market
    is moving right now, so the next poll is five minutes away whatever the
    clock says. That one rule is what makes bowl season work without polling
    every five minutes for six weeks, and it costs nothing in a regular week
    because the release window is already dense.
    """
    if opened_last_poll:
        return DENSE_INTERVAL_SECONDS
    if in_release_window(when):
        return DENSE_INTERVAL_SECONDS
    if in_postseason_window(when):
        return POSTSEASON_INTERVAL_SECONDS
    return SPARSE_INTERVAL_SECONDS


@dataclass(frozen=True)
class Quote:
    """One price, at one book, on one market, at one moment."""

    game: str
    book: str
    market: str
    line: float
    price: float | None
    seen_at: str
    # When the game starts, as the provider reports it. Optional because logs
    # captured before this field existed do not carry one, and because a
    # provider that omits it should degrade to the old behaviour rather than
    # discard the poll.
    commence_time: str | None = None
    venue_open_time: str | None = None
    event_ticker: str | None = None
    market_tickers: tuple[str, ...] = ()
    quote_inputs: tuple[dict, ...] = ()
    poll_time: str | None = None
    first_valid_two_sided_quote_time: str | None = None
    code_revision: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.game, self.book, self.market)

    @property
    def wrong_event(self) -> bool:
        """Whether this quote was read from a market for a different game day.

        True only when the event ticker and the kickoff both say so. A quote
        that carries neither is left to the checks it always had.
        """
        return event_matches_kickoff(self.event_ticker, self.commence_time) is False

    @property
    def before_kickoff(self) -> bool | None:
        """Whether this quote was seen before the game started.

        None when either timestamp is missing or unparseable, which the caller
        has to decide about rather than have decided for it.
        """
        if not self.commence_time:
            return None
        try:
            seen = datetime.fromisoformat(self.seen_at.replace("Z", "+00:00"))
            start = datetime.fromisoformat(self.commence_time.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return seen < start


@dataclass(frozen=True)
class PollResult:
    """Quotes plus compact explanations for listed markets that had no line."""

    quotes: list[Quote]
    market_diagnostics: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OpeningBook:
    """First-seen prices, which is what an opening line actually is."""

    path: Path
    opens: dict[tuple[str, str, str], Quote] = field(default_factory=dict)
    # Last quote per market that was seen before kickoff. Maintained as the
    # log is read rather than derived from a full history: a season of
    # five-minute polling is about six million quotes, and holding them to
    # compute one value per market would cost gigabytes to answer a question
    # that needs a single pass.
    closing: dict[tuple[str, str, str], Quote] = field(default_factory=dict)
    # (game, market) pairs whose kickoff time was missing or unreadable.
    _ungated: set[tuple[str, str]] = field(default_factory=set)
    # Last price seen for each market. The same append-only log that gives the
    # open gives the close, because every poll is written and nothing is
    # overwritten. No second data source, and no way for the two to disagree.
    latest: dict[tuple[str, str, str], Quote] = field(default_factory=dict)
    # `polled_at` of every poll already in the log. A replayed snapshot whose
    # poll is already here is skipped, so recovery after a lost push can be
    # run more than once without writing the same poll twice.
    polls: set[str] = field(default_factory=set)
    # Quotes the log holds that were read from another game day's market,
    # keyed by (game, event ticker) with how often each was seen. The log is
    # append-only and keeps them; no derived view is built from them.
    wrong_event: dict[tuple[str, str], int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "OpeningBook":
        """Rebuild from the raw log. Derived state is always recomputed."""
        path = Path(path)
        book = cls(path=path)
        if not path.exists():
            return book
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("polled_at"):
                    book.polls.add(str(rec["polled_at"]))
                for q in rec.get("quotes", []):
                    try:
                        quote = Quote(**q)
                    except TypeError:
                        continue
                    book._observe(quote)
        return book

    def record(
        self,
        quotes: Iterable[Quote],
        *,
        polled_at: str | None = None,
        market_diagnostics: Iterable[Mapping[str, Any]] = (),
    ) -> list[Quote]:
        """Append a poll to the log and return the quotes that were new.

        The return value is the point of the whole exercise: those are markets
        that had not been seen before, so their price is an opening line.

        ``polled_at`` is the poll's own time. A live poll that also writes a
        snapshot passes it so both carry one stamp, and a replay passes the
        snapshot's, so the log keeps the moment the market was actually
        observed rather than the moment it was written.
        """
        quotes = list(quotes)
        market_diagnostics = [dict(item) for item in market_diagnostics]
        stamp = polled_at or datetime.now(timezone.utc).isoformat()
        fresh = [q for q in quotes if q.key not in self.opens and not q.wrong_event]
        for q in quotes:
            self._observe(q)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        opener = gzip.open if self.path.suffix == ".gz" else open
        with opener(self.path, "at", encoding="utf-8") as fh:
            record = {
                "polled_at": stamp,
                "quotes": [q.__dict__ for q in quotes],
            }
            if market_diagnostics:
                record["marketDiagnostics"] = market_diagnostics
            fh.write(json.dumps(record) + "\n")
        self.polls.add(stamp)
        return fresh

    def replay(self, snapshot: Mapping[str, Any]) -> tuple[list[Quote], bool]:
        """Record a saved live-poll snapshot exactly as it was observed.

        The open loop keeps every poll it has not yet pushed. When a push is
        rejected because another writer moved the branch, it takes the branch
        as it is and replays those snapshots on top, so the first sighting of
        a market keeps its real time instead of being re-observed a poll later
        or lost. Returns the newly opened quotes and whether anything was
        written; a poll already in the log is skipped.
        """
        if snapshot.get("contract") != "CFB_EDGE_LIVE_QUOTE_SNAPSHOT_V1":
            raise ValueError("not a CFB_EDGE_LIVE_QUOTE_SNAPSHOT_V1 snapshot")
        stamp = str(snapshot.get("polled_at") or "")
        if not stamp:
            raise ValueError("snapshot has no polled_at; its observation time is unknown")
        if stamp in self.polls:
            return [], False
        quotes = []
        for raw in snapshot.get("quotes") or []:
            data = dict(raw)
            for key in ("market_tickers", "quote_inputs"):
                if isinstance(data.get(key), list):
                    data[key] = tuple(data[key])
            try:
                quotes.append(Quote(**data))
            except TypeError:
                continue
        diagnostics = snapshot.get("marketDiagnostics", [])
        if not isinstance(diagnostics, list) or any(
            not isinstance(item, Mapping) for item in diagnostics
        ):
            raise ValueError("snapshot marketDiagnostics must be a list of objects")
        return self.record(
            quotes, polled_at=stamp, market_diagnostics=diagnostics
        ), True

    def _observe(self, quote: Quote) -> None:
        """Fold one quote into the derived views, in a single pass."""
        if quote.wrong_event:
            # Another game day's market, matched on a shared team. It was
            # never this game's price, so it is not its open, its latest or
            # its close. Counted, so a report can say how many were refused.
            seen = (quote.game, str(quote.event_ticker))
            self.wrong_event[seen] = self.wrong_event.get(seen, 0) + 1
            return
        self.opens.setdefault(quote.key, quote)   # first wins, never overwritten
        self.latest[quote.key] = quote            # last wins, deliberately
        gated = quote.before_kickoff
        if gated is None:
            self._ungated.add((quote.game, quote.market))
        if gated is not False:                    # unknown counts as usable
            self.closing[quote.key] = quote

    def _consensus(self, source: dict, market: str) -> dict[str, float]:
        """Median line per game across whichever books are in `source`.

        Median rather than first-book, because one book posting an outlier and
        pulling it thirty seconds later should not define the number.
        """
        by_game: dict[str, list[float]] = {}
        for (game, _book, mkt), q in source.items():
            if mkt == market:
                by_game.setdefault(game, []).append(q.line)
        out: dict[str, float] = {}
        for game, lines in by_game.items():
            lines.sort()
            mid = len(lines) // 2
            out[game] = (
                lines[mid] if len(lines) % 2 else 0.5 * (lines[mid - 1] + lines[mid])
            )
        return out

    def closing_quotes(self) -> dict[tuple[str, str, str], Quote]:
        """Last quote per market that was seen *before* kickoff.

        The capture runs from Sunday into Tuesday and games kick off inside
        that window, so plain last-seen is not a close: a quote taken while a
        game is being played is an in-play number, and closing line value
        measured against one is noise recorded as signal. That would corrupt
        the scorecard the whole project rests on, quietly, which is the worst
        shape a defect can take here.

        Quotes carrying no kickoff time are kept. Logs written before the field
        existed have none, and dropping them would silently empty an old log
        rather than admit it cannot be gated. `ungated_games` reports how many
        are in that position.
        """
        return self.closing

    def ungated_games(self, market: str = "spread") -> set[str]:
        """Games whose close could not be gated for want of a kickoff time."""
        return {game for game, mkt in self._ungated if mkt == market}

    def consensus_closes(self, market: str = "spread") -> dict[str, float]:
        """Median closing line per game, ignoring anything seen after kickoff.

        Still only as good as how late the capture ran: a log that stopped on
        Monday gives Monday's number and calls it a close. The guard here is
        against the other end, polling past kickoff, which the schedule makes
        routine rather than exceptional.
        """
        return self._consensus(self.closing_quotes(), market)

    def consensus_opens(self, market: str = "spread") -> dict[str, float]:
        """Median opening line per game, across whichever books were seen first.

        Median rather than first-book, because one book posting an outlier and
        pulling it thirty seconds later should not define the open.
        """
        return self._consensus(self.opens, market)

    def first_quotes(self, market: str = "spread") -> dict[str, Quote]:
        """Earliest persisted quote per game, with its provenance."""
        out: dict[str, Quote] = {}
        for (game, _book, mkt), q in self.opens.items():
            if mkt != market:
                continue
            prior = out.get(game)
            if prior is None or str(q.seen_at or "") < str(prior.seen_at or ""):
                out[game] = q
        return out

    def first_seen(self, market: str = "spread") -> dict[str, str]:
        return {game: str(q.seen_at or "") for game, q in self.first_quotes(market).items() if q.seen_at}

    def write_opens_csv(self, path: str | Path, market: str = "spread") -> int:
        """Emit line plus audit provenance; only TRUE_OPEN is promotion-grade."""
        import csv
        rows = sorted(self.consensus_opens(market).items())
        evidence = self.first_quotes(market)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["game", "opening_line", "source", "first_seen",
                        "venue_open_time", "open_lag_seconds", "event_ticker",
                        "market_tickers", "first_valid_two_sided_quote_time",
                        "poll_time", "code_revision"])
            for game, line in rows:
                q = evidence.get(game)
                first_seen = str(getattr(q, "seen_at", "") or "") if q else ""
                venue_open = str(getattr(q, "venue_open_time", "") or "") if q else ""
                source, lag = classify_open_provenance(first_seen, venue_open)
                tickers = "|".join(getattr(q, "market_tickers", ()) or ()) if q else ""
                w.writerow([game, line, source, first_seen, venue_open,
                            "" if lag is None else f"{lag:.3f}",
                            str(getattr(q, "event_ticker", "") or "") if q else "",
                            tickers,
                            str(getattr(q, "first_valid_two_sided_quote_time", "") or "") if q else "",
                            str(getattr(q, "poll_time", "") or "") if q else "",
                            str(getattr(q, "code_revision", "") or "") if q else ""])
        return len(rows)



# A fetcher takes nothing and returns quotes. Injectable so the loop can be
# tested without a network, and so a new provider is one function.
Fetcher = Callable[[], list[Quote] | PollResult]


def run_once(book: OpeningBook, fetch: Fetcher) -> list[Quote]:
    """One poll. Returns the newly-opened markets."""
    result = fetch()
    if isinstance(result, PollResult):
        return book.record(
            result.quotes, market_diagnostics=result.market_diagnostics
        )
    return book.record(result)


def watch(
    book: OpeningBook,
    fetch: Fetcher,
    *,
    max_polls: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    on_new: Callable[[list[Quote]], None] | None = None,
) -> int:
    """Poll until stopped, recording first-seen prices.

    A network failure is logged and skipped rather than fatal: the next poll is
    minutes away and an exception here would end the capture, which is the one
    outcome worth avoiding.
    """
    polls = 0
    opened = 0
    while max_polls is None or polls < max_polls:
        try:
            fresh = run_once(book, fetch)
            opened = len(fresh)
            if fresh and on_new:
                on_new(fresh)
        except Exception as exc:                      # noqa: BLE001
            print(f"poll failed, continuing: {exc}")
            opened = 0
        polls += 1
        if max_polls is not None and polls >= max_polls:
            break
        sleep(poll_interval(now(), opened_last_poll=opened))
    return polls


def _slate_games(path: str) -> list[str]:
    """The week's fixtures as "Away @ Home", which orients the exchange."""
    import csv

    with open(path, newline="", encoding="utf-8") as fh:
        return [r["game"].strip() for r in csv.DictReader(fh) if r.get("game")]


def _slate_kickoffs(path: str) -> dict[str, str]:
    """Authoritative kickoff timestamps keyed by canonical slate game.

    The exchange contract close time is not the game kickoff. A missing schedule
    kickoff stays missing so downstream pre-kickoff gates fail closed.
    """
    import csv

    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            game = (row.get("game") or "").strip()
            kickoff = (row.get("kickoff") or "").strip()
            if game and kickoff:
                out[game] = kickoff
    return out


def closing_fixtures(
    path: str | Path,
    now: datetime | None = None,
    window_seconds: int = CLOSING_WINDOW_SECONDS,
) -> dict[str, str]:
    """Games on a slate that kick off within the window, keyed to their kickoff.

    The open loop polls the week whose lines are opening, which from Friday
    evening is next week. Nothing then watched the week being played, so the
    last price before kickoff was whatever a scheduled job happened to see.
    On 3 October 2026 that was hours old for every frozen Week 6 game that
    had started, and none could be graded (D44). A game joins the poll
    shortly before it starts and leaves at kickoff, so a close costs twenty
    observations and not a weekend of them.

    A row with no readable kickoff is left out: there is no moment to be near.
    """
    moment = _as_utc(now)
    out: dict[str, str] = {}
    for game, kickoff in _slate_kickoffs(str(path)).items():
        start = _parse_time(kickoff)
        if start is None:
            continue
        lead = (start - moment).total_seconds()
        if 0 < lead <= window_seconds:
            out[game] = kickoff
    return out


def close_coverage(
    log_path: str | Path,
    slate_path: str | Path,
    now: datetime | None = None,
    max_age_seconds: int = CLOSE_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """How many started games on a slate have a close a grader would accept.

    A close is the newest price seen at or before kickoff. It counts when it
    is at most ``max_age_seconds`` old at kickoff. Read straight from the
    append-only log, so the answer is the evidence and not a report about it.
    A quote from another game day's market is not counted (D43).
    """
    moment = _as_utc(now)
    kickoffs = {g: _parse_time(k) for g, k in _slate_kickoffs(str(slate_path)).items()}
    newest: dict[str, datetime] = {}
    path = Path(log_path)
    if path.exists():
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for q in rec.get("quotes", []):
                    game = q.get("game")
                    start = kickoffs.get(game)
                    if start is None or q.get("market") != "spread":
                        continue
                    if event_matches_kickoff(q.get("event_ticker"), start.isoformat()) is False:
                        continue
                    seen = _parse_time(q.get("seen_at") or "")
                    if seen is None or seen > start:
                        continue
                    if game not in newest or seen > newest[game]:
                        newest[game] = seen
    rows = []
    for game, start in sorted(kickoffs.items()):
        if start is None or start > moment:
            continue
        seen = newest.get(game)
        age = None if seen is None else (start - seen).total_seconds()
        rows.append({
            "game": game,
            "kickoff": start.isoformat(),
            "closeSeenAt": None if seen is None else seen.isoformat(),
            "closeAgeSeconds": age,
            "gradeable": age is not None and age <= max_age_seconds,
        })
    fresh = sum(r["gradeable"] for r in rows)
    missing = sum(r["closeSeenAt"] is None for r in rows)
    return {
        "asOf": moment.isoformat(),
        "maximumCloseAgeSeconds": max_age_seconds,
        "slateGames": len(kickoffs),
        "startedGames": len(rows),
        "gradeableCloses": int(fresh),
        "staleCloses": len(rows) - int(fresh) - int(missing),
        "noPriceBeforeKickoff": int(missing),
        "games": rows,
    }


def main(argv: list[str] | None = None) -> int:
    """Run the capture.

        python3 -m cfb_edge.watch --log data/opens.jsonl.gz --out opens.csv
        python3 -m cfb_edge.watch --log data/opens.jsonl.gz --once

    Leave it running through Sunday evening and Monday. It polls every five
    minutes inside the release window, half-hourly through December and early
    January when bowl numbers trickle out, and hourly the rest of the time. Any
    poll that finds a new market tightens the next one to five minutes. It
    records the first price it sees for each market and never overwrites one.
    """
    import argparse

    from .providers.oddsapi import OddsApiUnreachable, fetch_board

    p = argparse.ArgumentParser(prog="cfb_edge.watch", description=main.__doc__)
    p.add_argument("--log", default="data/opens.jsonl.gz",
                   help="immutable raw capture, appended to (default data/opens.jsonl.gz)")
    p.add_argument("--out", help="write the opens CSV that `play --opens` reads")
    p.add_argument("--once", action="store_true", help="single poll, then exit")
    p.add_argument("--snapshot-out",
                   help="with --once, write the exact successful quote batch for prospective open locking")
    p.add_argument("--max-polls", type=int, default=None, dest="max_polls")
    p.add_argument("--rebuild", action="store_true",
                   help="skip polling; rebuild the opens CSV from the existing log")
    p.add_argument("--replay-snapshot", action="append", default=[], dest="replay",
                   help="skip polling; record a saved --snapshot-out file with its "
                        "original poll time (repeatable, applied in order)")
    p.add_argument("--slate",
                   help="the week's slate CSV. Required with --source kalshi, "
                        "because nothing in a Kalshi market says which team is "
                        "at home and a line written upside down is a sign error "
                        "on everything downstream.")
    p.add_argument("--closing-slate", dest="closing_slate",
                   help="with --source kalshi, the slate of the week being "
                        "played. Its games join the poll only when kickoff is "
                        "near, so the last price before kickoff is recorded.")
    p.add_argument("--closing-window-seconds", dest="closing_window", type=int,
                   default=CLOSING_WINDOW_SECONDS,
                   help="how long before kickoff a --closing-slate game joins "
                        f"the poll (default {CLOSING_WINDOW_SECONDS})")
    p.add_argument("--source", default="kalshi", choices=("kalshi", "oddsapi"),
                   help="where the line comes from. kalshi (the default) reads "
                        "the spread ladder and inverts it to a line: free, no "
                        "key, and measured on the venue that actually fills "
                        "you. oddsapi reads sportsbook opens, needs "
                        "ODDS_API_KEY, and costs about 8,400 credits a month.")
    p.add_argument("--window-open", action="store_true", dest="window_open",
                   help="poll nothing; exit 0 if the release window is open "
                        "and 1 if it is closed (at --at, default now)")
    p.add_argument("--at", help="with --window-open or --close-coverage, the UTC "
                                "time to test (ISO 8601)")
    p.add_argument("--close-coverage", action="store_true", dest="close_coverage",
                   help="poll nothing; report how many started games on --slate "
                        "have a price in --log within fifteen minutes of kickoff")
    p.add_argument("--regions", default="us,us2,eu",
                   help="the-odds-api regions. Billing is one credit per "
                        "region per market, so this is the main lever on cost: "
                        "the default costs 3 credits a poll and about 8,400 a "
                        "month at the schedule below; 'us' costs a third of "
                        "that and drops the low-hold European books.")
    args = p.parse_args(argv)

    if args.window_open:
        when = _parse_time(args.at) if args.at else None
        if args.at and when is None:
            print(f"cannot read --at {args.at!r} as a time")
            return 2
        is_open = in_release_window(when)
        print("release window open" if is_open else "release window closed")
        return 0 if is_open else 1

    if args.close_coverage:
        if not args.slate:
            print("--close-coverage needs --slate: the games and their kickoffs")
            return 2
        when = _parse_time(args.at) if args.at else None
        if args.at and when is None:
            print(f"cannot read --at {args.at!r} as a time")
            return 2
        report = close_coverage(args.log, args.slate, when)
        print(f"closes: {report['gradeableCloses']} of {report['startedGames']} started "
              f"games have a price within {report['maximumCloseAgeSeconds'] // 60} minutes "
              f"of kickoff ({report['staleCloses']} older, "
              f"{report['noPriceBeforeKickoff']} with none; "
              f"{report['slateGames']} games on the slate)")
        return 0

    book = OpeningBook.load(args.log)
    print(f"{len(book.opens)} markets already have a recorded open.")

    if args.replay:
        written = skipped = opened = 0
        for path in args.replay:
            try:
                snap = json.loads(Path(path).read_text(encoding="utf-8"))
                fresh, wrote = book.replay(snap)
            except (OSError, ValueError) as exc:
                print(f"cannot replay {path}: {exc}")
                return 2
            written += int(wrote)
            skipped += int(not wrote)
            opened += len(fresh)
        print(f"replayed {written} saved poll(s), skipped {skipped} already logged; "
              f"{opened} market(s) opened")
    elif not args.rebuild:
        def announce(fresh: list[Quote]) -> None:
            games = sorted({q.game for q in fresh})
            print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC] "
                  f"{len(fresh)} new markets across {len(games)} games")
            for g in games[:10]:
                lines = [q for q in fresh if q.game == g]
                print(f"    OPENED  {g}: {lines[0].line:+.1f} ({lines[0].book})")
            if len(games) > 10:
                print(f"    ... and {len(games) - 10} more")

        if args.source == "kalshi":
            from .providers.kalshi import KalshiUnreachable, board_quotes

            if not args.slate:
                print("--source kalshi needs --slate: a Kalshi market does not "
                      "say which team is at home, and the schedule is the only "
                      "thing that does.")
                return 2

            def fetch() -> PollResult:
                # The slate names the home team; a Kalshi market does not.
                games = _slate_games(args.slate)
                kickoffs = _slate_kickoffs(args.slate)
                if args.closing_slate:
                    try:
                        near = closing_fixtures(args.closing_slate,
                                                window_seconds=args.closing_window)
                    except (OSError, KeyError) as exc:
                        # The opening poll must not be lost to a closing slate
                        # that is missing or malformed.
                        print(f"closing slate unusable, polling opens only: {exc}")
                        near = {}
                    added = [g for g in near if g not in kickoffs]
                    for game in added:
                        games.append(game)
                        kickoffs[game] = near[game]
                    if added:
                        print(f"closing: {len(added)} game(s) within "
                              f"{args.closing_window // 60} min of kickoff")
                diagnostics: list[dict[str, Any]] = []
                quotes = board_quotes(
                    games=games, kickoffs=kickoffs, diagnostics=diagnostics
                )
                return PollResult(quotes, diagnostics)

            unreachable: tuple[type[Exception], ...] = (KalshiUnreachable,)
        else:
            def fetch() -> list[Quote]:
                return fetch_board(regions=args.regions)

            unreachable = (OddsApiUnreachable,)

        try:
            if args.once:
                # A diagnostic has no next poll to recover on.
                try:
                    result = fetch()
                    if isinstance(result, PollResult):
                        quotes = result.quotes
                        diagnostics = result.market_diagnostics
                    else:
                        quotes = result
                        diagnostics = []
                    # One stamp for the log line and the snapshot. A replay
                    # recognises a poll by this value, so if they differed a
                    # poll whose push did land would be written a second time.
                    stamp = datetime.now(timezone.utc).isoformat()
                    fresh = book.record(
                        quotes, polled_at=stamp, market_diagnostics=diagnostics
                    )
                    if args.snapshot_out:
                        snapshot = {
                            "schemaVersion": 1,
                            "contract": "CFB_EDGE_LIVE_QUOTE_SNAPSHOT_V1",
                            "polled_at": stamp,
                            "quotes": [q.__dict__ for q in quotes],
                        }
                        if diagnostics:
                            snapshot["marketDiagnostics"] = diagnostics
                        target = Path(args.snapshot_out)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(
                            json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8",
                        )
                    if fresh:
                        announce(fresh)
                except Exception as exc:
                    print(f"cannot capture: {exc}")
                    return 2
            else:
                watch(book, fetch, max_polls=args.max_polls, on_new=announce)
        except unreachable as exc:
            print(f"cannot capture: {exc}")
            return 2
        except KeyboardInterrupt:
            print("\nstopped.")

    if args.out:
        n = book.write_opens_csv(args.out)
        print(f"wrote {n} opening lines to {args.out}")
        print(f"next: python3 -m cfb_edge play --slate <slate> --opens {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
