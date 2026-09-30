"""The fill test: does a shopped row get priced at a size it can actually take?

The bug these guard against is the one `shop.py` warned about in prose for weeks
without measuring: a row clearing the gate on a price resting one contract deep.
So the keystone assertions are that a thin book does not bet, that a deep book
prices worse than its top of book, and that turning the probe off restores the
old behaviour exactly, because frozen cohorts depend on that.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from cfb_edge import fill, shop
from cfb_edge.engine import config, reasons
from cfb_edge.providers import kalshi

ROOT = Path(__file__).resolve().parents[1]


def book(ticker: str, asks: list[tuple[float, float]]) -> kalshi.Book:
    """A Book with a YES ask ladder, cheapest first, and no bids."""
    return kalshi.Book(
        ticker=ticker,
        yes_asks=[kalshi.Level(price=p, size=s) for p, s in asks],
        yes_bids=[],
    )


MONEYLINE = [
    {"ticker": "KXNCAAFGAME-X-ARST", "yes_sub_title": "Arkansas St."},
    {"ticker": "KXNCAAFGAME-X-MEM", "yes_sub_title": "Memphis"},
]


class TestMoneylineIndex(unittest.TestCase):
    def test_it_resolves_a_team_to_its_ticker(self):
        index = fill.moneyline_index(
            MONEYLINE, known_teams={"Arkansas St.", "Memphis"}
        )
        self.assertEqual(index["Arkansas St."], "KXNCAAFGAME-X-ARST")
        self.assertEqual(index["Memphis"], "KXNCAAFGAME-X-MEM")

    def test_an_unresolvable_name_is_dropped_not_guessed(self):
        index = fill.moneyline_index(
            [{"ticker": "T", "yes_sub_title": "Not A Real Team"}],
            known_teams={"Memphis"},
        )
        self.assertEqual(index, {})

    def test_a_team_on_two_tickers_is_dropped_entirely(self):
        """Ambiguity must not resolve to whichever set member came last.

        `board_quotes` shipped that bug once: an orientation that fell out of
        set iteration order passed locally and failed on CI. A contract bought
        on the wrong team is a different bet, not a mispriced one.
        """
        index = fill.moneyline_index(
            [
                {"ticker": "T1", "yes_sub_title": "Memphis"},
                {"ticker": "T2", "yes_sub_title": "Memphis"},
            ],
            known_teams={"Memphis"},
        )
        self.assertEqual(index, {})

    def test_it_resolves_through_the_alias_table(self):
        index = fill.moneyline_index(
            [{"ticker": "T", "yes_sub_title": "Louisiana State"}],
            known_teams={"LSU"},
        )
        self.assertEqual(index, {"LSU": "T"})

    def test_a_market_missing_a_ticker_or_a_name_is_skipped(self):
        index = fill.moneyline_index(
            [
                {"ticker": "", "yes_sub_title": "Memphis"},
                {"ticker": "T", "yes_sub_title": ""},
            ],
            known_teams={"Memphis"},
        )
        self.assertEqual(index, {})


class TestLadderProbe(unittest.TestCase):
    def probe(self, asks, *, size=200):
        return fill.LadderProbe(
            {"Arkansas St.": "T"}, {"T": book("T", asks)}, size=size
        )

    def test_a_deep_book_prices_at_the_walked_average(self):
        p = self.probe([(40.0, 100.0), (50.0, 100.0)], size=200)
        got = p(side="Arkansas St.", home="Memphis", away="Arkansas St.")
        self.assertIsNone(got.flag)
        self.assertAlmostEqual(got.vwap_cents, 45.0)
        self.assertEqual(got.size, 200)

    def test_the_walked_price_is_worse_than_the_top_of_book(self):
        """The whole point. Sizing off the best ask overstates the edge."""
        asks = [(40.0, 10.0), (60.0, 1000.0)]
        p = self.probe(asks, size=200)
        got = p(side="Arkansas St.", home="Memphis", away="Arkansas St.")
        top = book("T", asks).best_ask
        self.assertEqual(top, 40.0)
        self.assertGreater(got.vwap_cents, top)

    def test_a_book_too_thin_for_the_size_is_NO_FILL(self):
        p = self.probe([(40.0, 5.0)], size=200)
        got = p(side="Arkansas St.", home="Memphis", away="Arkansas St.")
        self.assertEqual(got.flag, reasons.NO_FILL)
        self.assertIsNone(got.price)
        self.assertEqual(got.depth, 5.0)

    def test_an_unmapped_side_is_UNMAPPED(self):
        p = self.probe([(40.0, 1000.0)])
        got = p(side="Memphis", home="Memphis", away="Arkansas St.")
        self.assertEqual(got.flag, reasons.UNMAPPED)
        self.assertIsNone(got.ticker)

    def test_a_ticker_with_no_fetched_ladder_is_NO_FILL(self):
        p = fill.LadderProbe({"Arkansas St.": "T"}, {}, size=200)
        got = p(side="Arkansas St.", home="Memphis", away="Arkansas St.")
        self.assertEqual(got.flag, reasons.NO_FILL)
        self.assertIsNone(got.depth)

    def test_the_side_resolves_against_this_fixture_only(self):
        """A name ambiguous league-wide can still be unambiguous in one game."""
        p = fill.LadderProbe(
            {"Miami": "T"}, {"T": book("T", [(40.0, 1000.0)])}, size=10
        )
        got = p(side="Miami Hurricanes", home="Florida St.", away="Miami")
        self.assertIsNone(got.flag)
        self.assertEqual(got.ticker, "T")


class TestCentsToAmerican(unittest.TestCase):
    def test_a_coin_flip_is_even_money(self):
        self.assertAlmostEqual(fill._american_from_cents(50.0), 100.0)

    def test_a_favourite_is_negative(self):
        self.assertLess(fill._american_from_cents(80.0), 0)

    def test_a_longshot_is_a_big_positive(self):
        self.assertGreater(fill._american_from_cents(5.0), 1000)

    def test_a_price_outside_the_bounds_has_no_american_equivalent(self):
        for cents in (0.0, 100.0, -1.0, 101.0):
            with self.subTest(cents=cents):
                self.assertIsNone(fill._american_from_cents(cents))


def h2h(key, alpha, beta):
    """One book's moneyline on both sides, in the shape the pull publishes."""
    return {"key": key, "markets": [{"key": "h2h", "outcomes": [
        {"name": "Alpha", "price": alpha},
        {"name": "Beta", "price": beta}]}]}


