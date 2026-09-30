"""Run the open loop's own push-recovery shell against real git repositories.

`persist` and `replay_unpushed` are extracted verbatim from
`.github/workflows/capture-open-loop.yml`, so this exercises the code that runs
on the runner, not a paraphrase of it. Another writer moves `capture-data`
between the loop's commit and its push; the loop must end with both that
writer's commit and its own poll, at the poll's original observation time.

Local file remotes only: no sockets. Skipped where git or bash is missing.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/capture-open-loop.yml"
SEEN = "2026-10-03T16:07:30+00:00"
POLLED = "2026-10-03T16:07:31+00:00"


def _functions() -> str:
    text = WORKFLOW.read_text()
    start = text.index('          UNPUSHED="$RUNNER_TEMP/unpushed"')
    end = text.index("          while [ \"$(date -u +%s)\" -lt \"$deadline\" ]; do")
    body = text[start:end]
    return "\n".join(line[10:] if line.startswith(" " * 10) else line for line in body.splitlines())


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
                               "GIT_CONFIG_SYSTEM": "/dev/null"}).stdout


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "needs git and bash")
class TestPushRecovery(unittest.TestCase):
    def test_a_rejected_push_keeps_both_writers_and_the_real_poll_time(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote.git"
            _git(d, "init", "-q", "--bare", "-b", "capture-data", str(remote))
            seed = d / "seed"
            _git(d, "clone", "-q", str(remote), str(seed))
            for args in (("config", "user.email", "t@t"), ("config", "user.name", "t"),
                         ("config", "commit.gpgsign", "false"), ("checkout", "-q", "-b", "capture-data")):
                _git(seed, *args)
            (seed / "data").mkdir()
            (seed / "data" / "slate_current.csv").write_text(
                "game,projected_margin,side,posted_line,total,kickoff\n"
                "Away @ Home,-3.0,,,52.0,2026-10-10T16:00:00.000Z\n")
            _git(seed, "add", "-A")
            _git(seed, "commit", "-q", "-m", "seed")
            _git(seed, "push", "-q", "origin", "capture-data")

            work = d / "work"
            work.mkdir()
            cap = work / "capture-data"
            _git(d, "clone", "-q", "-b", "capture-data", str(remote), str(cap))
            for args in (("config", "user.email", "t@t"), ("config", "user.name", "t"),
                         ("config", "commit.gpgsign", "false")):
                _git(cap, *args)

            # The loop polled once: it saved the snapshot and appended the log.
            unpushed = d / "runner" / "unpushed"
            unpushed.mkdir(parents=True)
            quote = {"game": "Away @ Home", "book": "kalshi", "market": "spread", "line": -6.5,
                     "price": None, "seen_at": SEEN, "commence_time": "2026-10-10T16:00:00.000Z",
                     "venue_open_time": "2026-10-03T16:06:00+00:00",
                     "event_ticker": "KXNCAAFSPREAD-26OCT10AWYHOM", "market_tickers": ["A", "B"],
                     "quote_inputs": [{"strike": 5.5}, {"strike": 7.5}], "poll_time": SEEN,
                     "first_valid_two_sided_quote_time": SEEN, "code_revision": "abc"}
            snap = {"schemaVersion": 1, "contract": "CFB_EDGE_LIVE_QUOTE_SNAPSHOT_V1",
                    "polled_at": POLLED, "quotes": [quote]}
            (unpushed / "1.json").write_text(json.dumps(snap))
            env = {**os.environ, "PYTHONPATH": str(ROOT), "RUNNER_TEMP": str(d / "runner"),
                   "GITHUB_SHA": "deadbeef", "GIT_CONFIG_GLOBAL": "/dev/null",
                   "GIT_CONFIG_SYSTEM": "/dev/null"}
            subprocess.run([sys.executable, "-m", "cfb_edge.watch",
                            "--log", str(cap / "data" / "opens.jsonl.gz"),
                            "--out", str(cap / "data" / "opens.csv"),
                            "--replay-snapshot", str(unpushed / "1.json")],
                           cwd=work, check=True, capture_output=True, env=env)

            # Meanwhile another workflow moves the branch.
            other = d / "other"
            _git(d, "clone", "-q", "-b", "capture-data", str(remote), str(other))
            for args in (("config", "user.email", "o@o"), ("config", "user.name", "o"),
                         ("config", "commit.gpgsign", "false")):
                _git(other, *args)
            (other / "data" / "bridge.json").write_text("{}\n")
            _git(other, "add", "-A")
            _git(other, "commit", "-q", "-m", "bridge")
            _git(other, "push", "-q", "origin", "capture-data")

            fn = _functions().replace("${{ steps.slate.outputs.cohort_id }}", "CFB_TEST")
            script = (f"set -u\ncd {work}\n{fn}\n"
                      "persist 'capture open loop: test' && echo PUSHED || echo NOTPUSHED\n")
            out = subprocess.run(["bash", "-c", script], cwd=work, capture_output=True, text=True, env=env)
            self.assertIn("PUSHED", out.stdout, out.stdout + out.stderr)
            self.assertNotIn("NOTPUSHED", out.stdout, out.stdout + out.stderr)

            final = d / "final"
            _git(d, "clone", "-q", "-b", "capture-data", str(remote), str(final))
            self.assertTrue((final / "data" / "bridge.json").exists(), "the other writer's commit survives")
            with gzip.open(final / "data" / "opens.jsonl.gz", "rt") as fh:
                polls = [json.loads(line) for line in fh if line.strip()]
            self.assertEqual([p["polled_at"] for p in polls], [POLLED], "exactly one copy, original time")
            status = json.loads((final / "data" / "prospective-open-status.json").read_text())
            row = next(r for r in status["rows"] if r["game"] == "Away @ Home")
            self.assertEqual(row["observedAt"], SEEN)
            self.assertEqual(list(unpushed.glob("*.json")), [], "the queue clears once pushed")


if __name__ == "__main__":
    unittest.main()
