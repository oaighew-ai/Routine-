"""Acceptance checks 1 to 12, from `spec/BUILD_PROMPT.md` §11.

Each test names the check it discharges in its docstring, so a failure says
which contractual promise broke rather than which function did.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cfb_edge.clv import LoggedBet
from cfb_edge.distribution import KEY_BUMPS, total_pmf
from cfb_edge.engine import config
from cfb_edge.engine.clv2 import (
    MarketMismatch, clv_pct, close_fair_prob_at_entry, fair_prob, gate2,
)
from cfb_edge.engine.evidence import (
    SystemRecord, clv_kill, raw_weight, stage_a, wilson_lower,
)
from cfb_edge.engine.posterior import Quote, decide
from cfb_edge.engine.pricing import decide_at_price, executable, max_playable_price
from cfb_edge.engine.rows import candidate_row
from cfb_edge.engine.sizing import portfolio_scale, round_down, size
from cfb_edge.engine.version import ENGINE_VERSION
from cfb_edge.ledger import CANDIDATE, LedgerError, append, load
from cfb_edge.market import american_to_decimal, devig

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "golden_vectors.json"

SPREAD = dict(sport="ncaaf", market="spreads", side="home", line=-3.0,
              price=-110.0, other_price=-110.0)


def mature(system_id="mature", family="system", **kw) -> SystemRecord:
    """The 194-129 provider system acceptance check 5 is written around."""
    return SystemRecord(system_id=system_id, family=family,
                        wins_provider=194, losses_provider=129, **kw)


def cfg_with(**blend) -> config.Config:
    base = config.default()
    raw = json.loads(json.dumps(base.raw))
    raw["blend"].update(blend)
    return config.Config(raw=raw)


class Check01GoldenVectors(unittest.TestCase):
    """Check 1: the engine reproduces its frozen vectors below 1e-9.

    D1: there is no `edge_engine.jsx`, so these were generated from this engine.
    That makes this a drift test and not an independent one, which is stated in
    the fixture and in `scripts/golden_vectors.py` rather than left implied.
    """

    def setUp(self):
        self.payload = json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_at_least_ten_vectors(self):
        self.assertGreaterEqual(len(self.payload["vectors"]), 10)

    def test_fixture_is_not_stale(self):
        import scripts.golden_vectors as gv  # noqa: PLC0415
        self.assertEqual(
            json.dumps(gv.build(), indent=2, sort_keys=True) + "\n",
            GOLDEN.read_text(encoding="utf-8"),
            "golden vectors are stale; see scripts/golden_vectors.py",
        )

    def test_primitive_vectors_reproduce(self):
        import scripts.golden_vectors as gv  # noqa: PLC0415
        rebuilt = {v["name"]: v for v in gv.build()["vectors"]}
        for v in self.payload["vectors"]:
            if v["kind"] != "value":
                continue
            with self.subTest(v["name"]):
                self.assertAlmostEqual(
                    rebuilt[v["name"]]["expected"], v["expected"], delta=1e-9
                )

    def test_decision_vectors_reproduce(self):
        import scripts.golden_vectors as gv  # noqa: PLC0415
        rebuilt = {v["name"]: v for v in gv.build()["vectors"]}
        for v in self.payload["vectors"]:
            if v["kind"] != "decision":
                continue
            with self.subTest(v["name"]):
                got, want = rebuilt[v["name"]]["expected"], v["expected"]
                self.assertEqual(got["decision"], want["decision"])
                self.assertEqual(got["reasonCodes"], want["reasonCodes"])
                for key in ("p_post", "ev", "f_full", "stake", "portfolioScale"):
                    if want[key] is None:
                        self.assertIsNone(got[key])
                    else:
                        self.assertAlmostEqual(got[key], want[key], delta=1e-9)


class Check02EVAtTheExecutablePrice(unittest.TestCase):
    """Check 2: 1.5 points above no-vig at -110 is negative EV and must PASS."""

    def test_stated_figure(self):
        d = decide_at_price(0.5 + 0.015, executable(-110.0))
        self.assertAlmostEqual(d.ev, -0.016818, places=6)
        self.assertLess(d.ev, 0.0)
        self.assertFalse(d.playable)

    def test_through_the_whole_engine(self):
        cfg = config.default()
        # w0 * 0.5 + w * p_signal over (w0 + w) = 0.515 when w equals w0 and
        # p_signal is 0.53, so this is the same arithmetic end to end.
        rec = SystemRecord(system_id="s", family="system",
                           excess_n=400, excess_mean=0.03, excess_sd=0.0,
                           wins_provider=200, losses_provider=200)
        q = Quote(event_id="e", **SPREAD)
        d = decide(q, [(rec, "for")], cfg=cfg_with(w0=raw_weight(rec, cfg)))
        self.assertAlmostEqual(d.p_post, 0.515, places=9)
        self.assertAlmostEqual(d.ev, -0.016818, places=6)
        self.assertEqual(d.decision, "PASS")
        self.assertIn("NEG_EV", d.reason_codes)


class Check03OneFamilyOnePieceOfEvidence(unittest.TestCase):
    """Check 3: same family counts once; different families count twice."""

    def setUp(self):
        self.cfg = config.default()
        self.q = Quote(event_id="e", **SPREAD)
        self.one = decide(self.q, [(mature(), "for")], cfg=self.cfg)

    def test_same_family_twice_is_one(self):
        two = decide(
            self.q,
            [(mature("a"), "for"), (mature("b"), "for")],
            cfg=self.cfg,
        )
        self.assertEqual(two.stake_units, self.one.stake_units)
        self.assertAlmostEqual(two.p_post, self.one.p_post, places=12)
        self.assertIn("FAMILY_DUP", two.reason_codes)

    def test_different_families_count_twice(self):
        two = decide(
            self.q,
            [(mature("a", family="system"), "for"),
             (mature("b", family="sharp"), "for")],
            cfg=self.cfg,
        )
        self.assertGreater(two.stake_units, self.one.stake_units)
        self.assertNotIn("FAMILY_DUP", two.reason_codes)


class Check04OpposingSignals(unittest.TestCase):
    """Check 4: equal and opposite signals produce NO_BET with OPPOSED."""

    def test_cancels_to_no_bet(self):
        d = decide(
            Quote(event_id="e", **SPREAD),
            [(mature("a", family="system"), "for"),
             (mature("b", family="sharp"), "against")],
            cfg=config.default(),
        )
        self.assertAlmostEqual(d.p_post, 0.5, places=9)
        self.assertEqual(d.decision, "NO_BET")
        self.assertIn("OPPOSED", d.reason_codes)

    def test_opposing_alone_is_still_no_bet_not_pass(self):
        d = decide(
            Quote(event_id="e", **SPREAD),
            [(mature("b", family="sharp"), "against")],
            cfg=config.default(),
        )
        self.assertEqual(d.decision, "NO_BET")
        self.assertIn("OPPOSED", d.reason_codes)

    def test_same_family_both_sides_does_not_silently_cancel(self):
        """Dedupe keys on (family, side), so §6.6 stays reachable.

        Keyed on family alone, one of these would be dropped before the
        opposing rule ever ran, and the row would read as a single unopposed
        signal.
        """
        d = decide(
            Quote(event_id="e", **SPREAD),
            [(mature("a", family="system"), "for"),
             (mature("b", family="system"), "against")],
            cfg=config.default(),
        )
        self.assertEqual(d.decision, "NO_BET")
        self.assertIn("OPPOSED", d.reason_codes)


class Check05ProviderSystemBets(unittest.TestCase):
    """Check 5: 194-129, own n = 0, passes Stage A at -110 and produces a BET."""

    def test_stage_a_clears_breakeven(self):
        cfg = config.default()
        a = stage_a(mature(), p_nv=0.5, cfg=cfg)
        self.assertTrue(a.passed)
        self.assertAlmostEqual(a.p_signal, 0.5463260871230095, places=12)
        self.assertGreater(a.p_signal, executable(-110.0).breakeven)

    def test_produces_a_bet_row(self):
        d = decide(Quote(event_id="e", **SPREAD), [(mature(), "for")],
                   cfg=config.default())
        self.assertEqual(d.decision, "BET")
        self.assertGreater(d.stake_units, 0.0)
        self.assertGreater(d.ev, 0.0)
        self.assertEqual(d.signals[0].n_own, 0)


class Check06NoDiscontinuityAtThirty(unittest.TestCase):
    """Check 6: A1 removed the switch at own n = 30, so nothing steps there."""

    def test_stage_a_is_smooth_across_thirty(self):
        """Sampled where a 60% own record is exactly representable.

        Integer wins make the curve a fine sawtooth at every n, so sampling
        every n would measure the rounding rather than the rule. Stepping in
        fives keeps the own win rate at exactly 0.6 throughout, which isolates
        the question actually being asked: does anything happen at 30?
        """
        cfg = config.default()
        own = list(range(20, 46, 5))
        values = [
            stage_a(mature(wins_own=n * 3 // 5, losses_own=n * 2 // 5),
                    p_nv=0.5, cfg=cfg).p_signal
            for n in own
        ]
        for i in range(1, len(values) - 1):
            second = abs(values[i + 1] - 2 * values[i] + values[i - 1])
            self.assertLess(second, 5e-4, f"step at own n = {own[i]}")

    def test_thirty_is_not_a_branch(self):
        """The strongest form: at own n = 30 the pooled formula is still used.

        A1 removed the replacement rule, so `p_signal` at 30 has to be exactly
        the Wilson bound of the pooled record, not of the own record.
        """
        cfg = config.default()
        rec = mature(wins_own=18, losses_own=12)
        self.assertAlmostEqual(
            stage_a(rec, p_nv=0.5, cfg=cfg).p_signal,
            wilson_lower(rec.wins_pooled, rec.n_pooled),
            places=15,
        )
        self.assertNotAlmostEqual(
            stage_a(rec, p_nv=0.5, cfg=cfg).p_signal,
            wilson_lower(18, 30),
            places=3,
        )

    def test_weight_is_smooth_across_thirty(self):
        cfg = config.default()
        weights = [
            raw_weight(mature(wins_own=n, losses_own=0), cfg) for n in range(25, 36)
        ]
        for i in range(1, len(weights) - 1):
            second = abs(weights[i + 1] - 2 * weights[i] + weights[i - 1])
            self.assertLess(second, 1e-4)

    def test_pooling_uses_both_records(self):
        rec = mature(wins_own=30, losses_own=0)
        self.assertEqual(rec.n_pooled, 353)
        self.assertEqual(rec.wins_pooled, 224)


class Check07CLVKill(unittest.TestCase):
    """Check 7: A2 is monitoring only at n = 20 and kills at n = 60."""

    def setUp(self):
        self.cfg = config.default()

    def _rec(self, grades):
        return mature(own_bet_grades=grades, own_mean_clv=-0.004, own_roi=0.08)

    def test_no_effect_below_the_floor(self):
        rec = self._rec(20)
        killed, flags = clv_kill(rec, self.cfg)
        self.assertFalse(killed)
        self.assertEqual(flags, ())
        a = stage_a(rec, p_nv=0.5, cfg=self.cfg)
        self.assertGreater(a.w_sig, 0.0)
        self.assertNotIn("CLV_KILL", a.flags)

    def test_kills_and_flags_luck_at_sixty(self):
        rec = self._rec(60)
        a = stage_a(rec, p_nv=0.5, cfg=self.cfg)
        self.assertEqual(a.w_sig, 0.0)
        self.assertIn("CLV_KILL", a.flags)
        self.assertIn("LUCK_RISK", a.flags)

    def test_positive_clv_at_sixty_survives(self):
        rec = mature(own_bet_grades=60, own_mean_clv=0.012, own_roi=-0.02)
        a = stage_a(rec, p_nv=0.5, cfg=self.cfg)
        self.assertGreater(a.w_sig, 0.0)
        self.assertNotIn("CLV_KILL", a.flags)

    def test_killed_system_cannot_produce_a_bet(self):
        d = decide(Quote(event_id="e", **SPREAD), [(self._rec(60), "for")],
                   cfg=self.cfg)
        self.assertNotEqual(d.decision, "BET")
        self.assertIn("LUCK_RISK", d.reason_codes)


class Check08Rounding(unittest.TestCase):
    """Check 8: stakes round down only, and a bound ceiling is logged."""

    def setUp(self):
        self.cfg = config.default()

    def test_never_rounds_up(self):
        for raw in (0.0499, 0.05, 0.0999, 0.1, 0.1499, 0.9999, 1.9999):
            with self.subTest(raw=raw):
                self.assertLessEqual(round_down(raw, 0.05), raw + 1e-12)

    def test_lands_on_a_clean_step(self):
        self.assertEqual(round_down(0.1999, 0.05), 0.15)
        self.assertEqual(round_down(0.15, 0.05), 0.15)

    def test_ceiling_binds_and_is_logged(self):
        s = size(1.0, cfg=self.cfg, c=1)
        self.assertEqual(s.units, self.cfg.ceiling_units)
        self.assertIn("CEILING_HIT", s.flags)
        self.assertGreater(s.raw_units, self.cfg.ceiling_units)

    def test_sub_minimum_is_a_pass_not_a_tiny_bet(self):
        s = size(0.0005, cfg=self.cfg, c=1, min_stake_units=0.05)
        self.assertEqual(s.units, 0.0)
        self.assertIn("SUB_MIN", s.flags)

    def test_correlation_haircut_shrinks_the_stake(self):
        alone = size(0.05, cfg=self.cfg, c=1)
        crowded = size(0.05, cfg=self.cfg, c=6)
        self.assertLess(crowded.units, alone.units)
        self.assertAlmostEqual(portfolio_scale(1, rho=0.3), 1.0)


class Check09DevigSensitivity(unittest.TestCase):
    """Check 9: a decision that flips between de-vigs is a PASS."""

    def test_flagged_and_passed(self):
        d = decide(
            Quote(event_id="e", sport="ncaaf", market="h2h", side="home",
                  line=None, price=-118.0, other_price=110.0),
            [(mature(), "for")],
            cfg=config.default(),
        )
        self.assertEqual(d.decision, "PASS")
        self.assertIn("DEVIG_SENSITIVE", d.reason_codes)
        self.assertNotAlmostEqual(d.devig["proportional"], d.devig["power"], places=6)

    def test_agreeing_devigs_do_not_trip_it(self):
        d = decide(Quote(event_id="e", **SPREAD), [(mature(), "for")],
                   cfg=config.default())
        self.assertNotIn("DEVIG_SENSITIVE", d.reason_codes)


class Check10TotalsNeverUseTheMarginPMF(unittest.TestCase):
    """Check 10: totals convert through the total-points distribution."""

    def test_total_pmf_carries_no_key_numbers(self):
        pmf = total_pmf(52.0)
        self.assertIn(0, pmf)          # a 0-0 total is possible; a 0 margin is not
        for k in (3, 7):
            neighbours = 0.5 * (pmf[k + 52 - 1] + pmf[k + 52 + 1])
            self.assertAlmostEqual(pmf[k + 52], neighbours, delta=neighbours * 0.05)
        self.assertGreater(KEY_BUMPS[3], 2.0)   # the margin PMF does have them

    def test_totals_conversion_differs_from_the_margin_conversion(self):
        close_fair = fair_prob(-110.0, -110.0)
        totals, method = close_fair_prob_at_entry(
            market="totals", side_is_over=True, entry_line=51.5,
            close_line=52.5, close_fair=close_fair)
        spread, _ = close_fair_prob_at_entry(
            market="spreads", entry_line=51.5, close_line=52.5, close_fair=close_fair)
        self.assertEqual(method, "pmf")
        self.assertNotAlmostEqual(totals, spread, places=4)

    def test_a_totals_row_without_a_side_raises(self):
        with self.assertRaises(MarketMismatch):
            close_fair_prob_at_entry(market="totals", entry_line=51.5,
                                     close_line=52.5, close_fair=0.5)

    def test_an_unknown_market_raises_rather_than_reusing_a_distribution(self):
        with self.assertRaises(MarketMismatch):
            close_fair_prob_at_entry(market="first_half_spread", entry_line=1.5,
                                     close_line=2.5, close_fair=0.5)


class Check11FrozenRows(unittest.TestCase):
    """Check 11: changing w0 leaves written rows byte-identical (Law 1)."""

    def test_rows_do_not_move_when_the_config_does(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            d = decide(Quote(event_id="e", **SPREAD), [(mature(), "for")],
                       cfg=config.default())
            append(path, candidate_row(d, candidate_id="c1",
                                       logged_at="2026-09-18T20:00:00Z",
                                       phase="SHADOW"), CANDIDATE)
            before = path.read_bytes()

            shifted = cfg_with(w0=0.2)
            later = decide(Quote(event_id="e2", **SPREAD), [(mature(), "for")],
                           cfg=shifted)
            self.assertNotAlmostEqual(later.p_post,
                                      json.loads(before)["outputs"]["p_post"])
            self.assertEqual(path.read_bytes(), before)

            append(path, candidate_row(later, candidate_id="c2",
                                       logged_at="2026-09-18T21:00:00Z",
                                       phase="SHADOW"), CANDIDATE)
            self.assertTrue(path.read_bytes().startswith(before))
            self.assertEqual(len(load(path, CANDIDATE)), 2)

    def test_encoding_is_deterministic(self):
        d = decide(Quote(event_id="e", **SPREAD), [(mature(), "for")],
                   cfg=config.default())
        rows = [candidate_row(d, candidate_id="c1", logged_at="t", phase="SHADOW")
                for _ in range(2)]
        self.assertEqual(json.dumps(rows[0], sort_keys=True),
                         json.dumps(rows[1], sort_keys=True))


class Check12LedgerRejectsSamples(unittest.TestCase):
    """Check 12: the writer refuses a fixture row."""

    def setUp(self):
        self.d = decide(Quote(event_id="e", **SPREAD), [(mature(), "for")],
                        cfg=config.default())

    def test_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            row = candidate_row(self.d, candidate_id="c1", logged_at="t",
                                phase="SHADOW", sample=True)
            with self.assertRaises(LedgerError):
                append(path, row, CANDIDATE)
            self.assertFalse(path.exists())

    def test_a_bad_row_is_never_partially_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            good = candidate_row(self.d, candidate_id="c1", logged_at="t",
                                 phase="SHADOW")
            append(path, good, CANDIDATE)
            size_before = path.stat().st_size
            with self.assertRaises(Exception):
                append(path, {**good, "decision": "MAYBE"}, CANDIDATE)
            self.assertEqual(path.stat().st_size, size_before)


class A3Reconciliation(unittest.TestCase):
    """A3 demands reconciliation with this repository's CLV before grading."""

    def test_identity_holds_method_for_method(self):
        for method, name in (("multiplicative", "proportional"), ("power", "power")):
            with self.subTest(method):
                fair = devig([-108.0, -112.0], method)[0]
                d = american_to_decimal(-110.0)
                self.assertAlmostEqual(
                    clv_pct(fair, -110.0), (fair - 1.0 / d) * d, delta=1e-12
                )

    def test_against_the_existing_LoggedBet(self):
        bet = LoggedBet(date="2026-09-05", away="A", home="B", side="B",
                        line_taken=-3.0, price_taken=-110.0, stake=1.0,
                        closing_line=-3.0, closing_price=-108.0,
                        closing_opposite_price=-112.0, source="capture")
        shin_fair = devig([-108.0, -112.0], "shin")[0]
        self.assertAlmostEqual(
            clv_pct(shin_fair, -110.0),
            bet.price_clv * american_to_decimal(-110.0),
            delta=1e-12,
        )

    def test_cross_method_gap_is_recorded_not_hidden(self):
        """The engine pins proportional; `clv.py` defaults to Shin.

        The gap is small and one-directional, so it is asserted at its measured
        size rather than left to drift.
        """
        gap = abs(
            clv_pct(fair_prob(-108.0, -112.0), -110.0)
            - clv_pct(devig([-108.0, -112.0], "shin")[0], -110.0)
        )
        self.assertLess(gap, 1e-3)
        self.assertGreater(gap, 1e-5)

    def test_a_conventional_move_is_not_clv(self):
        """A3's worked example: +1.5 points of implied move is still -1.8% EV."""
        self.assertLess(clv_pct(0.5 + 0.015, -110.0), 0.0)
        self.assertAlmostEqual(clv_pct(0.515, -110.0), -0.016818, places=6)


