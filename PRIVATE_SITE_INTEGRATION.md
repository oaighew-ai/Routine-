# Private Site integration contract

The authoritative product remains:

- UI: https://cfb-edge-research.oaighew.chatgpt.site
- Picks authority: local `/api/picks`

GitHub remains research/evidence only.

## One external payload

The private Site should read exactly one GitHub evidence payload:

`https://raw.githubusercontent.com/oaighew-ai/Routine-/capture-data/data/private-site-bridge.json`

Do not read `picks.json` or reconstruct S02 from GitHub.

The bridge contains four UI-ready blocks:

- `authority`: confirms that picks remain private-Site local.
- `week5`: audit-grade opening coverage, executable-shadow count and CLV grading.
- `br2`: point-in-time feature coverage and raw-source replay status, including per-family row counts, fully populated rows, and the first non-null QB-continuity example.
- `runway`: the four-stage Evidence Runway for the command center.

## Required Site behavior

1. Render the existing pick card from local `/api/picks`.
2. Fetch the bridge read-only for research status.
3. If the bridge is unavailable, keep picks behavior unchanged and mark research telemetry unavailable.
4. Never let bridge values change a pick, stake, model threshold or delivery authorization.
5. Show `research.week5DecisionRuleChanged=false` and `deliveryEffect=NONE` in the research panel.
6. Treat `authority.githubCanPublishPicks=false` as an invariant. Fail closed if it is ever not false.

This gives the private Site one authoritative decision endpoint and one
non-authoritative research endpoint, while GitHub Pages can remain diagnostic.
