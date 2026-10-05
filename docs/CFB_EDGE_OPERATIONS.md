# CFB Edge operations

How CFB Edge runs each week, what is live, and what to do when something
stops. Phase: SHADOW. No money is at risk and nothing here places a bet.

Order of authority: `spec/` first, then `DECISIONS.md`, then this file. If
this file disagrees with either, they win and this file is wrong. Dated
facts are in the last section and nowhere else, so the rest stays true when
they change.

All times are UTC. Eastern time is UTC-4 until 2026-11-01 and UTC-5 after.

## 1. The one path

Decided in D39. There is one of each of these and no second one:

| Thing | Where | Changed by |
|---|---|---|
| Rules, code, configs, registries | `main` | Pull request, merged by the owner |
| Evidence | `capture-data`, append-only | GitHub Actions only |
| Decision engine | `cfb_edge.engine.decide` | Pull request |
| Weekly output | `cfb_edge.weekly_card` (`CFB_EDGE_WEEKLY_CARD_V1`) | Built, never edited |
| Delivery authority | `config/delivery_authority.json` | The owner, by pull request, and nobody else |
| Scheduled Claude task | "CFB Edge weekly routine" | The owner |

The private Site's `/api/picks` is the registered delivery contract (spec
section 3.4). This repository cannot see it. The card is diagnostic: it can
print BET only when the authority file allows delivery, and if that ever
happens it must show the authority's picks and not compute its own (D40).

The card is built by one command, for a workflow, a scheduled task or a
person alike:

```bash
scripts/build_weekly_card.sh <capture-data checkout> <out dir> [AS_OF]
```

It builds the slate for the week being played, takes the newest sportsbook
board fetched at or before `AS_OF`, and writes `weekly-card.json` and
`weekly-card.html`. Exit 3 means no regular-season week is left. Any other
non-zero exit writes no card. The same inputs at the same `AS_OF` rebuild it
byte for byte.

## 2. What runs, and when

Seven workflows have an automatic trigger. `tests/test_week5_workflows.py`
pins that set, so adding an eighth is a deliberate act.

| Workflow | Trigger | What it does |
|---|---|---|
| `capture-open-loop` | 19 launches from Fri 18:00 to Tue 16:30; hands itself to the next link | Polls the exchange every minute. Records opens for the week whose lines are opening and closes for the week being played |
| `br2-active-feature-capture` | Mon to Fri 13:30; on its own code changing | Point-in-time football features and the sportsbook board the card prices from |
| `cfb-operating-review` | Five fixed times a week (Mon, Tue, Thu, Fri, Sat); after each `capture` run | Writes `data/ops-health.json`. No picks |
| `private-site-bridge` | After the feature capture; on its own code changing | Republishes a summary for the private Site |
| `early-season-learning` | On its own code or the slate builder changing | Rebuilds the in-season CLV measurement |
| `watchdog` | Daily 13:17; Sun and Mon 01:17 | Opens an issue if the newest run receipt is stale or failed. Never commits |
| `tests` | Every push to `main`, every pull request | The gates in section 6 |

`capture` is deliberately absent from that table: it has no trigger. Its
schedule was removed in `8573ce8` so a scheduled poller could not queue behind
the open loop, and D44 leaves it a dispatch-only fallback that takes three polls
when no loop is running and then asks for one. The table is the set of
workflows that fire by themselves, which is what the test above pins.

Everything else under `.github/workflows/` runs only when someone starts it.
Ten of those served a closed cohort and carry a `RETIRED` header naming the
decision (D41, D44).

The week, in order:

| When | What happens |
|---|---|
| Fri 18:00 | Release window opens. The loop starts and polls next week's slate. Games of the week being played join the poll 20 minutes before kickoff |
| Fri 20:07 | The scheduled Claude task builds and publishes the card |
| Fri 23:00 | The cohort's game window opens. Features for this week are frozen |
| Sat | Games. Closes are recorded by the loop. At 20:07 the Claude task reports capture counts |
| Sun 00:00 | Next week's cohort becomes active. No configuration change is needed (D41) |
| Sun to Mon | Most of next week's markets are listed in this stretch (one cohort measured, D38) |
| Mon to Fri 13:30 | Feature capture for the active cohort |
| Tue 18:00 | Release window closes. The loop ends by itself |

