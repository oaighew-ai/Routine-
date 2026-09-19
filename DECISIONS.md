# Decisions

Append-only. Dated. Every entry carries a reversal criterion, because a decision
with no way to be wrong is a preference.

Amendments A1 to A5 come from `spec/BUILD_PROMPT.md` §6 and are recorded here
because that document requires an entry before any code implements them.

---

## 2026-09-18 — D1. Build on a derived spec

**Decision.** `spec/EDGE_OS_v2.md`, `spec/EDGE_OS_v1.md` and
`spec/edge_engine.jsx` do not exist in this repository and were never supplied.
BUILD_PROMPT §2 says to stop and list what is missing. The missing sources were
listed in the Phase 0 report; the owner chose to proceed on
`spec/EDGE_OS_v2_DERIVED.md`, written from `MODEL.md` plus BUILD_PROMPT, with
every uncited constant tagged PRIOR.

**Consequence, stated plainly.** Acceptance check 1 asks the engine to match
`edge_engine.jsx` on ten golden vectors. There is no jsx, so check 1 becomes a
self-consistency test: the golden vectors are generated from this engine and
frozen, which detects drift but cannot detect that the engine was wrong from the
start. Checks 5 and 6 test the derived Stage A, so they verify this
reconstruction rather than the specification.

**Reversal criterion.** The real v1/v2/jsx arrive. The derived spec is then
deleted rather than merged, every PRIOR is re-checked against the real text, and
the golden vectors are regenerated from the jsx.

## 2026-09-18 — D2. Python stays the single engine

**Decision.** BUILD_PROMPT §4 specifies one implementation per formula as an ES
module in `engine/`, inlined into the dashboard, with parity tests where the
existing backtest computes the same quantity in Python. This repository is
13,610 lines of standard-library-only Python with 172 passing tests, a CI check
that forbids third-party imports, and a margin PMF fitted on 14,687 games. The
engine lives in `cfb_edge/engine/` as Python. `npm run board` becomes a Python
entry point.

**Reason.** §4's own parity-test clause exists to police having two
implementations. Not creating the second one satisfies the clause's purpose at
zero cost. Porting instead would duplicate every measured constant and its test.

**Consequence.** §9's "the page computes all aggregates from ledger rows with the
same engine module" becomes "the page displays aggregates the engine computed
from ledger rows". The aggregates are still recomputed from rows on every build,
so Law 2 holds; what changes is where the arithmetic runs.

**Reversal criterion.** A dashboard requirement that genuinely needs client-side
recomputation, such as a what-if control. §9 explicitly forbids what-if controls
on that page, so this is not expected.

## 2026-09-18 — D3. One repository, ledger on a data branch

**Decision.** `edge-os` and `edge-os-ledger` do not exist and are not in this
session's scope. Code lives in `oaighew-ai/routine-`; ledger rows are written
under `ledger/` on the existing `capture-data` branch, which `capture.yml`
already uses as an append-only data branch.

**Reason.** The separation §4 wants is between code that changes by pull request
and data that automation appends to. A protected `main` and an unprotected data
branch gives that, without a cross-repo clone in every routine.

**Reversal criterion.** The owner creates the two repositories. Migration is
`git filter-repo` over `ledger/` plus a remote change, not a rewrite.

## 2026-09-18 — D4. Phase 0 items 2 to 4 answered by CI, not by a session

**Decision.** `api.the-odds-api.com`, `api.elections.kalshi.com` and
`www.scoresandodds.com` are all refused by this container's egress proxy
(`connect_rejected`), and no `ODDS_API_KEY` is set here. The plan tier and credit
burn, the venue-by-sport-by-market coverage matrix and the scoresandodds parse
check are therefore produced by `.github/workflows/phase0-coverage.yml` on a
GitHub-hosted runner.

**Reason.** `kalshi-truth.yml` exists in this repository for exactly this reason
and records the cost of not doing it: a parser written against a documented shape
that had never seen a real response was wrong in three ways at once, and because
the request itself succeeded, the capture reported no error and parsed zero
markets.

**Reversal criterion.** A session with egress to those hosts, or a supplied
capture of a real response.

