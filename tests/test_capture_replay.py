"""A poll lost to a push race is replayed at its real time, never re-observed.

The open loop shares the evidence branch with other writers. When its push is
rejected it takes the branch as it is. Before this, that also threw away the
poll it had just recorded, so a market that opened in that poll was first seen
a poll later, or not at all if the next push lost too. Replaying the saved
snapshot keeps the original observation time, which is the only number a true
open is graded on.
"""

from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from cfb_edge import watch

SEEN = "2026-10-03T16:07:30+00:00"


def snapshot(polled_at: str = "2026-10-03T16:07:31+00:00", line: float = -6.5) -> dict:
    q = watch.Quote(game="Away @ Home", book="kalshi", market="spread", line=line, price=None,
                    seen_at=SEEN, commence_time="2026-10-10T16:00:00Z",
                    venue_open_time="2026-10-03T16:06:00+00:00",
                    event_ticker="KXNCAAFSPREAD-26OCT10AWYHOM",
                    market_tickers=("A", "B"), quote_inputs=({"strike": 5.5}, {"strike": 7.5}),
                    poll_time=SEEN, first_valid_two_sided_quote_time=SEEN, code_revision="abc")
    return {"schemaVersion": 1, "contract": "CFB_EDGE_LIVE_QUOTE_SNAPSHOT_V1",
            "polled_at": polled_at, "quotes": [q.__dict__]}


class TestReplay(unittest.TestCase):
    def test_replay_keeps_the_original_observation_time(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "opens.jsonl.gz"
            book = watch.OpeningBook.load(log)
            fresh, wrote = book.replay(snapshot())
            self.assertTrue(wrote)
            self.assertEqual(len(fresh), 1)
            reloaded = watch.OpeningBook.load(log)
            self.assertEqual(reloaded.first_seen()["Away @ Home"], SEEN)
            with gzip.open(log, "rt") as fh:
                rec = json.loads(fh.readline())
            self.assertEqual(rec["polled_at"], "2026-10-03T16:07:31+00:00")

    def test_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "opens.jsonl.gz"
            book = watch.OpeningBook.load(log)
            book.replay(snapshot())
            again = watch.OpeningBook.load(log)
            fresh, wrote = again.replay(snapshot())
            self.assertFalse(wrote)
            self.assertEqual(fresh, [])
            with gzip.open(log, "rt") as fh:
                self.assertEqual(sum(1 for line in fh if line.strip()), 1)

    def test_a_replayed_open_never_displaces_an_earlier_first_sighting(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "opens.jsonl.gz"
            book = watch.OpeningBook.load(log)
            early = snapshot(polled_at="2026-10-03T16:06:40+00:00", line=-6.0)
            early["quotes"][0]["seen_at"] = "2026-10-03T16:06:39+00:00"
            book.replay(early)
            book.replay(snapshot(line=-6.5))
            reloaded = watch.OpeningBook.load(log)
            self.assertEqual(reloaded.consensus_opens()["Away @ Home"], -6.0)

    def test_a_snapshot_without_a_poll_time_is_refused(self):
        bad = snapshot()
        bad.pop("polled_at")
        with tempfile.TemporaryDirectory() as d:
            book = watch.OpeningBook.load(Path(d) / "opens.jsonl.gz")
            with self.assertRaises(ValueError):
                book.replay(bad)

    def test_an_unknown_contract_is_refused(self):
        bad = snapshot()
        bad["contract"] = "SOMETHING_ELSE"
        with tempfile.TemporaryDirectory() as d:
            book = watch.OpeningBook.load(Path(d) / "opens.jsonl.gz")
            with self.assertRaises(ValueError):
                book.replay(bad)

    def test_cli_replays_without_a_slate_or_a_network(self):
        with tempfile.TemporaryDirectory() as d:
            snap = Path(d) / "s.json"
            snap.write_text(json.dumps(snapshot()))
            log, out = Path(d) / "opens.jsonl.gz", Path(d) / "opens.csv"
            rc = watch.main(["--log", str(log), "--replay-snapshot", str(snap), "--out", str(out)])
            self.assertEqual(rc, 0)
            self.assertIn("true_open", out.read_text())


class TestTheLoopReplaysInsteadOfDiscarding(unittest.TestCase):
    """The workflow must keep unpushed polls and replay them after a reset."""

    TEXT = (Path(__file__).resolve().parents[1] / ".github/workflows/capture-open-loop.yml").read_text()

    def test_unpushed_snapshots_are_kept(self):
        self.assertIn("UNPUSHED=", self.TEXT)
        self.assertIn('cp "$SNAPSHOT" "$UNPUSHED/', self.TEXT)

    def test_recovery_replays_them(self):
        self.assertIn("--replay-snapshot", self.TEXT)

    def test_recovery_no_longer_repolls_as_a_substitute(self):
        keep = self.TEXT.split("- name: Keep it", 1)[1]
        self.assertNotIn("--snapshot-out", keep)


if __name__ == "__main__":
    unittest.main()
