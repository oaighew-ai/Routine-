from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cfb_edge.cohort import (
    CohortError,
    build_slate,
    contracts_in,
    coverage_report,
    find_active,
    find_for_provider_week,
    football_saturday,
    load,
    propose,
    weekend_windows,
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

    def test_an_unnamed_drop_is_reported_loudly_and_does_not_stop_the_build(self):
        """One flexed kickoff must not cost the other games their capture, and
        must not pass unremarked either."""
        import contextlib
        import io

        c = dict(load(self.WEEK6), coverage={"requireFullProviderWeek": True})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rows = build_slate(c, builder=self.provider_week5)
        self.assertEqual([r.game for r in rows], ["Friday @ Night", "Saturday @ Noon"])
        self.assertIn("COVERAGE:", err.getvalue())
        self.assertIn("Western Kentucky @ New Mexico State", err.getvalue())
        self.assertIn("North Texas @ Tulsa", err.getvalue())
        self.assertFalse(coverage_report(self.provider_week5(2026, 5), c)["complete"])

    def test_a_contract_covering_the_whole_week_reports_complete(self):
        c = dict(load(self.WEEK6), coverage={"requireFullProviderWeek": True},
                 gameWindow={"startsAt": "2026-10-01T16:00:00Z", "endsAt": "2026-10-04T12:00:00Z"})
        rows = build_slate(c, builder=self.provider_week5)
        self.assertEqual(len(rows), 4)
        self.assertNotIn("Next @ Week", [r.game for r in rows])
        self.assertTrue(coverage_report(self.provider_week5(2026, 5), c)["complete"])

    def test_a_named_exclusion_needs_a_reason(self):
        import contextlib
        import io

        excluded = [{"game": "Western Kentucky @ New Mexico State", "reason": "no Kalshi market"},
                    {"game": "North Texas @ Tulsa", "reason": "no Kalshi market"}]
        c = dict(load(self.WEEK6), coverage={"requireFullProviderWeek": True, "excludedGames": excluded})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(len(build_slate(c, builder=self.provider_week5)), 2)
        self.assertEqual(err.getvalue(), "", "a named exclusion is not a coverage warning")
        self.assertTrue(coverage_report(self.provider_week5(2026, 5), c)["complete"])
        bad = dict(load(self.WEEK6), coverage={"requireFullProviderWeek": True,
                                                "excludedGames": [{"game": "North Texas @ Tulsa"}]})
        with self.assertRaises(CohortError):
            build_slate(bad, builder=self.provider_week5)


class ProposedContractTests(unittest.TestCase):
    """The next cohort is derived from the schedule, not typed (D41)."""

    WEEK6 = ROOT / "config" / "br2_active_cohort.json"

    @staticmethod
    def provider_week6():
        return [
            SlateRow("Southern Miss @ Troy", 1.0, False, "2026-10-06", "2026-10-07T00:00:00.000Z"),
            SlateRow("Thursday @ Night", 1.0, False, "2026-10-08", "2026-10-08T23:30:00.000Z"),
            SlateRow("Friday @ Night", 1.0, False, "2026-10-09", "2026-10-09T23:00:00.000Z"),
            SlateRow("Saturday @ Noon", 1.0, False, "2026-10-10", "2026-10-10T16:00:00.000Z"),
            SlateRow("Saturday @ Late", 1.0, False, "2026-10-10", "2026-10-11T02:30:00.000Z"),
        ]

    def test_the_rule_reproduces_the_registered_week6_windows(self):
        registered = load(self.WEEK6)
        saturday = datetime(2026, 10, 3, tzinfo=timezone.utc)
        for key, value in weekend_windows(saturday).items():
            self.assertEqual(value, registered[key], key)

    def test_the_anchor_is_the_saturday_even_when_the_last_game_reads_as_sunday(self):
        kickoffs = [datetime(2026, 10, 7, 0, tzinfo=timezone.utc),
                    datetime(2026, 10, 11, 2, 30, tzinfo=timezone.utc)]
        self.assertEqual(football_saturday(kickoffs), datetime(2026, 10, 10, tzinfo=timezone.utc))

    def test_a_proposed_contract_names_every_game_it_leaves_out(self):
        c = propose(2026, 6, self.provider_week6())
        self.assertEqual(c["cohortId"], "CFB_2026_PRODUCT_WEEK7")
        self.assertEqual(c["providerWeeks"], {"cfbfastR": 6, "collegefootballdata": 6})
        self.assertEqual(c["gameWindow"], {"startsAt": "2026-10-09T23:00:00Z",
                                           "endsAt": "2026-10-11T12:00:00Z"})
        self.assertEqual([x["game"] for x in c["coverage"]["excludedGames"]],
                         ["Southern Miss @ Troy", "Thursday @ Night"])
        self.assertTrue(all(x["reason"] for x in c["coverage"]["excludedGames"]))
        report = coverage_report(self.provider_week6(), c)
        self.assertTrue(report["complete"])
        self.assertEqual(report["cohortRows"], 3)

    def test_a_proposed_contract_loads_and_builds(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.json"
            path.write_text(json.dumps(propose(2026, 6, self.provider_week6())))
            rows = build_slate(load(path), builder=lambda s, w: self.provider_week6())
            self.assertEqual(len(rows), 3)

    def test_a_season_without_a_registered_offset_needs_the_product_week(self):
        with self.assertRaises(CohortError):
            propose(2031, 6, self.provider_week6())
        self.assertEqual(propose(2031, 6, self.provider_week6(), product_week=6)["productWeek"], 6)


class RegisteredContractTests(unittest.TestCase):
    """What is checked in under config/cohorts/ must resolve one week at a time."""

    DIR = ROOT / "config" / "cohorts"

    def test_every_registered_contract_loads_and_names_its_exclusions(self):
        found = contracts_in(self.DIR)
        self.assertTrue(found, "no registered contracts")
        for path, c in found:
            with self.subTest(path=path.name):
                self.assertEqual(path.stem, c["cohortId"])
                self.assertTrue(c["coverage"]["requireFullProviderWeek"])
                for x in c["coverage"]["excludedGames"]:
                    self.assertTrue(x["game"] and x["reason"])
                saturday = datetime.fromisoformat(
                    c["gameWindow"]["startsAt"].replace("Z", "+00:00")) + timedelta(hours=1)
                self.assertEqual(saturday.weekday(), 5)
                self.assertEqual({k: c[k] for k in ("gameWindow", "captureWindow",
                                                    "prospectiveWindow")},
                                 weekend_windows(saturday))

    def test_product_weeks_are_consecutive_and_provider_weeks_follow(self):
        cs = sorted((c for _, c in contracts_in(self.DIR)), key=lambda c: c["productWeek"])
        weeks = [c["productWeek"] for c in cs]
        self.assertEqual(weeks, list(range(weeks[0], weeks[0] + len(weeks))))
        for c in cs:
            self.assertEqual(c["providerWeeks"]["cfbfastR"], c["productWeek"] - 1)

    def test_exactly_one_contract_is_open_at_every_weekday_capture_slot(self):
        """13:30 UTC, Monday to Friday, is when the feature capture asks which
        cohort it is working for. Two answers would file one week's evidence
        under another's name; none would stop the capture."""
        cs = sorted((c for _, c in contracts_in(self.DIR)), key=lambda c: c["productWeek"])
        first = datetime.fromisoformat(cs[0]["prospectiveWindow"]["startsAt"].replace("Z", "+00:00"))
        last = datetime.fromisoformat(cs[-1]["prospectiveWindow"]["endsAt"].replace("Z", "+00:00"))
        slot = first.replace(hour=13, minute=30)
        checked = 0
        while slot < last:
            if slot.weekday() < 5:
                path = find_active(self.DIR, now=slot, mode="prospective")
                self.assertIsNotNone(path, f"no contract open at {slot}")
                checked += 1
            slot += timedelta(days=1)
        self.assertEqual(checked, 5 * len(cs))

    def test_the_resolver_refuses_two_open_contracts(self):
        with tempfile.TemporaryDirectory() as d:
            c = propose(2026, 6, ProposedContractTests.provider_week6())
            (Path(d) / "a.json").write_text(json.dumps(c))
            (Path(d) / "b.json").write_text(json.dumps(dict(c, cohortId="CFB_2026_OTHER")))
            with self.assertRaises(CohortError):
                find_active(d, now=datetime(2026, 10, 6, 13, 30, tzinfo=timezone.utc))

    def test_the_fallback_is_used_only_when_nothing_is_open(self):
        fallback = ROOT / "config" / "br2_active_cohort.json"
        before = datetime(2026, 10, 2, 13, 30, tzinfo=timezone.utc)   # Week 6 still prospective
        self.assertEqual(find_active(self.DIR, now=before, fallback=fallback), fallback)
        monday = datetime(2026, 10, 5, 13, 30, tzinfo=timezone.utc)
        self.assertEqual(find_active(self.DIR, now=monday, fallback=fallback).name,
                         "CFB_2026_PRODUCT_WEEK7.json")

    def test_a_provider_week_finds_its_contract(self):
        paths = [self.DIR, ROOT / "config" / "br2_active_cohort.json",
                 ROOT / "config" / "week5_cohort.json"]
        self.assertEqual(find_for_provider_week(paths, 2026, 5).name, "br2_active_cohort.json")
        self.assertEqual(find_for_provider_week(paths, 2026, 4).name, "week5_cohort.json")
        self.assertEqual(find_for_provider_week(paths, 2026, 6).name, "CFB_2026_PRODUCT_WEEK7.json")
        self.assertIsNone(find_for_provider_week(paths, 2026, 1))


if __name__ == "__main__":
    unittest.main()
