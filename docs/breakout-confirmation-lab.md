# Breakout confirmation lab

This lab tests four fixed, point-in-time confirmation rules before applying the previously frozen
`staged_runner` management policy. It does not search thresholds or inspect the final four-day
holdout. The rules progressively test five-second persistence, market quality, directional
alignment, and the complete conjunction including pre-breakout compression.

```powershell
$episodes = "C:\ScalpForge\data\curated\session-episodes\xauusd-session-episodes-711bd66993d5c745\manifest.json"
$features = "C:\ScalpForge\data\curated\features\xauusd-features-7174d5ab6becbda6\manifest.json"

.\.venv\Scripts\scalpforge-run-breakout-confirmation-lab.exe `
  --episode-manifest $episodes `
  --feature-manifest $features `
  --output-root C:\ScalpForge\outputs\experiments\breakout-confirmation-lab
```

No rule is selected automatically. Passing a development gate permits prospective paper shadowing,
not real-money execution.
