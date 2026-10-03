"""The order book's wire shape, which changed under us without an error.

Kalshi moved prices and sizes to fixed-point strings and re-wrapped the book
in `orderbook_fp`. `parse_book` read only the old `orderbook` key, so every
live market parsed to an empty book: `best_ask` None, `depth` 0, and `vwap`
refusing every size. Nothing raised, because an empty book is a legitimate
state for a market with nothing resting in it. These tests pin both shapes so
the next rename fails loudly here instead of quietly zeroing the board.

The `orderbook_fp` payload below is the worked example from
docs.kalshi.com/getting_started/orderbook_responses (read 2026-09-25),
including its stated answer: a best YES ask of $0.44 against a best YES bid
of $0.42, a two-cent spread.
"""

import unittest

from cfb_edge.providers.kalshi import Book, parse_book


DOC_EXAMPLE = {
    "orderbook_fp": {
        "yes_dollars": [["0.0100", "200.00"], ["0.1500", "100.00"],
                        ["0.3000", "11.00"], ["0.4100", "10.00"],
                        ["0.4200", "13.00"]],
        "no_dollars": [["0.0100", "100.00"], ["0.2500", "50.00"],
                       ["0.3800", "300.00"], ["0.4500", "20.00"],
                       ["0.5600", "17.00"]],
    }
}


class TestFixedPointBook(unittest.TestCase):
    """The live shape."""

    def test_the_live_shape_is_not_an_empty_book(self):
        # The whole bug in one assertion.
        book = parse_book("T", DOC_EXAMPLE)
        self.assertTrue(book.yes_asks)
        self.assertTrue(book.yes_bids)
        self.assertGreater(book.depth, 0)

    def test_it_reproduces_the_documented_spread(self):
        book = parse_book("T", DOC_EXAMPLE)
        self.assertAlmostEqual(book.best_bid, 42.0, places=9)
        self.assertAlmostEqual(book.best_ask, 44.0, places=9)   # 100 - 56
        self.assertAlmostEqual(book.spread, 2.0, places=9)

    def test_no_bids_invert_into_yes_asks_cheapest_first(self):
        book = parse_book("T", DOC_EXAMPLE)
        prices = [round(level.price, 4) for level in book.yes_asks]
        self.assertEqual(prices, sorted(prices))
        self.assertEqual(prices[0], 44.0)
        self.assertEqual(prices[-1], 99.0)                      # 100 - 1

    def test_the_best_ask_is_never_read_off_the_yes_array(self):
        # Reading the YES bids as asks would report 42c, a price nobody offers.
        book = parse_book("T", DOC_EXAMPLE)
        self.assertGreater(book.best_ask, book.best_bid)

    def test_size_comes_from_the_matching_no_level(self):
        book = parse_book("T", DOC_EXAMPLE)
        self.assertEqual(book.yes_asks[0].size, 17.0)           # the 0.56 rung


class TestFractionalContracts(unittest.TestCase):
    """Counts carry two decimals, and truncating them loses real size."""

    def test_a_fractional_size_is_not_rounded_to_nothing(self):
        book = parse_book("T", {"orderbook_fp": {
            "yes_dollars": [], "no_dollars": [["0.6000", "0.50"]]}})
        self.assertEqual(book.depth, 0.5)
        self.assertEqual(book.best_ask, 40.0)

    def test_depth_sums_fractions(self):
        book = parse_book("T", {"orderbook_fp": {
            "yes_dollars": [],
            "no_dollars": [["0.6000", "0.50"], ["0.5900", "1.25"]]}})
        self.assertAlmostEqual(book.depth, 1.75, places=9)

    def test_a_sub_cent_price_survives_as_fractional_cents(self):
        # The tapered grids tick to $0.0001, so 1.25c is a real resting price.
        book = parse_book("T", {"orderbook_fp": {
            "yes_dollars": [["0.0125", "10.00"]], "no_dollars": []}})
        self.assertAlmostEqual(book.best_bid, 1.25, places=9)


class TestWalkingTheLadder(unittest.TestCase):
    """`vwap` is the only function that prices the size actually wanted."""

    def _book(self) -> Book:
        # YES asks at 44 (17), 45 (20), 62 (300) after inversion.
        return parse_book("T", {"orderbook_fp": {
            "yes_dollars": [],
            "no_dollars": [["0.5600", "17.00"], ["0.5500", "20.00"],
                           ["0.3800", "300.00"]]}})

    def test_a_size_inside_the_top_level_pays_the_top_price(self):
        self.assertAlmostEqual(self._book().vwap(10), 44.0, places=9)

    def test_a_size_that_eats_two_levels_is_a_weighted_average(self):
        # 17 @ 44 + 3 @ 45 = 748 + 135 = 883 over 20.
        self.assertAlmostEqual(self._book().vwap(20), 883.0 / 20.0, places=9)

    def test_a_fractional_size_fills_without_a_float_crumb(self):
        # The exact-equality version of this returned None: 17 - 16.5 left a
        # residual that never compared equal to zero on the next level.
        self.assertAlmostEqual(self._book().vwap(16.5), 44.0, places=9)

    def test_it_refuses_rather_than_extrapolating_past_the_book(self):
        self.assertIsNone(self._book().vwap(10_000))

    def test_a_nonpositive_size_is_not_a_fill(self):
        self.assertIsNone(self._book().vwap(0))


class TestLegacyAndMalformed(unittest.TestCase):
    """The old shape still parses, and one bad rung costs only that rung."""

    def test_the_integer_cent_shape_still_parses(self):
        book = parse_book("T", {"orderbook": {"yes": [[45, 800], [46, 500]],
                                              "no": [[51, 900], [52, 600]]}})
        self.assertEqual([lv.price for lv in book.yes_asks], [48.0, 49.0])
        self.assertEqual(book.best_bid, 46.0)

    def test_an_empty_payload_is_an_empty_book_not_a_crash(self):
        for payload in ({}, {"orderbook": None}, {"orderbook_fp": None}):
            book = parse_book("T", payload)
            self.assertIsNone(book.best_ask)
            self.assertIsNone(book.spread)
            self.assertIsNone(book.vwap(1))
            self.assertEqual(book.depth, 0)

    def test_a_malformed_level_is_dropped_and_the_rest_survives(self):
        book = parse_book("T", {"orderbook_fp": {
            "yes_dollars": [],
            "no_dollars": [["0.5600", "17.00"], ["oops"], ["x", "y"],
                           ["0.5500", "20.00"]]}})
        self.assertEqual(len(book.yes_asks), 2)
        self.assertAlmostEqual(book.depth, 37.0, places=9)

    def test_a_zero_size_level_is_not_liquidity(self):
        book = parse_book("T", {"orderbook_fp": {
            "yes_dollars": [], "no_dollars": [["0.5600", "0.00"]]}})
        self.assertIsNone(book.best_ask)


if __name__ == "__main__":
    unittest.main()
