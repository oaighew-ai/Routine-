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
    def test_the_default_weight_is_zero_because_that_is_what_was_measured(self):
        # Against 6,398 real closing lines the model's incremental coefficient
        # was -0.02 (t = -0.31). Zero is the measurement, not a placeholder.
        self.assertEqual(blend.MAX_MODEL_WEIGHT, 0.0)
        for n in (1, 5, 20, 1000):
            self.assertEqual(blend.model_weight(n), 0.0)

    def test_the_schedule_shape_survives_for_a_model_that_earns_a_vote(self):
        cap = blend.DEMONSTRATED_EDGE_WEIGHT
        self.assertLess(blend.model_weight(1, max_weight=cap), 0.10)
        self.assertLess(blend.model_weight(1000, max_weight=cap), cap + 1e-9)
        self.assertGreater(blend.model_weight(10, max_weight=cap),
                           blend.model_weight(2, max_weight=cap))

    def test_edge_is_the_disagreement_scaled_by_the_weight(self):
        w = blend.model_weight(6)
        edge = blend.blended_edge(model_line=-10.0, market_line=-7.0, weight=w)
        self.assertAlmostEqual(edge, w * (-7.0 - -10.0), places=12)

    def test_a_ten_point_disagreement_in_week_two_is_not_a_bet(self):
        # True at the demonstrated-edge weight, and trivially true at the
        # measured default of zero.
        edge = blend.blended_edge(
            -16.0, -6.0, blend.model_weight(1, max_weight=blend.DEMONSTRATED_EDGE_WEIGHT))
        self.assertLess(abs(edge), 1.0)
        self.assertEqual(blend.blended_edge(-16.0, -6.0, blend.model_weight(1)), 0.0)


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
        from cfb_edge.projection import RATING_SCALE

        model = self._seasoned_model()
        fair = -((model.rating("Strong") - model.rating("Weak")) * RATING_SCALE
                 + model.hfa)
        c = evaluate(model, Matchup("Strong", "Weak"), market_home_line=round(fair * 2) / 2)
        self.assertFalse(c.is_bet)

    def test_takes_the_side_the_model_prefers(self):
        from cfb_edge.projection import RATING_SCALE

        model = self._seasoned_model()
        fair = -((model.rating("Strong") - model.rating("Weak")) * RATING_SCALE
                 + model.hfa)
        # Market makes the home favourite far too cheap: back home.
        # A weight has to be passed explicitly now: the default is zero, which
        # makes every edge zero and leaves no side to prefer.
        w = blend.DEMONSTRATED_EDGE_WEIGHT
        cheap = evaluate(model, Matchup("Strong", "Weak"),
                         market_home_line=fair + 12.0, max_model_weight=w)
        self.assertEqual(cheap.side, "home")
        # Market makes it far too expensive: back away.
        rich = evaluate(model, Matchup("Strong", "Weak"),
                        market_home_line=fair - 12.0, max_model_weight=w)
        self.assertEqual(rich.side, "away")

    def test_the_default_weight_produces_no_bets_at_all(self):
        """The measured behaviour: an empty card, on every game."""
        model = self._seasoned_model()
        for offset in (-14.0, -5.0, 0.0, 5.0, 14.0):
            c = evaluate(model, Matchup("Strong", "Weak"), market_home_line=offset)
            self.assertFalse(c.is_bet)
            self.assertAlmostEqual(c.edge_points, 0.0, places=9)

    def test_early_season_threshold_is_stricter(self):
        from cfb_edge.edge import DEFAULT_MIN_EDGE, EARLY_SEASON_MIN_EDGE

        self.assertGreater(EARLY_SEASON_MIN_EDGE, DEFAULT_MIN_EDGE)

    def test_a_bet_carries_positive_expected_value(self):
        from cfb_edge.projection import RATING_SCALE

        model = self._seasoned_model()
        fair = -((model.rating("Strong") - model.rating("Weak")) * RATING_SCALE
                 + model.hfa)
        c = evaluate(model, Matchup("Strong", "Weak"), market_home_line=fair + 14.0,
                     max_model_weight=blend.DEMONSTRATED_EDGE_WEIGHT)
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
    """The simulator runs at the demonstrated-edge weight, not the measured
    default of zero, because simulating a model that never bets says nothing.
    The authoritative result is the real closing-line test in blend.py."""

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

    def test_the_shipped_default_bets_nothing_ever(self):
        """What the real closing-line test implies for live use."""
        from cfb_edge.backtest import simulate
        from cfb_edge.blend import MAX_MODEL_WEIGHT

        r = simulate(weeks_played=10, market_sigma=3.0, seasons=20,
                     max_model_weight=MAX_MODEL_WEIGHT)
        self.assertEqual(r.bets, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestKalshiFees(unittest.TestCase):
    """The fee curve, and what it does to a flat entry bar."""

    def test_matches_kalshi_published_example(self):
        from cfb_edge.kalshi_fees import fee_dollars

        # Kalshi documents 20 contracts at 60c costing $0.34.
        self.assertAlmostEqual(fee_dollars(20, 0.60), 0.34, places=9)

    def test_fee_peaks_at_a_coin_flip(self):
        from cfb_edge.kalshi_fees import fee_cents_per_contract

        at_half = fee_cents_per_contract(0.50)
        self.assertAlmostEqual(at_half, 1.75, places=9)
        for price in (0.05, 0.21, 0.35, 0.65, 0.84, 0.95):
            self.assertLess(fee_cents_per_contract(price), at_half)

    def test_fee_is_symmetric_about_a_coin_flip(self):
        from cfb_edge.kalshi_fees import fee_cents_per_contract

        for price in (0.10, 0.25, 0.40):
            self.assertAlmostEqual(
                fee_cents_per_contract(price),
                fee_cents_per_contract(1.0 - price),
                places=12,
            )

    def test_the_entry_bar_must_bend_with_the_fee(self):
        # The point of the module: a flat cent bar is the wrong shape, because
        # clearing the same net edge costs more gross edge near 50c.
        from cfb_edge.kalshi_fees import required_gross_edge_cents

        self.assertGreater(
            required_gross_edge_cents(0.50), required_gross_edge_cents(0.21)
        )
        self.assertAlmostEqual(required_gross_edge_cents(0.50), 2.75, places=9)

    def test_a_one_cent_gross_edge_near_a_coin_flip_is_negative(self):
        from cfb_edge.kalshi_fees import net_edge

        edge = net_edge(0.49, 1.0)
        self.assertFalse(edge.survives_fees)
        self.assertLess(edge.net_ev, 0.0)

    def test_a_three_cent_gross_edge_survives_but_loses_most_of_itself(self):
        from cfb_edge.kalshi_fees import net_edge

        edge = net_edge(0.47, 3.0)
        self.assertTrue(edge.survives_fees)
        self.assertGreater(edge.fee_share_of_gross, 0.5)
        self.assertAlmostEqual(edge.gross_ev, 0.0638, places=3)
        self.assertAlmostEqual(edge.net_ev, 0.0267, places=3)

    def test_the_same_gross_edge_is_worth_more_in_the_tails(self):
        from cfb_edge.kalshi_fees import net_edge

        tail = net_edge(0.21, 3.0)
        middle = net_edge(0.47, 3.0)
        self.assertGreater(tail.net_cents, middle.net_cents)

    def test_rejects_impossible_inputs(self):
        from cfb_edge.kalshi_fees import fee_dollars

        with self.assertRaises(ValueError):
            fee_dollars(10, 0.0)
        with self.assertRaises(ValueError):
            fee_dollars(10, 1.0)
        with self.assertRaises(ValueError):
            fee_dollars(0, 0.5)


class TestKalshiBook(unittest.TestCase):
    """The order-book inversion, which is where phantom edges come from."""

    def _payload(self):
        # YES bids at 45/46; NO bids at 51/52 -> YES asks at 48/49.
        return {"orderbook": {"yes": [[45, 800], [46, 500]],
                              "no": [[51, 900], [52, 600]]}}

    def test_no_bids_become_yes_asks(self):
        from cfb_edge.providers.kalshi import parse_book

        book = parse_book("T", self._payload())
        self.assertEqual([l.price for l in book.yes_asks], [48.0, 49.0])
        self.assertEqual(book.best_ask, 48.0)

    def test_yes_side_is_never_read_as_an_ask(self):
        # The classic error: treating the YES bid array as an offer book makes
        # the price look better than anything fillable, which downstream shows
        # up as a large edge that does not exist.
        from cfb_edge.providers.kalshi import parse_book

        book = parse_book("T", self._payload())
        self.assertGreater(book.best_ask, book.best_bid)
        self.assertEqual(book.best_bid, 46.0)
        self.assertEqual(book.spread, 2.0)

    def test_vwap_walks_the_book(self):
        from cfb_edge.providers.kalshi import parse_book

        book = parse_book("T", self._payload())
        self.assertEqual(book.vwap(200), 48.0)
        # 600 at 48 then 200 at 49.
        self.assertAlmostEqual(book.vwap(800), 48.25, places=9)

    def test_vwap_refuses_a_size_the_book_cannot_fill(self):
        from cfb_edge.providers.kalshi import parse_book

        self.assertIsNone(parse_book("T", self._payload()).vwap(10_000))

    def test_empty_book_is_handled(self):
        from cfb_edge.providers.kalshi import parse_book

        book = parse_book("T", {})
        self.assertIsNone(book.best_ask)
        self.assertIsNone(book.spread)
        self.assertIsNone(book.vwap(1))

    def test_unreachable_host_names_itself(self):
        from cfb_edge.providers.kalshi import KalshiUnreachable, fetch_markets
        import urllib.error

        def refuse(url):
            raise urllib.error.URLError("Tunnel connection failed: 403 Forbidden")

        with self.assertRaises(KalshiUnreachable) as ctx:
            fetch_markets("KXNCAAFGAME", opener=refuse)
        self.assertIn("api.elections.kalshi.com", str(ctx.exception))


class TestGate(unittest.TestCase):
    def _c(self, **kw):
        from cfb_edge.gate import Candidate

        base = dict(ticker="T", label="L", game="A @ B", market="ML",
                    entry_cents=21.0, fair_cents=24.9, fair_is_assumed=False,
                    depth=500_000, spread=2.0)
        base.update(kw)
        return Candidate(**base)

    def test_a_real_edge_clears_all_six(self):
        from cfb_edge.gate import evaluate

        r = evaluate(self._c())
        self.assertTrue(r.cleared)
        self.assertEqual(r.passed, 6)
        self.assertAlmostEqual(r.net_ev, 0.1304, places=3)

    def test_a_coin_flip_gap_smaller_than_the_fee_is_rejected(self):
        from cfb_edge.gate import evaluate

        r = evaluate(self._c(entry_cents=49.0, fair_cents=50.0,
                             fair_is_assumed=True), allow_assumed_fair=True)
        self.assertFalse(r.cleared)
        self.assertFalse(r.checks["EDGE"])
        self.assertLess(r.net_cents, 0)
        self.assertIn("needed 2.75c", r.reason)

    def test_assumed_fair_is_rejected_by_default(self):
        from cfb_edge.gate import evaluate

        r = evaluate(self._c(entry_cents=47.0, fair_cents=50.0, fair_is_assumed=True))
        self.assertFalse(r.checks["BOOK"])
        self.assertTrue(evaluate(
            self._c(entry_cents=47.0, fair_cents=50.0, fair_is_assumed=True),
            allow_assumed_fair=True).checks["BOOK"])

    def test_a_book_too_thin_to_fill_fails_depth(self):
        from cfb_edge.gate import evaluate

        r = evaluate(self._c(depth=120), size=200)
        self.assertFalse(r.checks["DEPTH"])
        self.assertFalse(r.cleared)

    def test_a_wide_quote_is_not_a_market(self):
        from cfb_edge.gate import evaluate

        self.assertFalse(evaluate(self._c(spread=9.0)).checks["QUOTE"])

    def test_kelly_reduces_to_net_over_one_minus_price(self):
        from cfb_edge.gate import quarter_kelly

        self.assertAlmostEqual(quarter_kelly(21.0, 2.74, cap=1.0),
                               0.25 * 2.74 / 79.0, places=12)
        self.assertEqual(quarter_kelly(21.0, -1.0), 0.0)

    def test_ranking_keeps_only_cleared_markets(self):
        from cfb_edge.gate import evaluate, rank

        rs = [evaluate(self._c()), evaluate(self._c(depth=1))]
        self.assertEqual(len(rank(rs)), 1)


class TestBoardEndToEnd(unittest.TestCase):
    def test_offline_board_produces_a_card(self):
        from cfb_edge.board import main
        import io, contextlib

        fixtures = Path(__file__).resolve().parent / "fixtures"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--offline", str(fixtures / "board.json"),
                         "--book", str(fixtures / "lines.csv")])
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Arkansas St.", out)
        self.assertIn("cleared all six checks", out)
        # The thin market has a bigger gross edge but cannot fill; it must not
        # reach the card.
        self.assertNotIn("1. Thin market", out)

    def test_blocked_network_returns_an_error_not_an_empty_board(self):
        """An empty board and a blocked board mean opposite things.

        The blockage is injected rather than relied upon. This test used to
        call the real endpoint and assert it failed, which passed only where
        the egress policy happened to block Kalshi and reported a false pass
        on any machine with open internet. CI caught it on the first run.
        """
        from cfb_edge.board import main
        import io, contextlib, urllib.error

        def blocked(url, **kw):
            raise urllib.error.URLError("Tunnel connection failed: 403 Forbidden")

        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = main(["--week", "2"], opener=blocked)
        self.assertEqual(code, 2)
        self.assertIn("could not fetch", err.getvalue())
        self.assertNotIn("no markets", buf.getvalue().lower())


class TestCLVExtract(unittest.TestCase):
    def _series(self):
        from datetime import datetime, timedelta, timezone
        from cfb_edge.clv_extract import build_series

        kick = datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
        recs = []
        # A market that drifts from 40c to 46c into kickoff.
        for i, price in enumerate([40, 41, 43, 45, 46]):
            recs.append({"ticker": "MOVER", "ts": (kick - timedelta(minutes=1440 - i * 360)).isoformat(),
                         "yes_mid": price, "kickoff": kick.isoformat()})
        # A market nobody ever traded.
        for i in range(5):
            recs.append({"ticker": "FLAT", "ts": (kick - timedelta(minutes=1440 - i * 360)).isoformat(),
                         "yes_mid": 17, "kickoff": kick.isoformat()})
        return build_series(recs), kick

    def test_flat_markets_are_identified(self):
        series, _ = self._series()
        self.assertTrue(series["MOVER"].moved)
        self.assertFalse(series["FLAT"].moved)

    def test_close_is_the_last_price_before_kickoff(self):
        series, _ = self._series()
        self.assertEqual(series["MOVER"].close, 46.0)

    def test_price_at_never_looks_into_the_future(self):
        from datetime import timedelta

        series, kick = self._series()
        s = series["MOVER"]
        # 1440m before kickoff only the first observation exists.
        self.assertEqual(s.price_at(kick - timedelta(minutes=1440)), 40.0)
        self.assertIsNone(s.price_at(kick - timedelta(minutes=2000)))

    def test_clv_is_close_minus_entry(self):
        from cfb_edge.clv_extract import measure

        series, _ = self._series()
        m = measure([{"ticker": "MOVER", "game": "A @ B", "entry_price": 41.0}], series)[0]
        self.assertAlmostEqual(m.clv, 5.0, places=9)
        self.assertTrue(m.beat_close)

    def test_flat_market_reports_zero_clv_and_is_flagged(self):
        from cfb_edge.clv_extract import measure, summarise

        series, _ = self._series()
        ms = measure([
            {"ticker": "MOVER", "game": "A @ B", "entry_price": 41.0},
            {"ticker": "FLAT", "game": "C @ D", "entry_price": 17.0},
        ], series)
        s = summarise(ms)
        self.assertEqual(s.flat, 1)
        self.assertEqual(s.moved, 1)
        # The flat row drags the overall mean but not the moved-only mean.
        self.assertGreater(s.mean_clv_moved_only, s.mean_clv)

    def test_entries_without_a_captured_series_are_dropped(self):
        from cfb_edge.clv_extract import measure

        series, _ = self._series()
        self.assertEqual(
            measure([{"ticker": "UNSEEN", "game": "x", "entry_price": 50.0}], series), []
        )

    def test_truncated_lines_do_not_kill_a_run(self):
        import gzip, tempfile, os
        from cfb_edge.clv_extract import read_runs

        fd, path = tempfile.mkstemp(suffix=".jsonl.gz")
        os.close(fd)
        try:
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                fh.write('{"ticker":"A","ts":1,"yes_mid":50}\n')
                fh.write('{"ticker":"B","ts":2,  \n')   # truncated tail
            self.assertEqual(len(list(read_runs([path]))), 1)
        finally:
            os.unlink(path)


