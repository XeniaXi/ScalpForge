# Exact Structural Sequence Lab

This lab tests whether waiting after a five-minute structural break improves executable
expectancy. It replays the one-second bid/ask path and never submits orders.

## Frozen hypotheses

- `hold_5s` and `hold_15s`: enter only if price remains beyond the broken level.
- `retest_resume`: enter after price revisits the level and resumes in the break direction.
- `sweep_fade`: enter opposite the break after price returns inside the prior range.
- `compression_activity_hold`: require 60/300-second compression, elevated tick activity,
  and a five-second hold.

Each entry is evaluated with a 60-second time exit, a broken-level structural exit, and a
3 bps activation / 1.5 bps distance trailing exit. Entries and exits cross the actual bid/ask
and charge 0.5 bps slippage per side. Paths crossing a gap longer than five seconds are
rejected. A trade must fit entirely inside one walk-forward test fold. The final four days
remain sealed.

These initial thresholds are fixed hypotheses, not parameters selected on test results. Any
later threshold search must fit train/validation segments and score a frozen choice once on
each test segment.

## RDP command

```powershell
Set-Location C:\ScalpForge
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

$features = "C:\ScalpForge\data\curated\features\xauusd-features-3ab3baecb4d4534c\manifest.json"
$structure = "C:\ScalpForge\data\curated\structure\xauusd-structure-6a2aeed7e2cef605\manifest.json"

.\.venv\Scripts\scalpforge-run-sequence-lab.exe `
    --feature-manifest $features `
    --structural-manifest $structure `
    --output-root C:\ScalpForge\outputs\experiments\sequence-lab
```

A policy is not promotable merely because its average is positive. Its block-bootstrap lower
bound and most fold means must be positive, its trade count must be adequate, and it must
survive doubled costs and broker-feed comparison before the sealed holdout is considered.
