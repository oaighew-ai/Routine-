"""The open loop hands itself to the next link instead of waiting for cron.

Scheduled launches in this repository arrive hours late, and a late launch is
a stretch of the release window with no poll. A link that ran its slot now
dispatches its successor with workflow_dispatch. These tests run the step's own
shell, lifted verbatim from `.github/workflows/capture-open-loop.yml`, against
a stand-in `gh`, so they exercise what the runner executes.

The guards matter as much as the dispatch: a link that fails at once must not
re-launch itself in a circle for four days.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from cfb_edge import watch

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/capture-open-loop.yml"
STEP = "      - name: Hand over to the next link"

INSIDE = "2026-10-03T16:00:00Z"     # Saturday afternoon
CLOSED = "2026-10-06T18:00:00Z"     # Tuesday, the minute the window shuts


def _step() -> str:
    text = WORKFLOW.read_text()
    block = text[text.index(STEP):]
    block = block[:block.index("\n      - name: ", 1)]
    return block


def _script() -> str:
    body = _step().split("        run: |\n", 1)[1]
    return "\n".join(line[10:] if line.startswith(" " * 10) else line for line in body.splitlines())


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class TestHandOver(unittest.TestCase):
    def run_step(self, *, at: str = INSIDE, polls: str = "320", failures: str = "2",
                 ran: int | None = 330 * 60, gh_exit: int = 0):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            calls = d / "gh-calls.txt"
            gh = d / "gh"
            gh.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{calls}"\nexit {gh_exit}\n')
            gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
            summary = d / "summary.md"
            env = {**os.environ, "PATH": f"{d}:{os.environ['PATH']}", "PYTHONPATH": str(ROOT),
                   "GH_TOKEN": "x", "POLLS": polls, "FAILURES": failures, "REF": "main",
                   "MIN_HANDOVER_SECONDS": "600", "HANDOVER_AT": at,
                   "GITHUB_STEP_SUMMARY": str(summary)}
            if ran is None:
                env["STARTED"] = ""
            else:
                env["STARTED"] = str(int(time.time()) - ran)
            out = subprocess.run(["bash", "-c", _script()], cwd=ROOT, env=env,
                                 capture_output=True, text=True)
            return (out.returncode, calls.read_text().splitlines() if calls.exists() else [],
                    summary.read_text() if summary.exists() else "", out.stdout + out.stderr)

    def test_a_full_link_inside_the_window_dispatches_the_next_one(self):
        rc, calls, summary, log = self.run_step()
        self.assertEqual(rc, 0, log)
        self.assertEqual(calls, ["workflow run capture-open-loop.yml --ref main"])
        self.assertIn("next link dispatched on main", summary)

    def test_the_chain_ends_when_the_window_closes(self):
        rc, calls, summary, log = self.run_step(at=CLOSED)
        self.assertEqual(rc, 0, log)
        self.assertEqual(calls, [])
        self.assertIn("release window closed", summary)

    def test_a_link_that_died_young_does_not_relaunch_itself(self):
        rc, calls, summary, log = self.run_step(ran=45)
        self.assertEqual(rc, 0, log)
        self.assertEqual(calls, [], "a fast-failing link would dispatch itself in a circle")
        self.assertIn("left to cron", summary)

    def test_a_link_with_no_successful_poll_does_not_relaunch_itself(self):
        rc, calls, _summary, log = self.run_step(polls="7", failures="7")
        self.assertEqual(rc, 0, log)
        self.assertEqual(calls, [])

    def test_a_missing_start_time_fails_closed(self):
        rc, calls, _summary, log = self.run_step(ran=None)
        self.assertEqual(rc, 0, log)
        self.assertEqual(calls, [])

    def test_a_failed_dispatch_does_not_fail_the_job(self):
        """The report and the final push still have to run after this step."""
        rc, calls, summary, log = self.run_step(gh_exit=1)
        self.assertEqual(rc, 0, log)
        self.assertEqual(len(calls), 1)
        self.assertIn("dispatch failed", summary)


class TestHandOverIsWiredIn(unittest.TestCase):
    TEXT = WORKFLOW.read_text()

    def test_the_token_may_dispatch_a_workflow(self):
        self.assertIn("actions: write", self.TEXT)

    def test_it_runs_only_after_a_loop_that_finished(self):
        step = _step()
        self.assertIn("!cancelled()", step)
        self.assertIn("steps.loop.outcome == 'success'", step)
        self.assertIn("steps.slate.outputs.ok == '1'", step)

    def test_a_cancelled_run_is_not_resurrected(self):
        """Someone who cancels the loop meant it."""
        self.assertNotIn("always()", _step())

    def test_the_next_link_is_queued_before_the_final_push(self):
        self.assertLess(self.TEXT.index(STEP), self.TEXT.index("      - name: Keep it"))

    def test_the_loop_reports_when_it_started(self):
        self.assertIn('echo "started=$(date -u +%s)" >> "$GITHUB_OUTPUT"', self.TEXT)


class TestWindowOpenCheck(unittest.TestCase):
    def test_exit_code_says_whether_the_window_is_open(self):
        self.assertEqual(watch.main(["--window-open", "--at", INSIDE]), 0)
        self.assertEqual(watch.main(["--window-open", "--at", CLOSED]), 1)

    def test_an_unreadable_time_is_not_an_open_window(self):
        self.assertEqual(watch.main(["--window-open", "--at", "soon"]), 2)

    def test_it_touches_no_log(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "opens.jsonl.gz"
            watch.main(["--window-open", "--at", INSIDE, "--log", str(log)])
            self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
