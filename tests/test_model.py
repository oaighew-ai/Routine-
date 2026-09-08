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
        model = self._seasoned_model()
        fair = -(model.rating("Strong") - model.rating("Weak") + model.hfa)
        c = evaluate(model, Matchup("Strong", "Weak"), market_home_line=round(fair * 2) / 2)
        self.assertFalse(c.is_bet)

    def test_takes_the_side_the_model_prefers(self):
        model = self._seasoned_model()
        fair = -(model.rating("Strong") - model.rating("Weak") + model.hfa)
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
        model = self._seasoned_model()
        fair = -(model.rating("Strong") - model.rating("Weak") + model.hfa)
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
        from cfb_edge.board import main
        import io, contextlib

        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = main(["--week", "2"])
        # An empty board and a blocked board mean opposite things.
        self.assertEqual(code, 2)
        self.assertIn("could not fetch", err.getvalue())


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

    def test_a_free_price_needs_no_edge(self):
        from cfb_edge.line_movement import clv_required

        self.assertAlmostEqual(clv_required(100).clv_needed, 0.0, places=9)


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
            b = best_strike(0.44, projected_margin=pm)
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

        b = best_strike(0.44, projected_margin=10.0)
        self.assertTrue(b.venue.startswith("book"))
        self.assertEqual(abs(b.strike), 7)

    def test_an_exchange_wins_when_the_book_line_is_an_ordinary_number(self):
        from cfb_edge.venue import best_strike

        b = best_strike(0.44, projected_margin=0.0)
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
        on_peak = find_plays("g", projected_margin=7.0, side="home",
                             venues=self._venues(), posted_line=-7.0)
        b110 = [p for p in on_peak if p.venue == "book -110"]
        b105 = [p for p in on_peak if p.venue == "book -105"]
        self.assertTrue(b110)
        self.assertLess(b110[0].net, 0.005)
        self.assertGreater(b105[0].net, b110[0].net * 4)

        # Line not a key number: no book play at all, at any price.
        off_key = find_plays("g", projected_margin=0.0, side="home",
                             venues=self._venues(), posted_line=-1.5)
        self.assertFalse(any(p.venue.startswith("book") for p in off_key))

        # A key number far from the projection is not enough either: the
        # density there is too low for -110 even though 14 is a key number.
        far = find_plays("g", projected_margin=10.0, side="home",
                         venues=self._venues(), posted_line=-14.0)
        self.assertFalse(any(p.venue == "book -110" for p in far))
        self.assertTrue(any(p.venue == "book -105" for p in far))

    def test_a_book_only_appears_when_its_posted_line_is_a_key_number(self):
        from cfb_edge.strategy import find_plays

        on_key = find_plays("g", projected_margin=10.0, side="home",
                            venues=self._venues(), posted_line=-7.0)
        self.assertTrue(any(p.venue == "book -105" for p in on_key))
        off_key = find_plays("g", projected_margin=10.0, side="home",
                             venues=self._venues(), posted_line=-8.5)
        self.assertFalse(any(p.venue.startswith("book") for p in off_key))

    def test_a_reduced_juice_book_on_a_key_number_beats_the_exchange(self):
        from cfb_edge.strategy import find_plays

        plays = find_plays("g", projected_margin=10.0, side="home",
                           venues=self._venues(), posted_line=-7.0)
        self.assertEqual(plays[0].venue, "book -105")

    def test_the_exchange_carries_the_board_away_from_key_lines(self):
        from cfb_edge.strategy import find_plays

        plays = find_plays("g", projected_margin=1.0, side="home",
                           venues=self._venues(), posted_line=-1.5)
        self.assertTrue(plays)
        self.assertTrue(all(p.venue == "exchange" for p in plays))
        self.assertEqual(plays[0].number, 3)

    def test_a_smaller_edge_produces_no_card_at_all(self):
        from cfb_edge.strategy import find_plays

        plays = find_plays("g", projected_margin=0.0, side="home",
                           venues=self._venues(), posted_line=-3.0,
                           clv_points=0.10)
        self.assertEqual(plays, [])

    def test_stakes_are_capped_per_bet(self):
        from cfb_edge.strategy import MAX_STAKE, find_plays

        plays = find_plays("g", projected_margin=0.0, side="home",
                           venues=self._venues(), posted_line=-3.0,
                           clv_points=3.0)
        self.assertTrue(plays)
        for p in plays:
            self.assertLessEqual(p.stake, MAX_STAKE + 1e-12)

    def test_the_card_takes_one_play_per_game(self):
        from cfb_edge.strategy import build_card, find_plays

        per_game = [
            find_plays(f"g{i}", projected_margin=float(i), side="home",
                       venues=self._venues(), posted_line=-1.5)
            for i in range(4)
        ]
        card = build_card(per_game)
        self.assertEqual(len(card), len({p.game for p in card}))
        self.assertLessEqual(len(card), 4)

    def test_the_weekly_exposure_cap_scales_proportionally(self):
        from cfb_edge.strategy import build_card, find_plays

        per_game = [
            find_plays(f"g{i}", projected_margin=0.0, side="home",
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
                find_plays("g", projected_margin=0.0, side=blank,
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
