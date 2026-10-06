from __future__ import annotations

import gzip
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cfb_edge.saturday_savant import (
    CRAWL_DELAY_SECONDS,
    SCHEDULE_URL,
    capture_upcoming,
    parse_game_page,
)

NOW = datetime(2026, 10, 6, 16, 0, tzinfo=timezone.utc)
GAME_PAGE = b"""<!doctype html><html><body>
<script type="application/ld+json">
{"@type":"SportsEvent","competitor":[{"name":"Southern Miss"},{"name":"Troy"}],
"mainEntityOfPage":"https://saturdaysavant.com/game/401871090",
"startDate":"2026-10-06T20:00:00-04:00"}
</script>
<header class="gx">
 <div class="gx-side gx-side--away"><a class="gx-name">Southern Miss</a>
  <b class="gx-num gx-num--pct">20<small>%</small></b></div>
 <div class="gx-side gx-side--home"><a class="gx-name">Troy</a>
  <b class="gx-num gx-num--pct">80<small>%</small></b></div>
 <div class="gx-cap-aside"><span>locked before kickoff</span></div>
 <div class="gx-facts"><span><span class="gx-k">Margin</span>
  <b>TROY by ~12</b></span><span><span class="gx-k">Vegas</span>
  <b>TROY -9.5</b></span></div>
</header></body></html>"""
SCHEDULE_PAGE = b"""<html><body>
<a class="game-card-v2 is-upcoming" href="/game/401871090">game</a>
<a class="game-card-v2 is-final" href="/game/401870000">old</a>
</body></html>"""


class SaturdaySavantTests(unittest.TestCase):
    def test_parses_probability_margin_teams_and_timezone(self):
        row = parse_game_page(
            GAME_PAGE,
            game_id="401871090",
            source_url="https://saturdaysavant.com/game/401871090",
            observed_at=NOW,
        )
        self.assertEqual(row["canonicalGameId"], "cfbd:401871090")
        self.assertEqual(row["awayTeam"], "Southern Miss")
        self.assertEqual(row["homeTeam"], "Troy")
        self.assertEqual(row["homeWinProbability"], 0.8)
        self.assertEqual(row["awayWinProbability"], 0.2)
        self.assertEqual(row["approxExpectedHomeMargin"], 12.0)
        self.assertEqual(row["kickoffAt"], "2026-10-07T00:00:00+00:00")

    def test_capture_archives_exact_pages_and_obeys_crawl_delay(self):
        pages = {
            SCHEDULE_URL: SCHEDULE_PAGE,
            "https://saturdaysavant.com/game/401871090": GAME_PAGE,
        }
        requested = []
        sleeps = []
        with tempfile.TemporaryDirectory() as tmp:
            report = capture_upcoming(
                archive_root=tmp,
                now=lambda: NOW,
                fetch=lambda url: requested.append(url) or pages[url],
                sleep=sleeps.append,
            )
            self.assertEqual(report["status"], "CAPTURED")
            self.assertEqual(len(report["forecasts"]), 1)
            self.assertEqual(len(report["sourceManifest"]), 2)
            self.assertEqual(
                requested,
                [SCHEDULE_URL, "https://saturdaysavant.com/game/401871090"],
            )
            self.assertEqual(sleeps, [CRAWL_DELAY_SECONDS])
            for item in report["sourceManifest"]:
                raw = gzip.decompress((Path(tmp) / item["archivePath"]).read_bytes())
                self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"])
            self.assertEqual(report["model"]["deliveryEligible"], False)
            self.assertEqual(report["model"]["promotionEffect"], "NONE")
            self.assertEqual(report["model"]["stakeUnits"], 0)
            self.assertIsNone(report["evaluation"]["probabilityMetrics"])
            self.assertEqual(report["evaluation"]["spreadMarketComparison"], "NOT_EVALUATED")

    def test_forecasts_observed_after_kickoff_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = capture_upcoming(
                archive_root=tmp,
                now=lambda: NOW + timedelta(hours=9),
                fetch=lambda url: SCHEDULE_PAGE if url == SCHEDULE_URL else GAME_PAGE,
                sleep=lambda _: None,
            )
        self.assertEqual(report["status"], "NO_PROSPECTIVE_FORECASTS")
        self.assertEqual(report["forecasts"], [])
        self.assertEqual(
            report["excluded"][0]["reason"],
            "FORECAST_CAPTURED_AT_OR_AFTER_KICKOFF",
        )

    def test_no_locked_forecast_is_rejected(self):
        bad = GAME_PAGE.replace(b"locked before kickoff", b"Forecast")
        with self.assertRaisesRegex(ValueError, "lock claim is missing"):
            parse_game_page(
                bad,
                game_id="401871090",
                source_url="https://saturdaysavant.com/game/401871090",
                observed_at=NOW,
            )

    def test_probability_sum_must_be_coherent(self):
        bad = GAME_PAGE.replace(b">80<small>%", b">70<small>%")
        with self.assertRaisesRegex(ValueError, "do not sum to 1"):
            parse_game_page(
                bad,
                game_id="401871090",
                source_url="https://saturdaysavant.com/game/401871090",
                observed_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
