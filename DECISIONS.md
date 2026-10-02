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
## 2026-09-29 — D31. The delivery gates test a claim the strategy never made

`config/delivery_authority.json` gates delivery on `LOG_LOSS_ADVANTAGE`,
`BRIER_NO_WORSE` and an `ANYTIME_E_VALUE` over those. All three ask whether
the model forecasts outcomes better than the market. Measured over 48
forecasts it does not: log-loss advantage -0.0055 where +0.003 is required,
Brier worse by 0.0028, e-value 1.024 against a required 20.

That is a true finding about the model and it is not the strategy's claim.
What has ever measured positive here is +0.44 points of closing line value
against the **opening** number, which asserts that the market is slow rather
than that the model is accurate. A model can be worse than the close at
predicting football and still take value from a stale opening line. The two
propositions are independent, and the authority tests only the first.

Consequence: the authority as configured cannot green-light this strategy
even if the strategy works. The e-value compounds against us while the model
is the worse forecaster, so more data moves it away from the gate rather than
toward it — 1.024 today, 0.340 after 200 forecasts, 0.065 after 500.

`cfb_edge/clv_gate.py` adds a second, independent gate on the CLV claim. It
does not replace the accuracy gates and it cannot relax them; both must be
read, and delivery still requires the authority.

### The null is the fee-adjusted breakeven, not zero

Beating zero CLV does not earn anything, so a gate against zero would pass a
losing strategy. Derivation, all inputs measured in this repository:

    Kalshi fee            0.07 * P * (1 - P) per contract
    worst case at P=0.5   0.07 * 0.25            = 0.0175
    margin residual sd    CLOSING_LINE_RESIDUAL_SD = 15.39 (6,398 games)
    slope at the money    phi(0) / 15.39         = 0.02592 probability/point
    points per unit prob  15.39 / phi(0)         = 38.58
    breakeven             0.0175 * 38.58         = 0.675 points

**The breakeven is 0.675 points and the strategy's headline claim is +0.44.**
Two independent routes agree: the -110 sportsbook figure this project has
quoted since the start is 0.67. So the best number this project has ever
measured does not clear the fee at the strike where the contracts trade, and
the three weeks that were measurable gave +0.052 at t = 0.32.

P = 0.5 is not a pessimistic choice. It is where a spread contract sits, and
it is where the fee peaks.

### The test

A uniform mixture over positive tilts:

    E_n = mean_k exp( lambda_k * S_n - lambda_k^2 * sigma^2 * n / 2 )

with S_n the running sum of (clv - null). Each term is an e-process when the
true mean is at or below the null, and a convex mixture of e-processes is an
e-process, so the whole is valid by construction with no special functions.
Every tilt is positive, so it accumulates only on evidence above the null and
cannot be triggered by a losing run — the failure mode a two-sided mixture
would have.

Threshold 20.0 (alpha = 0.05), chosen to match `minimumAnytimeEValue` so the
two gates are directly comparable. `tests/test_clv_gate.py` verifies Ville's
bound by simulating 600 null paths rather than asserting the algebra.

### Minimum sample: derived

Evidence accrues at `d^2 / (2 sigma^2)` nats per observation at the tilt best
suited to a true excess `d`, and the threshold is `ln(20) = 3.0` nats:

    sigma = 2.00 (measured over the 155 games of early-season-learning.json;
                  the paper log's nine entries gave 0.61, so 2.00 is the
                  conservative of the two)
    d = 0.50      ->  96 observations
    d = 0.33      -> 220
    d = 0.25      -> 384

`evaluate` uses `max(configured sigma, observed sd)`, because understating
sigma inflates the e-value and this gate must never fail toward passing.

`MINIMUM_WEEK_CLUSTERS = 4` is tagged **PRIOR (Law 6)**. It is not a power
calculation. Week 3 of 2026 measured +0.539 at t = 2.26 and week 1 measured
-0.510 at t = -1.91, so a gate satisfied by one favourable week would have
passed on week three alone. Four is the smallest number that forbids it.

### Failure behavior

Fails closed. The default verdict is `INSUFFICIENT`; `PASS` requires the
e-value to cross with the sample minimums met. Only `true_open` and `fill`
rows enter, via `GRADEABLE` in `clv.py`, so the gate cannot be widened here
by accident. A gradeable row with no closing line is counted as unsettled
rather than dropped.

