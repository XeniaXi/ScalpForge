# Point-in-time research dataset

The research table converts ordered executable bid/ask ticks into one row per active UTC second.
It deliberately contains features only; future-return labels are built and joined separately so a
label cannot enter an online feature by accident.

Initial columns include executable bid and ask, mid, spread and trailing spread shock, tick and
quote-change counts, trailing tick-intensity ratio, intrasecond and 1/5/30/60-second returns,
60-second realized volatility, UTC session, elapsed time since the previous active bar, and a gap
start flag. JForex volume is excluded until its unit semantics are validated.

All rolling values use ticks at or before the feature timestamp. Closed-market seconds are not
forward-filled. The first active second after more than five seconds is marked as a gap start.

## RDP workflow

First ingest the complete set of JForex batches:

```powershell
.\.venv\Scripts\scalpforge-ingest-jforex.exe `
    --source-dir "C:\Users\Administrator\JForex4\Strategies\files\ScalpForgeHistorical" `
    --archive-root "C:\ScalpForge\data\raw\historical\dukascopy\jforex" `
    --output-root "C:\ScalpForge\data\curated\external"
```

Copy the `dataset_id` from that command. Build features from its manifest:

```powershell
$datasetId = "PASTE_DATASET_ID"

.\.venv\Scripts\scalpforge-build-features.exe `
    --source-manifest "C:\ScalpForge\data\curated\external\$datasetId\manifest.json" `
    --output-root "C:\ScalpForge\data\curated\features"
```

The result is content-addressed and idempotent. Re-running it with the same immutable source and
configuration returns the same dataset rather than silently replacing it.

The walk-forward API uses anchored training windows and distinct validation/test intervals. Purge
is inserted between training and validation; embargo is inserted between validation and test.
Final holdout selection remains a separate experiment-level decision and is never automated by the
feature builder.
