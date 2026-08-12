# Multi-hour research dataset

Stage 1 converts the one-second executable quote features into five-minute decision bars and derives only information available when each bar closes. It supplies separate state for volatility expansion, multi-scale momentum, and prior multi-hour boundary crossing. Labels and future returns are deliberately absent.

The currently inspected annual sample is marked `development_only` and `holdout_eligible: false`. A later lab must use new/prospective data for confirmation.

On the RDP, reinstall after pulling and run:

```powershell
Set-Location C:\ScalpForge
.\.venv\Scripts\python.exe -m pip install -e .

$features = "C:\ScalpForge\data\curated\features\xauusd-features-7174d5ab6becbda6\manifest.json"
.\.venv\Scripts\scalpforge-build-multi-hour-features.exe `
  --feature-manifest $features `
  --output-root C:\ScalpForge\data\curated\multi-hour
```

The next stage will create physically separate multi-hour outcome labels. Do not use this artifact to enable real-money execution.

## Stage 2: separate outcomes

Stage 2 requires the schema-revision-2 feature artifact, which retains the first executable bid and ask of each decision bar. Primary outcomes enter on the next bar and cover 1, 2, 4, and 8 hours. Six and twenty-four hours are descriptive diagnostics only. Outcomes include gross returns, executable base returns, 1.5x and 2x slippage stress, five-minute-bar MFE/MAE proxies, and time to those excursions. Any path crossing a missing bar or gap is invalid.

```powershell
$multiHour = Get-ChildItem C:\ScalpForge\data\curated\multi-hour `
  -Recurse -Filter manifest.json |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 -ExpandProperty FullName

.\.venv\Scripts\scalpforge-build-multi-hour-outcomes.exe `
  --feature-manifest $multiHour `
  --output-root C:\ScalpForge\data\curated\multi-hour-outcomes
```

These MFE/MAE values are bar-range proxies rather than tick-exact executable excursions. Stage 3 must not represent them as realizable fills.