A large, settled sample whose mean sits below the null reads `FAIL`, not
`INSUFFICIENT`. "Insufficient" invites another season; below breakeven with
the sample in hand is an answer.

### Promotion effect

None. This gate grants nothing. It can only withhold, report, or say that the
CLV claim has been tested. Against the capture as it stands — 150 rows, 114
`first_seen` and 36 `late` — it reads `INSUFFICIENT` with 0 gradeable
observations, which is the honest state.


## 2026-09-28 — D32. QB and opponent-adjusted EPA coverage repairs are forward-only

**Decision.** Preserve the completed Product Week 5 BR2 artifacts exactly as
measured: `qbContinuityDiff` coverage 1/57 and `epaDiff` coverage 0/57.
Do not backfill those rows after game outcomes.

For later prospective cohorts, replace the operational dependency on paired
historical depth-chart PDFs with `CFB_EDGE_BR2_QB_CONTINUITY_V4`. The base
team continuity is the current incumbent quarterback's share of team passing
attempts across the last three completed games. The default incumbent is the
passing-attempt leader in the most recent completed game. A validated official
pregame starter may override only when that player maps to captured historical
participation. Missing player-box history remains null.

CFBD WEPA remains the preferred `epaDiff` source. If it is unavailable before
the decision timestamp, `CFB_EDGE_OA_EPA_V1` may populate the same conceptual
opponent-adjusted EPA family for later prospective cohorts. The fallback uses
cfbfastR play-level EPA from completed prior weeks only and a frozen two-way
alternating ridge adjustment with 50 equivalent plays and 50 iterations. It
must preserve the source-asset hash, retrieval time, through-week cutoff and a
content-addressed archive of the exact rows used.

**Why.** The Week 5 missingness was primarily source availability and
operational coverage. Requiring two archived official depth charts per team
does not measure QB continuity better than actual prior participation, and a
CFBD entitlement failure should not make genuine play-level EPA unavailable
when a separately versioned, replayable opponent-adjustment can be computed.

**Failure behavior.** Ambiguous or future-known QB evidence fails closed.
Missing player participation stays null. Failed cfbfastR download/schema/replay
leaves EPA null. PPA is never relabeled EPA.

**Authority effect.** None. S02, S04_ES2, staking and delivery are unchanged.
S04_BR2 remains `DATA_COLLECTION_ONLY`.

**Reversal criterion.** Replace either adapter only with a preregistered source
or feature contract that preserves the same or stronger point-in-time,
identity, raw-lineage and replay guarantees.


## 2026-09-28 — D33. Active market provenance is venue-replayable and forward-only

**Decision.** Keep the append-only live opening log authoritative for what the
poller actually observed. Never relabel a late `first_seen` row as
`true_open`. For the later BR2 active cohort only, add
`CFB_EDGE_KALSHI_CANDLE_OPEN_V1`: recover opening evidence from Kalshi's own
one-minute historical YES bid/ask candles for the exact listed spread
contracts.

A recovered opening is audit-grade only when the exact event resolves to one
canonical CFBD fixture, the two or more contract rungs used to derive the
50-percent crossing are preserved, and the first valid two-sided implied line
occurs between 60 seconds before and 900 seconds after the latest
exchange-reported `open_time` among those selected rungs. The existing
15-minute true-open tolerance is unchanged.

At the active BR2 decision snapshot, capture a fresh spreads-only sportsbook
board for Pinnacle, DraftKings, FanDuel, BetMGM and BetRivers and a current
Kalshi-derived line. Preserve provider update time, retrieval time, canonical
game identity and content hashes. A derived exchange line is market evidence
but never counts as sportsbook depth. Sparse snapshots may prove that movement
occurred, but may not invent an exact movement timestamp.

**Why.** The Oct. 2-3 cohort has exact Kalshi event and contract provenance, but
the live poller first saw many games after the 900-second window. The venue's
own timestamped bid/ask archive can independently prove what was knowable at
open without rewriting the live log. Separately, BR2 already had an
information-state engine but the active workflow passed it no market quotes,
forcing every row to report missing fresh-market evidence.

