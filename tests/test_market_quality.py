from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from cfb_edge.engine import config as engine_config
from cfb_edge.engine.posterior import Decision, Quote
from cfb_edge.market_quality import (
    key_number_crossings,
    movement_metrics,
    quality_for_row,
)
from cfb_edge.shop import ShopRow

ROOT = Path(__file__).resolve().parents[1]


def event(line=3.0, fetched="2026-09-21T12:00:00Z"):
    def market(book, point, mine=-105, other=-115, age_minutes=1):
        minute = 0 - age_minutes
        stamp = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        stamp = stamp.replace(minute=0)
        stamp = stamp.timestamp() - age_minutes * 60
        last = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        return {
            "key": book,
            "markets": [{
                "key": "spreads",
                "lastUpdate": last,
                "outcomes": [
                    {"name": "Home Hawks", "price": mine, "point": point},
                    {"name": "Away Bears", "price": other, "point": -point},
                ],
            }],
        }

    return {
        "sport": "americanfootball_ncaaf",
        "fetched_at": fetched,
        "book_set": ["pinnacle", "draftkings", "fanduel", "betmgm"],
        "events": [{
            "id": "evt",
            "commenceTime": "2026-09-26T19:30:00Z",
            "home": "Home Hawks",
            "away": "Away Bears",
            "bookmakers": [
                market("pinnacle", line, -108, -102, 1),
                market("draftkings", line, -102, -118, 2),
                market("fanduel", line, -104, -116, 3),
                market("betmgm", line + 0.5, -110, -110, 4),
            ],
        }],
    }


def row():
    q = Quote(
        event_id="evt",
        sport="americanfootball_ncaaf",
        market="spreads",
        side="Home Hawks",
        line=3.0,
        price=-102.0,
        other_price=-118.0,
        consensus_price=-108.0,
        consensus_other_price=-102.0,
        venue="draftkings",
        book_set=("draftkings", "fanduel", "betmgm"),
        starts_at="2026-09-26T19:30:00Z",
        price_source="capture",
    )
    d = Decision(
        quote=q,
        decision="PASS",
        p_baseline=0.49,
        p_post=0.49,
        ev=0.01,
        devig={"proportional": 0.492, "power": 0.488},
    )
    return ShopRow(
        decision=d,
        event_id="evt",
        home="Home Hawks",
        away="Away Bears",
        commence_time="2026-09-26T19:30:00Z",
        market="spreads",
        side="Home Hawks",
        venue="draftkings",
        venue_line=3.0,
        venue_price=-102.0,
        consensus_line=3.0,
        consensus_price=-108.0,
        consensus_books=("fanduel", "betmgm"),
        reference="pinnacle",
    )


def candidate():
    return {
        "game": "Away @ Home",
        "side": "Home",
        "openingHomeLine": 2.5,
    }


class MarketQualityTests(unittest.TestCase):
    def setUp(self):
        self.edge = engine_config.load(ROOT / "config/edge_os.json")
        self.cfg = {
            "referenceBook": "pinnacle",
            "minimumFreshSameLineBooks": 3,
            "maximumQuoteAgeSeconds": 900,
            "keyNumbers": [3, 7, 10, 14],
        }

    def test_exact_line_depth_and_freshness_pass_without_directional_authority(self):
        result = quality_for_row(
            row=row(),
            candidate=candidate(),
            pull=event(),
            history=[],
            cfg=self.cfg,
            edge_cfg=self.edge,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["directionalAuthority"])
        self.assertEqual(result["freshSameLineBookCount"], 3)
        self.assertEqual(
            result["freshSameLineBooks"],
            ["draftkings", "fanduel", "pinnacle"],
        )
        self.assertEqual(result["keyNumberCrossings"], [3])

    def test_thin_same_line_market_fails_closed(self):
        pull = event()
        # Move FanDuel away from the reference line so only Pinnacle + DK remain.
        pull["events"][0]["bookmakers"][2]["markets"][0]["outcomes"][0]["point"] = 3.5
        pull["events"][0]["bookmakers"][2]["markets"][0]["outcomes"][1]["point"] = -3.5
        result = quality_for_row(
            row=row(), candidate=candidate(), pull=pull, history=[],
            cfg=self.cfg, edge_cfg=self.edge,
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("INSUFFICIENT_FRESH_SAME_LINE_BOOKS", result["reasons"])

    def test_stale_reference_fails_closed(self):
        pull = event()
        pull["events"][0]["bookmakers"][0]["markets"][0]["lastUpdate"] = (
            "2026-09-21T11:30:00Z"
        )
        result = quality_for_row(
            row=row(), candidate=candidate(), pull=pull, history=[],
            cfg=self.cfg, edge_cfg=self.edge,
        )
        self.assertIn("REFERENCE_QUOTE_STALE", result["reasons"])

    def test_conservative_ev_is_the_worst_devig_reading(self):
        result = quality_for_row(
            row=row(), candidate=candidate(), pull=event(), history=[],
            cfg=self.cfg, edge_cfg=self.edge,
        )
        values = result["devigExecutableEv"]
        self.assertEqual(result["conservativeExecutableEv"], min(values.values()))
        self.assertIn("proportional", values)
        self.assertIn("power", values)

    def test_key_crossings_are_diagnostic_not_an_edge(self):
        self.assertEqual(key_number_crossings(2.5, 7.5), [3, 7])
        self.assertEqual(key_number_crossings(-7.5, -6.5), [7])

    def test_movement_velocity_and_reversal_use_only_prior_snapshots(self):
        base = event()
        prev2 = event(fetched="2026-09-21T08:00:00Z")
        prev1 = event(fetched="2026-09-21T10:00:00Z")
        # Home side moved 2.5 -> 3.5 -> 3.0, so the last segment reverses.
        for snap, point in ((prev2, 2.5), (prev1, 3.5), (base, 3.0)):
            for book in snap["events"][0]["bookmakers"]:
                if book["key"] == "pinnacle":
                    book["markets"][0]["outcomes"][0]["point"] = point
                    book["markets"][0]["outcomes"][1]["point"] = -point
        metrics = movement_metrics(
            [prev2, prev1, base],
            event_id="evt",
            side="Home Hawks",
            reference_book="pinnacle",
        )
        self.assertEqual(metrics["observations"], 3)
        self.assertTrue(metrics["reversal"])
        self.assertGreater(metrics["previousVelocityPointsPerHour"], 0)
        self.assertLess(metrics["latestVelocityPointsPerHour"], 0)


if __name__ == "__main__":
    unittest.main()
