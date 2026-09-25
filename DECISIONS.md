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

## 2026-09-19 — D14. Candidates come from the price when no system exists, and the sharp book is the reference

**Decision.** `cfb_edge/shop.py` produces candidates with no handicapping
system at all, by pricing each book's quote against **Pinnacle's no-vig
price**. `runner.scan` stays as it is; this is a second driver into the same
`decide()` path, with zero signals, so `p_post` is just the reference's
de-vigged probability and Law 4 decides on EV at the executable price. §8 of
BUILD_PROMPT already names reduced-juice shopping as an owned strategy.

**Why there was a board with nothing on it.** `scan` is driven by signal
fires. `systems.jsonl` is empty, so every game resolved to `NO_EVIDENCE` and
the board was correctly blank. That is the right answer to "what does my
handicapper like" and the wrong answer to "is there anything here worth
betting".

### The direction, which I had backwards first

The first version used the median of the soft books as the reference and
shopped every venue against it, Pinnacle included. That bets Pinnacle every
time Pinnacle is cheaper than DraftKings and FanDuel think it should be —
that is, every time Pinnacle is right. It produced plausible picks with
positive EV attached, which is the worst kind of wrong. Corrected before any
live pull: the reference is Pinnacle where it quotes the market, the soft
median where it does not, the reference book is never itself a candidate, and
the fallback is recorded on the row (`reference`) rather than blended in
silently. 56 of 1,482 quotes on the live slate used the fallback.

**This is the assumption the whole method rests on**, and it is not proven
here: that Pinnacle's no-vig price is closer to the truth than the books it
prices. If it is wrong, every sign flips. It is the first thing the SHADOW
ledger should be able to falsify.

### Three constraints, each of which changed the live result

- **A different line is a different bet.** Spreads and totals compare only at
  a matching number; the rest are logged `LINE_MISMATCH` and never bet.
  Bridging the half-point needs the margin PMF, and a model error imported
  into a market-relative measurement comes back looking like an edge. 558 of
  1,380 pre-game quotes were excluded this way.
- **A kicked-off game is not a candidate.** Added after the first live run,
  which produced six candidates of which four were games already in progress,
  two at +323% and +108% EV. Books run live markets at different speeds, so a
  slow book against a moving game reads as enormous value and is gone before
  it can be taken. Logged `IN_PLAY`; an unknown kickoff counts as started.
- **A book is never measured against a median it belongs to.** Worst in the
  thin markets where one outlier moves the median.

### What the live slate actually produced

One pull, 3 credits, 2026-09-19T18:47:04Z, 71 events and 67 with quotes.

| stage | quotes |
|---|---|
| priced | 1,482 |
| kick-off ahead | 1,380 |
| comparable to the reference | 822 |
| positive EV | 54 |
| survive de-vig sensitivity | **2** |

**The finding: all 52 rejected rows were moneylines, and every one died on
§6.10's de-vig sensitivity check.** Proportional and power de-vig disagree
about whether they are bets at all. 49 of the 52 sat at +300 or longer, which
is exactly where theory puts it: proportional de-vig spreads the margin evenly
and systematically overstates a longshot's chance, while a power fit loads
more of it onto the longshot. The largest number the scan produced was +112%
(BetMGM +4500 against a +2000 reference) and it was correctly rejected.

The two survivors are both home underdogs taking points near even money, at
+0.95% and +0.29% EV, staking $29.77 and expecting $0.25 in total. The second
is below any sane execution threshold and is logged anyway, because raising
the bar until only the comfortable rows survive is how a system stops being
able to report that it is not working.

**Reversal criterion.** A handicapping system is supplied, at which point
`scan` is the primary driver again and this becomes one signal among others,
not the board. Or SHADOW grading shows the shopped rows have no CLV, at which
point the Pinnacle-is-sharper assumption above is what failed and the entry
should say so.

## 2026-09-20 — D15. One delivery authority; NO_EVIDENCE never bets

**Decision.** `picks.json`, built only by `cfb_edge.source_of_truth`, is the
single delivery contract. Captures, shop rows, historical cards, shadow lanes,
and dashboard previews are inputs or diagnostics, never competing pick feeds.
The engine now returns PASS with zero stake whenever Stage A contributes no
registered evidence, even if a quoted price gap has positive EV. A row can no
longer say BET and NO_EVIDENCE at the same time.

