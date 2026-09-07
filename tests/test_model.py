"""Tests for the edge model.

These lean on properties that must hold rather than on golden numbers, because
the tunable constants are meant to be refit and a test that pins them would
just have to be rewritten every time someone did the right thing.
"""

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cfb_edge import blend, distribution, market, staking
from cfb_edge.distribution import CoverOutcome
from cfb_edge.edge import evaluate
from cfb_edge.projection import Matchup
from cfb_edge.ratings import Game, solve_ratings


class TestMarket(unittest.TestCase):
    def test_american_decimal_round_trip(self):
        for price in (-500, -250, -110, -101, 100, 145, 300, 1200):
            back = market.decimal_to_american(market.american_to_decimal(price))
            self.assertAlmostEqual(price, back, places=6)

    def test_break_even_at_minus_110(self):
        self.assertAlmostEqual(market.american_to_probability(-110), 0.5238095, places=6)
        self.assertAlmostEqual(market.payout_multiple(-110), 10 / 11, places=9)

    def test_every_devig_sums_to_one(self):
        for pair in ([-110, -110], [-450, 350], [-2000, 1200], [-105, -115]):
            for method in market.DEVIG_METHODS:
                probs = market.devig(pair, method)
                self.assertAlmostEqual(sum(probs), 1.0, places=9, msg=f"{method} {pair}")
                self.assertTrue(all(0.0 < p < 1.0 for p in probs))

    def test_symmetric_market_is_a_coin_flip(self):
        for method in market.DEVIG_METHODS:
            self.assertAlmostEqual(market.devig([-110, -110], method)[0], 0.5, places=9)

    def test_shin_equals_additive_on_two_way_markets(self):
        # Not a coincidence and not a fallback: on two outcomes Shin's method
        # reduces to the equal-margin adjustment. Documented in market.py.
        for pair in ([-450, 350], [-2000, 1200], [-150, 130], [-800, 550]):
            shin = market.devig(pair, "shin")
            additive = market.devig(pair, "additive")
            for a, b in zip(shin, additive):
                self.assertAlmostEqual(a, b, places=10)

    def test_shin_z_is_a_plausible_insider_fraction(self):
        z = market.shin_z([-800, 550])
        self.assertTrue(0.0 < z < 0.15, f"implausible z: {z}")

    def test_multiplicative_underprices_the_favourite(self):
        # Books take more margin on longshots, so proportional scaling leaves
        # too much vig on the favourite.
        mult = market.devig([-800, 550], "multiplicative")[0]
        add = market.devig([-800, 550], "additive")[0]
        self.assertLess(mult, add)

    def test_line_shopping_prefers_the_better_number_over_the_better_price(self):
        # Laying 2.5 at worse juice beats laying 3.0 at better juice, because
        # the half point crosses the most common margin in football.
        quotes = [
            market.Quote("book_a", -3.0, -105),
            market.Quote("book_b", -2.5, -115),
        ]
        self.assertEqual(market.best_quote(quotes).book, "book_b")

    def test_line_shopping_works_for_underdogs_too(self):
        quotes = [
            market.Quote("book_a", 6.5, -105),
            market.Quote("book_b", 7.0, -110),
        ]
        self.assertEqual(market.best_quote(quotes).book, "book_b")

    def test_price_breaks_a_tie_on_the_number(self):
        quotes = [
            market.Quote("book_a", -3.0, -115),
            market.Quote("book_b", -3.0, -105),
        ]
        self.assertEqual(market.best_quote(quotes).book, "book_b")

    def test_an_over_wants_the_lowest_total(self):
        quotes = [
            market.Quote("book_a", 52.5, -110),
            market.Quote("book_b", 51.5, -110),
        ]
        best = market.best_quote(quotes, prefer_higher=False)
        self.assertEqual(best.book, "book_b")

    def test_consensus_ignores_a_single_outlier(self):
        quotes = [market.Quote(f"b{i}", -7.0, -110) for i in range(4)]
        quotes.append(market.Quote("stale", -3.0, -110))
        self.assertEqual(market.consensus_line(quotes), -7.0)


