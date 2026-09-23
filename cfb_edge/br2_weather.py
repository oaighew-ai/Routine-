"""Capture point-in-time kickoff weather for S04_BR2 via Open-Meteo.

Live capture is prospective. Backtests must use Open-Meteo archived forecast
runs, never realized/reanalysis weather as a substitute for what was knowable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

CONTRACT="CFB_EDGE_BR2_WEATHER_V1"
BASE="https://api.open-meteo.com/v1/forecast"


def _time(value: Any) -> datetime | None:
    if not value: return None
    try: dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except (TypeError,ValueError): return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(value: Any) -> float | None:
    if value in (None,"") or isinstance(value,bool): return None
    try: x=float(value)
    except (TypeError,ValueError): return None
    return x if math.isfinite(x) else None


def _norm(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().replace("&","and").split())


def _venue_index(rows: Sequence[Mapping[str,Any]]) -> dict[str,dict[str,Any]]:
    by_id={}; by_name={}
    for r in rows:
        lat,lon=_num(r.get("latitude")),_num(r.get("longitude"))
        if lat is None or lon is None: continue
        p={"venueId":r.get("id"),"venueName":r.get("name"),"latitude":lat,"longitude":lon,
           "timezone":r.get("timezone"),"dome":bool(r.get("dome"))}
        if r.get("id") is not None: by_id[str(r["id"])]=p
        if r.get("name"): by_name[_norm(r["name"])]=p
    return {"byId":by_id,"byName":by_name}


def _game_index(rows: Sequence[Mapping[str,Any]]) -> dict[str,Mapping[str,Any]]:
    out={}
    for r in rows:
        away=str(r.get("awayTeam") or "").strip(); home=str(r.get("homeTeam") or "").strip()
        if away and home: out[_norm(f"{away} @ {home}")]=r
    return out


def _game_venue(game: Mapping[str,Any], idx: Mapping[str,Any]) -> Mapping[str,Any] | None:
    if game.get("venueId") is not None and str(game["venueId"]) in idx["byId"]:
        return idx["byId"][str(game["venueId"])]
    return idx["byName"].get(_norm(game.get("venue"))) if game.get("venue") else None


def forecast_url(lat: float, lon: float) -> str:
    params={
        "latitude":f"{lat:.6f}","longitude":f"{lon:.6f}",
        "hourly":"temperature_2m,precipitation_probability,precipitation,wind_speed_10m,wind_gusts_10m",
        "temperature_unit":"fahrenheit","wind_speed_unit":"mph","precipitation_unit":"inch",
        "timezone":"UTC","forecast_days":"16",
    }
    return BASE+"?"+urllib.parse.urlencode(params)


def _nearest_hour(payload: Mapping[str,Any], kickoff: datetime) -> dict[str,Any] | None:
    hourly=payload.get("hourly") or {}
    times=hourly.get("time") or []
    parsed=[]
    for i,t in enumerate(times):
        dt=_time(t)
        if dt is not None: parsed.append((abs((dt-kickoff).total_seconds()),i,dt))
    if not parsed: return None
    _,i,dt=min(parsed,key=lambda x:x[0])
    if abs((dt-kickoff).total_seconds()) > 3600:
        return None
    def val(name):
        arr=hourly.get(name) or []
        return arr[i] if i < len(arr) else None
    return {
        "validTime":dt.isoformat(),
        "temperatureF":_num(val("temperature_2m")),
        "precipProbabilityPct":_num(val("precipitation_probability")),
        "precipitationIn":_num(val("precipitation")),
        "windMph":_num(val("wind_speed_10m")),
        "windGustMph":_num(val("wind_gusts_10m")),
    }


def capture(
    *,
    slate: Sequence[Mapping[str,Any]],
    games: Sequence[Mapping[str,Any]],
    venues: Sequence[Mapping[str,Any]],
    out_raw_dir: str | Path,
    retrieved_at: datetime | None=None,
    opener=urllib.request.urlopen,
) -> dict[str,Any]:
    now=retrieved_at or datetime.now(timezone.utc)
    gi=_game_index(games); vi=_venue_index(venues)
    raw_dir=Path(out_raw_dir); raw_dir.mkdir(parents=True,exist_ok=True)
    rows=[]; manifest=[]; fetched: dict[str, tuple[dict[str,Any],str,Path,int]] = {}

    for s in slate:
        game=str(s.get("game") or "").strip(); kickoff=_time(s.get("kickoff"))
        source=gi.get(_norm(game)); venue=_game_venue(source or {},vi) if source else None
        exclusions=[]
        if kickoff is None: exclusions.append("KICKOFF_MISSING")
        if venue is None: exclusions.append("VENUE_COORDINATES_MISSING")
        if source is None: exclusions.append("CFBD_WEEK_GAME_MISSING")

        if venue and venue.get("dome") is True and kickoff is not None:
            rows.append({
                "game":game,"kickoff":kickoff.isoformat(),"retrievedAt":now.isoformat(),
                "venue":dict(venue),"auditGrade":True,"windMph":0.0,
                "weatherSuppressedByDome":True,"forecast":None,
                "exclusions":[],
            })
            continue

        if exclusions:
            rows.append({"game":game,"kickoff":None if kickoff is None else kickoff.isoformat(),
                         "retrievedAt":now.isoformat(),"venue":None if venue is None else dict(venue),
                         "auditGrade":False,"windMph":None,"forecast":None,
                         "exclusions":sorted(set(exclusions))})
            continue

        url=forecast_url(float(venue["latitude"]),float(venue["longitude"]))
        try:
            if url in fetched:
                payload,sha,raw_path,raw_bytes=fetched[url]
            else:
                req=urllib.request.Request(url,headers={"User-Agent":"cfb-edge/1.0","Accept":"application/json"})
                with opener(req,timeout=60) as resp:
                    raw=resp.read()
                payload=json.loads(raw)
                sha=hashlib.sha256(raw).hexdigest()
                raw_path=raw_dir/f"{sha}.json"
                if not raw_path.exists(): raw_path.write_bytes(raw)
                raw_bytes=len(raw)
                fetched[url]=(payload,sha,raw_path,raw_bytes)
                manifest.append({"kind":"weather_forecast","source":"open-meteo","url":url,
                                 "retrievedAt":now.isoformat(),"sha256":sha,"bytes":raw_bytes,
                                 "path":str(raw_path)})
            point=_nearest_hour(payload,kickoff)
            if point is None:
                exclusions.append("KICKOFF_OUTSIDE_FORECAST_HORIZON")
                wind=None
            else:
                wind=point["windMph"]
                if wind is None: exclusions.append("WIND_MISSING")
            rows.append({
                "game":game,"kickoff":kickoff.isoformat(),"retrievedAt":now.isoformat(),
                "venue":dict(venue),"auditGrade":not exclusions,"windMph":wind,
                "weatherSuppressedByDome":False,"forecast":point,
                "source":{"provider":"open-meteo","url":url,"sha256":sha},
                "exclusions":sorted(set(exclusions)),
            })
        except Exception as exc:
            rows.append({"game":game,"kickoff":kickoff.isoformat(),"retrievedAt":now.isoformat(),
                         "venue":dict(venue),"auditGrade":False,"windMph":None,"forecast":None,
                         "exclusions":["WEATHER_FETCH_FAILED"],"errorType":type(exc).__name__})

    return {
        "schemaVersion":1,"contract":CONTRACT,"status":"DATA_COLLECTION_ONLY",
        "generatedAt":now.isoformat(),"provider":"open-meteo",
        "historicalReplayPolicy":"Use archived forecast runs for backtests; realized weather is not a PIT substitute.",
        "summary":{"games":len(rows),"auditGradeRows":sum(1 for r in rows if r.get("auditGrade")),
                   "domeRows":sum(1 for r in rows if r.get("weatherSuppressedByDome"))},
        "sourceManifest":manifest,"rows":rows,
    }


def _list(path):
    x=json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(x,list): raise SystemExit(f"{path}: expected JSON array")
    return x


def _slate(path):
    import csv
    with Path(path).open(newline="",encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slate",required=True); p.add_argument("--games",required=True)
    p.add_argument("--venues",required=True); p.add_argument("--raw-dir",required=True)
    p.add_argument("--out",required=True)
    a=p.parse_args(argv)
    r=capture(slate=_slate(a.slate),games=_list(a.games),venues=_list(a.venues),out_raw_dir=a.raw_dir)
    o=Path(a.out); o.parent.mkdir(parents=True,exist_ok=True)
    o.write_text(json.dumps(r,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(r["summary"],sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