class TestClusterBootstrap(unittest.TestCase):
    def _board(self, seed=5, games=67, per_game=13, game_sd=1.6, mean=0.15):
        import random

        rng = random.Random(seed)
        values, clusters = [], []
        for g in range(games):
            effect = rng.gauss(0, game_sd)
            for _ in range(per_game):
                values.append(effect + rng.gauss(0, 0.5) + mean)
                clusters.append(f"G{g}")
        return values, clusters

    def test_clustering_widens_the_interval(self):
        from cfb_edge.bootstrap import cluster_bootstrap, naive_bootstrap

        v, c = self._board()
        naive = naive_bootstrap(v, replicates=2000)
        clustered = cluster_bootstrap(v, c, replicates=2000)
        self.assertGreater(clustered.width, naive.width * 2)
        self.assertEqual(clustered.clusters, 67)
        self.assertEqual(naive.clusters, len(v))

    def test_the_naive_interval_would_promote_what_the_honest_one_holds(self):
        # The whole reason this module exists.
        from cfb_edge.bootstrap import cluster_bootstrap, naive_bootstrap

        v, c = self._board()
        self.assertTrue(naive_bootstrap(v, replicates=2000).excludes_zero)
        self.assertFalse(cluster_bootstrap(v, c, replicates=2000).excludes_zero)

    def test_point_estimate_is_unchanged_by_clustering(self):
        from cfb_edge.bootstrap import cluster_bootstrap, naive_bootstrap

        v, c = self._board()
        self.assertAlmostEqual(
            naive_bootstrap(v, replicates=500).point,
            cluster_bootstrap(v, c, replicates=500).point,
            places=12,
        )

    def test_independent_data_needs_no_inflation(self):
        from cfb_edge.bootstrap import inflation_factor

        v, _ = self._board(game_sd=0.0)          # no shared game effect
        singleton = [f"G{i}" for i in range(len(v))]
        self.assertLess(abs(inflation_factor(v, singleton, replicates=1500) - 1.0), 0.15)

    def test_sample_size_planning_is_sane(self):
        from cfb_edge.bootstrap import required_clusters

        # A smaller edge against the same noise needs more games.
        self.assertGreater(required_clusters(0.10, 1.7), required_clusters(0.30, 1.7))
        self.assertEqual(required_clusters(-0.1, 1.7), 0)

    def test_mismatched_inputs_are_rejected(self):
        from cfb_edge.bootstrap import cluster_bootstrap

        with self.assertRaises(ValueError):
            cluster_bootstrap([1.0, 2.0], ["A"])
        with self.assertRaises(ValueError):
            cluster_bootstrap([], [])


class TestMeasureCommand(unittest.TestCase):
    """The end-to-end promotion check, on a fixture with a known shape."""

    def _run(self, *extra):
        import io, contextlib
        from cfb_edge.measure import main

        fx = Path(__file__).resolve().parent / "fixtures"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--runs", str(fx / "capture.jsonl.gz"),
                         "--entries", str(fx / "entries.csv"),
                         "--replicates", "800", *extra])
        return code, out.getvalue(), err.getvalue()

    def test_it_reports_both_intervals_and_holds(self):
        code, out, _ = self._run()
        self.assertEqual(code, 0)
        self.assertIn("resampling contracts", out)
        self.assertIn("resampling games", out)
        # The fixture has a real shared game effect, so clustering must matter.
        self.assertIn("clustering says hold", out)
        self.assertIn("verdict on the clustered interval: hold", out)

    def test_a_healthy_beat_rate_does_not_promote(self):
        # The fixture beats the close on most decided markets and still holds.
        _, out, _ = self._run()
        self.assertRegex(out, r"beat \d+ / tied \d+ / lost \d+")
        self.assertNotIn("verdict on the clustered interval: PROMOTE", out)

    def test_flat_markets_are_counted_separately(self):
        _, out, _ = self._run()
        self.assertIn("never moved", out)
        self.assertIn("carries no information", out)
        self.assertIn("markets that moved", out)

    def test_horizons_are_reported(self):
        _, out, _ = self._run()
        for minutes in (5, 30, 120, 1440):
            self.assertIn(f"{minutes}m before kickoff", out)

    def test_missing_capture_files_fail_loudly(self):
        import io, contextlib
        from cfb_edge.measure import main

        fx = Path(__file__).resolve().parent / "fixtures"
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = main(["--runs", "no/such/*.gz", "--entries", str(fx / "entries.csv")])
        self.assertEqual(code, 2)
        self.assertIn("no capture files matched", err.getvalue())

    def test_a_wrong_price_field_returns_nothing_rather_than_garbage(self):
        code, _, err = self._run("--price-field", "not_a_real_field")
        self.assertEqual(code, 2)
        self.assertIn("Check --price-field", err)


class TestLadder(unittest.TestCase):
    """Strike ladders: coherence, key numbers, and why the spread trade dies."""

    def _rungs(self, thresholds, prices, half=1.0):
        from cfb_edge.ladder import Strike

        return [Strike(t, yes_bid=p - half, yes_ask=p + half)
                for t, p in zip(thresholds, prices)]

    def test_a_coherent_ladder_has_no_arbitrage(self):
        from cfb_edge.ladder import Ladder, find_arbitrage

        lad = Ladder("g", "t", self._rungs([2.5, 3.5, 6.5, 9.5], [69, 66, 59, 50]))
        self.assertEqual(find_arbitrage(lad), [])

    def test_a_small_inversion_is_not_an_arbitrage(self):
        # Both legs pay a fee, and near a coin flip that is about 3.5c. An
        # inversion smaller than its own fees is not an opportunity.
        from cfb_edge.ladder import Ladder, find_arbitrage

        lad = Ladder("g", "t", self._rungs([6.5, 7.5], [51, 54]))
        self.assertEqual(find_arbitrage(lad), [])

    def test_a_large_inversion_is_found_and_priced(self):
        from cfb_edge.ladder import Ladder, find_arbitrage

        lad = Ladder("g", "t", self._rungs([6.5, 7.5], [51, 61]))
        arbs = find_arbitrage(lad)
        self.assertEqual(len(arbs), 1)
        self.assertGreater(arbs[0].profit_cents, 0)
        # Payout is at least 100 in every state, so profit is 100 - cost - fees.
        self.assertAlmostEqual(
            arbs[0].profit_cents,
            100.0 - arbs[0].cost_cents - arbs[0].fee_cents,
            places=9,
        )

    def test_arbitrage_uses_tradeable_prices_not_midpoints(self):
        from cfb_edge.ladder import Ladder, Strike, find_arbitrage

        # Mids invert (51 vs 53) but ask/bid do not cross enough to pay.
        lad = Ladder("g", "t", [Strike(6.5, 46, 56), Strike(7.5, 48, 58)])
        self.assertEqual(find_arbitrage(lad), [])

    def test_a_smooth_ladder_underprices_the_key_numbers(self):
        from cfb_edge.distribution import _normal_cdf, sigma_for_total
        from cfb_edge.ladder import Ladder, key_number_gaps

        mu, sig = 10.0, sigma_for_total(52.0)
        ts = [2.5, 3.5, 6.5, 7.5]
        prices = [(1.0 - _normal_cdf(t, mu, sig)) * 100.0 for t in ts]
        gaps = key_number_gaps(Ladder("g", "t", self._rungs(ts, prices)),
                               model_margin=mu)
        by_margin = {g.margin: g for g in gaps}
        self.assertTrue(by_margin[3].underpriced)
        self.assertTrue(by_margin[7].underpriced)
        # Three is the most underpriced, because it carries the most extra mass.
        self.assertLess(by_margin[3].ratio, by_margin[7].ratio)

    def test_a_correctly_priced_ladder_raises_no_flag(self):
        from cfb_edge.distribution import margin_pmf, sigma_for_total
        from cfb_edge.ladder import Ladder, key_number_gaps

        mu = 10.0
        pmf = margin_pmf(mu, sigma_for_total(52.0))
        ts = [2.5, 3.5, 6.5, 7.5]
        prices = [sum(v for k, v in pmf.items() if k > t) * 100.0 for t in ts]
        for g in key_number_gaps(Ladder("g", "t", self._rungs(ts, prices)),
                                 model_margin=mu):
            self.assertAlmostEqual(g.ratio, 1.0, delta=0.06)

    def test_only_isolated_single_margins_are_reported(self):
        from cfb_edge.ladder import Ladder, key_number_gaps

        # 2.5 -> 6.5 spans four margins and pins nothing.
        lad = Ladder("g", "t", self._rungs([2.5, 6.5], [69, 59]))
        self.assertEqual(key_number_gaps(lad, model_margin=10.0), [])

    def test_the_key_number_spread_cannot_clear_its_own_fees(self):
        """The finding this module exists to establish.

        Kalshi charges per leg on each leg's own notional, so a vertical spread
        between two mid-ladder strikes pays fees as though two 65-cent
        contracts were traded while the position is worth a few cents.

        The original version of this test asserted something stronger: that the
        fee alone exceeded the whole bucket's value, making the breakeven cost
        negative. Refitting the key numbers against 14,687 real games disproved
        that. The true mispricing on a three-point margin is about 3.7c, not
        the 0.94c the earlier priors implied, so the buckets are worth roughly
        four times what the model used to think and their breakeven costs are
        positive.

        The trade is still uneconomic, but for a weaker and more contingent
        reason: at the one-cent tick, crossing two spreads plus two fees costs
        about 4.2c against a breakeven of at best 2.94c. That is a gap of a
        cent and a half rather than an impossibility, and it would close for a
        maker who never crosses the spread.
        """
        from cfb_edge.distribution import _normal_cdf, sigma_for_total
        from cfb_edge.ladder import Ladder, bucket_trades

        mu, sig = 10.0, sigma_for_total(52.0)
        ts = [2.5, 3.5, 6.5, 7.5, 9.5, 10.5]
        prices = [(1.0 - _normal_cdf(t, mu, sig)) * 100.0 for t in ts]
        # Even at a half-cent half-spread, tighter than Kalshi's tick allows.
        trades = bucket_trades(Ladder("g", "t", self._rungs(ts, prices, half=0.5)),
                               model_margin=mu)
        self.assertTrue(trades)
        for t in trades:
            self.assertFalse(t.clears)
        # Breakeven is positive now, but far below what the tick actually
        # costs, and the best case is the three-point bucket.
        best = max(trades, key=lambda t: t.breakeven_cost)
        self.assertEqual(best.margin, 3)
        self.assertGreater(best.breakeven_cost, 0.0)
        self.assertLess(best.breakeven_cost, 4.0)

    def test_the_refit_quadrupled_the_key_number_mispricing(self):
        """Guards the correction, so the old prior cannot creep back."""
        from cfb_edge.distribution import KEY_BUMPS

        self.assertGreater(KEY_BUMPS[3], 2.5)
        self.assertGreater(KEY_BUMPS[7], 2.2)
        # The troughs are as real as the peaks and must not be dropped.
        self.assertLess(KEY_BUMPS[9], 0.5)
        self.assertLess(KEY_BUMPS[12], 0.5)

    def test_coherence_report_counts_inversions(self):
        from cfb_edge.ladder import Ladder, coherence_report

        lad = Ladder("COL @ GT", "GT", self._rungs([2.5, 6.5, 7.5], [69, 51, 61]))
        self.assertIn("1 inverted", coherence_report(lad))
        self.assertIn("GT", coherence_report(lad))


class TestKeyNumberDrift(unittest.TestCase):
    """Guards the era findings from 17,472 games, 2001-2025."""

    def test_seven_carries_the_modern_value_not_the_pooled_one(self):
        # The seven rose from 2.05 (2001-2010) to 2.44 (2011-2025), z = 3.25,
        # so the pooled 2.32 understates it for a game played today.
        from cfb_edge.distribution import KEY_BUMPS

        self.assertAlmostEqual(KEY_BUMPS[7], 2.44, places=2)

    def test_three_is_flat_and_keeps_the_full_sample_fit(self):
        # No drift (t = 0.41 across 25 years), so the larger sample wins and
        # chasing the most recent era would be fitting noise.
        from cfb_edge.distribution import KEY_BUMPS

        self.assertAlmostEqual(KEY_BUMPS[3], 2.64, places=2)

    def test_three_still_outranks_seven(self):
        from cfb_edge.distribution import KEY_BUMPS

        self.assertGreater(KEY_BUMPS[3], KEY_BUMPS[7])

    def test_flat_numbers_were_not_chased(self):
        # 14 and 21 read lower in the most recent era, but their trends are
        # insignificant (t = 0.47 and -0.12), so the pooled fit is kept.
        from cfb_edge.distribution import KEY_BUMPS

        self.assertAlmostEqual(KEY_BUMPS[14], 1.48, places=2)
        self.assertAlmostEqual(KEY_BUMPS[21], 1.68, places=2)


class TestEncompassing(unittest.TestCase):
    """The test that decides whether a model deserves a blend weight."""

    def _data(self, model_signal, n=4000, seed=3):
        """Outcomes driven by the market plus, optionally, real model signal."""
        import random

        rng = random.Random(seed)
        actual, market, model = [], [], []
        for _ in range(n):
            truth = rng.gauss(0, 14)
            extra = rng.gauss(0, 6)
            market.append(truth)
            model.append(truth + rng.gauss(0, 5) + (extra if model_signal else 0.0))
            actual.append(truth + (extra if model_signal else 0.0) + rng.gauss(0, 15))
        return actual, market, model

    def test_a_useless_model_gets_a_zero_coefficient(self):
        from cfb_edge.encompassing import encompassing_regression

        r = encompassing_regression(*self._data(model_signal=False))
        self.assertFalse(r.model_adds_information)
        self.assertLess(abs(r.model_coef), 0.15)
        self.assertGreater(r.market_t, 5.0)

    def test_a_model_with_real_signal_is_detected(self):
        from cfb_edge.encompassing import encompassing_regression

        r = encompassing_regression(*self._data(model_signal=True))
        self.assertTrue(r.model_adds_information)
        self.assertGreater(r.implied_model_weight, 0.0)

    def test_the_shipped_weight_matches_the_measured_verdict(self):
        # blend.py ships zero because the real regression said zero.
        from cfb_edge.blend import MAX_MODEL_WEIGHT
        from cfb_edge.encompassing import encompassing_regression

        r = encompassing_regression(*self._data(model_signal=False))
        self.assertFalse(r.model_adds_information)
        self.assertEqual(MAX_MODEL_WEIGHT, 0.0)

    def test_collinear_projections_are_refused(self):
        from cfb_edge.encompassing import encompassing_regression

        market = [float(i) for i in range(50)]
        with self.assertRaises(ValueError):
            encompassing_regression(market, market, market)

    def test_mismatched_lengths_are_refused(self):
        from cfb_edge.encompassing import encompassing_regression

        with self.assertRaises(ValueError):
            encompassing_regression([1.0, 2.0], [1.0, 2.0], [1.0])