class TestDistribution(unittest.TestCase):
    def test_pmf_is_a_distribution(self):
        pmf = distribution.margin_pmf(-6.5, 16.0)
        self.assertAlmostEqual(sum(pmf.values()), 1.0, places=12)
        self.assertTrue(all(p > 0 for p in pmf.values()))

    def test_ties_are_impossible(self):
        # Regulation ties go to overtime, so no game ends level.
        pmf = distribution.margin_pmf(0.0, 16.0)
        self.assertEqual(pmf.get(0, 0.0), 0.0)

    def test_pickem_is_a_coin_flip_and_cannot_push(self):
        pmf = distribution.margin_pmf(0.0, 16.0)
        outcome = distribution.cover_probability(pmf, 0.0)
        self.assertAlmostEqual(outcome.win, 0.5, places=9)
        self.assertEqual(outcome.push, 0.0)

    def test_three_is_the_most_common_margin(self):
        pmf = distribution.margin_pmf(0.0, 16.0)
        by_mass = sorted(range(1, 15), key=lambda k: pmf[k], reverse=True)
        self.assertEqual(by_mass[0], 3)
        self.assertEqual(by_mass[1], 7)

    def test_half_points_are_worth_more_at_key_numbers(self):
        pmf = distribution.margin_pmf(0.0, 16.0)

        def crossing(k):
            return distribution.half_point_value(
                pmf, -(k + 0.5)
            ) + distribution.half_point_value(pmf, -k)

        self.assertGreater(crossing(3), crossing(5))
        self.assertGreater(crossing(3), crossing(8))
        self.assertGreater(crossing(7), crossing(8))

    def test_sigma_scales_with_the_total_and_is_bounded(self):
        self.assertLess(
            distribution.sigma_for_total(38.0), distribution.sigma_for_total(70.0)
        )
        lo, hi = distribution.SIGMA_BOUNDS
        for total in (5.0, 52.0, 300.0):
            self.assertTrue(lo <= distribution.sigma_for_total(total) <= hi)

    def test_favourite_covers_less_often_than_it_wins(self):
        pmf = distribution.margin_pmf(7.0, 16.0)
        wins = distribution.cover_probability(pmf, 0.0).win
        covers = distribution.cover_probability(pmf, -7.0).win_excluding_push
        self.assertGreater(wins, covers)

    def test_key_bumps_refit_recovers_a_known_shape(self):
        rng = random.Random(7)
        truth = {3: 1.5, 7: 1.3, 10: 1.15}
        source = distribution.margin_pmf(0.0, 16.0, key_bumps=truth)
        keys = list(source)
        weights = [source[k] for k in keys]
        sample = rng.choices(keys, weights=weights, k=60000)
        fitted = distribution.fit_key_bumps(sample)
        for k, expected in truth.items():
            self.assertAlmostEqual(fitted[k], expected, delta=0.08)
        self.assertAlmostEqual(fitted[5], 1.0, delta=0.08)

    def test_refit_refuses_a_small_sample(self):
        with self.assertRaises(ValueError):
            distribution.fit_key_bumps([3, 7, 10])


class TestBlend(unittest.TestCase):
    def test_weight_is_small_early_and_capped_late(self):
        self.assertLess(blend.model_weight(1), 0.10)
        self.assertLess(blend.model_weight(1000), blend.MAX_MODEL_WEIGHT + 1e-9)
        self.assertGreater(blend.model_weight(10), blend.model_weight(2))

    def test_edge_is_the_disagreement_scaled_by_the_weight(self):
        w = blend.model_weight(6)
        edge = blend.blended_edge(model_line=-10.0, market_line=-7.0, weight=w)
        self.assertAlmostEqual(edge, w * (-7.0 - -10.0), places=12)

    def test_a_ten_point_disagreement_in_week_two_is_not_a_bet(self):
        # The single most important behaviour in the system.
        edge = blend.blended_edge(-16.0, -6.0, blend.model_weight(1))
        self.assertLess(abs(edge), 1.0)


