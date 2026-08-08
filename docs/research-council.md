# ScalpForge research council

The council is a set of competing, falsifiable research mandates. It is not a vote that can
override the independent risk governor, and it is not evidence that any strategy is profitable.

Nine advisors investigate distinct candidate edges: trend continuation, short-horizon momentum,
range mean reversion, compression breakout, volatility expansion, liquidity reversal,
microstructure pressure, news reaction, and post-news reversal. The tenth advisor is deliberately
skeptical: it vetoes promotion when lineage, leakage, costs, stability, sample size, or drawdown do
not meet the shared standard.

## Experiment protocol

1. Freeze the hypothesis, features, eligible regimes, exits, costs, and rejection conditions.
2. Build features only from information available at the replay clock.
3. Use purged, embargoed walk-forward folds; strategies share the same folds and market data.
4. Fit thresholds on training data, select once on validation data, and leave the final holdout
   untouched until the experiment is frozen.
5. Replay executable bid/ask prices with measured latency, spread, slippage, and commissions.
6. Record every opportunity, abstention, rejection, fill, exit, and later outcome.
7. Report expectancy in R and basis points, confidence intervals, calibration, drawdown, tail loss,
   turnover, regime/session breakdowns, and performance at 1x and 2x estimated costs.
8. Compare against no-trade, random-time/direction, and simple momentum/mean-reversion baselines.
9. Correct for testing many variants and report failed experiments; do not retain only winners.
10. Promotion merely permits shadow/demo evaluation. It never enables real-money execution.

The initial software gate requires positive net expectancy, at least 95% estimated confidence of
positive expectancy, at least 200 out-of-sample trades, four walk-forward folds with at least 75%
profitable, maximum drawdown no greater than 5%, and survival at twice estimated costs. These are
minimum research gates, not guarantees, and may become stricter as evidence accumulates.

Fixed cash-profit targets such as $1 or $10 are not strategy definitions. They vary with lot size,
account currency, volatility, and price level. ScalpForge expresses exits in basis points, risk
units, time, and market-state invalidation, then calculates cash outcomes from constrained sizing.
