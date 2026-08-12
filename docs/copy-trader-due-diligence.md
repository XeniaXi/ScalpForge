# Copy-trader due diligence

Copy-platform histories are an external research source, not an execution signal. ScalpForge
does not rank providers by headline return and does not connect this module to live execution.

## Input contract

Export one provider per UTF-8 CSV with these columns:

```csv
provider_id,opened_at,closed_at,symbol,side,volume,net_profit
provider-123,2024-01-02T09:00:00Z,2024-01-02T09:12:00Z,XAUUSD,buy,0.01,4.20
```

Timestamps must contain an offset. `net_profit` must include commissions, swaps and platform
fees where the source provides them. Preserve the original export outside Git and record the
platform, account verification status, export time, currency and stated starting equity in the
research journal.

## Run

For an MQL5 semicolon export, normalize it first. Confirm the displayed history's server-time
offset before running; do not guess it when event timing matters:

```powershell
.\.venv\Scripts\scalpforge-normalize-mql5-history.exe `
  C:\ScalpForge\data\raw\copy-traders\mql5-export.csv `
  --output C:\ScalpForge\data\normalized\copy-traders\provider-123.csv `
  --provider-id provider-123 `
  --source-utc-offset-hours 2
```

The converter handles MQL5's duplicate `Time` and `Volume` headers, excludes balance rows, and
adds listed commission and swap to profit. Keep the original export for provenance.

Then audit the normalized history:

```powershell
.\.venv\Scripts\scalpforge-audit-copy-trader.exe `
  C:\ScalpForge\data\raw\copy-traders\provider-123.csv `
  --starting-equity 10000 `
  --output-root C:\ScalpForge\outputs\copy-trader-audits
```

The initial gate requires 730 days, 200 closed trades, profit factor of at least 1.15, maximum
drawdown no greater than 20% of stated starting equity, at least 55% profitable months, limited
dependence on the largest win, and no median position-size escalation above 1.25 after losses.
Passing means **eligible for prospective paper shadowing only**. It is not evidence that the
provider will remain profitable and never enables real-money execution.

## Known limitations

- Incomplete exports can hide open losses, deleted providers and failed accounts.
- Starting equity, deposits and withdrawals materially affect drawdown calculations.
- Trade-level history may not reproduce copier slippage, latency or rejected orders.
- The size-after-loss test is a warning heuristic, not proof of martingale behaviour.
- Candidate selection from a current leaderboard has survivorship and multiple-testing bias.

Before paper shadowing, manually verify live-account status, deposits/withdrawals, floating
drawdown, platform regulation, Nigerian eligibility, and whether the provider permits copying.

## Cash-flow and episode-aware lab

Run this against each original, unmerged MQL5 export. It retains deposits and withdrawals for
closed-balance reconstruction and joins overlapping tickets with the same symbol and direction
into one risk episode:

```powershell
.\.venv\Scripts\scalpforge-run-copy-episode-lab.exe `
  C:\ScalpForge\data\raw\copy-traders\provider-detailed.csv `
  --provider-id provider-detailed `
  --source-utc-offset-hours 2 `
  --output-root C:\ScalpForge\outputs\copy-trader-episode-audits
```

Its drawdown remains a closed-balance statistic. MQL5 trade-history exports do not reveal the
worst floating equity while positions were open, so the lab never grants paper eligibility by
itself and cannot authorize live execution.
