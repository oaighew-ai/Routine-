"""Fetch a week's Kalshi board, run the gate, print the card.

    python3 -m cfb_edge.board --week 2
    python3 -m cfb_edge.board --week 2 --book lines.csv --size 200
    python3 -m cfb_edge.board --offline captured.json --json card.json

Run it where the network allows api.elections.kalshi.com. A locked-down egress
policy shows up here as a clear error naming the host rather than an empty
board, because an empty board and a blocked board mean opposite things and
must never look alike.

Book prices are the leg that needs a key. Without `--book` the run falls back
to treating a half-number line as the book's own coin flip at 50c, which is an
assumption rather than an observation, and the gate rejects those by default.
Pass --allow-assumed to see them anyway, clearly marked.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .gate import Candidate, DEFAULT_SIZE, MIN_NET_EV, evaluate, rank
from .providers import kalshi


def load_book_lines(path: str | Path) -> dict[str, float]:
    """Map a Kalshi ticker to the book's de-vigged fair value in cents."""
    out: dict[str, float] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ticker = (row.get("ticker") or "").strip()
            fair = (row.get("fair_cents") or "").strip()
            if ticker and fair:
                out[ticker] = float(fair)
    return out


def build_candidates(
    markets: dict[str, list[dict]],
    books: dict[str, kalshi.Book],
    fair: dict[str, float],
    *,
    size: int,
) -> list[Candidate]:
    out: list[Candidate] = []
    for market_type, rows in markets.items():
        for m in rows:
            ticker = m.get("ticker", "")
            book = books.get(ticker)
            if book is None:
                continue
            entry = book.vwap(size)
            if entry is None:
                # Cannot fill the size; record it so the gate can say why.
                entry = book.best_ask if book.best_ask is not None else 0.0
            observed = fair.get(ticker)
            out.append(
                Candidate(
                    ticker=ticker,
                    label=(m.get("yes_sub_title") or m.get("title") or ticker)[:34],
                    game=m.get("event_ticker", ""),
                    market=market_type,
                    entry_cents=float(entry),
                    fair_cents=observed if observed is not None else 50.0,
                    fair_is_assumed=observed is None,
                    depth=book.depth,
                    spread=book.spread,
                )
            )
    return out


def run(args: argparse.Namespace, *, opener: kalshi.Opener | None = None) -> int:
    """Price the live board, or replay a captured one.

    `opener` is injected the way `slate.build` takes one, so that the
    unreachable-network path can be tested without depending on whether the
    machine running the test happens to be able to reach Kalshi. A test that
    asserts a blocked network by actually being blocked passes only where the
    egress policy blocks it, and reports a false pass everywhere else.
    """
    if args.offline:
        blob = json.loads(Path(args.offline).read_text(encoding="utf-8"))
        markets = blob["markets"]
        books = {t: kalshi.parse_book(t, p) for t, p in blob["books"].items()}
    else:
        try:
            markets = kalshi.fetch_series(opener=opener)
            books = {}
            for rows in markets.values():
                for m in rows:
                    ticker = m.get("ticker")
                    if ticker:
                        books[ticker] = kalshi.fetch_book(ticker, opener=opener)
        except kalshi.KalshiUnreachable as exc:
            print(f"could not fetch the board.\n{exc}", file=sys.stderr)
            return 2

    fair = load_book_lines(args.book) if args.book else {}
    candidates = build_candidates(markets, books, fair, size=args.size)
    results = [
        evaluate(
            c,
            size=args.size,
            min_net_ev=args.min_net_ev,
            allow_assumed_fair=args.allow_assumed,
        )
        for c in candidates
    ]

    if args.show_all:
        for r in sorted(results, key=lambda r: r.net_ev, reverse=True):
            print(r.line())
        print()

    card = rank(results)
    assumed = sum(1 for c in candidates if c.fair_is_assumed)
    print(
        f"{len(candidates)} markets priced, {assumed} on an assumed 50c fair value, "
        f"{len(card)} cleared all six checks."
    )
    if not fair:
        print(
            "No book file supplied, so every fair value here is assumed. Pass "
            "--book to compare against real de-vigged sportsbook prices."
        )
    if not card:
        print("\nNo bets. An empty card is a result, not a failure.")
    for i, r in enumerate(card, 1):
        print(
            f"\n{i}. {r.candidate.label} ({r.candidate.market})"
            f"\n   {r.candidate.game} | entry {r.candidate.entry_cents:.1f}c"
            f"\n   gross {r.gross_cents:+.2f}c - fee {r.fee_cents:.2f}c "
            f"= net {r.net_cents:+.2f}c ({r.net_ev:+.2%})"
            f"\n   stake {r.stake_fraction:.2%} of bankroll"
        )

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                [
                    {
                        "name": r.candidate.label,
                        "game": r.candidate.game,
                        "mkt": r.candidate.market,
                        "entry": r.candidate.entry_cents,
                        "gross": r.gross_cents,
                        "basis": "assumed 50¢" if r.candidate.fair_is_assumed else "book de-vig",
                    }
                    for r in sorted(results, key=lambda r: r.net_ev, reverse=True)
                ],
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json} — paste into the CFB Edge Net page")
    return 0


def main(argv: list[str] | None = None, *,
         opener: kalshi.Opener | None = None) -> int:
    p = argparse.ArgumentParser(prog="cfb_edge.board", description=__doc__)
    p.add_argument("--week", type=int, help="label only; Kalshi returns open markets")
    p.add_argument("--book", help="CSV of ticker,fair_cents from your book source")
    p.add_argument("--size", type=int, default=DEFAULT_SIZE,
                   help=f"contracts the fill must support (default {DEFAULT_SIZE})")
    p.add_argument("--min-net-ev", type=float, default=MIN_NET_EV, dest="min_net_ev")
    p.add_argument("--allow-assumed", action="store_true", dest="allow_assumed",
                   help="admit markets whose fair value is an assumed 50c")
    p.add_argument("--show-all", action="store_true", dest="show_all")
    p.add_argument("--offline", help="replay a captured JSON board")
    p.add_argument("--json", help="write the board for the CFB Edge Net page")
    return run(p.parse_args(argv), opener=opener)


if __name__ == "__main__":
    raise SystemExit(main())
