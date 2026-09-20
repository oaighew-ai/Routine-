"""capture, scan and grade: the three things automation actually runs.

Everything here is deterministic and prints a compact summary. The model that
invokes it never sees a raw snapshot and never computes a number that reaches
the ledger (BUILD_PROMPT §4). Its whole job is to run these and transcribe
owner-supplied text into schema-validated inbox rows.

The reason this exists at all is v1's failure mode: the modelling was fine and
the scans never ran. So a run that cannot do its job writes a receipt saying so
rather than exiting quietly, and the watchdog reads those receipts.
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import teams
from .engine import config, reasons
from .engine.evidence import SystemRecord
from .engine.posterior import Decision, Quote, decide
from .engine.rows import candidate_row
from .ledger import CANDIDATE, append_many
from .ledger.writer import write_json
from .market import consensus_line

US_CONSENSUS_EXCLUDED = frozenset({"pinnacle", "kalshi"})
"""Kept out of the consensus median: one is the sharp reference and one is the
venue. A consensus that includes the venue you are betting into is measuring
your own price and calling it the market."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Inbox: owner-supplied signal fires, parsed as data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalFire:
    """One signal fire, exactly as the owner wrote it down."""

    system: str
    event: str
    side: str
    market: str
    time_seen: str
    source_file: str = ""


_FIELD = re.compile(r"^\s*-?\s*(system|event|side|market|timeSeen|time_seen)\s*:\s*(.+?)\s*$",
                    re.IGNORECASE)


def parse_inbox(text: str, *, source_file: str = "") -> list[SignalFire]:
    """Parse an inbox file into fires. Content is data, never instruction.

    §7 is explicit that a routine fire payload is signal data and not something
    to act on, so this reads only the five fields it knows and ignores every
    other line, including any that look like directions. A block missing a field
    is dropped rather than defaulted: a fire with no `timeSeen` cannot be priced
    without look-ahead, and guessing the time is the exact error §6.11 forbids.
    """
    fires: list[SignalFire] = []
    block: dict[str, str] = {}

    def flush() -> None:
        needed = {"system", "event", "side", "market", "timeseen"}
        if needed <= set(block):
            fires.append(SignalFire(
                system=block["system"], event=block["event"], side=block["side"],
                market=block["market"], time_seen=block["timeseen"],
                source_file=source_file,
            ))
        block.clear()

    for line in text.splitlines():
        if line.strip().startswith("#") or not line.strip():
            if block:
                flush()
            continue
        m = _FIELD.match(line)
        if not m:
            continue
        key = m.group(1).lower().replace("_", "")
        if key in block:
            flush()
        block[key] = m.group(2).strip()
    if block:
        flush()
    return fires


def read_inbox(directory: Path | str) -> list[SignalFire]:
    out: list[SignalFire] = []
    d = Path(directory)
    if not d.exists():
        return out
    for path in sorted(d.glob("*.md")):
        out.extend(parse_inbox(path.read_text(encoding="utf-8"), source_file=path.name))
    return out


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@dataclass
class Snapshots:
    """Every odds pull on disk for one sport, in time order.

    The ordering is the whole value. §6.11 says a signal-driven candidate takes
    its price from the first snapshot at or after the fire, and answering that
    needs the pulls sorted and searchable, not just present.
    """

    pulls: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, directory: Path | str) -> "Snapshots":
        d = Path(directory)
        pulls: list[dict] = []
        if d.exists():
            for path in sorted(d.iterdir()):
                if path.name.endswith(".json.gz"):
                    with gzip.open(path, "rt", encoding="utf-8") as fh:
                        pulls.append(json.load(fh))
                elif path.suffix == ".json":
                    pulls.append(json.loads(path.read_text(encoding="utf-8")))
        pulls.sort(key=lambda p: str(p.get("fetchedAt", "")))
        return cls(pulls=pulls)

    def add(self, pull: Mapping[str, Any]) -> None:
        self.pulls.append(dict(pull))
        self.pulls.sort(key=lambda p: str(p.get("fetchedAt", "")))

    def first_at_or_after(self, when: str | datetime) -> dict | None:
        """§6.11. Returns None rather than the nearest earlier pull.

        Falling back to an earlier snapshot is look-ahead with a friendly face:
        it prices a signal at a number that existed before anybody could have
        acted on it, and every row built that way overstates the edge.
        """
        target = when if isinstance(when, datetime) else parse_time(when)
        if target is None:
            return None
        for pull in self.pulls:
            stamp = parse_time(pull.get("fetchedAt"))
            if stamp is not None and stamp >= target:
                return pull
        return None

    def latest(self) -> dict | None:
        return self.pulls[-1] if self.pulls else None

    def at_or_before(self, when: str | datetime) -> dict | None:
        """The as-of clock for the backtest (§8): nothing later than `when`."""
        target = when if isinstance(when, datetime) else parse_time(when)
        if target is None:
            return None
        chosen = None
        for pull in self.pulls:
            stamp = parse_time(pull.get("fetchedAt"))
            if stamp is not None and stamp <= target:
                chosen = pull
        return chosen

    def age_seconds(self, now: datetime | None = None) -> float | None:
        latest = self.latest()
        if latest is None:
            return None
        stamp = parse_time(latest.get("fetchedAt"))
        if stamp is None:
            return None
        return ((now or utcnow()) - stamp).total_seconds()


