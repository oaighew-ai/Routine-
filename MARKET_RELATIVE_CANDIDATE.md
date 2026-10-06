# S06_MR1 market-relative challenger

S06_MR1 is a frozen, point-in-time ridge residual challenger. It predicts a
correction to the market-implied home spread margin:

`target = actualHomeMargin - marketHomeMargin`

Its estimate is added to the recorded market margin. It cannot create a pick,
cover probability, price, stake, or delivery card. The private Site remains the
only picks authority, and the current authority remains unchanged.

## Frozen contract

The complete freeze is [config/s06_market_relative.json](./config/s06_market_relative.json).
It fixes the nine existing BR2 features, identity transforms after
training-window standardization, ridge regression, and alpha 10.0. Alpha is
inherited as an explicit PRIOR from the existing S04 specification; it is not
tuned using walk-forward outcomes. Any change to features, transforms,
estimator, alpha, or the start time requires a new freeze/version.

The prospective window starts at `2026-10-06T03:00:00Z`. Earlier rows can only
train the model after their own outcomes settle and after all input timestamps
and immutable source-manifest hashes pass validation. Only forecasts at or
after the frozen start time count as evaluation predictions.

## Point-in-time row contract

Run `python -m cfb_edge.market_relative --data <csv> --archive-root <evidence-root> --out <report.json>`.
The CSV needs one row per game and these base columns:

`gameId,season,week,marketHomeMargin,actualHomeMargin,marketAsOf,marketSourcePath,marketSourceSha256,marketManifestPath,marketManifestSha256,forecastAt,kickoffAt,settledAt,resultSourcePath,resultSourceSha256,resultManifestPath,resultManifestSha256`

Each feature listed in the freeze additionally requires its value,
`<feature>AsOf`, `<feature>SourcePath`, `<feature>SourceSha256`,
`<feature>ManifestPath`, and `<feature>ManifestSha256`. Manifests are version-1
JSON with a timezone-aware `observedAt` equal to the row source timestamp and a
non-empty `files` list of archive-relative paths and SHA-256 digests. The runner
verifies each manifest's hash and re-hashes every listed file inside the
evidence archive; all source paths must be listed by the
corresponding manifest. All timestamps must include a timezone; feature and
market timestamps must be no later than `forecastAt`, `forecastAt` must precede
kickoff, and the result settlement time cannot precede kickoff. Rows with
missing, invalid, duplicate, out-of-archive, mismatched, or future-dated inputs
stop the run with a line-specific error instead of receiving a synthetic value.

For each exact forecast timestamp, training includes only rows whose
`settledAt` is strictly earlier. All games forecast at the same timestamp are
predicted together. The report includes data, freeze, and implementation SHA-256
hashes, training cutoffs, prediction timestamps, and market/challenger margin
MAE and RMSE.

## What completion means

The 500-row training floor and the reporting minima of 200 predictions across
eight kickoff weeks are frozen operational minima, not evidence of an edge.
The reporting minima reuse the current S02 sample/week contract; their
sufficiency for a different model remains unproven. Even a completed report
requires independent review and later prospective assessment. The report always
sets `deliveryEligible=false`, `promotionEffect=NONE`, and `stakeUnits=0`.

No verified timestamped multi-season feature-and-market dataset is present in
this checkout. Therefore S06_MR1 is created and frozen but untrained until such
rows are supplied. A run with too few prior settled rows reports that explicit
status; it does not claim performance or readiness.
