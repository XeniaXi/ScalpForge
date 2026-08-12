# Causal regime-gate lab

This research-only lab tests 13 fixed, interpretable participation gates against the corrected session-episode dataset. It does not tune exits or place orders.

The gates cover session, spread, activity, volatility, session-range width, short-term trend alignment, and Tuesday-through-Thursday participation. Numeric thresholds are medians computed from each fold's training features only. Every gate is reported; test results never select a gate. The final four-day holdout remains sealed.

Historical macro and news gates are intentionally excluded until their timestamps and annual coverage pass a separate availability audit. This prevents recently collected GDELT data from being backfilled or treated as if it existed throughout the annual sample.

```powershell
Set-Location C:\ScalpForge
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .

$episodes = "C:\ScalpForge\data\curated\session-episodes\xauusd-session-episodes-711bd66993d5c745\manifest.json"

.\.venv\Scripts\scalpforge-run-regime-gate-lab.exe `
  --episode-manifest $episodes `
  --output-root C:\ScalpForge\outputs\experiments\regime-gate-lab
```
