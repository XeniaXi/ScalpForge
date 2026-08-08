# Competitive evidence: multi-strategy Gold EA

Screenshots supplied by the project owner appear to show an MT4 Gold EA labelled “The Gold Reaper
V3.3,” with “extreme” trade frequency, at least nine separately reported strategies, small displayed
lot sizes, a 30% maximum allowed drawdown, and copy-trading performance claims.

The owner additionally reports that the observed EA targets up to roughly $10 profit, while copied
variants are modified to target roughly $1. This suggests exit-policy cloning/tuning, not evidence
that either cash target is optimal.

This is unverified observational evidence. Screenshots do not establish audited returns, deposits,
withdrawals, exposure, survivorship, execution quality, or whether all images represent the same
account and period. ScalpForge must not copy unknown logic or treat headline gain as validation.

## Useful design hypotheses

- Evaluate multiple transparent strategy families rather than searching for one universal rule.
- Give every strategy an isolated virtual ledger on identical point-in-time replay data.
- Record all opportunities, including abstentions and risk rejections.
- Measure expectancy by regime, session, news context, costs, and correlation with other strategies.
- Introduce an ensemble selector only after frozen out-of-sample evidence.
- Treat `$1` and `$10` exits as named experimental variants. Compare them using return in basis
  points, reward-to-risk (`R`), holding time, adverse excursion, costs, and account/lot scaling.
- Do not optimize for trade frequency or impose an arbitrary low trade count. Enforce opportunity
  quality, aggregate exposure, cost, correlation, and independent risk constraints.

## Explicit non-goals

- A 30% drawdown allowance is not adopted.
- Percentage gain and time-to-target are not optimization objectives.
- Fixed cash profit targets are not portable across balance, lot size, contract, spread, or regime.
- Martingale, grid recovery, averaging into losses, and forced trading remain prohibited.