class TestLineMovement(unittest.TestCase):
    """CLV is the right scoreboard, but it still has to clear the price."""

    def test_a_worse_price_demands_more_clv(self):
        from cfb_edge.line_movement import clv_required

        self.assertGreater(clv_required(-120).clv_needed, clv_required(-110).clv_needed)
        self.assertGreater(clv_required(-110).clv_needed, clv_required(-105).clv_needed)

    def test_the_measured_edge_misses_at_minus_110_and_clears_at_reduced_juice(self):
        # The headline result: 0.44 points of CLV, real (t = 4.7), not enough.
        from cfb_edge.line_movement import clv_required

        self.assertFalse(clv_required(-110, achieved=0.44).clears)
        self.assertTrue(clv_required(-105, achieved=0.44).clears)

    def test_break_even_price_inverts_the_requirement(self):
        from cfb_edge.line_movement import break_even_price, clv_required

        for clv in (0.25, 0.5, 0.75):
            price = break_even_price(clv)
            self.assertLessEqual(clv_required(price).clv_needed, clv + 0.05)

    def test_density_is_averaged_not_read_off_a_key_number(self):
        # A point estimate at 3 would overstate the density badly.
        from cfb_edge.distribution import margin_pmf, sigma_for_total
        from cfb_edge.line_movement import local_density

        pmf = margin_pmf(0.0, sigma_for_total(52.0))
        self.assertLess(local_density(), pmf[3])
        self.assertGreater(local_density(), pmf[9])

        # The centre follows the line, so density at a real number is read
        # under the distribution that number implies rather than under a
        # pick'em. A seven is a peak wherever the market has put it.
        # Zero is not in the support at all: a regulation tie goes to overtime.
        at_seven = margin_pmf(-7.0, sigma_for_total(52.0))
        self.assertNotIn(0, at_seven)
        self.assertGreater(local_density(7.0), at_seven[-25])
        self.assertAlmostEqual(local_density(7.0), local_density(-7.0), places=9)

    def test_a_free_price_needs_no_edge(self):
        from cfb_edge.line_movement import clv_required

        self.assertAlmostEqual(clv_required(100).clv_needed, 0.0, places=9)


class TestBetLog(unittest.TestCase):
    """The scorecard is worthless if nothing feeds it, and worse than
    worthless if what feeds it has the sign backwards."""

    def _log(self, path, **kw):
        from cfb_edge.cli import main
        import io, contextlib
        args = ["log", "--bets", str(path)]
        for k, v in kw.items():
            args += [f"--{k.replace('_', '-')}", str(v)]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(args)
        return code, buf.getvalue()

    def _tmp(self):
        import os, tempfile
        fd, path = tempfile.mkstemp(suffix=".csv"); os.close(fd); os.unlink(path)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def test_an_exchange_strike_is_stored_as_laying_that_number(self):
        """The trap this command exists to remove.

        The card prints the Kansas play as "Kansas at +3", meaning a contract
        paying if Kansas wins by more than three. In the convention
        `cover_probability` documents, that is Kansas *laying* three, so the
        stored line is -3. Anyone translating by hand gets this backwards
        roughly half the time, and the error is invisible: the scorecard still
        prints, just with the sign of its only conclusion flipped.
        """
        from cfb_edge.clv import load_bets

        path = self._tmp()
        code, out = self._log(path, game="Missouri @ Kansas", side="Kansas",
                              strike=3, cents=26, stake=0.0037)
        self.assertEqual(code, 0)
        bet, = load_bets(path)
        self.assertEqual(bet.line_taken, -3.0)
        self.assertEqual(bet.side, "Kansas")
        self.assertEqual((bet.away, bet.home), ("Missouri", "Kansas"))
        self.assertIn("laying 3", out)

        # A book's line is taken as given, in the same convention.
        other = self._tmp()
        self._log(other, game="A @ B", side="A", line=+6.5, price=-110, stake=0.01)
        self.assertEqual(load_bets(other)[0].line_taken, 6.5)

    def test_cents_convert_to_the_price_the_scorecard_needs(self):
        from cfb_edge.clv import load_bets
        from cfb_edge.market import (american_to_probability,
                                     probability_to_american)

        path = self._tmp()
        self._log(path, game="A @ B", side="B", strike=3, cents=26, stake=0.01)
        bet, = load_bets(path)
        # American odds cannot express every cent exactly: `decimal_to_american`
        # rounds, so the round trip loses at most 8.93e-06 of probability across
        # 1c to 99c. The edge being measured is around 1e-2, so that is under a
        # tenth of one percent of it. Measured rather than assumed, and asserted
        # here so a future rounding change that made it worse would show up.
        self.assertAlmostEqual(american_to_probability(bet.price_taken), 0.26,
                               delta=1e-5)
        worst = max(abs(american_to_probability(probability_to_american(c / 100))
                        - c / 100) for c in range(1, 100))
        self.assertLess(worst, 1e-5)

    def test_a_side_that_is_not_playing_is_refused(self):
        path = self._tmp()
        with self.assertRaises(SystemExit):
            self._log(path, game="Missouri @ Kansas", side="Kansas State",
                      strike=3, cents=26, stake=0.01)

    def test_the_log_round_trips_into_the_scorecard(self):
        from cfb_edge.cli import main
        from cfb_edge.clv import build_report, load_bets, settle_bet
        import io, contextlib

        path = self._tmp()
        self._log(path, game="Missouri @ Kansas", side="Kansas", strike=3,
                  cents=26, stake=0.0037, date="2026-09-11")
        settle_bet(path, game="Missouri @ Kansas", closing_line=-4.5,
                   result="win")
        report = build_report(load_bets(path))
        self.assertEqual(report.bets, 1)
        self.assertEqual(report.beat_close, 1)
        self.assertAlmostEqual(report.mean_line_clv, 1.5, places=9)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(main(["clv", "--bets", str(path)]), 0)
        self.assertIn("Beat close 1", buf.getvalue())

    def test_settling_an_ambiguous_game_refuses_rather_than_guesses(self):
        """Settling the wrong row corrupts the only scorecard there is."""
        from cfb_edge.clv import BetNotFound, settle_bet

        path = self._tmp()
        for d in ("2026-09-11", "2026-10-11"):
            self._log(path, game="A @ B", side="B", strike=3, cents=50,
                      stake=0.01, date=d)
        with self.assertRaises(BetNotFound) as ctx:
            settle_bet(path, game="A @ B", closing_line=-3.0)
        self.assertIn("2026-09-11", str(ctx.exception))
        # Naming the date resolves it.
        bet = settle_bet(path, game="A @ B", date="2026-10-11", closing_line=-3.0)
        self.assertEqual(bet.date, "2026-10-11")

    def test_settling_a_game_that_was_never_logged_says_so(self):
        from cfb_edge.clv import BetNotFound, settle_bet

        path = self._tmp()
        self._log(path, game="A @ B", side="B", strike=3, cents=50, stake=0.01)
        with self.assertRaises(BetNotFound):
            settle_bet(path, game="C @ D", closing_line=-3.0)

    def test_a_result_outside_win_loss_push_is_refused(self):
        from cfb_edge.clv import settle_bet

        path = self._tmp()
        self._log(path, game="A @ B", side="B", strike=3, cents=50, stake=0.01)
        with self.assertRaises(ValueError):
            settle_bet(path, game="A @ B", result="cashed out")

    def test_appending_preserves_earlier_bets(self):
        from cfb_edge.clv import load_bets

        path = self._tmp()
        for i in range(4):
            self._log(path, game=f"A{i} @ B{i}", side=f"B{i}", strike=3,
                      cents=50, stake=0.01)
        bets = load_bets(path)
        self.assertEqual(len(bets), 4)
        self.assertEqual([b.home for b in bets], ["B0", "B1", "B2", "B3"])


