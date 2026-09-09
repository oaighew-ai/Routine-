"""One command that answers whether a shadow log has found anything.

    python3 -m cfb_edge.measure --runs "data/raw/*.jsonl.gz" --entries entries.csv
    python3 -m cfb_edge.measure --runs "data/raw/*.gz" --entries entries.csv \
                                --price-field yes_mid --alpha 0.05

It rebuilds closing line value from the raw capture, then reports the interval
two ways: resampling contracts, and resampling whole games. The gap between
them is the point. If the shared game effect in your data is small the two
agree and nothing changes; if it is large the naive interval is a fraction of
its honest width, and anything promoted on it was promoted on a bar far lower
than intended.

The last line is a planning figure: how many games it would take, at the
dispersion actually present, before the observed mean could clear zero. It is
the number that decides whether a season is long enough to find out.

Nothing here places or recommends a wager. It grades a log.
"""

from __future__ import annotations

import argparse
import csv
import glob
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from .bootstrap import cluster_bootstrap, naive_bootstrap, required_clusters
from .clv_extract import DEFAULT_FIELDS, DEFAULT_HORIZONS, build_series, measure, summarise


def load_entries(path: str | Path) -> list[dict]:
    """Read the shadow log.

    Needs `ticker` and `entry_price`; `game` is used for clustering and falls
    back to the ticker, which makes every contract its own cluster and quietly
    removes the correction. So a missing game column is reported, not ignored.
    """
    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ticker = (row.get("ticker") or "").strip()
            price = (row.get("entry_price") or row.get("entry") or "").strip()
            if not ticker or not price:
                continue
            rows.append({
                "ticker": ticker,
                "entry_price": float(price),
                "game": (row.get("game") or row.get("event_ticker") or "").strip(),
            })
    return rows


def run(args: argparse.Namespace) -> int:
    paths = sorted(p for pattern in args.runs for p in glob.glob(pattern))
    if not paths:
        print(f"no capture files matched {args.runs}", file=sys.stderr)
        return 2

    entries = load_entries(args.entries)
    if not entries:
        print(f"no usable rows in {args.entries}", file=sys.stderr)
        return 2

    ungrouped = sum(1 for e in entries if not e["game"])
    fields = dict(DEFAULT_FIELDS)
    if args.price_field:
        fields["price"] = args.price_field
    if args.time_field:
        fields["timestamp"] = args.time_field

    from .clv_extract import read_runs

    series = build_series(read_runs(paths), fields=fields)
    measured = measure(entries, series, horizons=DEFAULT_HORIZONS)
    if not measured:
        print(
            "No entry matched a captured series. Check --price-field and "
            "--time-field against the recorder's schema; the extractor returns "
            "nothing rather than guessing.",
            file=sys.stderr,
        )
        return 2

    print(f"{len(paths)} capture files, {len(series)} markets with a series.\n")
    print(summarise(measured).summary())

    graded = [m for m in measured if m.clv is not None]
    pools = {
        "all graded entries": graded,
        "markets that moved": [m for m in graded if m.moved],
    }

    for name, pool in pools.items():
        if len(pool) < 2:
            print(f"\n{name}: too few to bootstrap ({len(pool)})")
            continue
        values = [m.clv for m in pool]
        clusters = [m.game or m.ticker for m in pool]
        naive = naive_bootstrap(values, replicates=args.replicates, alpha=args.alpha)
        clustered = cluster_bootstrap(
            values, clusters, replicates=args.replicates, alpha=args.alpha
        )
        by_game: dict[str, list[float]] = defaultdict(list)
        for value, key in zip(values, clusters):
            by_game[key].append(value)
        per_game = [statistics.fmean(v) for v in by_game.values()]
        sd = statistics.stdev(per_game) if len(per_game) > 1 else 0.0

        print(f"\n{name}  ({len(values)} contracts across {len(by_game)} games)")
        print(f"  resampling contracts : {naive.describe()}")
        print(f"  resampling games     : {clustered.describe()}")
        if naive.width:
            print(f"  clustered interval is {clustered.width / naive.width:.2f}x wider")
        if naive.excludes_zero and not clustered.excludes_zero:
            print("  the naive interval would promote this; clustering says hold")
        print(f"  per-game CLV sd {sd:.3f}c")
        if clustered.point > 0 and sd > 0:
            print(
                f"  games needed for a lower bound above zero at this mean: "
                f"{required_clusters(clustered.point, sd, alpha=args.alpha)}"
            )
        verdict = "PROMOTE" if clustered.low > 0 else "hold"
        print(f"  verdict on the clustered interval: {verdict}")

    if ungrouped:
        print(
            f"\n{ungrouped} entries had no game label, so each became its own "
            f"cluster. That silently removes the correction for those rows; "
            f"add a game column to fix it."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cfb_edge.measure", description=__doc__)
    p.add_argument("--runs", nargs="+", required=True,
                   help="glob(s) for gzipped JSONL capture files")
    p.add_argument("--entries", required=True, help="CSV shadow log")
    p.add_argument("--price-field", dest="price_field",
                   help=f"price key in the raw records (default {DEFAULT_FIELDS['price']})")
    p.add_argument("--time-field", dest="time_field",
                   help=f"timestamp key (default {DEFAULT_FIELDS['timestamp']})")
    p.add_argument("--replicates", type=int, default=10_000)
    p.add_argument("--alpha", type=float, default=0.05)
    return run(p.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
