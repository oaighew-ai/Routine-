# BR2 point-in-time context architecture

This subsystem fills missing football-context inputs without changing the
frozen Week 5 S04_ES2 rule or creating a second picks authority.

## Evidence flow

1. **Exa discovers.** It is used to locate official athletic-department media
   centers, weekly game notes, depth charts and availability materials.
   Search results are never features.
2. **Firecrawl captures.** Official pages/PDFs are snapshotted and hashed.
   Firecrawl monitors discover changes. Browser rendering may capture visible
   public Action Network context. Locked content is treated as unavailable.
3. **GitHub freezes deterministic sources.** CFBD and Open-Meteo payloads are
   archived by SHA-256 with retrieval timestamps and source-health records.
4. **Validators decide admissibility.** A feature stays null unless its source
   contract passes. QB continuity requires official evidence for both teams.
5. **BR2 warehouse joins only audited values.** The warehouse remains
   collection-only until a separate feature freeze and untouched future
   chronological holdout are registered.

## Feature definitions

- `epaDiff`: home minus away opponent-adjusted net EPA. Preferred source is
  the prospectively frozen CFBD WEPA snapshot. When CFBD WEPA is unavailable,
  later prospective cohorts may use the separately versioned
  `CFB_EDGE_OA_EPA_V1` fallback: cfbfastR play-level EPA from completed prior
  weeks is fit with a fixed two-way regularized offense/opponent adjustment.
  The source contract, through-week cutoff, source-asset SHA-256 and canonical
  used-row archive are retained. PPA is never relabeled as EPA, and a later
  season aggregate never repairs an earlier cohort.
- `linePlayDiff`: home minus away net line yards, where team net is offensive
  `lineYards - defensive lineYards`. Stuff rate, power success and front-seven
  havoc are preserved as auxiliary evidence but are not blended into the
  feature until separately preregistered.
- `travelMilesDiff`: home minus away great-circle miles from each program's
  registered CFBD home location to the game's CFBD venue. This is a stable
  travel-burden proxy, not an itinerary estimate.
- `windMph`: Open-Meteo 10m sustained wind nearest kickoff from the forecast
  actually captured pregame. Confirmed domes are explicitly neutralized at 0.
  Historical evaluation must use archived forecast runs, not realized weather.
- `qbContinuityDiff`: home minus away incumbent-QB continuity. For each team,
  continuity is the selected QB's passing-attempt share over the team's last
  three completed games. The default incumbent is the passing-attempt leader
  in the most recent completed game. A deterministically validated official
  pregame starter may override that incumbent only when the player maps to
  captured prior participation. Missing participation remains null; official
  source ambiguity does not fabricate a replacement value.

## Web-source controls

Exa is discovery-only. Firecrawl/LLM extraction is candidate evidence until a
deterministic validator confirms the exact fact. Action Network is
corroboration-only. Public odds and line movement may be archived as market
context, but locked PRO fields remain unavailable and no Action projection is a
training label.

## Failure behavior

New context adapters fail closed to null. Failure of EPA, travel, weather,
line-play or QB context must not destroy the existing five-feature BR2 snapshot.
The source-health artifact differentiates provider/adapter failure from genuine
feature absence.

## Promotion boundary

None of these inputs may affect S02 or S04_ES2. BR2 remains
`DATA_COLLECTION_ONLY` until its feature contract is frozen in advance,
multiple independent prospective weeks are collected, a future chronological
holdout is reserved before model selection, and the challenger beats the market
baseline under the registered loss/economic gates.


## Prospective coverage repair

The completed 2026 Product Week 5 evidence remains immutable at its measured
coverage: QB continuity 1/57 and opponent-adjusted EPA 0/57. Those missing
values are not backfilled after outcomes.

Starting with `CFB_2026_PRODUCT_WEEK6`, the active BR2 collection pipeline
uses `CFB_EDGE_BR2_QB_CONTINUITY_V4` and, only when preferred CFBD WEPA is
unavailable, `CFB_EDGE_OA_EPA_V1`. Both remain collection-only and have no
S02/S04_ES2, staking, delivery or promotion effect.
