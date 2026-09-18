"""One-shot capture diagnostic; every invocation uses fresh scratch state."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from . import slate, watch
from .clv import CAPTURED, LATE


# What a parsed row may say about itself here. `LATE` counts: this function
# asks whether the chain works, not whether the line is worth betting. A poll
# run outside the release window produces honest rows that are correctly
# refused by the closing line value, and reporting that as a broken capture
# would send someone hunting a parser bug that is not there. `UNVERIFIED` does
# not count, because a row with no provenance is not evidence the parse ran.
PARSED = frozenset({CAPTURED, LATE})

REQUIRED_COLUMNS = ("game", "opening_line", "source")


def count_lines(path: Path) -> int:
    """Reject missing or malformed output instead of counting arbitrary text."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, strict=True)
        names = reader.fieldnames or []
        # Required columns must be present; extra ones are allowed, so adding a
        # column to the capture does not read here as a corrupt file.
        if [n for n in REQUIRED_COLUMNS if n not in names]:
            raise ValueError(
                f"invalid opening-lines CSV header: {names!r} is missing one of "
                f"{list(REQUIRED_COLUMNS)}")
        count = 0
        for row in reader:
            if (None in row or not row["game"] or not row["game"].strip()
                    or row["source"] not in PARSED
                    or not math.isfinite(float(row["opening_line"]))):
                raise ValueError("invalid opening-lines CSV row")
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("season", nargs="?", type=int, default=2026)
    parser.add_argument("week", nargs="?", default="current")
    parser.add_argument("source", nargs="?", choices=("kalshi", "oddsapi"),
                        default="kalshi")
    args = parser.parse_args(argv)
    stage = "scratch setup"
    try:
        with TemporaryDirectory(prefix="cfb-preflight-") as directory:
            root = Path(directory)
            schedule, log, output = (root / "slate.csv", root / "poll.jsonl.gz",
                                     root / "opens.csv")
            stage = "slate"
            print(f"1/3 Building the slate ({args.season}, week {args.week})...")
            if slate.main(["--season", str(args.season), "--week", args.week,
                           "--out", str(schedule)]) != 0:
                print("FAIL: slate generation failed; see the error above.")
                return 2
            if not watch._slate_games(str(schedule)):
                print("FAIL: slate contains no fixtures.")
                return 2
            stage = "capture"
            print(f"2/3 One poll against {args.source}...")
            if watch.main(["--source", args.source, "--slate", str(schedule),
                           "--log", str(log), "--out", str(output), "--once"]) != 0:
                print("FAIL: capture failed; see the original error above.")
                return 2
            stage = "output validation"
            print("3/3 Validating fresh opening lines...")
            count = count_lines(output)
            if not count:
                print("FAIL: poll completed with 0 usable lines. The board may "
                      "be empty, fixtures unmatched, or quotes rejected. "
                      "This does not by itself establish a parser defect.")
                return 1
            print(f"PASS: {count} opening lines parsed from {args.source}.")
            print("Start capture with the same season, week and source:")
            print(f"scripts/capture.sh {args.season} {args.week} {args.source}")
            print(f"scripts\\capture.bat {args.season} {args.week} {args.source}")
            return 0
    except Exception as exc:
        print(f"FAIL: {stage}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
