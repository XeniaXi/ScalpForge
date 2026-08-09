# Structural Event Lab v1

The lab tests whether past-only structural breaks in XAU/USD produce enough favorable movement
to overcome the execution drag measured by the frozen baselines. It is a research artifact and
cannot submit orders.

## What v1 measures

- Prior rolling highs and lows over 60, 300, 900, and 3,600 seconds, excluding the current row.
- A 300-second structural-break direction and distance.
- A session-reset tick-activity-weighted VWAP proxy. It is deliberately named a proxy because
  spot-FX tick activity is not centralized traded volume.
- 60-to-300-second range compression.
- Gross midpoint movement, executable net return, and inferred total execution drag.
- Results split by session and low/medium/high volatility, spread, and tick-activity regimes.
- A deterministic random direction at each identical event timestamp as a paired control.
- Block-bootstrap confidence intervals that preserve local clusters of trades.

The final four calendar days remain sealed. A `retest_compatible_continuation` label means the
future path range is consistent with a retest and continuation; v1 does not claim that the
ordering is proven. A later path-sequence artifact will identify exact break, retest, and
continuation timestamps.

## RDP commands

```powershell
Set-Location C:\ScalpForge
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

$features = "C:\ScalpForge\data\curated\features\xauusd-features-3ab3baecb4d4534c\manifest.json"
$outcomes = "C:\ScalpForge\data\curated\outcomes\xauusd-outcomes-11b4f0b57b2e13ea\manifest.json"

.\.venv\Scripts\scalpforge-build-structure.exe `
    --feature-manifest $features `
    --output-root C:\ScalpForge\data\curated\structure

$structure = Get-ChildItem C:\ScalpForge\data\curated\structure `
    -Recurse -Filter manifest.json |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty FullName

.\.venv\Scripts\scalpforge-run-structural-lab.exe `
    --feature-manifest $features `
    --structural-manifest $structure `
    --outcome-manifest $outcomes `
    --output-root C:\ScalpForge\outputs\experiments\structural-lab
```

## Promotion interpretation

A promising slice must have positive net expectancy, a positive block-bootstrap lower bound,
a positive paired-random delta, representation across multiple folds, and resilience when
costs are doubled. V1 discovers hypotheses; it does not promote them. News proximity is not
silently inferred in this report—the normalized GDELT event-time join will be added as a
separate provenance-bearing component after price-structure diagnostics are established.
