# Gold strategy hypothesis registry

Community posts, copy profiles, product pages, and repository performance claims are leads, not evidence. ScalpForge independently reimplements only observable mechanisms and judges them with executable prices. It does not copy unlicensed code or treat `AI`, verified profiles, screenshots, or README returns as validation.

## Registered research families

1. **Volatility expansion and persistence.** A statistically abnormal price and volatility expansion may continue when the path remains efficient and liquidity normalizes. Competing hypothesis: unconfirmed liquidity shocks revert.
2. **Higher-timeframe trend and pullback.** A completed H4 trend state followed by an H1 pullback and close back with the trend may continue for several hours.
3. **Displacement/FVG retracement.** A large M15 displacement may leave a price zone whose first causal retracement resumes an independently measured H1 trend. FVG geometry must beat matched displacement events without an FVG.
4. **Structure sweep and rejection.** A sweep of a prior completed multi-hour or daily boundary followed by a close back inside may revert toward the prior range midpoint.
5. **Transparent nonlinear ensemble.** A small boosted-tree model may discover interactions among returns, volatility, structure, session, and regime. It must beat logistic and fixed-rule baselines on identical nested walk-forward splits; all transformations and calibration are train-only.

Simple momentum is a sanity baseline. ORB/Donchian is a registered negative control because close relatives already failed ScalpForge tests.

## Exclusions

- Uncapped grid, martingale, zone recovery, and averaging-down are not alpha candidates.
- Commercial or community `AI` is excluded unless frozen weights, training provenance, timestamped predictions, and inputs can be audited.
- Passive limit fills are optimistic sensitivity tests until queue position and last-look behavior can be modeled.
- Copy-profile profitability cannot establish strategy correctness because floating drawdown, resets, survivorship, latency, broker differences, and setting changes may be hidden.

## Sources used only to generate hypotheses

- [FvgGold-EA](https://github.com/foeed/FvgGold-EA), MIT: inspectable M15 displacement/FVG plus H1 trend concept.
- [XAUBot AI](https://github.com/GifariKemal/xaubot-ai), MIT: feature inventory and ML architecture; published performance is not imported.
- [gold-pro-scalper](https://github.com/n30dyn4m1c/gold-pro-scalper), MIT: cost-aware conditional mean-reversion ideas.
- [GOLD_ORB](https://github.com/yulz008/GOLD_ORB): no visible license; concept only and negative control.
- [MetaTrader signal-copying documentation](https://www.metatrader5.com/en/terminal/help/signals/signal_subscriber): execution depends on latency and provider/subscriber account differences.

## Build implications

Stage 2 keeps 1/2/4/8-hour outcomes primary. Six and twenty-four hours are diagnostic only. It records time-to-MFE/MAE and discloses missing commission, swap, copy-latency, and tick-exact path modeling.

Stage 3 must add causal completed M15/H1/H4 bars, closed-bar indicators, prior daily/multi-hour boundaries, and persistent displacement/FVG zone state. Feature availability must be explicit. Strategy experiments remain separate so a trade cannot simultaneously prove momentum, breakout, and volatility expansion.