class TestEarlySeasonGate(unittest.TestCase):
    """Weeks 1 and 2 have no measurable edge, so they produce no plays."""

    def test_no_side_before_the_gate_however_large_the_gap(self):
        from cfb_edge.strategy import MIN_SEASON_WEEK, signal_side

        # Charlotte at Ole Miss: the model says 21.7, the market said 47.5.
        kw = dict(projected_margin=21.7, opening_home_line=-47.5)
        for wk in range(1, MIN_SEASON_WEEK):
            side, gap = signal_side("Ole Miss", "Charlotte", week=wk, **kw)
            self.assertIsNone(side, f"week {wk} should produce no side")
            self.assertGreater(abs(gap), 20)     # the gap is still reported
        side, _ = signal_side("Ole Miss", "Charlotte", week=MIN_SEASON_WEEK, **kw)
        self.assertIsNotNone(side)

    def test_omitting_the_week_skips_the_check_rather_than_guessing_one(self):
        from cfb_edge.strategy import signal_side

        side, _ = signal_side("B", "A", projected_margin=0.0,
                              opening_home_line=8.0, week=None)
        self.assertEqual(side, "B")

    def test_a_large_disagreement_is_not_capped(self):
        """Worth a test because the opposite looks obviously right.

        Bucketed over 1,375 historical bets, CLV rises with the size of the
        disagreement: +0.099 at 4-6 points, +0.527 at 8-10, +0.572 at 14-20.
        A cap would throw away the best-paying bets in the set.
        """
        from cfb_edge.strategy import signal_side

        side, gap = signal_side("Ole Miss", "Charlotte", projected_margin=21.7,
                                opening_home_line=-47.5, week=8)
        self.assertEqual(side, "Charlotte")
        self.assertGreater(abs(gap), 20)

    def test_the_paper_log_is_gated_too(self):
        """The stop rule reads the paper log, so an ungated week would feed it
        observations the strategy is not claiming to make."""
        from cfb_edge.paper import signals_for

        slate = {"Missouri @ Kansas": -0.07}
        opens = {"Missouri @ Kansas": 6.5}
        self.assertEqual(signals_for(slate, opens, date="2026-09-08", week=2), [])
        self.assertEqual(len(signals_for(slate, opens, date="2026-09-08", week=3)), 1)

    def test_the_cli_says_the_week_is_why(self):
        import contextlib, io, os, tempfile
        from cfb_edge.cli import main

        fd, slate = tempfile.mkstemp(suffix=".csv"); os.close(fd)
        fd, opens = tempfile.mkstemp(suffix=".csv"); os.close(fd)
        self.addCleanup(os.unlink, slate); self.addCleanup(os.unlink, opens)
        open(slate, "w").write("game,projected_margin,side,posted_line,total\n"
                               "Missouri @ Kansas,-0.07,,,52\n")
        open(opens, "w").write("game,opening_line\nMissouri @ Kansas,6.5\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["play", "--slate", slate, "--opens", opens, "--week", "2"])
        out = buf.getvalue()
        self.assertIn("Week 2: no plays", out)
        self.assertIn("not because they were quiet", out)


class TestPaperSignals(unittest.TestCase):
    """Measuring costs nothing, so measure everything."""

    def _tmp(self, suffix=".csv"):
        import os, tempfile
        fd, path = tempfile.mkstemp(suffix=suffix); os.close(fd); os.unlink(path)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def _log(self, polls):
        import gzip, json
        path = self._tmp(".jsonl.gz")
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            for quotes in polls:
                fh.write(json.dumps({"polled_at": "x", "quotes": quotes}) + "\n")
        return path

    def _q(self, game, line, seen):
        return {"game": game, "book": "bk", "market": "spread",
                "line": line, "price": -110, "seen_at": seen}

    def test_one_log_yields_both_the_open_and_the_close(self):
        """The append-only capture already holds both, so there is no second
        source and no way for the two to disagree."""
        from cfb_edge.watch import OpeningBook

        path = self._log([[self._q("A @ B", 6.5, "t0")],
                          [self._q("A @ B", 6.0, "t1")],
                          [self._q("A @ B", 5.5, "t2")]])
        book = OpeningBook.load(path)
        self.assertEqual(book.consensus_opens()["A @ B"], 6.5)
        self.assertEqual(book.consensus_closes()["A @ B"], 5.5)

        # And a live capture keeps both as it records.
        from cfb_edge.watch import Quote
        book.record([Quote("A @ B", "bk", "spread", 4.5, -110, "t3")])
        self.assertEqual(book.consensus_opens()["A @ B"], 6.5)
        self.assertEqual(book.consensus_closes()["A @ B"], 4.5)

    def test_a_signal_records_the_line_from_the_backed_side(self):
        from cfb_edge.paper import signals_for

        # Home line +6.5, model near a pick'em: the home side is underpriced.
        home, = signals_for({"Missouri @ Kansas": -0.07},
                            {"Missouri @ Kansas": 6.5}, date="2026-09-08")
        self.assertEqual(home.side, "Kansas")
        self.assertEqual(home.line_taken, 6.5)

        # Home line -2.5, model has the away side better: the away number is
        # the negation of the home one.
        away, = signals_for({"Oklahoma @ Michigan": -2.67},
                            {"Oklahoma @ Michigan": -2.5}, date="2026-09-08")
        self.assertEqual(away.side, "Oklahoma")
        self.assertEqual(away.line_taken, 2.5)

    def test_games_under_the_bar_are_not_recorded(self):
        from cfb_edge.paper import signals_for

        # The real week 2 board: two fire, two do not.
        slate = {"Missouri @ Kansas": -0.07, "Oklahoma @ Michigan": -2.67,
                 "Ohio State @ Texas": -0.86, "Arizona State @ Texas A&M": 12.52}
        opens = {"Missouri @ Kansas": 6.5, "Oklahoma @ Michigan": -2.5,
                 "Ohio State @ Texas": -2.5, "Arizona State @ Texas A&M": -14.5}
        sides = {s.side for s in signals_for(slate, opens, date="2026-09-08")}
        self.assertEqual(sides, {"Kansas", "Oklahoma"})

    def test_recording_the_same_week_twice_adds_nothing(self):
        """The board opens in pieces, so the command has to be re-runnable."""
        from cfb_edge.paper import record, signals_for

        path = self._tmp()
        sigs = signals_for({"A @ B": 0.0}, {"A @ B": 6.5}, date="2026-09-08")
        self.assertEqual(record(path, sigs), (1, 0))
        self.assertEqual(record(path, sigs), (0, 1))

    def test_grading_flips_the_sign_for_an_away_signal(self):
        """The close is captured from the home side; an away signal took its
        negation, and getting that backwards inverts the only conclusion."""
        from cfb_edge.clv import load_bets
        from cfb_edge.paper import grade, record, signals_for

        path = self._tmp()
        record(path, signals_for({"Oklahoma @ Michigan": -2.67},
                                 {"Oklahoma @ Michigan": -2.5}, date="2026-09-08"))
        # Home line moved -2.5 -> +6.5, nine points toward the away side.
        graded, still_open = grade(path, {"Oklahoma @ Michigan": 6.5})
        self.assertEqual((graded, still_open), (1, 0))
        bet, = load_bets(path)
        self.assertEqual(bet.closing_line, -6.5)
        self.assertAlmostEqual(bet.line_clv, 9.0, places=9)

    def test_grading_twice_cannot_move_a_recorded_close(self):
        from cfb_edge.clv import load_bets
        from cfb_edge.paper import grade, record, signals_for

        path = self._tmp()
        record(path, signals_for({"A @ B": 0.0}, {"A @ B": 6.5}, date="2026-09-08"))
        grade(path, {"A @ B": 5.5})
        grade(path, {"A @ B": 1.0})
        self.assertEqual(load_bets(path)[0].closing_line, 5.5)

    def test_paper_rows_can_never_read_as_realised_profit(self):
        from cfb_edge.clv import CAPTURED, build_report, load_bets
        from cfb_edge.paper import grade, record, signals_for

        path = self._tmp()
        record(path, signals_for({"A @ B": 0.0, "C @ D": 0.0},
                                 {"A @ B": 6.5, "C @ D": 7.0}, date="2026-09-08",
                                 sources={"A @ B": CAPTURED, "C @ D": CAPTURED}))
        grade(path, {"A @ B": 5.5, "C @ D": 6.0})
        report = build_report(load_bets(path))
        self.assertEqual(report.staked, 0.0)
        self.assertEqual(report.realised_profit, 0.0)
        self.assertEqual(report.roi, 0.0)
        # The line CLV is still measured, which is the whole point.
        self.assertAlmostEqual(report.mean_line_clv, 1.0, places=9)
        self.assertTrue(all(b.price_clv is None for b in load_bets(path)))





class TestCurrentWeek(unittest.TestCase):
    """A weekly scheduled task cannot be told a week that moves.

    `capture.bat` shipped without a slate at all, so `--source kalshi` refused
    to start every single time it was run: the documented way to start the
    capture could not start the capture. Fixing it needs the week, and a task
    registered once in September cannot carry the right one into October.
    """

    ROWS = [
        {"season_type": "regular", "week": "1", "start_date": "2026-08-29T16:00:00Z"},
        {"season_type": "regular", "week": "1", "start_date": "2026-09-07T23:00:00Z"},
        {"season_type": "regular", "week": "2", "start_date": "2026-09-13T16:00:00Z"},
        {"season_type": "regular", "week": "3", "start_date": "2026-09-20T16:00:00Z"},
        {"season_type": "postseason", "week": "1", "start_date": "2026-12-20T16:00:00Z"},
    ]

    def test_it_names_the_week_still_being_priced(self):
        from cfb_edge.slate import current_week

        self.assertEqual(current_week(2026, today="2026-09-14", rows=self.ROWS), 3)

    def test_a_week_still_in_progress_is_the_current_one(self):
        """Saturday of week 3 is still week 3, not week 4. A capture started
        mid-week records whatever has not posted yet."""
        from cfb_edge.slate import current_week

        self.assertEqual(current_week(2026, today="2026-09-20", rows=self.ROWS), 3)

    def test_a_long_opening_week_does_not_shift_the_count(self):
        """Week 1 of 2026 runs Aug 29 to Sep 7. Deriving the week by dividing
        elapsed days by seven would put Sep 14 in week 3 by luck here and in
        the wrong week most other seasons."""
        from cfb_edge.slate import current_week

        self.assertEqual(current_week(2026, today="2026-09-01", rows=self.ROWS), 1)
        self.assertEqual(current_week(2026, today="2026-09-08", rows=self.ROWS), 2)

    def test_a_finished_season_reports_nothing_rather_than_the_last_week(self):
        """Returning 15 forever would look exactly like a working capture."""
        from cfb_edge.slate import current_week

        self.assertIsNone(current_week(2026, today="2027-01-15", rows=self.ROWS))

    def test_the_postseason_does_not_masquerade_as_a_regular_week(self):
        from cfb_edge.slate import current_week

        self.assertIsNone(current_week(2026, today="2026-12-01", rows=self.ROWS))



class TestCalibration(unittest.TestCase):
    """Is the model's projected margin the size reality is?

    `RATING_SCALE` corrects the solver's compression, and was a hand-measured
    constant that nothing in the package could re-check. Measured walk-forward
    over 2,995 FBS games from 2021 to 2025 it was still seven percent too low,
    in the same direction in every bucket of projected margin. A compressed
    model makes every underdog look undervalued, so a strategy that bets on
    disagreeing with the market reads its own scale error as signal.
    """

    def _row(self, week, home, away, hp, ap, *, neutral=False, kind="regular",
             div="fbs"):
        return {"season_type": kind, "home_division": div, "away_division": div,
                "week": str(week), "home_team": home, "away_team": away,
                "home_points": str(hp), "away_points": str(ap),
                "neutral_site": "TRUE" if neutral else "FALSE"}

    def _league(self, weeks=12, teams=16, spread=2.0, noise=0.0, seed=5):
        """A synthetic season whose true strengths are known."""
        import random
        rng = random.Random(seed)
        names = [f"T{i:02d}" for i in range(teams)]
        truth = {n: (i - (teams - 1) / 2) * spread / teams for i, n in enumerate(names)}
        rows = []
        for week in range(1, weeks + 1):
            order = names[:]
            rng.shuffle(order)
            for home, away in zip(order[::2], order[1::2]):
                margin = truth[home] - truth[away] + 3.2
                if noise:
                    margin += rng.gauss(0, noise)
                margin = round(margin)
                base = 24
                rows.append(self._row(week, home, away, base + margin, base))
        return rows

    def test_a_perfect_fit_returns_slope_one(self):
        from cfb_edge.calibration import fit

        points = [(x, 2.0 + 1.0 * x) for x in range(-20, 21)]
        c = fit(points)
        self.assertAlmostEqual(c.slope, 1.0, places=9)
        self.assertAlmostEqual(c.intercept, 2.0, places=9)
        self.assertFalse(c.compressed)

    def test_a_compressed_projection_reports_slope_above_one(self):
        from cfb_edge.calibration import fit

        # Projections are 70% of reality, so reality is 1/0.7 times projection.
        points = [(0.7 * x, float(x)) for x in range(-20, 21)]
        c = fit(points)
        self.assertAlmostEqual(c.slope, 1.0 / 0.7, places=6)
        self.assertAlmostEqual(c.implied_scale(1.40), 1.40 / 0.7, places=6)

    def test_a_slope_needs_three_points_and_real_spread(self):
        from cfb_edge.calibration import fit

        with self.assertRaises(ValueError):
            fit([(1.0, 1.0), (2.0, 2.0)])
        with self.assertRaises(ValueError):
            fit([(3.0, 1.0), (3.0, 2.0), (3.0, 9.0)])

    def test_the_target_week_cannot_rate_the_teams_that_predict_it(self):
        """The test that makes the measurement worth anything.

        Solving ratings on the whole season and projecting games inside it
        returns a slope near one however wrong the scale is, because each
        team's own result helped set the rating that predicts it. Walk-forward
        is the difference between measuring the model and measuring nothing.
        """
        from cfb_edge.calibration import walk_forward_points
        from cfb_edge.ratings import Game, solve_ratings
        from cfb_edge.projection import Matchup, project

        rows = self._league(weeks=14, teams=16, noise=6.0)
        points = walk_forward_points(rows, from_week=4, min_history=40)
        # 8 games a week, usable from the week history first reaches 40.
        self.assertGreater(len(points), 60)

        # Every projection must be reproducible from strictly earlier weeks.
        records = [(int(r["week"]), r["home_team"], r["away_team"],
                    int(r["home_points"]) - int(r["away_points"])) for r in rows]
        by_week = {}
        for week, home, away, margin in records:
            by_week.setdefault(week, []).append((home, away, margin))

        week = max(w for w in by_week if w >= 4)
        history = [Game(h, a, 24 + m, 24, False)
                   for w, h, a, m in records if w < week]
        model = solve_ratings(history)
        home, away, _ = by_week[week][0]
        expected = project(model, Matchup(home=home, away=away)).home_margin
        actual_for_that_game = [p for p, _ in points]
        self.assertIn(round(expected, 6),
                      [round(v, 6) for v in actual_for_that_game])

    def test_a_scale_that_fits_drives_the_slope_to_one(self):
        from cfb_edge.calibration import fit, walk_forward_points

        rows = self._league(weeks=14, teams=16, noise=5.0)
        before = fit(walk_forward_points(rows, from_week=4, min_history=40))
        corrected = before.implied_scale(1.0)
        after = fit(walk_forward_points(rows, from_week=4, min_history=40,
                                        rating_scale=corrected))
        self.assertLess(abs(after.slope - 1.0), abs(before.slope - 1.0) + 1e-9)
        self.assertLess(abs(after.t_against_one), 1.96)

    def test_only_fbs_regular_season_games_with_scores_are_measured(self):
        from cfb_edge.calibration import _rows_to_records

        rows = [
            self._row(5, "A", "B", 28, 21),
            self._row(5, "C", "D", 28, 21, kind="postseason"),
            self._row(5, "E", "F", 28, 21, div="fcs"),
            {**self._row(5, "G", "H", 28, 21), "home_points": ""},
            {**self._row(5, "I", "J", 28, 21), "week": "not a week"},
            {**self._row(5, "", "K", 28, 21)},
        ]
        kept = _rows_to_records(rows)
        self.assertEqual([r[1] for r in kept], ["A"])

    def test_pooling_favours_the_season_measured_most_precisely(self):
        from cfb_edge.calibration import Calibration, pool

        tight = Calibration(games=600, slope=1.00, intercept=0.0,
                            stderr=0.01, residual_sd=16.0)
        loose = Calibration(games=60, slope=2.00, intercept=0.0,
                            stderr=0.50, residual_sd=16.0)
        p = pool([tight, loose])
        self.assertLess(p.slope, 1.01)
        self.assertEqual(p.games, 660)
        self.assertLess(p.stderr, tight.stderr)

    def test_the_scale_in_use_is_the_one_the_measurement_supports(self):
        """Guards the constant against drifting back by accident."""
        from cfb_edge.projection import RATING_SCALE

        self.assertAlmostEqual(RATING_SCALE, 1.50, places=2)


class TestOneSidedLadder(unittest.TestCase):
    """A ladder quoted from one side is still a ladder.

    Kalshi's college football book is thin, so a game quoted only from the
    favourite's side is the common case rather than the odd one. These were
    being dropped from the capture on the grounds that there was "no second
    team to anchor the away side of the curve", which is not what the away
    team is for: `survival_curve` takes both names from the slate and uses the
    away one only to recognise away rungs and complement them. A ladder with
    no away rungs never needs it, and dropping those games cost observations
    where they are scarcest.
    """

    def _board(self, team, strikes, event="26sep19kytam",
               title="Kentucky at Texas A&M"):
        return [{"event_ticker": event, "ticker": f"{event}-{i}", "title": title,
                 "yes_sub_title": f"{team} wins by more than {s}",
                 "yes_bid": b, "yes_ask": a, "status": "active",
                 "close_time": "2026-09-20T23:30:00Z"}
                for i, (s, b, a) in enumerate(strikes)]

    def _opener(self, board):
        import json
        return lambda url, **kw: json.dumps({"markets": board, "cursor": ""})

    def test_a_home_only_ladder_still_yields_the_line(self):
        from cfb_edge.providers.kalshi import board_quotes

        board = self._board("Texas A&M", [(2.5, 91, 93), (10.5, 67, 71),
                                          (16.5, 48, 52), (24.5, 26, 30)])
        quotes = board_quotes(games=["Kentucky @ Texas A&M"],
                              opener=self._opener(board),
                              seen_at="2026-09-14T22:00:00+00:00")
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].game, "Kentucky @ Texas A&M")
        self.assertAlmostEqual(quotes[0].line, -16.5, places=1)

    def test_an_away_only_ladder_yields_the_line_with_the_sign_flipped(self):
        from cfb_edge.providers.kalshi import board_quotes

        board = self._board("Kentucky", [(2.5, 91, 93), (10.5, 67, 71),
                                         (16.5, 48, 52), (24.5, 26, 30)])
        quotes = board_quotes(games=["Kentucky @ Texas A&M"],
                              opener=self._opener(board),
                              seen_at="2026-09-14T22:00:00+00:00")
        self.assertEqual(len(quotes), 1)
        self.assertAlmostEqual(quotes[0].line, 16.5, places=1)

    def test_a_lone_team_on_two_slate_fixtures_is_skipped(self):
        """Uniqueness or nothing. Two candidate fixtures is a coin flip on
        orientation, which is the exact defect the schedule lookup exists to
        remove."""
        from cfb_edge.providers.kalshi import board_quotes

        board = self._board("Texas A&M", [(2.5, 91, 93), (16.5, 48, 52)])
        quotes = board_quotes(
            games=["Kentucky @ Texas A&M", "Texas A&M @ Auburn"],
            opener=self._opener(board), seen_at="2026-09-14T22:00:00+00:00")
        self.assertEqual(quotes, [])

    def test_a_lone_team_not_on_the_slate_is_skipped(self):
        from cfb_edge.providers.kalshi import board_quotes

        board = self._board("Texas A&M", [(2.5, 91, 93), (16.5, 48, 52)])
        quotes = board_quotes(games=["Florida @ Auburn"],
                              opener=self._opener(board),
                              seen_at="2026-09-14T22:00:00+00:00")
        self.assertEqual(quotes, [])

    def test_a_ladder_that_never_crosses_a_coin_flip_still_yields_nothing(self):
        """The one-sided fix must not weaken the refusal to extrapolate."""
        from cfb_edge.providers.kalshi import board_quotes

        board = self._board("Texas A&M", [(2.5, 91, 93), (10.5, 67, 71)])
        quotes = board_quotes(games=["Kentucky @ Texas A&M"],
                              opener=self._opener(board),
                              seen_at="2026-09-14T22:00:00+00:00")
        self.assertEqual(quotes, [])


