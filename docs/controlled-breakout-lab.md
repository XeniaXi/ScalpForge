# Controlled breakout-episode lab

This confirmatory lab tests whether session-range breakout expectancy comes from entry staging,
runner management, both, or neither. It consumes the point-in-time session episodes and executable
bid/ask features; labels are never loaded. The final four days remain sealed.

The four frozen variants are `single_fixed`, `single_runner`, `staged_fixed`, and `staged_runner`.
Staging uses equal-sized entries at 4 and 8 adverse basis points with three tickets maximum. Every
variant has a quick-failure exit and a hard 20-basis-point basket stop. Runner variants take half at
8 basis points and trail the remainder by 6 basis points. Costs are charged per ticket and stressed
at 1.5x and 2x.

No winning variant is selected automatically. A variant must independently clear the research gate
before it can be considered for a new, prospective paper-shadow experiment.

```powershell
$episodes = "C:\ScalpForge\data\curated\session-episodes\xauusd-session-episodes-711bd66993d5c745\manifest.json"
$features = "C:\ScalpForge\data\curated\features\xauusd-features-7174d5ab6becbda6\manifest.json"

.\.venv\Scripts\scalpforge-run-controlled-breakout-lab.exe `
  --episode-manifest $episodes `
  --feature-manifest $features `
  --output-root C:\ScalpForge\outputs\experiments\controlled-breakout-lab
```

The output remains research-only and cannot enable real-money execution.
