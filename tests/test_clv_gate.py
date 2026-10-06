"""Tests for the CLV gate, including the statistical guarantee it rests on.

The guarantee is the whole point of the module, and it is the one thing a
reader cannot check by eye. Ville's inequality says an e-process crosses a
threshold T under the null with probability at most 1/T. If the construction
in `clv_gate.e_value` is wrong, that bound breaks and the gate passes
strategies that do not work. So it is measured here by simulation rather than
argued for in a docstring.
"""
from __future__ import annotations

import math
import random
import statistics
import unittest

from cfb_edge.clv_gate import (CLOSING_LINE_RESIDUAL_SD, E_VALUE_THRESHOLD,
                               MINIMUM_WEEK_CLUSTERS, SUB_GAUSSIAN_SD,
                               Observation, breakeven_points, e_value,
                               evaluate, observations_required,
                               points_per_unit_probability, tilt_grid)


def _paths(mean, sigma, trials, length, seed):
    """Normal paths with a given mean. Seeded, so a failure is reproducible."""
    rng = random.Random(seed)
    return [[rng.gauss(mean, sigma) for _ in range(length)]
            for _ in range(trials)]


class TestTheEProcessGuarantee(unittest.TestCase):
    """Under the null the process must almost never cross the threshold.

    A broken construction fails here rather than in production. In particular
    a two-sided mixture — the natural mistake — would also fire on strongly
    negative runs, which for this gate means declaring an edge when the
    strategy is losing.
    """

    NULL = 0.6
    SIGMA = 2.0
    TRIALS = 600
    LENGTH = 120

    def test_crossing_rate_under_the_null_respects_villes_bound(self):
        crossed = 0
        for path in _paths(self.NULL, self.SIGMA, self.TRIALS, self.LENGTH, 20260929):
            _, peak = e_value(path, null_mean=self.NULL, sigma=self.SIGMA)
            if peak >= E_VALUE_THRESHOLD:
                crossed += 1
        rate = crossed / self.TRIALS
        # The bound is 1/20 = 0.05. The allowance is Monte Carlo slack at
        # n=600 (binomial sd about 0.009), not licence for an invalid test.
        self.assertLessEqual(
            rate, 0.08,
            f"crossed on {crossed}/{self.TRIALS} null paths ({rate:.1%}); "
            f"Ville's bound is {1 / E_VALUE_THRESHOLD:.1%}")

    def test_a_mean_below_the_null_is_even_safer(self):
        """The null is composite: mean <= null. Below it must not help."""
        crossed = 0
        for path in _paths(self.NULL - 0.5, self.SIGMA, 300, self.LENGTH, 5150):
            _, peak = e_value(path, null_mean=self.NULL, sigma=self.SIGMA)
            if peak >= E_VALUE_THRESHOLD:
                crossed += 1
        self.assertLessEqual(crossed / 300, 0.08)

    def test_negative_evidence_cannot_trigger_it(self):
        """One-sidedness. A losing strategy must not look like a winning one.

        An e-value below 1 means the data favour the null, so the properties
        worth asserting are that it lands there, that the process never rose
        above its starting point, and that a longer losing run only makes it
        smaller. A fixed tiny bound would instead be asserting the slowest
        tilt in the grid, which is arbitrary.
        """
        short = [self.NULL - 2.0] * 60
        long = [self.NULL - 2.0] * 120

        current, peak = e_value(short, null_mean=self.NULL, sigma=self.SIGMA)
        self.assertLess(current, 1.0, "a losing run produced positive evidence")
        self.assertEqual(peak, 1.0, "the process should never have risen")
        self.assertLess(
            current, E_VALUE_THRESHOLD / 100.0,
            "a losing run came within two orders of magnitude of passing")

        longer, long_peak = e_value(long, null_mean=self.NULL, sigma=self.SIGMA)
        self.assertLess(longer, current, "more losing evidence did not shrink it")
        self.assertEqual(long_peak, 1.0)

    def test_every_tilt_is_positive(self):
        """The property the previous test depends on, checked at the source."""
        for lam in tilt_grid():
            self.assertGreater(lam, 0.0)

    def test_it_rejects_a_two_sided_tilt_grid(self):
        with self.assertRaises(ValueError):
            e_value([1.0], null_mean=0.0, sigma=1.0, tilts=(0.1, -0.1))


