# Historical XAU/USD data

ScalpForge separates research history from broker-native execution evidence. External prices may be
used for hypothesis discovery and replay engineering, but they are never treated as executable
AvaTrade quotes.

## Supported first sources

1. Export AvaTrade GOLD M1 history from MT4 History Center for broker-specific long-range context.
2. Export bid/ask tick CSV from a historical provider such as Dukascopy for microstructure research.
3. Keep the original download outside Git under `data/raw/historical/<provider>/`.

Every import requires an explicit provider, venue, and source timezone. Never guess whether an
export is UTC or broker-server time.

Dukascopy may export the quote stream as separate ASK and BID CSV files. Merge only a matching pair:

```powershell
.\.venv\Scripts\scalpforge-merge-dukascopy.exe `
  --ask C:\path\XAU-USD_ASK.csv `
  --bid C:\path\XAU-USD_BID.csv `
  --output C:\ScalpForge\data\raw\historical\dukascopy\xauusd_ticks.csv
```

The merger streams both files in lockstep and refuses different row counts, timestamp mismatches,
crossed quotes, or non-flat OHLC bars. Its sidecar manifest preserves both source checksums.

```powershell
.\.venv\Scripts\scalpforge-import-ticks.exe `
  C:\ScalpForge\data\raw\historical\dukascopy\xauusd.csv `
  --source dukascopy `
  --venue SWFX `
  --instrument XAUUSD `
  --source-timezone UTC `
  --normalize-to C:\ScalpForge\data\curated\external
```

The command validates the complete ordered source before writing, streams it into daily
Zstandard-compressed Parquet partitions, and writes a content-addressed manifest containing the
source checksum and timestamp assumption. The manifest marks all rows
`external_non_executable=true`.

Use `--price-scale` only when the provider documents an integer price encoding. A repeat import with
identical bytes and assumptions resolves to the same dataset ID.

The replay layer opens the generated `manifest.json`, confines every referenced partition to that
dataset directory, and emits ordered `MarketTick` events using the original provider and venue.
Parquet timestamps are stored as timezone-neutral canonical UTC values for cross-platform
compatibility; the replay reader restores explicit UTC before emitting events.

## Acceptance gates

- Bid and ask must be positive and ask must not be below bid.
- Timestamps must be ordered and normalized to UTC.
- Naive timestamps require an explicit timezone assumption.
- Provider and venue provenance must be non-empty.
- The immutable source SHA-256 must be recorded.
- External data cannot calibrate AvaTrade fill quality, slippage, or actual spread costs.