class TestLineProvenance(unittest.TestCase):
    """A line is evidence only when something recorded it independently.

    This is the same defect as the `seen_at` bug, one layer up. There, a blank
    timestamp silently became the current clock and biased closing line value
    +23.5 points against a true +1.0, in the direction of confirming the
    strategy. Here a number with no origin at all loads, prices a card and
    grades into CLV exactly like a captured one. Both fail toward belief, which
    is the only direction that costs money.
    """

    def _tmp(self):
        import os, tempfile
        fd, path = tempfile.mkstemp(suffix=".csv"); os.close(fd); os.unlink(path)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def test_a_line_with_no_provenance_is_kept_out_of_the_clv(self):
        from cfb_edge.clv import LoggedBet, build_report

        typed = LoggedBet(date="2026-09-14", away="Michigan State",
                          home="Notre Dame", side="Michigan State",
                          line_taken=29.5, price_taken=-110.0, stake=0.0,
                          closing_line=14.0)
        report = build_report([typed])
        # 15.5 points of CLV, which would read as a spectacular confirmation.
        self.assertEqual(typed.line_clv, 15.5)
        self.assertEqual(report.graded, 0)
        self.assertEqual(report.unverified, 1)
        self.assertEqual(report.mean_line_clv, 0.0)
        self.assertIn("EXCLUDED", report.summary())

    def test_a_captured_line_grades(self):
        from cfb_edge.clv import CAPTURED, LoggedBet, build_report

        seen = LoggedBet(date="2026-09-14", away="A", home="B", side="B",
                         line_taken=-6.5, price_taken=-110.0, stake=0.0,
                         closing_line=-7.5, source=CAPTURED)
        report = build_report([seen])
        self.assertEqual(report.graded, 1)
        self.assertEqual(report.unverified, 0)
        self.assertAlmostEqual(report.mean_line_clv, 1.0, places=9)
        self.assertNotIn("EXCLUDED", report.summary())

    def test_a_real_fill_grades_because_a_ticket_recorded_it(self):
        from cfb_edge.clv import FILLED, LoggedBet, build_report

        held = LoggedBet(date="2026-09-14", away="A", home="B", side="B",
                         line_taken=-3.0, price_taken=-110.0, stake=0.01,
                         closing_line=-4.5, source=FILLED)
        self.assertTrue(held.gradeable)
        self.assertEqual(build_report([held]).graded, 1)

    def test_an_unverified_bet_still_counts_its_money(self):
        """Provenance gates the measurement, never the profit and loss.

        A bet placed at a number somebody typed still won or lost real money.
        Dropping it from the P&L would be a second lie told to correct a first.
        """
        from cfb_edge.clv import LoggedBet, build_report

        typed = LoggedBet(date="2026-09-14", away="A", home="B", side="B",
                          line_taken=-3.0, price_taken=-110.0, stake=1.0,
                          closing_line=-4.5, result="win")
        report = build_report([typed])
        self.assertEqual(report.graded, 0)
        self.assertEqual(report.staked, 1.0)
        self.assertGreater(report.realised_profit, 0.0)

    def test_a_legacy_row_with_no_source_column_reads_as_unverified(self):
        """Absence of a provenance is not a provenance.

        Every row written before this column existed came from a hand-assembled
        opens file, so defaulting the other way would silently bless exactly
        the rows that prompted the check.
        """
        import csv
        from cfb_edge.clv import UNVERIFIED, load_bets

        path = self._tmp()
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "away", "home", "side", "line_taken",
                        "price_taken", "stake", "closing_line"])
            w.writerow(["2026-09-14", "A", "B", "B", "-3.0", "-110.0", "0.0", "-4.5"])
        bet = load_bets(path)[0]
        self.assertEqual(bet.source, UNVERIFIED)
        self.assertFalse(bet.gradeable)

    def test_the_capture_stamps_every_line_it_writes(self):
        import csv
        from cfb_edge.clv import CAPTURED
        from cfb_edge.watch import OpeningBook, Quote

        book = OpeningBook(path=Path(self._tmp()))
        book.record([Quote(game="A @ B", market="spread", line=-6.5,
                           price=-110.0, book="kalshi",
                           seen_at="2026-09-14T00:00:00+00:00")])
        path = self._tmp()
        book.write_opens_csv(path)
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["source"], CAPTURED)

    def test_a_hand_written_opens_file_does_not_grade(self):
        """The end-to-end version of the defect, through the real commands."""
        import csv as _csv
        from cfb_edge.clv import build_report, load_bets
        from cfb_edge.paper import grade, record, signals_for

        opens_path = self._tmp()
        with open(opens_path, "w", newline="", encoding="utf-8") as fh:
            w = _csv.writer(fh)
            w.writerow(["game", "opening_line"])   # no source column
            w.writerow(["Michigan State @ Notre Dame", "-29.5"])

        raw, sources = {}, {}
        with open(opens_path, newline="", encoding="utf-8") as fh:
            for row in _csv.DictReader(fh):
                raw[row["game"]] = float(row["opening_line"])
                sources[row["game"]] = (row.get("source") or "").strip() or "unverified"

        log = self._tmp()
        record(log, signals_for({"Michigan State @ Notre Dame": 15.32}, raw,
                                date="2026-09-14", week=3, sources=sources))
        grade(log, {"Michigan State @ Notre Dame": -14.0})
        report = build_report(load_bets(log))
        self.assertEqual(report.graded, 0)
        self.assertEqual(report.unverified, 1)


