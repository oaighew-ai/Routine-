"""Record every signal, not only the ones worth betting.

The stop rule needs about a hundred graded observations to say anything, and a
card produces two bets a week. At that rate a dead strategy goes unnoticed for
three seasons, which is documented in `stopping.py` and is the single largest
practical problem this project has.

The signal, though, fires on roughly half of every board with a captured
opening line. Only the few that clear their own fee reach a card. Nothing has
to be at risk to measure closing line value, so every signal is recorded here
and graded the same way a bet is, and the sample that decides whether the edge
exists grows about ten times faster than the sample that costs money.

A paper signal is written as a `LoggedBet` with a stake of zero, into its own
file. Same shape means `clv` and the stop rule read it with no special case;
its own file means paper results can never be mistaken for realised profit.
Price CLV is left blank rather than invented, because no price was quoted: a
paper signal measures the line and says nothing about what it could be filled
at, which is exactly the distinction the Kalshi propagation question turns on.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from .clv import BET_FIELDS, LoggedBet, load_bets, save_bets
from .strategy import signal_side

# A stake of zero is what marks a row as paper, and it keeps realised profit at
# zero however the report is read.
PAPER_STAKE = 0.0
# `clv` needs a price to load a row. This one is never used for anything: paper
# rows carry no closing price, so `price_clv` returns None and only line CLV,
# which is what the stop rule reads, is computed.
NOMINAL_PRICE = -110.0


def signals_for(
    slate: dict[str, float], opens: dict[str, float], *,
    date: str | None = None, min_disagreement: float = 4.0,
    week: int | None = None,
) -> list[LoggedBet]:
    """Every game where the model disagrees with the open by enough to act.

    `slate` maps "Away @ Home" to the model's projected home margin, `opens`
    maps the same key to the opening home line.
    """
    day = date or _dt.date.today().isoformat()
    out: list[LoggedBet] = []
    for game, projected in sorted(slate.items()):
        if game not in opens or "@" not in game:
            continue
        away, home = (s.strip() for s in game.split("@", 1))
        open_line = opens[game]
        side, _gap = signal_side(home, away, projected_margin=projected,
                                 opening_home_line=open_line,
                                 min_disagreement=min_disagreement, week=week)
        if side is None:
            continue
        # The line from the backed side's view: the home number as posted, or
        # its negation for the away side.
        taken = open_line if side == home else -open_line
        out.append(LoggedBet(date=day, away=away, home=home, side=side,
                             line_taken=float(taken), price_taken=NOMINAL_PRICE,
                             stake=PAPER_STAKE))
    return out


def record(path: str | Path, signals: list[LoggedBet]) -> tuple[int, int]:
    """Write signals to the log, skipping games already recorded.

    Returns (added, skipped). Re-running a week is harmless: a signal already
    present is left as it is rather than duplicated, so the same command can be
    run again after more of the board has opened.
    """
    path = Path(path)
    existing = load_bets(path) if path.exists() else []
    seen = {(b.date, b.away, b.home) for b in existing}
    added = [s for s in signals if (s.date, s.away, s.home) not in seen]
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        save_bets(existing + added, path)
    return len(added), len(signals) - len(added)


def grade(path: str | Path, closes: dict[str, float]) -> tuple[int, int]:
    """Fill in closing lines from a map of "Away @ Home" to the home close.

    Returns (graded, still open). Only rows without a closing line are
    touched, so grading twice cannot move a number that is already recorded.
    """
    path = Path(path)
    bets = load_bets(path)
    graded = 0
    for b in bets:
        if b.closing_line is not None:
            continue
        close = closes.get(f"{b.away} @ {b.home}")
        if close is None:
            continue
        b.closing_line = float(close if b.side == b.home else -close)
        graded += 1
    if graded:
        save_bets(bets, path)
    return graded, sum(1 for b in bets if b.closing_line is None)


__all__ = ["signals_for", "record", "grade", "PAPER_STAKE", "BET_FIELDS"]
