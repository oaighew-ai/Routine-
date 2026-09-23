from __future__ import annotations

import unittest
from datetime import datetime, timezone

from cfb_edge.br2_context import build_context, haversine_miles


class Br2ContextTests(unittest.TestCase):
    def test_haversine_zero_and_known_distance(self):
        self.assertAlmostEqual(haversine_miles(33.749, -84.388, 33.749, -84.388), 0.0, places=6)
        # Atlanta to Athens, GA is roughly 60 miles great-circle.
        d=haversine_miles(33.749, -84.388, 33.9519, -83.3576)
        self.assertGreater(d, 55)
        self.assertLess(d, 65)

    def test_epa_line_and_travel_are_explicit_differences(self):
        slate=[{"game":"A @ H","kickoff":"2026-09-26T16:00:00Z"}]
        games=[{"awayTeam":"A","homeTeam":"H","venueId":10,"neutralSite":False}]
        teams=[
            {"school":"H","location":{"latitude":40.0,"longitude":-80.0}},
            {"school":"A","location":{"latitude":41.0,"longitude":-81.0}},
        ]
        venues=[{"id":10,"name":"H Stadium","latitude":40.0,"longitude":-80.0,"dome":False}]
        wepa=[
            {"team":"H","epa":{"total":0.30},"epaAllowed":{"total":0.10}},
            {"team":"A","epa":{"total":0.20},"epaAllowed":{"total":0.25}},
        ]
        advanced=[
            {"team":"H","offense":{"lineYards":3.2},"defense":{"lineYards":2.6}},
            {"team":"A","offense":{"lineYards":2.8},"defense":{"lineYards":3.0}},
        ]
        weather={"rows":[{"game":"A @ H","auditGrade":True,"windMph":12.0}]}
        r=build_context(
            slate=slate,week_games=games,teams=teams,venues=venues,
            wepa=wepa,advanced=advanced,qb_stats=[],weather=weather,qb_evidence=None,
            as_of=datetime(2026,9,23,tzinfo=timezone.utc),
        )
        row=r["rows"][0]
        self.assertAlmostEqual(row["features"]["epaDiff"],0.25)
        self.assertAlmostEqual(row["features"]["linePlayDiff"],0.8)
        self.assertLess(row["features"]["travelMilesDiff"],0)
        self.assertEqual(row["features"]["windMph"],12.0)
        self.assertIsNone(row["features"]["qbContinuityDiff"])
        self.assertFalse(row["audit"]["qbContinuityDiff"])


    def test_qb_continuity_uses_prior_week_primary_passer_share(self):
        slate=[{"game":"A @ H","kickoff":"2026-09-26T16:00:00Z"}]
        qb_stats=[
            {"team":"H","playerId":"h1","player":"Home QB1","position":"QB",
             "category":"passing","statType":"ATT","stat":"80"},
            {"team":"H","playerId":"h2","player":"Home QB2","position":"QB",
             "category":"passing","statType":"ATT","stat":"20"},
            {"team":"A","playerId":"a1","player":"Away QB1","position":"QB",
             "category":"passing","statType":"attempts","stat":"60"},
            {"team":"A","playerId":"a2","player":"Away QB2","position":"QB",
             "category":"passing","statType":"attempts","stat":"40"},
            # A non-attempt passing stat must not affect concentration.
            {"team":"H","playerId":"h1","player":"Home QB1","position":"QB",
             "category":"passing","statType":"YDS","stat":"900"},
        ]
        official={"rows":[{"game":"A @ H","auditGrade":True,"featureValue":-1,
                           "source":"official_depth_chart"}]}
        r=build_context(
            slate=slate,
            week_games=[{"awayTeam":"A","homeTeam":"H"}],
            teams=[],venues=[],wepa=[],advanced=[],qb_stats=qb_stats,
            weather=None,qb_evidence=official,
            as_of=datetime(2026,9,23,tzinfo=timezone.utc),
        )
        row=r["rows"][0]
        self.assertAlmostEqual(row["features"]["qbContinuityDiff"],0.2)
        self.assertTrue(row["audit"]["qbContinuityDiff"])
        self.assertEqual(row["evidence"]["qbContinuity"]["home"]["primaryPlayer"],"Home QB1")
        self.assertEqual(row["evidence"]["qbInformationState"]["source"],"official_depth_chart")
        # The official packet is context only and does not overwrite the metric.
        self.assertNotEqual(row["features"]["qbContinuityDiff"],-1)

    def test_qb_continuity_fails_closed_when_one_team_has_no_prior_attempts(self):
        r=build_context(
            slate=[{"game":"A @ H","kickoff":"2026-09-26T16:00:00Z"}],
            week_games=[{"awayTeam":"A","homeTeam":"H"}],
            teams=[],venues=[],wepa=[],advanced=[],
            qb_stats=[{"team":"H","playerId":"h1","player":"Home QB",
                       "category":"passing","statType":"ATT","stat":"20"}],
            weather=None,qb_evidence=None,
            as_of=datetime(2026,9,23,tzinfo=timezone.utc),
        )
        row=r["rows"][0]
        self.assertIsNone(row["features"]["qbContinuityDiff"])
        self.assertFalse(row["audit"]["qbContinuityDiff"])

    def test_neutral_site_travel_uses_both_program_origins(self):
        slate=[{"game":"A @ H","kickoff":"2026-09-26T16:00:00Z"}]
        games=[{"awayTeam":"A","homeTeam":"H","venueId":99,"neutralSite":True}]
        teams=[
            {"school":"H","location":{"latitude":40.0,"longitude":-80.0}},
            {"school":"A","location":{"latitude":35.0,"longitude":-90.0}},
        ]
        venues=[{"id":99,"name":"Neutral","latitude":38.0,"longitude":-85.0,"dome":False}]
        r=build_context(
            slate=slate,week_games=games,teams=teams,venues=venues,
            wepa=[],advanced=[],qb_stats=[],weather=None,qb_evidence=None,
            as_of=datetime(2026,9,23,tzinfo=timezone.utc),
        )
        e=r["rows"][0]["evidence"]["travel"]
        self.assertGreater(e["homeMiles"],0)
        self.assertGreater(e["awayMiles"],0)
        self.assertTrue(e["neutralSite"])

    def test_post_kickoff_context_is_never_audited(self):
        r=build_context(
            slate=[{"game":"A @ H","kickoff":"2026-09-26T16:00:00Z"}],
            week_games=[{"awayTeam":"A","homeTeam":"H","venueId":10}],
            teams=[
                {"school":"H","location":{"latitude":40.0,"longitude":-80.0}},
                {"school":"A","location":{"latitude":41.0,"longitude":-81.0}},
            ],
            venues=[{"id":10,"name":"H Stadium","latitude":40.0,"longitude":-80.0}],
            wepa=[
                {"team":"H","epa":{"total":1},"epaAllowed":{"total":0}},
                {"team":"A","epa":{"total":0},"epaAllowed":{"total":1}},
            ],
            advanced=[],qb_stats=[],
            weather=None,qb_evidence=None,
            as_of=datetime(2026,9,26,17,tzinfo=timezone.utc),
        )
        row=r["rows"][0]
        self.assertFalse(row["pregameEligible"])
        self.assertTrue(all(v is None for v in row["features"].values()))
        self.assertTrue(all(v is False for v in row["audit"].values()))


if __name__=="__main__":
    unittest.main()
