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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping

# Books post look-ahead numbers for the coming week from Sunday evening, and
# the rest of the market fills in through Monday and Tuesday. These are the
# hours worth polling densely, in UTC. Sunday 22:00 UTC is Sunday afternoon in
# the United States, which is early enough to catch Circa-style early releases.
RELEASE_WINDOW_UTC = {
    6: range(22, 24),   # Sunday evening
    0: range(0, 24),    # Monday, all day
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


def in_release_window(when: datetime | None = None) -> bool:
    """Whether now is when a regular week's opening lines are likely to appear."""
    when = when or datetime.now(timezone.utc)
    hours = RELEASE_WINDOW_UTC.get(when.weekday())
    return hours is not None and when.hour in hours


def in_postseason_window(when: datetime | None = None) -> bool:
    """Whether now is bowl and playoff season, when the board fills in slowly."""
    when = when or datetime.now(timezone.utc)
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

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.game, self.book, self.market)

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
                for q in rec.get("quotes", []):
                    try:
                        quote = Quote(**q)
                    except TypeError:
                        continue
                    book._observe(quote)
        return book

    def record(self, quotes: Iterable[Quote]) -> list[Quote]:
        """Append a poll to the log and return the quotes that were new.

        The return value is the point of the whole exercise: those are markets
        that had not been seen before, so their price is an opening line.
        """
        quotes = list(quotes)
        fresh = [q for q in quotes if q.key not in self.opens]
        for q in quotes:
            self._observe(q)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        opener = gzip.open if self.path.suffix == ".gz" else open
        with opener(self.path, "at", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "polled_at": datetime.now(timezone.utc).isoformat(),
                "quotes": [q.__dict__ for q in quotes],
            }) + "\n")
        return fresh

    def _observe(self, quote: Quote) -> None:
        """Fold one quote into the derived views, in a single pass."""
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

    def write_opens_csv(self, path: str | Path, market: str = "spread") -> int:
        """Emit exactly what `cfb_edge play --opens` consumes."""
        import csv

        rows = sorted(self.consensus_opens(market).items())
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["game", "opening_line"])
            w.writerows(rows)
        return len(rows)


# A fetcher takes nothing and returns quotes. Injectable so the loop can be
# tested without a network, and so a new provider is one function.
Fetcher = Callable[[], list[Quote]]


def run_once(book: OpeningBook, fetch: Fetcher) -> list[Quote]:
    """One poll. Returns the newly-opened markets."""
    return book.record(fetch())


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
    p.add_argument("--max-polls", type=int, default=None, dest="max_polls")
    p.add_argument("--rebuild", action="store_true",
                   help="skip polling; rebuild the opens CSV from the existing log")
    p.add_argument("--regions", default="us,us2,eu",
                   help="the-odds-api regions. Billing is one credit per "
                        "region per market, so this is the main lever on cost: "
                        "the default costs 3 credits a poll and about 8,400 a "
                        "month at the schedule below; 'us' costs a third of "
                        "that and drops the low-hold European books.")
    args = p.parse_args(argv)

    book = OpeningBook.load(args.log)
    print(f"{len(book.opens)} markets already have a recorded open.")

    if not args.rebuild:
        def announce(fresh: list[Quote]) -> None:
            games = sorted({q.game for q in fresh})
            print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC] "
                  f"{len(fresh)} new markets across {len(games)} games")
            for g in games[:10]:
                lines = [q for q in fresh if q.game == g]
                print(f"    OPENED  {g}: {lines[0].line:+.1f} ({lines[0].book})")
            if len(games) > 10:
                print(f"    ... and {len(games) - 10} more")

        def fetch() -> list[Quote]:
            return fetch_board(regions=args.regions)

        try:
            watch(book, fetch,
                  max_polls=1 if args.once else args.max_polls, on_new=announce)
        except OddsApiUnreachable as exc:
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