def spreads(key, point, alpha, beta):
    return {"key": key, "markets": [{"key": "spreads", "outcomes": [
        {"name": "Alpha", "point": point, "price": alpha},
        {"name": "Beta", "point": -point, "price": beta}]}]}


def event(bookmakers):
    return {
        "id": "evt1", "home": "Beta", "away": "Alpha",
        "commenceTime": "2026-09-20T19:00:00Z",
        "bookmakers": bookmakers,
    }


class TestShopWiring(unittest.TestCase):
    """The probe as `shop` actually calls it.

    Team names are Alpha and Beta so the alias table is not in the loop: the
    probe resolves a side against the two teams in the fixture, and an exact
    name resolves to itself. The alias path has its own tests above.
    """

    def setUp(self):
        self.cfg = config.load(ROOT / "config" / "edge_os.json")

    def run_shop(self, bookmakers, **kw):
        kw.setdefault("age_seconds", 0.0)
        kw.setdefault("now", datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc))
        return shop.shop({"events": [event(bookmakers)]}, cfg=self.cfg, **kw)

    def alpha_on_kalshi(self, rows):
        return [r for r in rows
                if r.venue == "kalshi" and r.side == "Alpha"]

    def probe_for(self, asks, *, size=200):
        return fill.LadderProbe(
            {"Alpha": "T"}, {"T": book("T", asks)}, size=size
        )

    def kalshi_rows(self, rows):
        return [r for r in rows if r.venue == "kalshi"]

    BOOKS = [
        h2h("kalshi", 400, -450),
        h2h("pinnacle", 300, -340),
        h2h("draftkings", 320, -360),
        h2h("fanduel", 310, -350),
    ]

    def test_without_a_probe_nothing_changes(self):
        """The default path must behave exactly as before: cohorts price here.

        D33-D35 make those cohorts forward-only, so a probe that switched itself
        on would re-price a settled measurement after the fact.
        """
        rows = self.run_shop(self.BOOKS)
        self.assertTrue(rows)
        for r in rows:
            self.assertIsNone(r.fill)
            self.assertFalse(r.priced_at_fill)
            self.assertAlmostEqual(r.decided_price, r.venue_price)

    def test_a_thin_kalshi_book_is_flagged_NO_FILL_with_no_stake(self):
        """Deliberately not asserting `not bets`: that would be vacuous.

        No shopped row can BET today — `decide` returns PASS with zero stake for
        any row with no registered Stage-A signal, and `shop` passes none — so
        `assertFalse(bets)` would pass whether or not this code existed and would
        credit the fill test with a refusal it did not make. What is actually new
        is the code on the row, and that the stake stays nailed to zero once
        NO_FILL is present, which is what must survive a system being registered.
        """
        rows = self.alpha_on_kalshi(self.run_shop(
            self.BOOKS, fill_probe=self.probe_for([(20.0, 3.0)])
        ))
        self.assertTrue(rows)
        for r in rows:
            self.assertIn(reasons.NO_FILL, r.decision.reason_codes)
            self.assertEqual(r.decision.stake_units, 0.0)
            self.assertEqual(r.decision.decision, reasons.PASS)

    def test_NO_FILL_is_absent_when_the_ladder_covers_the_size(self):
        """The guard against a code that fires on everything."""
        rows = self.alpha_on_kalshi(self.run_shop(
            self.BOOKS, fill_probe=self.probe_for([(20.0, 10_000.0)])
        ))
        self.assertTrue(rows)
        for r in rows:
            self.assertNotIn(reasons.NO_FILL, r.decision.reason_codes)
            self.assertTrue(r.priced_at_fill)

    def test_unavailable_size_turns_a_positive_EV_negative(self):
        """The material effect: the number the board ranks by, corrected.

        +400 against this sharp reference reads as a healthy edge. Ten contracts
        rest at that price and the operator wants 200, so the real average is 39c
        and the same row is well under water. An EV inflated by size that is not
        there is a candidate that looks like a miss later for a reason nobody can
        reconstruct.
        """
        quoted = self.alpha_on_kalshi(self.run_shop(self.BOOKS))[0]
        walked = self.alpha_on_kalshi(self.run_shop(
            self.BOOKS,
            fill_probe=self.probe_for([(20.0, 10.0), (40.0, 10_000.0)]),
        ))[0]
        self.assertGreater(quoted.ev, 0.0)
        self.assertLess(walked.ev, 0.0)
        self.assertLess(walked.ev, quoted.ev)

    def test_the_decision_is_made_on_the_walked_price(self):
        """Law 4: EV at the executable price, and Law 5: gated once."""
        rows = self.alpha_on_kalshi(self.run_shop(
            self.BOOKS,
            fill_probe=self.probe_for([(20.0, 10.0), (40.0, 10_000.0)]),
        ))
        self.assertTrue(rows)
        row = rows[0]
        self.assertTrue(row.priced_at_fill)
        # Only 10 contracts rest at 20c, so the other 190 come at 40c and the
        # row is decided on that average, not on the top of book.
        self.assertGreater(row.fill.vwap_cents, 20.0)
        self.assertNotAlmostEqual(row.decided_price, row.venue_price)
        self.assertAlmostEqual(row.decision.quote.price, row.decided_price)

    def test_the_quoted_price_is_still_recorded(self):
        """`venue_price` keeps meaning what the venue published."""
        rows = self.alpha_on_kalshi(self.run_shop(
            self.BOOKS,
            fill_probe=self.probe_for([(20.0, 10.0), (40.0, 10_000.0)]),
        ))
        self.assertAlmostEqual(rows[0].venue_price, 400.0)

    def test_only_the_named_venues_are_tested(self):
        seen: list[str] = []

        def probe(*, side, home, away):
            seen.append(side)
            return fill.Fill(size=200)

        rows = self.run_shop(self.BOOKS, fill_probe=probe)
        self.assertTrue(seen)
        for r in rows:
            if r.venue != "kalshi":
                self.assertIsNone(r.fill)

    def test_a_non_moneyline_market_is_left_untested(self):
        """Not a shortcut: a strike and a book line are different bets."""
        seen: list[str] = []

        def probe(*, side, home, away):
            seen.append(side)
            return fill.Fill(size=200)

        self.run_shop(
            [spreads("kalshi", -3.5, -110, -110),
             spreads("pinnacle", -3.5, -108, -108),
             spreads("draftkings", -3.5, -112, -112)],
            fill_probe=probe, markets=["spreads"],
        )
        self.assertEqual(seen, [])

    def test_the_summary_says_untested_is_not_passed(self):
        rows = self.run_shop(
            self.BOOKS, fill_probe=self.probe_for([(20.0, 3.0)])
        )
        text = shop.summary(rows)
        self.assertIn("NO_FILL", text)
        self.assertIn("untested quote", text)


class TestProbeFromPayloads(unittest.TestCase):
    def test_it_parses_the_live_fixed_point_shape(self):
        """D36: the live wire shape is `orderbook_fp`, in dollars."""
        probe = fill.probe_from_payloads(
            MONEYLINE,
            {"KXNCAAFGAME-X-ARST": {"orderbook_fp": {
                "yes_dollars": [["0.1900", "5000.00"]],
                "no_dollars": [["0.7900", "9000.00"]],
            }}},
            known_teams={"Arkansas St.", "Memphis"},
            size=100,
        )
        got = probe(side="Arkansas St.", home="Memphis", away="Arkansas St.")
        self.assertIsNone(got.flag)
        # A NO bid at 79c is a YES offer at 21c.
        self.assertAlmostEqual(got.vwap_cents, 21.0)

    def test_an_empty_payload_cannot_fill(self):
        probe = fill.probe_from_payloads(
            MONEYLINE,
            {"KXNCAAFGAME-X-ARST": {}},
            known_teams={"Arkansas St.", "Memphis"},
            size=100,
        )
        got = probe(side="Arkansas St.", home="Memphis", away="Arkansas St.")
        self.assertEqual(got.flag, reasons.NO_FILL)


if __name__ == "__main__":
    unittest.main()
