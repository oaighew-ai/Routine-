"""Command line entry point.

    python3 -m cfb_edge rate  --games data/games.csv --priors data/priors.csv
    python3 -m cfb_edge card  --games data/games.csv --priors data/priors.csv \
                              --slate data/slate.csv
    python3 -m cfb_edge clv   --bets data/bets.csv
    python3 -m cfb_edge play  --slate data/example_play.csv --book-price -105
"""

from __future__ import annotations

import argparse
import csv
import sys

from . import clv as clv_mod
from . import data as data_mod
from .edge import evaluate, rank_card
from .ratings import DEFAULT_HFA, solve_ratings
from .staking import DEFAULT_MAX_WEEKLY_EXPOSURE, apply_portfolio_cap


def _load_model(args: argparse.Namespace):
    games = data_mod.load_games(args.games) if args.games else []
    priors = data_mod.load_priors(args.priors) if args.priors else {}
    return solve_ratings(
        games, priors=priors, prior_weight=args.prior_weight, hfa=args.hfa
    )


def cmd_rate(args: argparse.Namespace) -> int:
    model = _load_model(args)
    if not model.ratings:
        print("no teams to rate", file=sys.stderr)
        return 1
    print(f"{len(model.ratings)} teams, converged={model.converged} "
          f"in {model.iterations} iterations, HFA {model.hfa:.2f}\n")
    ordered = sorted(model.ratings.items(), key=lambda kv: kv[1], reverse=True)
    limit = args.top or len(ordered)
    for rank, (team, rating) in enumerate(ordered[:limit], start=1):
        print(f"{rank:3d}. {team:<28} {rating:+7.2f}  "
              f"({model.games_played.get(team, 0)} games)")
    return 0


def cmd_card(args: argparse.Namespace) -> int:
    model = _load_model(args)
    slate = data_mod.load_slate(args.slate)

    candidates = [
        evaluate(
            model,
            matchup,
            market_home_line=market["market_home_line"],
            home_price=market["home_price"],
            away_price=market["away_price"],
            market_total=market["market_total"],
            min_edge=args.min_edge,
        )
        for matchup, market in slate
    ]

    if args.show_all:
        print("Every game considered:\n")
        for c in candidates:
            print("  " + c.describe())
        print()

    bets = rank_card(candidates)
    if not bets:
        print(f"No bets. {len(candidates)} games considered, none cleared the "
              f"threshold.\n\nAn empty card is a result, not a failure. The "
              f"model is built to return nothing when it knows nothing.")
        return 0

    scaled = apply_portfolio_cap(
        [c.stake.recommended for c in bets], max_total=args.max_exposure
    )
    total = sum(scaled)
    print(f"{len(bets)} bets from {len(candidates)} games. "
          f"Total exposure {total:.2%} of bankroll.\n")
    for c, stake in zip(bets, scaled):
        flag = " (scaled by portfolio cap)" if stake < c.stake.recommended - 1e-12 else ""
        print(f"  {c.team} {c.line:+.1f} @ {c.price:+.0f}")
        print(f"    {c.away} at {c.home} | model {c.model_line:+.1f} vs "
              f"market {c.market_line:+.1f}, blend weight {c.model_weight:.2f} "
              f"-> {c.blended_line:+.1f}")
        print(f"    edge {c.edge_points:+.2f} pts | win "
              f"{c.outcome.win_excluding_push:.1%} | EV "
              f"{c.stake.expected_value:+.2%} | stake {stake:.2%}{flag}")
    return 0


def cmd_clv(args: argparse.Namespace) -> int:
    from .stopping import evaluate

    bets = clv_mod.load_bets(args.bets)
    if not bets:
        print("no bets logged yet")
        return 0
    print(clv_mod.build_report(bets).summary())
    # Printed with the scorecard rather than behind its own command. A stop
    # rule you have to remember to run is one you consult when you already
    # suspect the answer.
    graded = [b.line_clv for b in bets if b.line_clv is not None]
    print()
    print(evaluate(graded).summary())
    return 0


