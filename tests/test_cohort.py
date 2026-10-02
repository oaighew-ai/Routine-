from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cfb_edge.cohort import (
    CohortError,
    build_slate,
    coverage_report,
    load,
    provider_week,
    status,
    validate_slate_rows,
)
from cfb_edge.slate import SlateRow


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "week5_cohort.json"


class CohortIdentityTests(unittest.TestCase):
    def test_product_week5_maps_to_provider_week4(self):
        c = load(CONFIG)
        self.assertEqual(c["productWeek"], 5)
        self.assertEqual(provider_week(c, "cfbfastR"), 4)
        self.assertEqual(provider_week(c, "collegefootballdata"), 4)

    def test_sep25_26_is_valid_and_october_provider_week5_is_rejected(self):
        c = load(CONFIG)
        good = validate_slate_rows(
            [
                {"game": "A @ H", "kickoff": "2026-09-25T20:00:00Z"},
                {"game": "B @ J", "kickoff": "2026-09-26T23:30:00Z"},
                {"game": "C @ K", "kickoff": "2026-09-27T03:30:00Z"},
            ],
            c,
        )
        self.assertTrue(good["valid"], good["errors"])

        bad = validate_slate_rows(
            [{"game": "A @ H", "kickoff": "2026-10-03T19:30:00Z"}],
            c,
        )
        self.assertFalse(bad["valid"])
        self.assertTrue(any("OUTSIDE_GAME_WINDOW" in x for x in bad["errors"]))

    def test_capture_and_prospective_windows_are_explicit(self):
        c = load(CONFIG)
        t = datetime(2026, 9, 23, 1, tzinfo=timezone.utc)
        self.assertTrue(status(c, now=t, mode="capture")["run"])
        self.assertTrue(status(c, now=t, mode="prospective")["run"])
        after_first_kick = datetime(2026, 9, 25, 21, tzinfo=timezone.utc)
        self.assertFalse(status(c, now=after_first_kick, mode="prospective")["run"])
        self.assertTrue(status(c, now=after_first_kick, mode="game")["run"])

    def test_build_uses_provider_week4_and_filters_to_canonical_window(self):
        c = load(CONFIG)
        seen = []
        def builder(season, week):
            seen.append((season, week))
            return [
                SlateRow("A @ H", 1.0, False, "2026-09-26", "2026-09-26T16:00:00Z"),
                SlateRow("Wrong @ Week", 1.0, False, "2026-10-03", "2026-10-03T16:00:00Z"),
            ]
        rows = build_slate(c, builder=builder)
        self.assertEqual(seen, [(2026, 4)])
        self.assertEqual([r.game for r in rows], ["A @ H"])


class ProviderWeekCoverageTests(unittest.TestCase):
    """A cohort may not drop games of its own week without saying so (D41)."""

    WEEK6 = ROOT / "config" / "br2_active_cohort.json"

    @staticmethod
    def provider_week5(season, week):
        return [
            # The two Thursday Oct 1 games the frozen Week 6 window starts after.
            SlateRow("Western Kentucky @ New Mexico State", 1.0, False, "2026-10-01", "2026-10-02T00:00:00Z"),
            SlateRow("North Texas @ Tulsa", 1.0, False, "2026-10-01", "2026-10-02T01:00:00Z"),
            SlateRow("Friday @ Night", 1.0, False, "2026-10-02", "2026-10-02T23:30:00Z"),
            SlateRow("Saturday @ Noon", 1.0, False, "2026-10-03", "2026-10-03T16:00:00Z"),
            # A provider mislabel from the following week.
            SlateRow("Next @ Week", 1.0, False, "2026-10-10", "2026-10-10T16:00:00Z"),
        ]

    def test_the_frozen_week6_contract_still_builds_and_reports_what_it_drops(self):
        c = load(self.WEEK6)
        rows = build_slate(c, builder=self.provider_week5)
        self.assertEqual([r.game for r in rows], ["Friday @ Night", "Saturday @ Noon"])
        report = coverage_report(self.provider_week5(2026, 5), c)
        self.assertFalse(report["requireFullProviderWeek"])
        self.assertFalse(report["complete"])
        self.assertEqual([x["game"] for x in report["sameWeekOutsideWindow"]],
                         ["Western Kentucky @ New Mexico State", "North Texas @ Tulsa"])
        self.assertEqual(report["otherWeekRowsDropped"], 1)

    def test_a_strict_contract_refuses_to_drop_its_own_thursday_games(self):
        c = dict(load(self.WEEK6), coverage={"requireFullProviderWeek": True})
        with self.assertRaises(CohortError) as ctx:
            build_slate(c, builder=self.provider_week5)
        self.assertIn("Western Kentucky @ New Mexico State", str(ctx.exception))
        self.assertIn("North Texas @ Tulsa", str(ctx.exception))

    def test_a_strict_contract_covering_the_whole_week_builds(self):
        c = dict(load(self.WEEK6), coverage={"requireFullProviderWeek": True},
                 gameWindow={"startsAt": "2026-10-01T16:00:00Z", "endsAt": "2026-10-04T12:00:00Z"})
        rows = build_slate(c, builder=self.provider_week5)
        self.assertEqual(len(rows), 4)
        self.assertNotIn("Next @ Week", [r.game for r in rows])

    def test_a_named_exclusion_needs_a_reason_and_is_then_allowed(self):
        excluded = [{"game": "Western Kentucky @ New Mexico State", "reason": "no Kalshi market"},
                    {"game": "North Texas @ Tulsa", "reason": "no Kalshi market"}]
        c = dict(load(self.WEEK6), coverage={"requireFullProviderWeek": True, "excludedGames": excluded})
        self.assertEqual(len(build_slate(c, builder=self.provider_week5)), 2)
        bad = dict(load(self.WEEK6), coverage={"requireFullProviderWeek": True,
                                                "excludedGames": [{"game": "North Texas @ Tulsa"}]})
        with self.assertRaises(CohortError):
            build_slate(bad, builder=self.provider_week5)


if __name__ == "__main__":
    unittest.main()
