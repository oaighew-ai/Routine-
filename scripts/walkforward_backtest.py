#!/usr/bin/env python3
"""Walk-forward evaluation of the CFB Edge prediction and decision layers.

Research only. ``promotionEffect: NONE``. Nothing this script prints can change
a frozen rule, a registry entry or the delivery authority (DECISIONS.md D18,
D26, D30). It exists so the weekly card's confidence numbers rest on a
measurement rather than on a constant somebody typed.

    python3 scripts/walkforward_backtest.py --cache /tmp/cfb-wf \
        --out docs/research/walkforward_2026.json

What it measures, all from public cfbfastR data (schedules and the multi-book
line file), with a strict as-of clock for everything the model computes:

A. **The market baseline (S00).** Closing-line accuracy, cover rates, and the
   calibration of Pinnacle's no-vig spread probability where the source still
   carries prices (2006-2019). This is the prior every decision starts from.
B. **The production projection** (``cfb_edge.slate.build_from_rows``, the exact
   code the live slate uses), walk-forward: week ``w`` is projected from games
   before ``w`` plus the prior season regressed halfway. Encompassing test
   against the closing line.
C. **H1, line movement.** Directional closing-line value of the projection's
   disagreement with the opening number, by disagreement band, week band,
   season and era. Every band that is tested is reported; none is selected.
D. **The decision layer.** The registered rule (week >= 3, |gap| >= 4) and the
   S04 4-6 band, graded at an ASSUMED -110 because the source carries no
   executable historical prices after 2019. ROI here is a model inference, not
   an observed fill.
E. **Calibration** of the numbers the card prints: P(beat the close) and mean
   CLV by band, fitted walk-forward (seasons before ``s`` predict ``s``).

Provenance limits, stated where they bind:

* Opening lines in the source carry **no timestamp** (DECISIONS.md D12). They
  are reference evidence of where a market was, never an executable entry.
* Opening lines are **not internally consistent**. On the 2024 file one book
  records a team opening +3 and two others record the same team opening -3 on
  the same game, a sign convention flip. A game enters the H1 sample only when
  at least two books' openers agree within ``OPEN_DISPERSION_MAX`` points;
  single-book openers are reported separately and never pooled.
* ``numberfire`` and ``teamrankings`` in the line file are projection sites,
  not sportsbooks, and are excluded from every market number.

Standard library plus ``cfb_edge`` only, like the package. Network is used only
to fetch the public files, once, into ``--cache``.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import statistics as st
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from cfb_edge.market import american_to_probability, devig_multiplicative  # noqa: E402
from cfb_edge.slate import build_from_rows  # noqa: E402

RAW = "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main"
LINES_URL = f"{RAW}/betting/csv/cfb_line_odds.csv.gz"
SCHEDULE_URL = RAW + "/schedules/csv/cfb_schedules_{season}.csv"

# Projection sites and aggregates, not books. Never a market number.
NOT_A_BOOK = frozenset({"numberfire", "teamrankings"})
AGGREGATE = frozenset({"consensus"})

# Two books' openers further apart than this are treated as inconsistent and
# the game leaves the H1 sample. PRIOR (Law 6): chosen so a sign flip on a
# three-point line (a six-point disagreement) cannot pass, while ordinary
# half-point and one-point book differences do.
OPEN_DISPERSION_MAX = 2.0

GAP_BANDS = [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0), (6.0, 8.0), (8.0, 10.0),
             (10.0, 14.0), (14.0, 99.0)]
WEEK_BANDS = [(1, 2), (3, 4), (5, 7), (8, 11), (12, 20)]
ERAS = [(2012, 2015), (2016, 2019), (2021, 2023), (2024, 2025)]

# The registered rule (MODEL.md, strategy.py) and the S04 family band.
REGISTERED_MIN_WEEK = 3
REGISTERED_MIN_GAP = 4.0
S04_BAND = (4.0, 6.0)

ASSUMED_PRICE = -110   # model inference only: no historical executable prices


# --------------------------------------------------------------------------
# fetching

def _get(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["curl", "-sSf", "--retry", "5", "--retry-all-errors",
                        "--max-time", "900", "-o", str(dest), url],
                       capture_output=True)
    if r.returncode != 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"could not fetch {url}: {r.stderr.decode()[:200]}")
    return dest


def schedule(season: int, cache: Path) -> list[dict]:
    p = _get(SCHEDULE_URL.format(season=season), cache / f"sched_{season}.csv")
    return list(csv.DictReader(io.StringIO(p.read_text(encoding="utf-8"))))


# --------------------------------------------------------------------------
# lines

def _f(v: str | None) -> float | None:
    v = (v or "").strip()
    if not v or v.upper() == "NA":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _resolve_abbrs(rows: Iterable[dict]) -> dict[str, str]:
    """Book abbreviation -> team id: the id present in every one of its rows."""
    seen: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        seen[r["abbr"]][r["home_team_id"]] += 1
        seen[r["abbr"]][r["away_team_id"]] += 1
    out = {}
    for a, c in seen.items():
        (top, n1), = c.most_common(1)
        n2 = c.most_common(2)[1][1] if len(c) > 1 else 0
        if len(c) == 1 or n1 > 2 * n2:
            out[a] = top
    return out


@dataclass
class GameLines:
    close_by_book: dict[str, float] = field(default_factory=dict)
    open_by_book: dict[str, float] = field(default_factory=dict)
    pin_line: float | None = None
    pin_home_odds: float | None = None
    pin_away_odds: float | None = None
    consensus_close: float | None = None
    consensus_open: float | None = None

    @property
    def close(self) -> float | None:
        if self.close_by_book:
            return st.median(self.close_by_book.values())
        return self.consensus_close

    def open_consistent(self) -> tuple[float | None, int, float | None]:
        """(median open, books, dispersion) when >= 2 books agree, else Nones."""
        vals = list(self.open_by_book.values())
        if len(vals) < 2:
            return None, len(vals), None
        disp = max(vals) - min(vals)
        if disp > OPEN_DISPERSION_MAX:
            return None, len(vals), disp
        return st.median(vals), len(vals), disp

    def open_single(self) -> float | None:
        vals = list(self.open_by_book.values())
        return vals[0] if len(vals) == 1 else None


def load_lines(cache: Path) -> dict[int, GameLines]:
    path = _get(LINES_URL, cache / "lines.csv.gz")
    spreads = [r for r in csv.DictReader(gzip.open(path, "rt"))
               if r["market_type"] == "spread"]
    home_of = _resolve_abbrs(spreads)
    # Home-side rows only; the away row is the same number negated.
    by_game: dict[int, GameLines] = defaultdict(GameLines)
    away_odds: dict[tuple[int, str], float] = {}
    for r in spreads:
        try:
            gid = int(float(r["game_id"]))
        except (TypeError, ValueError):
            continue
        book = (r.get("book") or "").strip()
        if book in NOT_A_BOOK:
            continue
        is_home = home_of.get(r["abbr"]) == r["home_team_id"]
        is_away = home_of.get(r["abbr"]) == r["away_team_id"]
        if is_away and book == "PINNACLE":
            o = _f(r.get("odds"))
            if o is not None:
                away_odds[(gid, book)] = o
            continue
        if not is_home:
            continue
        g = by_game[gid]
        close, opn = _f(r.get("lines")), _f(r.get("opening_lines"))
        if book in AGGREGATE:
            g.consensus_close = close if close is not None else g.consensus_close
            g.consensus_open = opn if opn is not None else g.consensus_open
            continue
        if close is not None:
            g.close_by_book[book] = close
        if opn is not None:
            g.open_by_book[book] = opn
        if book == "PINNACLE" and close is not None:
            g.pin_line = close
            g.pin_home_odds = _f(r.get("odds"))
    for (gid, _book), o in away_odds.items():
        if gid in by_game:
            by_game[gid].pin_away_odds = o
    return dict(by_game)


# --------------------------------------------------------------------------
# assembling the walk-forward table

@dataclass
class Row:
    season: int
    week: int
    game_id: int
    home: str
    away: str
    neutral: bool
    margin: int
    proj: float
    close: float | None
    open_: float | None
    open_books: int
    open_disp: float | None
    open_single: float | None
    pin_line: float | None
    pin_p_home: float | None


def assemble(seasons: Sequence[int], cache: Path, lines: dict[int, GameLines],
             log=print) -> list[Row]:
    rows: list[Row] = []
    sched_cache: dict[int, list[dict]] = {}

    def sched(s: int) -> list[dict]:
        if s not in sched_cache:
            sched_cache[s] = schedule(s, cache)
        return sched_cache[s]

    for s in seasons:
        cur, prev = sched(s), sched(s - 1)
        index: dict[tuple[int, str, str], dict] = {}
        weeks = set()
        for r in cur:
            if (r.get("season_type") or "").lower() != "regular":
                continue
            if (r.get("home_division") or "").lower() != "fbs" or \
               (r.get("away_division") or "").lower() != "fbs":
                continue
            try:
                w = int(float(r["week"]))
            except (TypeError, ValueError):
                continue
            weeks.add(w)
            index[(w, r["away_team"], r["home_team"])] = r
        kept = 0
        for w in sorted(weeks):
            for sr in build_from_rows(s, w, prior_rows=prev, rows=cur):
                away, home = (x.strip() for x in sr.game.split("@", 1))
                r = index.get((w, away, home))
                if r is None:
                    continue
                hp, ap = _f(r.get("home_points")), _f(r.get("away_points"))
                if hp is None or ap is None:
                    continue
                gid = int(float(r["game_id"]))
                gl = lines.get(gid)
                if gl is None or gl.close is None:
                    continue
                o, nb, disp = gl.open_consistent()
                pin_p = None
                if (gl.pin_line is not None and gl.pin_home_odds is not None
                        and gl.pin_away_odds is not None):
                    try:
                        pin_p = devig_multiplicative(
                            [gl.pin_home_odds, gl.pin_away_odds])[0]
                    except (ValueError, ZeroDivisionError):
                        pin_p = None
                rows.append(Row(
                    season=s, week=w, game_id=gid, home=home, away=away,
                    neutral=sr.neutral, margin=int(hp - ap), proj=sr.projected_margin,
                    close=gl.close, open_=o, open_books=nb, open_disp=disp,
                    open_single=gl.open_single(), pin_line=gl.pin_line,
                    pin_p_home=pin_p,
                ))
                kept += 1
        log(f"  {s}: {kept} FBS-vs-FBS games with a closing line", flush=True)
    return rows


# --------------------------------------------------------------------------
# statistics

def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def clustered(values: Sequence[float], clusters: Sequence[object]) -> dict:
    """Mean with a cluster-robust standard error (clusters = season-week)."""
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "se": None, "t": None, "clusters": 0}
    m = mean(values)
    groups: dict[object, float] = defaultdict(float)
    for v, c in zip(values, clusters):
        groups[c] += v - m
    g = len(groups)
    if g < 2:
        return {"n": n, "mean": m, "se": None, "t": None, "clusters": g}
    var = sum(s * s for s in groups.values()) / (n * n) * g / (g - 1)
    se = math.sqrt(var)
    return {"n": n, "mean": m, "se": se, "t": (m / se) if se > 0 else None,
            "clusters": g, "lo95": m - 1.96 * se, "hi95": m + 1.96 * se}


def ols(ys: Sequence[float], xs: Sequence[Sequence[float]]) -> dict:
    """OLS with intercept via normal equations; classical SEs. Small k only."""
    n, k = len(ys), len(xs[0]) + 1
    X = [[1.0, *x] for x in xs]
    xtx = [[sum(X[r][i] * X[r][j] for r in range(n)) for j in range(k)] for i in range(k)]
    xty = [sum(X[r][i] * ys[r] for r in range(n)) for i in range(k)]
    inv = _invert(xtx)
    beta = [sum(inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    resid = [ys[r] - sum(beta[i] * X[r][i] for i in range(k)) for r in range(n)]
    s2 = sum(e * e for e in resid) / (n - k)
    se = [math.sqrt(s2 * inv[i][i]) for i in range(k)]
    return {"n": n, "beta": beta, "se": se,
            "t": [b / s if s > 0 else None for b, s in zip(beta, se)],
            "residual_sd": math.sqrt(s2)}


def _invert(m: list[list[float]]) -> list[list[float]]:
    n = len(m)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(m)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(a[r][c]))
        a[c], a[p] = a[p], a[c]
        piv = a[c][c]
        a[c] = [v / piv for v in a[c]]
        for r in range(n):
            if r != c:
                f = a[r][c]
                a[r] = [vr - f * vc for vr, vc in zip(a[r], a[c])]
    return [row[n:] for row in a]


def cover(margin: int, home_line: float, side: str) -> float | None:
    """1 win, 0 loss, None push, for a spread bet on `side` at `home_line`."""
    x = margin + home_line
    if abs(x) < 1e-9:
        return None
    home_covers = x > 0
    return 1.0 if (home_covers if side == "home" else not home_covers) else 0.0


def roi_at(price: float, wins: int, losses: int) -> float | None:
    n = wins + losses
    if n == 0:
        return None
    win_mult = (100.0 / -price) if price < 0 else (price / 100.0)
    return (wins * win_mult - losses) / n


def max_drawdown(units: Sequence[float]) -> float:
    peak, run, worst = 0.0, 0.0, 0.0
    for u in units:
        run += u
        peak = max(peak, run)
        worst = min(worst, run - peak)
    return worst


def band_of(value: float, bands: Sequence[tuple[float, float]]) -> str | None:
    for lo, hi in bands:
        if lo <= value < hi:
            return f"{lo:g}-{hi:g}" if hi < 99 else f"{lo:g}+"
    return None


# --------------------------------------------------------------------------
# analyses

def market_baseline(rows: Sequence[Row]) -> dict:
    close_err = [r.margin + r.close for r in rows]
    proj_err = [r.margin - r.proj for r in rows]
    out = {
        "games": len(rows),
        "closeLineBias": mean(close_err),
        "closeLineResidualSd": st.pstdev(close_err),
        "closeLineMae": mean([abs(e) for e in close_err]),
        "projectionMae": mean([abs(e) for e in proj_err]),
        "projectionBias": mean(proj_err),
    }
    with_open = [r for r in rows if r.open_ is not None]
    if with_open:
        out["openLineMae_sameGames"] = mean([abs(r.margin + r.open_) for r in with_open])
        out["closeLineMae_sameGames"] = mean([abs(r.margin + r.close) for r in with_open])
        out["openGames"] = len(with_open)
    covers = [c for c in (cover(r.margin, r.close, "home") for r in rows) if c is not None]
    out["homeCoverRateAtClose"] = mean(covers)
    fav = [cover(r.margin, r.close, "home" if r.close < 0 else "away")
           for r in rows if abs(r.close) > 0]
    fav = [c for c in fav if c is not None]
    out["favoriteCoverRateAtClose"] = mean(fav)

    # Calibration of Pinnacle's no-vig home-cover probability, where priced.
    pin = [r for r in rows if r.pin_p_home is not None and r.pin_line is not None]
    bins: dict[str, list[float]] = defaultdict(list)
    preds: dict[str, list[float]] = defaultdict(list)
    brier_m, brier_half, n_pin = 0.0, 0.0, 0
    for r in pin:
        c = cover(r.margin, r.pin_line, "home")
        if c is None:
            continue
        p = r.pin_p_home
        b = f"{math.floor(p * 50) / 50:.2f}"
        bins[b].append(c)
        preds[b].append(p)
        brier_m += (p - c) ** 2
        brier_half += (0.5 - c) ** 2
        n_pin += 1
    table = []
    ece = 0.0
    for b in sorted(bins):
        obs, pr = mean(bins[b]), mean(preds[b])
        table.append({"bin": b, "n": len(bins[b]), "meanPredicted": pr, "observed": obs})
        ece += len(bins[b]) / max(n_pin, 1) * abs(obs - pr)
    out["pinnacleNoVigCalibration"] = {
        "games": n_pin,
        "seasons": sorted({r.season for r in pin}),
        "brier": brier_m / n_pin if n_pin else None,
        "brierCoinFlip": brier_half / n_pin if n_pin else None,
        "ece": ece if n_pin else None,
        "bins": table,
    }
    return out


def encompassing(rows: Sequence[Row]) -> dict:
    ys = [float(r.margin) for r in rows]
    xs = [[-r.close, r.proj] for r in rows]
    fit = ols(ys, xs)
    return {"n": fit["n"],
            "closeLineCoef": fit["beta"][1], "closeLineT": fit["t"][1],
            "projectionCoef": fit["beta"][2], "projectionT": fit["t"][2],
            "impliedModelWeight": fit["beta"][2],
            "residualSd": fit["residual_sd"]}


def h1_rows(rows: Sequence[Row], *, single_book: bool = False) -> list[dict]:
    out = []
    for r in rows:
        opn = r.open_single if single_book else r.open_
        if opn is None:
            continue
        gap = r.proj - (-opn)          # model home margin minus market's, at open
        if abs(gap) < 1e-9:
            continue
        side = "home" if gap > 0 else "away"
        clv = (opn - r.close) if side == "home" else (r.close - opn)
        out.append({
            "season": r.season, "week": r.week, "gap": abs(gap), "side": side,
            "clv": clv, "beat": 1.0 if clv > 0 else (0.0 if clv < 0 else None),
            "coverOpen": cover(r.margin, opn, side),
            "coverClose": cover(r.margin, r.close, side),
            "fav": (opn < 0) == (side == "home"),
            "cluster": (r.season, r.week),
        })
    return out


def summarize(sig: Sequence[dict]) -> dict:
    if not sig:
        return {"n": 0}
    clv = clustered([s["clv"] for s in sig], [s["cluster"] for s in sig])
    beats = [s["beat"] for s in sig if s["beat"] is not None]
    wo = [s["coverOpen"] for s in sig if s["coverOpen"] is not None]
    wc = [s["coverClose"] for s in sig if s["coverClose"] is not None]
    wins, losses = int(sum(wo)), len(wo) - int(sum(wo))
    units_seq = []
    for s in sorted(sig, key=lambda s: (s["season"], s["week"])):
        c = s["coverOpen"]
        if c is None:
            continue
        units_seq.append((100.0 / 110.0) if c else -1.0)
    return {
        "n": len(sig),
        "clv": clv,
        "beatCloseRate": mean(beats) if beats else None,
        "coverRateAtOpen": mean(wo) if wo else None,
        "coverRateAtClose": mean(wc) if wc else None,
        "roiAtAssumedMinus110": roi_at(ASSUMED_PRICE, wins, losses),
        "maxDrawdownUnits": max_drawdown(units_seq),
        "decisions": wins + losses,
    }


def h1_report(rows: Sequence[Row]) -> dict:
    sig = h1_rows(rows)
    single = h1_rows(rows, single_book=True)
    out: dict = {"variantsTested": 0, "consistentOpenSignals": len(sig),
                 "singleBookSignalsExcluded": len(single)}

    def bump():
        out["variantsTested"] += 1

    out["all"] = summarize(sig); bump()
    out["byGap"] = {}
    for b in GAP_BANDS:
        name = band_of(b[0], [b])
        out["byGap"][name] = summarize([s for s in sig if b[0] <= s["gap"] < b[1]]); bump()
    out["byWeek"] = {}
    for lo, hi in WEEK_BANDS:
        out["byWeek"][f"{lo}-{hi}"] = summarize(
            [s for s in sig if lo <= s["week"] <= hi and s["gap"] >= REGISTERED_MIN_GAP]); bump()
    out["byEra_registeredRule"] = {}
    for lo, hi in ERAS:
        out["byEra_registeredRule"][f"{lo}-{hi}"] = summarize([
            s for s in sig if lo <= s["season"] <= hi
            and s["week"] >= REGISTERED_MIN_WEEK and s["gap"] >= REGISTERED_MIN_GAP]); bump()
    out["bySeason_registeredRule"] = {}
    for season in sorted({s["season"] for s in sig}):
        out["bySeason_registeredRule"][str(season)] = summarize([
            s for s in sig if s["season"] == season
            and s["week"] >= REGISTERED_MIN_WEEK and s["gap"] >= REGISTERED_MIN_GAP]); bump()
    reg = [s for s in sig if s["week"] >= REGISTERED_MIN_WEEK and s["gap"] >= REGISTERED_MIN_GAP]
    out["registeredRule"] = summarize(reg); bump()
    out["registeredRule_favorite"] = summarize([s for s in reg if s["fav"]]); bump()
    out["registeredRule_underdog"] = summarize([s for s in reg if not s["fav"]]); bump()
    out["registeredRule_homeSide"] = summarize([s for s in reg if s["side"] == "home"]); bump()
    out["registeredRule_awaySide"] = summarize([s for s in reg if s["side"] == "away"]); bump()
    out["s04Band_4to6_week3plus"] = summarize([
        s for s in sig if s["week"] >= REGISTERED_MIN_WEEK and S04_BAND[0] <= s["gap"] < S04_BAND[1]]); bump()
    out["singleBookOpens_registeredRule_notPooled"] = summarize([
        s for s in single if s["week"] >= REGISTERED_MIN_WEEK and s["gap"] >= REGISTERED_MIN_GAP]); bump()
    return out


def walkforward_calibration(rows: Sequence[Row], target_seasons: Sequence[int]) -> dict:
    """P(beat close) and mean CLV by gap band, fitted on seasons before s only."""
    sig = [s for s in h1_rows(rows) if s["week"] >= REGISTERED_MIN_WEEK]
    by_season: dict[int, list[dict]] = defaultdict(list)
    for s in sig:
        by_season[s["season"]].append(s)
    bands = [(4.0, 6.0), (6.0, 8.0), (8.0, 10.0), (10.0, 99.0)]
    folds = []
    pooled_pred, pooled_obs = [], []
    for s in target_seasons:
        train = [x for x in sig if x["season"] < s]
        test = by_season.get(s, [])
        if not train or not test:
            continue
        fold = {"season": s, "trainN": len(train), "testN": len(test), "bands": []}
        for lo, hi in bands:
            tr = [x for x in train if lo <= x["gap"] < hi and x["beat"] is not None]
            te = [x for x in test if lo <= x["gap"] < hi and x["beat"] is not None]
            if not tr or not te:
                continue
            # Shrink toward a coin flip with 50 pseudo-games each side.
            p = (sum(x["beat"] for x in tr) + 50) / (len(tr) + 100)
            obs = mean([x["beat"] for x in te])
            fold["bands"].append({"band": band_of(lo, [(lo, hi)]), "predicted": p,
                                  "observed": obs, "n": len(te),
                                  "trainMeanClv": mean([x["clv"] for x in tr]),
                                  "testMeanClv": mean([x["clv"] for x in te])})
            pooled_pred.extend([p] * len(te))
            pooled_obs.extend([x["beat"] for x in te])
        folds.append(fold)
    brier = mean([(p - o) ** 2 for p, o in zip(pooled_pred, pooled_obs)]) if pooled_obs else None
    brier_half = mean([(0.5 - o) ** 2 for o in pooled_obs]) if pooled_obs else None
    return {"target": "P(line moves toward the model's side), non-zero moves only",
            "shrinkage": "Beta(50,50) prior toward 0.5",
            "folds": folds, "pooledBrier": brier, "pooledBrierCoinFlip": brier_half,
            "pooledN": len(pooled_obs)}


def economics() -> dict:
    """Breakeven CLV by where the edge is expressed. Derived, not fitted.

    For a strike ``s`` the value of one point of line is the change in the
    contract's win probability when the strike moves one point:
    ``P(margin > s - 0.5) - P(margin > s + 0.5)``. The Kalshi fee is charged at
    the strike's own price. Their ratio is the line movement needed to pay the
    fee there. D31's 0.675 is the at-the-money row under a smooth normal; the
    key-number rows use the fitted discrete margin distribution.
    """
    from cfb_edge.distribution import margin_pmf, sigma_for_total, REFERENCE_TOTAL
    from cfb_edge.kalshi_fees import fee_cents_per_contract
    pmf = margin_pmf(0.0, sigma_for_total(REFERENCE_TOTAL))

    def survival(x: float) -> float:
        return sum(v for k, v in pmf.items() if k > x)

    out = []
    for strike, label in ((0.5, "pick'em strike, off key"), (2.5, "just under 3"),
                          (3.0, "key number 3"), (7.0, "key number 7"),
                          (9.0, "off-key 9"), (14.0, "key number 14")):
        price = survival(strike)
        dens = survival(strike - 0.5) - survival(strike + 0.5)
        fee = fee_cents_per_contract(price) / 100.0   # price in dollars; fee returned in cents
        out.append({"strike": strike, "label": label, "price": price,
                    "probabilityPerPoint": dens, "kalshiFeeProbability": fee,
                    "breakevenClvPoints": (fee / dens) if dens > 0 else None})
    return {"note": "Line movement (points) needed for a Kalshi spread contract "
                    "to pay its fee, by strike, for a pick'em game (sigma at the "
                    "reference total). Key-number strikes need far less movement "
                    "than off-key strikes. Whether line CLV measured on "
                    "sportsbook openers transfers to an exchange strike is "
                    "untested (MODEL.md). Not a fitted result.",
            "rows": out}


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cache", type=Path, default=Path("/tmp/cfb-wf"))
    p.add_argument("--from-season", type=int, default=2012)
    p.add_argument("--to-season", type=int, default=2025)
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args(argv)
    a.cache.mkdir(parents=True, exist_ok=True)

    print(f"cache {a.cache}; seasons {a.from_season}-{a.to_season}", flush=True)
    lines = load_lines(a.cache)
    print(f"lines for {len(lines)} games", flush=True)
    seasons = [s for s in range(a.from_season, a.to_season + 1) if s != 2020]
    rows = assemble(seasons, a.cache, lines)
    print(f"{len(rows)} walk-forward rows", flush=True)

    report = {
        "contract": "CFB_EDGE_WALKFORWARD_V1",
        "promotionEffect": "NONE",
        "decisionEffect": "NONE",
        "seasons": seasons,
        "excluded": {"2020": "no opening lines in the source; COVID season"},
        "provenance": {
            "projection": "cfb_edge.slate.build_from_rows, as-of week start",
            "closeLine": "median of real books' home-side closing lines",
            "openLine": f"median of >=2 books' openers within {OPEN_DISPERSION_MAX} pts; "
                        "reference-only, untimestamped (D12)",
            "notBooks": sorted(NOT_A_BOOK),
            "price": f"ROI graded at an ASSUMED {ASSUMED_PRICE}; not an observed fill",
        },
        "marketBaseline": market_baseline(rows),
        "encompassing": encompassing(rows),
        "encompassingByEra": {
            f"{lo}-{hi}": encompassing([r for r in rows if lo <= r.season <= hi])
            for lo, hi in ERAS
        },
        "h1": h1_report(rows),
        "calibration": walkforward_calibration(rows, seasons[3:]),
        "economics": economics(),
    }
    text = json.dumps(report, indent=1, sort_keys=True, default=float)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {a.out}")
    _print_summary(report)
    return 0


def _fmt(x, nd=3):
    return "—" if x is None else f"{x:+.{nd}f}" if isinstance(x, float) else str(x)


def _print_summary(r: dict) -> None:
    mb, enc, h1 = r["marketBaseline"], r["encompassing"], r["h1"]
    print("\nA. market baseline")
    print(f"  games {mb['games']}  close MAE {mb['closeLineMae']:.2f}  "
          f"projection MAE {mb['projectionMae']:.2f}  close bias {mb['closeLineBias']:+.2f}")
    pc = mb["pinnacleNoVigCalibration"]
    if pc["games"]:
        print(f"  Pinnacle no-vig: n={pc['games']} Brier {pc['brier']:.4f} "
              f"(coin flip {pc['brierCoinFlip']:.4f}) ECE {pc['ece']:.4f}")
    print("B. encompassing (margin on -close and projection)")
    print(f"  close {enc['closeLineCoef']:+.3f} (t {enc['closeLineT']:+.1f})  "
          f"projection {enc['projectionCoef']:+.3f} (t {enc['projectionT']:+.2f})  n {enc['n']}")
    for era, e in r["encompassingByEra"].items():
        print(f"    {era}: projection {e['projectionCoef']:+.3f} (t {e['projectionT']:+.2f}) n {e['n']}")
    print(f"C. H1 line movement ({h1['variantsTested']} variants reported, none selected)")
    for k in ("all", "registeredRule", "s04Band_4to6_week3plus",
              "singleBookOpens_registeredRule_notPooled"):
        s = h1[k]
        c = s.get("clv") or {}
        print(f"  {k:44s} n={s['n']:5d} CLV {_fmt(c.get('mean'))} t {_fmt(c.get('t'),2)} "
              f"beat {_fmt(s.get('beatCloseRate'))} ROI@-110 {_fmt(s.get('roiAtAssumedMinus110'))}")
    for era, s in h1["byEra_registeredRule"].items():
        c = s.get("clv") or {}
        print(f"    era {era}: n={s['n']:4d} CLV {_fmt(c.get('mean'))} t {_fmt(c.get('t'),2)} "
              f"ROI@-110 {_fmt(s.get('roiAtAssumedMinus110'))}")
    cal = r["calibration"]
    print(f"E. walk-forward P(beat close) calibration: Brier {_fmt(cal['pooledBrier'],4)} "
          f"vs coin flip {_fmt(cal['pooledBrierCoinFlip'],4)} over n={cal['pooledN']}")


if __name__ == "__main__":
    raise SystemExit(main())
