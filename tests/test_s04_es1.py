from __future__ import annotations

import json
import unittest
from pathlib import Path

from cfb_edge.engine import config as engine_config
from cfb_edge.engine import reasons
from cfb_edge.engine.posterior import Decision, Quote
from cfb_edge.s04_es1 import (
    candidate_map,
    cohort_stats,
    evaluate,
    historical_reasons,
)
from cfb_edge.shop import ShopRow

ROOT = Path(__file__).resolve().parents[1]


def challenger():
    return json.loads((ROOT / "config/s04_es1.json").read_text())


def learning(*, week2_negative: bool = False):
    rows = []
    for week in (1, 2, 3):
        for i in range(5):
            gap = 5.0 if i % 2 == 0 else -5.0
            clv = 1.0
            if week2_negative:
                clv = -1.0
            rows.append({
                "week": week,
                "projection_gap_vs_open": gap,
                "directional_clv": clv,
            })
    return {"rows": rows}


def week4():
    return {
        "rows": [{
            "game": "Away @ Home",
            "side": "Home",
            "source": "capture",
            "openingHomeLine": -3.0,
            "projectionGapVsOpen": 5.0,
            "firstSeen": "2026-09-20T23:00:00Z",
            "kickoff": "2026-09-26T19:30:00Z",
        }]
    }


def shop_row(
    *,
    reference="pinnacle",
    ref_line=-3.5,
    venue_line=-3.5,
    ev=0.02,
    reason_codes=None,
    away="Away",
    home="Home",
    side="Home",
):
    quote = Quote(
        event_id="evt",
        sport="americanfootball_ncaaf",
        market="spreads",
        side=side,
        line=venue_line,
        price=-105.0,
        consensus_price=-110.0,
        consensus_other_price=-110.0,
        venue="draftkings",
        book_set=("draftkings", "fanduel", "betmgm"),
        starts_at="2026-09-26T19:30:00Z",
        price_source="capture",
    )
    d = Decision(
        quote=quote,
        decision=reasons.PASS,
        reason_codes=list(reason_codes or [reasons.NO_EVIDENCE]),
        p_baseline=0.5,
        p_post=0.5,
        ev=ev,
        stake_units=0.0,
    )
    return ShopRow(
        decision=d,
        event_id="evt",
        home=home,
        away=away,
        commence_time="2026-09-26T19:30:00Z",
        market="spreads",
        side=side,
        venue="draftkings",
        venue_line=venue_line,
        venue_price=-105.0,
        consensus_line=ref_line,
        consensus_price=-110.0,
        consensus_books=("fanduel", "betmgm"),
        reference=reference,
    )


