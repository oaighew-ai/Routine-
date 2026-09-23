from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cfb_edge.br2_weather import capture


class _Resp:
    def __init__(self,payload):
        self.raw=json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self): return self.raw


class Br2WeatherTests(unittest.TestCase):
    def test_nearest_kickoff_hour_and_raw_hash(self):
        payload={"hourly":{
            "time":["2026-09-26T15:00","2026-09-26T16:00","2026-09-26T17:00"],
            "temperature_2m":[70,72,71],
            "precipitation_probability":[10,20,30],
            "precipitation":[0,0.01,0.02],
            "wind_speed_10m":[8,12,14],
            "wind_gusts_10m":[15,20,22],
        }}
        def opener(req,timeout=0): return _Resp(payload)
        with tempfile.TemporaryDirectory() as td:
            r=capture(
                slate=[{"game":"A @ H","kickoff":"2026-09-26T16:20:00Z"}],
                games=[{"awayTeam":"A","homeTeam":"H","venueId":1}],
                venues=[{"id":1,"name":"Stadium","latitude":40,"longitude":-80,"dome":False}],
                out_raw_dir=td,
                retrieved_at=datetime(2026,9,23,tzinfo=timezone.utc),
                opener=opener,
            )
            row=r["rows"][0]
            self.assertTrue(row["auditGrade"])
            self.assertEqual(row["windMph"],12.0)
            self.assertEqual(row["forecast"]["validTime"],"2026-09-26T16:00:00+00:00")
            self.assertEqual(len(r["sourceManifest"]),1)
            self.assertTrue(Path(r["sourceManifest"][0]["path"]).exists())

    def test_two_outdoor_venues_share_one_batch_request(self):
        payloads=[
            {"hourly":{
                "time":["2026-09-26T16:00"],
                "temperature_2m":[70],"precipitation_probability":[0],
                "precipitation":[0],"wind_speed_10m":[8],"wind_gusts_10m":[12],
            }},
            {"hourly":{
                "time":["2026-09-26T16:00"],
                "temperature_2m":[75],"precipitation_probability":[0],
                "precipitation":[0],"wind_speed_10m":[14],"wind_gusts_10m":[20],
            }},
        ]
        calls=[]
        def opener(req,timeout=0):
            calls.append(req.full_url)
            return _Resp(payloads)
        with tempfile.TemporaryDirectory() as td:
            r=capture(
                slate=[
                    {"game":"A @ H","kickoff":"2026-09-26T16:00:00Z"},
                    {"game":"B @ J","kickoff":"2026-09-26T16:00:00Z"},
                ],
                games=[
                    {"awayTeam":"A","homeTeam":"H","venueId":1},
                    {"awayTeam":"B","homeTeam":"J","venueId":2},
                ],
                venues=[
                    {"id":1,"name":"One","latitude":40,"longitude":-80,"dome":False},
                    {"id":2,"name":"Two","latitude":35,"longitude":-90,"dome":False},
                ],
                out_raw_dir=td,
                retrieved_at=datetime(2026,9,23,tzinfo=timezone.utc),
                opener=opener,
            )
            self.assertEqual(len(calls),1)
            self.assertEqual(r["summary"]["batchRequests"],1)
            self.assertEqual(r["summary"]["successfulBatchRequests"],1)
            self.assertEqual([x["windMph"] for x in r["rows"]],[8.0,14.0])
            self.assertEqual(r["rows"][1]["source"]["locationIndex"],1)

    def test_dome_neutralizes_wind_without_network(self):
        def opener(*args,**kwargs):
            raise AssertionError("network should not be called for dome")
        with tempfile.TemporaryDirectory() as td:
            r=capture(
                slate=[{"game":"A @ H","kickoff":"2026-09-26T16:00:00Z"}],
                games=[{"awayTeam":"A","homeTeam":"H","venueId":1}],
                venues=[{"id":1,"name":"Dome","latitude":40,"longitude":-80,"dome":True}],
                out_raw_dir=td,
                retrieved_at=datetime(2026,9,23,tzinfo=timezone.utc),
                opener=opener,
            )
            row=r["rows"][0]
            self.assertTrue(row["auditGrade"])
            self.assertTrue(row["weatherSuppressedByDome"])
            self.assertEqual(row["windMph"],0.0)


if __name__=="__main__":
    unittest.main()