## 2026-09-18 — D5. Gate 2 gates on clusters as well as rows

**Decision.** A5 clusters standard errors by slate date but states its minimum in
BET rows (`n >= 60`). The engine additionally requires and reports a cluster
count, `MIN_CLUSTERS = 12` (PRIOR).

**Evidence.** `bootstrap.py` and `MODEL.md` ("Measuring whether any of it is
real") record this repository making the same mistake and measuring the cost:
naive `+0.303c [+0.187, +0.419]` promotes; clustered by game
`+0.303c [-0.091, +0.716]` does not. Same data, same point estimate, an interval
3.5x too narrow. `bootstrap.py`'s own docstring states the general rule: a
promotion rule counting contracts rather than games is set at a fraction of its
intended bar. Slate-date clusters are coarser than game clusters, so sixty rows
across a dozen Saturdays is a dozen independent observations, not sixty.

**Reversal criterion.** Measured intra-slate correlation near zero, which would
make rows and clusters interchangeable. `bootstrap.inflation_factor` measures it
directly; a factor near 1.0 over at least 30 clusters retires this entry.

## 2026-09-18 — D6. Candidates carry price provenance

**Decision.** Add `priceSource` to the candidate schema, with the four values
`cfb_edge/clv.py` already uses, and exclude non-gradeable rows from Gate 2 with
reason `UNGRADEABLE_PRICE`.

**Reason.** BUILD_PROMPT's schema has no such field. §6.11 forbids a price from
before `timeSeen`, which catches look-ahead but not lateness. `clv.py`'s `LATE`
docstring is the argument: the strategy is priced entirely on movement that has
not happened yet, a board first observed a day before kickoff has already spent
most of it, and on the week 3 2026 board pricing a late observation as an open
overstated every play by about 4.5x. Graded as an open, such a row reads as the
strategy underperforming rather than as a measurement error.

**Reversal criterion.** None expected. Retire only if every price in the ledger
is provably captured, which the field itself is what proves.

## 2026-09-19 — D7. The CFB consensus page exists, at a different path

**Decision.** Phase 0 item 4 is answered. §7 allows a scoresandodds parser for
CFB "only after Phase 0 confirms the page". It is confirmed, and the path in the
prompt is wrong.

**Evidence**, from three `phase0-coverage` runs (runs 1-3 on
`claude/jolly-feynman-p2rb1h`):

| path | HTTP | bytes | % strings |
|---|---|---|---|
| `/ncaaf/consensus` | 404 | 0 | 0 |
| `/ncaaf/consensus-picks` | **200** | 1,002,399 | **2,647** |
| `/ncaaf` | 200 | 1,015,763 | 664 |
| `/nba/consensus` | 404 | 0 | 0 |
| `/nba/consensus-picks` | **200** | 129,027 | 199 |

The first run probed only `/ncaaf/consensus`, got a 404, and concluded nothing
useful. A single 404 cannot separate "CFB has no consensus page" from "the path
is wrong" from "the site is down". The second run added an NBA control and
found the control 404 as well, which was the tell: the guessed *path shape* was
wrong for both sports, not the site.

**The page's real structure**, counted rather than assumed:

- `data-role` x728, `data-event` x219, `data-market` x219, `data-value` x219,
  `data-parity` x219
- `.consensus` x438, `.team-name` x438, `.percentage-a` x430,
  `.percentage-b` x430, `.trend-graph-percentage` x430, `.game-odds` x418

219 events, 438 team names (two per event), and ~430 percentage pairs. That is
a parseable board, and the selectors above are measured, not guessed.

**Why this is recorded rather than acted on.** No parser is written yet. §7 says
scoresandodds is parsed by a script and never read by the model, and
`GRAVEYARD.md` carries the cost of the opposite habit: `kalshi-truth.yml` exists
because a parser written against a documented shape it had never seen was wrong
in three ways at once while reporting no error. The next person writing this
parser keys on `data-event` and `.percentage-a` / `.percentage-b`, and adds the
chosen selector to this entry.

**Reversal criterion.** The site changes its markup. The probe step is kept in
`phase0-coverage.yml` precisely so a future run reports that rather than a
parser silently returning zero rows.

## 2026-09-19 — D8. Phase 0 items 2 and 3, answered

**Superseded the same day.** This entry first recorded that the API key was
rejected with a 401 and that items 2 and 3 were therefore open. A working key
was supplied and the coverage workflow re-run; what follows is the measurement.

**Item 2, the plan and the burn.** `x-requests-used` 171 plus
`x-requests-remaining` 329 is a **500-credit monthly allowance: the free tier**.
The key sees 82 sports. Historical odds, which A3 needs for closes and A4 for
lag prices, are paid-plan only.

**The billing rule, measured rather than reasoned about.** Credits are
`regions x markets`. A pull of 7 bookmakers across `h2h,spreads,totals`
reported `x-requests-last` of **3**.

This corrects a claim this repository made twice, in
`cfb_edge/providers/oddsapi.py` and in the first version of this entry: that
requesting by `bookmakers` "cuts it to a third". Naming books collapses only the
region factor to 1. The market factor is untouched, and the EDGE OS scan asks
for three markets where the old capture asked for one, so the two cost the same
3 credits per pull. The saving is real only at a constant market count. The
client's comment now says to read `x-requests-last` rather than infer the price
from the parameter used.

**What the free tier can and cannot do**, at 3 credits a pull:

| workload | cost | verdict |
|---|---|---|
| one CFB scan window, one sport | 3 | fine |
| a CFB Saturday, four windows | 12 | fine |
| a 15-week CFB season of scans | 180 | fits inside one month's 500 |
| `watch.py`'s 10-minute capture schedule | ~8,400/month | **16.8x the entire allowance** |

So SHADOW scanning is affordable on this plan and **the opening-line capture is
not**. That matters more than it looks: `MODEL.md` measures the edge from the
*opening* number, and `clv.py`'s `LATE` provenance exists because a board first
seen late has already spent most of the move. A plan that cannot afford opens
cannot feed the strategy the repository actually measured.

`credit_cap_monthly` is set to 500 in `config/edge_os.json`, and `fetch_pull`
raises `CreditCapReached` rather than quietly spending past it.

**Reversal criterion.** A paid tier. Re-run the workflow and this entry is
rewritten from the new headers, not edited by hand.

## 2026-09-19 — D9. The coverage matrix, and two venue gaps it exposes

**Acceptance check 16 passes for CFB.** Kalshi's prices reach `bookSet` on live
events, in all three markets.

**americanfootball_ncaaf — 90 events**

| venue | h2h | spreads | totals |
|---|---|---|---|
| `kalshi` | yes | yes | yes |
| `pinnacle` | yes | yes | yes |
| `draftkings` | yes | yes | yes |
| `fanduel` | yes | yes | yes |
| `betmgm` | yes | yes | yes |
| `betrivers` | yes | yes | yes |

**basketball_nba — 41 events**

| venue | h2h | spreads | totals |
|---|---|---|---|
| `kalshi` | yes | **no** | **no** |
| `pinnacle` | **not listed** | | |
| `draftkings` | yes | yes | yes |
| `fanduel` | yes | yes | yes |
| `betmgm` | yes | yes | yes |
| `betrivers` | yes | yes | yes |

Three findings, in order of what they cost:

1. **Kalshi lists no NBA spreads or totals, only moneyline.** §7 plans NBA from
   opening night, and on the one venue that can be bet and withdrawn from, two
   of the three markets do not exist there. An NBA spread signal has nowhere to
   be executed. Either NBA is a moneyline-only sport for this system, or it
   needs a second venue, and that is a decision rather than a detail.
2. **Pinnacle is absent from NBA entirely.** A3 names Pinnacle as the close
   reference and the US median as the fallback; on NBA the fallback is the only
   option, and `closeRef` will read `us_median` on every NBA row. That is
   already handled, but it means NBA closes are measured against a softer
   reference than CFB ones, and the two are not strictly comparable.
3. **`williamhill_us` is listed for neither sport.** A dead key, removed from
   the workflow's defaults. It cost nothing, but a key that never returns data
   is indistinguishable from a venue that dropped a market unless someone looks.

**Reversal criterion.** Re-run the workflow. The matrix is a snapshot of one
pull; venues add and drop markets, and Kalshi's NBA coverage in particular is
worth re-checking at opening night before any NBA rule is written around it.

## 2026-09-19 — D10. College football only

**Decision.** NBA is out of scope. `SPORTS` in BUILD_PROMPT reads "ncaaf now;
nba from opening night; mlb reuse only"; the owner has cut it to CFB alone.

**What it retires.** D9's two NBA findings stop being decisions: Kalshi listing
NBA moneyline only, and Pinnacle being absent from NBA so every NBA `closeRef`
would fall back to the US median. Neither needs answering now. They stay in D9
as measurements, because they will be true again the day NBA comes back.

**What it buys.** Every Odds API pull halves, one sport instead of two. Against
the 317 credits remaining on the free tier:

| scan markets | credits/pull | a 15-week season, 4 windows a week |
|---|---|---|
| `h2h,spreads,totals` | 3 | 180 |
| `spreads,totals` | 2 | 120 |
| `spreads` only | 1 | 60 |

**So SHADOW scanning now fits the free tier**, at any of those market counts,
with room. That was not true an hour ago.

**What it does not fix.** The opening-line capture. `watch.py`'s schedule costs
about 2,800 credits a month stripped all the way down to spreads alone, still
5.6x the allowance. Scope cuts and market cuts do not reach it, because the
cost is in the polling frequency, and the frequency is the point: the edge
`MODEL.md` measures runs from the open, and an open is only an open if
something was watching when it appeared.

That is the problem D11 is meant to test a way out of.

**Reversal criterion.** The owner puts NBA back. Re-run `phase0-coverage` with
both sport keys first: D9's NBA matrix is a snapshot and Kalshi's coverage is
worth re-checking at opening night rather than assumed from September.

## 2026-09-19 — D11. Test CFBD before building on it

**Decision.** Probe CollegeFootballData to find out whether it can carry the
market reference, and build nothing until it has answered.
`.github/workflows/cfbd-probe.yml` does the asking; it needs `CFBD_API_KEY` as
a repository secret.

**Why it is worth asking.** The capture problem above has exactly two exits: a
paid Odds API tier, or a different source for the market reference. CFBD is
college-football-only, which made it a half-answer while NBA was in scope and
makes it a whole one under D10. The repository already trusts the same family
of source: `cfb_edge/slate.py` builds slates from cfbfastR, and `teams.py`'s
entire alias table is keyed to cfbfastR spellings.

If CFBD carries opens and closes, the Odds API is needed only for the
executable venue price at the moment of decision — a small fraction of current
spend, because you price the venue when you are about to bet, not across the
whole board every ten minutes.

**The provenance question, which decides how far this can go.** A CFBD line is
a third-party record that a line existed. It is stronger than the screenshot
`clv.py` refuses to grade and weaker than this system's own capture. The
distinction that matters is what the number is used *for*:

- **As a close reference: defensible.** The question is where the market ended,
  and a timestamped third-party record answers it.
- **As an entry price: not defensible.** The claim would be that you could have
  taken a number you never saw. That is precisely the error `priceSource`
  exists to prevent (D6), and dressing it in an API response does not change it.

So even the best possible probe result does not license CFBD openers as entry
prices. It licenses them as a reference the entry is measured against.

**Nothing is assumed about the response.** `kalshi-truth.yml` is in this
repository because a parser written against a documented shape it had never
seen was wrong in three ways at once and reported no error while parsing zero
markets. The probe prints field names and counts and builds nothing.

**Reversal criterion.** The probe answers. A no is recorded and the paid tier
becomes the only exit; a yes gets its own entry naming the provider and the
provenance value before any client is written.

## 2026-09-19 — D12. CFBD carries the line, not its price

**The probe answered** (`cfbd-probe.yml`, run 2). Auth works, quota is
`x-calllimit-remaining: 897` on a limit near 1,000, and it is **a separate
budget from the Odds API entirely**.

`GET /lines?year=2026&week=3`: 119 games, 194 provider quotes. Every quote
carries the same eight fields, all 194 of 194:

`provider`, `spread`, `formattedSpread`, **`spreadOpen`**, `overUnder`,
**`overUnderOpen`**, `homeMoneyline`, `awayMoneyline`

Providers: `DraftKings` x119, `Bovada` x74, and `Draft Kings` x1 — the same
book under two spellings, which is a data wart any client has to normalise.

### What this is worth, stated as a split rather than a yes

My own probe printed "CFBD can carry the market reference" and that was too
strong; the verdict logic has been corrected so a future run cannot repeat it.
**A line is not a price.** There is no juice on a spread or a total anywhere in
the response. Only moneylines carry both sides.

**It can feed the measurement this repository actually validated.** `MODEL.md`
measures +0.44 points of closing line value from the *opening* number, with
t-statistics from 3.2 to 5.5, and `clv.py`'s `line_clv` and `probability_clv`
both work in points and need no price at all. `spreadOpen` hands that over
directly, for free, for every listed game.

**It cannot feed A3's `clvPct` on a spread.** That is EV at the closing no-vig
price and needs a two-way price to de-vig. Nor can it produce `p_baseline` for
a spread, for the same reason: Law 4 decides on EV at the executable price, and
a line with no juice is not a price. Moneylines are the exception — those carry
both sides and de-vig normally.

**It cannot be a consensus.** §7 wants the median of the listed US books. Two
books, one of which covers 74 of 119 games, is not that.

### What it does to the capture problem

This is the part that matters. D8 and D10 leave the opening-line capture at
about 5.6x the free-tier allowance even stripped to spreads alone, and the cost
is irreducible because it lives in the polling frequency.

**`spreadOpen` may make the polling unnecessary for the line-based
measurement.** The reason `watch.py` polls every ten minutes is to be watching
when the number appears. If CFBD simply reports what the number opened at, the
edge `MODEL.md` validated can be measured without the capture existing.

That is a large claim and it is not yet established. What is established is
that the field exists and is populated on every quote.

### Provenance, which bounds all of the above

Confirmed: **no timestamp on any quote.** So the rule written in advance in D11
stands unchanged, and the split above does not soften it.

- **As a close or open *reference*: defensible.** The question is where the
  market was, and a third-party record answers it.
- **As an *entry price*: not defensible.** The claim would be that you could
  have taken a number you never saw. `priceSource` exists to prevent exactly
  that (D6), and an API response does not change it.

Concretely: a CFBD opener is a legitimate thing to measure an entry *against*,
and never a legitimate thing to record as the entry. Any client gets a
`priceSource` of its own — not `capture`, which is reserved for what this
system watched happen.

**What is not decided here.** Which provider to use, whether a two-book
reference is good enough to gate on, and whether the line-based CLV or A3's
price-based `clvPct` is the gate. Those need their own entry and probably their
own measurement; this one records what the API returns and what that forecloses.

**Reversal criterion.** CFBD adds spread prices, or adds more providers, or
adds timestamps. The probe is kept so a re-run reports that rather than a
client silently continuing to work from two books.

## 2026-09-19 — D13. The gate is line-based CLV, not A3's price-based clvPct

**Decision.** Gate 2 is measured in **points of line beaten**, through
`clv.py`'s `line_clv` and `probability_clv`, not through A3's
`clvPct = closeFairProb x entryDecimal - 1`.

**Reason, in the owner's words: that is what we measured.** `MODEL.md` records
+0.44 points of closing line value from the opening number, with t-statistics
from 3.2 to 5.5 across variants, on 6,398 real closing lines. A3's definition
is a different quantity that was never measured here, and D12 established that
the data source which makes the measurement affordable — CFBD's `spreadOpen` —
cannot feed it on a spread at all, because it carries no juice.

Choosing the measured quantity over the prescribed one is Law 6's spirit
applied to a definition rather than a threshold.

**What this changes.**

- **Gate 2 (A5)** counts `line_clv` or `probability_clv` on BET rows, clustered
  by slate date, with D5's cluster minimum. `probability_clv` is the additive
  form — half a point at 3 is worth far more than at 12, and converting through
  the margin PMF is what makes rows comparable — so the gate runs on that and
  reports points alongside.
- **A3 is not deleted.** `clvPct` stays computed and stored wherever a two-way
  price exists, which is every moneyline and every Odds API pull. It becomes a
  second reading rather than the gate. Keeping both is cheap and the comparison
  is itself evidence: if they disagree in sign at n, that is worth knowing.
- **A4's `clvLagPct`** gets the same treatment: a lagged *line*, with the
  price-based lag kept where a price exists.
- **The `pmf` tag stays meaningful.** A5 requires the gate to pass with and
  without cross-number rows, and `probability_clv` converts through the margin
  PMF on every row, so essentially all rows become `pmf`. The with/without
  split is therefore re-expressed as: rows where the line did not move (exact,
  no conversion) versus rows where it did.

**What it does not change.** The betting decision. Law 4 still decides on EV at
the executable price net of fees, and that still needs a real two-way price
from the venue. CFBD cannot price a bet; it can only say where the market was.
This decision is about the **scorecard**, not the trigger.

**Reversal criterion.** A measured disagreement. Both quantities are stored on
every row where both are computable, so at n >= 60 the two can be compared
directly. If the price-based reading clears while the line-based one does not,
or the reverse, that is a finding and this entry gets revisited on it.

---

## Amendments carried from BUILD_PROMPT §6

## 2026-09-18 — A1. Stage A pools provider and own forward records

`p_hat = (wins_provider + wins_own) / (n_provider + n_own)`, and `w_sig` uses the
pooled n. The spec's replacement at own n >= 30 is removed. Reason, as given: at
own n = 30 a system needs 21-9 to clear the floor at -110, so nearly every system
would switch itself off the day it reached 30. Implemented in
`cfb_edge/engine/evidence.py`. Acceptance check 6 asserts continuity at n = 30.

**Reversal criterion.** Measured divergence between provider and own forward win
rates on the same system large enough that pooling is averaging two different
things: a two-proportion test rejecting equality at 95% with own n >= 60.

## 2026-09-18 — A2. Per-system CLV kill

Monitoring only below 60 own BET grades. At n >= 60, mean CLV <= 0 sets
`w_sig = 0` (`CLV_KILL`); positive ROI alongside adds `LUCK_RISK`. The stated
derivation: sigma = 4% (PRIOR), true mean +1.5%, false-kill rate about 0.2% at
n = 60. Implemented in `cfb_edge/engine/evidence.py`.

**Reversal criterion.** Recompute with measured sigma once own n >= 30, as A2
itself requires. A measured sigma materially above 4% raises the false-kill rate
and the threshold moves.

## 2026-09-18 — A3. CLV is EV at the closing no-vig price

`clvPct = closeFairProb_at_entry_line * entryDecimal - 1`. Reconciled with this
repository's existing definition before any grading, as A3 demands:
`clvPct = price_clv * entryDecimal` exactly, where `price_clv` is
`cfb_edge.clv.LoggedBet.price_clv`. Asserted to 1e-12 in `tests/test_engine.py`.
At -110 the two differ by 1.909x, so the reconciliation is not cosmetic.

One departure: A3 calls the margin PMF's key-number bumps PRIORS until measured
from at least 1,000 games. This repository's bumps are fitted on 14,687 games and
era-tested, so they are recorded as measured, citing `MODEL.md`.

**Reversal criterion.** The real `edge_engine.jsx` carries different bumps, in
which case the two are compared rather than one replacing the other.

## 2026-09-18 — A4. Execution lag graded at 30 minutes

Every BET row is also graded at the price 30 minutes after `loggedAt`, as
`clvLagPct`. 30 minutes is a PRIOR.

**Reversal criterion.** Measured time from decision to placement once live,
which replaces the 30 directly.

## 2026-09-18 — A5. Gate 2

Pooled BET rows, `clvLagPct`, 95% CI clustered by slate date, lower bound > 0,
n >= 60 minimum, passing with and without `pmf` rows. Live capital goes only to
sports whose own mean CLV is positive. Extended by D5 to gate on cluster count
as well.

**Reversal criterion.** None. This is the gate; changing it is changing what the
project is measuring, and would need its own entry with ledger evidence.
