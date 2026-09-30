"""The seam between the CLV producer and the CLV gate.

`week6-clv-close-grade.yml` runs two processes in sequence:

    cfb_edge.week6_clv   ->  a CSV
    cfb_edge.clv_gate    ->  --observations that CSV

Five exact column names are the entire contract between them, and until this
file nothing enforced it. `test_week6_clv.py` checks the producer's columns and
imports no gate; `test_clv_gate.py` checks the loader against a hand-written
CSV and imports no producer. So a rename on either side would pass both suites
and fail silently in production: `load_observations` skips a row it cannot
parse, the gate reports `0 gradeable`, and that is indistinguishable from "the
season has not produced evidence yet" — which happens to be true right now,
which is what makes it dangerous.

This project has already lost weeks to exactly that shape. The Kalshi parser
read `yes_bid` where the API sends `yes_bid_dollars`, so every poll recorded
nothing and raised nothing. A workflow that exits 0 while measuring nothing is
the most expensive kind of bug here, and the only defence is a test that runs
both halves against each other.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cfb_edge.clv import GRADEABLE
from cfb_edge.clv_gate import evaluate, load_observations
from cfb_edge.week6_clv import build_clv_gate_csv


def _grades(rows):
    return {"rows": rows}


def _row(game, side, opening, closing, *, source="true_open", gradeable=True,
         cohort="C1"):
    return {
        "cohort": cohort,
        "game": game,
        "side": side,
        "openingHomeLine": opening,
        "closingHomeLine": closing,
        "gradeable": gradeable,
        "provenance": {"entrySource": source},
    }


class TestTheProducerFeedsTheGate(unittest.TestCase):
    """Run both halves against each other, not each against a fixture."""

    @staticmethod
    def _round_trip(rows, *, week=6, cohort="C1"):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "clv.csv"
            summary = build_clv_gate_csv(
                grades=_grades(rows), cohort=cohort, week=week, out=path)
            observations = load_observations(str(path))
        return summary, observations

    def test_every_row_the_producer_writes_is_one_the_gate_can_read(self):
        """The assertion that makes a column rename fail loudly."""
        rows = [
            _row("AWAY @ HOME", "HOME", -3.0, -4.0),
            _row("X @ Y", "X", 7.0, 6.0),
        ]
        summary, observations = self._round_trip(rows)
        self.assertEqual(summary["rows"], 2)
        self.assertEqual(
            len(observations), summary["rows"],
            "the gate dropped a row the producer wrote; the column contract "
            "between week6_clv and clv_gate has drifted")

    def test_the_two_agree_on_how_many_rows_grade(self):
        """The divergence this file was written for.

        The producer used to count `source == "true_open"` while the gate
        counts membership in GRADEABLE, which is {true_open, fill}. A real
        fill is the strongest evidence this system can hold, and it was being
        reported as ungradeable by one half and graded by the other.
        """
        rows = [
            _row("A @ B", "B", -3.0, -4.0, source="true_open"),
            _row("C @ D", "D", -6.0, -7.0, source="fill"),
            _row("E @ F", "F", -2.0, -2.5, source="first_seen"),
        ]
        summary, observations = self._round_trip(rows)
        report = evaluate(observations)
        self.assertEqual(
            report.gradeable, summary["gradeableRows"],
            "the producer and the gate disagree on how many rows grade")
        self.assertEqual(report.gradeable, 2, "true_open and fill both count")

    def test_a_fill_is_not_silently_discarded(self):
        """Stated separately, because this is where real money shows up."""
        self.assertIn("fill", GRADEABLE)
        summary, observations = self._round_trip(
            [_row("A @ B", "B", -3.0, -4.0, source="fill")])
        self.assertEqual(summary["gradeableRows"], 1)
        self.assertEqual(evaluate(observations).gradeable, 1)

    def test_an_ungradeable_row_reaches_the_gate_and_is_refused_there(self):
        """Refused, not dropped. The gate has to be able to count it."""
        summary, observations = self._round_trip(
            [_row("A @ B", "B", -3.0, -4.0, source="first_seen")])
        self.assertEqual(summary["gradeableRows"], 0)
        report = evaluate(observations)
        self.assertEqual(len(observations), 1)
        self.assertEqual(report.gradeable, 0)
        self.assertEqual(report.excluded_provenance, 1)

    def test_a_row_frozen_before_its_close_is_unsettled_not_ungradeable(self):
        """`gradeable: False` blanks the close, so the gate must see it as
        awaiting a closing line rather than as bad provenance."""
        summary, observations = self._round_trip(
            [_row("A @ B", "B", -3.0, -4.0, gradeable=False)])
        self.assertEqual(summary["gradeableRows"], 0)
        report = evaluate(observations)
        self.assertEqual(report.excluded_unsettled, 1)
        self.assertEqual(report.excluded_provenance, 0)

    def test_the_away_side_sign_survives_the_round_trip(self):
        """The producer negates for the away side. A sign error here would
        invert the CLV of half the board and still look plausible."""
        _, observations = self._round_trip(
            [_row("AWAY @ HOME", "AWAY", 7.0, 6.0)])
        self.assertEqual(len(observations), 1)
        # Backing AWAY at a home line of +7.0 is taking -7.0; the close at
        # +6.0 is -6.0. CLV is open minus close: -7.0 - (-6.0) = -1.0.
        self.assertAlmostEqual(observations[0].open_line, -7.0)
        self.assertAlmostEqual(observations[0].close_line, -6.0)
        self.assertAlmostEqual(observations[0].clv, -1.0)

    def test_the_home_side_sign_survives_the_round_trip(self):
        _, observations = self._round_trip(
            [_row("AWAY @ HOME", "HOME", -3.0, -4.0)])
        # Backing HOME at -3.0 and the close moving to -4.0 is a point gained.
        self.assertAlmostEqual(observations[0].clv, 1.0)

    def test_the_week_the_producer_stamps_is_the_week_the_gate_clusters_on(self):
        """Week clustering is a gate condition, so a dropped week column would
        silently collapse every row into one cluster."""
        _, observations = self._round_trip(
            [_row("A @ B", "B", -3.0, -4.0)], week=9)
        self.assertEqual(observations[0].week, 9)
        self.assertEqual(evaluate(observations).week_clusters, 1)

    def test_an_empty_cohort_produces_an_empty_csv_the_gate_still_reads(self):
        """A workflow that exits 0 on no rows must not look like a pass."""
        summary, observations = self._round_trip(
            [_row("A @ B", "B", -3.0, -4.0, cohort="OTHER")])
        self.assertEqual(summary["rows"], 0)
        self.assertEqual(observations, [])
        report = evaluate(observations)
        self.assertEqual(report.verdict, "INSUFFICIENT")
        self.assertNotEqual(report.verdict, "PASS")


class TestTheColumnContractIsPinned(unittest.TestCase):
    """Name the columns in one place so a rename has to be deliberate."""

    COLUMNS = ("game", "week", "open_line", "close_line", "source")

    def test_the_producer_writes_exactly_these_columns(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "clv.csv"
            build_clv_gate_csv(
                grades=_grades([_row("A @ B", "B", -3.0, -4.0)]),
                cohort="C1", week=6, out=path)
            header = path.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(tuple(header.split(",")), self.COLUMNS)

    def test_the_gate_requires_exactly_these_columns(self):
        """Drop each column in turn; the gate must lose the row rather than
        invent a value for it."""
        import csv

        complete = {"game": "A @ B", "week": 6, "open_line": -3.0,
                    "close_line": -4.0, "source": "true_open"}
        for omit in ("week", "open_line"):
            with self.subTest(omitted=omit), tempfile.TemporaryDirectory() as d:
                path = Path(d) / "clv.csv"
                fields = [c for c in self.COLUMNS if c != omit]
                with path.open("w", newline="", encoding="utf-8") as fh:
                    w = csv.DictWriter(fh, fieldnames=fields)
                    w.writeheader()
                    w.writerow({k: complete[k] for k in fields})
                self.assertEqual(
                    load_observations(str(path)), [],
                    f"a CSV missing {omit} still produced observations")


if __name__ == "__main__":
    unittest.main()