class TestStaking(unittest.TestCase):
    def test_no_bet_at_the_break_even_win_rate(self):
        b = market.payout_multiple(-110)
        outcome = CoverOutcome(0.5238095, 0.0, 1 - 0.5238095)
        self.assertLessEqual(staking.size_bet(outcome, b).recommended, 0.0)

    def test_matches_the_textbook_kelly_formula_without_pushes(self):
        b = market.payout_multiple(-110)
        p = 0.55
        outcome = CoverOutcome(p, 0.0, 1 - p)
        expected = (b * p - (1 - p)) / b
        self.assertAlmostEqual(staking.full_kelly(outcome, b), expected, places=8)

    def test_pushes_do_not_change_the_optimal_fraction(self):
        # A push returns the stake, so only the conditional win rate matters.
        b = market.payout_multiple(-110)
        with_push = CoverOutcome(0.55, 0.06, 0.39)
        without = CoverOutcome(0.55 / 0.94, 0.0, 0.39 / 0.94)
        self.assertAlmostEqual(
            staking.full_kelly(with_push, b), staking.full_kelly(without, b), places=8
        )

    def test_growth_peaks_at_full_kelly_and_dies_at_double(self):
        b = market.payout_multiple(-110)
        outcome = CoverOutcome(0.55, 0.0, 0.45)
        k = staking.full_kelly(outcome, b)
        at_k = staking.kelly_growth_rate(outcome, b, k)
        self.assertGreater(at_k, staking.kelly_growth_rate(outcome, b, k * 0.5))
        self.assertGreater(at_k, staking.kelly_growth_rate(outcome, b, k * 1.5))
        self.assertLess(staking.kelly_growth_rate(outcome, b, k * 2.0), 1e-9)

    def test_single_bet_is_capped(self):
        b = market.payout_multiple(-110)
        huge = CoverOutcome(0.80, 0.0, 0.20)
        self.assertLessEqual(
            staking.size_bet(huge, b).recommended, staking.DEFAULT_MAX_STAKE + 1e-12
        )

    def test_portfolio_cap_scales_proportionally(self):
        scaled = staking.apply_portfolio_cap([0.02] * 10, max_total=0.10)
        self.assertAlmostEqual(sum(scaled), 0.10, places=9)
        self.assertAlmostEqual(scaled[0], scaled[-1], places=12)

    def test_portfolio_cap_leaves_a_small_card_alone(self):
        stakes = [0.01, 0.02]
        self.assertEqual(staking.apply_portfolio_cap(stakes, max_total=0.10), stakes)


class TestRatings(unittest.TestCase):
    def _round_robin(self):
        return [
            Game("A", "B", 31, 21),
            Game("B", "C", 28, 18),
            Game("C", "A", 10, 40),
        ]

    def test_converges_and_orders_correctly(self):
        model = solve_ratings(self._round_robin(), prior_weight=0.0)
        self.assertTrue(model.converged)
        self.assertGreater(model.rating("A"), model.rating("B"))
        self.assertGreater(model.rating("B"), model.rating("C"))

    def test_ratings_are_centred(self):
        model = solve_ratings(self._round_robin(), prior_weight=0.0)
        self.assertAlmostEqual(sum(model.ratings.values()), 0.0, places=9)

    def test_shrinkage_pulls_toward_the_prior(self):
        games = self._round_robin()
        loose = solve_ratings(games, prior_weight=0.0)
        tight = solve_ratings(games, priors={"A": 0, "B": 0, "C": 0}, prior_weight=8.0)
        self.assertLess(abs(tight.rating("A")), abs(loose.rating("A")))

    def test_blowouts_are_capped(self):
        modest = solve_ratings([Game("A", "B", 52, 24)], prior_weight=0.0)
        absurd = solve_ratings([Game("A", "B", 105, 0)], prior_weight=0.0)
        self.assertAlmostEqual(modest.rating("A"), absurd.rating("A"), places=6)

    def test_home_field_is_removed_from_the_rating(self):
        # An exactly-HFA home win means the teams are equal.
        model = solve_ratings(
            [Game("A", "B", 24, 22)], prior_weight=0.0, hfa=2.0
        )
        self.assertAlmostEqual(model.rating("A"), model.rating("B"), places=6)

    def test_unseen_team_is_flagged_rather_than_guessed(self):
        model = solve_ratings([Game("A", "B", 30, 20)], prior_weight=0.0)
        self.assertFalse(model.is_known("Some FCS School"))
        self.assertEqual(model.rating("Some FCS School"), 0.0)


