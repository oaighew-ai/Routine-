# CFB Edge Operating Architecture

Status: proposed production operating model
Date: 2026-09-21
Scope: NCAA FBS, SHADOW / paper research until promotion gates clear

## 1. Decision this architecture supports

CFB Edge exists to answer one question reliably:

> Is there a price-sensitive, prospectively validated college-football edge worth acting on, and if so at what executable price?

It does not exist to produce a pick for every game.

The system therefore separates four functions that must never collapse into one another:

1. **Capture** facts and prices.
2. **Research** hypotheses and challenger models.
3. **Decide** from frozen, validated inputs.
4. **Deliver** one authoritative card.

A successful week can contain zero bets.

## 2. Non-negotiable operating rules

- The market is the baseline prior, not the enemy to be beaten on every game.
- Historical research never authorizes live delivery by itself.
- No model can see information that was unavailable at the decision timestamp.
- Every candidate row is immutable once written.
- Every live or paper recommendation is price-sensitive and includes a maximum playable price.
- Every model has an explicit role in the registry.
- One model only may supply the authoritative paper card at a time.
- Missing provenance, stale data, failed validation, conflicting model identity, or replay failure returns **NO_BET**.
- Losing picks remain in the ledger.
- Closing-line value, calibration and prospective evidence are primary process measures. Win rate is secondary.
- New complexity must beat the incumbent prospectively before receiving delivery authority.

## 3. Production topology

### 3.1 Control plane: GitHub main

Owns code, specs, tests, model registry, decision rules and workflows.

Authoritative files:
- `config/model_registry.json`
- `config/delivery_authority.json`
- `config/edge_os.json`
- `DECISIONS.md`
- `GRAVEYARD.md`
- `MODEL.md`
- `spec/`

Changes to model identity, gates or formulas occur through pull requests and CI.

### 3.2 Evidence plane: capture-data branch

Append-only evidence store produced by automation.

Owns:
- market snapshots
- slate metadata
- capture reports
- frozen candidate rows
- grades
- paper ledger
- run receipts
- diagnostic pick output

Automation writes. Research code reads. Humans do not rewrite historical rows.

### 3.3 Research plane

Runs backtests, calibration studies, challenger models and feature experiments.

Outputs:
- evidence reports
- candidate model artifacts
- comparison metrics
- promotion recommendations

Research outputs are never picks.

### 3.4 Delivery plane

The private Site `/api/picks` remains the sole authoritative live/paper delivery contract.

The public GitHub dashboard is diagnostic only.

No second pick feed is permitted.

## 4. Data-source architecture

### Tier 1: executable market data

Primary use: current price, line shopping, baseline belief, closing reference.

Sources:
- The Odds API for major sportsbook prices
- Pinnacle when present as preferred sharp reference
- US-book median when Pinnacle is unavailable
- Kalshi only where the contract, settlement rule and fee schedule are verified

Required fields:
- event ID
- teams
- kickoff
- market
- side
- line
- price
- venue
- fetch timestamp
- book set

### Tier 2: opening and historical market evidence

Primary use: line-movement research and CLV.

Sources:
- first-seen capture log
- CFBD open fields where appropriate as reference data
- historical archives only when timestamp semantics are understood

Rule:
A reference open can validate movement but cannot be silently substituted for an executable entry price.

### Tier 3: game / football context

Primary use: challenger models only until validated.

Candidate fields:
- team efficiency
- opponent-adjusted efficiency
- QB status / continuity
- returning production
- coaching continuity
- travel / rest
- pace
- weather
- injury status

These features must enter through versioned snapshots with timestamps.

### Tier 4: results

Primary use: grading, calibration and retrospective diagnostics.

Fields:
- final score
- ATS result
- push
- moneyline outcome
- total outcome
- closing line
- closing fair probability

## 5. Storage schema

Physical source of truth remains append-only JSONL / gzipped JSON in the evidence plane. A relational analytical mirror may be added later, but it must be derived from immutable rows, not become an independently edited authority.

### games
- game_id
- season
- week
- kickoff_at
- away_team_id
- home_team_id
- neutral_site
- venue
- final_away
- final_home
- status

### market_snapshots
- snapshot_id
- game_id
- fetched_at
- source
- venue
- market
- side
- line
- price
- opposite_price
- book_set_hash
- raw_hash