class TestKickoffGuard(unittest.TestCase):
    """A quote taken while a game is being played is not a close.

    The capture runs Sunday into Tuesday and games kick off inside that
    window, so this is the routine case rather than the exceptional one. CLV
    measured against an in-play line is noise recorded as signal, and it would
    corrupt the scorecard quietly.
    """

    def _log(self, quotes_per_poll):
        import gzip, json, os, tempfile
        fd, path = tempfile.mkstemp(suffix=".jsonl.gz"); os.close(fd)
        self.addCleanup(os.unlink, path)
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            for quotes in quotes_per_poll:
                fh.write(json.dumps({"polled_at": "x", "quotes": quotes}) + "\n")
        return path

    def _q(self, line, seen, kick="2026-09-12T23:00:00Z", game="A @ B"):
        return {"game": game, "book": "bk", "market": "spread", "line": line,
                "price": -110, "seen_at": seen, "commence_time": kick}

    def test_the_close_is_the_last_price_before_kickoff(self):
        from cfb_edge.watch import OpeningBook

        path = self._log([
            [self._q(6.5, "2026-09-10T12:00:00Z")],   # Thursday
            [self._q(5.5, "2026-09-12T22:00:00Z")],   # an hour before kickoff
            [self._q(-14.0, "2026-09-12T23:30:00Z")],  # in play, must be ignored
            [self._q(-21.0, "2026-09-13T02:00:00Z")],  # after the final whistle
        ])
        book = OpeningBook.load(path)
        self.assertEqual(book.consensus_opens()["A @ B"], 6.5)
        self.assertEqual(book.consensus_closes()["A @ B"], 5.5)

    def test_an_ungated_quote_is_kept_and_reported(self):
        """Old logs carry no kickoff time. Dropping them would silently empty
        the log rather than admit the close cannot be gated."""
        from cfb_edge.watch import OpeningBook

        path = self._log([[{"game": "A @ B", "book": "bk", "market": "spread",
                            "line": 6.5, "price": -110, "seen_at": "t0"}],
                          [{"game": "A @ B", "book": "bk", "market": "spread",
                            "line": 4.0, "price": -110, "seen_at": "t1"}]])
        book = OpeningBook.load(path)
        self.assertEqual(book.consensus_closes()["A @ B"], 4.0)
        self.assertEqual(book.ungated_games(), {"A @ B"})

    def test_a_gated_game_is_not_reported_as_ungated(self):
        from cfb_edge.watch import OpeningBook

        path = self._log([[self._q(6.5, "2026-09-10T12:00:00Z")]])
        self.assertEqual(OpeningBook.load(path).ungated_games(), set())

    def test_the_guard_survives_a_live_capture_too(self):
        from cfb_edge.watch import OpeningBook, Quote

        path = self._log([[self._q(6.5, "2026-09-10T12:00:00Z")]])
        book = OpeningBook.load(path)
        book.record([Quote("A @ B", "bk", "spread", 3.0, -110,
                           "2026-09-12T22:30:00Z", "2026-09-12T23:00:00Z")])
        self.assertEqual(book.consensus_closes()["A @ B"], 3.0)
        book.record([Quote("A @ B", "bk", "spread", -30.0, -110,
                           "2026-09-13T00:00:00Z", "2026-09-12T23:00:00Z")])
        self.assertEqual(book.consensus_closes()["A @ B"], 3.0)

    def test_the_provider_carries_kickoff_onto_every_quote(self):
        """The gate is decorative unless the provider populates the field."""
        from cfb_edge.providers.oddsapi import parse_board

        quotes = parse_board([{
            "home_team": "Kansas", "away_team": "Missouri",
            "commence_time": "2026-09-12T23:00:00Z",
            "bookmakers": [{"title": "bk", "markets": [{"key": "spreads",
                "outcomes": [{"name": "Kansas", "point": 6.5, "price": -110}]}]}],
        }], seen_at="2026-09-10T12:00:00Z")
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].commence_time, "2026-09-12T23:00:00Z")
        self.assertIs(quotes[0].before_kickoff, True)

    def test_replaying_an_archived_payload_keeps_its_own_timestamp(self):
        """Without this the open is whenever you happened to re-parse.

        This test also exists because its predecessor passed for four days and
        then failed: it asserted before_kickoff was True against a fixture
        kickoff of 2026-09-12, using the real clock as seen_at. On 2026-09-13
        that kickoff was in the past and the assertion inverted. A test whose
        result depends on the date it runs is not a test.
        """
        from cfb_edge.providers.oddsapi import parse_board

        payload = [{
            "home_team": "Kansas", "away_team": "Missouri",
            "commence_time": "2020-09-12T23:00:00Z",
            "bookmakers": [{"title": "bk", "markets": [{"key": "spreads",
                "outcomes": [{"name": "Kansas", "point": 6.5, "price": -110}]}]}],
        }]
        archived = parse_board(payload, seen_at="2020-09-06T18:00:00Z")
        self.assertEqual(archived[0].seen_at, "2020-09-06T18:00:00Z")
        self.assertIs(archived[0].before_kickoff, True)

        # The same payload with no seen_at is stamped now, which for a 2020
        # kickoff means the quote reads as taken after the game finished.
        live = parse_board(payload)
        self.assertNotEqual(live[0].seen_at, archived[0].seen_at)
        self.assertIs(live[0].before_kickoff, False)

    def test_a_kalshi_ladder_inverts_to_the_line(self):
        """The ladder is a survival curve, so the line falls out of it.

        This is what lets the Odds API go. The old chain recorded a book's
        opening line, bet a Kalshi contract and scored against a book's close:
        two of three steps on a venue the operator never trades.
        """
        from cfb_edge.providers.kalshi import implied_line, survival_curve

        ladder = [
            {"yes_sub_title": "Texas A&M wins by over 3.5 points",
             "yes_bid": 88, "yes_ask": 90},
            {"yes_sub_title": "Texas A&M wins by over 16.5 points",
             "yes_bid": 49, "yes_ask": 51},
            {"yes_sub_title": "Kentucky wins by over 2.5 points",
             "yes_bid": 6, "yes_ask": 8},
        ]
        curve = survival_curve(ladder, home="Texas A&M", away="Kentucky")
        # An away rung at 2.5 is a home rung at -2.5 with the price complemented.
        self.assertAlmostEqual(curve[-2.5], 0.93, places=6)
        self.assertAlmostEqual(curve[16.5], 0.50, places=6)
        self.assertAlmostEqual(implied_line(curve), -16.5, places=2)

    def test_a_ladder_that_never_crosses_a_coin_flip_yields_no_line(self):
        """Extrapolating off the end of a ladder invents a number."""
        from cfb_edge.providers.kalshi import implied_line, survival_curve

        # Every rung deep in the money: the ladder does not reach the middle.
        one_sided = [
            {"yes_sub_title": "Georgia wins by over 30.5 points",
             "yes_bid": 10, "yes_ask": 12},
            {"yes_sub_title": "Georgia wins by over 40.5 points",
             "yes_bid": 3, "yes_ask": 5},
        ]
        curve = survival_curve(one_sided, home="Arkansas", away="Georgia")
        self.assertIsNone(implied_line(curve))
        # And a single rung is not a curve.
        self.assertIsNone(implied_line({5.0: 0.4}))

    def test_a_one_sided_quote_is_not_read_as_a_price(self):
        """A rung with no bid is an aspiration, not a market."""
        from cfb_edge.providers.kalshi import survival_curve

        self.assertEqual(
            survival_curve(
                [{"yes_sub_title": "Auburn wins by over 2.5 points",
                  "yes_bid": 0, "yes_ask": 47}],
                home="Auburn", away="Florida"),
            {})

    def test_the_kalshi_board_becomes_quotes_the_capture_already_eats(self):
        """New source, same instrument: watch, grade and clv do not change."""
        import json

        from cfb_edge.providers.kalshi import board_quotes

        page = {"markets": [
            {"event_ticker": "26SEP19KYTAM", "close_time": "2026-09-19T23:00:00Z",
             "yes_sub_title": "Texas A&M wins by over 3.5 points",
             "yes_bid": 88, "yes_ask": 90},
            {"event_ticker": "26SEP19KYTAM",
             "yes_sub_title": "Texas A&M wins by over 16.5 points",
             "yes_bid": 49, "yes_ask": 51},
            {"event_ticker": "26SEP19KYTAM",
             "yes_sub_title": "Kentucky wins by over 2.5 points",
             "yes_bid": 6, "yes_ask": 8},
            # A game whose ladder never crosses a coin flip is skipped, not
            # guessed at.
            {"event_ticker": "26SEP19GAARK",
             "yes_sub_title": "Georgia wins by over 30.5 points",
             "yes_bid": 10, "yes_ask": 12},
        ]}
        # The slate orients the board. Nothing in a Kalshi market says which
        # team is at home; inferring it from the event ticker gave an answer
        # that depended on set iteration order and flipped between machines.
        quotes = board_quotes(
            games=["Kentucky @ Texas A&M", "Georgia @ Arkansas"],
            opener=lambda url: json.dumps(page).encode(),
            seen_at="2026-09-14T22:00:00Z")

        self.assertEqual(len(quotes), 1)
        q = quotes[0]
        self.assertEqual(q.game, "Kentucky @ Texas A&M")
        self.assertEqual(q.book, "kalshi")
        self.assertEqual(q.line, -16.5)
        self.assertEqual(q.seen_at, "2026-09-14T22:00:00Z")
        # The kickoff gate needs this, and it comes free from close_time.
        self.assertIs(q.before_kickoff, True)

        # Orientation comes from the slate and nowhere else. Reverse the
        # fixture and the line flips sign; drop it and the game is skipped.
        flipped = board_quotes(
            games=["Texas A&M @ Kentucky"],
            opener=lambda url: json.dumps(page).encode(),
            seen_at="2026-09-14T22:00:00Z")
        self.assertEqual(flipped[0].game, "Texas A&M @ Kentucky")
        self.assertEqual(flipped[0].line, 16.5)

        self.assertEqual(
            board_quotes(games=[],
                         opener=lambda url: json.dumps(page).encode()), [])

    def test_a_strike_the_venue_does_not_list_is_never_quoted(self):
        """The defect that made the card unfillable.

        `find_plays` optimised over a fixed ladder and never asked the exchange
        what it sells. On a game the market had at -16.5 it quoted "Texas A&M
        at +3" while the only listed strike was the line itself, where the edge
        is negative. The operator filled what existed.
        """
        from cfb_edge.strategy import Venue, find_plays

        v = [Venue("exchange", is_exchange=True)]
        args = dict(market_line=-16.5, side="Texas A&M", venues=v)

        # The old behaviour, kept for callers that have no ladder.
        free = find_plays("Kentucky @ Texas A&M", **args)
        self.assertTrue(free)
        self.assertIn(free[0].number, (3, 7, 10, 14))

        # Given the ladder, nothing outside it may be quoted.
        listed = [16, 20, 24]
        got = find_plays("Kentucky @ Texas A&M", listed_strikes=listed, **args)
        for play in got:
            self.assertIn(play.number, listed)

        # And the ladder Kalshi actually showed clears nothing at all.
        self.assertEqual(
            find_plays("Kentucky @ Texas A&M", listed_strikes=[16], **args), [])

    def test_a_signal_with_nothing_fillable_is_reported_not_dropped(self):
        """Silence here is what made the card look uniformly actionable."""
        from cfb_edge.orders import orders_for, summarise

        slate = {"Kentucky @ Texas A&M": 24.23}
        opens = {"Kentucky @ Texas A&M": -16.5}

        # The venue lists only the line.
        thin = orders_for(slate, opens, lister=lambda g, s: [16], week=3)
        self.assertEqual(len(thin), 1)
        self.assertFalse(thin[0].placeable)
        self.assertIn("clear the fee", thin[0].reason)
        self.assertIn("No placeable bet", summarise(thin))

        # The venue lists nothing at all.
        none = orders_for(slate, opens, lister=lambda g, s: [], week=3)
        self.assertIn("lists no strike", none[0].reason)

        # A ladder that reaches a key number does produce a fillable order.
        rich = orders_for(slate, opens, lister=lambda g, s: [3, 7, 16], week=3,
                          bankroll=10_000.0)
        self.assertTrue(rich[0].placeable)
        self.assertIn(rich[0].strike, (3, 7))
        self.assertGreater(rich[0].net_edge, 0.0)
        self.assertGreater(rich[0].contracts, 0)
        # Contracts are floored, so the fill can never exceed the stake.
        self.assertLessEqual(
            rich[0].contracts * rich[0].limit_price, rich[0].dollars + 1e-9)

    def test_the_week_gate_still_applies_before_the_venue_is_asked(self):
        """Week 2 must not even reach the exchange."""
        from cfb_edge.orders import orders_for

        slate = {"Kentucky @ Texas A&M": 24.23}
        opens = {"Kentucky @ Texas A&M": -16.5}
        asked = []

        def lister(game, side):
            asked.append(game)
            return [3, 7]

        self.assertEqual(orders_for(slate, opens, lister=lister, week=2), [])
        self.assertEqual(asked, [])

    def test_an_impossible_price_is_refused_when_it_is_typed(self):
        """The bet log is the one file that cannot be recomputed.

        `market.american_to_decimal` has always rejected a price inside +-100,
        but nothing called it on the way in. So `log --price 66` wrote cleanly,
        read back cleanly while the bet was unsettled, and only raised weeks
        later when `settle` or `clv` tried to compute a payout, by which point
        the price actually received is gone. 66 is what someone types when the
        card quoted them 66 cents on an exchange contract.
        """
        import os
        import subprocess
        import sys
        import tempfile

        d = tempfile.mkdtemp()
        log = os.path.join(d, "bets.jsonl")

        def place(price_flag, value):
            return subprocess.run(
                [sys.executable, "-m", "cfb_edge", "log", "--bets", log,
                 "--game", "A @ B", "--side", "B", "--line", "3",
                 price_flag, str(value), "--stake", "0.01"],
                capture_output=True, text=True)

        for bad in (66, 0, 99, -99, 1):
            r = place("--price", bad)
            self.assertNotEqual(r.returncode, 0, f"--price {bad} was accepted")
            self.assertFalse(os.path.exists(log),
                             f"--price {bad} reached the log")
        # The cents hint fires only for something that looks like cents.
        self.assertIn("--cents 66", place("--price", 66).stderr
                      + place("--price", 66).stdout)

        for ok in (-110, 100, -100, 150):
            r = place("--price", ok)
            self.assertEqual(r.returncode, 0, f"--price {ok} was refused")
            os.unlink(log)

    def test_the_release_window_is_the_same_instant_in_every_timezone(self):
        """The schedule is defined in UTC and was read off wall-clock fields.

        `.weekday()` and `.hour` are wall-clock attributes, not instants, so an
        aware datetime in another zone used to be taken at face value. Sunday
        18:30-04:00 is 22:30 UTC and inside the release window, and it was
        answered as a quiet Sunday evening: a caller on US Eastern passing local
        time polled hourly straight through the window, which is exactly the
        failure this module exists to prevent.
        """
        from datetime import datetime, timedelta, timezone

        from cfb_edge.watch import (DENSE_INTERVAL_SECONDS, in_release_window,
                                    poll_interval)

        instant = datetime(2026, 9, 13, 22, 30, tzinfo=timezone.utc)
        for offset in (0, -4, -7, 2, 9, 13, -11):
            local = instant.astimezone(timezone(timedelta(hours=offset)))
            self.assertTrue(in_release_window(local), f"UTC{offset:+d}")
            self.assertEqual(poll_interval(local), DENSE_INTERVAL_SECONDS,
                             f"UTC{offset:+d}")

        # And an instant outside the window stays outside it, read from
        # anywhere. Saturday afternoon: games are still being played.
        quiet = datetime(2026, 9, 12, 19, 0, tzinfo=timezone.utc)
        for offset in (0, -4, -7, 2, 9):
            local = quiet.astimezone(timezone(timedelta(hours=offset)))
            self.assertFalse(in_release_window(local), f"UTC{offset:+d}")

    def test_a_naive_datetime_is_read_as_utc(self):
        """Stated rather than left to whatever attribute access happened to do."""
        from datetime import datetime, timezone

        from cfb_edge.watch import in_postseason_window, in_release_window

        self.assertTrue(in_release_window(datetime(2026, 9, 13, 22, 30)))
        self.assertFalse(in_release_window(datetime(2026, 9, 13, 20, 30)))
        self.assertEqual(
            in_release_window(datetime(2026, 9, 13, 22, 30)),
            in_release_window(datetime(2026, 9, 13, 22, 30,
                                       tzinfo=timezone.utc)))
        self.assertTrue(in_postseason_window(datetime(2026, 12, 20)))
        self.assertFalse(in_postseason_window(datetime(2026, 9, 13)))

    def test_an_unusable_seen_at_is_refused_rather_than_absorbed(self):
        """Both failures were silent, and both disabled the kickoff gate."""
        from datetime import datetime, timezone

        from cfb_edge.providers.oddsapi import parse_board

        payload = [{
            "home_team": "Kansas", "away_team": "Missouri",
            "commence_time": "2020-09-12T23:00:00Z",
            "bookmakers": [{"title": "bk", "markets": [{"key": "spreads",
                "outcomes": [{"name": "Kansas", "point": 6.5, "price": -110}]}]}],
        }]

        # "" used to take the `or` branch and get the current clock, which is
        # the exact substitution the argument exists to prevent.
        for bad in ("", "   ", "last tuesday", "2020-13-45"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_board(payload, seen_at=bad)

        # A datetime is the natural thing to reach for and is not a string.
        with self.assertRaises(ValueError):
            parse_board(payload, seen_at=datetime(2020, 9, 6, tzinfo=timezone.utc))

        # None still means "live poll", and a real offset still parses.
        self.assertEqual(len(parse_board(payload)), 1)
        off = parse_board(payload, seen_at="2020-09-06T14:00:00-04:00")
        self.assertIs(off[0].before_kickoff, True)

    def test_the_refusal_is_what_keeps_an_in_play_quote_out_of_the_close(self):
        """The bug was worth a test showing the damage, not just the raise.

        `Quote.before_kickoff` returns None when it cannot parse, and
        `OpeningBook._observe` counts None as usable so that logs predating the
        commence_time field still work. That tolerance is correct. It is also
        what made a malformed seen_at dangerous: every quote in the batch
        became eligible to be the close, including one taken mid-game, and the
        close moves toward whoever is winning.
        """
        import os
        import tempfile

        from cfb_edge.providers.oddsapi import parse_board
        from cfb_edge.watch import OpeningBook

        def board(line):
            return [{
                "home_team": "Kansas", "away_team": "Missouri",
                "commence_time": "2020-09-12T23:00:00Z",
                "bookmakers": [{"title": "bk", "markets": [{"key": "spreads",
                    "outcomes": [{"name": "Kansas", "point": line,
                                  "price": -110}]}]}],
            }]

        fd, path = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
        try:
            book = OpeningBook(path=Path(path))
            book.record(parse_board(board(6.5), seen_at="2020-09-06T18:00:00Z"))
            book.record(parse_board(board(5.5), seen_at="2020-09-12T22:30:00Z"))
            # Mid-game, Kansas being run over. Must not become the close.
            book.record(parse_board(board(-17.0),
                                    seen_at="2020-09-13T00:30:00Z"))

            self.assertEqual(book.consensus_opens()["Missouri @ Kansas"], 6.5)
            self.assertEqual(book.consensus_closes()["Missouri @ Kansas"], 5.5)
            self.assertEqual(book.ungated_games(), set())

            # And the batch that would have caused it cannot be built at all.
            with self.assertRaises(ValueError):
                parse_board(board(-17.0), seen_at="")
        finally:
            os.unlink(path)

    def test_a_provider_that_omits_kickoff_still_yields_quotes(self):
        from cfb_edge.providers.oddsapi import parse_board

        quotes = parse_board([{
            "home_team": "Kansas", "away_team": "Missouri",
            "bookmakers": [{"title": "bk", "markets": [{"key": "spreads",
                "outcomes": [{"name": "Kansas", "point": 6.5, "price": -110}]}]}],
        }])
        self.assertEqual(len(quotes), 1)
        self.assertIsNone(quotes[0].commence_time)
        self.assertIsNone(quotes[0].before_kickoff)

    def test_a_naive_timestamp_is_read_as_utc_not_rejected(self):
        from cfb_edge.watch import Quote

        self.assertIs(Quote("A @ B", "b", "spread", 3.0, -110,
                            "2026-09-12T22:00:00", "2026-09-12T23:00:00Z"
                            ).before_kickoff, True)
        self.assertIs(Quote("A @ B", "b", "spread", 3.0, -110,
                            "nonsense", "2026-09-12T23:00:00Z"
                            ).before_kickoff, None)


class TestStopRule(unittest.TestCase):
    """A rule fixed in advance, and the honest limits of what it can decide."""

    def test_the_boundaries_are_the_sprt_they_claim_to_be(self):
        import math
        from cfb_edge.stopping import (ALPHA, BETA, CLAIMED_CLV, CLV_SD_PER_BET,
                                       confirm_threshold, kill_threshold)

        v = CLV_SD_PER_BET ** 2
        for n in (1, 26, 100, 400):
            self.assertAlmostEqual(
                confirm_threshold(n),
                math.log((1 - BETA) / ALPHA) * v / CLAIMED_CLV + CLAIMED_CLV / 2 * n,
                places=9)
            self.assertAlmostEqual(
                kill_threshold(n),
                math.log(BETA / (1 - ALPHA)) * v / CLAIMED_CLV + CLAIMED_CLV / 2 * n,
                places=9)
            self.assertLess(kill_threshold(n), confirm_threshold(n))

    def test_the_error_rates_match_the_design(self):
        """The boundaries are only worth trusting if simulation agrees."""
        import random
        from cfb_edge.stopping import CLV_SD_PER_BET, confirm_threshold, kill_threshold

        def run(mu, trials=4000, cap=400):
            rng = random.Random(11)
            dead = alive = 0
            for _ in range(trials):
                s = 0.0
                for n in range(1, cap + 1):
                    s += rng.gauss(mu, CLV_SD_PER_BET)
                    if s <= kill_threshold(n): dead += 1; break
                    if s >= confirm_threshold(n): alive += 1; break
            return dead / trials, alive / trials

        dead_when_dead, alive_when_dead = run(0.0)
        self.assertGreater(dead_when_dead, 0.85)
        self.assertLess(alive_when_dead, 0.08)          # design alpha 0.05

        dead_when_alive, alive_when_alive = run(0.44)
        self.assertGreater(alive_when_alive, 0.70)      # design 1 - beta = 0.80
        self.assertLess(dead_when_alive, 0.25)

    def test_a_single_season_can_kill_but_cannot_confirm(self):
        """26 bets is a tripwire, not a verdict. Documented so nobody reads a
        quiet season as evidence the edge is real."""
        from cfb_edge.stopping import CLAIMED_CLV, confirm_threshold

        needed_per_bet = confirm_threshold(26) / 26
        self.assertGreater(needed_per_bet, 4 * CLAIMED_CLV)

    def test_the_verdict_moves_between_the_three_states(self):
        from cfb_edge.stopping import evaluate, kill_threshold

        self.assertEqual(evaluate([]).decision, "no graded bets yet")
        self.assertEqual(evaluate([0.5] * 10).decision, "CONTINUE")
        self.assertEqual(evaluate([-3.0] * 26).decision, "STOP")
        self.assertEqual(evaluate([5.0] * 200).decision, "CONFIRMED")
        # Landing on the boundary stops: the rule is "at or below", and a sum
        # of floats reaches it only to within rounding, so the comparison
        # carries a tolerance far below the half point a line moves in.
        n = 30
        self.assertEqual(evaluate([kill_threshold(n) / n] * n).decision, "STOP")
        self.assertEqual(evaluate([kill_threshold(n) / n * 1.001] * n).decision,
                         "STOP")
        self.assertEqual(evaluate([kill_threshold(n) / n * 0.9] * n).decision,
                         "CONTINUE")

    def test_the_scorecard_prints_the_rule_without_being_asked(self):
        import io, contextlib, os, tempfile
        from cfb_edge.cli import main

        fd, path = tempfile.mkstemp(suffix=".csv"); os.close(fd)
        self.addCleanup(os.unlink, path)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("date,away,home,side,line_taken,price_taken,stake,"
                     "closing_line,closing_price,closing_opposite_price,result\n")
            for i in range(3):
                fh.write(f"2026-09-1{i},A,B,B,-3.0,-110,0.01,-4.5,,,win\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["clv", "--bets", path])
        out = buf.getvalue()
        self.assertIn("Stop rule after 3 graded bets", out)
        self.assertIn("CONTINUE", out)


class TestRealizedHold(unittest.TestCase):
    """Measure a book rather than believe it."""

    def test_a_standard_book_measures_as_minus_110(self):
        h = market.realized_hold([(-110, -110)] * 50)
        self.assertAlmostEqual(h.median_hold, 0.0476, places=4)
        self.assertAlmostEqual(h.equivalent_price, -110, places=0)

    def test_a_reduced_juice_book_measures_as_advertised(self):
        h = market.realized_hold([(-105, -105)] * 50)
        self.assertAlmostEqual(h.equivalent_price, -105, places=0)
        self.assertTrue(h.clears(-105))

    def test_a_book_that_only_sometimes_offers_it_is_caught(self):
        # Advertises -105, posts it on a fifth of markets. The median is what
        # you actually meet, and it is -110.
        quotes = [(-105, -105)] * 20 + [(-110, -110)] * 80
        h = market.realized_hold(quotes)
        self.assertAlmostEqual(h.equivalent_price, -110, places=0)
        self.assertFalse(h.clears(-105))

    def test_the_threshold_comparison_runs_the_right_way(self):
        cheap = market.realized_hold([(-103, -103)] * 10)
        dear = market.realized_hold([(-115, -115)] * 10)
        self.assertTrue(cheap.clears(-105))
        self.assertFalse(dear.clears(-105))

    def test_empty_input_is_refused(self):
        with self.assertRaises(ValueError):
            market.realized_hold([])


class TestVenue(unittest.TestCase):
    """Where to express a line edge, once you have one."""

    def test_the_best_strike_is_a_key_number(self):
        from cfb_edge.venue import best_strike, is_key_number

        for pm in (0.0, 3.0, 6.0, 9.0):
            b = best_strike(0.44, market_margin=pm)
            self.assertTrue(is_key_number(b.strike), f"projected {pm} -> {b.strike}")

    def test_tails_are_not_the_answer(self):
        # The fee falls in the tails, but so does the density, and they very
        # nearly cancel. A deep strike loses to a key number every time.
        # Bounds are widened so the deep strikes are actually in the ladder;
        # the module's default 5-95% band already excludes most of them.
        from cfb_edge.venue import rank_expressions

        ranked = {
            e.strike: e
            for e in rank_expressions(0.44, price_bounds=(0.01, 0.99))
            if e.venue == "exchange"
        }
        self.assertIn(28, ranked, "widened bounds should reach the deep strikes")
        self.assertGreater(ranked[3].net, ranked[28].net)
        self.assertGreater(ranked[7].net, ranked[24].net)

    def test_the_default_bounds_exclude_the_deepest_strikes(self):
        """Guards the bug this test originally had: 28 sits at 3.4% and is
        filtered out by the default band, so a test naming it needs wider
        bounds rather than a different expectation."""
        from cfb_edge.venue import rank_expressions

        strikes = {e.strike for e in rank_expressions(0.44) if e.venue == "exchange"}
        self.assertNotIn(28, strikes)
        self.assertIn(3, strikes)

    def test_a_book_wins_when_its_line_lands_on_a_key_number(self):
        # Projected at 10, a book posts 7, and its vig beats an exchange fee at
        # the same 50/50 proposition.
        from cfb_edge.venue import best_strike

        b = best_strike(0.44, market_margin=10.0)
        self.assertTrue(b.venue.startswith("book"))
        self.assertEqual(abs(b.strike), 7)

    def test_an_exchange_wins_when_the_book_line_is_an_ordinary_number(self):
        from cfb_edge.venue import best_strike

        b = best_strike(0.44, market_margin=0.0)
        self.assertEqual(b.venue, "exchange")

    def test_the_exchange_fee_peaks_at_a_coin_flip(self):
        from cfb_edge.venue import exchange_fee

        self.assertAlmostEqual(exchange_fee(0.5), 0.0175, places=6)
        for p in (0.1, 0.25, 0.75, 0.9):
            self.assertLess(exchange_fee(p), exchange_fee(0.5))

    def test_the_exchange_fee_always_undercuts_minus_110(self):
        from cfb_edge.venue import book_vig, exchange_fee

        self.assertLess(exchange_fee(0.5), book_vig(-110))

    def test_a_bigger_edge_is_profitable_in_more_places(self):
        from cfb_edge.venue import rank_expressions

        small = sum(1 for e in rank_expressions(0.20) if e.profitable)
        large = sum(1 for e in rank_expressions(1.00) if e.profitable)
        self.assertGreater(large, small)


class TestStrategy(unittest.TestCase):
    """The capstone: the one strategy the evidence supports."""

    def _venues(self):
        from cfb_edge.strategy import Venue

        return [Venue("book -105", american_price=-105),
                Venue("book -110", american_price=-110),
                Venue("exchange", is_exchange=True)]

    def test_a_minus_110_book_clears_only_barely_and_only_on_a_key_line(self):
        """An earlier version of this test claimed -110 never clears. It does,
        marginally, when the posted line is a key number sitting at the
        projected margin, which is where the density peaks. Away from that it
        does not clear at all, and even where it does the margin is a tenth of
        what a -105 book earns on the same bet."""
        from cfb_edge.strategy import find_plays

        # Line on a key number at the projection: clears, barely.
        on_peak = find_plays("g", market_line=-7.0, side="home",
                             venues=self._venues(), posted_line=-7.0)
        b110 = [p for p in on_peak if p.venue == "book -110"]
        b105 = [p for p in on_peak if p.venue == "book -105"]
        self.assertTrue(b110)
        self.assertLess(b110[0].net, 0.005)
        self.assertGreater(b105[0].net, b110[0].net * 4)

        # Line not a key number: no book play at all, at any price.
        off_key = find_plays("g", market_line=-0.0, side="home",
                             venues=self._venues(), posted_line=-1.5)
        self.assertFalse(any(p.venue.startswith("book") for p in off_key))

        # There is no such thing as a book posting a key number far from the
        # market, because the book's own number is the market. What is left is
        # the vig difference at the number itself, and it decides the venue.
        at_14 = find_plays("g", market_line=-14.0, side="home",
                           venues=self._venues(), posted_line=-14.0)
        n110 = [p for p in at_14 if p.venue == "book -110"]
        n105 = [p for p in at_14 if p.venue == "book -105"]
        self.assertTrue(n105)
        self.assertGreater(n105[0].net, max([p.net for p in n110], default=0.0))

    def test_a_book_only_appears_when_its_posted_line_is_a_key_number(self):
        from cfb_edge.strategy import find_plays

        on_key = find_plays("g", market_line=-10.0, side="home",
                            venues=self._venues(), posted_line=-7.0)
        self.assertTrue(any(p.venue == "book -105" for p in on_key))
        off_key = find_plays("g", market_line=-10.0, side="home",
                             venues=self._venues(), posted_line=-8.5)
        self.assertFalse(any(p.venue.startswith("book") for p in off_key))

    def test_a_reduced_juice_book_on_a_key_number_beats_the_exchange(self):
        from cfb_edge.strategy import find_plays

        plays = find_plays("g", market_line=-10.0, side="home",
                           venues=self._venues(), posted_line=-7.0)
        self.assertEqual(plays[0].venue, "book -105")

    def test_the_exchange_carries_the_board_away_from_key_lines(self):
        from cfb_edge.strategy import find_plays

        plays = find_plays("g", market_line=-1.0, side="home",
                           venues=self._venues(), posted_line=-1.5)
        self.assertTrue(plays)
        self.assertTrue(all(p.venue == "exchange" for p in plays))
        self.assertEqual(plays[0].number, 3)

    def test_a_smaller_edge_produces_no_card_at_all(self):
        from cfb_edge.strategy import find_plays

        plays = find_plays("g", market_line=-0.0, side="home",
                           venues=self._venues(), posted_line=-3.0,
                           clv_points=0.10)
        self.assertEqual(plays, [])

    def test_stakes_are_capped_per_bet(self):
        from cfb_edge.strategy import MAX_STAKE, find_plays

        plays = find_plays("g", market_line=-0.0, side="home",
                           venues=self._venues(), posted_line=-3.0,
                           clv_points=3.0)
        self.assertTrue(plays)
        for p in plays:
            self.assertLessEqual(p.stake, MAX_STAKE + 1e-12)

    def test_the_quoted_price_is_the_market_not_the_projection(self):
        """The bug this pins cost twelve cents on a one cent edge.

        Missouri at Kansas, week 2 of 2026: the model projected a pick'em, the
        market posted Kansas +6.5. Pricing the three off the projection quoted
        39c on a contract the market was offering near 26c. A card is an
        instruction to go and fill an order, so a price nobody is offering is
        not a rounding error, it is a losing bet.
        """
        from cfb_edge.distribution import margin_pmf, sigma_for_total
        from cfb_edge.strategy import find_plays

        plays = find_plays("Missouri @ Kansas", market_line=6.5, side="Kansas",
                           venues=self._venues())
        three = [p for p in plays if p.number == 3]
        self.assertTrue(three)

        pmf = margin_pmf(-6.5, sigma_for_total(52.0))
        self.assertAlmostEqual(three[0].strike_price,
                               sum(v for k, v in pmf.items() if k > 3), places=9)
        # And nowhere near what the projection alone would have said.
        model = margin_pmf(0.0, sigma_for_total(52.0))
        self.assertGreater(abs(three[0].strike_price
                               - sum(v for k, v in model.items() if k > 3)), 0.10)

    def test_line_movement_is_valued_where_the_line_actually_is(self):
        """Both terms come from the market, not just the price.

        A move of `clv_points` converts to probability at the density under it,
        and the line moves from where the market is rather than from where the
        model wishes it were. Kansas is the case where that lowers the edge and
        Michigan the case where it raises it, so neither direction is a
        convenient assumption.
        """
        from cfb_edge.distribution import margin_pmf, sigma_for_total
        from cfb_edge.strategy import DEFAULT_CLV_POINTS, find_plays

        for line, projection in ((6.5, 0.0), (-2.5, -2.67)):
            plays = find_plays("A @ B", market_line=line, side="B",
                               venues=self._venues())
            three = [p for p in plays if p.number == 3][0]
            market = margin_pmf(-line, sigma_for_total(52.0)).get(3, 0.0)
            model = margin_pmf(projection, sigma_for_total(52.0)).get(3, 0.0)
            self.assertAlmostEqual(three.gain, DEFAULT_CLV_POINTS * market, places=9)
            self.assertNotAlmostEqual(three.gain, DEFAULT_CLV_POINTS * model, places=4)

    def test_the_fee_rate_has_exactly_one_definition(self):
        """kalshi_fees says to read the real rate off the account.

        A number expected to change must not exist twice. Two copies agree
        right up until someone edits one of them, and the failure is silent:
        every play still prices, just against a fee nobody is charging.
        """
        from cfb_edge import kalshi_fees, venue

        self.assertIs(venue.FEE_COEFFICIENT, kalshi_fees.FEE_COEFFICIENT)
        for price in (0.10, 0.26, 0.50, 0.55, 0.90):
            self.assertAlmostEqual(
                venue.exchange_fee(price),
                kalshi_fees.fee_cents_per_contract(price) / 100.0, places=12)

    def test_the_card_caps_exposure_with_the_staking_rule(self):
        """One implementation of the cap, not two that happen to match."""
        from cfb_edge.staking import apply_portfolio_cap
        from cfb_edge.strategy import Venue, build_card, find_plays

        per_game = [find_plays(f"g{i}", market_line=-3.0, side="home",
                               venues=[Venue("exchange", is_exchange=True)],
                               clv_points=3.0) for i in range(20)]
        card = build_card(per_game, max_weekly_exposure=0.07)
        raw = [max(ps, key=lambda p: p.net).stake for ps in per_game if ps]
        raw.sort(reverse=True)
        want = apply_portfolio_cap(raw, max_total=0.07)
        self.assertAlmostEqual(sum(p.stake for p in card), sum(want), places=12)
        self.assertLessEqual(sum(p.stake for p in card), 0.07 + 1e-12)

    def test_the_cli_defaults_come_from_the_modules_that_own_them(self):
        """The third instance of one constant living in two places.

        `--hfa` defaulted to 2.2 long after the fitted value moved to 3.2, so
        every `rate` run used a home field a full point too low while
        `ratings.DEFAULT_HFA` sat next door with the right number. Nothing
        failed and nothing looked wrong; the output just quietly said 2.20.
        """
        from cfb_edge.cli import build_parser
        from cfb_edge.ratings import DEFAULT_HFA
        from cfb_edge.staking import DEFAULT_MAX_WEEKLY_EXPOSURE
        from cfb_edge.strategy import DEFAULT_CLV_POINTS

        rate = build_parser().parse_args(["rate", "--games", "x.csv"])
        self.assertEqual(rate.hfa, DEFAULT_HFA)

        play = build_parser().parse_args(["play", "--slate", "x.csv"])
        self.assertEqual(play.clv, DEFAULT_CLV_POINTS)
        self.assertEqual(play.max_exposure, DEFAULT_MAX_WEEKLY_EXPOSURE)

    def test_the_card_takes_one_play_per_game(self):
        from cfb_edge.strategy import build_card, find_plays

        per_game = [
            find_plays(f"g{i}", market_line=-float(i), side="home",
                       venues=self._venues(), posted_line=-1.5)
            for i in range(4)
        ]
        card = build_card(per_game)
        self.assertEqual(len(card), len({p.game for p in card}))
        self.assertLessEqual(len(card), 4)

    def test_the_weekly_exposure_cap_scales_proportionally(self):
        from cfb_edge.strategy import build_card, find_plays

        per_game = [
            find_plays(f"g{i}", market_line=-3.0, side="home",
                       venues=self._venues(), posted_line=-3.0, clv_points=3.0)
            for i in range(20)
        ]
        card = build_card(per_game, max_weekly_exposure=0.10)
        self.assertAlmostEqual(sum(p.stake for p in card), 0.10, places=9)

    def test_only_real_key_numbers_are_tradeable(self):
        from cfb_edge.strategy import is_tradeable

        self.assertTrue(is_tradeable(3))
        self.assertTrue(is_tradeable(7))
        self.assertFalse(is_tradeable(9))
        self.assertFalse(is_tradeable(12))

    def test_a_venue_with_neither_price_nor_flag_is_refused(self):
        from cfb_edge.strategy import Venue

        with self.assertRaises(ValueError):
            Venue("broken").cost(0.5)

    def test_a_missing_side_is_refused(self):
        """A card built from games with no signal looks authoritative and
        contains nothing, which is the worst output this module could make."""
        from cfb_edge.strategy import find_plays

        for blank in ("", "   ", None):
            with self.assertRaises(ValueError):
                find_plays("g", market_line=-0.0, side=blank,
                           venues=self._venues(), posted_line=-3.0)

    def test_the_side_is_derived_from_the_open_alone(self):
        """The signal needs one market number, not two. What the line does
        after the open is what is being predicted, not an input."""
        from cfb_edge.strategy import signal_side

        # Model wants home laying 10; the open only asks 3. Home is underpriced.
        side, gap = signal_side("Home", "Away", projected_margin=10.0,
                                opening_home_line=-3.0)
        self.assertEqual(side, "Home")
        self.assertLess(gap, 0)

        # Model wants home laying 3; the open asks 10. Away is underpriced.
        side, gap = signal_side("Home", "Away", projected_margin=3.0,
                                opening_home_line=-10.0)
        self.assertEqual(side, "Away")
        self.assertGreater(gap, 0)

    def test_a_small_disagreement_produces_no_signal(self):
        from cfb_edge.strategy import signal_side

        side, gap = signal_side("Home", "Away", projected_margin=7.0,
                                opening_home_line=-6.0)
        self.assertIsNone(side)
        self.assertLess(abs(gap), 4.0)

    def test_the_disagreement_threshold_is_adjustable(self):
        from cfb_edge.strategy import signal_side

        kw = dict(projected_margin=7.0, opening_home_line=-5.0)
        self.assertIsNone(signal_side("H", "A", **kw)[0])
        self.assertIsNotNone(
            signal_side("H", "A", min_disagreement=1.0, **kw)[0])

    def test_the_quoted_price_is_the_side_being_backed(self):
        """An away bet pays the complement of the home side's price. Quoting
        the home number would send you to the wrong side of the book."""
        from cfb_edge.strategy import Venue, find_plays

        ex = [Venue("exchange", is_exchange=True)]
        home = find_plays("Away @ Home", market_line=-9.9, side="Home",
                          venues=ex)[0]
        away = find_plays("Away @ Home", market_line=-9.9, side="Away",
                          venues=ex)[0]
        self.assertAlmostEqual(home.strike_price + away.strike_price, 1.0, places=9)
        self.assertLess(away.strike_price, 0.5)
        self.assertGreater(home.strike_price, 0.5)


class TestOpeningCapture(unittest.TestCase):
    """First-seen is the open, and nothing may overwrite it."""

    def _quotes(self, line, seen="t1"):
        from cfb_edge.watch import Quote

        return [Quote("A @ B", "BookOne", "spread", line, -110, seen)]

    def test_the_first_price_wins_permanently(self):
        import tempfile, os
        from cfb_edge.watch import OpeningBook

        fd, path = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
        try:
            book = OpeningBook(path=Path(path))
            self.assertEqual(len(book.record(self._quotes(-2.5))), 1)
            # The line moves nine points. The open must not follow it.
            self.assertEqual(len(book.record(self._quotes(6.5, "t2"))), 0)
            self.assertEqual(book.opens[("A @ B", "BookOne", "spread")].line, -2.5)
        finally:
            os.unlink(path)

    def test_the_open_survives_a_reload_from_raw(self):
        import tempfile, os
        from cfb_edge.watch import OpeningBook

        fd, path = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
        try:
            book = OpeningBook(path=Path(path))
            book.record(self._quotes(-2.5))
            book.record(self._quotes(6.5, "t2"))
            reloaded = OpeningBook.load(path)
            self.assertEqual(
                reloaded.opens[("A @ B", "BookOne", "spread")].line, -2.5)
        finally:
            os.unlink(path)

    def test_consensus_takes_the_median_across_books(self):
        import tempfile, os
        from cfb_edge.watch import OpeningBook, Quote

        fd, path = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
        try:
            book = OpeningBook(path=Path(path))
            book.record([Quote("A @ B", f"Book{i}", "spread", ln, -110, "t")
                         for i, ln in enumerate((-3.0, -3.5, -14.0))])
            # The outlier must not define the open.
            self.assertEqual(book.consensus_opens()["A @ B"], -3.5)
        finally:
            os.unlink(path)

    def test_polling_is_dense_only_in_the_release_window(self):
        from datetime import datetime, timezone
        from cfb_edge.watch import DENSE_INTERVAL_SECONDS, SPARSE_INTERVAL_SECONDS, poll_interval

        sunday_evening = datetime(2026, 9, 13, 22, tzinfo=timezone.utc)
        friday_noon = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
        self.assertEqual(poll_interval(sunday_evening), DENSE_INTERVAL_SECONDS)
        self.assertEqual(poll_interval(friday_noon), SPARSE_INTERVAL_SECONDS)

    def test_bowl_season_polls_between_dense_and_sparse(self):
        from datetime import datetime, timezone
        from cfb_edge.watch import (DENSE_INTERVAL_SECONDS, POSTSEASON_INTERVAL_SECONDS,
                                    SPARSE_INTERVAL_SECONDS, poll_interval)

        # A Friday in bowl season is outside every weekly release window, but
        # bowl numbers post on no weekly rhythm at all.
        bowl_friday = datetime(2026, 12, 18, 12, tzinfo=timezone.utc)
        self.assertEqual(poll_interval(bowl_friday), POSTSEASON_INTERVAL_SECONDS)
        self.assertLess(POSTSEASON_INTERVAL_SECONDS, SPARSE_INTERVAL_SECONDS)
        self.assertGreater(POSTSEASON_INTERVAL_SECONDS, DENSE_INTERVAL_SECONDS)

        # January playoff dates are in; February is not.
        self.assertEqual(poll_interval(datetime(2027, 1, 8, 12, tzinfo=timezone.utc)),
                         POSTSEASON_INTERVAL_SECONDS)
        self.assertEqual(poll_interval(datetime(2027, 2, 5, 12, tzinfo=timezone.utc)),
                         SPARSE_INTERVAL_SECONDS)

        # Championship-week Sunday is still a regular release, and the weekly
        # window has to win there rather than being coarsened by the calendar.
        self.assertEqual(poll_interval(datetime(2026, 12, 6, 22, tzinfo=timezone.utc)),
                         DENSE_INTERVAL_SECONDS)

    def test_a_poll_that_opened_something_tightens_the_next_one(self):
        from datetime import datetime, timezone
        from cfb_edge.watch import DENSE_INTERVAL_SECONDS, poll_interval

        # The calendar is a prior; a first-seen price is evidence. Every hour
        # of the year goes dense once the board is demonstrably opening.
        for when in (datetime(2026, 9, 11, 12, tzinfo=timezone.utc),   # regular Friday
                     datetime(2026, 12, 18, 12, tzinfo=timezone.utc),  # bowl season
                     datetime(2027, 2, 5, 12, tzinfo=timezone.utc)):   # offseason
            self.assertEqual(poll_interval(when, opened_last_poll=3),
                             DENSE_INTERVAL_SECONDS)

    def test_the_watch_loop_feeds_new_opens_back_into_its_own_interval(self):
        import tempfile, os
        from datetime import datetime, timezone
        from cfb_edge.watch import (DENSE_INTERVAL_SECONDS, OpeningBook, Quote,
                                    SPARSE_INTERVAL_SECONDS, watch)

        fd, path = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
        try:
            book = OpeningBook.load(path)
            boards = [
                [Quote("A @ B", "bk", "spread", -3.0, -110, "t0")],   # opens: dense next
                [Quote("A @ B", "bk", "spread", -3.0, -110, "t1")],   # nothing new
            ]
            slept: list[float] = []
            watch(book, lambda: boards[min(len(slept), len(boards) - 1)],
                  max_polls=3, sleep=slept.append,
                  now=lambda: datetime(2026, 6, 5, 12, tzinfo=timezone.utc))
            # First poll opened a market, so the wait after it is dense. The
            # second saw nothing new on a June Friday, so it falls back to
            # sparse. A Monday would not test this: Mondays are always dense.
            self.assertEqual(slept[0], DENSE_INTERVAL_SECONDS)
            self.assertEqual(slept[1], SPARSE_INTERVAL_SECONDS)
        finally:
            os.unlink(path)

    def test_a_failing_poll_does_not_end_the_capture(self):
        import tempfile, os
        from cfb_edge.watch import OpeningBook, watch

        fd, path = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("network blip")
            return self._quotes(-3.0)

        try:
            book = OpeningBook(path=Path(path))
            watch(book, flaky, max_polls=2, sleep=lambda _s: None)
            self.assertEqual(calls["n"], 2)
            self.assertIn(("A @ B", "BookOne", "spread"), book.opens)
        finally:
            os.unlink(path)

    def test_only_the_home_side_is_stored(self):
        from cfb_edge.providers.oddsapi import parse_board

        payload = [{"home_team": "Kansas", "away_team": "Missouri", "bookmakers": [
            {"title": "Pinnacle", "markets": [{"key": "spreads", "outcomes": [
                {"name": "Kansas", "point": 6.5, "price": -105},
                {"name": "Missouri", "point": -6.5, "price": -105}]}]}]}]
        quotes = parse_board(payload)
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].line, 6.5)
        self.assertEqual(quotes[0].game, "Missouri @ Kansas")

    def test_a_missing_key_fails_loudly(self):
        import os
        from cfb_edge.providers.oddsapi import OddsApiUnreachable, fetch_board

        saved = os.environ.pop("ODDS_API_KEY", None)
        try:
            with self.assertRaises(OddsApiUnreachable):
                fetch_board()
        finally:
            if saved is not None:
                os.environ["ODDS_API_KEY"] = saved


