# News and world-event attribution

This subsystem stores evidence for research. It cannot submit orders, bypass the risk governor, or
turn headline tone directly into a signal.

## Sources

- GDELT DOC API supplies timestamped reporting about gold, monetary policy, yields, inflation,
  conflict, sanctions, and systemic risk.
- FRED/ALFRED supplies macro observations and historical vintages. A future licensed calendar feed
  will add consensus forecasts and exact release-time actuals.
- Broker ticks supply the observed XAU/USD reaction.

Every raw response is archived with its ingestion time and SHA-256. Normalized records retain event,
publication, and receipt times separately to prevent look-ahead leakage. Event keys make ingestion
idempotent. FRED observation dates and GDELT seen-times are explicitly ineligible for precise
intraday reaction measurement. That requires a source with a verified release timestamp.

## Attribution discipline

An event near a price move is a candidate cause, not proof. Reaction measurements at 5, 30, 60,
300, and 900 seconds are stored as `candidate_only`. Promotion requires corroboration from multiple
independent sources and related markets such as DXY and Treasury yields.

## Commands

Recent global reporting (no API key):

```powershell
scalpforge-ingest-news gdelt --max-records 250
```

Historical macro vintages require a FRED API key supplied only as an environment variable:

```powershell
$env:FRED_API_KEY = "your-key"
scalpforge-ingest-news fred --series-id CPIAUCSL --title "US CPI inflation" `
  --realtime-start 2026-01-01 --realtime-end 2026-08-07
```

Do not commit raw news payloads or normalized datasets. The repository ignores `data/*`.

## Windows RDP schedule

Install a research-only polling task (15 minutes by default):

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\ops\windows\Install-ScalpForgeNews.ps1 `
  -ProjectRoot C:\ScalpForge
Start-ScheduledTask -TaskName "ScalpForge-News-Collector"
```

GDELT may return HTTP 429 when its public service is busy. The scheduled task will try again at the
next interval; never increase request frequency in response to throttling.
