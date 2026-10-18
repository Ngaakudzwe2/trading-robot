# -*- coding: utf-8 -*-
"""
entries_v5_macro.py

Macro-filtered gold strategy (Hypothesis 3). Gold LONG requires both macro
drivers pointing the right way:
    (1) DXY slope over the window < 0   (dollar weakening)
    (2) US10Y momentum over the window < 0   (yields falling)

Run as two arms, because the brief's rule is ambiguous on its own and the
difference between them is the whole question:

  MACRO_ONLY   the two conditions ARE the entry. Enter long on the first H1 bar
               where both hold, exit on an ATR stop/target. A direct test of
               whether macro state alone carries information.
  V2_FILTERED  entries_v2's pullback trigger, gated by the same two conditions.
               Tests whether macro rescues the OHLC signal that was retired at
               PF 0.374.

Running only the second would confound two questions: a weak result could be
the macro filter's fault or the dead pullback trigger's, and there would be no
way to tell which. Running only the first would leave the brief's actual
sentence ("Gold LONG entries require...") untested.

Shorts are not taken. The brief specifies conditions for LONG entries only, and
inventing a mirrored short rule would be adding an untested hypothesis to a test
of this one.

Costs: the honest ones, not the project's historical $0.40. Measured median gold
spread is $0.65 (from 204,214 real ticks) and the spread is applied on BOTH
sides here -- entry at the ask, exit at the bid -- rather than the half-spread-
at-entry-only model used elsewhere in this project, which cancelled itself out
because stop and target were anchored to the already-adjusted fill. See
data/performance_report.md, 2026-09-10.

Usage:
    python3 -m src.entries_v5_macro
    python3 -m src.entries_v5_macro --slope-window 24 --arms macro_only
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np
from scipy import stats

from src import macro_features
from src.candle import Candle
from src.entries_v2 import Direction, TradeRecordV2, compute_metrics
from src.indicators import atr, ema
from src.backtest_structures import sma

ATR_PERIOD = 14

# Real measured costs. spread is charged in FULL per round trip (half each side).
MEASURED_SPREAD = 0.65
SLIPPAGE_PER_SIDE = 0.05


@dataclass
class MacroConfig:
    slope_window: int = macro_features.DEFAULT_SLOPE_WINDOW
    atr_sl_mult: float = 2.0
    atr_tp_mult: float = 2.5
    # V2_FILTERED arm only -- the retired flagship's own structural parameters,
    # reused unchanged so this measures the macro filter's effect and not a
    # re-tuning of the pullback trigger.
    trend_sma_period: int = 50
    pullback_ema_period: int = 21
    pullback_tolerance_pct: float = 0.20


def _macro_ok(dxy_slope: Optional[float], y_mom: Optional[float]) -> bool:
    """Both drivers supportive. A missing value is NOT treated as passing -- during
    warmup there is no macro state, and letting those bars through would silently
    turn the filter off for the start of every fold."""
    if dxy_slope is None or y_mom is None:
        return False
    return dxy_slope < 0 and y_mom < 0


def simulate(h1: List[Candle], aligned: Dict[str, List[Optional[float]]], config: MacroConfig,
             arm: str = "macro_only", notional: float = 10_000.0,
             yield_key: str = "us10y_mom") -> List[TradeRecordV2]:
    """Long-only, one position at a time, ATR bracket exit. Costs charged both sides."""
    atr_vals = atr(h1, ATR_PERIOD)
    closes = [c.close for c in h1]
    h1_ema = ema(closes, config.pullback_ema_period) if arm == "v2_filtered" else None
    trend = aligned.get("_trend_sma") if arm == "v2_filtered" else None
    tol = config.pullback_tolerance_pct / 100.0

    dxy = aligned["dxy_slope_pct"]
    ymom = aligned[yield_key]

    half_spread = MEASURED_SPREAD / 2.0
    trades: List[TradeRecordV2] = []
    position: Optional[dict] = None

    for i in range(1, len(h1)):
        c = h1[i]
        a = atr_vals[i]
        t = datetime.fromtimestamp(c.open_time / 1000, tz=timezone.utc)

        if position is not None:
            exit_price = exit_reason = None
            if c.low <= position["sl"]:
                exit_price, exit_reason = position["sl"], "SL"
            elif c.high >= position["tp"]:
                exit_price, exit_reason = position["tp"], "TP"
            if exit_price is not None:
                # Exit a long on the BID: subtract the half-spread as well as slippage.
                fill = exit_price - half_spread - SLIPPAGE_PER_SIDE
                qty = position["qty"]
                pnl = (fill - position["entry_price"]) * qty
                trades.append(TradeRecordV2(
                    direction=Direction.LONG, entry_time=position["entry_time"],
                    entry_price=position["entry_price"], stop=position["sl"],
                    target=position["tp"], exit_time=t, exit_price=fill,
                    exit_reason=exit_reason, qty=qty, pnl=pnl))
                position = None
            continue

        if a is None or a <= 0:
            continue
        if not _macro_ok(dxy[i], ymom[i]):
            continue

        if arm == "v2_filtered":
            if h1_ema[i] is None or trend is None or trend[i] is None:
                continue
            if not (c.close > trend[i] and c.low <= h1_ema[i] * (1 + tol) and c.close > h1_ema[i]):
                continue

        # Enter a long on the ASK.
        fill = c.close + half_spread + SLIPPAGE_PER_SIDE
        qty = notional / fill
        position = {"entry_price": fill, "qty": qty, "entry_time": t,
                    "sl": fill - config.atr_sl_mult * a, "tp": fill + config.atr_tp_mult * a}

    return trades


def _slice_idx(h1: List[Candle], start: datetime, end: datetime) -> range:
    s, e = start.timestamp() * 1000, end.timestamp() * 1000
    lo = next((i for i, c in enumerate(h1) if c.open_time >= s), len(h1))
    hi = next((i for i, c in enumerate(h1) if c.open_time > e), len(h1))
    return range(lo, hi)


def walk_forward(h1: List[Candle], aligned: Dict[str, List[Optional[float]]],
                  config: MacroConfig, arm: str, folds: List[dict],
                  notional: float = 10_000.0, yield_key: str = "us10y_mom") -> dict:
    """Same fold geometry as every other evaluation in this project (12mo context /
    3mo validate / slide 3mo), scoring only trades ENTERED inside each validate
    window. Sharpe is 0.0 (neutral) for folds with fewer than 3 trades."""
    fold_sharpes, all_trades = [], []
    for fold in folds:
        pad = fold["train_end"] - timedelta(days=90)
        idx = _slice_idx(h1, pad, fold["validate_end"])
        sub_h1 = h1[idx.start:idx.stop]
        sub_aligned = {k: v[idx.start:idx.stop] for k, v in aligned.items()}
        tr = simulate(sub_h1, sub_aligned, config, arm, notional, yield_key)
        window = [x for x in tr if fold["train_end"] <= x.entry_time < fold["validate_end"]]
        m = compute_metrics(window, notional)
        fold_sharpes.append(m["sharpe"] if (m["sharpe"] is not None and m["n_trades"] >= 3) else 0.0)
        all_trades.extend(window)

    pooled = compute_metrics(all_trades, notional)
    pnls = np.array([x.pnl for x in all_trades])
    sig = {"t": None, "p": None, "ci_low": None, "ci_high": None}
    if len(pnls) > 1:
        t, p = stats.ttest_1samp(pnls / notional * 100, 0.0)
        rng = np.random.default_rng(42)
        sims = np.array([rng.choice(pnls, len(pnls), replace=True).sum()
                         for _ in range(5000)]) / notional * 100
        sig = {"t": float(t), "p": float(p),
               "ci_low": float(np.percentile(sims, 2.5)),
               "ci_high": float(np.percentile(sims, 97.5))}
    return {"median_sharpe": statistics.median(fold_sharpes) if fold_sharpes else 0.0,
            "pooled": pooled, "sig": sig, "trades": all_trades}


def _report(label: str, res: dict) -> None:
    p, s = res["pooled"], res["sig"]
    pf = p["profit_factor"]
    print(f"\n{label}")
    print(f"  Trades        {p['n_trades']}")
    print(f"  Win rate      {p['win_rate_pct']:.1f}%")
    print(f"  Profit factor {pf:.3f}" if pf is not None else "  Profit factor undefined")
    print(f"  Net P&L       {p['net_pnl_pct']:+.2f}%")
    print(f"  Max drawdown  {p['max_drawdown_pct']:.2f}%")
    print(f"  Median fold Sharpe {res['median_sharpe']:+.3f}")
    if s["p"] is not None:
        verdict = "SIGNIFICANT" if s["p"] < 0.05 else "not significant"
        print(f"  t={s['t']:.3f}  p={s['p']:.4f}  bootstrap 95% CI "
              f"[{s['ci_low']:+.2f}%, {s['ci_high']:+.2f}%]  -> {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Macro-filtered gold strategy, 2018-2026 walk-forward.")
    parser.add_argument("--slope-window", type=int, default=macro_features.DEFAULT_SLOPE_WINDOW)
    parser.add_argument("--arms", nargs="+", default=["macro_only", "v2_filtered"])
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2026-09-01")
    args = parser.parse_args()

    from src.lowfreq_v2_eval import generate_folds

    print("Loading gold H1 + aligned daily macro...")
    h1, aligned, daily = macro_features.load_h1_with_macro(
        start="2017-06-01", slope_window=args.slope_window)
    print(f"{len(h1)} H1 bars, {len(daily)} daily macro rows\n")

    # entries_v2's daily trend SMA, aligned the same no-lookahead way, for the filtered arm.
    from src.htf_align import align_htf_to_m5
    cfg = MacroConfig(slope_window=args.slope_window)
    dates = [d["date"] for d in daily]
    dcandles = [Candle(open_time=int(datetime.strptime(x, "%Y-%m-%d")
                                      .replace(tzinfo=timezone.utc).timestamp() * 1000),
                        open=d["gold"], high=d["gold"], low=d["gold"], close=d["gold"], volume=0.0)
                for x, d in zip(dates, daily)]
    trend_daily = sma([d["gold"] for d in daily], cfg.trend_sma_period)
    aligned["_trend_sma"] = align_htf_to_m5(h1, dcandles, trend_daily, macro_features.DAILY_MINUTES)

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    folds = generate_folds(start, end)
    print(f"{len(folds)} walk-forward folds, {folds[0]['train_end'].date()} -> "
          f"{folds[-1]['validate_end'].date()}  (12mo context / 3mo validate / slide 3mo)")
    print(f"Costs: ${MEASURED_SPREAD:.2f} spread charged BOTH sides + "
          f"${SLIPPAGE_PER_SIDE:.2f}/side slippage")
    print("=" * 78)

    for arm in args.arms:
        res = walk_forward(h1, aligned, cfg, arm, folds)
        _report(f"{arm.upper()}  (DXY slope < 0 AND US10Y momentum < 0, "
                f"{args.slope_window}d window)", res)

    # The real-yield variant: same rule, substituting the 10y TIPS real yield for
    # nominal. Reported because it is the economically correct driver for gold, and
    # the brief's nominal specification may simply be the wrong series.
    res = walk_forward(h1, aligned, cfg, "macro_only", folds, yield_key="real_mom")
    _report(f"MACRO_ONLY, REAL yield instead of nominal ({args.slope_window}d window)", res)


if __name__ == "__main__":
    main()
