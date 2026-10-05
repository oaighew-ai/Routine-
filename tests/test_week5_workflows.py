from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = [
    ROOT / ".github/workflows/s04-es2-week5.yml",
    ROOT / ".github/workflows/week5-close-capture.yml",
    ROOT / ".github/workflows/week5-capture-health.yml",
    ROOT / ".github/workflows/week5-signal-grade.yml",
    ROOT / ".github/workflows/br2-feature-capture.yml",
    ROOT / ".github/workflows/week5-late-open-capture.yml",
]


class Week5WorkflowCohortTests(unittest.TestCase):
    def test_every_week5_path_references_canonical_cohort(self):
        for path in WORKFLOWS:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("config/week5_cohort.json", text)
                self.assertIn("cfb_edge.cohort", text)

    def test_no_workflow_uses_runtime_week_number_as_cohort_identity(self):
        forbidden = (
            "current_week(2026)",
            "opening_week(2026)",
            "cfb_edge.slate --season 2026 --week 5",
            "/games?year=2026&week=5",
        )
        for path in WORKFLOWS:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                for token in forbidden:
                    self.assertNotIn(token, text)


    # The schedule and workflow_run pins that lived here described the Week 5
    # window while it was open. That window closed on 2026-09-27 and D41
    # retired the automatic triggers; RetiredCohortWorkflowTests below pins
    # the retired state instead. The original triggers are in git history.

    def test_es2_decision_files_are_not_part_of_cohort_fix_surface(self):
        # The cohort identity layer must remain independent from frozen
        # thresholds. This test makes the intended boundary explicit.
        freeze = (ROOT / "config/week5_freeze.json").read_text(encoding="utf-8")
        self.assertIn('"freezeId": "S04_ES2_WEEK5_FREEZE_V1"', freeze)
        self.assertIn('"minimumConservativeExecutableEv": 0.005', freeze)
        self.assertIn('"maximumOpenCaptureLagSeconds": 900', freeze)
        self.assertIn('"actualStakeUnits": 0', freeze)


RETIRED = [
    "s04-es1-live.yml",
    "s04-es2-week5.yml",
    "week5-close-capture.yml",
    "week5-signal-grade.yml",
    "week5-capture-health.yml",
    "week5-late-open-capture.yml",
    "br2-feature-capture.yml",
    "powerup-health.yml",
]


WEEK6_RETIRED = [
    "week6-clv-close-grade.yml",
    "week6-clv-decision-freeze.yml",
]


def _on_block(text: str) -> str:
    lines = text.splitlines()
    start = lines.index("on:")
    end = next(i for i in range(start + 1, len(lines)) if lines[i][:1].isalpha())
    return "\n".join(line for line in lines[start:end] if line.strip())


class RetiredCohortWorkflowTests(unittest.TestCase):
    """Closed-cohort workflows run only when someone asks (D41)."""

    def test_retired_workflows_have_no_automatic_trigger(self):
        for name in RETIRED:
            with self.subTest(workflow=name):
                text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
                self.assertEqual(_on_block(text), "on:\n  workflow_dispatch:")
                self.assertIn("RETIRED 2026-10-02 (D41)", text)

    def test_retirement_kept_the_jobs(self):
        for name in RETIRED:
            with self.subTest(workflow=name):
                text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
                self.assertIn("\njobs:\n", text)

    def test_the_week6_workflows_retired_when_their_cohort_closed(self):
        """D41 kept the Week 6 grader live until Week 6 was graded. Its game
        window closed 2026-10-04 12:00 UTC with no gradeable row, and the loop
        records closes itself from then on (D44)."""
        for name in WEEK6_RETIRED:
            with self.subTest(workflow=name):
                text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
                self.assertEqual(_on_block(text), "on:\n  workflow_dispatch:")
                self.assertIn("RETIRED 2026-10-04 (D44)", text)
                self.assertIn("\njobs:\n", text)

    def test_nothing_scheduled_still_serves_a_closed_cohort(self):
        """Every workflow with a schedule or a main push trigger is one the
        operating path names. A new cohort-specific workflow has to be added
        here on purpose, with the date it retires."""
        live = set()
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            block = _on_block(path.read_text(encoding="utf-8"))
            if "schedule:" in block or "branches: [main]" in block:
                live.add(path.name)
        self.assertEqual(live, {
            "br2-active-feature-capture.yml",
            "capture-open-loop.yml",
            # capture.yml is absent on purpose. 8573ce8 made it dispatch-only
            # so a scheduled poller could not queue behind the open loop, and
            # D44 leaves it a fallback that restarts the loop when none runs.
            # It has no schedule and no main push trigger, so it is not live.
            "cfb-operating-review.yml",
            "early-season-learning.yml",
            "private-site-bridge.yml",
            "tests.yml",
            "watchdog.yml",
        })


if __name__ == "__main__":
    unittest.main()
