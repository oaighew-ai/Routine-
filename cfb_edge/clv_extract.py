"""Reconstruct closing line value from a raw capture series.

The capture layer records a dense price series per market. CLV is a pure
function of that series, so it can be rebuilt at any time, which is exactly why
the recorder shipped first. This is the extractor that turns the series into
the numbers a promotion decision needs.

Three things it does that a naive version does not.

**It measures against real horizons.** A single entry-to-close number hides
whether an edge decays in five minutes or holds to kickoff, and those imply
opposite things about how fast you must act. Each entry is measured at several
offsets from kickoff as well as at the close.

**It separates markets that moved from markets that did not.** A shadow log
dominated by contracts whose price never changed reports a mean CLV near zero
and a beat-close rate near zero, and neither figure says anything about
predictive skill: the market simply never gave an opinion. Those rows are
counted and reported separately rather than diluting the sample.

**It reports the price as probability, because on Kalshi it already is one.**
A cent of CLV is a point of win probability with no de-vig step, which is the
one genuine measurement advantage of an exchange over a sportsbook.
"""

from __future__ import annotations

import gzip
import json
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

# Offsets before kickoff, in minutes, at which each entry is also measured.
DEFAULT_HORIZONS = (5, 30, 120, 1440)

# Field names in the raw records. Overridable, because the recorder's schema is
# its own business and this module should not force a rename to be useful.
DEFAULT_FIELDS = {
    "ticker": "ticker",
    "timestamp": "ts",
    "price": "yes_mid",
    "kickoff": "kickoff",
}


