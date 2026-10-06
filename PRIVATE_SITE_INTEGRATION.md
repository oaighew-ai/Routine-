# Private Site integration contract

The authoritative product remains:

- UI: https://cfb-edge-research.oaighew.chatgpt.site
- Picks authority: local `/api/picks`

GitHub remains research/evidence only.

## One external payload

The private Site should read exactly one GitHub evidence payload:

`https://raw.githubusercontent.com/oaighew-ai/Routine-/capture-data/data/private-site-bridge.json`

Do not read `picks.json` or reconstruct S02 from GitHub.

The bridge contains seven UI-ready blocks:

- `authority`: confirms that picks remain private-Site local.
- `week5`: audit-grade opening coverage, executable-shadow count and CLV grading.
- `marketCoverage`: weekly market-match audit status, summary and per-game market
  identity. Missing audit data is `UNAVAILABLE`, not zero coverage.
- `activeMarket`: current prospective market/open-capture status and the
  expected open cohort. Keep this separate from the historical Week 5
  `marketCoverage` audit.
- `marketRelative`: the frozen S06_MR1 walk-forward status, coverage, error
  metrics and freeze/dataset hashes. It is evaluation telemetry, never pick
  authority.
- `br2`: point-in-time feature coverage and raw-source replay status, including per-family row counts, fully populated rows, and the first non-null QB-continuity example.
- `runway`: the four-stage Evidence Runway for the command center.

The `sourceTimes` metadata records each source artifact's own generation time
(`asOf` for authority). A missing timestamp is `null`; do not substitute the
bridge's `generatedAt` or treat it as evidence that every source is fresh.

## Required Site behavior

1. Render the existing pick card from local `/api/picks`.
2. Fetch the bridge read-only for research status.
3. If the bridge is unavailable, keep picks behavior unchanged and mark research telemetry unavailable.
4. Never let bridge values change a pick, stake, model threshold or delivery authorization.
5. Show `research.week5DecisionRuleChanged=false` and `deliveryEffect=NONE` in the research panel.
6. Treat `authority.githubCanPublishPicks=false` as an invariant. Fail closed if it is ever not false.
7. Show `activeMarket` as current prospective market evidence, using its own
   `generatedAt`, `status`, `summary`, and `openingPolicy`. Do not label
   `marketCoverage` freshness as the current-market state; that block describes
   the historical audit cohort.
8. Keep market coverage and source timestamps informational only; neither can alter the pick card or delivery authority.
9. Render `marketRelative` as a model-evaluation panel. Show its status and
   prediction/week coverage next to the market MAE comparison and freeze hash.
   Never infer model readiness from positive MAE improvement; `deliveryEligible`
   must remain false, `promotionEffect` must remain `NONE`, and stake must remain
   zero.

This gives the private Site one authoritative decision endpoint and one
non-authoritative research endpoint, while GitHub Pages can remain diagnostic.
