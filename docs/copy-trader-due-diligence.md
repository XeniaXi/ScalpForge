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