def _price_from(args: argparse.Namespace, cents_attr: str, price_attr: str,
                label: str) -> float | None:
    """One price, from either an exchange quote in cents or an American price."""
    from .market import probability_to_american

    cents, price = getattr(args, cents_attr), getattr(args, price_attr)
    if cents is not None and price is not None:
        raise SystemExit(f"give {label} in cents or as an American price, not both")
    if cents is not None:
        if not 0 < cents < 100:
            raise SystemExit(f"{label} in cents must be between 1 and 99, got {cents}")
        return probability_to_american(cents / 100.0)
    return price


def cmd_log(args: argparse.Namespace) -> int:
    """Record a bet at the moment it is placed.

    The price you actually got is the whole measurement. It cannot be
    reconstructed on Tuesday, so a bet that is not logged now is a bet that
    never enters the scorecard.
    """
    import datetime as _dt

    from .clv import LoggedBet, append_bet

    if "@" not in args.game:
        raise SystemExit(f"game must read 'Away @ Home', got {args.game!r}")
    away, home = (s.strip() for s in args.game.split("@", 1))
    side = args.side.strip()
    if side.lower() not in (away.lower(), home.lower()):
        raise SystemExit(f"side must be {away!r} or {home!r}, got {side!r}")

    # The card prints an exchange play as "Kansas at +3", meaning a contract
    # that pays if Kansas wins by more than three. In betting convention that
    # is Kansas laying three, so the stored line is negative. Nobody should
    # have to do that flip by hand: --strike takes the card's number and
    # --line takes a sportsbook's, and the echo below states which was meant.
    if (args.strike is None) == (args.line is None):
        raise SystemExit("give exactly one of --strike (exchange) or --line (book)")
    line = -abs(args.strike) if args.strike is not None else args.line

    price = _price_from(args, "cents", "price", "the price")
    if price is None:
        raise SystemExit("give the price with --cents (exchange) or --price (book)")

    bet = LoggedBet(
        date=args.date or _dt.date.today().isoformat(),
        away=away, home=home, side=side,
        line_taken=float(line), price_taken=float(price), stake=float(args.stake),
    )
    n = append_bet(args.bets, bet)

    verb = "laying" if line < 0 else "taking"
    print(f"logged bet {n} to {args.bets}")
    print(f"  {bet.date}  {side} {verb} {abs(line):g} in {away} @ {home}")
    print(f"  at {price:+.0f} American"
          + (f" ({args.cents}c)" if args.cents is not None else "")
          + f", staking {bet.stake:.2%} of bankroll")
    print(f"  wins if {side} beat the number, so if their margin {'exceeds' if line < 0 else 'is above'} "
          f"{-line:+g}")
    return 0


def cmd_settle(args: argparse.Namespace) -> int:
    """Fill in where the market closed, which is what CLV is measured against."""
    from .clv import BetNotFound, settle_bet

    close = args.closing_line
    if close is not None and args.closing_strike is not None:
        raise SystemExit("give the close as --closing-line or --closing-strike, not both")
    if args.closing_strike is not None:
        close = -abs(args.closing_strike)

    try:
        bet = settle_bet(
            args.bets, game=args.game, date=args.date, closing_line=close,
            closing_price=_price_from(args, "closing_cents", "closing_price",
                                      "the closing price"),
            closing_opposite_price=_price_from(
                args, "closing_opposite_cents", "closing_opposite_price",
                "the closing opposite price"),
            result=args.result,
        )
    except (BetNotFound, ValueError) as exc:
        print(exc)
        return 2

    print(f"settled {bet.side} in {bet.away} @ {bet.home} ({bet.date})")
    if bet.line_clv is not None:
        moved = bet.line_clv
        verdict = ("beat the close" if moved > 0 else
                   "tied the close" if moved == 0 else "lost to the close")
        print(f"  took {bet.line_taken:+g}, closed {bet.closing_line:+g} "
              f"-> {moved:+.1f} points, {verdict}")
        print(f"  which is {bet.probability_clv():+.2%} of win probability")
    if bet.price_clv is not None:
        print(f"  price CLV {bet.price_clv:+.2%}")
    if bet.result:
        print(f"  result {bet.result}, profit {bet.profit():+.4f} units")
    return 0