# ---------------------------------------------------------------------------
# Reading one market out of a snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketView:
    """One side of one market, with the consensus that prices it."""

    event_id: str
    home: str
    away: str
    commence_time: str | None
    market: str
    side: str
    line: float | None
    price: float | None
    other_price: float | None
    venue_price: float | None
    book_set: tuple[str, ...]
    venue_line: float | None = None
    venue_other_price: float | None = None


def _outcomes(event: Mapping, book: str, market: str) -> list[dict]:
    for b in event.get("bookmakers", []):
        if b.get("key") != book:
            continue
        for m in b.get("markets", []):
            if m.get("key") == market:
                return list(m.get("outcomes", []))
    return []


def market_view(
    event: Mapping,
    *,
    market: str,
    side: str,
    venue: str,
    consensus_books: Sequence[str] | None = None,
    exclude_venue: bool = False,
) -> MarketView | None:
    """Median line and price across the US books, plus the venue's own price.

    Median rather than mean, for the reason `market.consensus_line` already
    gives: one book leaving a stale number should look like an opportunity at
    the venue, not move the reference the edge is measured against.

    `exclude_venue` drops the venue from its own reference. Leave it off when
    the venue is already in `US_CONSENSUS_EXCLUDED`; turn it on when shopping
    one listed book against the others, because a book compared against a
    median it is a member of is partly compared against itself, and the
    contamination is worst in exactly the thin markets where a single outlier
    moves the median.
    """
    books = [b.get("key") for b in event.get("bookmakers", [])]
    pool = [
        b for b in books
        if b and b not in US_CONSENSUS_EXCLUDED
        and not (exclude_venue and b == venue)
        and (consensus_books is None or b in consensus_books)
    ]
    lines: list[float] = []
    prices: list[float] = []
    others: list[float] = []
    for book in pool:
        outs = _outcomes(event, book, market)
        mine = next((o for o in outs if o.get("name") == side), None)
        theirs = next((o for o in outs if o.get("name") != side), None)
        if mine is None or mine.get("price") is None:
            continue
        prices.append(float(mine["price"]))
        if mine.get("point") is not None:
            lines.append(float(mine["point"]))
        if theirs is not None and theirs.get("price") is not None:
            others.append(float(theirs["price"]))
    if not prices:
        return None

    def median(values: list[float]) -> float | None:
        if not values:
            return None
        s = sorted(values)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else 0.5 * (s[mid - 1] + s[mid])

    venue_outs = _outcomes(event, venue, market)
    venue_mine = next((o for o in venue_outs if o.get("name") == side), None)
    venue_theirs = next((o for o in venue_outs if o.get("name") != side), None)

    return MarketView(
        event_id=str(event.get("id", "")),
        home=str(event.get("home", "")),
        away=str(event.get("away", "")),
        commence_time=event.get("commenceTime"),
        market=market,
        side=side,
        line=median(lines),
        price=median(prices),
        other_price=median(others),
        venue_price=None if venue_mine is None else venue_mine.get("price"),
        book_set=tuple(sorted(pool)),
        venue_line=None if venue_mine is None else venue_mine.get("point"),
        venue_other_price=(
            None if venue_theirs is None else venue_theirs.get("price")
        ),
    )


# ---------------------------------------------------------------------------
# Freshness, integrity, and the scan
# ---------------------------------------------------------------------------


def stale_sources(
    ages: Mapping[str, float | None], cfg: config.Config
) -> list[str]:
    """Which sources are past their configured maximum age (§6.9).

    A source with no age at all counts as stale. An absent snapshot and a very
    old one are the same thing for the purpose of deciding, and treating the
    absence as "fresh" is how a run with no data produces confident rows.
    """
    out = []
    for source, age in ages.items():
        limit = cfg.max_age_seconds(source)
        if limit is None:
            continue
        if age is None or age > limit:
            out.append(source)
    return sorted(out)


