"""Freeze and grade the Week 6 recovered-open CLV cohort.

This is deliberately separate from S02 delivery authority. The 27 opening rows
were recovered from venue-native candles and registered before kickoff. This
module converts that frozen registry into S04_ES2-compatible inputs and later
builds a close report from the append-only Kalshi capture log.

No side, threshold, opening line, or cohort membership may be changed after the
decision freeze.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .clv import GRADEABLE

FREEZE_CONTRACT = "CFB_EDGE_WEEK6_CLV_FREEZE_V1"
DECISION_CONTRACT = "CFB_EDGE_WEEK6_CLV_DECISION_FREEZE_V1"
CLOSE_CONTRACT = "CFB_EDGE_WEEK6_CLV_CLOSE_REPORT_V1"


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except (TypeError, ValueError):
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(value: Any) -> float | None:
    if value in (None,"") or isinstance(value,bool):
        return None
    try:
        return float(value)
    except (TypeError,ValueError):
        return None


def _sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_freeze(path: str | Path) -> dict[str, Any]:
    obj=json.loads(Path(path).read_text(encoding="utf-8"))
    if obj.get("contract") != FREEZE_CONTRACT:
        raise ValueError("unexpected Week 6 CLV freeze contract")
    rows=obj.get("rows") or []
    if len(rows) != int(obj.get("entryRowsFrozen") or -1):
        raise ValueError("freeze row count does not match entryRowsFrozen")
    games=[str(r.get("game") or "") for r in rows]
    if not games or len(games) != len(set(games)):
        raise ValueError("freeze games are empty or duplicated")
    registered=_time(obj.get("registeredAt"))
    if registered is None:
        raise ValueError("freeze registration time missing")
    for row in rows:
        kickoff=_time(row.get("kickoff"))
        observed=_time(row.get("openObservedAt"))
        recovered=_time(row.get("recoveryRetrievedAt"))
        lag=_num(row.get("openLagSeconds"))
        if not kickoff or not observed or not recovered:
            raise ValueError(f"{row.get('game')}: freeze timestamps incomplete")
        if not (observed < recovered <= registered < kickoff):
            raise ValueError(f"{row.get('game')}: freeze chronology invalid")
        if lag is None or lag < -60 or lag > 900:
            raise ValueError(f"{row.get('game')}: opening lag outside frozen tolerance")
        if not row.get("evidenceSha256") or len(str(row["evidenceSha256"])) != 64:
            raise ValueError(f"{row.get('game')}: opening evidence hash invalid")
    return obj


def write_opens_csv(freeze: Mapping[str, Any], out: str | Path) -> None:
    target=Path(out)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("w",newline="",encoding="utf-8") as fh:
        w=csv.writer(fh)
        w.writerow([
            "game","opening_line","source","first_seen",
            "venue_open_time","open_lag_seconds",
        ])
        for row in freeze.get("rows") or []:
            w.writerow([
                row["game"],row["openingHomeLine"],"true_open",
                row["openObservedAt"],row["venueOpenTime"],row["openLagSeconds"],
            ])


def freeze_decisions(
    *,
    source_report: Mapping[str, Any],
    cohort_freeze: Mapping[str, Any],
    cohort_freeze_sha256: str,
    decision_time: datetime,
) -> dict[str, Any]:
    if decision_time.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    registered=_time(cohort_freeze.get("registeredAt"))
    if registered is None or decision_time < registered:
        raise ValueError("decision freeze cannot predate cohort registration")
    frozen_games={r["game"] for r in cohort_freeze.get("rows") or []}
    for row in source_report.get("candidateAuditRows") or []:
        if row.get("auditGrade") and row.get("game") not in frozen_games:
            raise ValueError("S04_ES2 admitted a game outside the frozen 27-row cohort")
    top=list(source_report.get("topFive") or [])
    inspected=list(source_report.get("inspectedLiveRows") or [])
    candidates=list(source_report.get("candidateAuditRows") or [])
    for row in [*top,*inspected,*candidates]:
        kickoff=_time(row.get("kickoff"))
        if kickoff and decision_time >= kickoff:
            raise ValueError(f"{row.get('game')}: decision freeze occurred after kickoff")
    basis={
        "sourceContract":source_report.get("contract"),
        "modelId":source_report.get("modelId"),
        "modelVersion":source_report.get("modelVersion"),
        "generatedAt":source_report.get("generatedAt"),
        "openCohort":source_report.get("openCohort"),
        "candidateAuditRows":candidates,
        "inspectedLiveRows":inspected,
        "qualifiedCount":source_report.get("qualifiedCount"),
        "topFive":top,
    }
    evidence_sha=hashlib.sha256(
        json.dumps(basis,sort_keys=True,separators=(",",":")).encode()
    ).hexdigest()
    return {
        "schemaVersion":1,
        "contract":DECISION_CONTRACT,
        "cohortId":cohort_freeze.get("cohortId"),
        "frozenAt":decision_time.astimezone(timezone.utc).isoformat(),
        "cohortFreezeSha256":cohort_freeze_sha256,
        "sourceReportSha256":evidence_sha,
        "sourceContract":source_report.get("contract"),
        "modelId":source_report.get("modelId"),
        "modelVersion":source_report.get("modelVersion"),
        "status":"FROZEN_PRE_OUTCOME",
        "deliveryEffect":"NONE",
        "stakingEffect":"NONE",
        "promotionEffect":"NONE",
        "candidateAuditRows":candidates,
        "inspectedLiveRows":inspected,
        "qualifiedCount":source_report.get("qualifiedCount"),
        "topFive":top,
    }


def build_close_report(
    *,
    freeze: Mapping[str, Any],
    log_path: str | Path,
    now: datetime,
    maximum_close_age_seconds: int = 900,
) -> dict[str, Any]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    by_game={r["game"]:[] for r in freeze.get("rows") or []}
    path=Path(log_path)
    if path.exists():
        opener=gzip.open if path.suffix==".gz" else open
        with opener(path,"rt",encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec=json.loads(line)
                poll=_time(rec.get("polled_at"))
                for q in rec.get("quotes") or []:
                    game=str(q.get("game") or "")
                    if game not in by_game or q.get("market")!="spread":
                        continue
                    at=_time(q.get("seen_at"))
                    line_value=_num(q.get("line"))
                    if not poll or not at or line_value is None or at>poll:
                        continue
                    by_game[game].append({
                        "book":q.get("book"),
                        "derivedHomeLine":line_value,
                        "observedAt":at.isoformat(),
                        "pollAt":poll.isoformat(),
                        "eventTicker":q.get("event_ticker"),
                        "marketTickers":list(q.get("market_tickers") or []),
                    })

    games=[]
    for entry in freeze.get("rows") or []:
        game=entry["game"]
        kickoff=_time(entry.get("kickoff"))
        observations=[
            x for x in by_game.get(game,[])
            if kickoff and _time(x["observedAt"]) <= kickoff
        ]
        observations.sort(key=lambda x:_time(x["observedAt"]))
        last=observations[-1] if observations else None
        close_at=_time(last["observedAt"]) if last else None
        close_age=(kickoff-close_at).total_seconds() if kickoff and close_at else None
        exclusions=[]
        if now < kickoff:
            exclusions.append("GAME_NOT_STARTED")
        if last is None:
            exclusions.append("PRE_KICKOFF_CLOSE_MISSING")
        elif close_age is None or close_age < -1 or close_age > maximum_close_age_seconds:
            exclusions.append("CLOSE_NOT_FRESH_ENOUGH")
        games.append({
            "game":game,
            "kickoff":entry.get("kickoff"),
            "auditGradeOpen":True,
            "openClassification":"true_open",
            "firstSeen":entry.get("openObservedAt"),
            "venueOpenTime":entry.get("venueOpenTime"),
            "openLagSeconds":entry.get("openLagSeconds"),
            "openingHomeLine":entry.get("openingHomeLine"),
            "observations":observations[-6:],
            "closeObservedAt":None if close_at is None else close_at.isoformat(),
            "closeAgeSeconds":close_age,
            "gradeableClose":not exclusions,
            "exclusions":exclusions,
        })

    gradeable=sum(bool(x["gradeableClose"]) for x in games)
    return {
        "schemaVersion":1,
        "contract":CLOSE_CONTRACT,
        "cohortId":freeze.get("cohortId"),
        "generatedAt":now.astimezone(timezone.utc).isoformat(),
        "logSha256":_sha(path) if path.exists() else None,
        "maximumCloseAgeSeconds":maximum_close_age_seconds,
        "summary":{
            "frozenOpenRows":len(games),
            "gradeableCloseRows":gradeable,
            "pendingOrExcludedRows":len(games)-gradeable,
        },
        "games":games,
    }


def build_clv_gate_csv(
    *,
    grades: Mapping[str, Any],
    cohort: str,
    week: int,
    out: str | Path,
) -> dict[str, Any]:
    rows=[]
    for row in grades.get("rows") or []:
        if row.get("cohort") != cohort:
            continue
        game=str(row.get("game") or "")
        side=str(row.get("side") or "")
        opening=_num(row.get("openingHomeLine"))
        closing=_num(row.get("closingHomeLine"))
        if "@" not in game or opening is None:
            continue
        away,home=(x.strip() for x in game.split("@",1))
        if side==home:
            entry_side=opening
            close_side=closing
        elif side==away:
            entry_side=-opening
            close_side=None if closing is None else -closing
        else:
            continue
        if row.get("gradeable") is not True:
            close_side=None
        rows.append({
            "game":game,
            "week":week,
            "open_line":entry_side,
            "close_line":close_side,
            "source":(row.get("provenance") or {}).get("entrySource") or "",
        })
    target=Path(out)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("w",newline="",encoding="utf-8") as fh:
        w=csv.DictWriter(
            fh,fieldnames=["game","week","open_line","close_line","source"]
        )
        w.writeheader()
        for row in rows:
            w.writerow({
                **row,
                "close_line":"" if row["close_line"] is None else row["close_line"],
            })
    return {
        "cohort":cohort,
        "rows":len(rows),
        # GRADEABLE in clv.py is the only authority on which provenance
        # counts, and it is {true_open, fill}. Hardcoding "true_open" here
        # reported a real fill -- an order that actually filled, the strongest
        # evidence there is -- as ungradeable, while cfb_edge.clv_gate graded
        # it. Two numbers with the same name and different answers.
        "gradeableRows":sum(
            r["close_line"] is not None and r["source"] in GRADEABLE
            for r in rows
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)

    po=sub.add_parser("opens-csv")
    po.add_argument("--freeze",required=True)
    po.add_argument("--out",required=True)

    pd=sub.add_parser("freeze-decisions")
    pd.add_argument("--freeze",required=True)
    pd.add_argument("--report",required=True)
    pd.add_argument("--out",required=True)

    pc=sub.add_parser("close-report")
    pc.add_argument("--freeze",required=True)
    pc.add_argument("--log",required=True)
    pc.add_argument("--out",required=True)
    pc.add_argument("--maximum-close-age-seconds",type=int,default=900)

    pg=sub.add_parser("clv-csv")
    pg.add_argument("--grades",required=True)
    pg.add_argument("--cohort",required=True,choices=["DIRECTIONAL_SIGNAL","EXECUTABLE_SHADOW"])
    pg.add_argument("--week",type=int,required=True)
    pg.add_argument("--out",required=True)

    args=p.parse_args(argv)
    if args.command=="clv-csv":
        grades=json.loads(Path(args.grades).read_text(encoding="utf-8"))
        summary=build_clv_gate_csv(
            grades=grades,cohort=args.cohort,week=args.week,out=args.out
        )
        print(json.dumps(summary,sort_keys=True))
        return 0

    freeze=load_freeze(args.freeze)
    if args.command=="opens-csv":
        write_opens_csv(freeze,args.out)
        return 0
    if args.command=="freeze-decisions":
        report=json.loads(Path(args.report).read_text(encoding="utf-8"))
        result=freeze_decisions(
            source_report=report,
            cohort_freeze=freeze,
            cohort_freeze_sha256=_sha(args.freeze),
            decision_time=datetime.now(timezone.utc),
        )
    else:
        result=build_close_report(
            freeze=freeze,log_path=args.log,now=datetime.now(timezone.utc),
            maximum_close_age_seconds=args.maximum_close_age_seconds,
        )
    target=Path(args.out)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(result.get("summary") or {
        "qualifiedCount":result.get("qualifiedCount"),
        "topFive":len(result.get("topFive") or []),
    },sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
