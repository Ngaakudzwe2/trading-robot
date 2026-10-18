# -*- coding: utf-8 -*-
"""
nas100_battery.py

Institutional validation battery for the NAS100 5-minute opening-momentum
strategy (github.com/Ngaakudzwe2/NASDAQ), run against real Dukascopy M5 data.

It drives THAT repo's own engine (`nasdaq_bt.engine.run_backtest`) rather than
reimplementing its rules. Reimplementing would introduce drift between what is
tested here and what that repo actually does -- and the whole point is to test
the strategy as written, including its gap rule, chandelier trail, leverage cap
and swap accounting, none of which AMARO's own engines model.

What this adds on top of that repo's single-shot backtest:

  Walk-forward      30 contiguous folds. Parameters are FIXED throughout -- there
                    is no per-fold tuning, so every fold is out-of-sample by
                    construction and no fold can be curve-fitted.
  Long/short split  The brief's own analysis flags that 2019-2026 was largely a
                    bull market, so a long-biased system would flatter itself.
                    Reported separately for exactly that reason.
  Significance      Bootstrap p-value and a one-sample t-test on per-trade
                    returns. Both, because the trade-return distribution is
                    heavy-tailed and the t-test alone is optimistic there.
  Monte Carlo       Resampling with replacement to get a drawdown and P&L
                    distribution rather than the single realised path.
  Buy-and-hold      The benchmark that matters on an index. A long-biased
                    strategy on a rising index must beat holding it, or it is an
                    expensive way to be long.

REAL SPREAD. Costs use the MEASURED 0.89-point tick spread (ask 29172.002 /
bid 29171.109, decoded from real Dukascopy ticks) rather than the repo's assumed
1.5. Both are reported: the repo's assumption is conservative here, which is the
opposite of this project's gold-spread error, and it is worth showing that the
result does not depend on the softer figure.

Usage:
    python3 -m src.nas100_battery
    python3 -m src.nas100_battery --folds 30 --csv data/NAS100_M5.csv
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

# The NASDAQ repo is a sibling checkout, not a package on this project's path.
NASDAQ_REPO = Path("/private/tmp/claude-501/-Users-admin-Desktop---admin-s-MacBook-Pro-AMARO/"
                    "772c29ac-de74-4635-980f-4a0c138d8318/scratchpad/NASDAQ")

MEASURED_SPREAD_POINTS = 0.89   # decoded from real ticks
REPO_ASSUMED_SPREAD = 1.5

# Client gates (CAPITAL_PROTECTION_REPORT.md)
GATE_PF = 1.5
GATE_P = 0.05


def _patch_long_only(engine_mod) -> None:
    """Clamps short signals to flat, making the strategy long-only.

    Done by wrapping the repo's own _signal rather than filtering trades afterwards,
    because the two are NOT equivalent. The engine holds one position at a time, so
    in the two-sided version an open SHORT blocks any long signal that fires while it
    runs. Removing shorts frees those slots, and a genuine long-only run therefore
    takes MORE long trades than the long subset of a two-sided run -- reading the
    long rows off the combined result would understate it.
    """
    original = engine_mod._signal

    def long_only(o, c, e, rule: str) -> int:
        return 1 if original(o, c, e, rule) == 1 else 0

    engine_mod._signal = long_only


def _import_engine(repo: Path):
    src = repo / "src"
    if not src.exists():
        raise SystemExit(f"NASDAQ repo not found at {repo}. Clone it first.")
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from nasdaq_bt.config import RiskConfig, StrategyConfig  # noqa: E402
    from nasdaq_bt.data import load_csv  # noqa: E402
    from nasdaq_bt.engine import run_backtest  # noqa: E402
    return load_csv, run_backtest, StrategyConfig, RiskConfig


def fold_bounds(index: pd.DatetimeIndex, n_folds: int) -> List[tuple]:
    """Contiguous equal-width folds over the data's own span. Equal by TIME rather
    than by bar count, so a fold spanning a thin period is not silently wider in
    calendar terms than its neighbours."""
    start, end = index[0], index[-1]
    edges = pd.date_range(start, end, periods=n_folds + 1)
    return [(edges[i], edges[i + 1]) for i in range(n_folds)]


def _trade_returns(trades: pd.DataFrame, equity0: float) -> np.ndarray:
    if trades.empty or "pnl" not in trades.columns:
        return np.array([])
    return trades["pnl"].to_numpy(dtype=float) / equity0 * 100


def _block_metrics(trades: pd.DataFrame, equity0: float) -> dict:
    if trades.empty:
        return {"n": 0, "win_rate_pct": 0.0, "profit_factor": None, "net_pnl_pct": 0.0,
                "max_drawdown_pct": 0.0, "sharpe": None}
    pnl = trades["pnl"].to_numpy(dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    gp, gl = float(wins.sum()), float(-losses.sum())
    eq = equity0 + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate(([equity0], eq)))[1:]
    dd = float(((peak - eq) / peak * 100).max())
    sharpe = float(pnl.mean() / pnl.std() * np.sqrt(len(pnl))) if len(pnl) > 1 and pnl.std() > 0 else None
    return {"n": int(len(pnl)), "win_rate_pct": float(len(wins) / len(pnl) * 100),
            "profit_factor": (gp / gl) if gl > 0 else None,
            "net_pnl_pct": float(pnl.sum() / equity0 * 100),
            "max_drawdown_pct": dd, "sharpe": sharpe}


def significance(returns_pct: np.ndarray, iterations: int = 10_000, seed: int = 42) -> dict:
    """Bootstrap p-value plus a t-test.

    The bootstrap p is two-sided and computed by recentring the sample to a zero
    mean and asking how often a resample's mean is at least as extreme as the
    observed one -- the correct null for 'this strategy has no edge'. Simply
    counting how many resamples are positive would answer a different and much
    easier question, and would look far more impressive.
    """
    n = len(returns_pct)
    out = {"n": n, "mean_pct": None, "t": None, "p_ttest": None, "p_bootstrap": None,
           "ci_low": None, "ci_high": None}
    if n < 3:
        return out
    obs = float(returns_pct.mean())
    out["mean_pct"] = obs
    t, p = stats.ttest_1samp(returns_pct, 0.0)
    out["t"], out["p_ttest"] = float(t), float(p)

    rng = np.random.default_rng(seed)
    centred = returns_pct - obs
    draws = rng.choice(centred, size=(iterations, n), replace=True).mean(axis=1)
    out["p_bootstrap"] = float((np.abs(draws) >= abs(obs)).mean())

    tot = rng.choice(returns_pct, size=(iterations, n), replace=True).sum(axis=1)
    out["ci_low"], out["ci_high"] = float(np.percentile(tot, 2.5)), float(np.percentile(tot, 97.5))
    return out


def monte_carlo(trades: pd.DataFrame, equity0: float, iterations: int = 5000,
                 seed: int = 42) -> dict:
    if trades.empty:
        return {}
    pnl = trades["pnl"].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    nets, dds = np.empty(iterations), np.empty(iterations)
    for i in range(iterations):
        s = rng.choice(pnl, size=len(pnl), replace=True)
        eq = equity0 + np.cumsum(s)
        peak = np.maximum.accumulate(np.concatenate(([equity0], eq)))[1:]
        nets[i] = s.sum() / equity0 * 100
        dds[i] = ((peak - eq) / peak * 100).max()
    return {"net_p5": float(np.percentile(nets, 5)), "net_p50": float(np.percentile(nets, 50)),
            "net_p95": float(np.percentile(nets, 95)),
            "dd_p50": float(np.percentile(dds, 50)), "dd_p95": float(np.percentile(dds, 95)),
            "prob_loss_pct": float((nets < 0).mean() * 100)}


def buy_and_hold(df: pd.DataFrame) -> dict:
    """Index return over the same span, and its peak-to-trough drawdown."""
    close = df["close"].to_numpy(dtype=float)
    ret = (close[-1] / close[0] - 1.0) * 100
    peak = np.maximum.accumulate(close)
    return {"net_pnl_pct": float(ret), "max_drawdown_pct": float(((peak - close) / peak * 100).max())}


def run_battery(csv_path: str, n_folds: int, spread_points: float, repo: Path,
                 tz: str = "UTC", long_only: bool = False) -> dict:
    load_csv, run_backtest, StrategyConfig, RiskConfig = _import_engine(repo)
    if long_only:
        import nasdaq_bt.engine as _eng
        _patch_long_only(_eng)
        run_backtest = _eng.run_backtest

    df = load_csv(csv_path, tz)
    strat = StrategyConfig()            # repo defaults: symmetric, ATR stop, chandelier trail
    risk = RiskConfig(spread_points=spread_points)
    equity0 = risk.initial_equity

    folds = fold_bounds(df.index, n_folds)
    fold_sharpes: List[float] = []
    all_trades: List[pd.DataFrame] = []

    for lo, hi in folds:
        sub = df[(df.index >= lo) & (df.index < hi)]
        if len(sub) < 200:
            fold_sharpes.append(0.0)
            continue
        trades, _eq = run_backtest(sub, strat, risk)
        m = _block_metrics(trades, equity0)
        # Neutral score for a fold too thin to judge -- same convention as every
        # other evaluation in this project.
        fold_sharpes.append(m["sharpe"] if (m["sharpe"] is not None and m["n"] >= 3) else 0.0)
        if not trades.empty:
            all_trades.append(trades)

    pooled_trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    pooled = _block_metrics(pooled_trades, equity0)
    rets = _trade_returns(pooled_trades, equity0)

    longs = pooled_trades[pooled_trades["direction"] == "long"] if not pooled_trades.empty else pd.DataFrame()
    shorts = pooled_trades[pooled_trades["direction"] == "short"] if not pooled_trades.empty else pd.DataFrame()

    return {"csv": csv_path, "spread_points": spread_points, "folds": n_folds,
            "bars": int(len(df)), "span": [str(df.index[0]), str(df.index[-1])],
            "pooled": pooled, "median_fold_sharpe": statistics.median(fold_sharpes) if fold_sharpes else 0.0,
            "fold_sharpes": [round(s, 3) for s in fold_sharpes],
            "long": _block_metrics(longs, equity0), "short": _block_metrics(shorts, equity0),
            "significance": significance(rets), "monte_carlo": monte_carlo(pooled_trades, equity0),
            "buy_and_hold": buy_and_hold(df)}


def gate_check(res: dict) -> dict:
    p = res["significance"].get("p_bootstrap")
    pf = res["pooled"].get("profit_factor")
    msh = res["median_fold_sharpe"]
    bh = res["buy_and_hold"]["net_pnl_pct"]
    passes = {
        "pf_above_1_5": pf is not None and pf > GATE_PF,
        "p_below_0_05": p is not None and p < GATE_P,
        "median_fold_sharpe_positive": msh > 0,
        "beats_buy_and_hold": res["pooled"]["net_pnl_pct"] > bh,
    }
    passes["ALL"] = all(passes.values())
    return passes


def _print(res: dict) -> None:
    p, s, mc, bh = res["pooled"], res["significance"], res["monte_carlo"], res["buy_and_hold"]
    pf = p["profit_factor"]
    print(f"\n  spread {res['spread_points']} pts | {res['bars']:,} bars | "
          f"{res['span'][0][:10]} -> {res['span'][1][:10]} | {res['folds']} folds")
    print(f"  {'POOLED':<22} trades={p['n']:<5} win={p['win_rate_pct']:.1f}%  "
          f"PF={(f'{pf:.3f}' if pf else 'n/a')}  net={p['net_pnl_pct']:+.2f}%  "
          f"maxDD={p['max_drawdown_pct']:.2f}%")
    for name, b in (("LONG", res["long"]), ("SHORT", res["short"])):
        bpf = b["profit_factor"]
        print(f"  {name:<22} trades={b['n']:<5} win={b['win_rate_pct']:.1f}%  "
              f"PF={(f'{bpf:.3f}' if bpf else 'n/a')}  net={b['net_pnl_pct']:+.2f}%")
    print(f"  {'MEDIAN FOLD SHARPE':<22} {res['median_fold_sharpe']:+.3f}")
    if s["p_bootstrap"] is not None:
        print(f"  {'SIGNIFICANCE':<22} t={s['t']:.3f} p_ttest={s['p_ttest']:.4f}  "
              f"p_bootstrap={s['p_bootstrap']:.4f}")
    if mc:
        print(f"  {'MONTE CARLO':<22} net p5/p50/p95 = {mc['net_p5']:+.1f}% / "
              f"{mc['net_p50']:+.1f}% / {mc['net_p95']:+.1f}%  |  "
              f"DD p95={mc['dd_p95']:.1f}%  P(loss)={mc['prob_loss_pct']:.1f}%")
    print(f"  {'BUY & HOLD':<22} net={bh['net_pnl_pct']:+.2f}%  maxDD={bh['max_drawdown_pct']:.2f}%")
    if p["max_drawdown_pct"] > 0 and bh["max_drawdown_pct"] > 0:
        print(f"  {'RETURN / MAX DD':<22} strategy {p['net_pnl_pct'] / p['max_drawdown_pct']:.2f}x  "
              f"vs  buy&hold {bh['net_pnl_pct'] / bh['max_drawdown_pct']:.2f}x")

    g = gate_check(res)
    print(f"\n  GATES: PF>1.5 {'PASS' if g['pf_above_1_5'] else 'FAIL'} | "
          f"p<0.05 {'PASS' if g['p_below_0_05'] else 'FAIL'} | "
          f"median fold Sharpe>0 {'PASS' if g['median_fold_sharpe_positive'] else 'FAIL'} | "
          f"beats B&H {'PASS' if g['beats_buy_and_hold'] else 'FAIL'}"
          f"  =>  {'ALL PASS' if g['ALL'] else 'NOT CLEARED'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="NAS100 institutional validation battery.")
    ap.add_argument("--csv", default=str(NASDAQ_REPO / "data" / "NAS100_M5.csv"))
    ap.add_argument("--folds", type=int, default=30)
    ap.add_argument("--repo", default=str(NASDAQ_REPO))
    ap.add_argument("--tz", default="UTC")
    ap.add_argument("--json-out", default="data/nas100_battery.json")
    ap.add_argument("--long-only", action="store_true",
                     help="Clamp short signals to flat (see _patch_long_only).")
    args = ap.parse_args()

    print("=" * 96)
    print("NAS100 5-MIN OPENING MOMENTUM -- VALIDATION BATTERY")
    print("  Strategy engine: the NASDAQ repo's own run_backtest (rules unchanged, no tuning)")
    if "--long-only" in sys.argv:
        print("  MODE: LONG-ONLY (short signals clamped to flat)")
    print("=" * 96)

    results = {}
    for label, spread in (("MEASURED SPREAD (0.89 pts, from real ticks)", MEASURED_SPREAD_POINTS),
                           ("REPO ASSUMPTION (1.5 pts)", REPO_ASSUMED_SPREAD),
                           ("STRESS (2x measured, 1.78 pts)", MEASURED_SPREAD_POINTS * 2)):
        print(f"\n--- {label} ---")
        res = run_battery(args.csv, args.folds, spread, Path(args.repo), args.tz,
                           long_only=args.long_only)
        _print(res)
        results[label] = res

    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nFull output -> {args.json_out}")


if __name__ == "__main__":
    main()
