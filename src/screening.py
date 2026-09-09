# -*- coding: utf-8 -*-
"""
screening.py

Systematically screens instruments for mean-reversion vs. trending BEFORE any
strategy is designed for them. Added 2026-09-10.

The motivation is a specific, expensive lesson from this project: ten rounds of
strategy work were spent on XAUUSD without ever first asking whether the
instrument's own statistical character suited the strategy family being applied
to it. A trend-continuation setup on a mean-reverting series (or vice versa) is
fighting the data, and no amount of parameter tuning fixes that. This module is
the gate that question should have gone through first.

Two independent, standard measures -- reported together precisely because they
can disagree, and a disagreement is itself informative:

  Augmented Dickey-Fuller (statsmodels.tsa.stattools.adfuller)
      Null hypothesis: the series has a unit root (is a random walk / trending).
      REJECTING the null (low p) is evidence of STATIONARITY, i.e. mean
      reversion. Note the direction carefully -- it is the opposite of the usual
      "significant = interesting signal" reading, and getting it backwards would
      invert every conclusion here.

  Hurst exponent (rescaled-range over multiple lags)
      H ~ 0.5  random walk (no exploitable structure)
      H <  0.5  mean-reverting / anti-persistent
      H >  0.5  trending / persistent
      Estimated by regressing log(std of lagged differences) on log(lag) -- the
      standard variance-ratio formulation, which is more stable on financial
      series than the classic R/S rescaled range.

Both are run on LOG PRICES, not raw prices or returns. Returns are almost always
stationary for any tradeable instrument, so running ADF on returns produces a
uniform "everything mean-reverts" result that says nothing about tradability --
a real and easy mistake. Log prices are the level series whose stationarity
actually implies a tradeable mean-reverting relationship.

Also screens candidate PAIRS for cointegration (Engle-Granger), since statistical
arbitrage needs a stationary SPREAD rather than a stationary single instrument --
individually-trending legs can still form a mean-reverting spread, which is the
entire basis of pairs trading and is invisible to a single-series test.

Usage:
    python3 -m src.screening
    python3 -m src.screening --symbols EURJPY AUDCAD GBPJPY --start 2012-01-01
"""

from __future__ import annotations

import argparse
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
from statsmodels.tsa.stattools import adfuller, coint

from src import data_macro

MIN_OBS = 250  # ~1 trading year; below this both statistics are too noisy to act on