**Failure behavior.** Missing or ambiguous event identity, absent two-sided
candles, a first valid line outside the frozen tolerance, stale sportsbook
quotes, source-hash failure, or provider failure remains missing. No fallback
may widen time tolerances, synthesize a bookmaker price, or use a later closing
label.

**Authority effect.** None. This is research-only S04_BR2 evidence.
S02 remains the sole delivery candidate. Frozen Week 5 S04_ES2, staking,
delivery and promotion rules are unchanged.
## 2026-09-29 — D34. Week 6 recovered opens become a frozen prospective CLV cohort

**Decision.** The 27 audit-grade recovered true-open rows from the active
Oct. 2-3 cohort are frozen before any kickoff in
`config/week6_clv_freeze.json`. Cohort membership and opening lines are
immutable after registration.

The unchanged S04_ES2 + S03_M1 rule is run once against those 27 rows before
kickoff. The resulting directional and executable-shadow decisions are written
to an immutable decision-freeze artifact and may not be recomputed after later
market movement or outcomes are visible.

Close evidence comes only from the append-only live Kalshi capture log. The
close is the final captured derived home line at or before kickoff and must be
no more than 900 seconds old. `cfb_edge.signal_grader` then grades only the
already-frozen directional/executable rows.

The existing `CFB_EDGE_CLV_GATE_V1` remains the statistical authority for the
market-timing claim. Week 6 signal rows are exported in side-adjusted
coordinates before entering that gate. The fee-adjusted breakeven, e-value
threshold, minimum-observation requirement and minimum-week-cluster requirement
are unchanged.

The S02 delivery authority is rerun independently using the same
source-of-truth validation logic used by the authoritative card. CLV evidence is
reported beside S02 authority but cannot substitute for log-loss, Brier,
calibration, sample-size, freshness or replay gates.

**Why.** A venue-proven opening has value only if the strategy direction is
frozen before the close is known. Freezing the eligible open cohort but
selecting a side after observing market movement would create hindsight. This
two-stage design converts recovered pre-outcome opening evidence into genuinely
prospective CLV evidence without weakening any gate.

**Failure behavior.** A row with no pre-kickoff decision, no fresh captured
close, stale close, non-`true_open` provenance, or invalid side remains
ungradeable. A provisional market state is never promoted to a close.

**Authority effect.** None. S02 remains blocked unless its registered authority
itself passes. S04_ES2 remains shadow-only. S04_BR2 remains
`DATA_COLLECTION_ONLY`. Stake and delivery remain zero.

**Reversal criterion.** Change this protocol only through a separately
preregistered cohort and before that cohort's decisions or closing lines are
observable.


## 2026-09-29 — D35. Prospective live opens replace historical recovery from provider Week 6

**Decision.** The already-open provider Week 5 / Oct. 2-3 cohort keeps its
frozen Kalshi candle-recovery evidence exactly as registered. Beginning with
provider Week 6, BR2 market state may accept an opening line only from
`CFB_EDGE_PROSPECTIVE_OPEN_V1`.

The capture loop polls the live Kalshi spread board at a one-minute target
interval across the measured Saturday-through-Tuesday release band. The first
valid two-sided implied line for a canonical game is immediately locked with
event ticker, selected market tickers, quote inputs, provider `open_time`,
poll time, code revision and content-addressed evidence hash.

A live row is audit-grade only when its observation is within the existing
-60/+900 second true-open window. If the first valid live observation is later
than that, the row becomes terminal `MISSED_TRUE_OPEN_WINDOW`. It may not be
upgraded later from candlesticks, screenshots, sportsbook history or another
retrospective source.

The BR2 active market workflow must use `--prospective-only` from provider
Week 6 forward. A missing prospective status file, cohort mismatch or missing
game row remains missing rather than triggering historical recovery.

**Why.** Candle recovery was necessary to salvage the already-open Week 5
research cohort, but it is not the desired operating model. The stronger
evidence is what the system actually observed while the market was opening.
Making prospective capture authoritative removes the largest remaining
look-back path and turns a missed opening into an explicit measurable capture
failure rather than a later reconstruction problem.

**Authority effect.** None. S02, S04_ES2, staking, delivery and promotion
authority are unchanged. S04_BR2 remains `DATA_COLLECTION_ONLY`.

**Reversal criterion.** Replace the live prospective source only with a source
that proves equal or stronger venue-time, raw-quote, identity and immutable
lineage guarantees prospectively. Do not restore retrospective recovery for
new cohorts.

