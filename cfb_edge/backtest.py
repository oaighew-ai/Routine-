"""A simulator for a question real history answers badly.

This is no longer the primary evidence. The model has since been tested against
6,398 real closing lines from 2006 to 2025, and it lost: 47-49% against the
spread at every threshold, and an incremental regression coefficient of -0.02
with a t of -0.31, meaning the closing line already contains everything the
ratings know. That result lives in `blend.py`, which is why the default model
weight there is now zero.

What this simulator still answers, and history does not, is the conditional:
how sharp does a market have to be before a model like this stops working? It
runs at `DEMONSTRATED_EDGE_WEIGHT` rather than the measured default, because a
simulation of a model that never bets is not informative about anything.

The answer, from `python3 -m cfb_edge.backtest`, is the most useful number in
the project and it is not flattering. Against a market that prices games within
about a point of truth, which is what a major book's closing line on a
televised game looks like, the model wins 51.3% and loses about 1.5% per unit
staked. It needs the market to be roughly three points off before it clears the
juice.

That is not a reason to throw the model away. It is a instruction about where
to point it: at the numbers nobody has cleaned up yet. Weeknight games, group
of five matchups, early in the week before the sharp money lands, and at books
that copy their lines slowly. It is also a reason never to point it at the game
everyone is watching, which is the most efficiently priced number on the board.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .blend import DEMONSTRATED_EDGE_WEIGHT
from .edge import evaluate
from .market import payout_multiple
from .projection import SIMULATED_RATING_SCALE, Matchup
from .ratings import Game, solve_ratings

# Dispersion of a real college football result around its true expectation.
GAME_NOISE = 16.0

# How far a preseason prior sits from the truth. Market win totals are good but
# not clairvoyant.
PRIOR_NOISE = 4.0


@dataclass(frozen=True)
class BacktestResult:
    bets: int
    wins: int
    losses: int
    roi: float
    win_rate: float
    standard_error: float
    mean_edge: float
    stake_weighted_edge: float
    staked: float
    profit: float

    @property
    def beats_juice(self) -> bool:
        """Whether the win rate clears the break-even point at -110 by more
        than one standard error. Anything less is not evidence."""
        return self.win_rate - self.standard_error > 0.5238

    def summary(self) -> str:
        return (
            f"{self.bets} bets | win {self.win_rate:.2%} "
            f"| ROI {self.roi:+.2%} (+/-{self.standard_error:.2%})\n"
            f"mean edge {self.mean_edge:+.2f} pts, "
            f"stake-weighted {self.stake_weighted_edge:+.2f} pts\n"
            f"clears -110 juice: {'yes' if self.beats_juice else 'no'}"
        )


def simulate(
    *,
    weeks_played: int,
    market_sigma: float,
    seasons: int = 200,
    teams: int = 64,
    seed: int = 17,
    game_noise: float = GAME_NOISE,
    prior_noise: float = PRIOR_NOISE,
    max_model_weight: float = DEMONSTRATED_EDGE_WEIGHT,
) -> BacktestResult:
    """Run the full pipeline against a synthetic league with known true ratings.

    `market_sigma` is the whole experiment: it is how many points the simulated
    market's line sits away from the true line. Small values are a sharp
    market, large values a soft one.
    """
    rng = random.Random(seed)
    n = wins = losses = 0
    profit = staked = weighted_edge = total_edge = 0.0

    for _ in range(seasons):
        names = [f"T{i}" for i in range(teams)]
        truth = {t: rng.gauss(0.0, 10.0) for t in names}
        priors = {t: truth[t] + rng.gauss(0.0, prior_noise) for t in names}

        results: list[Game] = []
        for _ in range(weeks_played):
            pool = names[:]
            rng.shuffle(pool)
            for i in range(0, teams, 2):
                home, away = pool[i], pool[i + 1]
                margin = int(
                    round(truth[home] - truth[away] + 2.2 + rng.gauss(0.0, game_noise))
                ) or 3
                results.append(Game(home, away, 24 + margin, 24))

        model = solve_ratings(results, priors=priors, prior_weight=4.0)

        pool = names[:]
        rng.shuffle(pool)
        for i in range(0, teams, 2):
            home, away = pool[i], pool[i + 1]
            fair_home_line = -(truth[home] - truth[away] + 2.2)
            # Books post half-points, so the simulated market does too.
            line = round((fair_home_line + rng.gauss(0.0, market_sigma)) * 2) / 2

            candidate = evaluate(
                model, Matchup(home, away), market_home_line=line, market_total=52.0,
                max_model_weight=max_model_weight,
                rating_scale=SIMULATED_RATING_SCALE,
            )
            if not candidate.is_bet:
                continue

            fair_for_side = (
                fair_home_line if candidate.side == "home" else -fair_home_line
            )
            edge = candidate.line - fair_for_side
            stake = candidate.stake.recommended

            n += 1
            staked += stake
            total_edge += edge
            weighted_edge += stake * edge

            margin = int(
                round(truth[home] - truth[away] + 2.2 + rng.gauss(0.0, game_noise))
            ) or 3
            signed = margin if candidate.side == "home" else -margin
            settled = signed + candidate.line
            if settled > 0:
                profit += stake * payout_multiple(candidate.price)
                wins += 1
            elif settled < 0:
                profit -= stake
                losses += 1

    live = wins + losses
    return BacktestResult(
        bets=n,
        wins=wins,
        losses=losses,
        roi=profit / staked if staked else 0.0,
        win_rate=wins / live if live else 0.0,
        standard_error=1.0 / math.sqrt(n) if n else 0.0,
        mean_edge=total_edge / n if n else 0.0,
        stake_weighted_edge=weighted_edge / staked if staked else 0.0,
        staked=staked,
        profit=profit,
    )


def main() -> int:
    print("How sharp can the market be before this model stops working?\n")
    print(f"{'weeks':>6} {'mkt sd':>7} {'bets':>6} {'win%':>8} {'ROI':>9} {'edge':>7}")
    print("-" * 48)
    for weeks in (1, 4, 10):
        for sigma in (1.0, 2.0, 3.0):
            r = simulate(weeks_played=weeks, market_sigma=sigma, seasons=200)
            print(
                f"{weeks:>6} {sigma:>7.1f} {r.bets:>6} {r.win_rate:>7.2%} "
                f"{r.roi:>+8.2%} {r.mean_edge:>+6.2f}"
            )
    print(
        "\nRead the sd=1.0 rows first. That is roughly a major book's closing\n"
        "number on a televised game, and the model does not beat it. The\n"
        "profitable rows need the market to be two to three points wrong,\n"
        "which happens on quiet games and early in the week, not on the\n"
        "marquee matchup."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
