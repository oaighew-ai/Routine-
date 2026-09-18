#!/usr/bin/env python3
"""Regenerate `tests/fixtures/golden_vectors.json`.

**Read `DECISIONS.md` D1 before trusting these.** BUILD_PROMPT acceptance check 1
asks the engine to match `spec/edge_engine.jsx` on at least ten golden vectors.
There is no jsx in this repository, so these vectors are generated from this
engine and frozen. That makes them a drift test, not an independent check: they
will catch a refactor that changes a number, and they cannot catch the engine
having been wrong from the start.

Regenerate only with a `DECISIONS.md` entry saying which number changed and why.
A silent regeneration turns the one test that guards the arithmetic into a test
that agrees with whatever the arithmetic currently does.

    python3 scripts/golden_vectors.py --check     # fail if the fixture is stale
    python3 scripts/golden_vectors.py --write     # rewrite it
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cfb_edge.engine import config  # noqa: E402
from cfb_edge.engine.clv2 import clv_pct, close_fair_prob_at_entry, fair_prob  # noqa: E402
from cfb_edge.engine.evidence import SystemRecord, raw_weight, wilson_lower  # noqa: E402
from cfb_edge.engine.posterior import Quote, decide  # noqa: E402
from cfb_edge.engine.pricing import decide_at_price, executable  # noqa: E402
from cfb_edge.engine.sizing import portfolio_scale, round_down  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "golden_vectors.json"

MATURE = dict(system_id="mature", family="system", wins_provider=194, losses_provider=129)
YOUNG = dict(system_id="young", family="sharp", wins_provider=3, losses_provider=1)
DEEP = dict(system_id="deep", family="model", wins_provider=500, losses_provider=400)


def _decision_vector(name: str, quote: Quote, signals, cfg, c=1) -> dict:
    d = decide(quote, signals, cfg=cfg, c=c)
    return {
        "name": name,
        "kind": "decision",
        "inputs": {
            "market": quote.market, "side": quote.side, "line": quote.line,
            "price": quote.price, "other_price": quote.other_price,
            "venue": quote.venue, "push": quote.push, "c": c,
            "signals": [
                {"record": {k: v for k, v in r.__dict__.items() if v not in (0, 0.0, "")},
                 "side": side}
                for r, side in signals
            ],
        },
        "expected": {
            "p_baseline": d.p_baseline, "p_model": d.p_model, "w_mod": d.w_mod,
            "p_post": d.p_post, "ev": d.ev, "f_full": d.f_full,
            "portfolioScale": d.portfolio_scale, "stake": d.stake_units,
            "maxPlayablePrice": d.max_playable_price,
            "decision": d.decision, "reasonCodes": sorted(d.reason_codes),
        },
    }


def build() -> dict:
    cfg = config.default()
    mature, young, deep = SystemRecord(**MATURE), SystemRecord(**YOUNG), SystemRecord(**DEEP)
    spread = dict(sport="ncaaf", market="spreads", side="home", line=-3.0,
                  price=-110.0, other_price=-110.0)

    vectors = [
        _decision_vector("v01 mature system, -110 spread",
                         Quote(event_id="e1", **spread), [(mature, "for")], cfg),
        _decision_vector("v02 no signals at all",
                         Quote(event_id="e2", **spread), [], cfg),
        _decision_vector("v03 two signals, one family",
                         Quote(event_id="e3", **spread),
                         [(mature, "for"),
                          (SystemRecord(**{**MATURE, "system_id": "mature2"}), "for")], cfg),
        _decision_vector("v04 two signals, two families",
                         Quote(event_id="e4", **spread),
                         [(mature, "for"),
                          (SystemRecord(**{**MATURE, "system_id": "m2", "family": "sharp"}), "for")],
                         cfg),
        _decision_vector("v05 equal and opposite",
                         Quote(event_id="e5", **spread),
                         [(mature, "for"),
                          (SystemRecord(**{**MATURE, "system_id": "m3", "family": "sharp"}), "against")],
                         cfg),
        _decision_vector("v06 tiny sample is not evidence",
                         Quote(event_id="e6", **spread), [(young, "for")], cfg),
        _decision_vector("v07 deep but thin edge",
                         Quote(event_id="e7", **spread), [(deep, "for")], cfg),
        _decision_vector("v08 four correlated positions",
                         Quote(event_id="e8", **spread), [(mature, "for")], cfg, c=4),
        _decision_vector("v09 de-vig sensitive moneyline",
                         Quote(event_id="e9", sport="ncaaf", market="h2h", side="home",
                               line=None, price=-118.0, other_price=110.0),
                         [(mature, "for")], cfg),
        _decision_vector("v10 price better than -110",
                         Quote(event_id="e10", **{**spread, "price": -102.0,
                                                  "other_price": -102.0}),
                         [(mature, "for")], cfg),
        _decision_vector("v11 whole-number spread with push mass",
                         Quote(event_id="e11", sport="ncaaf", market="spreads", side="home",
                               line=-3.0, price=-110.0, other_price=-110.0, push=0.097),
                         [(mature, "for")], cfg),
        _decision_vector("v12 late price, logged but ungradeable",
                         Quote(event_id="e12", **spread, price_source="late"),
                         [(mature, "for")], cfg),
    ]

    primitives = [
        {"name": "p01 wilson 194-323", "kind": "value",
         "expected": wilson_lower(194, 323)},
        {"name": "p02 wilson 3-4", "kind": "value", "expected": wilson_lower(3, 4)},
        {"name": "p03 raw weight n=323", "kind": "value",
         "expected": raw_weight(SystemRecord(**MATURE), cfg)},
        {"name": "p04 portfolio scale c=4 rho=0.3", "kind": "value",
         "expected": portfolio_scale(4, rho=0.3)},
        {"name": "p05 round down 0.2749 to 0.05", "kind": "value",
         "expected": round_down(0.2749, 0.05)},
        {"name": "p06 ev at -110 on 0.515", "kind": "value",
         "expected": decide_at_price(0.515, executable(-110.0)).ev},
        {"name": "p07 kalshi 49c payout", "kind": "value",
         "expected": executable(49.0, kind="exchange_cents", venue="kalshi",
                                venue_config={"fee_rule": "kalshi_general",
                                              "fee_coefficient": 0.07}).payout},
        {"name": "p11 kalshi fee on an american quote", "kind": "value",
         "expected": executable(-105.0, venue="kalshi",
                                venue_config={"fee_rule": "kalshi_general",
                                              "fee_coefficient": 0.07}).payout},
        {"name": "p08 clvPct at -110 on a -108/-112 close", "kind": "value",
         "expected": clv_pct(fair_prob(-108.0, -112.0), -110.0)},
        {"name": "p09 cross-number spread conversion", "kind": "value",
         "expected": close_fair_prob_at_entry(
             market="spreads", entry_line=-2.5, close_line=-3.0,
             close_fair=fair_prob(-108.0, -112.0))[0]},
        {"name": "p10 totals conversion, over", "kind": "value",
         "expected": close_fair_prob_at_entry(
             market="totals", side_is_over=True, entry_line=51.5, close_line=52.5,
             close_fair=fair_prob(-110.0, -110.0))[0]},
    ]

    return {
        "note": "Generated from this engine, not from edge_engine.jsx. See DECISIONS.md D1.",
        "engineVersion": __import__("cfb_edge.engine.version", fromlist=["x"]).ENGINE_VERSION,
        "w0": cfg.w0,
        "n_half": cfg.n_half,
        "vectors": vectors + primitives,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="rewrite the fixture")
    ap.add_argument("--check", action="store_true", help="fail if the fixture is stale")
    args = ap.parse_args(argv)

    payload = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.write:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(payload, encoding="utf-8")
        print(f"wrote {FIXTURE} ({len(json.loads(payload)['vectors'])} vectors)")
        return 0
    if args.check:
        if not FIXTURE.exists():
            print("fixture missing; run with --write")
            return 1
        if FIXTURE.read_text(encoding="utf-8") != payload:
            print("golden vectors are stale. A number the engine produces has "
                  "changed. Explain it in DECISIONS.md before regenerating.")
            return 1
        print("golden vectors current")
        return 0
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
