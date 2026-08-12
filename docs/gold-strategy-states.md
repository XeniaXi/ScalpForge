# Causal gold strategy states

Stage 3 derives label-free M15, H1, and H4 state from the five-minute Stage 1 artifact. A higher-timeframe bar enters the state only after it has closed. The artifact includes:

- completed M15 ATR and displacement strength;
- completed H1 fast/slow EMA trend and ATR;
- completed H4 return;
- persistent M15 three-candle FVG zones with expiry and mitigation state;
- four-hour prior-boundary rejection tags;
- existing volatility-expansion and path-efficiency context.

These are independent mechanism tags, not trading signals or proof of profitability. FVG rules are independently implemented from a public hypothesis and do not copy third-party source code.

```powershell
$multiHour = "C:\ScalpForge\data\curated\multi-hour\xauusd-multi-hour-375348bef6918ef6\manifest.json"

.\.venv\Scripts\scalpforge-build-gold-strategy-states.exe `
  --feature-manifest $multiHour `
  --output-root C:\ScalpForge\data\curated\gold-strategy-states
```

The result remains development-only, contains no outcome labels, and cannot enable real-money execution.