class TestTeamAliases(unittest.TestCase):
    """Names must resolve exactly or not at all."""

    KNOWN = {"Miami", "Miami (OH)", "Ole Miss", "Mississippi State", "Louisiana",
             "UL Monroe", "Louisiana Tech", "Hawai'i", "San José State",
             "Texas A&M", "BYU", "UConn", "UCF", "NC State", "Southern Miss"}

    def test_the_two_miamis_never_cross(self):
        """The mistake that bets a different school in a different state."""
        from cfb_edge.teams import resolve

        for name in ("Miami (FL)", "Miami Hurricanes", "Miami Florida", "Miami"):
            self.assertEqual(resolve(name, self.KNOWN), "Miami")
        for name in ("Miami (OH)", "Miami RedHawks", "Miami Ohio"):
            self.assertEqual(resolve(name, self.KNOWN), "Miami (OH)")

    def test_mississippi_is_ole_miss_and_not_mississippi_state(self):
        from cfb_edge.teams import resolve

        self.assertEqual(resolve("Mississippi", self.KNOWN), "Ole Miss")
        self.assertEqual(resolve("Mississippi State", self.KNOWN), "Mississippi State")
        self.assertEqual(resolve("Southern Mississippi", self.KNOWN), "Southern Miss")

    def test_the_louisiana_family_stays_separate(self):
        from cfb_edge.teams import resolve

        self.assertEqual(resolve("Louisiana-Lafayette", self.KNOWN), "Louisiana")
        self.assertEqual(resolve("Louisiana-Monroe", self.KNOWN), "UL Monroe")
        self.assertEqual(resolve("Louisiana Tech", self.KNOWN), "Louisiana Tech")

    def test_accents_and_punctuation_survive_a_round_trip(self):
        from cfb_edge.teams import resolve

        self.assertEqual(resolve("Hawaii", self.KNOWN), "Hawai'i")
        self.assertEqual(resolve("San Jose State", self.KNOWN), "San José State")
        self.assertEqual(resolve("Texas A and M", self.KNOWN), "Texas A&M")

    def test_an_unknown_school_is_refused_not_guessed(self):
        """An unmatched game costs a skipped bet; a mismatched one costs a
        wrong bet. There is no fuzzy fallback for exactly this reason."""
        from cfb_edge.teams import resolve

        self.assertIsNone(resolve("Slippery Rock", self.KNOWN))
        self.assertIsNone(resolve("Miami Dolphins", self.KNOWN))

    def test_game_strings_preserve_home_and_away(self):
        from cfb_edge.teams import resolve_game

        self.assertEqual(
            resolve_game("Miami (FL) @ Texas A&M", self.KNOWN), "Miami @ Texas A&M")
        self.assertIsNone(resolve_game("Miami (FL) vs Texas A&M", self.KNOWN))

    def test_the_report_surfaces_gaps_rather_than_hiding_them(self):
        from cfb_edge.teams import match_games

        known = ["Missouri @ Kansas", "Miami @ Texas A&M"]
        report = match_games(["Missouri @ Kansas", "Nowhere @ Kansas"], known)
        self.assertEqual(len(report.matched), 1)
        self.assertEqual(report.unmatched, ["Nowhere @ Kansas"])
        self.assertIn("ALIASES", report.summary())
        self.assertAlmostEqual(report.rate, 0.5, places=9)