def _parse_time(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def read_runs(paths: Iterable[str | Path]) -> Iterator[dict]:
    """Stream records out of gzipped JSONL run files.

    Malformed lines are skipped rather than fatal: a truncated tail on the most
    recent run is normal if capture was interrupted, and losing that run's last
    few records should not cost you the other thirteen.
    """
    for path in paths:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


@dataclass
class Series:
    """One market's price history, ordered in time."""

    ticker: str
    times: list[datetime] = field(default_factory=list)
    prices: list[float] = field(default_factory=list)
    kickoff: datetime | None = None

    def add(self, when: datetime, price: float) -> None:
        self.times.append(when)
        self.prices.append(price)

    def sort(self) -> None:
        if not self.times:
            return
        order = sorted(range(len(self.times)), key=lambda i: self.times[i])
        self.times = [self.times[i] for i in order]
        self.prices = [self.prices[i] for i in order]

    @property
    def moved(self) -> bool:
        """Whether the price ever changed. A flat series carries no information
        about skill and is excluded from the headline CLV figures."""
        return len(set(self.prices)) > 1

    @property
    def close(self) -> float | None:
        """Last observation at or before kickoff, which is the closing price.
        Falls back to the final observation when kickoff is unknown."""
        if not self.prices:
            return None
        if self.kickoff is None:
            return self.prices[-1]
        idx = bisect_right(self.times, self.kickoff) - 1
        return self.prices[idx] if idx >= 0 else None

    def price_at(self, when: datetime) -> float | None:
        """Last observation at or before `when`. No interpolation: a price you
        could not have seen yet is not a price."""
        idx = bisect_right(self.times, when) - 1
        return self.prices[idx] if idx >= 0 else None


def build_series(
    records: Iterable[Mapping],
    *,
    fields: Mapping[str, str] = DEFAULT_FIELDS,
    kickoffs: Mapping[str, datetime] | None = None,
) -> dict[str, Series]:
    out: dict[str, Series] = {}
    for rec in records:
        ticker = rec.get(fields["ticker"])
        when = _parse_time(rec.get(fields["timestamp"]))
        price = rec.get(fields["price"])
        if not ticker or when is None or price is None:
            continue
        series = out.get(ticker)
        if series is None:
            series = out[ticker] = Series(ticker=ticker)
        series.add(when, float(price))
        if series.kickoff is None:
            series.kickoff = _parse_time(rec.get(fields["kickoff"]))
    if kickoffs:
        for ticker, kick in kickoffs.items():
            if ticker in out:
                out[ticker].kickoff = kick
    for series in out.values():
        series.sort()
    return out


@dataclass(frozen=True)
class EntryCLV:
    """One logged entry, measured against the market's later opinion."""

    ticker: str
    game: str
    entry_price: float
    close_price: float | None
    moved: bool
    horizons: dict[int, float | None]

    @property
    def clv(self) -> float | None:
        """Cents of closing line value. On Kalshi this is already a probability
        difference, so it needs no de-vig and is additive across entries."""
        if self.close_price is None:
            return None
        return self.close_price - self.entry_price

    @property
    def beat_close(self) -> bool | None:
        c = self.clv
        return None if c is None else c > 0


def measure(
    entries: Sequence[Mapping],
    series: Mapping[str, Series],
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> list[EntryCLV]:
    """Grade each logged entry against its market's series.

    An entry is `{"ticker": ..., "game": ..., "entry_price": ...}`. Entries on
    a market with no captured series are dropped, because grading against a
    series you do not have is guesswork.
    """
    out: list[EntryCLV] = []
    for e in entries:
        ticker = e.get("ticker")
        s = series.get(ticker)
        if s is None or not s.prices:
            continue
        marks: dict[int, float | None] = {}
        for minutes in horizons:
            if s.kickoff is None:
                marks[minutes] = None
                continue
            from datetime import timedelta

            at = s.kickoff - timedelta(minutes=minutes)
            price = s.price_at(at)
            marks[minutes] = (
                None if price is None else price - float(e["entry_price"])
            )
        out.append(
            EntryCLV(
                ticker=ticker,
                game=e.get("game", ""),
                entry_price=float(e["entry_price"]),
                close_price=s.close,
                moved=s.moved,
                horizons=marks,
            )
        )
    return out


@dataclass
class CLVSummary:
    entries: int
    graded: int
    moved: int
    flat: int
    mean_clv: float
    mean_clv_moved_only: float
    beat: int
    tied: int
    lost: int
    by_horizon: dict[int, float]

    @property
    def beat_rate(self) -> float:
        live = self.beat + self.lost
        return self.beat / live if live else 0.0

    def summary(self) -> str:
        return (
            f"{self.entries} entries, {self.graded} with a closing price.\n"
            f"{self.moved} moved, {self.flat} never moved "
            f"({self.flat / self.graded:.0%} of the sample carries no information)\n"
            f"mean CLV {self.mean_clv:+.3f}c overall, "
            f"{self.mean_clv_moved_only:+.3f}c among markets that moved\n"
            f"beat {self.beat} / tied {self.tied} / lost {self.lost} "
            f"({self.beat_rate:.1%} of decided)\n"
            + "\n".join(
                f"  {m:>5}m before kickoff: {v:+.3f}c"
                for m, v in sorted(self.by_horizon.items(), reverse=True)
            )
        )


def summarise(measured: Sequence[EntryCLV]) -> CLVSummary:
    graded = [m for m in measured if m.clv is not None]
    moved = [m for m in graded if m.moved]
    clvs = [m.clv for m in graded]
    by_h: dict[int, float] = {}
    if graded:
        for minutes in graded[0].horizons:
            vals = [m.horizons[minutes] for m in graded
                    if m.horizons.get(minutes) is not None]
            if vals:
                by_h[minutes] = sum(vals) / len(vals)
    return CLVSummary(
        entries=len(measured),
        graded=len(graded),
        moved=len(moved),
        flat=len(graded) - len(moved),
        mean_clv=sum(clvs) / len(clvs) if clvs else 0.0,
        mean_clv_moved_only=(
            sum(m.clv for m in moved) / len(moved) if moved else 0.0
        ),
        beat=sum(1 for c in clvs if c > 0),
        tied=sum(1 for c in clvs if c == 0),
        lost=sum(1 for c in clvs if c < 0),
        by_horizon=by_h,
    )