S02 remains the sole possible paper-delivery model and must match its frozen
version, SHA-256 and protocol. S01 is quarantined; S03 is input-integrity only;
S04 is unfitted; S05 is monitor-only; F03 and ROUTINE_SHOP cannot deliver.
Current authority is MODEL_REVIEW_REQUIRED, so the canonical card is NO BET.

**Why.** The 19 September live shop board produced two rows labelled BET while
also carrying NO_EVIDENCE. The rows were useful price-dislocation observations,
but calling them picks contradicted the validation policy and created a second,
less strict source of truth. One of the two covered and one did not; that 1-1
outcome supplies no validation and cannot repair the contradiction.

**Reversal criterion.** A registered price-shopping model may become eligible
only after clean forward CLV evidence passes the same immutable chronology,
coverage, week-cluster, calibration, deterministic replay and anytime-valid
promotion gates. The source-of-truth boundary remains even if that model later
passes.

## 2026-09-20 — D16. Unknown week and fractional lines fail closed

**Decision.** A derived line-movement signal requires an explicit season week.
Missing week metadata now returns no side, just as weeks before the registered
gate do. Book line matching also compares the exact decimal number. A posted
3.5 can no longer be truncated and treated as a posted 3, and a listed 3.5
contract can no longer be rewritten as a different integer strike.

**Why.** The earlier implementation used `int(abs(posted_line))` and converted
listed strikes to integers. That silently changed the wager being evaluated.
It also treated an unknown week as permission to bypass the early-season gate.
Both behaviours turned missing or different inputs into positive evidence.

**Reversal criterion.** Fractional strikes may be evaluated when the margin
distribution prices their exact settlement boundary and a test proves the
contract mapping. Unknown weeks may pass only if a different registered timing
source proves the same season gate before the decision is made.


## 2026-09-21 — D17. Model identity and model implementation provenance are separate facts

**Decision.** `config/model_registry.json` continues to own model role and delivery
eligibility. `config/model_implementation_registry.json` owns where the
implementation actually lives and whether this repository can reproduce it.

S02 is explicitly **external to this repository** today. The private Site owns
its forecast implementation and authoritative delivery surface. The repository
must not reconstruct S02 from its name, validation metrics, previous outputs, or
chat context.

When S02 remains external, the only admissible bridge into this evidence plane
is a frozen `CFB_EDGE_S02_FORECASTS_V1` export validated by
`cfb_edge.external_forecast`. The envelope and every row must match the
committed model ID, version, SHA-256 and protocol exactly, carry pre-kickoff
timestamps and frozen input hashes, and append without rewriting history.

**Why.** A model hash is evidence about identity, not source code. Treating the
registry metadata as enough to rebuild the model would create a new,
unvalidated S02 that merely shares the old one's name. The external contract
lets prospective evidence be audited without making that substitution.

**Reversal criterion.** The exact S02 implementation and frozen artifacts are
brought into this repository and deterministic parity against the private Site
is proven on registered test vectors. At that point
`reproducibleInRepo` becomes true and the external import is retained only as
a delivery/export compatibility check.


## 2026-09-21 — D18. S04 predicts market residuals and can only earn authority prospectively

**Decision.** S04 begins as one regularized residual challenger, not a
multi-model ensemble. Its target is the realized home margin minus the market
home-margin baseline. It trains chronologically on prior seasons only.

The initial harness in `cfb_edge.challenger` exists to answer whether
football-context features add information **beyond the market**. Historical
walk-forward evidence may reject S04. It has `promotionEffect: NONE` and cannot
alter S02, the card, or delivery eligibility.

**Why.** The repository's strongest existing evidence says the closing market
contains nearly all of the raw game-prediction information tested so far. A
larger ensemble built to predict scores from scratch would add degrees of
freedom before proving incremental value. Residual prediction makes the hurdle
explicit and reduces the temptation to celebrate accuracy the market already
owned.

**Reversal criterion.** At least two independently specified challengers each
show stable prospective incremental calibration/loss improvement across the
registered minimum sample and week clusters. Only then is an ensemble design
worth testing, and its weights must be learned from prospective performance
rather than assigned by narrative confidence.


## 2026-09-21 — D19. Operating health is observable, derived, and never a second pick feed

**Decision.** `cfb_edge.ops_health` produces
`CFB_EDGE_OPS_HEALTH_V1`, a derived control-plane contract containing
validation progress, capture freshness, model provenance, immutable ledger
counts, and next actions. It cannot promote a model or create a pick.

