from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from cfb_edge.week5_capture_health import build


class Week5CaptureHealthTests(unittest.TestCase):
    def write_csv(self, path, fields, rows):
        with path.open("w", newline="", encoding="utf-8") as fh:
            w=csv.DictWriter(fh, fieldnames=fields)
            w.writeheader(); w.writerows(rows)

    def test_true_open_requires_full_replay_lineage(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); slate=root/"slate.csv"; opens=root/"opens.csv"
            self.write_csv(slate,["game","kickoff"],[{"game":"A @ H","kickoff":"2026-09-26T16:00:00Z"}])
            fields=["game","opening_line","source","first_seen","venue_open_time","open_lag_seconds",
                    "event_ticker","market_tickers","first_valid_two_sided_quote_time","poll_time","code_revision"]
            self.write_csv(opens,fields,[{
                "game":"A @ H","opening_line":"-3","source":"true_open",
                "first_seen":"2026-09-20T20:00:00Z","venue_open_time":"2026-09-20T19:59:00Z",
                "open_lag_seconds":"60","event_ticker":"E","market_tickers":"M1|M2",
                "first_valid_two_sided_quote_time":"2026-09-20T20:00:00Z",
                "poll_time":"2026-09-20T20:00:02Z","code_revision":"abc",
            }])
            r=build(slate,opens)
            self.assertEqual(r["status"],"AUDIT_GRADE_CAPTURE_COMPLETE")
            self.assertTrue(r["rows"][0]["provenanceComplete"])
            self.assertTrue(r["rows"][0]["evidenceSha256"])

    def test_true_open_missing_contract_tickers_is_integrity_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); slate=root/"slate.csv"; opens=root/"opens.csv"
            self.write_csv(slate,["game","kickoff"],[{"game":"A @ H","kickoff":"2026-09-26T16:00:00Z"}])
            fields=["game","opening_line","source","first_seen","venue_open_time","open_lag_seconds",
                    "event_ticker","market_tickers","first_valid_two_sided_quote_time","poll_time","code_revision"]
            self.write_csv(opens,fields,[{
                "game":"A @ H","opening_line":"-3","source":"true_open",
                "first_seen":"2026-09-20T20:00:00Z","venue_open_time":"2026-09-20T19:59:00Z",
                "open_lag_seconds":"60","event_ticker":"E","market_tickers":"",
                "first_valid_two_sided_quote_time":"2026-09-20T20:00:00Z",
                "poll_time":"2026-09-20T20:00:02Z","code_revision":"abc",
            }])
            r=build(slate,opens)
            self.assertEqual(r["status"],"INTEGRITY_FAILURE")
            self.assertIn("TRUE_OPEN_MISSING_MARKET_TICKERS",r["rows"][0]["integrityIssues"])


if __name__=="__main__":
    unittest.main()
