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