The scheduled operating rhythm is Monday model review, Tuesday opening board,
Thursday midweek refresh, Friday pre-final, and Saturday final board, resolved
in `America/New_York` so daylight-saving changes do not silently move the
window.

The private Site `/api/picks` is the sole delivery authority. This **supersedes
D15 only on the physical location/name of the delivery contract**; D15's
single-source principle and NO_EVIDENCE rule remain in force. GitHub Pages,
`ops-health.json`, capture reports, shadow forecasts, challenger reports and
local diagnostic cards are observability/research surfaces only.

**Why.** The project had accumulated multiple technically plausible surfaces
that could be mistaken for picks. A control-plane dashboard is useful only if
it makes authority clearer, not if it becomes another board with different
rules. The health contract therefore reports the same committed gates and
links to the private decision surface rather than publishing its own card.

**Reversal criterion.** The delivery surface may move again only if one new
contract can preserve immutable model identity, fail-closed gate recomputation,
frozen input lineage and deterministic replay. There must still be exactly one
authoritative card.


## 2026-09-21 — D20. Weeks 1–3 are learning evidence, but Week 3 is not training data

**Decision.** The first three 2026 weeks are incorporated through
`CFB_EDGE_EARLY_SEASON_LEARNING_V1` with a fixed chronology:

- Weeks 1–2: **TRAIN**.
- Week 3: **PSEUDO_HOLDOUT** exactly once.
- Week 4: **PROSPECTIVE**, never fitted in this module.

The learner is market-anchored. Its only explanatory variable is the frozen
pregame disagreement between the weekly projection and the opening
market-implied home margin. It estimates two quantities from Weeks 1–2:
incremental realized-margin residual and open-to-close movement. Both learned
weights are hard-capped at 0.25 during this early-season phase.

Week 3 qualifies the challenger for a Week 4 **shadow watchlist only** when all
registered checks pass: enough training and holdout games, positive MAE
improvement versus the opening market, positive mean directional CLV, a
majority of holdout games beating the close in the predicted direction, and a
positive movement slope.

CFBD `spreadOpen` / `spread` are reference evidence only. They may train and
score the historical hypothesis, but they never become executable entry prices.
The Week 4 shadow board accepts only this system's own timely
`source=capture` opening rows. The shadow board has zero stake and
`deliveryEffect: NONE`.

**Why.** Using all three completed weeks to fit and then reporting their
performance would convert hindsight into apparent validation. Refusing all three
weeks would discard useful information. A train / pseudo-holdout / prospective
split extracts the information while preserving one uncontaminated check before
Week 4.

**Reversal criterion.** Once enough prospective weeks exist, this temporary
early-season split is retired in favor of the registered multi-week prospective
promotion framework. Week 3 is never moved from holdout into training for the
2026 Week 4 decision after its result is known. Any future refit must start from
Week 4 forward under a separately versioned challenger.


## 2026-09-21 — D21. S04_ES1 freezes the 4–6 point early-season cohort for a Week 4 prospective test

**Decision.** After the D20 early-season learning run rejected the simple linear
residual learner, one narrower challenger is frozen for prospective Week 4:
`S04_ES1 / week4-4to6-market-confirmed-1`.

The signal is not the raw projection and the projection is not treated as fair
value. A game enters the challenger only when this system's timely captured
opening line differs from the frozen pregame projection by **at least 4.0 and
less than 6.0 points**.

That bucket is not new to the project. The historical strategy already reported
the 4–6 disagreement band before this 2026 review. The completed 2026 Weeks 1–3
then showed positive mean directional CLV in that same band in every week. Those
observations justify a prospective experiment, not a promotion, because the
2026 bucket was selected for renewed attention after its results were visible.

A Week 4 row can surface as `EXECUTABLE_SHADOW` only when all of the following
also hold:

- the Weeks 1–3 4–6 cohort meets the frozen minimum sample and per-week checks;
- the current sportsbook is offering the **same spread** as Pinnacle;
- Pinnacle supplies the two-way market reference;
- both de-vig methods agree on the sign of the live price opportunity;
- market-relative EV at the offered sportsbook price is at least the frozen
  threshold in `config/s04_es1.json`;
- the move from the captured open to the current Pinnacle line has not already
  consumed the historical cohort's mean directional CLV.

