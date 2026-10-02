"""Canonical cohort identity for the 2026 product Week 5 experiment.

The product calls the Sep 25-26 slate "Week 5". Upstream schedule/data providers
label the same games regular-season Week 4. Provider week numbers are therefore
mapping metadata, not cohort identity.

This module is control-plane only. It cannot change S04_ES2 thresholds, staking,
delivery authority, or model outputs.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import slate as slate_module

CONTRACT = "CFB_EDGE_COHORT_IDENTITY_V1"


class CohortError(ValueError):
    pass


def _time(value: Any) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise CohortError(f"invalid cohort timestamp: {value!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CohortError("cohort contract must be a JSON object")
    if payload.get("contract") != CONTRACT:
        raise CohortError(
            f"unexpected cohort contract {payload.get('contract')!r}; expected {CONTRACT}"
        )
    for key in ("cohortId", "season", "productWeek", "providerWeeks"):
        if payload.get(key) in (None, ""):
            raise CohortError(f"cohort contract missing {key}")
    for key in ("gameWindow", "captureWindow", "prospectiveWindow"):
        window = payload.get(key) or {}
        start, end = _time(window.get("startsAt")), _time(window.get("endsAt"))
        if start >= end:
            raise CohortError(f"{key} must have startsAt < endsAt")
    return payload


def provider_week(contract: Mapping[str, Any], provider: str) -> int:
    weeks = contract.get("providerWeeks") or {}
    if provider not in weeks:
        raise CohortError(f"provider week mapping missing for {provider}")
    try:
        return int(weeks[provider])
    except (TypeError, ValueError) as exc:
        raise CohortError(f"invalid provider week for {provider}") from exc


def window(contract: Mapping[str, Any], mode: str) -> tuple[datetime, datetime]:
    key = {
        "game": "gameWindow",
        "capture": "captureWindow",
        "prospective": "prospectiveWindow",
    }.get(mode)
    if key is None:
        raise CohortError(f"unknown cohort mode {mode!r}")
    raw = contract.get(key) or {}
    return _time(raw.get("startsAt")), _time(raw.get("endsAt"))


def status(
    contract: Mapping[str, Any],
    *,
    now: datetime | None = None,
    mode: str = "capture",
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise CohortError("now must be timezone-aware")
    start, end = window(contract, mode)
    if current < start:
        state = "PRE_WINDOW"
    elif current >= end:
        state = "CLOSED"
    else:
        state = "ACTIVE"
    return {
        "cohortId": contract["cohortId"],
        "season": int(contract["season"]),
        "productWeek": int(contract["productWeek"]),
        "cfbfastRWeek": provider_week(contract, "cfbfastR"),
        "cfbdWeek": provider_week(contract, "collegefootballdata"),
        "mode": mode,
        "startsAt": start.isoformat(),
        "endsAt": end.isoformat(),
        "now": current.isoformat(),
        "state": state,
        "run": state == "ACTIVE",
    }


def validate_slate_rows(
    rows: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    start, end = window(contract, "game")
    errors: list[str] = []
    kickoffs: list[datetime] = []
    games: list[str] = []
    for i, row in enumerate(rows, start=1):
        game = str(row.get("game") or "").strip()
        if not game:
            errors.append(f"ROW_{i}_GAME_MISSING")
        else:
            games.append(game)
        raw = row.get("kickoff")
        try:
            kickoff = _time(raw)
        except CohortError:
            errors.append(f"ROW_{i}_KICKOFF_INVALID")
            continue
        kickoffs.append(kickoff)
        if kickoff < start or kickoff >= end:
            errors.append(
                f"ROW_{i}_OUTSIDE_GAME_WINDOW:{kickoff.isoformat()}"
            )
    if not rows:
        errors.append("SLATE_EMPTY")
    if games and len(set(games)) != len(games):
        errors.append("DUPLICATE_GAME")
    return {
        "contract": CONTRACT,
        "cohortId": contract["cohortId"],
        "productWeek": int(contract["productWeek"]),
        "rowCount": len(rows),
        "valid": not errors,
        "errors": errors,
        "firstKickoff": min(kickoffs).isoformat() if kickoffs else None,
        "lastKickoff": max(kickoffs).isoformat() if kickoffs else None,
        "gameWindow": {
            "startsAt": start.isoformat(),
            "endsAt": end.isoformat(),
        },
    }


def read_slate(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def validate_slate_file(
    path: str | Path,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    return validate_slate_rows(read_slate(path), contract)


def _requires_full_week(contract: Mapping[str, Any]) -> bool:
    return bool((contract.get("coverage") or {}).get("requireFullProviderWeek"))


def _excused(contract: Mapping[str, Any]) -> dict[str, str]:
    """Games a strict contract leaves out on purpose, each with its reason."""
    out: dict[str, str] = {}
    for item in (contract.get("coverage") or {}).get("excludedGames") or []:
        game, reason = str(item.get("game") or "").strip(), str(item.get("reason") or "").strip()
        if not game or not reason:
            raise CohortError("every coverage.excludedGames entry needs a game and a reason")
        out[game] = reason
    return out


def partition_provider_week(
    rows: Sequence[Any],
    contract: Mapping[str, Any],
) -> dict[str, list[Any]]:
    """Split one provider week into the cohort and what it leaves out.

    A provider-week game outside the game window but inside the capture window
    belongs to this week: the window is too narrow for it (the 2026 Week 6
    contract starts Friday 23:00 UTC and so drops two Thursday games). A game
    outside the capture window is a provider mislabel from another week.
    """
    g_start, g_end = window(contract, "game")
    c_start, c_end = window(contract, "capture")
    parts: dict[str, list[Any]] = {"cohort": [], "sameWeekOutsideWindow": [], "otherWeek": []}
    for row in rows:
        kickoff = _time(getattr(row, "kickoff", None))
        if g_start <= kickoff < g_end:
            parts["cohort"].append(row)
        elif c_start <= kickoff < c_end:
            parts["sameWeekOutsideWindow"].append(row)
        else:
            parts["otherWeek"].append(row)
    return parts


def coverage_report(rows: Sequence[Any], contract: Mapping[str, Any]) -> dict[str, Any]:
    parts = partition_provider_week(rows, contract)
    excused = _excused(contract)
    left_out = [
        {"game": getattr(r, "game", ""), "kickoff": str(getattr(r, "kickoff", "")),
         "excusedBecause": excused.get(getattr(r, "game", ""))}
        for r in parts["sameWeekOutsideWindow"]
    ]
    unexcused = [x for x in left_out if not x["excusedBecause"]]
    return {
        "requireFullProviderWeek": _requires_full_week(contract),
        "cohortRows": len(parts["cohort"]),
        "sameWeekOutsideWindow": left_out,
        "unexcusedSameWeekOutsideWindow": len(unexcused),
        "otherWeekRowsDropped": len(parts["otherWeek"]),
        "complete": not unexcused,
    }


def build_slate(
    contract: Mapping[str, Any],
    *,
    builder: Callable[[int, int], Sequence[Any]] | None = None,
) -> list[Any]:
    season = int(contract["season"])
    week = provider_week(contract, "cfbfastR")
    rows = list((builder or slate_module.build)(season, week))
    # Contracts written before 2026-10-02 keep their frozen windows. A contract
    # that sets coverage.requireFullProviderWeek may not drop a game of its own
    # week without naming it and the reason (D41).
    coverage = coverage_report(rows, contract)
    if coverage["requireFullProviderWeek"] and not coverage["complete"]:
        missing = [x["game"] for x in coverage["sameWeekOutsideWindow"] if not x["excusedBecause"]]
        raise CohortError(
            f"{contract['cohortId']} game window drops {len(missing)} game(s) of its own provider "
            f"week: {', '.join(missing)}; widen gameWindow or list them in coverage.excludedGames"
        )
    filtered = partition_provider_week(rows, contract)["cohort"]
    if not filtered:
        raise CohortError(
            f"{contract['cohortId']} produced no games from cfbfastR provider week {week}"
        )
    audit = validate_slate_rows(
        [
            {"game": getattr(r, "game", ""), "kickoff": getattr(r, "kickoff", "")}
            for r in filtered
        ],
        contract,
    )
    if not audit["valid"]:
        raise CohortError("canonical slate failed cohort validation: " + "; ".join(audit["errors"]))
    return filtered


def _print_env(report: Mapping[str, Any]) -> None:
    for key, value in (
        ("cohort_id", report["cohortId"]),
        ("season", report["season"]),
        ("product_week", report["productWeek"]),
        ("cfbfastr_week", report["cfbfastRWeek"]),
        ("cfbd_week", report["cfbdWeek"]),
        ("mode", report["mode"]),
        ("state", report["state"]),
        ("starts_at", report["startsAt"]),
        ("ends_at", report["endsAt"]),
        ("run", 1 if report["run"] else 0),
    ):
        print(f"{key}={value}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    ps = sub.add_parser("status")
    ps.add_argument("--config", required=True)
    ps.add_argument("--mode", choices=("capture", "game", "prospective"), default="capture")
    ps.add_argument("--now")

    pb = sub.add_parser("build-slate")
    pb.add_argument("--config", required=True)
    pb.add_argument("--out", required=True)

    pv = sub.add_parser("validate-slate")
    pv.add_argument("--config", required=True)
    pv.add_argument("--slate", required=True)

    pp = sub.add_parser("provider-week")
    pp.add_argument("--config", required=True)
    pp.add_argument("--provider", choices=("cfbfastR", "collegefootballdata"), required=True)

    args = p.parse_args(argv)
    contract = load(args.config)

    if args.command == "status":
        now = _time(args.now) if args.now else None
        report = status(contract, now=now, mode=args.mode)
        _print_env(report)
        return 0
    if args.command == "provider-week":
        print(provider_week(contract, args.provider))
        return 0
    if args.command == "build-slate":
        season, week = int(contract["season"]), provider_week(contract, "cfbfastR")
        provider_rows = list(slate_module.build(season, week))
        rows = build_slate(contract, builder=lambda s, w: provider_rows)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        slate_module.write_csv(rows, args.out)
        audit = validate_slate_file(args.out, contract)
        # Whatever the window leaves out of its own week is printed, never dropped silently.
        audit["coverage"] = coverage_report(provider_rows, contract)
        print(json.dumps(audit, sort_keys=True))
        return 0
    if args.command == "validate-slate":
        audit = validate_slate_file(args.slate, contract)
        print(json.dumps(audit, sort_keys=True))
        if not audit["valid"]:
            raise SystemExit(2)
        return 0
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
