# Shock resiliency and macro-event research

ScalpForge now maintains two distinct research tracks.

The shock-resiliency lab uses the existing annual point-in-time features. A training-only robust threshold detects abnormal five-second moves. To keep annual execution bounded, threshold estimation uses deterministic 60-second samples from each training fold; test episodes still scan every available second. A fixed classifier then fades a recovering/retracing shock, continues a normalized persistent shock, or abstains. Results are compared with fade-all, continue-all, and deterministic random-direction controls at 30, 60, and 300 seconds. Episodes are separated by at least 300 seconds; the final holdout remains sealed.

The macro-event importer is a strict data contract, not a signal generator. It currently allows only CPI and Employment Situation records and requires scheduled time, verified release time, consensus-as-of time before release, initial actual, source URL, and source-snapshot SHA-256. Strategy eligibility requires at least 20 events, at least eight per family, and valid vintage timing. It refuses incomplete evidence rather than backfilling modern values.

Run the shock lab after installing the updated repository:

```powershell
$features = "C:\ScalpForge\data\curated\features\xauusd-features-7174d5ab6becbda6\manifest.json"
$outcomes = "C:\ScalpForge\data\curated\outcomes\xauusd-outcomes-463a39305b8a639a\manifest.json"

.\.venv\Scripts\scalpforge-run-shock-resiliency-lab.exe `
  --feature-manifest $features `
  --outcome-manifest $outcomes `
  --output-root C:\ScalpForge\outputs\experiments\shock-resiliency-lab
```

Macro CSV columns are:

```text
event_id,event_family,scheduled_at_utc,released_at_utc,consensus_as_of_utc,consensus,initial_actual,previous_as_displayed,revised_previous,unit,source_url,snapshot_sha256
```

Do not populate `consensus` from a current webpage unless the provider can prove it is the frozen pre-release vintage.