The historical CLV is a timing budget, not a forecast of future profit. The
live market-relative EV is a price-dislocation check, not proof Pinnacle is
truth. Passing both keys still produces **zero stake**, `promotionEffect:
NONE`, and `deliveryEffect: NONE`.

The live workflow requests spreads only and no more than five named books,
making each pull one Odds API billing unit. It scans more frequently late in
the week, but first checks that the current season week is still Week 4; after
that it exits before an Odds API call.

**Why.** The simple Weeks 1–2 residual fit learned negative coefficients and
failed the Week 3 MAE/movement checks. Increasing model complexity after that
failure would be fitting noise. The 4–6 band is the only pre-existing
disagreement band that also stayed directionally positive across all three
completed 2026 weeks, so it is the narrowest defensible hypothesis to freeze
for a genuine forward test.

**Reversal criterion.** Week 4 is graded exactly as frozen. A negative or
non-positive Week 4 directional CLV result retires S04_ES1 from automatic
forward use unless a separately pre-registered explanation survives a new
prospective cohort. A positive Week 4 result still does not promote it; it
becomes one prospective week in the normal multi-week validation framework.
The 4–6 boundaries, EV floor, Pinnacle requirement and movement rule may not be
changed using Week 4 outcomes and then re-scored as though they were unchanged.


## 2026-09-21 — D22. Artifact logic adds a market-quality layer, not a larger football model

**Decision.** The recovered CFB Edge / Action-style blueprint is adopted only
where it adds independently measurable information to the current system.

The current Week 4 S04_ES1 rule is **not changed**. It remains a frozen
prospective shadow test. Artifact-derived logic is attached as a non-binding
monitor named `S03_M1 / market-quality-monitor-1`.

S03_M1 measures, for each live spread candidate:

- number of fresh books quoting the **same exact spread** as Pinnacle;
- Pinnacle and target-venue quote age, with 15 minutes as the registered
  freshness ceiling;
- current line dispersion across fresh books;
- same-line no-vig probability dispersion;
- executable EV under both proportional and power de-vig, with the minimum
  recorded as conservative executable EV;
- line movement velocity and reversal when enough archived snapshots exist;
- opening-to-current crossings of 3, 7, 10 and 14, using the repository's
  existing measured discrete-margin structure.

Only the first two items are hard **market-quality** checks in S03_M1:
at least three fresh same-line books and quotes no older than 15 minutes.
Dispersion, velocity, reversal and key-number state are logged as diagnostics.
They do not receive directional weight because this repository has not yet
shown prospectively that they improve the market baseline.

**Pressure-test result.** On the Week 4 S04_ES1 live board available before this
decision, requiring three books at Pinnacle's exact point would have reduced the
12 frozen signal games to seven market-quality games. It would not have created
a bet. That is the intended behavior: the overlay removes weak market states
rather than manufacturing additional opportunities.

**Selection-risk correction.** The 4–6 gap cohort was selected after inspecting
multiple related cuts. The new `config/experiment_registry.json` permanently
records the failed linear learner plus the broad and narrow gap buckets that
were inspected. The Weeks 1–3 4–6 record is therefore classified as
high-selection-risk exploratory evidence, never confirmatory evidence.

**Next challenger.** `S04_ES2 / gap-4to6-plus-market-quality-1` is
preregistered before Week 4 outcomes. Its first prospective week is Week 5. It
keeps the 4–6 signal family but additionally requires the S03_M1 quote-quality
pass and conservative executable EV of at least the existing 0.5% floor.
Movement velocity, reversal, dispersion and key-number crossings remain
diagnostic in this first version.

**Explicit non-additions.** Injuries, weather, ticket/handle splits, sharp-money
labels, historical systems and expert consensus are not added now. The
blueprint itself requires point-in-time timestamps, stable definitions and
independent incremental tests for those fields. Adding them without that
provenance would increase degrees of freedom faster than information.

**Why.** The repository already implements much of the blueprint's evidence
discipline: one delivery authority, exact-contract matching, replay/provenance,
chronological challengers, no-evidence fail-closed behavior, proper-score gates
for S02 and immutable shadow cohorts. The missing high-value piece is market
microstructure quality around a candidate. It is available from data already
being captured and can be tested without inventing a new football probability.

**Reversal criterion.** S03_M1 features may become directional only after a
separately registered chronological test shows incremental proper-score or CLV
value beyond the existing market baseline. S04_ES2 may be promoted only from
prospective evidence beginning Week 5 under its frozen rule. Week 4 outcomes
cannot be used to alter the rule and then be counted as validation.


