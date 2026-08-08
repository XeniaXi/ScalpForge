# Outcome labels and frozen baselines

This stage turns the point-in-time feature dataset into testable research evidence. It does
not enable order execution and it does not promote a strategy.

## Safety and causality

- Features remain a separate, point-in-time artifact with no future columns.
- Outcome files are marked `future_information: true` and `external_non_executable: true`.
- Returns use executable sides: a long enters at ask and exits at bid; a short enters at bid
  and exits at ask.
- The default model charges 0.5 basis points of slippage on each side in addition to spread.
- Labels crossing a continuity gap longer than five seconds are invalid.
- Each horizon is written separately so the RDP does not materialize all labels at once.
- The final four calendar days are sealed and are not evaluated by the baseline command.

## Build outcomes on the RDP

After pulling and reinstalling the editable package, run:

```powershell
Set-Location C:\ScalpForge

$features = "C:\ScalpForge\data\curated\features\xauusd-features-3ab3baecb4d4534c\manifest.json"
$outcomeRoot = "C:\ScalpForge\data\curated\outcomes"

.\.venv\Scripts\scalpforge-build-outcomes.exe `
    --feature-manifest $features `
    --output-root $outcomeRoot
```

The command creates content-addressed partitions for 5, 15, 30, 60, and 300 seconds. It is
safe to rerun: an existing artifact with the same source and configuration is reused.

## Run frozen baselines

Select the newly created outcome manifest and run:

```powershell
$outcomes = Get-ChildItem C:\ScalpForge\data\curated\outcomes `
    -Recurse -Filter manifest.json |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty FullName

.\.venv\Scripts\scalpforge-run-baselines.exe `
    --feature-manifest $features `
    --outcome-manifest $outcomes `
    --output-root C:\ScalpForge\outputs\experiments\baselines
```

The report compares abstention, deterministic random direction, momentum, mean reversion,
and a compression-breakout proxy. Decisions are spaced at least 60 seconds apart and only
anchored walk-forward test windows are scored.

## Interpretation

These baselines answer whether a candidate adds value beyond trivial rules after modeled
spread and slippage. A positive average alone is insufficient. No candidate may advance
until it passes the research council gates, cost stress, broker-feed comparison, multiple
market regimes, and the untouched holdout. The holdout should be opened once for a frozen
candidate, not repeatedly during strategy development.

Gold Reaper's public design is used only as a hypothesis source. ScalpForge tests breakout
filtering, volatility normalization, session behavior, trailing exits, and strategy
diversification independently; it does not copy marketing profit targets or drawdown claims.