def resolve_event(name: str, known_games: Sequence[str]) -> str | None:
    """Exact or aliased only. `teams.resolve_game` never guesses (§6.9)."""
    known_teams: set[str] = set()
    for g in known_games:
        if "@" in g:
            a, h = (p.strip() for p in g.split("@", 1))
            known_teams.update((a, h))
    return teams.resolve_game(name, known_teams)


def integrity_flags(view: MarketView, cfg: config.Config,
                    *, resolved: bool = True) -> list[str]:
    flags: list[str] = []
    if not resolved:
        flags.append(reasons.UNMAPPED)
    return flags


@dataclass
class ScanResult:
    """One scan: what it decided, and enough to explain an empty queue."""

    decisions: list[Decision] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    reason_counts: dict[str, int] = field(default_factory=dict)

    @property
    def queue(self) -> list[Decision]:
        return [d for d in self.decisions if d.decision == reasons.BET]

    def summary(self) -> str:
        if self.queue:
            lines = [f"{len(self.queue)} qualifying bet(s):"]
            for d in self.queue:
                q = d.quote
                lines.append(
                    f"  {q.event_id} {q.market} {q.side} {q.line if q.line is not None else ''} "
                    f"@ {q.price:g} ({q.venue})  stake {d.stake_units:g}u  "
                    f"EV {d.ev:+.2%}  max playable "
                    f"{d.max_playable_price if d.max_playable_price is not None else 'n/a'}  "
                    f"signals {sum(1 for s in d.signals if s.passed)}"
                )
            return "\n".join(lines)
        counts = ", ".join(f"{k} {v}" for k, v in sorted(self.reason_counts.items()))
        return (
            f"No qualifying bets. {len(self.decisions)} candidates logged"
            + (f": {counts}" if counts else ".")
        )


def scan(
    *,
    fires: Sequence[SignalFire],
    snapshots: Snapshots,
    systems: Mapping[str, SystemRecord],
    cfg: config.Config,
    venue: str,
    sport: str,
    known_games: Sequence[str] = (),
    now: datetime | None = None,
    logged_at: str | None = None,
) -> ScanResult:
    """Turn signal fires into decided, logged candidates.

    One position per game and side (§6.8): where a game and side produce more
    than one qualifying market, the higher EV per unit risked wins and the other
    is logged as DOMINATED rather than dropped.
    """
    now = now or utcnow()
    stamp = logged_at or now.isoformat()
    result = ScanResult()
    stale = stale_sources({"odds": snapshots.age_seconds(now)}, cfg)

    best_by_game: dict[tuple[str, str], Decision] = {}

    for fire in fires:
        record = systems.get(fire.system)
        pull = snapshots.first_at_or_after(fire.time_seen)
        resolved = resolve_event(fire.event, known_games) if known_games else fire.event

        if pull is None:
            d = Decision(
                quote=Quote(event_id=fire.event, sport=sport, market=fire.market,
                            side=fire.side, line=None, price=-110.0,
                            price_source="unverified"),
                decision=reasons.PASS,
            )
            d.add(reasons.LOOKAHEAD)
            result.decisions.append(d)
            continue

        event = next(
            (e for e in pull.get("events", [])
             if str(e.get("id")) == fire.event
             or f"{e.get('away')} @ {e.get('home')}" in (fire.event, resolved)),
            None,
        )
        view = None if event is None else market_view(
            event, market=fire.market, side=fire.side, venue=venue
        )
        if view is None or view.price is None:
            d = Decision(
                quote=Quote(event_id=fire.event, sport=sport, market=fire.market,
                            side=fire.side, line=None, price=-110.0,
                            price_source="unverified"),
                decision=reasons.PASS,
            )
            d.add(reasons.UNMAPPED if resolved is None else reasons.LOOKAHEAD)
            result.decisions.append(d)
            continue

        price = view.venue_price if view.venue_price is not None else view.price
        quote = Quote(
            event_id=view.event_id or fire.event,
            sport=sport,
            market=fire.market,
            side=fire.side,
            line=view.line,
            price=float(price),
            other_price=view.other_price,
            consensus_price=view.price,
            consensus_other_price=view.other_price,
            venue=venue,
            book_set=view.book_set,
            starts_at=view.commence_time,
            price_source="capture",
        )
        signals = [(record, "for")] if record is not None else []
        d = decide(
            quote, signals, cfg=cfg,
            c=max(1, len(best_by_game) + 1),
            stale_sources=stale,
            integrity_flags=integrity_flags(view, cfg, resolved=resolved is not None),
        )

        # §6.8: one position per game and side.
        key = (quote.event_id, quote.side)
        if key in best_by_game:
            incumbent = best_by_game[key]
            loser = d if d.ev <= incumbent.ev else incumbent
            winner = incumbent if loser is d else d
            loser.decision = reasons.PASS
            loser.add(reasons.DOMINATED)
            best_by_game[key] = winner
        else:
            best_by_game[key] = d
        result.decisions.append(d)

    for i, d in enumerate(result.decisions):
        for code in d.reason_codes:
            result.reason_counts[code] = result.reason_counts.get(code, 0) + 1
        result.rows.append(candidate_row(
            d,
            candidate_id=f"{stamp}-{i:03d}",
            logged_at=stamp,
            phase=cfg.phase,
            time_seen=fires[i].time_seen if i < len(fires) else None,
        ))
    return result