class A5Gate2(unittest.TestCase):
    """A5 plus D5: the gate counts clusters as well as rows."""

    def _rows(self, n, slates, value=0.02, decision="BET"):
        return [
            {"decision": decision, "clvLagPct": value, "clvMethod": "exact",
             "gradeable": True, "sport": "ncaaf",
             "slateDate": f"2026-09-{(i % slates) + 1:02d}"}
            for i in range(n)
        ]

    def test_counts_only_bet_rows(self):
        rows = self._rows(60, 20) + self._rows(200, 20, decision="PASS")
        self.assertEqual(gate2(rows).n_rows, 60)

    def test_ungradeable_prices_are_excluded(self):
        rows = self._rows(60, 20)
        rows[0]["gradeable"] = False
        self.assertEqual(gate2(rows).n_rows, 59)

    def test_rows_without_clusters_do_not_pass(self):
        """Sixty rows on three Saturdays is three observations, not sixty."""
        result = gate2(self._rows(60, 3))
        self.assertEqual(result.n_rows, 60)
        self.assertEqual(result.n_clusters, 3)
        self.assertFalse(result.passed)

    def test_enough_rows_and_clusters_with_a_real_edge_passes(self):
        result = gate2(self._rows(60, 20))
        self.assertGreaterEqual(result.n_clusters, 12)
        self.assertTrue(result.passed)

    def test_zero_edge_does_not_pass(self):
        self.assertFalse(gate2(self._rows(60, 20, value=0.0)).passed)

    def test_pmf_rows_can_be_excluded(self):
        rows = self._rows(60, 20)
        for r in rows[:30]:
            r["clvMethod"] = "pmf"
        self.assertEqual(gate2(rows, include_pmf=False).n_rows, 30)


