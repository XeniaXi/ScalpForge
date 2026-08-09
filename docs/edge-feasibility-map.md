# Edge Feasibility Map

This report determines where XAU/USD movement is large enough to justify further strategy
research. It does not select parameters, promote a signal, or submit orders.

## Comparisons

- Prior-level windows: 60, 300, 900, and 3,600 seconds.
- Exit horizons: 5, 15, 30, 60, and 300 seconds.
- Causal directions: breakout continuation and breakout reversal.
- Slippage stress: 0, 0.25, 0.5, and 1 basis point per side, always crossing bid/ask.
- Clearance targets: net 1, 2, 3, and 5 bps within 300 seconds.
- An endpoint oracle reports the better direction after the fact. It is explicitly non-tradable
  and is used only as an upper-bound feasibility diagnostic.

All observations are spaced by at least 60 seconds, contained within walk-forward test folds,
and excluded from the final four-day holdout. Block-bootstrap intervals preserve local trade
clustering.

## RDP command

```powershell
Set-Location C:\ScalpForge
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

$features = "C:\ScalpForge\data\curated\features\xauusd-features-3ab3baecb4d4534c\manifest.json"
$structure = "C:\ScalpForge\data\curated\structure\xauusd-structure-6a2aeed7e2cef605\manifest.json"

.\.venv\Scripts\scalpforge-run-feasibility-map.exe `
    --feature-manifest $features `
    --structural-manifest $structure `
    --output-root C:\ScalpForge\outputs\experiments\feasibility
```

The JSON report is intentionally detailed. Save it and inspect the top causal combinations;
an oracle result must never be represented as a strategy result.

## Historical expansion

The present July-to-August sample can reject weak hypotheses but cannot establish durability.
Continue exporting JForex history in bounded hourly batches until at least six months are
available; 12–24 months is preferred. Preserve every CSV and manifest, resume from checkpoint,
and consolidate only after batch validation reports no checksum or timestamp errors. Rebuild
features, structure, outcomes, and reports as new content-addressed datasets—never overwrite
the current evidence.