Unique logical key:
`game_id + fetched_at + venue + market + side + line`

### model_forecasts
- forecast_id
- game_id
- model_id
- model_version
- model_sha256
- protocol
- generated_at
- market
- side
- fair_line
- fair_probability
- uncertainty
- feature_snapshot_hash
- sample
- delivery_eligible

### candidates
- candidate_id
- game_id
- logged_at
- phase
- market
- side
- line
- executable_price
- venue
- p_baseline
- p_model
- p_post
- ev
- f_full
- stake_units
- max_playable_price
- decision
- reason_codes
- model_id
- model_version
- model_sha256
- protocol
- spec_hash
- source_snapshot_id
- price_source

### grades
- candidate_id
- graded_at
- close_ref
- close_line
- close_price
- close_fair_probability
- clv_pct
- clv_lag_pct
- clv_method
- raw_move
- result
- units

### placements
Live phase only.
- candidate_id
- placed_at
- venue
- placed_price
- placed_line
- stake

### model_validations
- validation_id
- model_id
- model_version
- model_sha256
- protocol
- completed_at
- cohort_start
- cohort_end
- n_forecasts
- week_clusters
- outcome_coverage
- log_loss
- market_log_loss
- brier
- market_brier
- ece
- market_ece
- anytime_e_value
- replay_verified
- gate_status
- failed_gates

### run_receipts
- run_id
- workflow
- started_at
- completed_at
- revision
- status
- games_seen
- rows_written
- stale_count
- error_count
- source_age_max
- credits_used
- artifact_hashes

## 6. Model stack

### S00: market benchmark

Not a betting model.

Purpose:
- consensus baseline
- no-vig probability
- closing benchmark
- benchmark loss / calibration

Every challenger must beat or add value to S00 on prospective data.

### S02: production paper candidate

Role:
- market-anchor shrinkage
- sole possible paper delivery candidate
- frozen protocol
- no silent retraining

Current rule:
S02 cannot deliver while `delivery_authority.json` has `allowPaperDelivery=false`.

### S03: input-integrity shadow

Purpose:
- freeze prospective inputs
- prove deterministic replay
- detect source drift and missing data

It never supplies a pick.

### S04: football-context challenger

This is where the master prompt's richer football model belongs.

Recommended first version:
- market baseline
- opponent-adjusted efficiency
- QB continuity / availability
- offensive and defensive explosiveness
- success rate
- line-of-scrimmage proxy
- pace
- home / travel / rest
- weather where material

Do not begin with seven independent models.

Start with one regularized residual model that predicts **market residual**, not raw score:

`target = realized margin - market-implied margin`

or, for probability evaluation:

`target = outcome - market no-vig probability`

The challenger earns complexity only after prospective evidence.

### S05: information-flow monitor

Purpose:
- line movement
- book dispersion
- stale-book detection
- unusual disagreement
- injury/news timing where timestamped

Initially context only.

### Ensemble policy

No ensemble enters production until at least two independently validated challenger models exist.

When that happens:
- market baseline retains explicit weight
- model weights are determined from prospective calibration / loss
- disagreement increases uncertainty
- a model cannot receive more weight because it had a short hot streak

## 7. Validation stack

Every delivery-eligible model must pass four layers.

### Layer A: deterministic correctness
- unit tests
- golden vectors
- no network in tests
- replay identical from frozen inputs
- sign and price-convention checks

### Layer B: historical walk-forward
- train only on prior periods
- validate chronologically
- untouched test cohort
- no closing-line leakage
- no late injury leakage

Purpose: reject obviously weak ideas, not authorize delivery.

### Layer C: prospective shadow
Required for promotion.

Measure:
- n
- week clusters
- outcome coverage
- log loss vs market
- Brier vs market
- ECE
- CLV
- execution-lag CLV
- anytime evidence

### Layer D: promotion gate

Promotion is mechanical from a committed gate file.

No manual override because a model "looks good."

## 8. Decision engine

Order of operations:

1. Load delivery authority.
2. Verify model ID, version, SHA and protocol.
3. Verify validation freshness.
4. Verify deterministic replay.
5. Load latest eligible snapshot at or before decision time.
6. Reject stale or unmapped inputs.
7. Build market baseline.
8. Run production model.
9. Compute fair probability / fair line.
10. Compare against executable price.
11. Apply fee-adjusted EV.
12. Run de-vig sensitivity.
13. Run model-disagreement / uncertainty checks.
14. Compute maximum playable price.
15. Apply one-position-per-game rule.
16. Size only if phase permits.
17. Freeze candidate row.
18. Publish only if all delivery gates pass.

