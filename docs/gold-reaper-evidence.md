# Gold Reaper evidence review

Reviewed 2026-08-08. This note distinguishes disclosed product mechanics from marketing,
testimonials, clones, and performance claims. It does not copy proprietary code or assert that the
product is profitable.

## Identity and disclosed design

The likely inspiration is Profalgo/Wim Schrynemakers' **The Gold Reaper**, published for MT4 and
MT5 in February 2024. Public product material describes XAUUSD breakout trading across multiple
timeframes, up to nine selectable internal strategies, selectable/automatic trade frequency, and
multiple entry-confirmation algorithms.

Later release notes and setup material disclose several important mechanisms:

- support/resistance breakout variants grouped by risk;
- a candle-close fake-breakout filter;
- high/low trailing-stop management;
- spread and slippage controls;
- an NFP filter that can close positions and remove pending orders;
- variable price-level scaling of entries, stops, and exits;
- lot-size adjustment as stop distance and the price of gold change;
- weighted sizing intended to balance strategies by historical drawdown;
- daily/total drawdown settings and lower-risk prop-firm configurations.

This is evidence for a *design family*, not enough information to reproduce its rules exactly.

## Why the design can plausibly have edge

Gold frequently moves from compression into persistent, high-volatility repricing. Stop-entry
breakouts can participate in those right-tail moves without predicting direction. Multiple time
windows can capture different compression and session structures. Confirmation can reduce false
breakouts, while trailing exits allow occasional large winners to offset many small failed entries.
Normalizing distances and size to the current gold price helps prevent an old fixed-dollar setup
from silently taking more risk as XAUUSD's nominal level changes.

Portfolio construction may make the equity curve appear steadier when the nine variants trigger at
different times. That benefit disappears when supposedly independent variants become correlated
during the same shock, so correlation must be measured out of sample rather than assumed.

## What the public evidence does and does not establish

- The product has existed only since February 2024. Claims that the exact current version has
  worked unchanged for many years are impossible: material changes added news filtering, fake-
  breakout filtering, variable-value scaling, weighted sizing, and lot-size fixes through 2026.
- One third-party page reports 135 closed trades from March-June 2026, 72.6% winners, profit factor
  1.97, and 5.56% maximum balance drawdown. That is a limited sample published by an affiliate-like
  review site, not an independent long-horizon audit.
- A 2024 academic case study reports attractive demo/free-version results, but calls its figures
  authors' projections and does not provide enough reproducible broker, cost, period, parameter,
  or walk-forward detail for model validation.
- A Myfxbook page named Gold Reaper reports a loss and 37.65% drawdown, but it is attributed to a
  different account owner and cannot be proven to be the same product/version/settings.
- Product comments include users reporting drawdown beyond the configured/historical expectation.
  The developer explains that the setting is based on historical component drawdowns. Therefore it
  must not be interpreted as a hard realized-loss guarantee.
- Reviews and community longevity show adoption and support, not expectancy. Survivorship,
  affiliate incentives, parameter differences, broker differences, deposits/withdrawals, and
  unpublished failed accounts remain material confounders.

There was no strong new independent 30-day evidence found that changes this assessment.

## ScalpForge decisions derived from the evidence

Adopt and test:

1. Multiple independently journaled breakout hypotheses across sessions and horizons.
2. Price-level/volatility-normalized entry, stop, and exit distances.
3. Executable fake-breakout confirmation measured against the cost of later entry.
4. High/low and volatility trailing exits as competing, frozen exit policies.
5. Scheduled-event controls broader than NFP, with event-specific abstain/continue experiments.
6. Spread, latency, and slippage rejection at signal time and cost-stressed replay.
7. Portfolio sizing informed by out-of-sample covariance and tail co-loss, not strategy count.
8. Broker-feed reconciliation and version-frozen walk-forward evaluation.

Reject:

1. A historical or requested drawdown percentage masquerading as a guaranteed maximum.
2. A default 30% drawdown budget.
3. Sizing that refuses to decrease after losses (`OnlyUp`).
4. Fixed $1/$10 profit targets independent of volatility, spread, and account risk.
5. Combining nine correlated variants and calling the result diversified without evidence.
6. Updating rules after drawdowns and presenting the combined history as one unchanged strategy.
7. Treating testimonials, screenshots, or a single account as promotion evidence.

The Gold Reaper architecture strengthens the case for ScalpForge's breakout, compression,
news-aware, and portfolio laboratories. It does not replace the promotion gate: positive
walk-forward expectancy after doubled costs, stable folds, controlled drawdown, untouched holdout,
and extended demo execution remain mandatory.
