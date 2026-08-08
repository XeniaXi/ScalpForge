# Phase 1 quality toolkit

The quality toolkit reads immutable MT4/news archives and never accesses broker credentials or
execution functions.

## Health semantics

The GOLD collector distinguishes:

- `healthy`: market expected open and recorder heartbeat is fresh;
- `market_closed_heartbeat_healthy`: configured weekly closure and fresh heartbeat;
- `stale`: heartbeat is too old;
- `missing`: expected daily recorder file is absent.

The weekly schedule is conservative (Friday 22:00 UTC through Sunday 22:00 UTC). Broker holidays
remain a separate calendar input and must not be inferred from missing ticks.

## Reports

```powershell
scalpforge-quality ticks
scalpforge-quality news
```

Tick reports select the largest checksum-valid cumulative snapshot for each source day. They report
active coverage, tick counts, frequency, spreads, gaps, duplicates, and invalid manifests. Status
progresses from `collecting_under_24h` to `ready_for_24h_checkpoint` at 20 observed hours and
`ready_for_48h_review` at 40 observed hours across at least two active days.

News reports measure unique events, sources, relevance categories, timing quality, raw archive
integrity, and current provider health.

Install hourly reporting on the Windows RDP:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\ops\windows\Install-ScalpForgeQuality.ps1 `
  -ProjectRoot C:\ScalpForge
```

## Replay-ready Parquet

Create a content-addressed, deduplicated dataset only after reviewing source quality:

```powershell
scalpforge-quality dataset
```

The command never edits raw CSV files. It writes Zstandard-compressed daily Parquet partitions and
a manifest under `data/curated/ticks/<dataset-id>/`. Re-running identical inputs resolves to the same
dataset ID.
