# Graveyard

Ideas that were tried and died. **Never re-propose one of these without new
evidence**, and "it feels like it should work" is not evidence. Each entry says
what would have to be true for it to come back.

BUILD_PROMPT §2 seeds this file from EDGE_OS_v1 §14, which was never supplied.
What follows is seeded instead from results this repository actually measured,
which is the same standard applied to a different source.

---

## The projection-priced card

**What it was.** Pricing a play off the model's projected margin instead of the
market's posted line.

**How it died.** `MODEL.md`, "The pricing bug: quoting a number nobody was
offering" and "The real backtest: 6,398 games against actual closing lines". The
model has no incremental information over the closing line: coefficient -0.02,
t = -0.31 across 6,398 real closing lines. A price derived from a projection with
no incremental information is a price for a contract nobody is offering. Both
terms were wrong, not just the price.

**Guarded by.** `tests.yml` asserts `find_plays` still takes `market_line` and
does not take `projected_margin`. The check exists because this was the most
expensive bug the project has had.

**Comes back if.** The projection shows incremental information over the closing
line on a held-out sample: a positive coefficient with t > 2 on at least 1,000
games.

## A flat entry bar in cents

**What it was.** A fixed minimum gross edge, in cents, for a cross-market entry.

**How it died.** `kalshi_fees.py` and `MODEL.md`, "Kalshi fees, for the
cross-market engine". Kalshi's fee is `0.07 * C * P * (1-P)`, which peaks at 50c
and falls toward both tails. Book-derived fair values cluster near 50c, exactly
where the fee is at its 1.75c maximum. A 3c gross edge is worth 2.7c at 21c and
negative three quarters of a cent at 49c. A flat bar is loosest precisely where
the fee is highest, so it lets the worst trades through and holds the best back.

**Replaced by.** `required_gross_edge_cents`, a bar that bends with the fee curve.

**Comes back if.** A venue's fee stops depending on price.

## CLV as a sizing control at small n

**What it was.** Letting measured CLV scale stakes before the measurement means
anything.

**How it died.** `bootstrap.py` and `MODEL.md`, "Measuring whether any of it is
real". Contracts on one game are not independent observations. Naive
`+0.303c [+0.187, +0.419]` promotes; clustered `+0.303c [-0.091, +0.716]` does
not. At the per-game dispersion in that sample roughly 500 games are needed
before a true +0.15c edge shows a lower bound above zero.

**Replaced by.** A2's floor: CLV is monitoring only below 60 own BET grades, and
then kills rather than sizes.

**Comes back if.** Measured per-cluster dispersion is small enough that a sizing
signal is resolvable at the sample sizes this project actually reaches.
`bootstrap.required_clusters` answers this directly.

## Fuzzy team-name matching

**What it was.** Nearest-match resolution of team names across providers.

**How it died.** `teams.py`. Every plausible fuzzy scheme in college football
maps `Mississippi` to `Mississippi State` or `Miami` to `Miami (OH)` at some edit
distance. An unmatched game costs one skipped bet; a mismatched one costs a bet
on a different team in a different state. Those are not the same mistake.

**Replaced by.** Exact resolution through normalisation or an explicit alias, and
`UNMAPPED` otherwise.

**Comes back if.** Never, at any edit distance. A provider stable enough to trust
fuzzily is stable enough to add to `ALIASES`.

## Grading a line with no capture behind it

**What it was.** Computing CLV from a line typed in by hand, read off a
screenshot, or taken from a search result.

**How it died.** `clv.py`. Such a number is identical to a captured opening line
in every way a float can be identical, and the difference only surfaces in the
CLV it produces, by which point it has been believed.

**Replaced by.** The `source` enum and `GRADEABLE`; here, `priceSource` (D6).

**Comes back if.** Never. A number with no timestamp cannot measure movement.

## Betting the first two weeks of a season

**What it was.** Running the card from week 1.

**How it died.** `MODEL.md`, "The first two weeks of a season do not work". The
ratings have no current-season information yet, and the measured edge is absent
there.

**Replaced by.** `MIN_SEASON_WEEK = 3` in `strategy.py`.

**Comes back if.** A prior strong enough to carry weeks 1 and 2 is measured out
of sample.