class TestEdge(unittest.TestCase):
    def _seasoned_model(self, gap=20.0):
        """A model with enough games that the blend weight is meaningful."""
        games = []
        for i in range(10):
            games.append(Game("Strong", f"Filler{i}", 24 + int(gap), 24))
            games.append(Game("Weak", f"Filler{i}", 24, 24 + int(gap)))
        return solve_ratings(games, prior_weight=0.0)

    def test_refuses_games_with_an_unrated_team(self):
        model = self._seasoned_model()
        c = evaluate(
            model,
            Matchup("Strong", "Nowhere State"),
            market_home_line=-40.0,
            known_teams_only=True,
        )
        self.assertFalse(c.is_bet)
        self.assertEqual(c.rejected_for, "unrated team")

    def test_no_bet_when_the_market_matches_the_model(self):
        model = self._seasoned_model()
        fair = -(model.rating("Strong") - model.rating("Weak") + model.hfa)
        c = evaluate(model, Matchup("Strong", "Weak"), market_home_line=round(fair * 2) / 2)
        self.assertFalse(c.is_bet)

    def test_takes_the_side_the_model_prefers(self):
        model = self._seasoned_model()
        fair = -(model.rating("Strong") - model.rating("Weak") + model.hfa)
        # Market makes the home favourite far too cheap: back home.
        cheap = evaluate(model, Matchup("Strong", "Weak"), market_home_line=fair + 12.0)
        self.assertEqual(cheap.side, "home")
        # Market makes it far too expensive: back away.
        rich = evaluate(model, Matchup("Strong", "Weak"), market_home_line=fair - 12.0)
        self.assertEqual(rich.side, "away")

    def test_early_season_threshold_is_stricter(self):
        from cfb_edge.edge import DEFAULT_MIN_EDGE, EARLY_SEASON_MIN_EDGE

        self.assertGreater(EARLY_SEASON_MIN_EDGE, DEFAULT_MIN_EDGE)

    def test_a_bet_carries_positive_expected_value(self):
        model = self._seasoned_model()
        fair = -(model.rating("Strong") - model.rating("Weak") + model.hfa)
        c = evaluate(model, Matchup("Strong", "Weak"), market_home_line=fair + 14.0)
        if c.is_bet:
            self.assertGreater(c.stake.expected_value, 0.0)
            self.assertGreater(c.outcome.win_excluding_push, 0.5238)


class TestCLV(unittest.TestCase):
    def test_beating_the_close_is_positive_clv(self):
        from cfb_edge.clv import LoggedBet

        bet = LoggedBet("2026-09-13", "A", "B", "home", -2.5, -110, 0.01,
                        closing_line=-3.5)
        self.assertEqual(bet.line_clv, 1.0)
        self.assertGreater(bet.probability_clv(), 0.0)

    def test_losing_to_the_close_is_negative_clv(self):
        from cfb_edge.clv import LoggedBet

        bet = LoggedBet("2026-09-13", "A", "B", "home", -7.0, -110, 0.01,
                        closing_line=-6.0)
        self.assertEqual(bet.line_clv, -1.0)
        self.assertLess(bet.probability_clv(), 0.0)

    def test_a_point_of_clv_is_worth_more_across_a_key_number(self):
        from cfb_edge.clv import LoggedBet

        across = LoggedBet("d", "A", "B", "home", -2.5, -110, 0.01, closing_line=-3.5)
        away_from = LoggedBet("d", "A", "B", "home", -11.5, -110, 0.01, closing_line=-12.5)
        self.assertEqual(across.line_clv, away_from.line_clv)
        self.assertGreater(across.probability_clv(), away_from.probability_clv())

    def test_profit_accounting(self):
        from cfb_edge.clv import LoggedBet

        win = LoggedBet("d", "A", "B", "home", -3.0, -110, 1.0, result="win")
        loss = LoggedBet("d", "A", "B", "home", -3.0, -110, 1.0, result="loss")
        push = LoggedBet("d", "A", "B", "home", -3.0, -110, 1.0, result="push")
        self.assertAlmostEqual(win.profit(), 10 / 11, places=9)
        self.assertAlmostEqual(loss.profit(), -1.0, places=9)
        self.assertEqual(push.profit(), 0.0)


class TestBacktestHonesty(unittest.TestCase):
    def test_the_model_does_not_beat_a_sharp_market(self):
        """The claim the README makes, pinned so it cannot rot silently."""
        from cfb_edge.backtest import simulate

        sharp = simulate(weeks_played=10, market_sigma=1.0, seasons=60)
        self.assertFalse(sharp.beats_juice)

    def test_the_model_does_beat_a_soft_market(self):
        from cfb_edge.backtest import simulate

        soft = simulate(weeks_played=10, market_sigma=3.0, seasons=60)
        self.assertGreater(soft.mean_edge, 1.5)
        self.assertGreater(soft.roi, 0.0)

    def test_it_bets_nothing_in_week_one(self):
        from cfb_edge.backtest import simulate

        self.assertEqual(simulate(weeks_played=1, market_sigma=3.0, seasons=40).bets, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
