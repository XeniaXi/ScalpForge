# Causal path-management lab

This lab tests whether entry timing and deterministic exits can preserve the weak gross session-breakout signal after executable bid/ask costs. It is research-only and never sends orders.

The candidate family is fixed in code before evaluation: immediate market entry or bounded pullback entry; 60/180/300/900-second maximum holding periods; and 4/8/12-bps stops and targets. Validation selects at most one policy per walk-forward fold. The policy is then scored once on that fold's unseen test period. A fold may abstain entirely. The final four-day holdout remains sealed.

Install the updated package, then run on the RDP:

```powershell
Set-Location C:\ScalpForge
python -m pip install -e .

$episodes = "C:\ScalpForge\data\curated\session-episodes\xauusd-session-episodes-711bd66993d5c745\manifest.json"
$features = "C:\ScalpForge\data\curated\features\xauusd-features-7174d5ab6becbda6\manifest.json"

.\.venv\Scripts\scalpforge-run-path-management-lab.exe `
  --episode-manifest $episodes `
  --feature-manifest $features `
  --output-root C:\ScalpForge\outputs\experiments\path-management-lab
```

Passing requires at least 100 unseen test trades, six active folds, a positive block-bootstrap lower bound, profitability at 1.5x cost, non-negative expectancy at 2x cost, at least 60% profitable active folds, and profit factor of at least 1.15. Passing is a research milestone, not authorization for real-money trading.