## 2026-09-25 — D36. The Kalshi order book is read from the fixed-point wire shape

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

## 2026-09-30 — D37. The pick path prices Kalshi moneylines at the fill, not the quote

**Decision.** `shop.py` accepts an injected `fill_probe`. When given, every
moneyline row on a venue that publishes resting size is priced at the average
that `gate.DEFAULT_SIZE` contracts would actually pay, by walking the live
ladder through `Book.vwap` (D36), and rows whose resting size cannot cover the
order are flagged `NO_FILL` with stake nailed to zero. The probe lives in
`cfb_edge/fill.py`, is wired into `.github/workflows/shop-board.yml`, and is
**off by default**.

**Why this was needed.** `shop.py` has warned in prose since it was written
that "a median is what was quoted, not what you would be filled at, and the gap
between the two is where this kind of edge usually dies." Nothing measured that
gap. Only `board.py` reached `parse_book`, `vwap`, `depth` and `gate.py`;
`shop.py` — the module that produces the picks — reached none of them and took
its Kalshi price from The Odds API, which publishes no size at all.

**What it actually changes, stated precisely.** Not the decision. No shopped row
can BET as things stand: `decide` returns PASS with zero stake for any row
without a registered Stage-A-eligible signal, `shop` passes none, and
`systems.jsonl` is empty. What changes is the EV written to the record. On a
live-shaped fixture a Kalshi moneyline quoted at +400 carries **+15.7%** EV
against the sharp reference; walked at 200 contracts against a ladder holding 10
at that price, the executable average is 39c and the same row is **−39.9%**.
That number is what the board ranks by and what the SHADOW ledger grades, so an
EV inflated by size that is not there produces a candidate that reads as a miss
later for a reason nobody can reconstruct. The fill test becomes a bet-blocker
the moment a system is registered; until then it is a truth-in-labelling fix on
the record, which is what the ledger exists to protect.

**The venue asymmetry, which is the design question this decision settles.**
Only Kalshi publishes depth, so only Kalshi rows face the test. This does not
make the soft-book rows safe: a DraftKings row still carries no fill test and
its price is exactly as unverified as every Kalshi row was before this. The one
venue whose liquidity can be checked is now held to a stricter standard than the
venues whose liquidity cannot, so the board will under-select Kalshi relative to
books that get no scrutiny at all. Reading "fewer Kalshi rows survive" as
"Kalshi is worse" inverts the finding, which is only that Kalshi is where being
wrong about size is detectable. The absence of `NO_FILL` on a soft-book row
carries no information and must never be read as a fill test that passed.

**Moneylines only, and that is not a shortcut.** A Kalshi contract and a book's
moneyline on the same team are the same bet, which is why `shop.py` already
notes that moneylines "have no line and are always comparable". Spreads are not:
`strike_of` floors half-points because 16.5 and 16 settle identically on the
exchange, while a book quoting −16.5 against −16 is offering a different wager.
Bridging that needs the margin PMF, and `shop.py` already refuses those
comparisons as `LINE_MISMATCH` for exactly that reason. Spread and total rows
are left untested rather than tested badly.

**Off by default, on purpose.** `s04_es1` and `s04_es2` price frozen cohorts
through `shop()`. D33, D34 and D35 make those cohorts forward-only, so
re-pricing a settled cohort against a ladder pulled today would rewrite a
measurement after the fact. The probe is injected by the live board only, and
`tests/test_fill.py::test_without_a_probe_nothing_changes` holds the default
path in place.

**The size is a policy input, not a derived one.** Deriving the fill size from
the stake is circular: the stake depends on the edge, the edge depends on the
executable price, the executable price depends on the size. `board.py` already
breaks that loop with an exogenous `--size`, and `fill.py` imports the same
`gate.DEFAULT_SIZE` so "the size the gate insists it can fill" has one
definition. **This is a PRIOR (Law 6):** 200 contracts is the number `gate.py`
has always used and no derivation for it exists in this repository. It is not
measured and must not be presented as if it were.