## 2026-09-22 — D23. Opening-line evidence requires venue-time proof; Week 5 is the first audit-grade cohort

**Decision.** A timestamped first sighting is not an opening line. Capture now
persists the exact Kalshi event and bracketing spread contracts used to derive
the line, their venue `open_time`, quote inputs, poll time and code revision.
A row is `true_open` only when both bracketing contracts expose venue-open
metadata and the first valid quote arrives within 900 seconds of the later
contract open. Legacy `capture` and `first_seen` observations remain
research data but cannot enter CLV promotion or stopping evidence.

Week 4 is quarantined unless an individual row can be recovered as
`true_open`. The recovery artifact may attach still-available Kalshi
`open_time` metadata but never rewrites the immutable raw capture.

S04_ES2 Week 5 is the first audit-grade prospective cohort. Its frozen rule
explicitly requires `source=true_open` in addition to the already registered
market-quality and executable-EV gates.

**Scheduler control.** Capture starts two hours before the nominal Sunday
window, requests runners every 15 minutes, and each runner performs three polls
four minutes apart. Venue timestamps, not cron timing, decide classification.

**Reversal criterion.** The 900-second tolerance may change only before a new
prospective cohort under a separately registered rule. Week 4 outcomes or
recovered classifications cannot tune it and then count as confirmation.


### D23 clarification: historical recovery is one-way

A legacy row may **not** be upgraded to `true_open` from metadata recovered
later. The original raw schema did not persist the exact bracketing contracts
or their two-sided quote state, so a later lookup cannot recreate that fact.

The Week 4 recovery therefore uses a stricter one-way proof. For each Kalshi
event it takes the **latest `open_time` of any available rung**. This is the
most generous possible open timestamp for an unknown historical pair. A
first-seen observation more than 900 seconds after even that upper bound is
definitively not a true open. A row inside the bound remains `unverified`;
it is never promoted retroactively.


## 2026-09-22 — D24. S04_ES2 Week 5 runtime implements the frozen rule without adding a new one

**Decision.** Week 5 is operationally ready as the first audit-grade S04_ES2
prospective cohort. The runtime is now implemented in `cfb_edge/s04_es2.py`
and scheduled by `.github/workflows/s04-es2-week5.yml`.

The runtime adds no directional feature and does not re-fit any parameter. It
implements the rule already frozen before Week 4 outcomes:

- opening evidence must be `source=true_open`;
- recorded open lag must be no more than 900 seconds;
- absolute projection/open disagreement must be at least 4 and below 6 points;
- Pinnacle remains the reference and the target book must offer the exact same spread;
- S03_M1 market quality must pass;
- conservative executable EV must be at least 0.5%;
- the inherited historical movement budget must remain positive.

The Week 5 slate is frozen on the evidence branch when `opening_week == 5`.
Later scans continue to use that frozen slate even after the capture pipeline
moves on to Week 6. If no audit-grade signal exists, the runtime does not call
the Odds API. Every qualifying row remains shadow-only with zero stake,
`deliveryEffect: NONE`, and `promotionEffect: NONE`.

**Why.** Preregistration without an executable runtime leaves room for
implementation drift once outcomes begin arriving. Building the runtime before
Week 5 opens fixes the exact selection and execution contract while it is still
prospective.

**Reversal criterion.** No Week 5 result may change these gates and remain part
of the same experiment. Any rule change creates a separately versioned
challenger whose prospective clock starts after that change.


## 2026-09-22 — D25. Week 5 rule is runtime-locked; capture quality is measured independently

**Decision.** The S04_ES2 Week 5 rule is now frozen in `config/week5_freeze.json`.
The runtime compares the active S04_ES2 and S03_M1 configurations to that manifest
before any live odds request. Any drift in signal thresholds, open-lag tolerance,
market-quality requirements, EV floor, reference book, venue set, staking state or
delivery authority fails closed.

Capture quality is evaluated by a separate `week5_capture_health` contract that
reads every game on the Week 5 slate, not only games where the model generates a
signal. It reports TRUE_OPEN coverage, source counts and capture-lag distribution,
and it has no decision or promotion authority.

The capture-health workflow runs after successful capture jobs while Week 5 is the
current or opening week. It writes a versioned audit artifact to the evidence branch.
An internally inconsistent TRUE_OPEN row is an integrity failure; incomplete coverage
is reported as collection state rather than converted into a model judgment.