Default result:
`NO_BET`

## 9. Automation cadence

All times America/New_York.

### Sunday evening through Tuesday: opening capture
Goal: preserve first-seen evidence before value disappears.

Current 10-minute capture loop remains the high-frequency evidence process during the release window.

Outputs:
- slate
- first-seen lines
- raw snapshots
- capture report
- run receipt

### Tuesday 09:00: opening-board research run
- create full slate
- freeze first prospective S02/S04 forecasts
- create watchlist
- do not force picks

### Thursday 07:00: midweek refresh
- refresh prices
- refresh known availability / context inputs
- compare against frozen forecasts
- score line movement
- flag data conflicts

### Friday 17:00: pre-final board
- re-run decision engine
- isolate actionable candidates
- identify unresolved QB / weather / injury holds

### Saturday 08:00: final morning board
- refresh executable prices
- publish BET / WATCH / NO_BET
- include max playable price

### Pre-kick checks
For active candidates only:
- refresh quote
- expire if worse than max playable price
- reject stale source
- re-evaluate material status changes

### Postgame / Sunday
- ingest results
- grade all candidates
- compute CLV
- update calibration
- write weekly scorecard

### Monday 08:00: model review
- diagnose errors
- compare production vs challengers
- never retrain directly from a weekly loss
- open research issues for statistically testable hypotheses

## 10. Agent responsibilities

### Capture Agent
Owns:
- odds retrieval
- opening-line capture
- timestamping
- raw hashes
- source freshness

Cannot:
- recommend bets
- alter model weights

### Integrity Agent
Owns:
- schema validation
- team mapping
- duplicate detection
- stale-data checks
- deterministic replay
- source drift

Can veto delivery.

### Market Agent
Owns:
- no-vig
- consensus
- reference price
- line shopping
- fee normalization
- max playable price

Cannot create a football projection.

### Production Model Agent
Owns:
- S02 forecast only
- frozen production protocol

Cannot retrain itself.

### Challenger Research Agent
Owns:
- S04/S05 experiments
- walk-forward tests
- ablations
- feature selection
- prospective comparison

Cannot publish picks.

### Risk / Decision Agent
Owns:
- evidence gates
- EV
- sizing
- one-position rules
- reason codes
- fail-closed decision

### Grading Agent
Owns:
- close selection
- outcome grading
- CLV
- lag CLV
- calibration metrics
- weekly diagnostics

Cannot rewrite candidate history.

### Delivery Agent
Owns:
- authoritative `/api/picks`
- card serialization
- dashboard feed

Can publish only rows approved by the Risk / Decision Agent.

## 11. Dashboard wireframe

### Header
- Week
- phase
- production model
- model version
- validation age
- source freshness
- last successful capture

### Row 1: decision KPIs
- games analyzed
- BET
- WATCH
- NO_BET
- expired
- unresolved holds

### Primary table: actionable board
Columns:
- rank
- game
- market
- side
- current price
- bet to
- fair price / fair line
- estimated edge
- confidence / uncertainty
- key driver
- primary risk
- last move
- status

### Market movement panel
- biggest favorable moves
- biggest adverse moves
- stale-book candidates
- cross-book dispersion

### Model health panel
Production vs market:
- log loss
- Brier
- ECE
- spread MAE
- CLV
- lag CLV
- sample
- week clusters

### Challenger panel
For S04/S05:
- shadow sample
- incremental log-loss improvement
- incremental Brier improvement
- CLV
- calibration
- promotion status

No challenger "win rate leaderboard."

### Reliability panel
- capture freshness
- missing books
- mapping failures
- replay status
- failed evidence gates
- current workflow health

## 12. Build sequence

### P0: stabilize evidence capture
Owner: Capture + Integrity
Status: in progress / largely implemented

Exit criteria:
- correct current-week slate
- scheduled capture green
- first-seen semantics verified
- no silent empty captures
- immutable receipts

### P1: canonical schema and event IDs
Owner: Integrity

Work:
- consolidate row schemas
- enforce event identity
- add input hashes everywhere
- define schema-version migrations

