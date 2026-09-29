from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cfb_edge.prospective_open import CONTRACT, update


def quote(*, seen="2026-10-04T12:01:00Z", venue="2026-10-04T12:00:00Z",
          line=-3.5, kickoff="2026-10-10T16:00:00Z"):
    return {
        "game":"Away @ Home",
        "book":"kalshi",
        "market":"spread",
        "line":line,
        "price":None,
        "seen_at":seen,
        "commence_time":kickoff,
        "venue_open_time":venue,
        "event_ticker":"E",
        "market_tickers":["M1","M2"],
        "quote_inputs":[
            {"ticker":"M1","strike":2.5,"team":"Home","probability":0.56,
             "yesBid":0.51,"yesAsk":0.61,"openTime":venue},
            {"ticker":"M2","strike":3.5,"team":"Home","probability":0.44,
             "yesBid":0.39,"yesAsk":0.49,"openTime":venue},
        ],
        "poll_time":seen,
        "first_valid_two_sided_quote_time":seen,
        "code_revision":"abc",
    }


class ProspectiveOpenTests(unittest.TestCase):
    def setUp(self):
        self.slate=[{"game":"Away @ Home","kickoff":"2026-10-10T16:00:00Z"}]
        self.sha="a"*64
        self.now=datetime(2026,10,4,12,1,5,tzinfo=timezone.utc)

    def test_true_open_locks_from_live_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            r=update(
                slate=self.slate, slate_sha256=self.sha,
                snapshot={"polled_at":"2026-10-04T12:01:05Z","quotes":[quote()]},
                existing=None, evidence_dir=Path(td)/"evidence",
                revision="abc", generated_at=self.now,
            )
        self.assertEqual(r["contract"],CONTRACT)
        self.assertFalse(r["historicalRecoveryAllowed"])
        self.assertEqual(r["summary"]["capturedTrueOpenRows"],1)
        row=r["rows"][0]
        self.assertEqual(row["state"],"CAPTURED_TRUE_OPEN")
        self.assertTrue(row["auditGrade"])
        self.assertEqual(row["openLagSeconds"],60)
        self.assertEqual(row["source"],"LIVE_KALSHI_POLL")
        self.assertFalse(row["historicalRecoveryUsed"])
        self.assertEqual(len(row["evidenceSha256"]),64)

    def test_late_first_valid_quote_locks_as_missed(self):
        q=quote(seen="2026-10-04T12:16:01Z")
        with tempfile.TemporaryDirectory() as td:
            r=update(
                slate=self.slate, slate_sha256=self.sha,
                snapshot={"polled_at":"2026-10-04T12:16:02Z","quotes":[q]},
                existing=None, evidence_dir=Path(td)/"evidence",
                generated_at=datetime(2026,10,4,12,16,2,tzinfo=timezone.utc),
            )
        self.assertEqual(r["rows"][0]["state"],"MISSED_TRUE_OPEN_WINDOW")
        self.assertFalse(r["rows"][0]["auditGrade"])
        self.assertGreater(r["rows"][0]["openLagSeconds"],900)

    def test_terminal_miss_can_never_be_upgraded(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"evidence"
            first=update(
                slate=self.slate, slate_sha256=self.sha,
                snapshot={"polled_at":"2026-10-04T12:16:02Z",
                          "quotes":[quote(seen="2026-10-04T12:16:01Z")]},
                existing=None, evidence_dir=root,
                generated_at=datetime(2026,10,4,12,16,2,tzinfo=timezone.utc),
            )
            second=update(
                slate=self.slate, slate_sha256=self.sha,
                snapshot={"polled_at":"2026-10-04T12:01:02Z","quotes":[quote()]},
                existing=first, evidence_dir=root,
                generated_at=datetime(2026,10,4,12,17,0,tzinfo=timezone.utc),
            )
        self.assertEqual(second["rows"][0]["state"],"MISSED_TRUE_OPEN_WINDOW")
        self.assertEqual(second["rows"][0]["evidenceSha256"],
                         first["rows"][0]["evidenceSha256"])
        self.assertEqual(second["summary"]["capturedTrueOpenRows"],0)

    def test_incomplete_provenance_stays_unlocked(self):
        q=quote()
        q["market_tickers"]=["M1"]
        with tempfile.TemporaryDirectory() as td:
            r=update(
                slate=self.slate, slate_sha256=self.sha,
                snapshot={"polled_at":"2026-10-04T12:01:05Z","quotes":[q]},
                existing=None, evidence_dir=Path(td)/"evidence",
                generated_at=self.now,
            )
        row=r["rows"][0]
        self.assertEqual(row["state"],"PROVENANCE_INCOMPLETE")
        self.assertFalse(row["locked"])
        self.assertEqual(r["summary"]["terminalRows"],0)

    def test_different_slate_cannot_reuse_existing_status(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                update(
                    slate=self.slate, slate_sha256=self.sha,
                    snapshot={"quotes":[]},
                    existing={"slateSha256":"b"*64,"rows":[]},
                    evidence_dir=Path(td)/"evidence",
                    generated_at=self.now,
                )


if __name__=="__main__":
    unittest.main()
