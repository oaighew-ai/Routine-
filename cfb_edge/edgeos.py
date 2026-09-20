"""`python3 -m cfb_edge.edgeos <capture|scan|grade>`: the automation entry point.

The `run-edge` skill and the routines both call this, so an interactive session
and a scheduled run execute the identical procedure. Every subcommand writes a
run receipt, including when it fails: a run that dies quietly is the failure
mode this whole project exists to prevent.

Output is a compact summary by design. §4 forbids reading raw snapshots or whole
ledger files into a conversation, and the easiest way to honour that is for the
scripts never to print them.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import runner
from .engine import config
from .engine.clv2 import gate2_report
from .engine.evidence import SystemRecord
from .ledger import CANDIDATE, GRADE, load
from .ledger.writer import write_json


def _ledger(root: str) -> Path:
    return Path(root)


def _run_id(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H-%M-%SZ")


def _systems(path: Path) -> dict[str, SystemRecord]:
    """Load systems from the ledger. Never seeds one that is not there.

    BUILD_PROMPT §5: seed systems only from owner-supplied records, and never
    invent a system, a record or a line. An unknown system in an inbox fire
    produces a candidate with no evidence, which is the honest answer, rather
    than a default record that would look like evidence.
    """
    out: dict[str, SystemRecord] = {}
    if not path.exists():
        return out
    for row in load(path):
        record = row.get("providerRecord") or {}
        out[row["id"]] = SystemRecord(
            system_id=row["id"],
            family=row.get("family", "system"),
            wins_provider=int(record.get("w", 0)),
            losses_provider=int(record.get("l", 0)),
        )
    return out


def cmd_capture(args) -> int:
    """One odds pull, stored as a snapshot."""
    from .providers.oddsapi import CreditCapReached, OddsApiUnreachable, fetch_pull

    now = datetime.now(timezone.utc)
    ledger = _ledger(args.ledger)
    receipt = runner.Receipt(run_id=_run_id(now), mode="capture",
                             started_at=now.isoformat())
    books = [b.strip() for b in args.bookmakers.split(",") if b.strip()]
    try:
        pull = fetch_pull(sport=args.sport, bookmakers=books,
                          markets=tuple(args.markets.split(",")),
                          credit_cap_monthly=args.credit_cap)
    except (OddsApiUnreachable, CreditCapReached) as exc:
        receipt.step("pull odds", False, str(exc))
        receipt.write(ledger / "runs", now)
        print(f"FAILED at pull odds: {exc}", file=sys.stderr)
        return 1

    path = ledger / "snapshots" / pull.sport / f"{pull.fetched_at.replace(':', '-')}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump({
            "sport": pull.sport, "fetchedAt": pull.fetched_at,
            "markets": list(pull.markets), "bookSet": list(pull.book_set),
            "events": [dict(e) for e in pull.events],
            "creditsRemaining": pull.credits_remaining,
        }, fh, sort_keys=True)
    receipt.credits_remaining = pull.credits_remaining
    receipt.step("pull odds", True, pull.summary())
    receipt.step("write snapshot", True, str(path.relative_to(ledger)))
    receipt.write(ledger / "runs", now)
    print(pull.summary())
    return 0


def cmd_scan(args) -> int:
    """Decide every fire in the inbox, and log every decision."""
    now = datetime.now(timezone.utc)
    ledger = _ledger(args.ledger)
    cfg = config.load(args.config) if args.config else config.default()
    receipt = runner.Receipt(run_id=_run_id(now), mode="scan",
                             started_at=now.isoformat())

    fires = runner.read_inbox(ledger / "inbox")
    receipt.step("read inbox", True, f"{len(fires)} fire(s)")

    snapshots = runner.Snapshots.load(ledger / "snapshots" / args.sport_key)
    age = snapshots.age_seconds(now)
    receipt.source_ages = {"odds": age}
    receipt.step("load snapshots", bool(snapshots.pulls),
                 f"{len(snapshots.pulls)} pull(s), newest "
                 f"{'n/a' if age is None else f'{age / 60:.0f}m'} old")

    systems = _systems(ledger / "systems.jsonl")
    result = runner.scan(fires=fires, snapshots=snapshots, systems=systems,
                         cfg=cfg, venue=args.venue, sport=args.sport, now=now)
    receipt.reason_counts = dict(result.reason_counts)

    if result.rows and not args.dry_run:
        runner.write_candidates(ledger / "candidates.jsonl", result.rows)
        receipt.step("write candidates", True, f"{len(result.rows)} row(s)")
    else:
        receipt.step("write candidates", True,
                     "dry run, nothing written" if args.dry_run else "no rows")

    receipt.write(ledger / "runs", now)
    print(result.summary())
    return 0


def cmd_grade(args) -> int:
    """Report Gate 2 from the graded rows already on disk.

    Pulling closes and lag prices needs the historical endpoint and a paid plan,
    so the fetch half of this runs in the routine's environment, not here. What
    this does is the part that must never be guessed: recompute the gate from
    rows, every time, and print it.
    """
    now = datetime.now(timezone.utc)
    ledger = _ledger(args.ledger)
    cfg = config.load(args.config) if args.config else config.default()
    receipt = runner.Receipt(run_id=_run_id(now), mode="grade",
                             started_at=now.isoformat())

    candidates = {c["id"]: c for c in load(ledger / "candidates.jsonl", CANDIDATE)}
    grades = load(ledger / "grades.jsonl", GRADE)
    receipt.step("load ledger", True,
                 f"{len(candidates)} candidate(s), {len(grades)} grade(s)")

    rows = []
    for g in grades:
        c = candidates.get(g["candidateId"])
        if c is None:
            continue
        rows.append({
            "decision": c["decision"],
            "sport": c["sport"],
            "clvLagPct": g.get("clvLagPct"),
            "clvMethod": g.get("clvMethod"),
            "gradeable": c.get("priceSource") in ("capture", "fill"),
            "slateDate": (c.get("startsAt") or c["loggedAt"])[:10],
        })

    report = gate2_report(rows, min_rows=cfg.gate2_min_rows,
                          min_clusters=cfg.gate2_min_clusters,
                          alpha=cfg.gate2_alpha)
    receipt.step("gate 2", True, f"{len(rows)} graded row(s)")
    receipt.write(ledger / "runs", now)

    print("Gate 2 (A5, clustered by slate date; D5 adds the cluster minimum)")
    for line in report:
        print("  " + line.describe())
    flags = {"LUCK_RISK": 0, "STALE": 0, "UNMAPPED": 0}
    for c in candidates.values():
        for code in c.get("reasonCodes", []):
            if code in flags:
                flags[code] += 1
    print("  " + ", ".join(f"{k} {v}" for k, v in sorted(flags.items())))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cfb_edge.edgeos", description=__doc__)
    p.add_argument("--ledger", default="ledger",
                   help="ledger root (D3: the capture-data branch)")
    p.add_argument("--config", default=None, help="override config/edge_os.json")
    sub = p.add_subparsers(dest="mode", required=True)

    c = sub.add_parser("capture", help="one odds pull into snapshots/")
    c.add_argument("--sport", default="ncaaf")
    c.add_argument("--bookmakers",
                   default="kalshi,pinnacle,draftkings,fanduel,betmgm")
    c.add_argument("--markets", default="h2h,spreads,totals")
    c.add_argument("--credit-cap", type=float, default=None)
    c.set_defaults(func=cmd_capture)

    s = sub.add_parser("scan", help="decide the inbox against the latest snapshot")
    s.add_argument("--sport", default="ncaaf")
    s.add_argument("--sport-key", default="americanfootball_ncaaf")
    s.add_argument("--venue", default="kalshi")
    s.add_argument("--dry-run", action="store_true",
                   help="decide and print, write nothing")
    s.set_defaults(func=cmd_scan)

    g = sub.add_parser("grade", help="recompute Gate 2 from ledger rows")
    g.set_defaults(func=cmd_grade)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
