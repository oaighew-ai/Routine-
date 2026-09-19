# EDGE OS v2.0, derived

> **Read this first.** This is not EDGE_OS_v2.md. The real `spec/EDGE_OS_v2.md`,
> `spec/EDGE_OS_v1.md` and `spec/edge_engine.jsx` were never supplied to this
> repository, and `spec/BUILD_PROMPT.md` §2 says to stop when a source is
> missing. Under decision **D1** the owner chose to proceed on a derived spec
> instead. So every rule below is one of:
>
> - **CITED** — taken from BUILD_PROMPT §6, or measured in this repository and
>   cited to `MODEL.md`.
> - **PRIOR** — a shape or constant this document chose because the real spec
>   was unavailable. Law 6 requires the tag. A PRIOR is not evidence.
>
> When the real v2 arrives, this file is deleted, not merged, and every PRIOR is
> re-checked against it. Acceptance check 1 tests this engine against itself,
> not against `edge_engine.jsx`, and so proves internal consistency only.

Version: `derived-1`. The engine records the SHA-256 of this file as `specHash`
on every candidate row, so a row can always be traced to the rules that produced
it.

---

## Laws

- **Law 1.** Rows are frozen when written. A parameter change never rewrites
  history. CITED (BUILD_PROMPT §5).
- **Law 2.** Every aggregate is recomputed from rows at load. Nothing is ever
  incremented. CITED (§5).
- **Law 3.** Missing data is a reason code, never a guess. No number that lands
  in the ledger is estimated by a model. CITED (§4, appendix).
- **Law 4.** Decide on EV at the executable price, net of venue fees. The
  de-vigged consensus is a belief, not a price. CITED (§6.1).
- **Law 5. Gate once.** Stage A per signal, Stage B per bet, and bet only when
  `f_full > 0`. There is no separate per-pick edge threshold. CITED (§6.2).
- **Law 6.** No threshold without a derivation. Otherwise it is tagged PRIOR.
  CITED (appendix).

---

## 1. Baseline belief

`p_baseline` is the de-vigged consensus probability for the side under
consideration, computed from the median line and price across the listed US
books (`bookSet` records which). CITED (§7).

Two de-vig methods are computed on every two-way market:

- **proportional** — `cfb_edge.market.devig_multiplicative`
- **power** — `cfb_edge.market.devig_power`

`p_baseline` is the **proportional** value. The power value is carried alongside
so §6.10's sensitivity check can be run. PRIOR: the choice of proportional as
the reported baseline rather than power. `market.py` documents proportional as
the more biased of the two; it is used here because it is the industry default
and because §6.10 makes the disagreement between them a reason code rather than
a modelling choice, so the baseline's identity matters less than the gap.

---

## 2. Stage A: is this signal evidence?

Run once per signal on a candidate.

### 2.1 Pooled record (A1, CITED)

```
n      = nProvider + nOwn
wins   = winsProvider + winsOwn
p_hat  = wins / n
```

There is no replacement of the provider record at own n = 30. That is the whole
of A1, and acceptance check 6 asserts the resulting curve is continuous there.

### 2.2 Evidence floor

Stage A never uses `p_hat`. It uses a lower confidence bound, so a 3-1 record
cannot outvote a 194-129 one.

`p_floor` is the **Wilson score interval** lower bound at 95%:

```
z      = 1.959963985
d      = 1 + z^2/n
centre = p_hat + z^2/(2n)
half   = z * sqrt( p_hat(1-p_hat)/n + z^2/(4n^2) )
p_floor = (centre - half) / d
```

PRIOR: the choice of Wilson over Agresti-Coull, Jeffreys or a normal
approximation. Wilson is chosen because it is well behaved at small n and at
p near 0 or 1, where the normal approximation returns bounds outside [0,1] and
would silently hand a tiny sample a usable floor.

Worked example, which is acceptance check 5: a provider system at 194-129,
n = 323, own n = 0 gives `p_hat = 0.600619` and `p_floor = 0.546327`. The
breakeven at -110 is 0.523810, so the floor clears it and the signal is
evidence.