class TestSlateBuilder(unittest.TestCase):
    """A slate must not be able to see the week it is projecting."""

    def _season_csv(self, season):
        header = ("week,home_team,away_team,home_division,away_division,"
                  "home_points,away_points,neutral_site,start_date\n")
        if season == 2025:
            body = ("1,Alpha,Beta,fbs,fbs,31,10,FALSE,2025-09-01\n"
                    "2,Beta,Alpha,fbs,fbs,14,21,FALSE,2025-09-08\n")
        else:
            body = ("1,Alpha,Beta,fbs,fbs,28,14,FALSE,2026-09-05\n"
                    "2,Beta,Alpha,fbs,fbs,7,35,FALSE,2026-09-12\n"
                    "3,Alpha,Beta,fbs,fbs,99,0,FALSE,2026-09-19\n")
        return (header + body).encode()

    def _opener(self):
        def open_url(url):
            return self._season_csv(2025 if "2025" in url else 2026)
        return open_url

    def test_it_projects_the_target_week(self):
        from cfb_edge.slate import build

        rows = build(2026, 3, opener=self._opener())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].game, "Beta @ Alpha")

    def test_the_target_week_result_cannot_leak_into_the_ratings(self):
        """Week 3 has Alpha winning 99-0. If that leaked, the projection
        would be enormous. It must not."""
        from cfb_edge.slate import build

        rows = build(2026, 3, opener=self._opener())
        self.assertLess(abs(rows[0].projected_margin), 40.0)

    def test_an_unreachable_schedule_names_the_host(self):
        import urllib.error
        from cfb_edge.slate import ScheduleUnreachable, fetch_season

        def refuse(url):
            raise urllib.error.URLError("Tunnel connection failed: 403 Forbidden")

        with self.assertRaises(ScheduleUnreachable) as ctx:
            fetch_season(2026, opener=refuse)
        self.assertIn("raw.githubusercontent.com", str(ctx.exception))

    def test_the_written_csv_is_what_play_consumes(self):
        import tempfile, os, csv as _csv
        from cfb_edge.slate import build, write_csv

        fd, path = tempfile.mkstemp(suffix=".csv"); os.close(fd)
        try:
            write_csv(build(2026, 3, opener=self._opener()), path)
            with open(path, newline="", encoding="utf-8") as fh:
                cols = _csv.DictReader(fh).fieldnames
            self.assertEqual(
                cols, ["game", "projected_margin", "side", "posted_line", "total"])
        finally:
            os.unlink(path)

    def test_the_ticker_finder_matches_on_any_field(self):
        from cfb_edge.providers.kalshi import find_markets
        import json

        payload = json.dumps({"markets": [
            {"ticker": "KXNCAAFSPREAD-26SEP11MIZKAN-KAN3",
             "event_ticker": "26SEP11MIZKAN",
             "title": "Missouri at Kansas",
             "yes_sub_title": "Kansas wins by over 3 points",
             "yes_bid": 40, "yes_ask": 42},
            {"ticker": "KXNCAAFSPREAD-26SEP12ASUTAM-TAM3",
             "event_ticker": "26SEP12ASUTAM",
             "title": "Arizona State at Texas A&M",
             "yes_sub_title": "Texas A&M wins by over 3 points",
             "yes_bid": 62, "yes_ask": 64},
        ]}).encode()

        rows = find_markets("Kansas", opener=lambda url: payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "KXNCAAFSPREAD-26SEP11MIZKAN-KAN3")
        self.assertEqual(rows[0]["yes_bid"], 40)

    def test_the_finder_reports_nothing_rather_than_a_near_miss(self):
        from cfb_edge.providers.kalshi import find_markets
        import json

        payload = json.dumps({"markets": [
            {"ticker": "X", "title": "Missouri at Kansas",
             "yes_sub_title": "Kansas wins by over 3 points"}]}).encode()
        self.assertEqual(find_markets("Nebraska", opener=lambda url: payload), [])


class TestRatingScale(unittest.TestCase):
    """Ratings come out compressed; uncorrected that is a directional bias."""

    def test_the_simulator_needs_a_different_scale_than_real_data(self):
        """The constant is a property of the pipeline, not of football. The
        simulator's priors are truth plus noise, so its ratings never compress
        and applying the real-data scale there over-corrects."""
        from cfb_edge.projection import RATING_SCALE, SIMULATED_RATING_SCALE

        self.assertAlmostEqual(SIMULATED_RATING_SCALE, 1.00, places=2)
        self.assertGreater(RATING_SCALE, SIMULATED_RATING_SCALE)

    def test_the_scale_is_applied_to_the_rating_gap(self):
        from cfb_edge.projection import RATING_SCALE, Matchup, project
        from cfb_edge.ratings import RatingModel

        model = RatingModel(ratings={"A": 5.0, "B": -5.0},
                            games_played={"A": 5, "B": 5}, hfa=3.2)
        p = project(model, Matchup("A", "B"))
        self.assertAlmostEqual(p.rating_gap, 10.0 * RATING_SCALE, places=9)
        self.assertAlmostEqual(p.home_margin, 10.0 * RATING_SCALE + 3.2, places=9)

    def test_home_field_is_not_scaled(self):
        """HFA was fitted directly against real margins and is already in the
        right units; scaling it would double-count the correction."""
        from cfb_edge.projection import Matchup, project
        from cfb_edge.ratings import RatingModel

        model = RatingModel(ratings={"A": 0.0, "B": 0.0},
                            games_played={"A": 5, "B": 5}, hfa=3.2)
        self.assertAlmostEqual(project(model, Matchup("A", "B")).home_margin,
                               3.2, places=9)

    def test_a_neutral_site_gets_no_home_field(self):
        from cfb_edge.projection import RATING_SCALE, Matchup, project
        from cfb_edge.ratings import RatingModel

        model = RatingModel(ratings={"A": 5.0, "B": -5.0},
                            games_played={"A": 5, "B": 5}, hfa=3.2)
        p = project(model, Matchup("A", "B", neutral=True))
        self.assertAlmostEqual(p.home_margin, 10.0 * RATING_SCALE, places=9)

    def test_the_scale_expands_rather_than_shrinks(self):
        """Measured at 1.40 against 6,398 real closing lines. A value below 1
        would mean the ratings were too spread out, which they are not."""
        from cfb_edge.projection import RATING_SCALE

        self.assertGreater(RATING_SCALE, 1.0)
        self.assertLess(RATING_SCALE, 2.0)

    def test_compression_biases_toward_underdogs(self):
        """The reason this matters. An uncorrected model understates every
        mismatch, so the favourite always looks overpriced and a
        disagreement-based strategy bets underdogs on its own scale error."""
        from cfb_edge.projection import RATING_SCALE
        from cfb_edge.strategy import signal_side

        raw_gap = 20.0                      # a genuine mismatch
        market_line = -raw_gap              # market has it right
        compressed = raw_gap / RATING_SCALE
        side_wrong, _ = signal_side("Home", "Away", projected_margin=compressed,
                                    opening_home_line=market_line)
        side_right, _ = signal_side("Home", "Away", projected_margin=raw_gap,
                                    opening_home_line=market_line)
        self.assertEqual(side_wrong, "Away")   # the false underdog signal
        self.assertIsNone(side_right)          # corrected: no signal at all
