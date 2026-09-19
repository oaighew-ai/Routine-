"""Tests for the price-shopping candidate path.

The test that matters most here is `test_reference_is_the_sharp_book_not_the
_soft_median`. The first version of `shop.py` used the soft median as truth and
shopped every venue against it, which meant it bet Pinnacle every time Pinnacle
was cheaper than DraftKings — that is, every time Pinnacle was right. The bug
produced plausible-looking picks with positive EV attached, which is the worst
kind, so it gets a test that fails loudly if the direction ever flips back.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from cfb_edge import shop
from cfb_edge.engine import config, reasons

ROOT = Path(__file__).resolve().parents[1]


def book(key, *, spread=None, h2h=None, totals=None):
    markets = []
    if spread is not None:
        point, mine, theirs = spread
        markets.append({"key": "spreads", "outcomes": [
            {"name": "Alpha", "point": point, "price": mine},
            {"name": "Beta", "point": -point, "price": theirs}]})
    if h2h is not None:
        mine, theirs = h2h
        markets.append({"key": "h2h", "outcomes": [
            {"name": "Alpha", "price": mine},
            {"name": "Beta", "price": theirs}]})
    if totals is not None:
        point, over, under = totals
        markets.append({"key": "totals", "outcomes": [
            {"name": "Over", "point": point, "price": over},
            {"name": "Under", "point": point, "price": under}]})
    return {"key": key, "markets": markets}


def event(bookmakers, event_id="evt1"):
    return {
        "id": event_id, "home": "Beta", "away": "Alpha",
        "commenceTime": "2026-09-20T19:00:00Z",
        "bookmakers": bookmakers,
    }


class ShopTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(ROOT / "config" / "edge_os.json")

    def run_shop(self, bookmakers, **kw):
        kw.setdefault("age_seconds", 0.0)
        # Every fixture kicks off at 19:00; shop an hour before it, because
        # after kickoff nothing bets and every test below would pass vacuously.
        kw.setdefault("now", datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc))
        return shop.shop({"events": [event(bookmakers)]}, cfg=self.cfg, **kw)

    # -- direction --------------------------------------------------------

    def test_reference_is_the_sharp_book_not_the_soft_median(self):
        """A soft book slower than Pinnacle is the bet; Pinnacle never is.

        Pinnacle has Beta at +118 (fair, no-vig, about 0.450). The soft books
        are all still at +126 to +130, which is a better price for the same
        side. The bet has to be at a soft book. If the reference ever flips
        back to the soft median, the sign flips with it and this system starts
        betting into the sharpest price on the board.
        """
        rows = self.run_shop([
            book("pinnacle", h2h=(-128, +118)),
            book("draftkings", h2h=(-150, +130)),
            book("fanduel", h2h=(-152, +128)),
            book("betmgm", h2h=(-148, +126)),
            book("betrivers", h2h=(-150, +130)),
        ])
        bets = [r for r in rows if r.bets]
        self.assertTrue(bets, "a soft book 12 cents off the sharp price should bet")
        for r in bets:
            self.assertNotEqual(r.venue, shop.SHARP_REFERENCE)
            self.assertEqual(r.side, "Beta")
        self.assertNotIn(shop.SHARP_REFERENCE, {r.venue for r in rows})

    def test_the_sharp_book_is_never_a_candidate(self):
        """Even priced cheaply on both sides, the reference is not shoppable."""
        rows = self.run_shop([
            book("pinnacle", h2h=(-101, -101)),
            book("draftkings", h2h=(-150, +130)),
            book("fanduel", h2h=(-152, +128)),
            book("betmgm", h2h=(-148, +126)),
        ])
        self.assertNotIn(shop.SHARP_REFERENCE, {r.venue for r in rows})

    # -- line comparability ----------------------------------------------

    def test_a_different_spread_is_a_different_bet(self):
        """-2.5 against a reference of -3 is logged, never bet."""
        rows = self.run_shop([
            book("pinnacle", spread=(-3.0, -105, -105)),
            book("draftkings", spread=(-2.5, -110, -110)),
            book("fanduel", spread=(-3.0, -110, -110)),
            book("betmgm", spread=(-3.0, -110, -110)),
        ])
        dk = [r for r in rows if r.venue == "draftkings" and r.market == "spreads"]
        self.assertTrue(dk)
        for r in dk:
            self.assertIn(reasons.LINE_MISMATCH, r.decision.reason_codes)
            self.assertFalse(r.bets)
            self.assertEqual(r.decision.stake_units, 0.0)

    def test_a_matching_spread_is_compared_on_price(self):
        """At the same number the comparison is pure price.

        Pinnacle at -110 both ways de-vigs to exactly 0.5, so +100 is the
        breakeven price and not a bet; +115 is. The two cases are tested
        together because the boundary is where a sloppy `>=` would show up as
        a board full of coin flips sized as edges.
        """
        def alpha_at(price):
            rows = self.run_shop([
                book("pinnacle", spread=(-3.0, -110, -110)),
                book("draftkings", spread=(-3.0, price, -120)),
                book("fanduel", spread=(-3.0, -110, -110)),
                book("betmgm", spread=(-3.0, -110, -110)),
            ])
            dk = [r for r in rows
                  if r.venue == "draftkings" and r.market == "spreads"
                  and r.side == "Alpha"]
            self.assertEqual(len(dk), 1)
            self.assertNotIn(reasons.LINE_MISMATCH, dk[0].decision.reason_codes)
            return dk[0]

        self.assertAlmostEqual(alpha_at(+100).ev, 0.0, places=9)
        self.assertFalse(alpha_at(+100).bets, "breakeven is not an edge")
        self.assertTrue(alpha_at(+115).bets)

    def test_moneylines_never_mismatch(self):
        rows = self.run_shop([
            book("pinnacle", h2h=(-128, +118)),
            book("draftkings", h2h=(-150, +130)),
            book("fanduel", h2h=(-152, +128)),
            book("betmgm", h2h=(-148, +126)),
        ])
        h2h = [r for r in rows if r.market == "h2h"]
        self.assertTrue(h2h)
        for r in h2h:
            self.assertNotIn(reasons.LINE_MISMATCH, r.decision.reason_codes)

    # -- the reference itself ---------------------------------------------

    def test_soft_median_fallback_is_recorded_not_hidden(self):
        """With no sharp quote the median stands in, and the row says so."""
        rows = self.run_shop([
            book("draftkings", h2h=(-150, +130)),
            book("fanduel", h2h=(-152, +128)),
            book("betmgm", h2h=(-148, +126)),
            book("betrivers", h2h=(-150, +129)),
        ])
        self.assertTrue(rows)
        self.assertEqual({r.reference for r in rows}, {"soft_median"})
        self.assertIn("soft median", shop.summary(rows))

    def test_soft_median_excludes_the_venue_it_prices(self):
        """A book is not measured against a median it is a member of.

        DraftKings is a wild outlier. With DraftKings inside the median the
        reference is dragged toward it and the gap shrinks; held out, the gap
        is the full distance to the other books. The held-out number is the
        larger one, and it is the honest one.
        """
        books = [
            book("draftkings", h2h=(-400, +320)),
            book("fanduel", h2h=(-152, +128)),
            book("betmgm", h2h=(-148, +126)),
            book("betrivers", h2h=(-150, +130)),
        ]
        rows = self.run_shop(books)
        dk = [r for r in rows if r.venue == "draftkings" and r.side == "Beta"]
        self.assertEqual(len(dk), 1)
        self.assertNotIn("draftkings", dk[0].consensus_books)

    def test_thin_market_without_a_sharp_quote_is_skipped(self):
        """Two books is a disagreement, not a consensus.

        Three soft books is also too thin, because holding the venue out of
        its own reference leaves two. Four is the smallest board that prices
        anything without the sharp book.
        """
        for n in (2, 3):
            books = [book(k, h2h=(-150, +130)) for k in
                     ("draftkings", "fanduel", "betmgm")[:n]]
            self.assertEqual(self.run_shop(books), [], f"{n} soft books")

    # -- in-play -----------------------------------------------------------

    def test_a_game_already_under_way_never_bets(self):
        """The first live run's four false picks, as a test.

        A book 12 cents off the sharp price is a candidate before kickoff and
        noise after it, because after kickoff the gap is how fast each book
        updates a moving game. Same prices, same books, opposite answer.
        """
        books = [
            book("pinnacle", h2h=(-128, +118)),
            book("draftkings", h2h=(-150, +130)),
            book("fanduel", h2h=(-152, +128)),
            book("betmgm", h2h=(-148, +126)),
        ]
        before = shop.shop(
            {"events": [event(books)]}, cfg=self.cfg, age_seconds=0.0,
            now=datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc),
        )
        after = shop.shop(
            {"events": [event(books)]}, cfg=self.cfg, age_seconds=0.0,
            now=datetime(2026, 9, 20, 20, 0, tzinfo=timezone.utc),
        )
        self.assertTrue([r for r in before if r.bets])
        self.assertFalse([r for r in after if r.bets])
        for r in after:
            self.assertIn(reasons.IN_PLAY, r.decision.reason_codes)
            self.assertEqual(r.decision.stake_units, 0.0)
        self.assertIn("kicked off", shop.summary(after))

    def test_kickoff_exactly_now_counts_as_started(self):
        books = [
            book("pinnacle", h2h=(-128, +118)),
            book("draftkings", h2h=(-150, +130)),
            book("fanduel", h2h=(-152, +128)),
            book("betmgm", h2h=(-148, +126)),
        ]
        rows = shop.shop(
            {"events": [event(books)]}, cfg=self.cfg, age_seconds=0.0,
            now=datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(rows)
        self.assertFalse([r for r in rows if r.bets])

    def test_an_unknown_kickoff_counts_as_started(self):
        """The unknown case takes the side that only ever skips a bet."""
        for missing in (None, "", "not a timestamp"):
            ev = event([
                book("pinnacle", h2h=(-128, +118)),
                book("draftkings", h2h=(-150, +130)),
                book("fanduel", h2h=(-152, +128)),
                book("betmgm", h2h=(-148, +126)),
            ])
            ev["commenceTime"] = missing
            rows = shop.shop({"events": [ev]}, cfg=self.cfg, age_seconds=0.0,
                             now=datetime(2026, 9, 1, tzinfo=timezone.utc))
            self.assertTrue(rows, repr(missing))
            self.assertFalse([r for r in rows if r.bets], repr(missing))

    # -- bookkeeping -------------------------------------------------------

    def test_one_position_per_game_and_side(self):
        """Three books offering the same side leave one bet and two DOMINATED."""
        rows = self.run_shop([
            book("pinnacle", h2h=(-128, +118)),
            book("draftkings", h2h=(-150, +130)),
            book("fanduel", h2h=(-152, +129)),
            book("betmgm", h2h=(-148, +127)),
        ])
        beta = [r for r in rows if r.side == "Beta" and r.market == "h2h"]
        self.assertEqual(sum(1 for r in beta if r.bets), 1)
        dominated = [r for r in beta
                     if reasons.DOMINATED in r.decision.reason_codes]
        self.assertEqual(len(dominated), len(beta) - 1)
        for r in dominated:
            self.assertEqual(r.decision.stake_units, 0.0)
        best = next(r for r in beta if r.bets)
        self.assertEqual(best.venue, "draftkings")

    def test_every_row_carries_no_evidence(self):
        """Nothing here is a handicapping signal, and the codes have to say so."""
        rows = self.run_shop([
            book("pinnacle", h2h=(-128, +118)),
            book("draftkings", h2h=(-150, +130)),
            book("fanduel", h2h=(-152, +128)),
            book("betmgm", h2h=(-148, +126)),
        ])
        self.assertTrue(rows)
        for r in rows:
            self.assertIn(reasons.NO_EVIDENCE, r.decision.reason_codes)

    def test_rows_become_ledger_candidates(self):
        rows = self.run_shop([
            book("pinnacle", h2h=(-128, +118)),
            book("draftkings", h2h=(-150, +130)),
            book("fanduel", h2h=(-152, +128)),
            book("betmgm", h2h=(-148, +126)),
        ])
        out = shop.board_rows(rows, logged_at="2026-09-19T12:00:00+00:00",
                              phase="SHADOW")
        self.assertEqual(len(out), len(rows))
        self.assertEqual({r["phase"] for r in out}, {"SHADOW"})
        self.assertEqual(len({r["id"] for r in out}), len(out))

    def test_a_stale_pull_decides_nothing(self):
        rows = self.run_shop([
            book("pinnacle", h2h=(-128, +118)),
            book("draftkings", h2h=(-150, +130)),
            book("fanduel", h2h=(-152, +128)),
            book("betmgm", h2h=(-148, +126)),
        ], age_seconds=999_999.0)
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r.decision.decision, reasons.STALE_DECISION)
            self.assertFalse(r.bets)


if __name__ == "__main__":
    unittest.main()
