# Capital Protection Report — AMARO Trading System

**As of 18 October 2026** · Prepared for client review

> **Status: $20,000 USD held, undeployed.** No client capital has been placed in
> any market. Every result in this report is simulated against real historical
> prices or recorded from paper trading. This document explains why the funds are
> held, what the testing found, and the exact conditions required to release them.

---

## 1. Summary

Four fundamentally different trading strategies have been built and tested to
completion. **None produced a statistically significant edge.** A wide screen of
31 instruments and 435 instrument pairs then found **zero tradeable candidates**
after correcting for multiple comparisons.

The funds are held because deploying them would mean trading a system that the
evidence says does not work. That is not caution for its own sake — the live
paper test is currently losing money, and the historical tests explain why.

| | |
|---|---|
| Capital deployed | **$0** |
| Capital held | $20,000 USD |
| Strategies tested to completion | 4 |
| Strategies clearing the significance gate | **0** |
| Instruments screened | 31 |
| Instrument pairs screened | 435 |
| Tradeable candidates found | **0** |

---

## 2. What the testing found

### Forward test (live paper trading, real prices, no real money)

| Metric | Value | Required |
|---|---|---|
| Closed trades | 21 | 100+ |
| Win rate | 23.8% | — |
| Profit factor | **0.374** | above 1.5 |
| Net P&L | **−8.96%** | positive |
| Max drawdown | 9.07% | — |

A profit factor of 0.374 means the system earned 37 cents for every dollar it
lost. Sixteen of twenty-one trades lost money.

### Historical walk-forward testing

Each strategy was tested across 2012–2026 using walk-forward validation, where
parameters are fixed on earlier data and tested on later data the model has never
seen. This is the standard method for detecting strategies that only work in
hindsight.

| Strategy | Trades | Profit factor | p-value | Verdict |
|---|---|---|---|---|
| Daily crossover (v1) | — | — | — | Too infrequent to validate |
| Pullback-to-average (v2) | 1,850 | 0.989 | — | No edge; coin flip before costs |
| Session order blocks (v4) | 32 | 1.860 | 0.117 | Failed on a second instrument (p=0.845) |
| Macro-filtered gold (v5) | 825 | 1.010 | 0.897 | No edge, large sample |
| Pair arbitrage (v6) | 66 | 1.591 | 0.166 | Pair not cointegrated |

The `p-value` is the probability that a result this good could arise from a
strategy with no real edge. The accepted scientific threshold is below 0.05.
**No strategy came close.** The best was 0.117 — meaning roughly a one-in-nine
chance of arising from luck alone, on a sample too small to distinguish the two.

### Why they failed: three root causes

**1. Gold's price behaves like a random walk.** Two standard statistical tests
place gold at a Hurst exponent of 0.494 (0.50 *is* a random walk) with a
stationarity p-value of 0.998. A strategy reading chart patterns is searching for
structure these tests say is not present.

**2. The entry signal never had an edge.** Stripped of its filters and tested
across 1,850 trades, the core signal produced a profit factor of 0.989 and a 33%
drawdown. Everything built afterwards was a filter on a signal that does not
predict direction.

**3. Transaction costs were understated.** The simulations charged a $0.40 spread.
Measurement against 204,214 real market ticks puts the true gold spread at $0.65,
and a flaw in how the simulation applied spread meant it was effectively charging
none at all. Every historical figure above is therefore optimistic.

---

## 3. The wide-universe screen

Before concluding, the full investable universe was screened for any statistical
structure a strategy could exploit — 31 instruments across FX majors and crosses,
equity indices, commodities and crypto, over 2018–2026.

**The correction matters more than the raw result.** Testing 435 pairs at the
conventional 5% threshold produces roughly 22 false positives by arithmetic alone.
An earlier, smaller screen in this project tested 66 pairs, found 3 "significant"
results, and nominated one for development — but chance predicts 3.3 at that
threshold, so it had found precisely its own noise. That candidate later failed
outright. This screen applies both Bonferroni and Benjamini-Hochberg corrections
so the error cannot recur.

| Screen | Tests | Raw hits (p<0.05) | Expected by chance | Survive correction |
|---|---|---|---|---|
| Per instrument | 31 | 3 | 1.6 | **1** |
| Instrument pairs | 435 | 37 | 21.8 | **2** |
| **Tradeable candidates** | | | | **0** |

### What survived, and why it still isn't tradeable

**VIX** is genuinely and strongly mean-reverting (Hurst 0.291, p < 0.0001,
surviving the strictest correction). This is the single robust statistical finding
in the entire universe. It is also well known, and the VIX index cannot be traded
directly — exposure requires futures or options, whose roll costs and term
structure typically consume more than the reversion offers.

**VIX~Gold and VIX~Silver** cointegrate at overwhelming significance (p < 0.00001,
Johansen 37.4 and 36.9 against a 15.5 threshold). But the *spread* between them
does not mean-revert (Hurst 0.477 and 0.483 — random walk). A stable long-term
relationship without a reverting spread gives nothing to trade: there is no
reliable point at which the spread returns, so there is no exit.

Every other instrument clustered at a Hurst exponent near 0.50 (range 0.291–0.551,
median 0.446) — statistically indistinguishable from random price movement.

---

## 4. Gates required before any capital is deployed

These are objective and measured on **forward-test data only** — trades placed
after the strategy is fixed, never on historical simulation.

| # | Gate | Threshold | Current | Status |
|---|---|---|---|---|
| 1 | Continuous data | 30 days unbroken | feed restored 18 Oct | In progress |
| 2 | Honest cost accounting | Measured spread, charged both sides | Correction pending | Not met |
| 3 | Sample size | 100+ closed forward trades | 21 | Not met |
| 4 | Profit factor | Above 1.5 | 0.374 | Not met |
| 5 | Statistical significance | p below 0.05 | not yet testable | Not met |
| 6 | Demo execution | Order round-trip verified on demo account | Not started | Not met |

**All six must be met. Gates 4 and 5 must be met together.** Profit factor alone
can look strong across a handful of lucky trades; the significance test asks
whether a result that good could have come from chance. Until it answers no, a
good-looking number is not evidence. This is the specific error that nominated a
failed candidate earlier in this project.

### Why no deadline is attached

Gate 3 alone requires roughly 100 trades at a current rate of about five per
month. Committing to a date would create pressure to pass gates on schedule
rather than on evidence — the exact failure this process exists to prevent.

---

## 5. Honest assessment

The most likely conclusion from this body of work is that **the approaches tested
do not contain a tradeable edge**, and that further variations on them will not
find one. Four distinct methods, tested properly, all failed. A corrected screen
of the whole investable universe found nothing. These results are consistent with
each other and with the statistical character of the instruments themselves.

This is not a failed project. It is a project that measured something most
systems never test, and arrived at a reliable answer before any capital was
exposed. The infrastructure built along the way — walk-forward validation,
significance testing, placebo controls, measured transaction costs — is what made
that answer trustworthy, and it is reusable against any future strategy.

**Recommendation: the $20,000 remains held.** Any decision to deploy should wait
on all six gates above, and should be taken in the knowledge that the current
evidence does not suggest they will be met by the system as it stands.

---

*Prepared from real historical market data (Dukascopy tick data, Yahoo Finance,
Federal Reserve Economic Data), walk-forward validation, and live paper trading.
Full technical detail in `data/performance_report.md`; screen output in
`data/universe_screen.json`.*
