# Executive Memo — AMARO Trading System

**To:** Client · **Date:** 18 October 2026 · **Re:** Status of $20,000 USD allocation

---

## Bottom line

**Your $20,000 is held in full. $0 has been placed in any market.** Five trading
strategies were built and tested to completion; none produced a statistically
significant edge. A screen of the entire investable universe then confirmed there
is no validated candidate to deploy. The capital is held because the evidence
says the system does not yet work — not because testing is incomplete.

| | |
|---|---|
| Capital deployed | **$0** |
| Capital held | **$20,000 USD** |
| Strategies tested to completion | 5 |
| Strategies clearing the evidence bar | **0** |
| Capital-readiness gates met | **0 of 6** |

## What the testing showed

The most recent test, a NAS100 index momentum strategy, returned **+24.1% across
436 trades while the index itself rose +102.8%** — under a quarter of simply
holding it, after paying costs on 436 round trips. Tested at two sample sizes as
data accumulated, every metric moved against it. Detail in the addendum to the
full report.

Live paper trading (real prices, simulated fills) currently stands at a **profit
factor of 0.374** across 21 trades — the system returned 37 cents for every dollar
lost, with a **net −8.96%**. Historical testing explains why: the core entry
signal, tested across 1,850 trades, produced a profit factor of 0.989, which is a
coin flip before costs. No strategy reached statistical significance; the best
p-value was 0.117 against a required 0.05.

## The six gates that protected the capital

Each gate is objective and measured on forward-test data only — trades placed
after a strategy is locked, never on historical simulation.

| # | Gate | Threshold | Current |
|---|---|---|---|
| 1 | Continuous data | 30 days unbroken | Restored 18 Oct |
| 2 | Honest cost accounting | Measured spread, charged both sides | Pending |
| 3 | Sample size | 100+ closed forward trades | 21 |
| 4 | Profit factor | Above 1.5 | 0.374 |
| 5 | Statistical significance | **p below 0.05** | Not yet testable |
| 6 | Demo execution | Verified order round-trip | Not started |

Gates 4 and 5 must be satisfied **together**. A strong profit factor across a
handful of trades can arise from luck; the significance test asks whether a result
that good could have occurred by chance. Requiring both is what stopped an earlier
candidate — which looked convincing — from reaching live capital.

## The 435-pair universe screen

Before concluding, 31 instruments were screened across FX, equity indices,
commodities and crypto, covering 2018–2026 — 435 instrument pairs in total.

**The statistical correction is the finding.** Testing 435 pairs at the
conventional 5% threshold produces roughly 22 false positives by arithmetic alone.
Applying Benjamini-Hochberg (false discovery rate) and Bonferroni corrections:

| Measure | Result |
|---|---|
| Raw "significant" results at p < 0.05 | 37 |
| Expected from chance alone | 21.8 |
| Survive strict correction | 2 |
| **Tradeable candidates** | **0** |

Both survivors pair market volatility (VIX) against gold and silver. They are
genuinely related over the long run, but the *spread* between them moves randomly
— so there is no reliable point at which it returns, and therefore nothing to
trade. Every other instrument measured as statistically indistinguishable from
random price movement.

This also corrected an earlier error in our own work: a smaller screen found three
"significant" pairs where chance predicts 3.3, and one was advanced for
development before failing outright. That is precisely the mistake this corrected
screen is designed to prevent.

## Next steps: a change of inputs, not of settings

Further adjustment of retail technical patterns has been stopped. Repeatedly
tuning the same indicators against the same price history produces results that
look strong and do not survive contact with new data — a pattern we observed
directly and repeatedly.

Research has moved to genuine quantitative inputs:

- **Macro drivers** — the US dollar index and inflation-adjusted Treasury yields,
  being gold's established fundamental drivers, rather than chart shapes.
- **Statistical pre-screening** — instruments are now tested for exploitable
  structure *before* any strategy is designed for them, with multiple-comparison
  correction applied as standard.
- **Honest cost accounting** — transaction costs measured against 204,214 real
  market ticks, replacing an assumed figure that understated them.

One genuine result carried forward: the macro filter does not create profit, but
it reduced maximum drawdown from 33% to 12.4%. It is a demonstrable risk control.

## Commitment

**Live capital remains held in full until a strategy satisfies all six gates,
including statistical significance at p < 0.05 on forward-test data.** No
deployment date is set, because committing to one would create pressure to pass
gates on schedule rather than on evidence.

Our present assessment is that the system as it stands is unlikely to meet these
gates. We would rather tell you that now than discover it with your capital at
risk.

---

*Full detail: `CAPITAL_PROTECTION_REPORT.md`. Technical record:
`data/performance_report.md`. Screen output: `data/universe_screen.json`.*
