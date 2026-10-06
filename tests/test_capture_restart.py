"""A single-poll run restarts the open loop when none is running (D44).

The loop hands itself from link to link, but a link that is cancelled, fails,
or loses its queued successor leaves nothing polling until a cron launch
arrives, and those arrive hours late. `capture.yml` shares the loop's
concurrency group, so it only runs when no loop does; when it has polled
successfully inside the release window it asks for one.

These tests run the step's own shell, lifted verbatim from the workflow,
against a stand-in `gh`.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = (ROOT / ".github/workflows/capture.yml").read_text()
LOOP = (ROOT / ".github/workflows/capture-open-loop.yml").read_text()
STEP = "      - name: Restart the open loop if none is running"

INSIDE = "2026-10-03T21:25:00Z"     # Saturday evening, between two links
CLOSED = "2026-10-06T18:00:00Z"     # Tuesday, the minute the window shuts


def _step() -> str:
    block = CAPTURE[CAPTURE.index(STEP):]
    nxt = block.find("\n      - name: ", 1)
    return block if nxt < 0 else block[:nxt]


def _script() -> str:
    body = _step().split("        run: |\n", 1)[1]
    return "\n".join(line[10:] if line.startswith(" " * 10) else line for line in body.splitlines())


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class TestRestart(unittest.TestCase):
    def run_step(self, *, at: str = INSIDE, gh_exit: int = 0):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            calls = d / "gh-calls.txt"
            gh = d / "gh"
            gh.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{calls}"\nexit {gh_exit}\n')
            gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
            summary = d / "summary.md"
            env = {**os.environ, "PATH": f"{d}:{os.environ['PATH']}", "PYTHONPATH": str(ROOT),
                   "GH_TOKEN": "x", "REF": "main", "RESTART_AT": at,
                   "GITHUB_STEP_SUMMARY": str(summary)}
            out = subprocess.run(["bash", "-c", _script()], cwd=ROOT, env=env,
                                 capture_output=True, text=True)
            return (out.returncode, calls.read_text().splitlines() if calls.exists() else [],
                    summary.read_text() if summary.exists() else "", out.stdout + out.stderr)

    def test_inside_the_window_it_asks_for_the_loop(self):
        rc, calls, summary, log = self.run_step()
        self.assertEqual(rc, 0, log)
        self.assertEqual(calls, ["workflow run capture-open-loop.yml --ref main"])
        self.assertIn("open loop requested on main", summary)

    def test_outside_the_window_it_asks_for_nothing(self):
        rc, calls, summary, log = self.run_step(at=CLOSED)
        self.assertEqual(rc, 0, log)
        self.assertEqual(calls, [])
        self.assertIn("release window closed", summary)

    def test_a_refused_request_does_not_fail_the_capture(self):
        rc, calls, summary, log = self.run_step(gh_exit=1)
        self.assertEqual(rc, 0, log)
        self.assertEqual(len(calls), 1)
        self.assertIn("the crons remain", summary)


class TestTheStepIsGuarded(unittest.TestCase):
    def test_it_runs_only_after_a_successful_poll_of_a_built_slate(self):
        """A run whose own poll failed must not ask for a loop: with the
        exchange or the schedule down, every delivered cron would relaunch a
        loop that dies at once."""
        condition = next(line for line in _step().splitlines() if line.strip().startswith("if:"))
        self.assertIn("!cancelled()", condition)
        self.assertIn("steps.slate.outputs.ok == '1'", condition)
        self.assertIn("steps.poll.outcome == 'success'", condition)

    def test_it_is_the_last_step(self):
        """The request queues behind this job. Asked for last, the wait before
        the loop starts is the seconds this job takes to end."""
        self.assertEqual(CAPTURE.rstrip().rsplit("\n      - name: ", 1)[1].splitlines()[0],
                         "Restart the open loop if none is running")

    def test_the_workflow_may_dispatch(self):
        head = CAPTURE[:CAPTURE.index("\njobs:")]
        self.assertIn("  actions: write", head)
        self.assertIn("  contents: write", head)

    def test_both_workflows_share_one_group_and_neither_cancels_the_other(self):
        """The claim the step rests on: a single-poll run only starts when no
        loop holds the group, and a queued run never kills a running one."""
        for text in (CAPTURE, LOOP):
            head = text[:text.index("\njobs:")]
            self.assertIn("concurrency:\n  group: cfb-capture\n  cancel-in-progress: false", head)

    def test_the_loop_it_asks_for_is_the_one_that_exists(self):
        self.assertIn("gh workflow run capture-open-loop.yml", _step())
        self.assertTrue((ROOT / ".github/workflows/capture-open-loop.yml").exists())
        self.assertIn("workflow_dispatch:", LOOP[:LOOP.index("\njobs:")])


if __name__ == "__main__":
    unittest.main()
