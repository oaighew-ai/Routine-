from __future__ import annotations

import unittest
from datetime import datetime, timezone

from cfb_edge.qb_usage_continuity import build


def passing_box(game_id, team_rows):
    return {
        "id": game_id,
        "teams": [
            {
                "team": team,
                "categories": [{
                    "name": "passing",
                    "types": [{
                        "name": "ATT",
                        "athletes": [
                            {"id": pid, "name": name, "stat": str(att)}
                            for pid, name, att in players
                        ],
                    }],
                }],
            }
            for team, players in team_rows
        ],
    }


class QbUsageContinuityTests(unittest.TestCase):
    def setUp(self):
        self.as_of=datetime(2026,9,29,1,0,tzinfo=timezone.utc)
        self.kickoff="2026-10-03T16:00:00Z"
        self.slate=[{"game":"A @ H","kickoff":self.kickoff}]
        self.games=[
            {"id":1,"startDate":"2026-09-05T16:00:00Z","completed":True},
            {"id":2,"startDate":"2026-09-12T16:00:00Z","completed":True},
            {"id":3,"startDate":"2026-09-19T16:00:00Z","completed":True},
            {"id":4,"startDate":"2026-10-10T16:00:00Z","completed":True},
        ]
        self.boxes=[
            passing_box(1,[("H",[("h1","Home QB",20),("h2","Backup H",5)]),
                           ("A",[("a1","Away QB",10),("a2","Backup A",10)])]),
            passing_box(2,[("H",[("h1","Home QB",22),("h2","Backup H",3)]),
                           ("A",[("a1","Away QB",15),("a2","Backup A",5)])]),
            passing_box(3,[("H",[("h1","Home QB",25)]),
                           ("A",[("a2","Backup A",18),("a1","Away QB",2)])]),
            # This future game must never enter the feature.
            passing_box(4,[("H",[("h2","Backup H",100)]),
                           ("A",[("a1","Away QB",100)])]),
        ]
        self.manifest=[
            {
                "kind":"player_box",
                "retrievedAt":"2026-09-28T20:00:00Z",
                "path":f"raw/player-w{week}.json.gz",
                "sha256":str(week)*64,
            }
            for week in (1,2,3)
        ]

    def test_last_game_leader_drives_base_continuity(self):
        r=build(
            slate=self.slate,prior_games=self.games,player_boxes=self.boxes,
            source_manifest=self.manifest,as_of=self.as_of,
        )
        row=r["rows"][0]
        self.assertTrue(row["auditGrade"])
        # H incumbent = Home QB: 67/75. A incumbent = Backup A: 33/60.
        self.assertAlmostEqual(row["home"]["continuity"],67/75)
        self.assertAlmostEqual(row["away"]["continuity"],33/60)
        self.assertAlmostEqual(row["featureValue"],67/75-33/60)
        self.assertEqual(row["home"]["starterSource"],"LAST_COMPLETED_GAME_ATTEMPT_LEADER")
        self.assertEqual(row["away"]["starterSource"],"LAST_COMPLETED_GAME_ATTEMPT_LEADER")
        self.assertNotIn("4",row["home"]["gamesConsidered"])

    def test_validated_official_starter_can_override_incumbent(self):
        official={
            "rows":[{
                "game":"A @ H","auditGrade":True,
                "home":{
                    "team":"H","firstListedQb":"Backup H",
                    "retrievedAt":"2026-09-28T21:00:00Z",
                    "sourceUrl":"https://h.example.edu/depth",
                    "packetSha256":"x",
                },
                "away":{
                    "team":"A","firstListedQb":"Away QB",
                    "retrievedAt":"2026-09-28T21:00:00Z",
                    "sourceUrl":"https://a.example.edu/depth",
                    "packetSha256":"y",
                },
            }]
        }
        r=build(
            slate=self.slate,prior_games=self.games,player_boxes=self.boxes,
            source_manifest=self.manifest,as_of=self.as_of,
            official_evidence=official,
        )
        row=r["rows"][0]
        self.assertTrue(row["auditGrade"])
        self.assertEqual(row["home"]["selectedQbName"],"Backup H")
        self.assertEqual(row["away"]["selectedQbName"],"Away QB")
        self.assertTrue(row["home"]["availabilityVerified"])
        self.assertAlmostEqual(row["home"]["continuity"],8/75)
        self.assertAlmostEqual(row["away"]["continuity"],27/60)

    def test_unmapped_official_qb_does_not_destroy_measured_base(self):
        official={
            "rows":[{
                "game":"A @ H","auditGrade":True,
                "home":{
                    "team":"H","firstListedQb":"Unknown New QB",
                    "retrievedAt":"2026-09-28T21:00:00Z",
                },
                "away":{
                    "team":"A","firstListedQb":"Away QB",
                    "retrievedAt":"2026-09-28T21:00:00Z",
                },
            }]
        }
        r=build(
            slate=self.slate,prior_games=self.games,player_boxes=self.boxes,
            source_manifest=self.manifest,as_of=self.as_of,
            official_evidence=official,
        )
        h=r["rows"][0]["home"]
        self.assertEqual(h["starterSource"],"LAST_COMPLETED_GAME_ATTEMPT_LEADER")
        self.assertFalse(h["availabilityVerified"])
        self.assertIn("OFFICIAL_QB_NOT_MAPPED_TO_RECENT_PARTICIPATION",h["notes"])

    def test_future_lineage_fails_closed(self):
        manifest=[dict(self.manifest[0],retrievedAt="2026-10-04T00:00:00Z")]
        r=build(
            slate=self.slate,prior_games=self.games,player_boxes=self.boxes,
            source_manifest=manifest,as_of=self.as_of,
        )
        row=r["rows"][0]
        self.assertFalse(row["auditGrade"])
        self.assertIsNone(row["featureValue"])
        self.assertIn("PLAYER_BOX_LINEAGE_INVALID",row["exclusions"])


if __name__=="__main__":
    unittest.main()
