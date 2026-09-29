from __future__ import annotations

import unittest
from datetime import datetime, timezone

from cfb_edge.br2_epa import build


def manifest(at="2026-09-29T00:00:00Z"):
    return [{
        "kind":"sdv_adjusted_epa_weekly","retrievedAt":at,
        "path":"raw/epa.csv.gz","sha256":"a"*64,
    }]


class AdjustedEpaTests(unittest.TestCase):
    def test_exact_prior_week_and_team_aliases(self):
        rows=[
            {"season":"2026","through_week":"4","pos_team":"Connecticut",
             "adj_off_epa":"0.30","adj_def_epa":"0.10","net_adj_epa":"0.20"},
            {"season":"2026","through_week":"4","pos_team":"Hawaii",
             "adj_off_epa":"0.10","adj_def_epa":"0.15","net_adj_epa":"-0.05"},
            {"season":"2026","through_week":"3","pos_team":"Connecticut",
             "adj_off_epa":"9","adj_def_epa":"0","net_adj_epa":"9"},
        ]
        r=build(
            csv_rows=rows,
            slate=[{"game":"Hawai'i @ UConn"}],
            season=2026,through_week=4,
            decision_time=datetime(2026,9,29,tzinfo=timezone.utc),
            source_manifest=manifest(),
        )
        by={x["team"]:x for x in r["rows"]}
        self.assertEqual(set(by),{"UConn","Hawai'i"})
        self.assertAlmostEqual(by["UConn"]["netAdjustedEpa"],0.20)
        self.assertEqual(r["summary"]["auditGradeTeams"],2)

    def test_duplicate_resolution_is_removed(self):
        rows=[
            {"season":"2026","through_week":"4","pos_team":"Connecticut",
             "adj_off_epa":"0.3","adj_def_epa":"0.1","net_adj_epa":"0.2"},
            {"season":"2026","through_week":"4","pos_team":"UConn",
             "adj_off_epa":"0.3","adj_def_epa":"0.1","net_adj_epa":"0.2"},
        ]
        r=build(
            csv_rows=rows,slate=[{"game":"A @ UConn"}],
            season=2026,through_week=4,
            decision_time=datetime(2026,9,29,tzinfo=timezone.utc),
            source_manifest=manifest(),
        )
        self.assertEqual(r["rows"],[])
        self.assertEqual(r["summary"]["duplicateResolvedTeams"],["UConn"])

    def test_post_decision_source_never_audit_grade(self):
        rows=[{"season":"2026","through_week":"4","pos_team":"A",
               "adj_off_epa":"0.3","adj_def_epa":"0.1","net_adj_epa":"0.2"}]
        r=build(
            csv_rows=rows,slate=[{"game":"A @ H"}],
            season=2026,through_week=4,
            decision_time=datetime(2026,9,29,tzinfo=timezone.utc),
            source_manifest=manifest("2026-09-30T00:00:00Z"),
        )
        self.assertEqual(r["summary"]["auditGradeTeams"],0)
        self.assertFalse(r["rows"][0]["auditGrade"])


if __name__=="__main__":
    unittest.main()
