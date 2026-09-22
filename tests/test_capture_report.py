import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from cfb_edge.capture_report import build, render


class CaptureReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name)/"poll.jsonl"
        self.slate = Path(self.temp.name)/"slate.csv"
        self.slate.write_text('game,projected_margin\nAway @ Home,3\n')
        self.now = datetime(2026, 9, 20, 22, 5, tzinfo=timezone.utc)
        self.quote = dict(
            game="Away @ Home", market="spread", book="kalshi", line=-3.5,
            seen_at="2026-09-20T22:04:00Z", commence_time="2026-09-26T16:00:00Z",
            venue_open_time="2026-09-20T22:00:00Z",
            event_ticker="E", market_tickers=["M1", "M2"],
            first_valid_two_sided_quote_time="2026-09-20T22:04:00Z",
            poll_time="2026-09-20T22:04:00Z", code_revision="test",
        )
        self.write([self.quote])

    def write(self, quotes):
        self.log.write_text(json.dumps(dict(polled_at="2026-09-20T22:04:10Z", quotes=quotes))+"\n")

    def test_fresh_is_not_deliverable(self):
        r = build(self.log, self.slate, self.now)
        self.assertTrue(r["freshAtGeneration"])
        self.assertFalse(r["allowDelivery"])
        self.assertEqual(r["games"][0]["stakeUnits"], 0)
        self.assertEqual(r["games"][0]["observations"][0]["derivedHomeLine"], -3.5)
        self.assertTrue(r["games"][0]["auditGradeOpen"])
        self.assertEqual(r["games"][0]["openClassification"], "true_open")
        self.assertEqual(r["openEvidence"]["auditGradeCount"], 1)

    def test_empty_poll_does_not_revive_old_quotes(self):
        with self.log.open("a") as f:
            f.write(json.dumps(dict(polled_at="2026-09-20T22:05:00Z", quotes=[]))+"\n")
        self.assertIn("ABSENT_FROM_LATEST_POLL", build(self.log, self.slate, self.now)["games"][0]["exclusions"])

    def test_failed_capture_cannot_reuse_fresh_log(self):
        self.assertFalse(build(self.log, self.slate, self.now, "failure")["freshAtGeneration"])

    def test_invalid_future_stale_and_kickoff(self):
        for value in ["invalid", "2026-09-20T23:00:00Z", "2026-09-20T21:00:00Z"]:
            self.quote["seen_at"] = value
            self.write([self.quote])
            self.assertIn("QUOTE_STALE_OR_INVALID", build(self.log, self.slate, self.now)["games"][0]["exclusions"])
        self.quote["commence_time"] = None
        self.write([self.quote])
        self.assertIn("KICKOFF_UNVERIFIED", build(self.log, self.slate, self.now)["games"][0]["exclusions"])

    def test_snapshot_escapes_imported_html(self):
        r = build(self.log, self.slate, self.now)
        r["games"][0]["game"] = '<script>alert(1)</script>'
        self.assertNotIn('<script>', render(r))

    def test_hash_changes_and_missing_inputs(self):
        before = build(self.log, self.slate, self.now)["logSha256"]
        self.write([])
        self.assertNotEqual(before, build(self.log, self.slate, self.now)["logSha256"])
        self.log.unlink()
        self.assertFalse(build(self.log, self.slate, self.now)["freshAtGeneration"])
