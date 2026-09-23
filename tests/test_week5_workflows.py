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

    def test_br2_qb_continuity_is_bounded_to_prior_provider_week(self):
        text = (ROOT / ".github/workflows/br2-feature-capture.yml").read_text(encoding="utf-8")
        self.assertIn("/stats/player/season?year=2026&startWeek=1&endWeek={prior_week}", text)
        self.assertIn("category=passing", text)
        self.assertIn("--qb-stats .tmp/br2/qb-passing-through-prior.json", text)
        self.assertNotIn("endWeek={target_week}", text)

    def test_es2_decision_files_are_not_part_of_cohort_fix_surface(self):
        # The cohort identity layer must remain independent from frozen
        # thresholds. This test makes the intended boundary explicit.
        freeze = (ROOT / "config/week5_freeze.json").read_text(encoding="utf-8")
        self.assertIn('"freezeId": "S04_ES2_WEEK5_FREEZE_V1"', freeze)
        self.assertIn('"minimumConservativeExecutableEv": 0.005', freeze)
        self.assertIn('"maximumOpenCaptureLagSeconds": 900', freeze)
        self.assertIn('"actualStakeUnits": 0', freeze)


if __name__ == "__main__":
    unittest.main()