def _slate_and_opens(slate_path, opens_path):
    """Projections and opening lines, reconciled by the alias map."""
    from .teams import match_games

    slate = {}
    with open(slate_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            slate[row["game"].strip()] = float(row["projected_margin"])

    raw = {}
    with open(opens_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            line = (row.get("opening_line") or row.get("open") or "").strip()
            if line:
                raw[row["game"].strip()] = float(line)

    report = match_games(list(raw), list(slate))
    opens = {canon: raw[foreign] for foreign, canon in report.matched.items()}
    return slate, opens, report


def cmd_signals(args: argparse.Namespace) -> int:
    """Record every signal on the board, whether or not it is worth betting.

    The card is two bets a week and the stop rule needs about a hundred graded
    observations. The signal fires far more often than the card does, and
    nothing has to be at risk to measure closing line value, so recording all
    of them is what makes the rule answerable this season instead of in 2029.
    """
    from .paper import record, signals_for

    slate, opens, report = _slate_and_opens(args.slate, args.opens)
    if report.unmatched:
        print(report.summary() + "\n")
    signals = signals_for(slate, opens, date=args.date,
                          min_disagreement=args.min_disagreement)
    added, skipped = record(args.out, signals)
    print(f"{len(opens)} games had an opening line; the model disagreed with "
          f"{len(signals)} of them by {args.min_disagreement:.1f} points or more.")
    print(f"recorded {added} to {args.out}"
          + (f", {skipped} already there" if skipped else ""))
    for s in signals[:12]:
        print(f"    {s.side} at {s.line_taken:+g} in {s.away} @ {s.home}")
    if len(signals) > 12:
        print(f"    ... and {len(signals) - 12} more")
    if added:
        print(f"\nGrade them once the market has closed:\n"
              f"    python3 -m cfb_edge grade --signals {args.out} --log <capture log>")
    return 0


def cmd_grade(args: argparse.Namespace) -> int:
    """Fill in closing lines, taken from the same capture log as the opens."""
    from .paper import grade
    from .watch import OpeningBook

    book = OpeningBook.load(args.log)
    closes = book.consensus_closes()
    if not closes:
        print(f"no quotes in {args.log}. Nothing to grade against.")
        return 2
    ungated = book.ungated_games()
    graded, still_open = grade(args.signals, closes)
    print(f"{len(closes)} games have a pre-kickoff closing price in {args.log}")
    if ungated:
        print(f"WARNING: {len(ungated)} of them carry no kickoff time, so their "
              f"close could not be gated and may be an in-play number. Logs "
              f"captured before commence_time was recorded look like this.")
    print(f"graded {graded} signals; {still_open} still have no closing line")
    print("\nA close is only as late as the capture ran. If polling stopped "
          "early, this grades against that moment and not the close. Quotes "
          "seen after kickoff are excluded.")
    if graded:
        print(f"\n    python3 -m cfb_edge clv --bets {args.signals}")
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    """The strategy the evidence supports, run over a slate."""
    from .strategy import (DEFAULT_CLV_POINTS, Venue, build_card, find_plays,
                           signal_side)

    venues = [Venue("exchange", is_exchange=True)]
    if args.book_price is not None:
        venues.append(Venue(f"book {args.book_price:+.0f}",
                            american_price=args.book_price))

    slate_games = [r["game"].strip() for r in
                   csv.DictReader(open(args.slate, newline="", encoding="utf-8"))]

    opens = {}
    if args.opens:
        from .teams import match_games

        raw = {}
        with open(args.opens, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                line = (row.get("opening_line") or row.get("open") or "").strip()
                if line:
                    raw[row["game"].strip()] = float(line)
        report = match_games(list(raw), slate_games)
        for foreign, canonical in report.matched.items():
            opens[canonical] = raw[foreign]
        if report.unmatched:
            print(report.summary() + "\n")

    per_game = []
    skipped = no_signal = 0
    with open(args.slate, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            game = row["game"].strip()
            proj = float(row["projected_margin"])
            side = (row.get("side") or "").strip()
            posted = (row.get("posted_line") or "").strip()

            if not side and game in opens:
                # Derive the side from the model's disagreement with the open.
                home, away = (game.split("@")[-1].strip(),
                              game.split("@")[0].strip())
                side, _gap = signal_side(home, away, projected_margin=proj,
                                         opening_home_line=opens[game],
                                         min_disagreement=args.min_disagreement)
                if side is None:
                    no_signal += 1
                    continue
                if not posted:
                    posted = str(opens[game])
            if not side:
                skipped += 1
                continue
            row["side"], row["posted_line"] = side, posted
            # Priced off the market's number, never off the projection: the
            # projection has already done its only job, which was picking the
            # side. An opening line is preferred over a posted one because the
            # signal is about movement away from the open.
            market_line = opens.get(game)
            if market_line is None:
                market_line = float(posted) if (posted or "").strip() else None
            if market_line is None:
                skipped += 1
                continue
            per_game.append(find_plays(
                row["game"],
                market_line=market_line,
                side=row["side"],
                venues=venues,
                posted_line=(float(row["posted_line"])
                             if (row.get("posted_line") or "").strip() else None),
                total=float(row.get("total") or 52.0),
                clv_points=args.clv,
            ))

    card = build_card(per_game, max_weekly_exposure=args.max_exposure)
    considered = len(per_game)
    if no_signal:
        print(f"{no_signal} games had an opening line but the model disagreed with "
              f"it by less than {args.min_disagreement:.1f} points, which is not "
              f"enough to act on.")
    if skipped:
        print(f"{skipped} rows have neither a side nor an opening line and were "
              f"skipped. Supply one opening number per game and the side is "
              f"derived; a row with neither is a game, not a bet.")
    if no_signal or skipped:
        print()
    if not considered:
        print("Nothing to price. Fill in the side column and run again.")
        return 1
    if not card:
        print(f"No plays. {considered} games considered, none cleared their venue cost.\n"
              f"At {args.clv:.2f} points of CLV that is the expected outcome on most "
              f"boards; the strategy fires on key numbers, not on every game.")
        return 0
    print(f"{len(card)} plays from {considered} games. "
          f"Exposure {sum(p.stake for p in card):.2%} of bankroll.\n")
    for i, p in enumerate(card, 1):
        print(f"  {i}. {p.describe()}")
    print(f"\nCLV assumption: {args.clv:.2f} points. This is the best-supported "
          f"hypothesis here, not a demonstrated profit.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cfb_edge", description="College football betting edge model"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_model_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--games", help="CSV of completed results")
        p.add_argument("--priors", help="CSV of preseason ratings in points")
        p.add_argument("--prior-weight", type=float, default=4.0,
                       dest="prior_weight",
                       help="prior strength in games (default 4.0)")
        # Taken from `ratings`, never restated. This sat at 2.2 long after the
        # fitted value moved to 3.2, so every `rate` run used a home field a
        # full point too low while the constant next door was right.
        p.add_argument("--hfa", type=float, default=DEFAULT_HFA,
                       help=f"home field advantage in points "
                            f"(default {DEFAULT_HFA})")

    p_rate = sub.add_parser("rate", help="fit and print power ratings")
    add_model_args(p_rate)
    p_rate.add_argument("--top", type=int, default=25)
    p_rate.set_defaults(func=cmd_rate)

    p_card = sub.add_parser("card", help="price a slate and produce a card")
    add_model_args(p_card)
    p_card.add_argument("--slate", required=True)
    p_card.add_argument("--min-edge", type=float, default=None, dest="min_edge",
                        help="override the automatic edge threshold")
    p_card.add_argument("--max-exposure", type=float,
                        default=DEFAULT_MAX_WEEKLY_EXPOSURE,
                        dest="max_exposure")
    p_card.add_argument("--show-all", action="store_true", dest="show_all",
                        help="print every game, including passes")
    p_card.set_defaults(func=cmd_card)

    p_play = sub.add_parser("play", help="the evidence-backed strategy over a slate")
    p_play.add_argument("--slate", required=True,
                        help="CSV: game,projected_margin,side,posted_line,total")
    p_play.add_argument("--book-price", type=float, default=None, dest="book_price",
                        help="your book's price, e.g. -105. Omit for exchange only.")
    p_play.add_argument("--opens", help="CSV of game,opening_line (home perspective). "
                        "Supply this and the side is derived for you.")
    p_play.add_argument("--min-disagreement", type=float, default=4.0,
                        dest="min_disagreement",
                        help="points the model must differ from the open (default 4)")
    p_play.add_argument("--clv", type=float, default=0.44,
                        help="points of closing line value assumed (default 0.44)")
    p_play.add_argument("--max-exposure", type=float, default=0.10,
                        dest="max_exposure")
    p_play.set_defaults(func=cmd_play)

    p_clv = sub.add_parser("clv", help="report closing line value on a bet log")
    p_clv.add_argument("--bets", required=True)
    p_clv.set_defaults(func=cmd_clv)

    p_sig = sub.add_parser(
        "signals", help="record every signal on the board, bet or not")
    p_sig.add_argument("--slate", required=True)
    p_sig.add_argument("--opens", required=True)
    p_sig.add_argument("--out", default="data/signals.csv")
    p_sig.add_argument("--date", help="ISO date (default today)")
    p_sig.add_argument("--min-disagreement", type=float, default=4.0,
                       dest="min_disagreement")
    p_sig.set_defaults(func=cmd_signals)

    p_grade = sub.add_parser(
        "grade", help="fill closing lines into a signal log from the capture")
    p_grade.add_argument("--signals", default="data/signals.csv")
    p_grade.add_argument("--log", default="data/opens.jsonl.gz",
                         help="the raw capture, which holds the close as well "
                              "as the open")
    p_grade.set_defaults(func=cmd_grade)

    p_log = sub.add_parser(
        "log", help="record a bet as placed, which is the only time you can")
    p_log.add_argument("--bets", default="data/bets.csv",
                       help="the log to append to (default data/bets.csv)")
    p_log.add_argument("--game", required=True, help='"Away @ Home"')
    p_log.add_argument("--side", required=True, help="the team you backed")
    p_log.add_argument("--strike", type=float,
                       help="exchange strike as the card prints it: 3 means a "
                            "contract paying if your side wins by more than 3")
    p_log.add_argument("--line", type=float,
                       help="a book's line from your side's view: -3 lays three")
    p_log.add_argument("--cents", type=float, help="exchange price paid, in cents")
    p_log.add_argument("--price", type=float, help="American price, e.g. -110")
    p_log.add_argument("--stake", type=float, required=True,
                       help="fraction of bankroll, e.g. 0.0037")
    p_log.add_argument("--date", help="ISO date (default today)")
    p_log.set_defaults(func=cmd_log)

    p_set = sub.add_parser(
        "settle", help="record where the market closed, and the result")
    p_set.add_argument("--bets", default="data/bets.csv")
    p_set.add_argument("--game", required=True, help='"Away @ Home"')
    p_set.add_argument("--date", help="needed only when one game has two bets")
    p_set.add_argument("--closing-line", type=float, dest="closing_line",
                       help="closing number from your side's view")
    p_set.add_argument("--closing-strike", type=float, dest="closing_strike",
                       help="closing strike as the card prints it")
    p_set.add_argument("--closing-cents", type=float, dest="closing_cents")
    p_set.add_argument("--closing-price", type=float, dest="closing_price")
    p_set.add_argument("--closing-opposite-cents", type=float,
                       dest="closing_opposite_cents",
                       help="the other side's closing cents; both are needed "
                            "for price CLV, which devigs the two-way close")
    p_set.add_argument("--closing-opposite-price", type=float,
                       dest="closing_opposite_price")
    p_set.add_argument("--result", choices=("win", "loss", "push"))
    p_set.set_defaults(func=cmd_settle)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