**Reversal criterion.** Any change to a frozen Week 5 rule creates a new version and
a new prospective clock. Week 5 outcomes may not be used to alter this manifest and
remain in the same validation cohort.


## 2026-09-22 — D26. Bayesian/regime complexity is rejected unless it beats the market out of sample

**Decision.** A new research harness, `S04_BR1 / bayesian-regime-residual-1`,
tests the proposed Bayesian market-residual, fixed regime, dynamic team-state
and ensemble architecture without changing S02, S04_ES1, S04_ES2, the Week 5
freeze, staking, or delivery.

The chronology is fixed from the preserved 2026 evidence: Week 1 fits candidate
parameters, Week 2 selects only among the registered grids, and Week 3 is an
untouched final holdout. The market remains the prior and a zero correction is
the default forecast.

**Untouched Week 3 result (57 games).** The opening-market margin baseline had
MAE 8.8553 and RMSE 11.5427. Every outcome-residual challenger was worse:

- global Bayesian residual: MAE 8.9933, RMSE 11.6330;
- fixed-gap regime Bayesian: MAE 8.9140, RMSE 11.5643;
- dynamic team residual state: MAE 9.2558, RMSE 11.9124;
- ensemble: MAE 8.9933, RMSE 11.6330.

The ensemble selected a global weight of 1.0, which is itself evidence that the
team-state layer added no value in the selection week.

The same architecture was tested against open-to-close movement. A no-move
forecast had MAE 1.3991 and RMSE 1.8635. Global Bayesian movement worsened those
to 1.5205 / 1.9796 and regime Bayesian movement to 1.6811 / 2.0877. Both learned
movement variants were directionally correct on 38% of the 50 Week 3 games with
a non-zero move.

**Consequence.** None of these components is added to the live decision engine.
They remain in the repository as an explicit negative result and reusable
ablation harness. This is not evidence against Bayesian methods in general; it
is evidence that these corrections, on the data presently available, do not
improve the market baseline.

The richer S04 football-context inputs (EPA, success rate, explosiveness, QB
continuity, line play, pace, rest, travel and weather) remain disabled because
the preserved evidence does not contain point-in-time clean histories for them.
Executable historical prices are also absent, so this cohort cannot establish
net betting ROI after vig or venue fees.

**Why.** Adding sophistication after a failed simple residual model would create
degrees of freedom faster than evidence. The correct use of Bayesian shrinkage
here is to make the market hard to dislodge, and the correct use of regime
testing is to reject unstable segmentation rather than rescue it post hoc.

**Reversal criterion.** Reconsider a rejected component only after a separately
registered, point-in-time dataset supports it on an untouched chronological
cohort with both MAE and RMSE improvement over the market baseline. Delivery
authority still requires prospective evidence and executable-price economics
under the existing governance; historical improvement alone cannot promote it.


## 2026-09-22 — D27. Week 5 evidence is graded automatically; BR2 is a separate point-in-time data plane

**Decision.** Week 5 S04_ES2 remains frozen exactly as registered in
`config/week5_freeze.json`. The system now adds verification around that rule,
not new decision logic.

`cfb_edge/week5_capture_health.py` requires complete replay lineage for every
row labeled `true_open`: first-seen time, venue open time, event ticker,
bracketing market tickers, first valid two-sided quote time, poll time, code
revision and the frozen open-lag tolerance. Each row receives an evidence hash.

`cfb_edge/signal_grader.py` automatically grades only signal rows that were
already audit-grade at decision time. The close is the final captured derived
home line at or before kickoff and must itself be fresh enough to grade.
Grading has `decisionEffect: NONE` and cannot create or rescue a signal.

`S04_BR2` is created as a collection-only point-in-time feature warehouse.
The first schema records EPA, PPA, success rate, explosiveness, QB continuity,
line play, pace, rest, travel and wind as distinct fields. Existing CFBD REST
data can populate PPA, success, explosiveness, pace and rest. PPA is not
relabeled as EPA, and unavailable QB, line-play, travel and weather fields stay
null until timestamped source adapters exist.

**Why.** The Week 4 failure was measurement provenance, not a shortage of model
complexity. The next model should be trained on information that can be proven
to have existed before the forecast, and its missingness must be visible rather
than backfilled from future knowledge.