**Law 4 and Law 5.** The probe runs before `decide`, not after, so the gate sees
the executable price and each row is still gated exactly once. Deciding on the
quote and re-deciding on the fill would gate twice and leave two EVs on the
record with no rule for which one counts. `venue_price` keeps meaning what the
venue published, because `s04_es1` and `s04_es2` record it as `currentPrice`;
the price the gate saw is `ShopRow.decided_price`, which reads off
`decision.quote.price` so the two cannot drift apart.

**Verification.** 26 tests in `tests/test_fill.py`, kept in a new file so a
concurrent edit to `test_shop.py` cannot conflict with it. Proven non-vacuous by
disabling the wiring in `shop.py` and re-running: 6 of the 26 fail, and they are
exactly the 6 that assert the wiring. `test_a_thin_kalshi_book_is_flagged_
NO_FILL_with_no_stake` deliberately does not assert `not bets`, because that
would pass whether or not this code existed and would credit the fill test with
a refusal it did not make.

**Reversal criterion.** If The Odds API begins publishing resting size, or
another venue on the board does, the asymmetry argument weakens and
`FILL_TESTED_VENUES` should grow to match rather than staying Kalshi-only out of
habit. If a margin-PMF bridge for strikes is ever derived and measured, the
moneyline-only restriction can be revisited — but not before, since that bridge
is the model error `shop.py` exists to keep out. If `gate.DEFAULT_SIZE` is ever
derived from something, this entry's PRIOR tag comes off with it.

## 2026-10-02 — D39. One operating path, one card, and promotion as a separate registered act

**Decision.** CFB Edge runs on one path, described in full in
`docs/CFB_EDGE_OPERATIONS.md`:

- Control plane: `main` (code, configs, registries, this file), changed by
  pull request only. Evidence plane: `capture-data`, append-only, the only
  branch automation writes (D3).
- One decision engine, `cfb_edge.engine.decide`. Nothing else produces a stake.
- One weekly output, `cfb_edge.weekly_card` (`CFB_EDGE_WEEKLY_CARD_V1`),
  rendered by `cfb_edge.card_render`. Any surface that shows picks derives from
  that JSON.
- One authority, `config/delivery_authority.json` (D17, D19). The card can
  print BET only when that file allows delivery (D40).
- One scheduled Claude task, "CFB Edge weekly routine": it builds and
  publishes the card and checks that capture is landing. It never merges,
  promotes, stakes, or edits a frozen config.

Surfaces that competed with this are superseded, not deleted, so history
survives: the LATTICE dashboard and `edge_os_v3_lattice.md` in the claude.ai
project (seeded sample data), the Week 3 Orders and CFB Edge Picks artifacts,
and the Ground Truth artifact (its test count and decision range predate D37).

**Production, challenger, promotion are three different things.**

- *Production* is what the authority names and allows. Today that is S02 at
  `MODEL_REVIEW_REQUIRED` with `allowPaperDelivery: false` and five failed
  gates, so production delivers nothing and the card's BET count is zero by
  construction until that file changes.
- A *challenger* is any registry entry that is not production (S04_ES2 frozen
  shadow, S04_BR2 data collection, the monitors). It runs from a frozen
  config, records its prediction before the outcome exists, stakes zero, and
  cannot change a card disposition; the card shows it under "Shadow signals".
- *Promotion* moves a challenger into the authority. Every condition below is
  required; none substitutes for another.
  1. Registration before the holdout: the fields
     `CFB_EDGE_PROMOTION_REVIEW_V1` lists (model, feature-contract,
     training-protocol and execution-policy hashes, holdout start/end/weeks,
     attempt number), committed here before any holdout outcome exists.
  2. Evidence from gradeable rows only (`GRADEABLE` in `clv.py`: `true_open`,
     `fill`). Recovered, `first_seen` and `late` rows never count.
  3. The registered policy's single final look: net return per unit after all
     costs (primary), paired Brier improvement of at least 0.001 against the
     decision market (secondary), calibration error at most 0.05, a 0.01 per
     unit stress cost, 5,000 bootstrap replicates, familywise alpha 0.05. Its
     minimum holdout is 26 weeks and 4,710 games. At roughly 55 games a week
     that is more than one full season, so no challenger can be promoted
     under the policy as registered before the 2027 regular season ends.
  4. `CFB_EDGE_CLV_GATE_V1` (D31) reads PASS, or a successor null that was
     registered before its data (D42).
  5. The authority's own gates pass for the candidate: 200 non-push
     forecasts, 8 week clusters, log-loss advantage of at least 0.003, Brier
     no worse than the market, anytime e-value of at least 20, validation no
     older than seven days.
  6. The walk-forward history (`scripts/walkforward_backtest.py`) shows no
     degradation against the market baseline. Necessary, never sufficient.
  7. The owner merges a pull request that edits
     `config/delivery_authority.json`. No automation makes that edit.