class TestItDetectsARealEdge(unittest.TestCase):
    """A gate that never passes is as useless as one that always does."""

    def test_a_one_point_excess_is_found(self):
        null, sigma, excess = 0.675, 2.0, 1.0
        length = 3 * observations_required(excess, sigma=sigma)
        crossed = 0
        trials = 200
        for path in _paths(null + excess, sigma, trials, length, 771):
            _, peak = e_value(path, null_mean=null, sigma=sigma)
            if peak >= E_VALUE_THRESHOLD:
                crossed += 1
        self.assertGreaterEqual(
            crossed / trials, 0.80,
            f"only {crossed}/{trials} detected a {excess}-point excess "
            f"over {length} observations")

    def test_the_sample_size_formula_matches_the_evidence_rate(self):
        """n = ln(T) / (d^2 / 2 sigma^2), computed rather than remembered."""
        for excess in (0.25, 0.5, 1.0):
            rate = excess ** 2 / (2 * SUB_GAUSSIAN_SD ** 2)
            self.assertEqual(
                observations_required(excess),
                math.ceil(math.log(E_VALUE_THRESHOLD) / rate))

    def test_a_smaller_edge_needs_more_games(self):
        self.assertGreater(observations_required(0.25), observations_required(0.5))

    def test_an_undetectable_excess_is_refused(self):
        for bad in (0.0, -0.5):
            with self.assertRaises(ValueError):
                observations_required(bad)


class TestTheBreakevenIsDerivedNotAsserted(unittest.TestCase):
    """The null the gate tests against, and why it is not zero."""

    def test_points_per_unit_probability_is_sd_over_phi_zero(self):
        self.assertAlmostEqual(
            points_per_unit_probability(15.39),
            15.39 / (1.0 / math.sqrt(2 * math.pi)), places=9)

    def test_the_breakeven_matches_the_stated_arithmetic(self):
        # 0.07 * 0.25 of a contract, converted at 15.39 / phi(0).
        expected = 0.07 * 0.25 * (CLOSING_LINE_RESIDUAL_SD * math.sqrt(2 * math.pi))
        self.assertAlmostEqual(breakeven_points(), expected, places=9)
        self.assertAlmostEqual(breakeven_points(), 0.675, places=2)

    def test_the_breakeven_exceeds_the_strategys_own_claim(self):
        """The finding that motivated this module.

        +0.44 points is the best result this project has ever measured, and it
        does not clear the fee at the strike where the contracts trade. If
        this assertion ever fails, either the fee changed or the claim did,
        and the gate's null needs revisiting.
        """
        self.assertGreater(
            breakeven_points(), 0.44,
            "the headline claim now clears the fee; re-derive the null")

    def test_a_wider_market_costs_more_points(self):
        self.assertGreater(breakeven_points(margin_sd=20.0), breakeven_points())

    def test_a_nonsense_margin_sd_is_refused(self):
        for bad in (0.0, -1.0):
            with self.assertRaises(ValueError):
                breakeven_points(margin_sd=bad)


def _obs(n, *, week=3, clv=1.0, source="true_open", close=0.0):
    return [Observation(game=f"G{i} @ H{i}", week=week + (i % 4),
                        open_line=close + clv, close_line=close, source=source)
            for i in range(n)]


class TestTheGateFailsClosed(unittest.TestCase):
    """Absence of evidence must never read as evidence."""

    def test_no_observations_is_insufficient_not_pass(self):
        report = evaluate([])
        self.assertEqual(report.verdict, "INSUFFICIENT")
        self.assertFalse(report.passing)
        self.assertEqual(report.gradeable, 0)
        self.assertIn("MINIMUM_OBSERVATIONS", report.failed_gates)
        self.assertIn("ANYTIME_E_VALUE", report.failed_gates)

    def test_the_current_capture_state_does_not_pass(self):
        """150 rows, none of them gradeable: exactly today's capture-data."""
        rows = (_obs(114, source="first_seen") + _obs(36, source="late"))
        report = evaluate(rows)
        self.assertEqual(report.gradeable, 0)
        self.assertEqual(report.excluded_provenance, 150)
        self.assertEqual(report.verdict, "INSUFFICIENT")

    def test_ungradeable_provenance_is_refused_and_counted(self):
        for bad in ("first_seen", "late", "capture", "unverified", ""):
            report = evaluate(_obs(40, source=bad))
            with self.subTest(source=bad):
                self.assertEqual(report.gradeable, 0, bad)
                self.assertEqual(report.excluded_provenance, 40)

    def test_only_true_open_and_fill_count(self):
        for good in ("true_open", "fill"):
            report = evaluate(_obs(40, source=good))
            with self.subTest(source=good):
                self.assertEqual(report.gradeable, 40)
                self.assertEqual(report.excluded_provenance, 0)

    def test_a_missing_closing_line_is_unsettled_not_dropped(self):
        rows = [Observation("A @ B", 3, 2.0, None, "true_open")] * 5
        report = evaluate(rows)
        self.assertEqual(report.gradeable, 0)
        self.assertEqual(report.excluded_unsettled, 5)
        self.assertEqual(report.excluded_provenance, 0)

    def test_one_good_week_cannot_carry_the_gate(self):
        """Week 3 of 2026 measured +0.539; the week before it measured -0.510."""
        many = _obs(400, week=3, clv=3.0)
        single = [Observation(o.game, 3, o.open_line, o.close_line, o.source)
                  for o in many]
        report = evaluate(single)
        self.assertEqual(report.week_clusters, 1)
        self.assertIn("MINIMUM_WEEK_CLUSTERS", report.failed_gates)
        self.assertNotEqual(report.verdict, "PASS")

    def test_a_large_sample_below_the_null_reads_FAIL_not_insufficient(self):
        """"Insufficient" invites another season. Below the null is an answer."""
        report = evaluate(_obs(400, clv=0.05))
        self.assertGreaterEqual(report.gradeable, report.minimum_observations)
        self.assertGreaterEqual(report.week_clusters, MINIMUM_WEEK_CLUSTERS)
        self.assertLess(report.mean_clv, report.null_mean)
        self.assertEqual(report.verdict, "FAIL")

    def test_a_genuine_edge_across_enough_weeks_passes(self):
        report = evaluate(_obs(200, clv=2.0))
        self.assertEqual(report.verdict, "PASS")
        self.assertTrue(report.passing)
        self.assertEqual(report.failed_gates, ())
        self.assertGreaterEqual(report.max_e_value, E_VALUE_THRESHOLD)


