# Gated ScalpForge roadmap

Only one evidence phase may promote work into the next. Data collection may continue in parallel,
but it cannot bypass a gate. Research artifacts never authorize execution.

## Phase 1 — Research foundation

Status: complete as infrastructure, rejected as a profitable strategy.

The platform ingests and validates ticks and news, builds immutable point-in-time datasets,
replays executable bid/ask outcomes, and records negative experiments. Five-minute movement near
one-hour structural levels is an opportunity ceiling, not a causal signal.

## Phase 2A — Evidence integrity (active)

1. Causal feature-availability timestamps and next-quote execution.
2. Explicit decision, transport, and broker latency.
3. Episode clustering and event-level sample counts.
4. Immutable experiment registry and exploratory/test/holdout roles.
5. Multiple-testing and block-length sensitivity reports.
6. Corrected feasibility rerun.

Gate: no same-second fills; future perturbations cannot alter prior decisions; all experiments
are registered; July is exploratory; episodes are deduplicated; full validation passes.

## Phase 2B — Data breadth and market truth

Acquire 6–24 months of JForex ticks, overlapping AvaTrade ticks, authoritative scheduled macro
events, and cross-feed event agreement. New evidence is content-addressed and never overwrites
the exploratory artifacts.

Gate: multiple regimes, valid macro timestamps, and no unexplained feed/time/price anomalies.

## Phase 2C — Direction and abstention dataset

One row represents one deduplicated one-hour structural episode. Labels compare executable
five-minute continuation and reversal utility; insufficient advantage becomes `abstain`.

Gate: every feature declares availability and every label remains in a separate future artifact.

Trade-history observation is optional supporting evidence, not a gate. If provider or copied
histories are unavailable, strategy discovery proceeds from immutable tick, macro-event, and news
datasets. Session-range hypotheses use independently implemented, causal Asia, London-open, and
New-York-open ranges; a range is unavailable until its defining window has completely closed.

## Phase 2D — Interpretable models

Begin with regularized logistic regression and shallow boosted trees. Calibrate probabilities on
time-separated validation data and select actions by after-cost expected utility, not accuracy.

Gate: positive lower-bound expectancy, most folds profitable, adequate independent episodes,
calibration monotonicity, simple-control superiority, and doubled-cost survival.

## Phase 2E — GPU research

GPU sequence models are permitted only after a simple model demonstrates reproducible predictive
information. Complexity must improve frozen outer-fold evidence, not merely in-sample fit.

## Phase 3 — Production-like demo

Add idempotent intents, acknowledgement/rejection/partial-fill state, reconciliation, persistent
kill switches, restart recovery, and measured demo latency/slippage. No live credentials.

## Phase 4 — Operational hardening

Add central telemetry, alerts, disk/clock/feed monitoring, incident runbooks, account-level risk,
and disconnect/duplicate/stale-state failure injection.

## Phase 5 — Independent live-readiness review

Requires frozen multi-regime evidence, independent holdout success, weeks of uninterrupted demo,
broker-specific execution measurements, tested shutdown controls, and explicit authorization.