Scheduled starts on this repository arrive hours late or not at all, so
nothing that matters depends on one arriving on time. The loop does its own
waiting and asks for its own successor (D38), and the single-poll workflow
asks for the loop when none is running (D44).

## 3. Weekly routine

This section is for the scheduled Claude task. It runs Friday and Saturday
at 20:07, September to November, in a fresh session each time. The task's
own hard rules outrank this section. Restated, because they are the point:
it merges nothing, pushes nothing, opens no pull request, edits no frozen
file, invents no number, and suggests no stake. If a tool refuses an
action, it reports the refusal and does not look for another route.

Every run, first:

1. Confirm today's date. Fetch `main` and `capture-data`.
2. Read `CLAUDE.md` and the newest entries of `DECISIONS.md`.

Friday:

1. Board age. Report when the newest file under
   `data/features/br2/market/raw/` on `capture-data` was fetched.
2. Card. Run `scripts/build_weekly_card.sh <capture-data checkout> <out dir>`
   from a checkout of `main`. Publish `<out dir>/weekly-card.html` to the
   card page named in the task's prompt, at that same address and no other.
   The file is a page fragment. If the script exits non-zero, publish
   nothing and report the exit code. Do not improvise a card.
3. Loop. Run `python3 -m cfb_edge.watch --window-open`. If it exits 0, check
   that the newest `capture open loop` commit on `capture-data` is under 20
   minutes old. If it is older and this session is able to start workflows
   in this repository, start `capture-open-loop.yml` on `main`. If it is not
   able to, say so.

Saturday:

1. Loop. The same check as Friday's step 3.
2. Opens. From `data/open-capture/CFB_<year>_PROVIDER_WEEK_<n>/status.json`
   for the newest cohort, report captured, missed, pending and voided rows.
   `CAPTURED_TRUE_OPEN` is the only success. Zero captured after venue opens
   have passed is a failure to report, not progress.
3. Closes. From a checkout of `main`, run
   `python3 -m cfb_edge.watch --close-coverage --log <capture-data checkout>/data/opens.jsonl.gz --slate <capture-data checkout>/data/slate_playing.csv`
   and report the line it prints. If `data/slate_playing.csv` is absent, say
   so. Do not read the log into the conversation.

Report in ten lines or fewer, conclusions first: what ran, what did not, the
counts, and anything that needs the owner, naming the decision. Quote only
numbers read in that run.

## 4. Component status

Status words: **production** is what the authority names and allows.
**shadow** runs from a frozen config at zero stake and cannot change a card
disposition. **monitor** reports and decides nothing. **collection** gathers
inputs for a model that does not exist yet. **retired** keeps its code and
evidence and has no automatic trigger. **blocked** is registered and not
allowed to act. **rejected** was tested and failed.

| Component | Status | Note |
|---|---|---|
| S02 market-anchor shrinkage | blocked | Sole delivery candidate. Implemented on the private Site, not here. `MODEL_REVIEW_REQUIRED`, five failed gates |
| Delivery authority | blocked | `allowPaperDelivery: false`. The card cannot print BET |
| Decision engine | production path, no registered system | `systems.jsonl` is empty, so every side returns PASS or LEAN |
| Weekly card | production output | D39, D40 |
| S01 | blocked | Quarantined incumbent |
| S03 | shadow | Input integrity only. Never supplies a pick |
| S03_M1, S05, ROUTINE_SHOP | monitor | |
| S04 | planned | Not fitted |
| S04_ES1 | shadow, workflow retired | D41 |
| S04_ES2 | shadow, frozen | No cohort is registered after product Week 6 (D34, D41) |
| S04_BR1 | rejected | Historical out-of-sample |
| S04_BR2 | collection | `DATA_COLLECTION_ONLY` |
| F03 | rejected | |
| CLV gate `CFB_EDGE_CLV_GATE_V1` | insufficient | Needs 96 gradeable rows across 4 weeks. A row needs a `true_open` and a close. See section 9 |
| Opening capture | live | D35, D38, D43 |
| Close capture | live from D44 | Not yet observed on a runner |

