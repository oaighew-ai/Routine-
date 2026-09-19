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

## 2026-09-19 — D8. The Odds API key is rejected, so items 2 and 3 stay open

**Decision.** Phase 0 items 2 (plan tier and credit burn) and 3 (the coverage
matrix) are **not** answered and are not guessed.

**Evidence.** `ODDS_API_KEY` is set as a repository secret — the runner shows it
masked in the step environment — and every call to `api.the-odds-api.com`
answers **HTTP 401** on both sport keys. The key exists and the API is refusing
it. Nothing about venue coverage, the plan tier or the remaining credit balance
can be stated from this, and none of it is inferred.

**What this changes in the meantime.** The only figure on record stays the one
already measured in `cfb_edge/providers/oddsapi.py`: the existing CFB capture on
`regions=us,us2,eu` costs 3 credits a poll across 652 polls a week, about 1,956
a week and 8,400 a month. Requesting by `bookmakers` bills as one region and
cuts that to a third. That is arithmetic from the documented billing rule, not a
reading of this account.

Acceptance check 16 (Kalshi prices reaching `bookSet`) therefore remains
unverified against a live board. The offline half passes.

**One diagnostic was wrong and is fixed.** The client reported the 401 as "if
this is a network policy denial the host has to be allowed", because
`HTTPError` subclasses `URLError` and the broad handler caught it first. A
server that answers is reachable; what it answered is the finding. The message
now names the cause per status code, and four tests hold the distinction.

**Reversal criterion.** A working key. Replace the secret and re-run
`phase0-coverage`; both items answer themselves in one run.

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
