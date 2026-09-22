import unittest

from cfb_edge import bayesian_regime as br


class BayesianRegimeTests(unittest.TestCase):
    def obs(self, week, i, gap, residual, move=0.0):
        return br.Observation(
            2026,
            week,
            str(i),
            f"A{i} @ H{i}",
            0.0,
            residual,
            gap,
            move,
        )

    def test_market_is_prior(self):
        rows = [
            self.obs(1, 1, 5, 10),
            self.obs(1, 2, -5, -10),
        ]
        weak = br.fit_slope(rows, prior_precision=1)
        strong = br.fit_slope(rows, prior_precision=1000)
        self.assertGreater(abs(weak.mean), abs(strong.mean))
        self.assertLess(strong.variance, weak.variance)

    def test_regime_boundaries_are_fixed(self):
        self.assertEqual(br.regime_name(3.999), "0-4")
        self.assertEqual(br.regime_name(-4.0), "4-8")
        self.assertEqual(br.regime_name(8.0), "8-12")
        self.assertEqual(br.regime_name(12.0), "12+")

    def test_team_state_uses_history_only(self):
        history = [
            br.Observation(2026, 1, "1", "A @ H", 0, 10, 0, 0)
        ]
        test = [
            br.Observation(2026, 2, "2", "A @ H", 0, -100, 0, 0)
        ]
        before = br.team_state_corrections(history, test, gain=0.2)
        mutated = [
            br.Observation(2026, 2, "2", "A @ H", 0, 100, 0, 0)
        ]
        after = br.team_state_corrections(history, mutated, gain=0.2)
        self.assertEqual(before, after)

    def test_week3_never_selects_hyperparameters(self):
        rows = []
        for week in (1, 2, 3):
            for i in range(8):
                gap = 5 if i % 2 == 0 else -5
                residual = (
                    (2 if week < 3 else 1000)
                    * (1 if gap > 0 else -1)
                )
                rows.append(
                    self.obs(
                        week,
                        week * 100 + i,
                        gap,
                        residual,
                        0.5 * (1 if gap > 0 else -1),
                    )
                )
        a = br.staged_holdout(rows)
        rows2 = [
            r
            if r.week < 3
            else br.Observation(
                r.season,
                r.week,
                r.game_id,
                r.game,
                r.market_home_margin,
                -r.actual_home_margin,
                r.projection_gap,
                r.close_market_margin,
            )
            for r in rows
        ]
        b = br.staged_holdout(rows2)
        self.assertEqual(
            a["outcomeResidual"]["globalBayesian"]["priorPrecision"],
            b["outcomeResidual"]["globalBayesian"]["priorPrecision"],
        )
        self.assertEqual(
            a["outcomeResidual"]["dynamicTeamState"]["gain"],
            b["outcomeResidual"]["dynamicTeamState"]["gain"],
        )
        self.assertEqual(
            a["outcomeResidual"]["ensemble"]["globalWeight"],
            b["outcomeResidual"]["ensemble"]["globalWeight"],
        )

    def test_harmful_component_is_rejected(self):
        rows = []
        for week in (1, 2, 3):
            for i in range(12):
                gap = 5 if i % 2 == 0 else -5
                sign = 1 if gap > 0 else -1
                residual = sign if week < 3 else -sign
                rows.append(
                    self.obs(
                        week,
                        week * 100 + i,
                        gap,
                        residual,
                        0.0,
                    )
                )
        report = br.staged_holdout(rows)
        self.assertEqual(report["status"], "SHADOW_ONLY")
        self.assertEqual(report["promotionEffect"], "NONE")
        self.assertEqual(
            report["outcomeResidual"]["globalBayesian"]["verdict"],
            "REJECTED_OOS",
        )


if __name__ == "__main__":
    unittest.main()