class S04ES1Tests(unittest.TestCase):
    def setUp(self):
        self.ch = challenger()
        self.edge = engine_config.load(ROOT / "config/edge_os.json")

    def test_4_to_6_cohort_can_qualify_only_as_shadow(self):
        stats = cohort_stats(
            learning(),
            gap_min=self.ch["gapMin"],
            gap_max=self.ch["gapMax"],
        )
        self.assertEqual(stats.n, 15)
        self.assertEqual(historical_reasons(stats, self.ch), [])
        self.assertGreater(stats.mean_directional_clv, 0)
        self.assertGreater(stats.beat_close_rate, 0.5)

    def test_one_negative_historical_week_blocks_the_challenger(self):
        stats = cohort_stats(
            learning(week2_negative=True),
            gap_min=self.ch["gapMin"],
            gap_max=self.ch["gapMax"],
        )
        self.assertIn("WEEK_2_NONPOSITIVE_CLV", historical_reasons(stats, self.ch))

    def test_candidate_map_uses_only_timely_capture_and_frozen_gap(self):
        w = week4()
        w["rows"].append({
            "game": "Other @ Team",
            "side": "Team",
            "source": "late",
            "openingHomeLine": -3.0,
            "projectionGapVsOpen": 5.0,
        })
        got = candidate_map(w, self.ch)
        self.assertIn(("Away @ Home", "Home"), got)
        self.assertNotIn(("Other @ Team", "Team"), got)

    def test_positive_live_pinnacle_confirmed_row_surfaces_zero_stake_shadow(self):
        report = evaluate(
            learning=learning(),
            week4=week4(),
            shop_rows=[shop_row()],
            challenger_cfg=self.ch,
            edge_cfg=self.edge,
            fetched_at="2026-09-21T12:00:00Z",
        )
        self.assertEqual(report["qualifiedCount"], 1)
        self.assertEqual(report["topFive"][0]["status"], "EXECUTABLE_SHADOW")
        self.assertEqual(report["topFive"][0]["actualStakeUnits"], 0)
        self.assertEqual(report["deliveryEffect"], "NONE")
        self.assertEqual(report["promotionEffect"], "NONE")

    def test_mascot_suffixes_resolve_without_fuzzy_matching(self):
        report = evaluate(
            learning=learning(),
            week4=week4(),
            shop_rows=[shop_row(
                away="Away Bears", home="Home Hawks", side="Home Hawks"
            )],
            challenger_cfg=self.ch,
            edge_cfg=self.edge,
            fetched_at="2026-09-21T12:00:00Z",
        )
        self.assertEqual(report["qualifiedCount"], 1)
        self.assertEqual(report["topFive"][0]["game"], "Away @ Home")
        self.assertEqual(report["topFive"][0]["side"], "Home")
        self.assertEqual(report["mappingFailures"], [])

    def test_soft_reference_never_qualifies(self):
        report = evaluate(
            learning=learning(),
            week4=week4(),
            shop_rows=[shop_row(reference="soft_median")],
            challenger_cfg=self.ch,
            edge_cfg=self.edge,
            fetched_at="2026-09-21T12:00:00Z",
        )
        self.assertEqual(report["qualifiedCount"], 0)
        self.assertIn(
            "PINNACLE_REFERENCE_REQUIRED",
            report["inspectedLiveRows"][0]["exclusions"],
        )

    def test_different_spread_never_qualifies(self):
        report = evaluate(
            learning=learning(),
            week4=week4(),
            shop_rows=[shop_row(reason_codes=[reasons.LINE_MISMATCH, reasons.NO_EVIDENCE])],
            challenger_cfg=self.ch,
            edge_cfg=self.edge,
            fetched_at="2026-09-21T12:00:00Z",
        )
        self.assertEqual(report["qualifiedCount"], 0)
        self.assertIn(
            reasons.LINE_MISMATCH,
            report["inspectedLiveRows"][0]["exclusions"],
        )

    def test_live_price_needs_positive_market_relative_ev(self):
        report = evaluate(
            learning=learning(),
            week4=week4(),
            shop_rows=[shop_row(ev=0.001)],
            challenger_cfg=self.ch,
            edge_cfg=self.edge,
            fetched_at="2026-09-21T12:00:00Z",
        )
        self.assertEqual(report["qualifiedCount"], 0)
        self.assertIn(
            "LIVE_PRICE_EV_BELOW_FLOOR",
            report["inspectedLiveRows"][0]["exclusions"],
        )

    def test_candidate_expires_after_historical_movement_budget_is_spent(self):
        # Home signal opened -3.0. A Pinnacle reference of -5.0 means two
        # points of favorable movement, larger than the synthetic +1.0 CLV
        # budget learned above.
        report = evaluate(
            learning=learning(),
            week4=week4(),
            shop_rows=[shop_row(ref_line=-5.0, venue_line=-5.0)],
            challenger_cfg=self.ch,
            edge_cfg=self.edge,
            fetched_at="2026-09-21T12:00:00Z",
        )
        self.assertEqual(report["qualifiedCount"], 0)
        self.assertIn(
            "HISTORICAL_MOVEMENT_BUDGET_SPENT",
            report["inspectedLiveRows"][0]["exclusions"],
        )


if __name__ == "__main__":
    unittest.main()