**Reversal criterion.** None for Week 5. Any change to the frozen S04_ES2
decision rule creates a new experiment. BR2 may move from collection to fitting
only after a separately frozen feature contract has adequate point-in-time
coverage and an untouched future chronological holdout is reserved.


## 2026-09-22 — D28. Promotion readiness becomes a first-class evidence contract

**Decision.** Add a cross-layer evidence-readiness contract and content-addressed
raw-source archive without changing any Week 5 decision rule.

`cfb_edge/powerup_health.py` reports four independent layers: replay-complete
opening provenance, the frozen S04_ES2 shadow, fresh pre-kickoff close grading,
and the S04_BR2 point-in-time warehouse. It reports status and next unlocks but
has no delivery, staking, or promotion authority.

BR2 snapshots must now preserve a manifest of the exact CFBD payloads used to
produce features. Each raw response is archived once under its SHA-256 content
hash. A future BR2 fit is not allowed merely because a feature matrix exists.
The feature contract must be frozen before outcomes, modeled fields need
timestamp-clean lineage, raw inputs must be replayable, multiple independent
prospective weeks must exist, and a future chronological holdout must be
reserved before hyperparameter selection.

**Why.** The project has already demonstrated that model complexity is easier
to add than trustworthy evidence. The control plane should make missing lineage
and missing evaluation runway visible before another model can inherit apparent
confidence from a clean-looking feature table.

**Week 5 effect.** None. S04_ES2 remains the frozen prospective shadow. Zero
stake and no delivery authority are unchanged.


## 2026-09-22 — D29. BR2 context sources are admitted by evidence class, not convenience

**Decision.** Implement the missing BR2 point-in-time context as separate,
audited source contracts while preserving `DATA_COLLECTION_ONLY` and the frozen
Week 5 S04_ES2 rule.

Four previously missing families now have deterministic capture paths:

- `epaDiff`: prospectively archived CFBD opponent-adjusted WEPA.
- `linePlayDiff`: week-bounded CFBD advanced net line yards.
- `travelMilesDiff`: CFBD team/venue coordinates and great-circle distance.
- `windMph`: pregame Open-Meteo forecast captured and hashed before kickoff;
  confirmed domes explicitly neutralize wind.

`qbContinuityDiff` remains evidence-gated. Exa may discover primary sources.
Firecrawl may capture pages/PDFs and monitor changes. Browser automation may
inspect visible public context such as Action Network's injury and line-movement
surfaces. None of those methods alone creates a feature. QB continuity requires
two independently audit-grade official-source packets, one for each team, with
pre-kickoff timestamps, content hashes and deterministic fact validation.

Action Network is corroboration-only. Public odds and line movement may be
archived as market context. Locked PRO fields are treated as unavailable and
must not be bypassed, inferred, or used as labels.

**Failure behavior.** New context adapters fail closed to null and publish
source-health state. Failure of a new adapter must not erase the original BR2
feature families.

**Historical rule.** Prospective snapshots are the evidence. A later-season
CFBD WEPA value cannot reconstruct an earlier week's opponent-adjusted EPA.
Weather backtests must use archived forecast runs rather than realized or
reanalysis weather.

**Promotion effect.** None. These inputs cannot influence S02 or S04_ES2.
A separately frozen BR2 feature contract, multiple independent prospective
weeks and an untouched chronological holdout remain mandatory before any fit.


## 2026-09-25 — D30. BUILD_PROMPT becomes a mandatory bootstrap manifest

**Decision.** `spec/BUILD_PROMPT.md` must no longer be interpreted in isolation.
Any model or agent working on CFB Edge must first ingest the current operating
architecture, BR2 point-in-time contract, BR2 hardening review, binding decisions,
model/implementation registries, delivery authority, active experiment/feature
contracts, and the current evidence-plane contracts on `capture-data`.

The bootstrap must emit an ingestion receipt with the main/evidence revisions,
relevant model statuses, delivery authority, active freeze ID, BR2 coverage,
opening-provenance state, grading state, missing/stale artifacts, and any
material conflict. A material conflict blocks write actions until resolved.

Precedence is now explicit: immutable factual evidence; active freeze manifests;
delivery authority; registries; `DECISIONS.md`; current operating/BR2
architecture; the historical build prompt; then reconstructed legacy specs.

**Why.** CFB Edge now spans a control plane, evidence plane, private delivery
authority, frozen prospective experiments, and collection-only challengers. A
future builder reading only the original build prompt can be internally
consistent while still violating the current system. The bootstrap turns the
repository into one deterministic handoff path.

