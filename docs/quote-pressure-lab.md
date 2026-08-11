# Quote-pressure cost-feasibility lab

This research-only lab investigates the strongest annual sequence result without touching the
sealed final holdout. It tests 15-, 30-, and 60-minute horizons and compares causal market
entries with passive limit entries. Passive orders must actually trade through their price within
five seconds; missed fills remain visible in the fill-rate report. Since quote data cannot reveal
queue position, passive-limit results are explicitly marked as an optimistic upper bound and
cannot independently pass the research gate.

Each anchored walk-forward fold searches fixed pressure, activity, spread, session, horizon, and
execution choices using only its training and validation intervals. The selected configuration is
then frozen and scored once on that fold's test interval. Test performance never chooses a
threshold. Episodes are separated far enough that maximum-horizon trades cannot overlap.

```powershell
$features = "C:\ScalpForge\data\curated\features\xauusd-features-7174d5ab6becbda6\manifest.json"
$structure = "C:\ScalpForge\data\curated\structure\xauusd-structure-ab21f608bdf2a5c3\manifest.json"

.\.venv\Scripts\scalpforge-run-quote-pressure-lab.exe `
  --feature-manifest $features `
  --structural-manifest $structure `
  --output-root C:\ScalpForge\outputs\experiments\quote-pressure-lab
```

The research gate requires at least 500 aggregate test trades, at least 4 bps gross expectancy,
positive net expectancy, profit factor of at least 1.15, and positive results in at least 60% of
selected test folds. Passing is a research milestone only; execution remains paper/demo-only.