`config/model_registry.json` owns the roles. The status text in the three
registry files predates the Week 5 and Week 6 results; where it and this
table differ, the registries are the authority and are due an update by
pull request.

## 5. What is frozen

Do not edit these. A change is a new registered file or a decision entry,
never an edit in place:

- `config/delivery_authority.json`. Owner only.
- Cohort contracts: `config/cohorts/*.json`, `config/br2_active_cohort.json`,
  `config/week5_cohort.json`. A contract is written before its window opens.
- Freezes: `config/week5_freeze.json`, `config/week6_clv_freeze.json`.
- Frozen rules: `config/s04_es1.json`, `config/s04_es2.json`,
  `config/s03_m1.json`.
- `config/promotion_review_v1.json`.
- The true-open window, minus 60 to plus 900 seconds (D35).
- Anything already on `capture-data`. Evidence is added, never rewritten. A
  row that was wrong is voided with its reason and kept (D43).

Promotion is a separate registered act with seven conditions (D39,
`docs/PROMOTION_SCORECARD.md`). The policy's minimum holdout is 26 weeks and
4,710 games, so nothing can be promoted under it before the 2027 regular
season ends. A challenger that looks better in-sample changes nothing.

## 6. Changing anything

1. Branch from `main`. Standard library only.
2. Run the gates `tests.yml` runs: the suite, the suite again with sockets
   denied, the standard-library import check, the CLI smoke tests,
   `scripts/golden_vectors.py --check`, the EDGE OS receipt, and the
   `find_plays` assertion.
3. A change to a rule, threshold, gate or schedule gets a `DECISIONS.md`
   entry: the decision, the evidence, what it does not establish, the
   authority effect and a reversal criterion. A threshold with no derivation
   is tagged PRIOR.
4. Open a pull request. The owner merges.

Merging has side effects on the evidence branch. A merge that touches
`capture-open-loop.yml`, `cfb_edge/watch.py` or `cfb_edge/prospective_open.py`
queues a loop run; the link already running keeps its old code until it
ends. A merge that touches the feature capture's paths runs a feature
capture. To make new capture code live at once, cancel the running loop
link after merging: the queued run starts in its place. Do not cancel
between five and twenty-five minutes past 01, 04, 07, 10, 13, 16, 19 or 22:
every listing measured so far fell a few minutes past one of those hours
(D38).

## 7. When something stops

| Symptom | Check | Do |
|---|---|---|
| No `capture open loop` commit for 20 minutes inside the window | Actions, `capture open loop` | Start it on `main`. One run is enough; it carries itself |
| Loop running, zero captured | `status.json` for the cohort: are rows pending or missed | Pending is normal before markets are listed. Missed rows carry the venue open time and the lag |
| A game locked against another game's market | `voidedRows` in the cohort's `status.json` | Nothing. The lock voids the row and rebuilds the game's row from the logged polls (D43) |
| Card build exits non-zero | The script's last lines | Exit 3: no week left. Otherwise the slate or an input is missing; publish nothing |
| Card shows a board more than a day old | Newest file under `data/features/br2/market/raw/` | Expected on Saturday: the feature capture runs Monday to Friday |
| Feature capture late | Card, "Scheduler" gate | Nothing. Late is recorded and degrades the gate |
| Tests fail on `main` | Actions, `tests` | Revert the merge by pull request |

Rollback is a revert of the merge commit, by pull request. A retired
workflow is revived by restoring its `on:` block from the parent of the
commit that retired it. Never force-push either branch.

## 8. Scheduled Claude tasks