def hurst_exponent(series: np.ndarray, max_lag: int = 100) -> Optional[float]:
    """
    Variance-ratio Hurst estimate: for a range of lags, measure the standard
    deviation of lag-differences, then fit log(std) ~ H * log(lag).

    Returns None rather than a number when the fit is not meaningful (too few
    points, or a degenerate/constant stretch producing non-positive std values)
    -- a silently-returned 0.5 would be indistinguishable from a real
    random-walk finding.
    """
    series = np.asarray(series, dtype=float)
    n = len(series)
    if n < MIN_OBS:
        return None
    max_lag = min(max_lag, n // 4)
    if max_lag < 10:
        return None

    lags = np.arange(2, max_lag)
    tau = []
    good_lags = []
    for lag in lags:
        diff = series[lag:] - series[:-lag]
        sd = float(np.std(diff))
        if sd > 0:
            tau.append(sd)
            good_lags.append(lag)
    if len(tau) < 10:
        return None
    slope = np.polyfit(np.log(good_lags), np.log(tau), 1)[0]
    return float(slope)


def adf_test(series: np.ndarray) -> Optional[dict]:
    """ADF on the level series. Low p => reject unit root => stationary/mean-reverting."""
    series = np.asarray(series, dtype=float)
    if len(series) < MIN_OBS:
        return None
    try:
        stat, pvalue, _, nobs, crit, _ = adfuller(series, autolag="AIC")
    except Exception:
        return None
    return {"stat": float(stat), "p": float(pvalue), "nobs": int(nobs),
            "crit_5pct": float(crit["5%"])}


def classify(adf: Optional[dict], hurst: Optional[float]) -> str:
    """Combines the two measures. Deliberately conservative: anything that isn't a
    clear, agreeing signal is called RANDOM WALK rather than being forced into a
    tradeable-looking bucket."""
    if adf is None or hurst is None:
        return "insufficient data"
    stationary = adf["p"] < 0.05
    if stationary and hurst < 0.45:
        return "MEAN-REVERTING (both agree)"
    if not stationary and hurst > 0.55:
        return "TRENDING (both agree)"
    if stationary and hurst >= 0.45:
        return "mixed (ADF stationary, Hurst not)"
    if not stationary and hurst <= 0.45:
        return "mixed (Hurst reverting, ADF not)"
    return "random walk (no exploitable structure)"


def screen_symbol(name: str, closes: List[float]) -> dict:
    logp = np.log(np.asarray(closes, dtype=float))
    logp = logp[np.isfinite(logp)]
    adf = adf_test(logp)
    h = hurst_exponent(logp)
    return {"symbol": name, "n": len(logp), "adf": adf, "hurst": h,
            "verdict": classify(adf, h)}


def screen_pair(a_name: str, a: Dict[str, float], b_name: str, b: Dict[str, float]) -> Optional[dict]:
    """Engle-Granger cointegration on the aligned log-price legs. Low p => the spread
    is stationary => a statistical-arbitrage candidate even if both legs trend."""
    rows = data_macro.align(a, b)
    if len(rows) < MIN_OBS:
        return None
    x = np.log(np.array([r[1] for r in rows], dtype=float))
    y = np.log(np.array([r[2] for r in rows], dtype=float))
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        return None
    try:
        stat, pvalue, _ = coint(x, y)
    except Exception:
        return None
    beta = float(np.polyfit(x, y, 1)[0])
    spread = y - beta * x
    return {"pair": f"{b_name}~{a_name}", "n": len(rows), "coint_p": float(pvalue),
            "beta": beta, "spread_hurst": hurst_exponent(spread)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Screen instruments for mean-reversion vs trending.")
    parser.add_argument("--symbols", nargs="+",
                         default=["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"] + data_macro.CROSS_PAIR_UNIVERSE)
    parser.add_argument("--start", default="2012-01-01")
    parser.add_argument("--no-pairs", action="store_true", help="Skip the cointegration matrix.")
    args = parser.parse_args()

    print(f"Loading {len(args.symbols)} series...")
    data = data_macro.fetch_many(args.symbols, start=args.start, log=lambda *_: None)
    series = {n: data_macro.to_series(rows) for n, rows in data.items() if rows}
    print(f"Loaded {len(series)}.\n")

    print("=" * 96)
    print(f"{'SYMBOL':10} {'N':>6} {'ADF stat':>10} {'ADF p':>8} {'Hurst':>7}  VERDICT")
    print("=" * 96)
    results = []
    for name in args.symbols:
        if name not in series:
            continue
        closes = [series[name][d] for d in sorted(series[name])]
        r = screen_symbol(name, closes)
        results.append(r)
        adf_stat = f"{r['adf']['stat']:>10.3f}" if r["adf"] else f"{'n/a':>10}"
        adf_p = f"{r['adf']['p']:>8.4f}" if r["adf"] else f"{'n/a':>8}"
        hu = f"{r['hurst']:>7.3f}" if r["hurst"] is not None else f"{'n/a':>7}"
        print(f"{r['symbol']:10} {r['n']:>6} {adf_stat} {adf_p} {hu}  {r['verdict']}")

    reverting = [r["symbol"] for r in results if r["verdict"].startswith("MEAN-REVERTING")]
    trending = [r["symbol"] for r in results if r["verdict"].startswith("TRENDING")]
    print(f"\n  mean-reverting: {reverting or 'none'}")
    print(f"  trending:       {trending or 'none'}")
    print(f"  neither:        {[r['symbol'] for r in results if r['symbol'] not in reverting + trending]}")

    if args.no_pairs:
        return
    print("\n" + "=" * 96)
    print("COINTEGRATION (Engle-Granger on log prices) -- stat-arb candidates, p < 0.05")
    print("=" * 96)
    names = [n for n in args.symbols if n in series]
    found = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            pr = screen_pair(a, series[a], b, series[b])
            if pr and pr["coint_p"] < 0.05:
                found.append(pr)
    for pr in sorted(found, key=lambda x: x["coint_p"]):
        sh = f"{pr['spread_hurst']:.3f}" if pr["spread_hurst"] is not None else "n/a"
        print(f"  {pr['pair']:20} n={pr['n']:>5}  coint_p={pr['coint_p']:.4f}  "
              f"beta={pr['beta']:>7.3f}  spread_hurst={sh}")
    if not found:
        print("  none below p<0.05")
    else:
        print(f"\n  {len(found)} cointegrated pair(s). A LOW spread_hurst (<0.45) is the one worth "
              f"pursuing:\n  cointegration says a stable relationship exists, spread_hurst says the "
              f"spread actually mean-reverts\n  rather than drifting -- both are needed before "
              f"designing a stat-arb strategy.")


if __name__ == "__main__":
    main()
