"""Canonical cohort identity.

The product calls the Sep 25-26 slate "Week 5". Upstream schedule/data providers
label the same games regular-season Week 4. Provider week numbers are therefore
mapping metadata, not cohort identity.

A cohort is a registered contract: which games, and the windows in which they
may be captured, frozen and played. Contracts are written before their window
opens and are never edited afterwards. `propose` derives one from the provider
schedule by the rule the Week 6 contract was written to, so a new week needs no
hand-typed dates, and `find_active` picks the contract whose window is open,
so advancing to the next week needs no configuration change at all (D41).

This module is control-plane only. It cannot change S04_ES2 thresholds, staking,
delivery authority, or model outputs.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
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
    # A contract that sets coverage.requireFullProviderWeek names every game of
    # its own week that the window leaves out (D41). If the schedule moves a
    # game outside the window after registration, the build says so on stderr
    # and carries on: one flexed kickoff must not cost the other fifty games
    # their capture. Contracts written before 2026-10-02 keep their frozen
    # windows and are reported the same way.
    coverage = coverage_report(rows, contract)
    if not coverage["complete"]:
        missing = [x["game"] for x in coverage["sameWeekOutsideWindow"] if not x["excusedBecause"]]
        print(
            f"COVERAGE: {contract['cohortId']} game window leaves out {len(missing)} game(s) of "
            f"its own provider week that the contract does not name: {', '.join(missing)}",
            file=sys.stderr,
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


# ---------------------------------------------------------------------------
# registering the next cohort without typing dates

# The weekend rule, read off the registered Week 6 contract
# (config/br2_active_cohort.json): the cohort is the games from Friday 23:00
# UTC to Sunday 12:00 UTC; capture runs from the previous Sunday 00:00 UTC; the
# prospective window closes when the game window opens. Friday 23:00 keeps
# Friday's feature capture, the last one before Saturday, inside the window.
WEEKEND_START_BEFORE_SATURDAY = timedelta(hours=1)      # Friday 23:00 UTC
WEEKEND_END_AFTER_SATURDAY = timedelta(hours=36)        # Sunday 12:00 UTC
CAPTURE_START_BEFORE_SATURDAY = timedelta(days=6)       # previous Sunday 00:00 UTC
EXCLUSION_REASON = ("kicks off before the weekend freeze (Friday 23:00 UTC); "
                    "one feature decision time is frozen per cohort")
# 2026: the provider folds the two opening weekends into its week 1, so from
# provider week 2 on the product week is one ahead.
PRODUCT_WEEK_OFFSET = {2026: 1}


def _z(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def football_saturday(kickoffs: Sequence[datetime]) -> datetime:
    """Saturday 00:00 UTC of the weekend a provider week ends on.

    Late Saturday games read as Sunday in UTC, so the anchor is taken from the
    last kickoff less twelve hours and walked back to a Saturday.
    """
    if not kickoffs:
        raise CohortError("no kickoff times to anchor a cohort on")
    day = (max(kickoffs) - timedelta(hours=12)).astimezone(timezone.utc)
    day = day.replace(hour=0, minute=0, second=0, microsecond=0)
    while day.weekday() != 5:
        day -= timedelta(days=1)
    return day


def weekend_windows(saturday: datetime) -> dict[str, dict[str, str]]:
    game_start = saturday - WEEKEND_START_BEFORE_SATURDAY
    game_end = saturday + WEEKEND_END_AFTER_SATURDAY
    capture_start = saturday - CAPTURE_START_BEFORE_SATURDAY
    return {
        "gameWindow": {"startsAt": _z(game_start), "endsAt": _z(game_end)},
        "captureWindow": {"startsAt": _z(capture_start), "endsAt": _z(game_end)},
        "prospectiveWindow": {"startsAt": _z(capture_start), "endsAt": _z(game_start)},
    }


def propose(
    season: int,
    provider_week_number: int,
    rows: Sequence[Any],
    *,
    product_week: int | None = None,
) -> dict[str, Any]:
    """The weekend contract for one provider week, from its schedule rows.

    Every game of the week that the weekend window leaves out is named, with
    the reason, so the contract passes its own full-week coverage rule.
    """
    kickoffs = [_time(getattr(r, "kickoff", None)) for r in rows]
    windows = weekend_windows(football_saturday(kickoffs))
    if product_week is None:
        if season not in PRODUCT_WEEK_OFFSET:
            raise CohortError(f"no product-week offset registered for {season}; pass product_week")
        product_week = provider_week_number + PRODUCT_WEEK_OFFSET[season]
    contract: dict[str, Any] = {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "cohortId": f"CFB_{season}_PRODUCT_WEEK{product_week}",
        "season": season,
        "productWeek": product_week,
        **windows,
        "providerWeeks": {"cfbfastR": provider_week_number,
                          "collegefootballdata": provider_week_number},
        "coverage": {"requireFullProviderWeek": True, "excludedGames": []},
        "policy": {
            "identityUsesProviderWeek": False,
            "frozenDecisionRuleChanged": False,
            "s04Es2DecisionEffect": "NONE",
            "stakingEffect": "NONE",
            "deliveryEffect": "NONE",
            "notes": "Weekend cohort generated by cfb_edge.cohort propose from the provider "
                     "schedule, by the rule the Week 6 contract was written to. Identity only.",
        },
    }
    parts = partition_provider_week(rows, contract)
    contract["coverage"]["excludedGames"] = [
        {"game": getattr(r, "game", ""), "kickoff": str(getattr(r, "kickoff", "")),
         "reason": EXCLUSION_REASON}
        for r in sorted(parts["sameWeekOutsideWindow"],
                        key=lambda r: (str(getattr(r, "kickoff", "")), getattr(r, "game", "")))
    ]
    if not parts["cohort"]:
        raise CohortError(f"{contract['cohortId']}: no game falls inside the weekend window")
    if parts["otherWeek"]:
        names = ", ".join(getattr(r, "game", "") for r in parts["otherWeek"])
        raise CohortError(f"{contract['cohortId']}: games outside the capture window: {names}")
    return contract


def contracts_in(directory: str | Path) -> list[tuple[Path, dict[str, Any]]]:
    base = Path(directory)
    if not base.is_dir():
        return []
    return [(path, load(path)) for path in sorted(base.glob("*.json"))]


def find_active(
    directory: str | Path,
    *,
    now: datetime | None = None,
    mode: str = "prospective",
    fallback: str | Path | None = None,
) -> Path | None:
    """The registered contract whose window is open now, or the fallback.

    Exactly one may be open. Two open contracts mean the registry itself is
    wrong, and guessing between them would put one week's evidence under
    another week's name.
    """
    current = now or datetime.now(timezone.utc)
    open_now = [path for path, contract in contracts_in(directory)
                if status(contract, now=current, mode=mode)["run"]]
    if len(open_now) > 1:
        raise CohortError("more than one cohort contract is open in "
                          f"{mode} mode: " + ", ".join(p.name for p in open_now))
    if open_now:
        return open_now[0]
    return Path(fallback) if fallback else None


def find_for_provider_week(
    paths: Sequence[str | Path],
    season: int,
    provider_week_number: int,
) -> Path | None:
    """The contract registered for a provider week, searching files and directories."""
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        candidates = [p for p, _ in contracts_in(path)] if path.is_dir() else ([path] if path.exists() else [])
        for candidate in candidates:
            contract = load(candidate)
            if (int(contract["season"]) == int(season)
                    and provider_week(contract, "cfbfastR") == int(provider_week_number)):
                found.append(candidate)
    if len({load(p)["cohortId"] for p in found}) > 1:
        raise CohortError(f"provider week {provider_week_number} is registered under more than "
                          "one cohort: " + ", ".join(str(p) for p in found))
    return found[0] if found else None


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

    pn = sub.add_parser("propose", help="write the weekend contract for a provider week")
    pn.add_argument("--season", type=int, required=True)
    pn.add_argument("--provider-week", type=int, required=True, dest="provider_week")
    pn.add_argument("--product-week", type=int, dest="product_week")
    pn.add_argument("--out", required=True)

    pa = sub.add_parser("active", help="print the contract whose window is open now")
    pa.add_argument("--dir", required=True, dest="directory")
    pa.add_argument("--fallback")
    pa.add_argument("--mode", choices=("capture", "game", "prospective"), default="prospective")
    pa.add_argument("--now")
    pa.add_argument("--github-env", dest="github_env",
                    help="print NAME=path instead of the bare path")

    pf = sub.add_parser("for-week", help="print the contract registered for a provider week")
    pf.add_argument("--season", type=int, required=True)
    pf.add_argument("--provider-week", type=int, required=True, dest="provider_week")
    pf.add_argument("paths", nargs="+")

    args = p.parse_args(argv)

    if args.command == "propose":
        rows = list(slate_module.build(args.season, args.provider_week))
        contract = propose(args.season, args.provider_week, rows, product_week=args.product_week)
        out = Path(args.out)
        if out.exists():
            raise SystemExit(f"{out} exists; a registered contract is never rewritten")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
        report = coverage_report(rows, contract)
        print(json.dumps({"cohortId": contract["cohortId"], "cohortRows": report["cohortRows"],
                          "excluded": len(contract["coverage"]["excludedGames"]),
                          "gameWindow": contract["gameWindow"]}, sort_keys=True))
        return 0
    if args.command == "active":
        now = _time(args.now) if args.now else None
        path = find_active(args.directory, now=now, mode=args.mode, fallback=args.fallback)
        if path is None:
            print("no cohort contract is open and no fallback was given", file=sys.stderr)
            return 3
        print(f"{args.github_env}={path.as_posix()}" if args.github_env else path.as_posix())
        return 0
    if args.command == "for-week":
        path = find_for_provider_week(args.paths, args.season, args.provider_week)
        if path is None:
            return 3
        print(path.as_posix())
        return 0

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
