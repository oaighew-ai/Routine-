# CFB Edge promotion review scorecard

Policy V1 is frozen before any new holdout is registered. It is a research-review contract, never a picks or staking authority. S02 remains the sole paper-delivery candidate; S04_ES2 and BR2 remain shadows.

## Six gates, all required

1. **Preregistration:** bind the policy hash, exact model, feature, training and execution artifacts; name all future holdout weeks and its start/end; allow one candidate attempt per model. An independent reviewer must approve the dependence unit before the holdout starts. BR2 cannot register a fitted model that does not yet exist.
2. **Data integrity:** one observation per canonical game ID, decision-time archived forecasts, input evidence, executable quotes, settlement and complete decision-universe accounting. Missing or invalid rows block the entire evaluation; they are not silently dropped. ES2 also requires true-open provenance with lag at most 900 seconds. BR2 has no dependency on ES2's opening gate unless its own registered feature/execution contract requires it.
3. **Historical replay:** independent, hash-bound review of timestamp-clean historical replay and its registered pass criteria, with all tried variants disclosed. Research papers motivate hypotheses; they are not empirical evidence for this particular implementation.
4. **Walk-forward:** independently reviewed chronological train/validation folds that pass the registered training protocol. Its review must predate the holdout. The scorecard checks the review contract; it does not manufacture a historical backtest or train BR2.
5. **Prospective holdout:** one fixed final evaluation, after the registered end. At least 4,710 distinct games and 26 week clusters under the planning assumptions below. A shortfall is inconclusive/blocked. Do not extend or change the holdout after viewing its performance; a new design requires a separately registered future experiment.
6. **Economics:** the one-sided 97.5% week-cluster bootstrap lower bound on average net unit returns must remain positive after an additional 0.01 unit cost per decision. The paired Brier improvement lower bound must be at least 0.001, and fixed ten-bin calibration error must be at most 0.05. These are preregistered design choices, not fitted results or universal betting standards.

Passing all gates yields **REVIEW_ELIGIBLE**. It does not modify delivery eligibility, actual stake, the existing stopping rule, S04_ES2's signal or market gates, or the private site's `/api/picks` authority. A separate independent promotion decision is still required.

## Statistical choices and practical limits

Two model families share a 5% false-positive budget, allocated as a one-sided 2.5% test per model. Return confidence bounds resample complete weeks, using 5,000 draws and a fixed seed. This is an approximate fixed-look bootstrap, not an anytime-valid guarantee. Persistent cross-week dependence can invalidate that approximation: the independent dependence review must reject the design if weeks are not a defensible sampling unit.

The planning calculation is `ceil(((z_0.975 + z_0.80) * 1 / 0.05)^2 * 1.5) = 4,710` games. Assumptions: a 5% net-return effect, one-unit return standard deviation, 80% power and a 1.5 dependence allowance. They are not measured BR2 performance. Detecting a 2% effect under the same assumptions takes roughly 29,434 games. This makes the feasibility constraint explicit: a low-frequency CFB strategy may need many seasons. A minimum count alone never establishes an edge.

Historical and operational stages can be completed earlier. They can justify continuing paper research, but do not replace the prospective economic requirement. The scorecard withholds aggregate return and confidence metrics before the final date. Other research artifacts may still contain outcomes; governance must preserve the untouched evaluation cohort.

Probabilities are conditional on a non-push result. Pushes return principal, incur recorded costs, and contribute zero to the per-decision Brier gain; calibration uses non-push rows. The evaluator derives realized unit returns from odds, settlement and costs. Input `costPerUnit` must include all fees/slippage not already represented in `decimalOdds`, without double counting.

## Evidence registration and storage

The CLI accepts `data/promotion/evidence.json` on the evidence branch, keyed by `S04_ES2` and `S04_BR2`. Each model entry contains `registration`, `historicalReplay`, `walkForward`, `universeAudit`, and `holdoutRows`. The test fixture documents exact field names. No entry is created for nonexistent evidence.

Hashes are content identifiers and review attestations, not cryptographic signatures. The independent reviewer must verify referenced raw artifacts, timestamps, the complete decision universe, feature definitions, transformations, missing-data rules, candidate grid, and train/validation boundaries. A local hash or `independentlyReviewed` field is not self-authenticating proof. The scorecard is deliberately unable to authorize trading even when supplied with passing attestations.

The bridge workflow publishes both `data/promotion-scorecard.json` and its compact representation in `data/private-site-bridge.json`. The command center displays the resulting states and blockers. Absent registration or evidence is BLOCKED, not PASS.

## Research basis

- Proper scoring rules: https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf
- Backtest selection risk: https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
- For a separately preregistered sequential design, rather than repeatedly inspecting this fixed-look test: https://arxiv.org/abs/1810.08240
