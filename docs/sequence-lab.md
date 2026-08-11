# Exact Structural Sequence Lab

This lab tests whether waiting after a five-minute structural break improves executable
expectancy. It replays the one-second bid/ask path and never submits orders.

## Frozen hypotheses

- `hold_5s` and `hold_15s`: enter only if price remains beyond the broken level.
- `retest_resume`: enter after price revisits the level and resumes in the break direction.
- `sweep_fade`: enter opposite the break after price returns inside the prior range.
- `compression_activity_hold`: require 60/300-second compression, elevated tick activity,
  and a five-second hold.
- `quote_pressure_hold`: require elevated activity, efficient quote changes, aligned
  five-second pressure, and no spread shock.
- `trend_alignment_hold`: require aligned five- and thirty-second returns before entry.

Each entry is evaluated at 60-, 300-, and 900-second horizons with a time exit, a broken-level
structural exit, and a 3 bps activation / 1.5 bps distance trailing exit. Entries and exits cross the actual bid/ask
and charge 0.5 bps slippage per side. Paths crossing a gap longer than five seconds are
rejected. Candidate episodes are separated by at least 960 seconds, covering signal delay,
execution delay, and the maximum holding horizon so evaluated trades cannot overlap. A trade
must fit entirely inside one walk-forward test fold. The final four
days remain sealed.

The annual implementation streams aligned 50,000-row feature and structure windows with
only the causal future look-ahead required for each candidate. It reports explicit abstention,
monthly and fold consistency, base/1.5x/2x cost results, and a machine-readable research gate.
Passing requires at least 500 trades, at least 4 bps mean gross movement, a positive bootstrap
lower bound, a positive family-adjusted fold lower bound, non-negative 1.5x- and 2x-cost
expectancy, profit factor of at least 1.15, at least four folds and six months, positive results
in at least 60% of folds and months, and no single positive month supplying over half of total
positive monthly performance. Policy abstention and execution rejection are reported separately.

These initial thresholds are fixed hypotheses, not parameters selected on test results. Any
later threshold search must fit train/validation segments and score a frozen choice once on
each test segment.

## RDP command

```powershell
Set-Location C:\ScalpForge
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

$features = "C:\ScalpForge\data\curated\features\xauusd-features-7174d5ab6becbda6\manifest.json"
$structure = "C:\ScalpForge\data\curated\structure\xauusd-structure-ab21f608bdf2a5c3\manifest.json"

.\.venv\Scripts\scalpforge-run-sequence-lab.exe `
    --feature-manifest $features `
    --structural-manifest $structure `
    --output-root C:\ScalpForge\outputs\experiments\sequence-lab
```

A policy is not promotable merely because its average is positive. Its block-bootstrap lower
bound and most fold means must be positive, its trade count must be adequate, and it must
survive doubled costs and broker-feed comparison before the sealed holdout is considered.
