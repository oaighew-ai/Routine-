"""Eastern time and portable formatting on a host with no tz database.

These tests matter more than their size suggests. On CI the tz database is
present, so `eastern.EASTERN` is a real `ZoneInfo` and the fallback below never
runs. A fallback that only executes on the one platform CI does not use is
untested by construction, which is how `card_render` came to carry a hardcoded
`-4` hours for years: correct in EDT, an hour wrong in EST, and no test could
tell because the branch was never taken.

So each test here forces the fallback by setting `EASTERN` to None, and the
expected offsets are checked against dates where EDT and EST differ.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from cfb_edge import eastern


class ForceFallback(unittest.TestCase):
    """Run the no-tz-database path whatever the host actually has."""

    def setUp(self):
        self._saved = eastern.EASTERN
        eastern.EASTERN = None

    def tearDown(self):
        eastern.EASTERN = self._saved


class TestEasternOffsets(ForceFallback):
    def offset(self, *args) -> float:
        when = datetime(*args, tzinfo=timezone.utc)
        return eastern.eastern_for(when).utcoffset(when).total_seconds() / 3600.0

    def test_summer_is_edt(self):
        self.assertEqual(self.offset(2026, 9, 22, 13, 0), -4.0)

    def test_winter_is_est(self):
        """December matters: bowl and playoff numbers post then."""
        self.assertEqual(self.offset(2026, 12, 20, 13, 0), -5.0)

    def test_january_is_est(self):
        self.assertEqual(self.offset(2027, 1, 5, 13, 0), -5.0)

    def test_the_spring_transition_is_the_second_sunday_in_march(self):
        # 2026-03-08 is the second Sunday. 07:00 UTC is 02:00 EST.
        self.assertEqual(self.offset(2026, 3, 8, 6, 59), -5.0)
        self.assertEqual(self.offset(2026, 3, 8, 7, 0), -4.0)

    def test_the_autumn_transition_is_the_first_sunday_in_november(self):
        # 2026-11-01 is the first Sunday. 06:00 UTC is 02:00 EDT.
        self.assertEqual(self.offset(2026, 11, 1, 5, 59), -4.0)
        self.assertEqual(self.offset(2026, 11, 1, 6, 0), -5.0)

    def test_a_naive_datetime_is_refused_rather_than_assumed(self):
        with self.assertRaises(ValueError):
            eastern.eastern_for(datetime(2026, 9, 22, 13, 0))

    def test_it_agrees_with_the_tz_database_where_one_exists(self):
        """The fallback is only defensible if it matches the real answer."""
        eastern.EASTERN = self._saved
        if self._saved is None:
            self.skipTest("host has no tz database to compare against")
        for args in ((2026, 9, 22, 13, 0), (2026, 12, 20, 13, 0),
                     (2026, 11, 1, 5, 59), (2026, 11, 1, 6, 0),
                     (2026, 3, 8, 6, 59), (2026, 3, 8, 7, 0)):
            when = datetime(*args, tzinfo=timezone.utc)
            real = when.astimezone(self._saved).utcoffset()
            eastern.EASTERN = None
            mine = when.astimezone(eastern.eastern_for(when)).utcoffset()
            eastern.EASTERN = self._saved
            self.assertEqual(mine, real, f"disagreed at {when.isoformat()}")


class TestPortableStrftime(unittest.TestCase):
    """`%-d` and `%-I` are a glibc extension; Windows raises on them."""

    WHEN = datetime(2026, 10, 5, 9, 7, tzinfo=timezone.utc)

    def test_no_pad_day(self):
        self.assertEqual(eastern.strf(self.WHEN, "%b %-d"), "Oct 5")

    def test_no_pad_twelve_hour(self):
        self.assertEqual(eastern.strf(self.WHEN, "%-I:%M"), "9:07")

    def test_midnight_is_twelve_not_zero(self):
        at = self.WHEN.replace(hour=0)
        self.assertEqual(eastern.strf(at, "%-I"), "12")

    def test_noon_is_twelve(self):
        at = self.WHEN.replace(hour=12)
        self.assertEqual(eastern.strf(at, "%-I"), "12")

    def test_padded_directives_are_untouched(self):
        self.assertEqual(eastern.strf(self.WHEN, "%d %H"), "05 09")

    def test_a_format_with_no_no_pad_directive_is_passed_straight_through(self):
        self.assertEqual(eastern.strf(self.WHEN, "%Y-%m-%d"), "2026-10-05")

    def test_it_does_not_raise_where_strftime_would(self):
        """The actual bug: render() died here on Windows with ValueError."""
        self.assertEqual(eastern.strf(self.WHEN, "%a %b %-d, %-I:%M %p")[:10],
                         "Mon Oct 5,")


if __name__ == "__main__":
    unittest.main()
