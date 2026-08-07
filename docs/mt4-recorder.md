# MT4 demo quote recorder

`ScalpForgeRecorder.mq4` is a read-only Expert Advisor. It contains no `OrderSend`, `OrderClose`, or account-management calls. It records the currently attached chart symbol only.

## Install

1. Create a demo account with the broker and install its MT4 terminal.
2. In MT4, open **File → Open Data Folder**.
3. Copy `mt4/Experts/ScalpForgeRecorder.mq4` into `MQL4/Experts`.
4. Open MetaEditor, compile the file, and confirm there are no errors.
5. Restart MT4 or refresh Expert Advisors in Navigator.
6. Open the broker's XAU/USD chart. The symbol might be `XAUUSD`, `GOLD`, or have a suffix.
7. Attach `ScalpForgeRecorder` to that chart. AutoTrading is not required because the recorder never trades.
8. Leave the terminal connected. Confirm the Experts log says the recorder is active.

## Output location

The EA uses MT4's `FILE_COMMON` directory. In MT4, open **File → Open Data Folder**, move up to the Terminal directory, then open `Common/Files`.

It creates:

- `scalpforge_<symbol>_<UTC-date>_ticks.csv`: ticks and periodic heartbeats
- `scalpforge_<symbol>_spec.csv`: broker and symbol contract specifications

No account name, balance, credentials, or positions are recorded. Each terminal/EA start receives a
new session ID, and `source_sequence` orders records within that session.

## Compare demos

Copy each broker's CSV to a distinct filename and run:

```bash
scalpforge-compare-feeds data/avatrade_ticks.csv data/broker_b_ticks.csv \
  --output data/broker-comparison.json
```

The initial report compares tick count, coverage, tick frequency, median/p95/p99 spread, gaps, duplicates, and quality errors. Keep terminals on the same Windows machine and attach them during the same period so the comparison is fair.

## Collection protocol

- Start with 48 hours to verify correctness.
- Then record at least 10 trading days from all candidate brokers concurrently.
- Include London open, New York open, daily rollover, and at least one major scheduled US release.
- Do not modify the CSV files. Copy completed snapshots and preserve their SHA-256 manifests.
- This recorder evaluates quote feeds only. Demo-order execution probes will be a separate opt-in component and remain disabled until reviewed.

## Known limitations

- Wall-clock fields have one-second resolution; `monotonic_ms` preserves within-session ordering.
- `GetTickCount()` resets and eventually wraps; `session_id` plus `source_sequence` is the durable ordering key.
- Demo feeds may differ from live feeds.
- A quiet market can legitimately produce apparent gaps; heartbeats distinguish a quiet feed from a disconnected recorder in later analysis.
