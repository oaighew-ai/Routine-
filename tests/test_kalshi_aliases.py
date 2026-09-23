"""Provider spellings must preserve slate identity and line orientation."""
import json
import unittest

from cfb_edge.providers.kalshi import board_quotes
from cfb_edge.teams import resolve


class KalshiAliasTests(unittest.TestCase):
    PAIRS = (
        ("Washington St.", "Washington State"),
        ("Mississippi St.", "Mississippi State"),
        ("UMass", "Massachusetts"),
        ("Louisiana-Monroe", "UL Monroe"),
        ("Sacramento St.", "Sacramento State"),
    )

    @staticmethod
    def markets(team):
        return [dict(event_ticker="E", ticker=f"E-{strike}",
                     yes_sub_title=f"{team} wins by over {strike} points",
                     yes_bid=bid, yes_ask=bid + 2)
                for strike, bid in ((3.5, 54), (7.5, 44))]

    def quotes(self, games, markets):
        payload = json.dumps({"markets": markets}).encode()
        return board_quotes(games=games, opener=lambda _: payload,
                            seen_at="2026-09-23T12:00:00+00:00")

    def test_documented_aliases_resolve_and_preserve_both_orientations(self):
        for raw, canonical in self.PAIRS:
            for home in (True, False):
                with self.subTest(raw=raw, home=home):
                    game = f"Arizona @ {canonical}" if home else f"{canonical} @ Arizona"
                    self.assertEqual(resolve(raw, {canonical, "Arizona"}), canonical)
                    quotes = self.quotes([game], self.markets(raw))
                    self.assertEqual(len(quotes), 1)
                    self.assertEqual(quotes[0].game, game)
                    self.assertEqual(quotes[0].line, -5.5 if home else 5.5)
                    self.assertEqual(quotes[0].market_tickers, ("E-3.5", "E-7.5") if home else ("E-7.5", "E-3.5"))

    def test_normalized_collisions_fail_closed(self):
        known = {"Washington State", "Washington St"}
        self.assertIsNone(resolve("WASHINGTON ST.", known))
        self.assertEqual(resolve("Washington State", known), "Washington State")
        self.assertEqual(self.quotes(
            ["Arizona @ Washington State", "Washington St @ Oregon"],
            self.markets("WASHINGTON ST.")), [])

    def test_alias_target_collisions_fail_closed(self):
        self.assertIsNone(resolve("UMass", {"Massachusetts", "Massachusetts University"}))
        self.assertIsNone(resolve("UMASS", {"UMass", "Massachusetts"}))

    def test_unknown_names_do_not_fuzzy_match(self):
        self.assertIsNone(resolve("Mississipi St.", {"Mississippi State"}))
        self.assertEqual(self.quotes(["Arizona @ Mississippi State"],
                                    self.markets("Mississipi St.")), [])

    def test_alias_on_multiple_fixtures_fails_closed(self):
        self.assertEqual(self.quotes(
            ["Arizona @ Washington State", "Washington State @ Oregon"],
            self.markets("Washington St.")), [])

    def test_conflicting_home_away_fixtures_fail_closed(self):
        markets = self.markets("Washington St.") + self.markets("Arizona")
        self.assertEqual(self.quotes(
            ["Arizona @ Washington State", "Washington State @ Arizona"], markets), [])

    def test_unresolved_second_team_invalidates_event(self):
        self.assertEqual(self.quotes(["Arizona @ Washington State"],
            self.markets("Washington St.") + self.markets("Unknown School")), [])


if __name__ == "__main__":
    unittest.main()