- *Demotion.* Any registered gate that fails on a later re-check reverts the
  authority file to its previous commit, recorded here.

**Why.** Twenty-five workflows, three scheduled tasks, two pull-request
check-in loops, a private Site and at least four pick surfaces each made sense
locally. Together they could not answer "what is this week's card, and why".
One card from one engine under one authority can.

**Authority effect.** None. S02 remains the sole delivery candidate, the
private Site remains the registered authority, and the frozen S04_ES2 rules,
S04_BR2 `DATA_COLLECTION_ONLY` and every threshold are unchanged.

**Reversal criterion.** Split the path only when a component must run where
this repository cannot, such as the private Site's model, and then only with
that component writing its output to `capture-data`, so the card still reads
one evidence plane.

## 2026-10-02 — D40. What the weekly card's dispositions and numbers mean

**Dispositions**, exactly one per game:

- **BET** only when the engine returns BET with a stake (a registered Stage-A
  signal, gated once, D15), the authority allows paper delivery, the
  executable quote is no older than `maximumQuoteAgeSeconds` (900 s), and
  neither critical gate (market capture, information state) reads FAIL.
  Today the card passes the engine no registered system, because none exists
  in this repository: `systems.jsonl` is empty and S02 is an external
  implementation. So the card cannot print BET. If the authority ever allows
  delivery, the card must show the authority's own picks, mirrored to
  `capture-data`, and never compute a second set.
- **LEAN** when a book's price beats the sharp reference's no-vig price at
  the same number under both multiplicative and power de-vig, and the engine
  did not flag `DEVIG_SENSITIVE`, but evidence, authority or freshness blocks
  a bet. Zero stake. A lean is a price observation logged so its closing-line
  value can be measured, not a prediction.
- **PASS** otherwise, with reason codes. PASS is a complete answer.

**Confidence** is the engine's posterior probability that the pick covers at
its executable number, push-adjusted through the fitted margin distribution.
With no registered evidence the posterior equals the reference's no-vig
probability (Pinnacle, else the US-book median), because the projection's
weight is zero: its incremental coefficient over the close is -0.012
(t = -0.43) across 9,113 walk-forward games. Measured calibration of that
reference: Pinnacle no-vig closing probabilities over 5,199 games, 2012-2025,
expected calibration error 0.013 and Brier 0.2501 against 0.2500 for a coin
flip. It is calibrated near 50% and nearly uninformative about who covers,
which is what an efficient spread market looks like. Spread confidence on this
card will therefore sit near 50%, and a figure far from 50% points at a data
problem before an edge.

**Edge** is expected value per unit at the executable price against the
reference; the card uses the worse of the two de-vig methods. The minimum
acceptable price is the worst price at which the engine's
`max_playable_price` sweep still clears.

**The margin distribution uses one sigma for every game.** The slate's
`total` column is the constant 52.0 on all 56 rows, a placeholder and not a
market total, so the card takes sigma at `REFERENCE_TOTAL` instead of
pretending to a game-specific one. It affects only the push probability and
the value of a half point, never which side has the edge.

**Gate states** are PASS, DEGRADED, FAIL and NOT APPLICABLE. A missing input
is FAIL or DEGRADED with a reason, never PASS. A critical FAIL blocks BET.

**Ranking** is `max(conservative EV, 0) x evidence factor x execution factor`,
with evidence factors 1.0 / 0.85 / 0.5 for PASS / DEGRADED / FAIL per gate
and execution halving every four hours of quote age. **Both factors are PRIOR
(Law 6)**: chosen, not derived. The ranking orders the card and has not been
validated against realized return; the card prints that.

**Traceability.** The card records the SHA-256 of every input, the code and
evidence revisions, and its own SHA-256 over a canonical serialization; the
same inputs at the same `--as-of` rebuild it byte for byte
(`tests/test_weekly_card.py`).

