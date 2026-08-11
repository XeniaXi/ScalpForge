# Causal session-strategy lab

This lab tests independently implemented session-range hypotheses on ScalpForge's executable
bid/ask outcomes. It does not copy GOLD_ORB source code or assume that an opening-range breakout
is profitable.

The preregistered family contains three fixed UTC ranges (`asia`, `london_open`, and
`new_york_open`), four policies, and two exit horizons: 24 hypotheses in total. Policies are an
immediate breakout, five-second momentum confirmation, momentum plus elevated quote activity,
and a fade when a breakout returns inside its range within 30 seconds.

Run the range builder first, followed by the lab:

```powershell
$features = "C:\ScalpForge\data\curated\features\xauusd-features-7174d5ab6becbda6\manifest.json"
$outcomes = "C:\ScalpForge\data\curated\outcomes\xauusd-outcomes-463a39305b8a639a\manifest.json"

.\.venv\Scripts\scalpforge-build-session-ranges.exe `
  --feature-manifest $features `
  --output-root C:\ScalpForge\data\curated\session-ranges

$sessions = Get-ChildItem C:\ScalpForge\data\curated\session-ranges `
  -Recurse -Filter manifest.json |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 -ExpandProperty FullName

.\.venv\Scripts\scalpforge-run-session-strategy-lab.exe `
  --feature-manifest $features `
  --session-manifest $sessions `
  --outcome-manifest $outcomes `
  --output-root C:\ScalpForge\outputs\experiments\session-strategy-lab
```

The final four days remain sealed. Signals are evaluated only in non-overlapping outer test
windows. A candidate must have positive block-bootstrap and family-adjusted lower bounds, broad
fold and month profitability, positive expectancy under 1.5x and 2x costs, adequate sample size,
and profit factor of at least 1.15. Passing this research gate does not authorize demo or live
execution.

The windows are fixed in UTC and deliberately marked as not daylight-saving adjusted. Later
research may register exchange-local variants as a separate hypothesis family; it must not alter
this family's definition after results are observed.
