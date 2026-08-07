# Initial implementation plan

## Phase 0 — foundations (current scaffold)

- domain contracts and safe configuration
- paper decision vertical slice
- independent risk checks and sizing
- TimescaleDB/Redis local environment
- architecture, operational invariants, and tests

Exit: repository builds, tests pass, live execution is impossible.

## Phase 1 — trustworthy data and replay

- implement broker tick/candle connector with sequence-gap monitoring
- ingest an economic calendar plus timestamped headline provider
- persist raw payloads, normalized events, revisions, and provenance
- build deterministic virtual-clock replay with spread/slippage/latency models
- implement dataset manifests, quality reports, and leakage tests
- create causal case files around 5s/30s/1m/5m/15m reaction windows

Exit: repeatable replay from a versioned dataset; no future information can cross the replay clock.

Implemented so far: provider-neutral tick CSV normalization, quality reports, content-addressed manifests, deterministic event-time replay, and a cost-aware tick execution simulator. Durable raw Parquet storage and live provider connectors remain outstanding.

The broker-validation portion now includes a read-only MT4 tick/specification recorder and objective feed-comparison reporting. Demo execution probes remain deliberately separate and disabled.

## Phase 2 — research baseline

- point-in-time microstructure, volatility, session, cross-asset, and news-surprise features
- interpretable regime baseline and calibrated signal scorer
- attribution ranking using timing, relevance, surprise, DXY/yield confirmation, and counterfactual baselines
- walk-forward experiments with purging/embargo and cost stress tests
- expectancy, calibration, drawdown, tail-loss, turnover, and abstention metrics

Exit: frozen out-of-sample report beats simple baselines after conservative costs.

## Phase 3 — demo execution

- Windows-side demo MT4 bridge with authenticated, idempotent messages
- reconciliation, heartbeats, circuit breakers, partial-fill handling, exit manager
- append-only SQL journal, dashboards, alerts, runbooks, backup/restore drills
- shadow mode followed by a minimum 30-day demo soak across market regimes

Exit: zero unreconciled orders, tested kill switch, acceptable latency and operational error budget.

## Phase 4 — live-readiness review (not authorization to trade)

- independent security, model-risk, and operational review
- broker/legal/data-license review
- strict capital and exposure limits; canary rollout and rollback plan
- explicit human approval and separate live credentials/configuration

No live connection is implemented by this plan automatically.

## First backlog

1. Alembic migrations and async PostgreSQL repositories.
2. Redis Streams event envelope with schema version and idempotency key.
3. CSV/Parquet historical tick importer plus data-quality CLI.
4. Virtual clock and event-driven replay engine.
5. Economic-release surprise normalization and reaction-window builder.
6. Movement case attribution service with `unknown` calibration.
7. MLflow-compatible experiment adapter and dataset manifest.
8. Prometheus metrics, traces, structured audit logging, and alert rules.