**Authority effect.** None. The card cannot produce a BET the engine and the
authority would not.

**Reversal criterion.** Replace the confidence definition only with a model
probability that has completed the promotion path in D39. Replace the PRIOR
factors only with values fitted to graded card history.

## 2026-10-02 — D41. Orchestration repairs are forward-only

Two defects let the evidence plane lose or bury information without anyone
deciding it should. (The capture repairs are D38.)

**1. A cohort may not drop games of its own week in silence.**
`cohort.build_slate` filtered the provider week to the game window without
recording what it removed. The Week 6 contract's window opens Friday
2026-10-02 23:00 UTC and so dropped the two Thursday games (Western Kentucky
at New Mexico State, North Texas at Tulsa). Contracts written from now on set
`coverage.requireFullProviderWeek: true`; `build_slate` then refuses to drop a
same-week game (inside the capture window, outside the game window) unless
`coverage.excludedGames` names it with a reason. Games outside the capture
window are provider mislabels and are still dropped. Existing contracts keep
their frozen windows; `build-slate` now prints what they leave out.

**2. Closed-cohort workflows lose their automatic triggers.** Eight workflows
served cohorts whose windows have closed and kept firing on schedule or on
`workflow_run` cascades: `s04-es1-live`, `s04-es2-week5`,
`week5-close-capture`, `week5-signal-grade`, `week5-capture-health`,
`week5-late-open-capture`, `br2-feature-capture` (superseded by
`br2-active-feature-capture`) and `powerup-health` (Week 5 readiness; the
card's system health supersedes it). Each run skipped its work, but a
successful completion still set off the readiness and bridge workflows.
Measured on `capture-data` over the seven days to 2026-10-02: 154 bridge
commits, 142 readiness commits and 61 signal-grade commits, each changing only
a `generatedAt` stamp, against 9 feature snapshots and 53 capture commits.
Every one of those 357 commits was also a chance for the capture loop's push
to be rejected (D38). Each retired workflow keeps `workflow_dispatch`, its
code, configs and evidence, and a header naming this entry.
`week6-clv-close-grade` stays live until Week 6 is graded.

Not changed: `private-site-bridge` still republishes after each remaining
upstream run, because the private Site is its consumer and this repository
cannot see whether the Site reads the stamp for freshness. `s04_es2.py`
reports 102 `mappingFailures` for Week 6; those are board rows for games that
were never candidates, not failed joins. The experiment's frozen Week 6
decisions are being graded, so the mislabel is recorded here and the module is
left alone until that cohort closes.

**Authority effect.** None. No frozen rule, threshold or decision changed;
the Week 5 and Week 6 cohort contracts are untouched.

**Reversal criterion.** Restore any retired trigger from the parent of this
commit if a closed cohort must be re-captured.

## 2026-10-02 — D42. Finding: the CLV breakeven depends on the strike (no rule change)

D31 sets one null, 0.675 points: the Kalshi fee at P = 0.5 converted to points
with the at-the-money slope of the margin distribution. The slope is not
constant. Where probability mass piles up on key margins a point is worth
more probability, so fewer points cover the same fee. Recomputed from the
fitted margin distribution and `kalshi_fees`: about 0.26 points at a strike
on 3 or 7, 0.38 at 14, 0.74 at pick'em, 1.72 at an off-key 9
(`docs/research/walkforward_2026.json`, `economics`).

Against those numbers the registered rule (week 3 or later, gap of at least
4) measured +0.339 points of CLV over 676 games in 2023-2025 with consistent
opens (95% interval +0.14 to +0.54, 39 week clusters). The interval straddles
the key-number breakeven and sits wholly below the pick'em one. By season:
2023 +0.681, 2024 +0.123, 2025 +0.141, so the pooled figure leans on one year.
The 2026 in-season measurement is +0.052 (t = 0.32, 155 games). These are
sportsbook closes; that they transfer to exchange strikes is untested.

**What this does not do.** It does not change D31's null, the CLV gate, or
any threshold. Replacing a null after looking at the data it would judge is
the error this file exists to prevent. The owner may register a strike-aware
null for a future cohort before that cohort's data exists; until then D31
stands.

**Reversal criterion.** Superseded by a registered strike-aware null, or
withdrawn if a recomputation with a better-fitted margin distribution moves
the key-number breakeven above 0.54.
