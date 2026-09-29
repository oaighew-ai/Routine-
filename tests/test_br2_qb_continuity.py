from __future__ import annotations

import unittest
from datetime import datetime, timezone

from cfb_edge.br2_qb_continuity import build


def manifest(at="2026-09-29T00:00:00Z"):
    return [
        {"kind":"play_stats","retrievedAt":at,"path":"raw/stats.json.gz","sha256":"a"*64},
        {"kind":"games","retrievedAt":at,"path":"raw/games.json.gz","sha256":"b"*64},
    ]


def game(gid, when, away="A", home="H"):
    return {"id":gid,"startDate":when,"awayTeam":away,"homeTeam":home,"completed":True}


def attempt(gid, team, athlete_id, name, play, stat_type):
    return {
        "gameId":gid,"team":team,"athleteId":athlete_id,"athleteName":name,
        "playId":play,"statType":stat_type,
    }


class QbContinuityTests(unittest.TestCase):
    def test_last_game_primary_passer_share_drives_feature(self):
        games=[
            game(1,"2026-09-05T16:00:00Z"),
            game(2,"2026-09-12T16:00:00Z"),
            game(3,"2026-09-19T16:00:00Z"),
        ]
        stats=[]
        # Home QB H1: 2/2, 2/2, 2/2 => continuity 1.0.
        for gid in (1,2,3):
            stats += [
                attempt(gid,"H","h1","Home QB",f"h{gid}a","Completion"),
                attempt(gid,"H","h1","Home QB",f"h{gid}b","Incompletion"),
            ]
        # Away latest primary A2. Across last three, A2 owns 2 of 6 attempts.
        for gid in (1,2):
            stats += [
                attempt(gid,"A","a1","Old QB",f"a{gid}a","Completion"),
                attempt(gid,"A","a1","Old QB",f"a{gid}b","Incompletion"),
            ]
        stats += [
            attempt(3,"A","a2","New QB","a3a","Completion"),
            attempt(3,"A","a2","New QB","a3b","Interception Thrown"),
        ]
        r=build(
            slate=[{"game":"A @ H","kickoff":"2026-10-03T16:00:00Z"}],
            play_stats=stats,prior_games=games,
            decision_time=datetime(2026,9,29,tzinfo=timezone.utc),
            source_manifest=manifest(),
        )
        row=r["rows"][0]
        self.assertTrue(row["auditGrade"])
        self.assertAlmostEqual(row["home"]["last3AttemptShare"],1.0)
        self.assertAlmostEqual(row["away"]["last3AttemptShare"],2/6)
        self.assertAlmostEqual(row["featureValue"],2/3)

    def test_duplicate_stat_rows_do_not_double_count(self):
        games=[game(1,"2026-09-19T16:00:00Z")]
        one=attempt(1,"H","h1","Home QB","p1","Completion")
        stats=[one,dict(one),attempt(1,"A","a1","Away QB","p2","Completion")]
        r=build(
            slate=[{"game":"A @ H","kickoff":"2026-10-03T16:00:00Z"}],
            play_stats=stats,prior_games=games,
            decision_time=datetime(2026,9,29,tzinfo=timezone.utc),
            source_manifest=manifest(),
        )
        self.assertEqual(r["rows"][0]["home"]["gamesUsed"][0]["teamAttempts"],1)

    def test_tied_latest_primary_passer_fails_closed(self):
        games=[game(1,"2026-09-19T16:00:00Z")]
        stats=[
            attempt(1,"H","h1","QB1","p1","Completion"),
            attempt(1,"H","h2","QB2","p2","Incompletion"),
            attempt(1,"A","a1","QB3","p3","Completion"),
        ]
        r=build(
            slate=[{"game":"A @ H","kickoff":"2026-10-03T16:00:00Z"}],
            play_stats=stats,prior_games=games,
            decision_time=datetime(2026,9,29,tzinfo=timezone.utc),
            source_manifest=manifest(),
        )
        self.assertFalse(r["rows"][0]["auditGrade"])
        self.assertIn("HOME_QB_CONTINUITY_UNAVAILABLE",r["rows"][0]["exclusions"])

    def test_source_after_decision_fails_closed(self):
        games=[game(1,"2026-09-19T16:00:00Z")]
        stats=[
            attempt(1,"H","h1","QB1","p1","Completion"),
            attempt(1,"A","a1","QB2","p2","Completion"),
        ]
        r=build(
            slate=[{"game":"A @ H","kickoff":"2026-10-03T16:00:00Z"}],
            play_stats=stats,prior_games=games,
            decision_time=datetime(2026,9,29,tzinfo=timezone.utc),
            source_manifest=manifest("2026-09-30T00:00:00Z"),
        )
        self.assertFalse(r["rows"][0]["auditGrade"])


if __name__=="__main__":
    unittest.main()