Exit:
Every forecast, quote, candidate and grade joins deterministically by game ID and timestamp.

### P2: S02 prospective production loop
Owner: Production Model + Risk

Work:
- freeze S02 forecast per game
- persist candidate rows
- persist reasons for every pass
- grade every row
- maintain validation snapshot

Exit:
A full week can replay byte-for-byte from evidence.

### P3: S04 residual challenger
Owner: Challenger Research

Work:
- build timestamped football-context dataset
- choose small initial feature set
- fit regularized residual model
- walk-forward backtest
- start prospective shadow

Exit:
No production effect. Only evidence collection.

### P4: dashboard consolidation
Owner: Delivery

Work:
- single data contract
- board + model health + reliability
- private Site remains authoritative
- public page remains diagnostic

Exit:
No competing cards or conflicting statuses.

### P5: automated weekly grading and review
Owner: Grading

Work:
- result ingestion
- close / lag close
- weekly scorecard
- confidence calibration
- model comparison

Exit:
Monday report requires no manual spreadsheet work.

### P6: promotion framework
Owner: Risk

Work:
- challenger comparison gate
- minimum sample and cluster requirements
- sequential evidence
- rollback rule
- model registry transition procedure

Exit:
Model promotion is a reproducible state transition, not a judgment call.

### P7: live-capital readiness
Blocked until evidence clears.

Required:
- delivery gate passed
- venue / fee rules verified
- placement logging works from phone
- actual fill price captured
- bankroll and max-loss limits explicitly approved
- rollback tested

## 13. Current-state assessment as of 2026-09-21

Already present:
- scheduled capture workflow
- CI across Python versions
- append-only evidence concepts
- CLV machinery
- model registry
- delivery authority
- deterministic diagnostics
- watchdog
- private-Site authority
- shadow model roles
- price-shopping workflow

Current blocker:
S02 is **not authorized for paper delivery** by the repository's delivery authority.

Observed validation snapshot:
- 48 non-push forecasts
- 2 week clusters
- outcome coverage 1.00
- model log loss worse than market
- model Brier worse than market
- anytime evidence far below threshold

Failed gates:
- minimum forecasts
- minimum weeks
- log-loss advantage
- Brier no worse than market
- anytime evidence

Therefore the correct operating state is:

**CAPTURE + VALIDATE + SHADOW. NO CLAIM OF VALIDATED PICKS EDGE.**

## 14. Immediate execution backlog

1. Merge or supersede the current-week capture fix after CI and one live capture verify the intended slate.
2. Make the evidence schema canonical across capture, candidates and grades.
3. Ensure every scheduled run writes a run receipt with source age and input hashes.
4. Freeze S02 prospective rows for every eligible game, including NO_BET outcomes.
5. Automate grading and weekly validation refresh.
6. Build S04 as a small residual challenger, not a seven-model ensemble.
7. Consolidate the dashboard onto one contract from the private Site.
8. Add promotion / rollback state transitions to the model registry.
9. Keep real staking disabled until the promotion gate and execution controls both pass.

## 15. Definition of done

CFB Edge is operational when:

- every eligible game is captured automatically
- every production forecast is immutable and replayable
- every game receives a recorded BET / WATCH / NO_BET outcome
- no recommendation can publish without valid provenance and current validation
- every candidate is graded automatically
- the dashboard shows one authoritative card
- challengers can be compared prospectively without contaminating production
- model promotion is mechanical
- an empty card is treated as a valid result


## 16. API, plugin and external-data enhancement layer

The next gains should come from better point-in-time evidence and market-state reconstruction, not another generic predictive model. Every source below must be assigned a narrow role before it can affect research.

### 16.1 Historical sportsbook archive

**Primary target:** The Odds API historical NCAAF archive.

Purpose:
- reconstruct point-in-time market state
- test open-to-decision movement
- test book disagreement and convergence
- test key-number crossings
- test price movement at unchanged spread
- build historical information-state features

Required rule:
Historical snapshots are research evidence. They do not automatically prove exact sportsbook venue-open timestamps unless the provider's timestamp semantics support that claim.

Candidate research timestamps:
- open / first observed
- T-48h
- T-24h
- T-12h
- T-6h
- T-3h
- T-1h
- close

### 16.2 Prospective injury and market feed

**Candidate provider:** SportsDataIO.

