from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from cfb_edge.opponent_adjusted_epa import fit_csv


class OpponentAdjustedEpaTests(unittest.TestCase):
    def write_fixture(self,path:Path):
        fields=[
            "year","week","game_id","id_play","pos_team","def_pos_team",
            "EPA","offense_play","completed",
        ]
        rows=[
            # A offense is consistently strong.
            {"year":2026,"week":1,"game_id":1,"id_play":1,"pos_team":"A","def_pos_team":"B","EPA":1.0,"offense_play":"1","completed":"1"},
            {"year":2026,"week":1,"game_id":1,"id_play":2,"pos_team":"A","def_pos_team":"B","EPA":0.8,"offense_play":"1","completed":"1"},
            {"year":2026,"week":2,"game_id":2,"id_play":3,"pos_team":"A","def_pos_team":"C","EPA":0.7,"offense_play":"1","completed":"1"},
            {"year":2026,"week":1,"game_id":1,"id_play":4,"pos_team":"B","def_pos_team":"A","EPA":-0.5,"offense_play":"1","completed":"1"},
            {"year":2026,"week":2,"game_id":2,"id_play":5,"pos_team":"C","def_pos_team":"A","EPA":-0.3,"offense_play":"1","completed":"1"},
            {"year":2026,"week":2,"game_id":3,"id_play":6,"pos_team":"B","def_pos_team":"C","EPA":0.0,"offense_play":"1","completed":"1"},
            # Must be excluded: future week and non-offense row.
            {"year":2026,"week":5,"game_id":9,"id_play":7,"pos_team":"B","def_pos_team":"A","EPA":10.0,"offense_play":"1","completed":"1"},
            {"year":2026,"week":2,"game_id":2,"id_play":8,"pos_team":"B","def_pos_team":"A","EPA":10.0,"offense_play":"0","completed":"1"},
        ]
        with path.open("w",newline="",encoding="utf-8") as fh:
            w=csv.DictWriter(fh,fieldnames=fields); w.writeheader(); w.writerows(rows)

    def test_fit_is_cutoff_clean_and_replayable(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            src=root/"pbp.csv"; self.write_fixture(src)
            archive=root/"archive"
            kwargs=dict(
                season=2026,through_week=2,archive_used_dir=archive,
                source_url="https://example/pbp.csv",
                source_sha256="f"*64,retrieved_at="2026-09-29T01:00:00Z",
            )
            rows1,meta1=fit_csv(src,**kwargs)
            rows2,meta2=fit_csv(src,**kwargs)
            by={r["team"]:r for r in rows1}
            self.assertEqual(meta1["eligiblePlays"],6)
            self.assertEqual(meta1["throughWeek"],2)
            self.assertEqual(meta1["archive"]["sha256"],meta2["archive"]["sha256"])
            self.assertTrue(Path(meta1["archive"]["path"]).exists())
            self.assertGreater(by["A"]["netOpponentAdjustedEpa"],by["B"]["netOpponentAdjustedEpa"])
            self.assertEqual(rows1,rows2)

    def test_archive_hash_matches_canonical_used_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); src=root/"pbp.csv"; self.write_fixture(src)
            _,meta=fit_csv(
                src,season=2026,through_week=2,archive_used_dir=root/"a",
                source_url="u",source_sha256="a"*64,
                retrieved_at="2026-09-29T01:00:00Z",
            )
            self.assertEqual(len(meta["archive"]["sha256"]),64)
            self.assertEqual(meta["contract"],"CFB_EDGE_OA_EPA_V1")


if __name__=="__main__":
    unittest.main()
