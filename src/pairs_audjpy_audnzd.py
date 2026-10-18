# -*- coding: utf-8 -*-
"""
pairs_audjpy_audnzd.py

Statistical-arbitrage system on the AUDJPY / AUDNZD spread -- the one candidate
that PASSED statistical screening (src/screening.py, 2026-09-10: Engle-Granger
p=0.0254 and spread Hurst 0.379 on post-SNB data, improving outside the pegged
era, with no pegged leg). Every directional gold strategy has been retired.

TIMEFRAME, stated plainly: the brief asked for 2018-2026 H1. Yahoo refuses
intraday history beyond 730 days, so H1 for these pairs starts 2024-01-01.
Daily reaches back to 2003. Both are therefore run:
  * DAILY 2018-2026 -- the full requested period, and the timeframe on which
    this candidate was screened. Daily closes are also the classic timeframe
    for pairs trading, not a downgrade.
  * H1 2024-2026 -- shorter (17k bars, ~2.8 years) but hourly, as a check on
    whether the relationship survives intraday.

THE LOOKAHEAD TRAP THIS AVOIDS. The hedge ratio beta and the z-score's
mean/sigma are estimated on a TRAILING window only, never on the full sample.
Fitting beta once over all data and then "backtesting" on it is the standard
way pairs backtests are accidentally faked: the spread is constructed using the
very prices the strategy then trades, so it is mean-reverting by construction.
Here beta is re-estimated at every bar from the preceding `beta_window` bars.

THE BRIEF'S RULE HAS NO STOP. Enter at |z|>2, exit at z=0 is unbounded on the
downside: if the relationship breaks (as EURCHF's did at the 2015 de-peg, which
is exactly how screening eliminated EURCHF~AUDCAD), the spread never returns to
zero and the position is held through an arbitrarily large loss. A z-stop and a
time-stop are therefore implemented and ON by default, and the no-stop variant
is reported alongside so the difference is visible rather than assumed.

COSTS. Both legs pay spread on entry and exit. AUDJPY quotes to 3 decimals
(point 0.001), AUDNZD to 5 (point 0.00001). Spread assumptions below are
illustrative retail figures in pips, NOT broker-verified -- the same standing
caveat as market_data.COST_PROFILES. Swap/financing is charged per day held on
both legs, since a pairs trade routinely sits open for days; it is the cost most
often omitted from pairs backtests, and omitting it here would flatter a
strategy whose whole design is to hold until reversion.

Usage:
    python3 -m src.pairs_audjpy_audnzd
    python3 -m src.pairs_audjpy_audnzd --interval 1h
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import stats
from statsmodels.tsa.stattools import adfuller, coint
from statsmodels.tsa.vector_ar.vecm import coint_johansen

from src import data_macro

LEG_Y, LEG_X = "AUDJPY", "AUDNZD"

# Illustrative retail costs, in PRICE units per leg, round-trip spread.
# AUDJPY: ~2 pips at 0.01/pip for a JPY cross. AUDNZD: ~3 pips at 0.0001/pip.
SPREAD = {"AUDJPY": 0.020, "AUDNZD": 0.00030}
# Financing per day held, as a fraction of each leg's notional. Pairs trades hold
# for days, so this compounds; it is the cost most pairs backtests leave out.
SWAP_PER_DAY_PCT = 0.0015  # ~0.55%/yr blended across both legs, charged as a cost


@dataclass
class PairsConfig:
    beta_window: int = 120      # trailing bars for the hedge ratio
    z_window: int = 60          # trailing bars for the z-score's mean/sigma
    entry_z: float = 2.0
    exit_z: float = 0.0
    stop_z: float = 3.5         # abandon if the spread keeps diverging
    max_hold_bars: int = 60     # time stop
    use_stops: bool = True
    risk_per_trade_pct: float = 1.0   # of notional, sized by spread volatility


@dataclass
class PairTrade:
    direction: str              # "long_spread" | "short_spread"
    entry_time: datetime
    exit_time: datetime
    entry_z: float
    exit_z: float
    exit_reason: str            # "target" | "stop_z" | "time" | "end"
    bars_held: int
    pnl: float


def load_pair(interval: str = "1d", start: str = "2018-01-01"
               ) -> Tuple[List[str], np.ndarray, np.ndarray]:
    """Aligned closes for both legs on their common timestamps."""
    if interval == "1d":
        raw = data_macro.fetch_many([LEG_Y, LEG_X], start=start, log=lambda *_: None)
        sy = data_macro.to_series(raw[LEG_Y])
        sx = data_macro.to_series(raw[LEG_X])
        rows = data_macro.align(sy, sx)
        rows = [r for r in rows if r[0] >= start]
    else:
        cy = data_macro.fetch_live_candles(LEG_Y, interval, limit=50_000, drop_forming=True)
        cx = data_macro.fetch_live_candles(LEG_X, interval, limit=50_000, drop_forming=True)
        my = {c.open_time: c.close for c in cy}
        mx = {c.open_time: c.close for c in cx}
        common = sorted(set(my) & set(mx))
        rows = [(datetime.fromtimestamp(t / 1000, tz=timezone.utc).isoformat(), my[t], mx[t])
                for t in common]
    dates = [r[0] for r in rows]
    return dates, np.array([r[1] for r in rows]), np.array([r[2] for r in rows])


def verify_cointegration(y: np.ndarray, x: np.ndarray) -> dict:
    """Johansen (trace + max-eigenvalue) and ADF on the OLS spread. Run on LOG prices.

    Johansen's null is r=0 (no cointegrating relationship); the statistic must EXCEED
    the critical value to reject it. ADF's null is a unit root; its p must be BELOW
    0.05 to reject. The two run in opposite directions, and reading either backwards
    inverts the conclusion -- so both are reported with their own verdict.
    """
    ly, lx = np.log(y), np.log(x)
    out: dict = {}

    jres = coint_johansen(np.column_stack([ly, lx]), det_order=0, k_ar_diff=1)
    # cvt/cvm columns are the 90%, 95%, 99% critical values.
    out["johansen"] = {
        "trace_stat_r0": float(jres.lr1[0]), "trace_crit_95": float(jres.cvt[0, 1]),
        "maxeig_stat_r0": float(jres.lr2[0]), "maxeig_crit_95": float(jres.cvm[0, 1]),
        "beta": float(-jres.evec[1, 0] / jres.evec[0, 0]),
    }
    out["johansen"]["trace_rejects_r0"] = out["johansen"]["trace_stat_r0"] > out["johansen"]["trace_crit_95"]
    out["johansen"]["maxeig_rejects_r0"] = out["johansen"]["maxeig_stat_r0"] > out["johansen"]["maxeig_crit_95"]

    beta_ols = float(np.polyfit(lx, ly, 1)[0])
    spread = ly - beta_ols * lx
    stat, p, _, nobs, crit, _ = adfuller(spread, autolag="AIC")
    out["adf"] = {"stat": float(stat), "p": float(p), "crit_5pct": float(crit["5%"]),
                  "nobs": int(nobs), "stationary": bool(p < 0.05), "beta_ols": beta_ols}
    out["engle_granger_p"] = float(coint(ly, lx)[1])
    return out


def _half_life(spread: np.ndarray) -> Optional[float]:
    """Ornstein-Uhlenbeck half-life of reversion, in bars. Tells you whether the
    exit horizon is plausible: a half-life far longer than max_hold_bars means the
    time stop fires before reversion can happen."""
    lag = spread[:-1]
    delta = np.diff(spread)
    if len(lag) < 10 or np.std(lag) == 0:
        return None
    b = np.polyfit(lag, delta, 1)[0]
    if b >= 0:
        return None
    return float(-np.log(2) / b)


def simulate(dates: List[str], y: np.ndarray, x: np.ndarray, config: PairsConfig,
             notional: float = 10_000.0, swap_per_day_pct: float = SWAP_PER_DAY_PCT,
             bars_per_day: float = 1.0, cost_mult: float = 1.0) -> List[PairTrade]:
    """
    Rolling-beta, rolling-z mean reversion. Everything is computed from a TRAILING
    window; no value at bar i uses any data from i or later.
    """
    ly, lx = np.log(y), np.log(x)
    n = len(ly)
    warm = max(config.beta_window, config.z_window) + 2

    trades: List[PairTrade] = []
    pos: Optional[dict] = None

    for i in range(warm, n):
        # Hedge ratio from the PRECEDING beta_window bars only.
        sl = slice(i - config.beta_window, i)
        beta = float(np.polyfit(lx[sl], ly[sl], 1)[0])
        spread_hist = ly[i - config.z_window:i] - beta * lx[i - config.z_window:i]
        mu, sd = float(np.mean(spread_hist)), float(np.std(spread_hist))
        if sd <= 0:
            continue
        s_now = ly[i] - beta * lx[i]
        z = (s_now - mu) / sd

        if pos is not None:
            held = i - pos["i"]
            reason = None
            if (pos["direction"] == "long_spread" and z >= config.exit_z) or \
               (pos["direction"] == "short_spread" and z <= config.exit_z):
                reason = "target"
            elif config.use_stops and abs(z) >= config.stop_z and \
                    np.sign(z) == np.sign(pos["entry_z"]):
                reason = "stop_z"
            elif config.use_stops and held >= config.max_hold_bars:
                reason = "time"
            elif i == n - 1:
                reason = "end"

            if reason:
                # Spread move, in log terms, times the dollar exposure on the y leg.
                move = (s_now - pos["s_entry"]) * (1 if pos["direction"] == "long_spread" else -1)
                gross = move * pos["exposure"]
                days = held / bars_per_day
                swap = pos["exposure"] * swap_per_day_pct / 100.0 * days * cost_mult
                trades.append(PairTrade(
                    direction=pos["direction"], entry_time=pos["t"],
                    exit_time=datetime.fromisoformat(dates[i]) if "T" in dates[i]
                    else datetime.strptime(dates[i], "%Y-%m-%d").replace(tzinfo=timezone.utc),
                    entry_z=pos["entry_z"], exit_z=z, exit_reason=reason, bars_held=held,
                    pnl=gross - pos["cost"] - swap))
                pos = None
            continue

        if abs(z) < config.entry_z:
            continue
        direction = "long_spread" if z <= -config.entry_z else "short_spread"

        # Dynamic sizing: exposure inversely proportional to spread volatility, so a
        # wide-sigma regime takes a smaller position for the same risk budget. Capped
        # at 3x notional -- without a cap, a near-zero sigma produces an absurd size.
        risk_budget = notional * config.risk_per_trade_pct / 100.0
        exposure = min(risk_budget / max(sd, 1e-6), notional * 3.0)

        # Round-trip spread on BOTH legs, scaled to this exposure.
        cost_frac = (SPREAD[LEG_Y] / y[i] + SPREAD[LEG_X] / x[i]) * cost_mult
        pos = {"direction": direction, "i": i, "entry_z": z, "s_entry": s_now,
               "exposure": exposure, "cost": exposure * cost_frac,
               "t": datetime.fromisoformat(dates[i]) if "T" in dates[i]
               else datetime.strptime(dates[i], "%Y-%m-%d").replace(tzinfo=timezone.utc)}

    return trades


def metrics(trades: List[PairTrade], notional: float = 10_000.0) -> dict:
    pnls = np.array([t.pnl for t in trades]) if trades else np.array([])
    n = len(pnls)
    wins, losses = pnls[pnls > 0], pnls[pnls < 0]
    gp, gl = float(wins.sum()), float(-losses.sum())
    equity = notional + np.cumsum(pnls) if n else np.array([notional])
    peak = np.maximum.accumulate(np.concatenate(([notional], equity)))[1:] if n else np.array([notional])
    dd = float(((peak - equity) / peak * 100).max()) if n else 0.0
    sharpe = None
    if n >= 2 and pnls.std() > 0:
        sharpe = float(pnls.mean() / pnls.std() * np.sqrt(n))
    return {"n_trades": n,
            "win_rate_pct": float(len(wins) / n * 100) if n else 0.0,
            "profit_factor": (gp / gl) if gl > 0 else None,
            "net_pnl_pct": float(pnls.sum() / notional * 100) if n else 0.0,
            "max_drawdown_pct": dd, "sharpe": sharpe,
            "avg_bars_held": float(np.mean([t.bars_held for t in trades])) if n else 0.0}


def walk_forward(dates: List[str], y: np.ndarray, x: np.ndarray, config: PairsConfig,
                 n_folds: int = 30, notional: float = 10_000.0,
                 bars_per_day: float = 1.0, cost_mult: float = 1.0) -> dict:
    """Contiguous folds over the series, scoring each fold's own trades. Each fold
    re-runs the simulation from a padded start so its rolling windows are warm, and
    counts only trades ENTERED inside the fold."""
    n = len(dates)
    warm = max(config.beta_window, config.z_window) + 2
    usable = n - warm
    fold_len = usable // n_folds
    fold_sharpes, all_trades = [], []

    for k in range(n_folds):
        lo = warm + k * fold_len
        hi = warm + (k + 1) * fold_len if k < n_folds - 1 else n
        pad = max(0, lo - warm)
        tr = simulate(dates[pad:hi], y[pad:hi], x[pad:hi], config, notional,
                      bars_per_day=bars_per_day, cost_mult=cost_mult)
        lo_t = dates[lo]
        lo_dt = (datetime.fromisoformat(lo_t) if "T" in lo_t
                 else datetime.strptime(lo_t, "%Y-%m-%d").replace(tzinfo=timezone.utc))
        fold_trades = [t for t in tr if t.entry_time >= lo_dt]
        m = metrics(fold_trades, notional)
        fold_sharpes.append(m["sharpe"] if (m["sharpe"] is not None and m["n_trades"] >= 3) else 0.0)
        all_trades.extend(fold_trades)

    pooled = metrics(all_trades, notional)
    pnls = np.array([t.pnl for t in all_trades]) if all_trades else np.array([])
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


def _print_result(label: str, res: dict) -> None:
    p, s = res["pooled"], res["sig"]
    pf = p["profit_factor"]
    print(f"\n{label}")
    print(f"  Trades             {p['n_trades']}")
    print(f"  Win rate           {p['win_rate_pct']:.1f}%")
    print(f"  Profit factor      {pf:.3f}" if pf is not None else "  Profit factor      undefined")
    print(f"  Net P&L            {p['net_pnl_pct']:+.2f}%")
    print(f"  Max drawdown       {p['max_drawdown_pct']:.2f}%")
    print(f"  Median fold Sharpe {res['median_sharpe']:+.3f}")
    print(f"  Avg bars held      {p['avg_bars_held']:.1f}")
    if s["p"] is not None:
        print(f"  t={s['t']:.3f}  p={s['p']:.4f}  bootstrap 95% CI "
              f"[{s['ci_low']:+.2f}%, {s['ci_high']:+.2f}%]  -> "
              f"{'SIGNIFICANT' if s['p'] < 0.05 else 'not significant'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="AUDJPY/AUDNZD statistical arbitrage.")
    ap.add_argument("--interval", default="1d", choices=["1d", "1h"])
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--folds", type=int, default=30)
    args = ap.parse_args()

    bars_per_day = 1.0 if args.interval == "1d" else 24.0
    cfg = PairsConfig() if args.interval == "1d" else PairsConfig(
        beta_window=480, z_window=240, max_hold_bars=240)

    dates, y, x = load_pair(args.interval, args.start)
    print(f"{LEG_Y}/{LEG_X} {args.interval}: {len(dates)} aligned bars, "
          f"{dates[0][:10]} -> {dates[-1][:10]}")

    print("\n" + "=" * 78 + "\n1. COINTEGRATION AND STATIONARITY (full sample, log prices)\n" + "=" * 78)
    v = verify_cointegration(y, x)
    j, a = v["johansen"], v["adf"]
    print(f"  Johansen trace     stat={j['trace_stat_r0']:.3f} vs 95% crit "
          f"{j['trace_crit_95']:.3f}  -> {'REJECTS no-cointegration' if j['trace_rejects_r0'] else 'cannot reject'}")
    print(f"  Johansen max-eigen stat={j['maxeig_stat_r0']:.3f} vs 95% crit "
          f"{j['maxeig_crit_95']:.3f}  -> {'REJECTS no-cointegration' if j['maxeig_rejects_r0'] else 'cannot reject'}")
    print(f"  Johansen beta      {j['beta']:.4f}   OLS beta {a['beta_ols']:.4f}")
    print(f"  ADF on spread      stat={a['stat']:.3f}  p={a['p']:.4f}  -> "
          f"{'STATIONARY (p<0.05)' if a['stationary'] else 'NOT stationary'}")
    print(f"  Engle-Granger      p={v['engle_granger_p']:.4f}")
    hl = _half_life(np.log(y) - a["beta_ols"] * np.log(x))
    print(f"  Reversion half-life {f'{hl:.1f} bars' if hl else 'n/a'}"
          f"  (time stop at {cfg.max_hold_bars} bars)")

    print("\n" + "=" * 78 + f"\n2-3. WALK-FORWARD, {args.folds} FOLDS, with spread + swap costs\n" + "=" * 78)
    _print_result(f"WITH STOPS (z-stop {cfg.stop_z}, time stop {cfg.max_hold_bars} bars)",
                  walk_forward(dates, y, x, cfg, args.folds, bars_per_day=bars_per_day))

    nostop = PairsConfig(**{**cfg.__dict__, "use_stops": False})
    _print_result("BRIEF'S RULE AS WRITTEN (no stop: enter |z|>2, exit z=0 only)",
                  walk_forward(dates, y, x, nostop, args.folds, bars_per_day=bars_per_day))

    _print_result("WITH STOPS, 2x costs (sensitivity)",
                  walk_forward(dates, y, x, cfg, args.folds, bars_per_day=bars_per_day, cost_mult=2.0))


if __name__ == "__main__":
    main()
