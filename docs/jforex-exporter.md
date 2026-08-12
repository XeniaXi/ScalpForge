# Read-only JForex historical exporter

`ScalpForgeHistoricalExporter.java` retrieves paired best bid/ask `ITick` history through
`IHistory.getTicks`. It never obtains `IEngine` and contains no order operation.

`ScalpForgeMarketHoursExporter.java` separately exports instrument-specific offline
domains using `IDataService.getOfflineTimeDomains(from, to, instrument)`. Its default
range covers Candidate A development data from 2025-11-01 through 2026-05-01 UTC.
The CSV and SHA-256 manifest are written beneath
`Strategies\files\ScalpForgeMarketHours`. Optional observer mode records future
`IInstrumentStatusMessage` tradability changes; it cannot reconstruct past status
messages and is disabled by default.

## Installation

In JForex, open **Settings > Preferences** and note the Strategies path. Install the source using
that exact path:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File C:\ScalpForge\ops\windows\Install-ScalpForgeJForexExporter.ps1 `
  -ProjectRoot C:\ScalpForge `
  -JForexStrategiesRoot "C:\Users\Administrator\AppData\Local\JForex\Strategies"
```

The path above is only an example; the JForex Preferences value is authoritative. Refresh the
Strategies panel, open `ScalpForgeHistoricalExporter.java`, and compile it in JForex.

## First validation run

- Instrument: `XAU/USD`
- Start UTC: `2026-08-03 12:00:00`
- End UTC exclusive: `2026-08-03 13:00:00`
- Batch minutes: `60`
- Output folder: `ScalpForgeHistorical`

JForex writes beneath its approved `IContext.getFilesDir()` directory and prints the full output
directory in Messages. Each batch contains canonical CSV, SHA-256 manifest, and resume checkpoint.
Inclusive provider endpoints are converted to exclusive ScalpForge batches to prevent overlap.

Compare the first run with dataset `xauusd-history-d3d368b3a492a9e2`. Expand the range only after
row counts, timestamps, prices, and spreads reconcile.

## Safety invariants

- No `IEngine`, order command, credential handling, or live execution.
- UTC input and millisecond UTC output.
- One bounded history request per configured batch.
- Rejects out-of-range, out-of-order, and crossed quotes.
- Writes `.partial` before atomically publishing CSV.
- Advances checkpoint only after CSV and manifest creation.
- Marks every export `read_only` and `external_non_executable`.

## Batch ingestion

After a bounded export completes, validate, archive, and consolidate every batch:

```powershell
.\.venv\Scripts\scalpforge-ingest-jforex.exe `
  --source-dir "C:\Users\Administrator\JForex4\Strategies\files\ScalpForgeHistorical" `
  --archive-root "C:\ScalpForge\data\raw\historical\dukascopy\jforex" `
  --output-root "C:\ScalpForge\data\curated\external"
```

The command verifies manifest contracts, checksums, row counts, quote ordering and batch overlap
before writing. Empty batches are archived as session evidence. JForex volume values are retained as
`jforex_native_unknown` and must not be used as features until their units are reconciled.

When disk space cannot hold both the JForex export and a second raw archive copy, use the validated
source directory as the raw archive in place by adding `--reference-source`. This mode still checks
every manifest, checksum, row count, interval, and quote. It does not copy or delete source files;
the resulting manifest references their existing paths, which must remain intact until backed up.