class MaxPlayablePrice(unittest.TestCase):
    """§6.3: swept across a grid, never obtained by inverting p_post."""

    def test_worst_price_still_clears(self):
        p = 0.545
        worst = max_playable_price(p)
        self.assertIsNotNone(worst)
        self.assertTrue(decide_at_price(p, executable(worst)).playable)
        self.assertFalse(decide_at_price(p, executable(worst - 1)).playable)

    def test_none_when_nothing_on_the_grid_clears(self):
        """The cheapest price on the grid is +400, which needs 20%."""
        self.assertIsNone(max_playable_price(0.10))
        self.assertIsNotNone(max_playable_price(0.30))

    def test_a_fee_moves_it(self):
        venue = {"fee_rule": "kalshi_general", "fee_coefficient": 0.07}
        free = {"fee_rule": "none"}
        self.assertGreater(
            executable(50.0, kind="exchange_cents", venue_config=free).payout,
            executable(50.0, kind="exchange_cents", venue="kalshi",
                       venue_config=venue).payout,
        )

    def test_the_same_fee_applies_to_an_american_quote_of_the_same_price(self):
        """The Odds API returns Kalshi in American, so the fee must survive it."""
        venue = {"fee_rule": "kalshi_general", "fee_coefficient": 0.07}
        cents = executable(52.380952380952380, kind="exchange_cents",
                           venue="kalshi", venue_config=venue)
        american = executable(-110.0, venue="kalshi", venue_config=venue)
        self.assertAlmostEqual(cents.payout, american.payout, places=9)
        self.assertLess(american.payout, executable(-110.0).payout)


