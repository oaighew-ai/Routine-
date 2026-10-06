# BR2 hardening and remaining evidence gaps

Review date: September 24, 2026. This is a collection and integrity review, not evidence of predictive edge. S04_ES2, its 900-second opening limit, and zero actual stakes are unchanged.

## How to obtain proven true opens

The last inspected capture-data snapshot has 57 observed games, all first_seen, and zero provenance-complete true opens. The alias repair recovered coverage, not the missing opening history. The capture-health report is now published.

For a newly opening market, the existing collector must persist the exact event and contract tickers, venue opening times for the contracts used, the first valid two-sided quote time, poll time, code revision, and replayable quote evidence. The first capture must fall within the frozen 900-second lag limit. The health report must validate the provenance, not merely count a source label.

Start the collector before markets list, poll at least every 60â€“120 seconds through the expected listing period, and alert on a poll gap above 5 minutes. A continuously running worker is preferable for this window: a GitHub schedule can be delayed and is not a timing guarantee. Keep a separate independent heartbeat and durable append-only storage. Test the entire path on a newly listed market before calling the next cohort audit-grade.

Do not overwrite the existing opening log, relabel first_seen, or accept a newly added rung as the original event opening. All 57 current Week 5 games are already observed. Continued tail capture does not turn those late observations into true opens. Recover only genuinely contemporaneous archives with exact identity and quote evidence; otherwise record Week 5 as nonqualifying and preregister a new prospective cohort separately.

## How to unblock opponent-adjusted EPA

The archived CFBD source-health result records HTTP 401 for `/wepa/team/season?year=2026`, while ordinary CFBD endpoints succeeded. The owner reports an existing paid tier. This makes key/account linkage or unapplied endpoint entitlement the next diagnostic target; HTTP 401 alone does not identify the cause.

1. Check the account behind the configured `CFBD_API_KEY`. CFBD's current public tier page lists opponent-adjusted metrics under Tier 1 ($1/month) and above: https://collegefootballdata.com/api-tiers .
2. If already subscribed, confirm that the key belongs to the entitled account; resolve account linkage with CFBD if needed. Do not put the key in chat or a committed file.
3. Update the existing repository secret only if the key changed. Rerun BR2 capture and require HTTP 200, an array with numeric `epa.total` and `epaAllowed.total` for both teams, and an archived payload hash and retrieval time.
4. Populate only decisions after that retrieval time. A new season aggregate cannot repair earlier-week snapshots. PPA remains a separate feature.

## Ten-gap disposition

| Gap | Implemented in this change | Still needed |
|---|---|---|
| Information state | Shadow adapter for same-market home spread movement, unchanged-spread implied price movement, dispersion, fresh-book count, observed last move, verified news and kickoff distance. Future/closing/conflicting quotes excluded. | Supply archived, canonically mapped sportsbook history and verified news. No fake zero for missing history. This is not yet a live complete feed. |
| Canonical decision time | Context and warehouse share exactly one decisionTime; sources must precede it and kickoff; context hashes are checked; future/incomplete-game plays excluded. | Continued prospective snapshots and operational verification. |
| True-open provenance | Existing capture-health publication confirmed; late rows stay excluded. | A genuinely new opening captured on time, or an already-existing qualifying contemporaneous archive. |
| Game identity | Immutable CFBD anchor, exact kickoff/orientation resolution, ambiguous fixtures rejected; crosswalk rejects conflicting provider mappings. Real replay resolves 57/57. | Reviewed Action, sportsbook, cfbfastR and exchange crosswalk coverage. IDs are not inferred from similar strings. |
| EPA / QB coverage | Week 5 remains immutable at EPA 0/57 and QB 1/57. For later cohorts, QB continuity now derives from completed-game CFBD passing-attempt usage with validated official starter overrides; EPA prefers CFBD WEPA and has a versioned cfbfastR play-EPA opponent-adjusted fallback. | Live prospective run must prove actual provider coverage/schema and replay. No Week 5 backfill. |
| Monitor ingestion | Existing discovery/capture boundaries preserved. | Autonomous discovery-to-archive-to-candidate ingestion remains unimplemented. It can run outside the five ChatGPT task slots, but requires provisioned provider credentials and a durable job. Candidate extraction must not self-approve evidence. |
| Weather replay | Single-run replay validator checks raw hash, explicit model run, availability before decision, units and kickoff forecast point. | Real archived-run availability evidence and an end-to-end historical golden fixture. The helper alone is not proof that a forecast was available. |
| Statistical plan | Existing no-fit/no-promotion gate retained. | Preregister the complete analysis plan below before any fit. No fitted BR2 model or holdout result is claimed. |
| Semantic tests | Real CFBD record subsets with original archive lineage test game identity, line-yard orientation, home/away sign, venue coordinates and dome boolean; adversarial timestamp and API tests added. | Broader real neutral-site/dome and provider-schema fixtures, plus complete external crosswalk tests. |
| QB v2 | Historical Week 5 artifact is frozen. Active prospective collection uses `CFB_EDGE_BR2_QB_CONTINUITY_V4`: last-three completed-game attempt share with optional validated official starter override. | Preserve richer starts/snaps/OR/availability/status-age diagnostics as shadow fields; do not add weights until separately frozen. |

## Statistical plan to register before fitting

Lock the target and decision horizon first, then the exact football and information-state feature list. Fit any winsorization and standardization on training data only. Specify complete-case rejection or a fixed training-only imputation method and missingness indicators before results are visible. Predeclare candidate families, every hyperparameter combination, chronological folds, the maximum number of challenger attempts, primary proper scoring metric, calibration diagnostics, minimum improvement and confidence interval procedure.

Reserve named future weeks that begin after the feature/analysis freeze. Never use them for thresholds, feature selection or retry decisions. Require fresh executable prices, fees, slippage and a preregistered economic test before any promotion; maintain zero stakes until the independent promotion contract passes. If sample size is inadequate, report inconclusive rather than relax the rule.

## API and operational limits

The BR2 CFBD workflow now retries only transient GET failures (429/5xx/network) with bounded waits, records response receipt times, and rejects non-array schema responses. It does not retry authorization failures. Weather rejects post-kickoff captures, negative wind and truthy non-boolean dome values. These are tested repairs, not a blanket claim that every external service is available.

Weather, source entitlements, scheduled-job punctuality, and monitor ingestion must remain explicit health signals. A green workflow is execution success, not proof of complete feature coverage or model quality.


## September 28 prospective coverage repair

The coverage fix is deliberately forward-only.

- QB continuity no longer depends on finding two consecutive historical
  depth-chart PDFs for every school. CFBD `/games/players?category=passing`
  supplies archived completed-game participation. The most recent
  passing-attempt leader is the default incumbent; a validated official
  pregame starter can override only when it maps to prior participation.
- CFBD WEPA remains preferred. If the endpoint remains authorization-blocked,
  `CFB_EDGE_OA_EPA_V1` downloads and hashes the current cfbfastR 2026
  play-by-play asset, selects only completed prior-week play-EPA rows, archives
  that exact used subset, and fits a fixed 50-play-ridge two-way
  offense/defense-allowed adjustment.
- A failure in either new adapter stays null and does not erase the other BR2
  families.
- The new active cohort is `CFB_2026_PRODUCT_WEEK6` (Oct. 2–3 product slate,
  provider Week 5). It is separate from the completed Week 5 S04_ES2 audit
  cohort.