**Authority effect.** None. This changes initialization and governance only.
S02 delivery permission, S04_ES2 frozen rules, S04_BR2 `DATA_COLLECTION_ONLY`,
stake, and delivery authority are unchanged.

**Reversal criterion.** Replace this bootstrap only with a single versioned
machine-readable manifest that covers the same control-plane, evidence-plane and
precedence requirements and is enforced by CI.
## 2026-09-25 — D31. The Kalshi order book is read from the fixed-point wire shape

**Decision.** `parse_book` accepts both `orderbook_fp` (the live shape) and
`orderbook` (the integer-cent shape it was written against), normalising both
to cents. `Level.size` and `Book.depth` become floats.

**Why this was not caught by a test or an alarm.** Kalshi moved prices and
contract counts to fixed-point strings and re-wrapped the book under
`orderbook_fp`. `parse_book` read only `orderbook`, so a successful request
parsed to an empty book: `best_ask` None, `depth` 0, `vwap` refusing every
size. That is indistinguishable from a market with nothing resting in it, so
nothing raised and nothing logged. The suite passed throughout, because its
only book fixture was hand-written in the old shape. A gate that refuses on
thin depth (`gate.py`'s DEPTH check) would therefore have refused every
Kalshi row for a reason that was never true.

The market-level rename (`yes_ask` to `yes_ask_dollars`) was already handled
by `_side_price`. Two sites still read the removed spellings: `parse_book`,
and the `find_markets` listing, which printed None for every price.

**Evidence.** Measured, not inferred, and measured twice. A probe workflow
(`.github/workflows/kalshi-depth.yml`, no Odds API credits) read the three CFB
series on two days. The two runs agree on what matters and disagree sharply on
one number, so both are recorded rather than reconciled.

| median size resting at the best ask | 2026-09-24 | 2026-09-25 |
|---|---|---|
| <=10c | 105 | 882 |
| 11-25c | 108 | 305 |
| 26-45c | 1,000 | 1,864 |
| 46-55c | 3,000 | 4,020 |
| >55c | 296 | 400 |
| priced markets | 4,746 | 4,751 |
| longshots <=10c | 300 | 330 |
| empty books in that band | 0/300 | 0/330 |
| median spread in that band | 2c | 2c |
| quoting inside 5c | 295/300 | 328/330 |

**Stable across both, and the finding that stands:** no empty longshot book,
a 2c median spread, and about 99% of the band quoting inside 5c. The
thin-book hypothesis that motivated the earlier longshot suppression is dead.
Those are real markets.

**Not stable, and a correction to what this session reported first:** the
median resting size moved by up to a factor of eight in a single day. The
earlier claim that a $94.29 stake at ~9c (~1,048 contracts) wants roughly ten
times the top of book was true of the 2026-09-24 snapshot and is not true of
the 2026-09-25 one, where 1,048 against 882 is about 1.2x and walking one
rung fills it.

The conclusion that survives is stronger than the one it replaces: the size
gap is real but varies by nearly an order of magnitude between pulls, so **no
static size or depth threshold can be derived from a single snapshot** --
under Law 6 any such constant would be a PRIOR wearing a measurement's
clothes. Sizing has to walk the live ladder at decision time, which is
precisely what `Book.vwap` does and what this fix restores.

`liquidity_dollars` reads 0.00 on all 4,746 markets despite real resting
sizes. It is unusable and must not enter any gate.

**Derivation for `_SIZE_TOLERANCE = 1e-6` (Law 6).** Not a PRIOR. Kalshi
documents contract granularity as 0.01 contracts, so no genuine unfilled
residual can be smaller than that; 1e-6 sits four orders of magnitude below
the smallest real quantity and admits only float error from walking the
ladder. Source: docs.kalshi.com/getting_started/fixed_point_migration, read
2026-09-25.

**Sub-cent prices are real.** `price_ranges` tick to $0.0001 on the tapered
grids, so cents are carried as floats rather than integers. A rung can sit at
1.2c, and rounding it is a real price error, not a display nicety.

**Reversal criterion.** If Kalshi retires the legacy `orderbook` key, the
compatibility branch and its test can go. If a third shape appears, the same
failure mode returns; the guard against that is
`tests/test_kalshi_book.py::test_the_live_shape_is_not_an_empty_book`, which
fails loudly rather than reporting an empty board.
