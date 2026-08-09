# Causal research refresh

The Phase 2A timing corrections change feature and outcome identities. Existing July research
artifacts remain useful as an audit trail, but must not be compared directly with revision 3
reports.

On the RDP, pull and reinstall the package before starting the refresh:

```powershell
Set-Location C:\ScalpForge
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Then run the restart-safe research chain against the curated JForex source manifest:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File C:\ScalpForge\ops\windows\Invoke-ScalpForgeResearchRefresh.ps1 `
  -ProjectRoot C:\ScalpForge `
  -SourceManifest C:\ScalpForge\data\curated\external\xauusd-jforex-d268673170cefd66\manifest.json
```

The chain rebuilds point-in-time features, causal executable outcomes, structural features, and
the structural, sequence, and feasibility reports. Content-addressed stages that completed
successfully are reused if the command is run again. The final holdout remains sealed and every
lab remains research-only.