### 2.3 Market-relative evidence (§6.4, CITED)

A win rate is only evidence relative to the prices the system was betting.

```
p_signal = p_nv + L
```

where `p_nv` is this pick's no-vig probability at entry and `L` is the lower 95%
bound of the system's mean excess `(win - p_nv)` over its own forward picks.

§6.4 also states this equals the `p_floor` form near even prices. The engine
therefore switches on the market:

- **Per-pick excess available** (the system has own forward rows carrying
  `p_nv` at entry): use `p_signal = p_nv + L`, with `L` the lower bound of the
  mean excess, estimated as `mean - z * sd / sqrt(n)`.
- **Aggregate record only, and the market is near even**: use
  `p_signal = p_floor`. "Near even" is `0.40 <= p_nv <= 0.60`. **PRIOR**: the
  0.40-0.60 band. It is chosen so that a -150/+130 market is outside it, since
  the breakeven-from-average-odds error §6.4 forbids grows with distance from
  even.
- **Aggregate record only, and the market is not near even**: the signal is
  **display only**. `w_sig = 0`, reason code `AGGREGATE_ONLY`, until own
  forward n >= 30. CITED (§6.4).

`L` is never derived from average odds. CITED (§6.4).

### 2.4 Weight

```
w_sig = flagWeight * n / (n + N_HALF)
```

**PRIOR**: the shrinkage shape and `N_HALF = 50`. The shape is chosen because it
is monotone in n, continuous (so A1 has no discontinuity at any n, which is
check 6), zero at n = 0, and asymptotes to `flagWeight`. `N_HALF = 50` means a
system carries half of its full weight at 50 pooled picks. The real v2's
flag-to-weight table would replace this whole subsection.

Default flag weights, all **PRIOR**:

| Flag | Family | Weight | Note |
|---|---|---|---|
| `sharp_money` | `sharp` | 1.00 | |
| `reverse_line_move` | `sharp` | 1.00 | §6.5 merges this into `sharp` |
| `line_move` | `sharp` | 0.80 | same family, so it is dropped when a stronger `sharp` signal fires |
| `steam` | `sharp` | 0.90 | |
| `model` | `model` | 1.00 | an owned model's own pick |
| `system` | `system` | 1.00 | a provider handicapping system |
| `situational` | `situational` | 0.60 | |
| `injury` | `news` | 0.70 | |
| `weather` | `news` | 0.50 | |

### 2.5 Stage A outcome

A signal passes Stage A when `w_sig > 0` and `p_signal` is defined. A signal
killed by A2 (§4) has `w_sig = 0` and does not pass.

---

## 3. Stage B: the posterior, and the decision

### 3.1 One family, one piece of evidence (§6.5, CITED)

Group the passing signals by `family` and keep only the largest `w_sig` in each.
Everything dropped is recorded with reason code `FAMILY_DUP`, so the row shows
what was excluded and why.

### 3.2 Opposing signals (§6.6, CITED)

A signal fired on the other side of this market enters as `1 - p_signal`. It
keeps its own `w_sig`. If the decision then comes out `f_full <= 0` **and at
least one opposing signal contributed**, the decision is `NO_BET` with reason
`OPPOSED` rather than a plain `PASS`.

### 3.3 The blend

```
w_mod   = sum of w_sig over the kept signals
p_model = sum(w_sig * p_signal) / w_mod          (undefined when w_mod = 0)
p_post  = (w0 * p_baseline + w_mod * p_model) / (w0 + w_mod)
```

`w0` is the weight on the market baseline. **PRIOR: `w0 = 0.5`,
calibrated to acceptance check 5**, which is the only quantitative statement
BUILD_PROMPT makes about what the defaults must do: a provider system at 194-129
with own n = 0 has to produce a BET row at -110. That pins `w0` given the rest
of the machinery, because `p_floor(194, 323) = 0.546327` against a baseline of
0.5 clears the -110 breakeven of 0.523810 only when the evidence outweighs the
market by more than 1.057:1, and `w_sig` at n = 323 is 0.866. `w0 = 1.0` returns
-0.44% EV on that row and fails the check; `w0 = 0.5` returns +1.06% and a 0.25u
stake.

