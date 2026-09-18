# capture-data

Captured opening lines for the college football edge model. **This branch holds
data only. No code, no history worth reading, no reason to merge it anywhere.**

`.github/workflows/capture.yml` on `main` appends to `data/opens.jsonl.gz` every
ten minutes through the release window (Sunday 22:00 UTC to Tuesday 18:00 UTC,
September through November) and pushes the result here.

## Why a separate branch

A GitHub Actions runner is destroyed when the job ends, so a capture that writes
to its own filesystem records nothing that survives. The log has to be pushed
somewhere, and pushing it to `main` would put a commit every ten minutes into
the history of the code.

## The files

| file | what it is |
|---|---|
| `data/opens.jsonl.gz` | the raw append-only capture, one record per poll |
| `data/opens.csv` | first-seen line per game, rebuilt from the log each run |
| `data/status.txt` | when it last ran and what it saw |

`opens.jsonl.gz` is the source of truth. Both other files are derived and can be
rebuilt:

```
python -m cfb_edge.watch --log data/opens.jsonl.gz --rebuild --out data/opens.csv
```

## What it is worth

The strategy's only measured edge is +0.44 points of closing line value against
the **opening** number, so what matters is the first price seen for each market
and never the latest. The capture preserves first-seen and refuses to overwrite
it, which is the entire reason this runs on a schedule rather than on demand.
