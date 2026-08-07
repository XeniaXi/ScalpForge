# Windows RDP deployment (demo data only)

This records broker quotes for research. It does not enable AutoTrading, contain broker credentials,
or place orders.

## Prerequisites

- A dedicated Windows RDP/VPS with stable networking and automatic time synchronization.
- AvaTrade MT4 installed and signed into the demo account.
- Python 3.12 and this repository on the RDP.
- Windows configured not to sleep. Disconnecting RDP is fine; signing out stops MT4.

## Install

1. In MT4, use `File > Open Data Folder` and copy the displayed path.
2. Create the environment and install the repository:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e .
   ```

3. Run PowerShell as the same Windows user that runs MT4:

   ```powershell
   .\ops\windows\Install-ScalpForgeCollector.ps1 `
     -ProjectRoot "C:\ScalpForge" `
     -Mt4DataRoot "C:\Users\YOUR_USER\AppData\Roaming\MetaQuotes\Terminal\TERMINAL_ID"
   ```

4. Compile and attach `ScalpForgeRecorder.mq4` to a `GOLD` chart. Set `OutputPrefix` to
   `scalpforge_v3`. Keep MT4's **AutoTrading button disabled**.
5. Sign out and back in once, or manually start the `ScalpForge-Demo-Collector` task.
6. Run `Test-ScalpForgeRdp.ps1` with the same paths. All checks should be `True`;
   AutoTrading is intentionally not required.

## Operations

- Daily recorder files use UTC dates and survive restarts through session IDs.
- The collector snapshots every minute; each snapshot has a SHA-256 manifest.
- `data/raw/avatrade/GOLD/health.latest.json` reports `healthy`, `stale`, or `missing`.
- During weekends or known market closures, `stale` is expected. During trading hours investigate it.
- Never publish screenshots containing credentials or account identifiers.
- Do not install the execution bridge during Phase 1.