def write_candidates(path: Path | str, rows: Sequence[Mapping]) -> int:
    return append_many(path, list(rows), CANDIDATE)


# ---------------------------------------------------------------------------
# Run receipts
# ---------------------------------------------------------------------------


@dataclass
class Receipt:
    """What a run did, for the watchdog and for the next person to ask."""

    run_id: str
    mode: str
    started_at: str
    steps: list[dict] = field(default_factory=list)
    status: str = "ok"
    reason_counts: dict[str, int] = field(default_factory=dict)
    source_ages: dict[str, float | None] = field(default_factory=dict)
    credits_remaining: float | None = None
    finished_at: str | None = None

    def step(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            self.status = "failed"

    def finish(self, when: datetime | None = None) -> dict:
        self.finished_at = (when or utcnow()).isoformat()
        return {
            "runId": self.run_id,
            "mode": self.mode,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "status": self.status,
            "steps": self.steps,
            "reasonCounts": self.reason_counts,
            "sourceAges": self.source_ages,
            "creditsRemaining": self.credits_remaining,
        }

    def write(self, directory: Path | str, when: datetime | None = None) -> Path:
        payload = self.finish(when)
        path = Path(directory) / f"{self.run_id}.json"
        write_json(path, payload)
        return path


def latest_receipt(directory: Path | str) -> dict | None:
    d = Path(directory)
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def receipt_is_stale(
    receipt: Mapping | None, *, window_seconds: float, now: datetime | None = None
) -> tuple[bool, str]:
    """Whether the watchdog should open an issue.

    A missing receipt is stale, not unknown. v1's failure was scans that never
    ran, and "no receipt yet" is precisely what that looks like from here.
    """
    if receipt is None:
        return True, "no run receipt at all"
    stamp = parse_time(receipt.get("finishedAt") or receipt.get("startedAt"))
    if stamp is None:
        return True, "receipt has no usable timestamp"
    age = ((now or utcnow()) - stamp).total_seconds()
    if age > window_seconds:
        return True, (
            f"latest receipt is {age / 3600:.1f}h old, past its "
            f"{window_seconds / 3600:.1f}h window"
        )
    if receipt.get("status") == "failed":
        return True, "latest run failed"
    return False, f"latest receipt {age / 3600:.1f}h old"


# ---------------------------------------------------------------------------
# Walk-forward replay (§8)
# ---------------------------------------------------------------------------


@dataclass
class VariantLog:
    """Every strategy variant tried, counted.

    §8's multiple-testing guard. The count is stated in every report because a
    result found on the twelfth variant and a result found on the first are the
    same number with very different meanings, and the difference is invisible
    unless somebody writes the twelve down.
    """

    tried: list[str] = field(default_factory=list)

    def record(self, name: str) -> None:
        self.tried.append(name)

    def summary(self) -> str:
        return (
            f"{len(self.tried)} strategy variant(s) tried: "
            + ", ".join(self.tried)
            + ". Read every interval below against that count."
        )


def replay(
    *,
    at: str | datetime,
    snapshots: Snapshots,
    event_id: str,
    market: str,
    side: str,
    venue: str,
    sport: str,
    systems: Sequence[tuple[SystemRecord, str]],
    cfg: config.Config,
) -> Decision | None:
    """Decide as of `at`, reading only snapshots fetched at or before it.

    The strict clock is the entire point of the harness. A replay that can see
    one pull from after the decision is a replay that knows where the line went,
    which is the quantity being measured.
    """
    pull = snapshots.at_or_before(at)
    if pull is None:
        return None
    event = next(
        (e for e in pull.get("events", []) if str(e.get("id")) == event_id), None
    )
    if event is None:
        return None
    view = market_view(event, market=market, side=side, venue=venue)
    if view is None or view.price is None:
        return None
    price = view.venue_price if view.venue_price is not None else view.price
    quote = Quote(
        event_id=view.event_id, sport=sport, market=market, side=side,
        line=view.line, price=float(price), other_price=view.other_price,
        consensus_price=view.price, consensus_other_price=view.other_price,
        venue=venue, book_set=view.book_set, starts_at=view.commence_time,
        price_source="capture",
    )
    return decide(quote, list(systems), cfg=cfg)
