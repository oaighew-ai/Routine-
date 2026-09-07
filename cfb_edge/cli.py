"""Command line entry point.

    python3 -m cfb_edge rate  --games data/games.csv --priors data/priors.csv
    python3 -m cfb_edge card  --games data/games.csv --priors data/priors.csv \
                              --slate data/slate.csv
    python3 -m cfb_edge clv   --bets data/bets.csv
"""

from __future__ import annotations

import argparse
import sys

from . import clv as clv_mod
from . import data as data_mod
from .edge import evaluate, rank_card
from .ratings import solve_ratings
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
    bets = clv_mod.load_bets(args.bets)
    if not bets:
        print("no bets logged yet")
        return 0
    print(clv_mod.build_report(bets).summary())
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
        p.add_argument("--hfa", type=float, default=2.2,
                       help="home field advantage in points (default 2.2)")

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

    p_clv = sub.add_parser("clv", help="report closing line value on a bet log")
    p_clv.add_argument("--bets", required=True)
    p_clv.set_defaults(func=cmd_clv)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