Potential uses:
- prospective CFB injury status
- opening/current/closing lines
- normalized game and player identities
- corroboration of material availability changes

Constraint:
If the provider does not retain intraday historical injury-state changes, archive every prospective response in the evidence plane. A current injury feed cannot retroactively reconstruct what was known two days earlier.

Depth-chart authority remains official school/conference material unless a future provider-specific contract proves equivalent provenance.

### 16.3 Independent market and injury source

**Candidate provider:** OpticOdds.

Potential uses:
- historical price-change events
- lock/unlock events
- settlement events
- independent sportsbook-line corroboration
- prospective injury context

Recommended role:
Use as a second market source rather than silently replacing the incumbent. If two authoritative feeds disagree materially for the same book/game/time, emit:

`MARKET_SOURCE_CONFLICT`

and fail closed for affected research rows until reconciled.

### 16.4 Enterprise sports-data layer

**Candidate provider:** Sportradar NCAA Football.

Potential uses:
- schedules
- rosters
- box scores
- play-by-play
- player participation
- betting-split research
- AI-agent/MCP integration where contractually available

Betting splits are shadow information-state inputs only at first. Ticket and money percentages must not become directional authority without prospective incremental evidence after controlling for line movement.

Candidate shadow fields:
- ticketShareDiff
- moneyShareDiff
- moneyMinusTickets
- splitChangeSinceOpen

### 16.5 Weather forecast-run history

**Provider:** Open-Meteo.

Current prospective forecast capture remains authoritative for `windMph`.

Enhance the weather shadow layer with:
- windMph
- windGustMph
- precipitationProbability
- precipitationAmount
- temperatureF
- forecastLeadHours
- windForecastChange24h
- precipitationForecastChange24h

Historical evaluation must use archived forecast runs or previous-run products, never realized weather or reanalysis as a substitute for the forecast available at decision time.

### 16.6 Injury-impact model

Do not model injuries as a raw count.

Build player-impact context from prior participation and usage.

Candidate usage weights:
- QB: pass-attempt share / snap share where reliable
- RB: rush + target opportunity share
- WR / TE: target share
- OL: starts / snaps where available
- defense: snap, tackle or pressure participation where reliable

Future shadow concept:

`teamAvailabilityImpact = sum(player_usage_weight * availability_effect)`

Then:

`availabilityImpactDiff = home - away`

This remains shadow-only until source coverage, missingness and feature math are frozen prospectively.

### 16.7 Preseason structural priors

Early-season uncertainty should be handled with explicit priors instead of forcing sparse current-season data to carry all the weight.

Candidate preseason inputs:
- returning production
- returning QB
- returning offensive-line starts
- transfer additions / losses
- recruiting / talent composite
- head-coach continuity
- coordinator continuity
- scheme changes
- prior-season EPA / PPA
- preseason market win total

Recommended architecture:
Use preseason priors with declining influence as current-season evidence accumulates. Do not tune decay weights after observing the evaluation cohort.

### 16.8 Information state becomes first-class

Create a separate point-in-time `informationState` contract rather than mixing market behavior into football quality.

At each canonical decision timestamp, preserve:
- open line
- current line
- open price
- current price
- open-to-decision line move
- price-only move at unchanged line
- fresh-book count
- cross-book spread dispersion
- cross-book price dispersion
- Pinnacle / reference-book deviation
- key-number crossings
- time since latest market move
- movement velocity
- movement acceleration
- verified material QB/injury news since open
- material weather-forecast change since open
- minutes to kickoff

Closing information is never allowed in an earlier information-state row.

The research question becomes:

> Given the football information available at decision time, has the market already incorporated it?

That is distinct from trying to predict games directly better than the close.

### 16.9 Bitemporal analytical warehouse

**Preferred analytical mirror:** Neon Postgres.

GitHub remains the immutable evidence authority.

Neon is a derived query layer only.

Recommended record shape:
- canonical_game_id
- feature_name
- feature_value
- observed_at
- valid_at
- decision_time
- source
- source_sha256
- parser_version
- feature_contract_version

Required property:
The system must be able to answer:

> What exactly did CFB Edge know about this game at timestamp X?

No manually edited database row may outrank the immutable GitHub evidence it derives from.

### 16.10 Research-discovery redundancy

Current roles:

