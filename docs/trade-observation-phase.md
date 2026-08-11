# Phase A: strategy observation and provenance

Phase A imports authorized, anonymized MT4/MT5 account-history CSV files into a canonical,
research-only dataset. It is designed to compare a provider/master account, a copied account,
and manual exit modifications without storing credentials or enabling execution.

The importer records source role, anonymized account alias, broker, timestamps normalized to UTC,
entry and exit origin, ticket, symbol, side, volume, prices, recorded SL/TP, profit, commission,
swap, duration, magic number, and a one-way comment hash. Raw comments are not retained because
they may contain account-identifying text. Unknown origins remain explicitly unknown; they are not
silently classified as provider or manual behavior.

Do not export passwords, account numbers, email addresses, API tokens, or investor credentials.
Use an alias such as `friend-copy-a`. Preserve the original export privately outside Git.

```powershell
.\.venv\Scripts\scalpforge-import-trade-history.exe `
  C:\PrivateData\friend-history.csv `
  --output-root C:\ScalpForge\data\curated\trade-observations `
  --source-system mt4 `
  --source-role copied_account `
  --account-alias friend-copy-a `
  --broker "Ava Trade Ltd." `
  --source-timezone UTC `
  --entry-origin copier `
  --exit-origin manual
```

Source data and normalized observations remain outside Git. The generated manifest is immutable,
content-addressed, credential-free, research-only, and non-executable.

GOLD_ORB is treated as an independently reimplemented hypothesis, not copied source. Its useful
research concepts are a session-defined H1 range, a minimum consolidation count, one long and one
short opportunity per day, fixed SL/TP, trailing protection, dynamic sizing, drawdown stops,
losing-streak suspension, and virtual shadow trading. The upstream repository does not state an
open-source license, and its published code and documentation are not evidence of profitability.

## Paired trade-management lab

After importing one `provider_master` history and one `copied_account` history, run:

```powershell
.\.venv\Scripts\scalpforge-run-trade-management-lab.exe `
  --provider-manifest C:\PrivateData\provider\manifest.json `
  --copied-manifest C:\PrivateData\copied\manifest.json `
  --output-root C:\ScalpForge\outputs\experiments\trade-management
```

Matching is deterministic and one-to-one. It uses only instrument, side, and entry-time proximity;
profit and exit data never influence which trades are paired. The report decomposes each pair into
provider entry/provider exit, copied entry/provider exit, provider entry/copied exit, and copied
entry/copied exit paths. This estimates copier-entry and exit-management effects in basis points.
It does not treat reported dollar profit as comparable across brokers or lot sizes.

The default 30-pair minimum is only a sample sufficiency marker, not a profitability gate. Results
remain descriptive, holdout-sealed, research-only, and incapable of enabling real-money trading.
