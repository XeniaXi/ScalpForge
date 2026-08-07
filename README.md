# ScalpForge

ScalpForge is an XAU/USD-first research platform for discovering, replaying, and evaluating short-horizon trading decisions. Its objective is **signal correctness and risk-adjusted expectancy**, not trade frequency, win-rate theatre, or quick-profit targets.

The repository starts in `paper` mode. Live trading is denied by configuration and by the execution boundary; the MT4 adapter is a documented stub only.

## What is included

- normalized market/tick and candle ingestion contracts
- scheduled and unscheduled news/event contracts
- causal-attribution case files linking moves to candidate catalysts
- deterministic historical replay primitives
- feature, regime, and signal-scoring interfaces
- an independent, fail-closed risk governor
- risk-based position sizing and exit plans
- paper execution plus a disabled MT4 bridge
- PostgreSQL persistence, Redis task/event backbone, OpenTelemetry-ready metrics/logging
- experiment records and an append-only decision journal

## Quick start

1. Copy `.env.example` to `.env`.
2. Run `docker compose up --build`.
3. Open `http://localhost:8000/docs` and `http://localhost:8000/health`.
4. Submit a paper decision to `POST /v1/decisions/evaluate`.

Without Docker:

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
pytest
uvicorn scalpforge_api.main:app --reload --app-dir apps/api
```

## Safety invariants

- `TRADING_MODE` defaults to `paper`; `live` is rejected.
- execution cannot bypass the risk governor
- stale/missing prices, invalid stops, excessive spread, drawdown, or daily-loss breaches fail closed
- every evaluation is journaled with inputs, model/config versions, reasons, and outcome status
- secrets are environment-provided and never committed

See [architecture](docs/architecture.md), [implementation plan](docs/implementation-plan.md), and [configuration](config/scalpforge.example.yaml).

Historical CSV files can be quality-checked and assigned a reproducible dataset ID with:

```bash
scalpforge-import-ticks data/ticks.csv --source provider-name --manifest data/manifest.json
```

The importer accepts comma, semicolon, or tab-separated files; case-insensitive bid/ask headers; and `occurred_at`, `timestamp`, `datetime`, or separate MT4-style `DATE`/`TIME` fields. Timestamps must carry an explicit timezone.

Run a deterministic cost-aware backtest with a tick file and a signal-intent file:

```bash
scalpforge-backtest data/xauusd-ticks.csv data/signals.csv \
  --source broker-export --latency-ms 250 --slippage-bps 0.5 \
  --commission-per-lot-side 3.5 --output data/result.json
```

Signal files use the columns `generated_at,side,stop_distance,take_profit_distance,score`. Backtests fill longs at ask and exit at bid (the reverse for shorts), apply adverse slippage, account for both commission sides, reject overlapping positions, and force-close open positions at the final tick.

For the planned rented training machine, see [Alibaba GPU deployment](docs/gpu-deployment.md). The GPU is deliberately isolated from broker credentials and order execution.

Phase 1 demo-feed collection uses the read-only [MT4 recorder](docs/mt4-recorder.md). Compare simultaneous AvaTrade and other broker recordings with:

```bash
scalpforge-compare-feeds data/avatrade_ticks.csv data/other_ticks.csv \
  --output data/broker-comparison.json
```

The recorder rotates its files daily in UTC and uses restart-safe session IDs. A read-only collector
creates SHA-256-addressed snapshots and a health report. See the [Windows RDP setup](docs/RDP_SETUP.md).

Phase 1B adds evidence-only [news and world-event attribution](docs/news-attribution.md). GDELT
reporting and FRED/ALFRED macro vintages are archived with hashes, normalized without sentiment-to-
trade shortcuts, and aligned to broker ticks using explicit reaction windows.

## Repository layout

```text
apps/api       control/query API
apps/worker    ingestion, replay and attribution jobs
packages/core  domain contracts and settings
packages/storage persistence boundary
packages/strategy features, regimes and signal scoring
packages/risk  independent policy and sizing
packages/execution paper broker and MT4 port
docs           architecture and delivery plan
infra          local infrastructure bootstrap
tests          invariant-focused tests
```

## Current status

This is a production-oriented scaffold and executable thin slice, not a proven strategy. No profitability claim is made. Before any future live mode, the system must pass the promotion gates in the implementation plan, including out-of-sample validation, walk-forward testing, execution-cost stress tests, demo soak time, and a separate security/operations review.