**Exa**
- deep primary-source discovery
- literature / provider research
- ambiguous-source resolution
- never feature-authoritative by itself

**Firecrawl**
- page/PDF capture
- monitoring and diffs
- JS-heavy page extraction
- raw-source preservation
- LLM extraction remains candidate evidence until deterministically validated

**Tavily, optional**
- independent web-search / extraction path
- useful as a second discovery engine when Exa misses a primary source
- discovery-only unless the located primary source is separately captured and validated

Search engines identify evidence. They are not the evidence.

### 16.11 Provider-consensus and evidence-confidence layer

Critical facts should be reconciled across independent sources where practical.

Example QB evidence:
- official team source
- structured injury provider
- market/injury corroboration
- prior participation data

Store an evidence-quality property separately from the predictive feature.

Suggested states:
- HIGH: official source + deterministic player mapping + PIT participation valid
- MEDIUM: official source with unresolved OR / ambiguity
- LOW: media / secondary reporting only
- BLOCKED: material source conflict

Evidence confidence is initially an operational quality property, not a model feature.

### 16.12 Sources and patterns not to add by default

Do not expand the model with:
- generic social sentiment
- LLM-generated team power ratings
- expert-pick consensus
- handicapping trend libraries such as ATS streak rules
- unbounded technical betting indicators
- opaque black-box model scores without PIT source lineage

These increase researcher degrees of freedom faster than they increase trustworthy signal.

## 17. Recommended end-state architecture

### Football state

Primary candidates:
- CollegeFootballData
- official school/conference sources
- prospective structured sports-data provider where validated
- Sportradar / equivalent enterprise feed where economically justified

Owns:
- efficiency
- participation
- QB continuity
- roster availability
- line play
- travel/rest
- preseason priors

### Market state

Primary candidates:
- The Odds API
- OpticOdds
- Pinnacle / executable sportsbook capture
- exchange data where contract semantics are verified

Owns:
- current executable prices
- historical snapshots
- line movement
- price movement
- book dispersion
- market timing
- close

### Forecast state

Primary:
- Open-Meteo prospective and archived forecast runs

Owns:
- wind
- gusts
- precipitation
- temperature
- forecast changes through time

### Discovery / capture state

- Exa: discovery
- Firecrawl: capture / monitoring
- Tavily: optional independent discovery

These never override primary-source or structured-feed provenance.

### Evidence and analytics

**GitHub**
- immutable raw evidence
- hashes
- source manifests
- model/config freezes
- audit artifacts
- tests and CI

**Neon**
- derived bitemporal analytical mirror
- fast PIT joins
- feature warehouse
- experiment queries

### Research models

**S04_BR2**
- football-context residual challenger
- remains `DATA_COLLECTION_ONLY`
- no stake, delivery or promotion authority

**S05 / information-state challenger**
- tests whether newly available information is already reflected in the market
- starts as shadow/context only

### Execution layer

The execution engine evaluates:
- current executable price
- fees / vig
- max playable price
- quote freshness
- book availability
- model uncertainty
- one-position-per-game rule

No predictive edge matters unless it survives actual execution economics.

## 18. API / plugin implementation priority

Recommended sequence:

1. Use the existing The Odds API integration to build a historical market-state archive.
2. Evaluate OpticOdds against The Odds API for sportsbook coverage, timestamp granularity and historical fidelity.
3. Evaluate SportsDataIO for prospective injury-state capture; begin archiving state changes immediately if adopted.
4. Evaluate Sportradar for player-participation depth, betting splits and agent/MCP economics.
5. Expand Open-Meteo to archived forecast-run replay and weather-change features.
6. Build player-usage-based injury impact as shadow evidence.
7. Add preseason structural priors with preregistered decay.
8. Make `informationState` a canonical timestamped dataset.
9. Add Neon as the derived bitemporal query layer while preserving GitHub evidence authority.
10. Add Tavily only as discovery redundancy, never as direct feature authority.
11. Freeze each new feature contract before outcomes are observed.
12. Require ablation testing against the market baseline and simpler BR2 variants before keeping any added feature.

Authority boundary remains unchanged:

- S02 delivery authority is unchanged.
- Frozen S04_ES2 rules are unchanged.
- S04_BR2 remains `DATA_COLLECTION_ONLY`.
- New providers do not create picks, stakes or promotion authority.
- Missing or conflicting source evidence fails closed.
