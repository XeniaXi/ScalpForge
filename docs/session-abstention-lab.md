# Session episode and abstention research

The session episode builder produces one deduplicated observation for the first breakout from each
registered range per UTC day. Causal market context is written to `episodes.parquet`; executable
300-second outcomes are written separately to `labels.parquet`. The feature artifact contains no
future return, MFE, MAE, or outcome validity fields.

The abstention lab fits a fixed ridge model inside each anchored walk-forward fold. Missing-value
imputation and scaling use training data only. A small preregistered set of expected-net-utility
thresholds is selected on the validation interval; the outer test interval is used once. If no
threshold has positive validation expectancy or enough observations, that fold abstains entirely.

```powershell
$features = "C:\ScalpForge\data\curated\features\xauusd-features-7174d5ab6becbda6\manifest.json"
$sessions = "C:\ScalpForge\data\curated\session-ranges\xauusd-session-ranges-4f07a15c19934aa1\manifest.json"
$structure = "C:\ScalpForge\data\curated\structure\xauusd-structure-ab21f608bdf2a5c3\manifest.json"
$outcomes = "C:\ScalpForge\data\curated\outcomes\xauusd-outcomes-463a39305b8a639a\manifest.json"

.\.venv\Scripts\scalpforge-build-session-episodes.exe `
  --feature-manifest $features `
  --session-manifest $sessions `
  --structural-manifest $structure `
  --outcome-manifest $outcomes `
  --output-root C:\ScalpForge\data\curated\session-episodes

$episodes = Get-ChildItem C:\ScalpForge\data\curated\session-episodes `
  -Recurse -Filter manifest.json |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 -ExpandProperty FullName

.\.venv\Scripts\scalpforge-run-abstention-lab.exe `
  --episode-manifest $episodes `
  --output-root C:\ScalpForge\outputs\experiments\abstention-lab
```

The model is an interpretable research control, not an execution model. Its final holdout remains
sealed, it cannot enable real-money trading, and a passing report would still require independent
review before demo promotion.