The resulting bar is high, which is the point. Measured against the same
baseline at -110: 33-24 and 3-1 both push the posterior *down*, because their
Wilson floors are below 0.5; 60-40 and 120-80 pass on negative EV; 500-400
(55.6% over 900 picks) still passes. Only a system with both a real edge and a
real sample bets. This is a calibration to one anchor point, not a measurement,
and it is the single most consequential PRIOR in this document.

When `w_mod = 0`, `p_post = p_baseline`, which always yields `f_full <= 0` at any
real price, so the row is a `PASS` with `NO_EVIDENCE`.

### 3.4 EV and Kelly at the executable price (Law 4, §6.1 CITED)

For an American or decimal price with net payout `b` per unit staked, after the
venue's fee rule:

```
ev     = p_post * b - (1 - p_post)
f_full = argmax over f of  p_post*log(1 + f*b) + (1 - p_post)*log(1 - f)
```

`f_full` is solved numerically by `cfb_edge.staking.full_kelly`, which is
push-aware. Spread markets on whole numbers carry real push mass and the closed
form is wrong for them; the same solver handles both cases, so there is one
implementation.

**Bet only if `f_full > 0`.** There is no second threshold (Law 5).

### 3.5 De-vig robustness (§6.10, CITED)

Run 3.3 and 3.4 twice, once with the proportional `p_baseline` and once with the
power one. If the resulting decisions differ, the row is a `PASS` with
`DEVIG_SENSITIVE`, whichever way round they came out.

### 3.6 `maxPlayablePrice` (§6.3, CITED)

Re-run Stage A and Stage B across a grid of prices and report the worst price at
which `f_full > 0` still holds. `p_post` is never inverted to produce it. The
grid is every American price from -400 to +400 in steps of 1, which resolves
better than a cent at every price a US book posts. PRIOR: the grid bounds and
step.

---

## 4. A2: the per-system CLV kill (CITED)

Computed from that system's own graded BET rows only.

- Below 60 own BET grades: monitoring only, no effect on `w_sig`. This is the
  Graveyard entry "CLV as a sizing control at small n" and it is why the rule has
  a floor at all.
- At n >= 60 own BET grades with mean `clvLagPct <= 0`: `w_sig = 0` and reason
  code `CLV_KILL`.
- If such a system also has positive ROI, add `LUCK_RISK`. A positive return on
  negative CLV is the signature of variance, not edge.

`sigma = 4%` is a **PRIOR**, to be recomputed from measured dispersion once own
n >= 30, per A2.

---

## 5. Sizing (§6.7, CITED)

```
stake_units = 0.25 * f_full * portfolioScale * bankroll / unit
```

with `unit = 0.01 * bankroll`, so `stake_units` is in units.