class TestSigmaCannotBeUnderstated(unittest.TestCase):
    """Understating sigma inflates the e-value, so the gate refuses to."""

    def test_the_observed_sd_wins_when_it_is_larger(self):
        rows = _obs(40, clv=1.0)
        noisy = [Observation(o.game, o.week, o.open_line + (i % 2) * 12.0,
                             o.close_line, o.source)
                 for i, o in enumerate(rows)]
        report = evaluate(noisy, sigma=0.01)
        self.assertGreater(report.sigma_used, 0.01)
        self.assertAlmostEqual(report.sigma_used, report.sd_clv, places=9)

    def test_a_larger_sigma_never_produces_more_evidence(self):
        values = [1.5] * 50
        small, _ = e_value(values, null_mean=0.675, sigma=1.0)
        large, _ = e_value(values, null_mean=0.675, sigma=4.0)
        self.assertGreater(small, large)

    def test_a_nonpositive_sigma_is_refused(self):
        for bad in (0.0, -1.0):
            with self.assertRaises(ValueError):
                e_value([1.0], null_mean=0.0, sigma=bad)


class TestTheReportIsReadable(unittest.TestCase):
    def test_the_summary_names_the_null_and_the_shortfall(self):
        text = evaluate(_obs(10, clv=0.1)).summary()
        self.assertIn("CFB_EDGE_CLV_GATE_V1", text)
        self.assertIn("breakeven", text)
        self.assertIn("e-value", text)

    def test_the_summary_survives_having_no_gradeable_rows(self):
        text = evaluate(_obs(5, source="first_seen")).summary()
        self.assertIn("no gradeable rows", text)

    def test_clv_is_open_minus_close(self):
        o = Observation("A @ B", 4, 3.5, 2.0, "true_open")
        self.assertAlmostEqual(o.clv, 1.5)
        self.assertTrue(o.gradeable)

    def test_clv_is_none_without_a_close(self):
        self.assertIsNone(Observation("A @ B", 4, 3.5, None, "true_open").clv)


class TestObservationsRoundTripThroughCsv(unittest.TestCase):
    def test_a_written_csv_reads_back(self):
        import tempfile
        from pathlib import Path

        from cfb_edge.clv_gate import load_observations

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "rows.csv"
            path.write_text(
                "game,week,open_line,close_line,source\n"
                "A @ B,4,3.5,2.0,true_open\n"
                "C @ D,4,-7.0,,true_open\n"
                "E @ F,5,1.0,1.0,first_seen\n"
                "G @ H,bad,1.0,1.0,true_open\n",
                encoding="utf-8")
            rows = load_observations(str(path))
        self.assertEqual(len(rows), 3, "the unparseable week should be skipped")
        self.assertAlmostEqual(rows[0].clv, 1.5)
        self.assertIsNone(rows[1].close_line)
        report = evaluate(rows)
        self.assertEqual(report.gradeable, 1)
        self.assertEqual(report.excluded_unsettled, 1)
        self.assertEqual(report.excluded_provenance, 1)


if __name__ == "__main__":
    unittest.main()
