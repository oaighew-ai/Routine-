# Saturday Savant prospective shadow feed

The collector records Saturday Savant's published per-game win probabilities
and approximate expected margins before kickoff. It requests only the public
`/games` and `/game/<id>` HTML pages, never query-string or `/api/` routes, and
waits at least the site's published five-second crawl delay between requests.
Each response is gzip-archived by SHA-256 in the `capture-data` branch alongside
its capture timestamp.

The prospective workflow runs Mondays during the season and can be dispatched
manually. It writes a UTC-timestamped file under
`data/saturday-savant/forecasts-*.json`; reruns do not overwrite a same-day
capture. A page captured at or after kickoff is excluded. Team IDs are the
page's game IDs and are recorded as `cfbd:<id>`; the site must continue to match
the CFBD event ID for a row to be usable.

This is a separately attributed external probability feed, not a replacement
for S02 and not a newly trained CFB Edge model. Win probability does not equal
spread-cover probability. Approximate margins are rounded, and the capture has
no same-point executable prices. Therefore this feed does not claim spread
edge, return, CLV, calibration improvement, or delivery readiness. It has no
pick, promotion, stake, or delivery authority. Its first purpose is to collect
untouched pre-kickoff outputs so they can later be scored against settled
results and aligned with independently archived market prices.

The Saturday Savant methodology page describes its own training, tuning, and
held-out seasons and publishes aggregate forecast scores. Those are source
claims, not CFB Edge verification and not market-relative validation.