- `portfolioScale` is the correlation haircut across positions on the same
  slate. With `c` correlated positions and `rho = 0.3` (PRIOR, CITED as PRIOR in
  A2's sibling rule §6.7): `portfolioScale = 1 / sqrt(1 + rho * (c - 1))`.
  **PRIOR**: this functional form. It is the standard correlated-Kelly haircut
  shape, it equals 1 at `c = 1`, and it decays slowly rather than cliffing.
- `c` counts positions already logged on the slate plus positions qualifying in
  this window, and is logged on every row. CITED.
- Round **down** to 0.05u. Never up. CITED.
- Below the venue minimum: `PASS`, `SUB_MIN`. CITED.
- A 2.0u ceiling exists only as a bug guard. Every time it binds, the row gets
  `CEILING_HIT`. CITED.

---

## 6. CLV (A3, A4, A5)

### 6.1 Definition (A3, CITED)

```
clvPct = closeFairProb_at_entry_line * entryDecimal - 1
```

the bet's EV at the closing no-vig price. Close reference is Pinnacle where
present, else the US median, recorded as `closeRef`.

**Reconciliation with this repository's existing CLV, which A3 requires before
grading anything.** `cfb_edge.clv.LoggedBet.price_clv` computes
`closeFairProb - 1/entryDecimal`. Multiplying by the decimal odds:

```
price_clv * d = closeFairProb * d - 1 = clvPct        exactly
```

So `clvPct = price_clv * entryDecimal`. The two are not interchangeable: at -110
they differ by a factor of 1.909, so grading A5 on `price_clv` would understate
the gate by nearly half. `tests/test_engine.py` asserts this identity against the
existing implementation, which is what keeps the two definitions from drifting
apart.

The identity holds **method for method**, and the two sides do not currently use
the same method: `cfb_edge.clv` de-vigs the close with Shin, and this engine uses
proportional, matching `p_baseline`. Measured on a -108/-112 close the fair
probabilities differ by 0.0002, worth 0.04 percentage points of `clvPct`. That is
small and it is one-directional across a whole ledger, so the engine pins
proportional explicitly rather than inheriting a default. The test fixes the
method on both sides and holds the identity to 1e-12; a separate assertion records
the cross-method gap so it cannot grow unnoticed.

Three further rules, all CITED:

- Spreads across numbers convert through the margin PMF and are tagged `pmf`.
  Here the PMF is `cfb_edge.distribution.margin_pmf`, whose key-number bumps are
  **measured**, not PRIOR: fitted on 14,687 FBS games 2004-2024 and era-tested
  across five five-year windows (`MODEL.md`, "Where the constants came from" and
  "Is the key-number structure drifting?"). This is the one place where this
  repository is better evidenced than A3 assumed.
- Totals use `cfb_edge.distribution.total_pmf` and never the margin PMF. That
  function carries no key-number bumps at all, so acceptance check 10 holds by
  construction.
- `rawMove` (the raw line and price move) is stored for comparison with old
  records and never used for a gate.

### 6.2 Execution lag (A4, CITED)

Every BET row is also graded at the price 30 minutes after `loggedAt`, as
`clvLagPct`. 30 minutes is a PRIOR, replaced by measured lag once live.

### 6.3 Gate 2 (A5, CITED, with one addition)

Pooled BET rows, on `clvLagPct`, 95% CI with standard errors clustered by slate
date, lower bound > 0, n >= 60 minimum. It must pass with and without `pmf`
rows. Live capital goes only to sports whose own mean CLV is positive.

**Addition (D5).** The gate also reports and requires a **cluster count**, not
only a row count. A5 sets its minimum in rows while clustering by slate date,
and this repository has already paid for that distinction once: `bootstrap.py`
and `MODEL.md` record a naive interval of `+0.303c [+0.187, +0.419]` against a
clustered one of `+0.303c [-0.091, +0.716]` on the same data, 3.5x too narrow,
promoting a signal the honest interval cannot separate from zero. Sixty BET rows
spread over a dozen Saturdays is a dozen clusters. The engine therefore carries
`MIN_CLUSTERS = 12` (**PRIOR**) alongside the cited `n >= 60`, and the board
prints both. Raising the row minimum is not a substitute: rows on one slate are
not independent.

Clustered intervals come from `cfb_edge.bootstrap.cluster_bootstrap`, resampling
whole slate dates.

---

## 7. Provenance, which this spec adds (D6)

BUILD_PROMPT's candidate schema has no field for where a price came from.
`cfb_edge/clv.py` has carried one since before this build, with four values and
a rule that only two of them can be graded:

| `priceSource` | Gradeable | Meaning |
|---|---|---|
| `capture` | yes | read from a capture log, stamped when seen |
| `fill` | yes | the number a real order filled at |
| `late` | **no** | captured honestly, but after the number stopped being new |
| `unverified` | **no** | no timestamp, no audit trail |

`late` is the subtle one and the reason the field exists. A line first seen on
the morning of the game is a real observation with a real timestamp and is not
an opening line; graded as one it does not look like a fabrication, it looks like
the strategy underperforming, which is worse. §6.11's look-ahead rule only
catches the opposite error. Candidates carry `priceSource`, and a row that is not
gradeable is graded for diagnostics but excluded from Gate 2 with reason
`UNGRADEABLE_PRICE`.

---

## 8. Reason codes

Every code the engine can emit, and what it means.

| Code | Emitted when |
|---|---|
| `NO_EVIDENCE` | no signal passed Stage A |
| `AGGREGATE_ONLY` | provider aggregates only, market not near even, own n < 30 (§6.4) |
| `CLV_KILL` | A2 killed this system at n >= 60 own BET grades |
| `LUCK_RISK` | killed by A2 while carrying positive ROI |
| `FAMILY_DUP` | dropped as the weaker signal in its family (§6.5) |
| `OPPOSED` | `f_full <= 0` with an opposing signal contributing (§6.6) |
| `NEG_EV` | `f_full <= 0` at the executable price (Law 5) |
| `DEVIG_SENSITIVE` | proportional and power de-vig disagree on the decision (§6.10) |
| `SUB_MIN` | stake below the venue minimum (§6.7) |
| `CEILING_HIT` | the 2.0u bug guard bound (§6.7) |
| `STALE` | a source exceeded its max age (§6.9) |
| `UNMAPPED` | a team name did not resolve, with no fuzzy fallback (§6.9) |
| `DEVIG_IMPLAUSIBLE` | both-side implied sum outside the configured band (§6.9) |
| `BOOK_DISAGREE` | books disagree beyond the configured band (§6.9) |
| `LOOKAHEAD` | no snapshot at or after `timeSeen` (§6.11) |
| `UNGRADEABLE_PRICE` | `priceSource` is `late` or `unverified` (§7 above) |
| `DOMINATED` | a better EV-per-unit-risked position exists on this game and side (§6.8) |

---

## 8a. Signal sources, as measured

§7 gates a CFB scoresandodds parser on Phase 0 confirming the page. Confirmed,
at `/ncaaf/consensus-picks` and not `/ncaaf/consensus`; the selectors are
recorded in `DECISIONS.md` D7 with the counts they were measured from. No parser
exists yet, and the one that gets written keys on `data-event` and
`.percentage-a` / `.percentage-b` rather than on anything inferred from how the
page looks in a browser.

Odds coverage is **measured** (D9). On CFB every listed venue quotes all three
markets, Kalshi included, so acceptance check 16 passes.

**This system is college football only** (D10). The NBA gaps D9 found — Kalshi
quoting moneyline alone, Pinnacle absent entirely — are recorded there and are
not live questions. They become live again the day NBA returns, and the matrix
should be re-measured then rather than read off a September snapshot.

The plan is the **free tier, 500 credits a month** (D8). Billing is
`regions x markets`, measured at 3 credits for a three-market pull, which makes
SHADOW scanning affordable and the opening-line capture impossible. Under D10's
CFB-only scope a full 15-week season of scans costs 180 credits and fits; the
capture still wants about 2,800 a month stripped to spreads alone, 5.6x the
allowance. Scope cuts do not reach it, because the cost is in the polling
frequency and the frequency is the point: the measured edge runs from the
*open*, and an open is only an open if something was watching when it appeared.
CFBD supplies `spreadOpen` and `overUnderOpen` on every quote, free and on a
separate quota (D12), which may make the polling unnecessary for the
line-based measurement `MODEL.md` validated. It supplies no juice on a spread
or a total, so it cannot produce `p_baseline` or A3's `clvPct` for those
markets, and it carries no timestamp, so under D6 it is a reference to measure
an entry against and never the entry itself.

## 9. What this document does not contain

The real EDGE_OS_v2 has a flag-to-weight table, a posterior blend and a
portfolio-Kelly rule of its own. §2.4, §3.3 and §5 above are this document's
reconstruction of them and nothing more. They are the weakest part of the build
and are the first thing to discard when the real spec arrives.