One recurring task is the operating path: "CFB Edge weekly routine", Friday
and Saturday 20:07. Two older tasks still exist and are the owner's to keep
or remove: a Saturday 14:00 capture check, and a one-off for 2026-12-06 that
closes the season. D39 names one task; the older Saturday check should be
disabled once the weekly routine has been seen to work for a full weekend.

## 9. Known gaps, as of 2026-10-04

Dated facts. Each line says what is known and what is not.

1. **True opens are captured for about two markets in three.** The first 17
   were captured on 2026-10-04, in product Week 7, with lags of 211 to 800
   seconds. None were captured in Weeks 5 and 6. In the same hours, eight
   markets yielded no readable line until 1.6 to 5 hours after their listed
   open, with polls a minute apart (D43). One night is not a rate.
2. **The log cannot say why a listed market had no line.** It records only
   lines the reader could read. A market that missed the window may have
   had no quotes, or quotes the reader refuses (a ladder that does not
   straddle 50%, or a bid and ask further apart than `MAX_SPREAD`). Logging
   the reason would settle which, and it decides whether the 900 second
   window or the reader is what to look at.
3. **Markets listed outside the release window cannot be true opens.** Two
   of product Week 7's markets were listed on a Wednesday and a Thursday,
   when nothing polls. Both are midweek games, which are listed about a
   week ahead. Covering them means polling all week.
4. **Product Week 6 has no gradeable close.** None of its 27 frozen games
   had a price within 900 seconds of kickoff (D44). Unrecoverable.
5. **S04_ES2 has no registered cohort after Week 6.** True opens captured
   now are evidence about the capture, not about the strategy (D41).
6. **No injury or availability feed.** The card's injury gate reads FAIL
   every week.
7. **CFBD WEPA returns 401.** The feature capture uses the cfbfastR fallback
   through the prior week.
8. **Saturday's card carries Friday's prices.** The board is captured Monday
   to Friday, so any price edge shown on Saturday is against a board up to
   a day old.
9. **The delivery authority snapshot is dated 2026-09-18.** This repository
   cannot see the private Site, so it cannot tell whether that is current.
10. **`grade` has no age limit on a close.** It takes the last price before
   kickoff however old. Week 6's grader has a 900 second limit; the general
   one does not. An age limit there is the owner's to register (D44).
11. **A team with two open moneyline markets gets no fill price.**
    `fill.moneyline_index` drops a team that resolves to more than one
    ticker, which happens if next week's market is listed while this week's
    is open. It fails closed. The game-day rule (D43) would choose between
    them.
12. **`kalshi_market_audit` still matches on team name alone.** It served the
    Week 5 audit and has no trigger.
13. **The card's confidence rests on a 2012 to 2019 sample.** Calibration of
    the sharp reference since 2020 is assumed (D40).
14. **The slate's `total` column is a constant 52.0** (D40).
15. **Thanksgiving week leaves out 13 games** (D41). Changing that needs a
    new contract before 2026-11-22.
16. **The card is not built by a workflow.** The scheduled Claude task or a
    person builds it.
17. **Scheduled starts mostly do not arrive.** Inside product Week 6's game
    window, 4 of 47 scheduled starts of the close workflow produced a poll
    (D44).
18. **Not yet seen working on a runner:** the restart step, close capture,
    and the cohort resolver's first scheduled run. They were tested locally
    against stand-ins. Seen working on 2026-10-04: the loop handing itself
    to the next link twice with no gap, and one replay after another job
    wrote to the branch (D38).

## 10. Where the reasons are

| Topic | Decision |
|---|---|
| Automation writes only to `capture-data` | D3 |
| One gate, registered evidence | D15 |
| Delivery authority | D17, D19 |
| CLV gate and its null | D31, D42 |
| Week 6 recovered-open cohort | D34 |
| Prospective true-open lock | D35 |
| Opening capture: window, hand-over, replay | D38 |
| One path; production, challenger, promotion | D39 |
| What the card's numbers mean | D40 |
| Cohorts, retired triggers | D41 |
| A market belongs to a game only on its game day | D43 |
| Close capture, loop restart, Week 6 retirement | D44 |