class VenueFeeProvenance(unittest.TestCase):
    """§6.12: a fee rule comes from a published schedule, never from memory."""

    def test_every_venue_declares_its_source(self):
        cfg = config.default()
        for name, venue in cfg.raw["venues"].items():
            with self.subTest(name):
                self.assertIn("source_url", venue)
                self.assertIn("verified", venue)
                if not venue["verified"]:
                    self.assertTrue(
                        venue.get("unverified_reason"),
                        "an unverified fee rule must say why it is unverified",
                    )

    def test_an_unknown_fee_rule_raises_rather_than_guessing(self):
        from cfb_edge.engine.pricing import UnsupportedVenue
        with self.assertRaises(UnsupportedVenue):
            executable(50.0, kind="exchange_cents", venue="mystery",
                       venue_config={"fee_rule": "probably_seven_percent"})


class BaselineIsTheConsensusNotThePrice(unittest.TestCase):
    """Law 4: the de-vigged consensus is the belief; the venue price is not.

    Regression. The first end-to-end run de-vigged the venue's price on our
    side against the consensus price on the other side, which is a two-way
    market no book was ever offering. It shifted `p_baseline` by 0.0046 on a
    -104 venue against a -108/-112 consensus, and it shifts it the same way
    every time the venue is the cheaper side, so it biases the whole ledger
    rather than adding noise to it.
    """

    def test_baseline_ignores_the_venue_price(self):
        consensus = Quote(event_id="e", sport="ncaaf", market="spreads",
                          side="home", line=-3.0, price=-104.0, other_price=-116.0,
                          consensus_price=-108.0, consensus_other_price=-112.0,
                          venue="kalshi")
        d = decide(consensus, [(mature(), "for")], cfg=config.default())
        self.assertAlmostEqual(
            d.p_baseline, devig([-108.0, -112.0], "multiplicative")[0], places=12
        )

    def test_the_mixed_pair_would_have_been_different(self):
        mixed = devig([-104.0, -112.0], "multiplicative")[0]
        honest = devig([-108.0, -112.0], "multiplicative")[0]
        self.assertGreater(abs(mixed - honest), 1e-3)

    def test_the_executable_price_is_still_the_venue_price(self):
        q = Quote(event_id="e", sport="ncaaf", market="spreads", side="home",
                  line=-3.0, price=-104.0, other_price=-116.0,
                  consensus_price=-108.0, consensus_other_price=-112.0,
                  venue="book_default")
        d = decide(q, [(mature(), "for")], cfg=config.default())
        at_venue = decide_at_price(d.p_post, executable(-104.0))
        self.assertAlmostEqual(d.ev, at_venue.ev, places=12)

    def test_falls_back_to_the_venue_pair_when_no_consensus_is_given(self):
        q = Quote(event_id="e", **SPREAD)
        self.assertEqual(q.baseline_pair, (-110.0, -110.0))


class EngineStamps(unittest.TestCase):
    """A row that cannot be traced to its rules cannot be re-examined."""

    def test_row_carries_version_and_spec_hash(self):
        d = decide(Quote(event_id="e", **SPREAD), [(mature(), "for")],
                   cfg=config.default())
        row = candidate_row(d, candidate_id="c1", logged_at="t", phase="SHADOW")
        self.assertEqual(row["engineVersion"], ENGINE_VERSION)
        self.assertEqual(len(row["specHash"]), 64)


if __name__ == "__main__":
    unittest.main()
