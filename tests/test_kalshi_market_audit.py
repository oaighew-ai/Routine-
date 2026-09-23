from __future__ import annotations

import unittest
from datetime import datetime, timezone

from cfb_edge.kalshi_market_audit import build


class KalshiMarketMatchAuditTests(unittest.TestCase):
    def market(self, event, team, strike, bid, ask):
        return {
            "event_ticker": event,
            "ticker": f"{event}-{team}-{strike}",
            "yes_sub_title": f"{team} wins by over {strike} points",
            "floor_strike": strike,
            "yes_bid": bid,
            "yes_ask": ask,
        }

    def test_alias_matched_event_is_priceable(self):
        event = "E-ARIZWSU"
        markets = [
            self.market(event, "Washington St.", 3.5, 54, 56),
            self.market(event, "Washington St.", 7.5, 44, 46),
        ]
        r = build(
            ["Arizona @ Washington State"],
            markets,
            generated_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )
        self.assertEqual(r["summary"]["priceableRows"], 1)
        self.assertEqual(r["rows"][0]["status"], "PRICEABLE")
        self.assertEqual(r["rows"][0]["eventTicker"], event)
        self.assertAlmostEqual(r["rows"][0]["derivedHomeLine"], -5.5, places=3)

    def test_event_can_match_but_be_unpriceable(self):
        event = "E-BALLKENT"
        markets = [self.market(event, "Kent St.", 3.5, 40, 70)]
        r = build(
            ["Ball State @ Kent State"],
            markets,
            generated_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )
        self.assertEqual(r["rows"][0]["status"], "EVENT_MATCHED_UNPRICEABLE")
        self.assertEqual(r["summary"]["matchedButUnpriceableRows"], 1)

    def test_absent_event_is_not_confused_with_unpriceable(self):
        r = build(
            ["Rice @ Fresno State"],
            [],
            generated_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )
        self.assertEqual(r["rows"][0]["status"], "NO_MATCHED_EVENT")
        self.assertEqual(r["summary"]["noMatchedEventRows"], 1)

    def test_unknown_team_is_reported_and_never_fuzzy_matched(self):
        markets = [self.market("E-X", "Miami Dolphins", 3.5, 45, 55)]
        r = build(
            ["Miami @ Florida State"],
            markets,
            generated_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )
        self.assertEqual(r["rows"][0]["status"], "NO_MATCHED_EVENT")
        self.assertEqual(r["unresolvedRawNames"], ["Miami Dolphins"])


if __name__ == "__main__":
    unittest.main()
