# -*- coding: utf-8 -*-
"""
macro_regime.py

Macro regime classification for XAUUSD from cross-asset inputs (DXY and the
10-year TIPS REAL yield), plus an honest test of whether that classification
actually separates forward gold returns.

Why real yields and not nominal: gold pays no coupon, so its opportunity cost
is the REAL rate. A nominal-yield rise driven by higher inflation expectations
is typically gold-POSITIVE, while the same nominal rise driven by higher real
rates is gold-NEGATIVE. Using nominal ^TNX would blend those opposite cases
into one number. FRED's DFII10 is used instead (see data_macro).

Signals produced per day:
  real_yield_mom   : change in the 10y real yield over `mom_window` days.
                     Falling real yields are the textbook gold tailwind.
  dxy_mom          : DXY change over the same window. A stronger dollar is
                     the textbook gold headwind.
  corr             : rolling correlation between daily DXY returns and daily
                     real-yield CHANGES. This is a regime descriptor, not a
                     direction: when the two move together, gold faces a
                     coherent macro driver; when they decouple, gold is more
                     likely being driven by something else (risk-off flows,
                     central-bank buying) and the macro read is less reliable.

classify() then gives one of: SUPPORTIVE / HOSTILE / NEUTRAL.

IMPORTANT: this module deliberately ships with test_separation(), and the
verdict it prints should be read before the filter is used for anything.
Building a plausible-sounding macro filter is easy; the whole lesson of this
project's first ten rounds is that plausible-sounding is not the same as
predictive. If the separation test shows no meaningful difference in forward
returns between SUPPORTIVE and HOSTILE days, this filter should NOT be used,
however sensible the economics sound.

Usage:
    python3 -m src.macro_regime
"""

from __future__ import annotations

import argparse
from typing import Dict, List, Optional, Tuple

import numpy as np

from src import data_macro

DEFAULT_MOM_WINDOW = 20    # ~1 trading month
DEFAULT_CORR_WINDOW = 60   # ~1 trading quarter


def build_signals(mom_window: int = DEFAULT_MOM_WINDOW,
                   corr_window: int = DEFAULT_CORR_WINDOW,
                   start: str = "2012-01-01") -> List[dict]:
    """Aligns gold, DXY and the real yield on common dates and derives the regime
    inputs. Alignment is by DATE, not position -- these three series have different
    trading calendars (FX vs CME vs FRED's US-holiday schedule), so index-based
    pairing would silently compare different days."""
    raw = data_macro.fetch_many(["XAUUSD", "DXY", "US10Y_REAL"], start=start, log=lambda *_: None)
    gold = data_macro.to_series(raw["XAUUSD"])
    dxy = data_macro.to_series(raw["DXY"])
    ry = data_macro.to_series(raw["US10Y_REAL"])

    rows = data_macro.align(gold, dxy, ry)
    out: List[dict] = []
    for i, (date, g, d, r) in enumerate(rows):
        rec = {"date": date, "gold": g, "dxy": d, "real_yield": r,
               "real_yield_mom": None, "dxy_mom": None, "corr": None}
        if i >= mom_window:
            rec["real_yield_mom"] = r - rows[i - mom_window][3]
            rec["dxy_mom"] = (d / rows[i - mom_window][2] - 1.0) * 100
        if i >= corr_window:
            window = rows[i - corr_window:i + 1]
            d_ret = np.diff([w[2] for w in window]) / np.array([w[2] for w in window[:-1]])
            r_chg = np.diff([w[3] for w in window])
            if np.std(d_ret) > 0 and np.std(r_chg) > 0:
                rec["corr"] = float(np.corrcoef(d_ret, r_chg)[0, 1])
        out.append(rec)
    return out


def classify(rec: dict) -> Optional[str]:
    """SUPPORTIVE  = real yields falling AND dollar weakening (both tailwinds)
       HOSTILE     = real yields rising AND dollar strengthening (both headwinds)
       NEUTRAL     = the drivers disagree, or warmup data is missing.
    Thresholds are exact zero rather than tuned bands, on purpose -- introducing
    tunable cutoffs here would reopen the same curve-fitting surface this pivot
    was meant to get away from."""
    if rec["real_yield_mom"] is None or rec["dxy_mom"] is None:
        return None
    supportive = rec["real_yield_mom"] < 0 and rec["dxy_mom"] < 0
    hostile = rec["real_yield_mom"] > 0 and rec["dxy_mom"] > 0
    if supportive:
        return "SUPPORTIVE"
    if hostile:
        return "HOSTILE"
    return "NEUTRAL"


def test_separation(rows: List[dict], horizon: int = 20) -> dict:
    """
    The honesty check: do SUPPORTIVE days actually precede better gold returns
    than HOSTILE days? Compares forward `horizon`-day gold returns bucketed by
    regime, with a Welch t-test on the two extreme buckets.

    A filter that sounds economically right but shows no separation here is not
    a filter -- it is a story.
    """
    buckets: Dict[str, List[float]] = {"SUPPORTIVE": [], "HOSTILE": [], "NEUTRAL": []}
    for i, rec in enumerate(rows):
        if i + horizon >= len(rows):
            break
        label = classify(rec)
        if label is None:
            continue
        fwd = (rows[i + horizon]["gold"] / rec["gold"] - 1.0) * 100
        buckets[label].append(fwd)

    # NON-OVERLAPPING buckets: stepping by `horizon` so no two observations share a
    # single forward day. This matters enormously and is the difference between a
    # real result and a mirage -- with daily sampling, consecutive 20-day forward
    # returns share 19 of their 20 days, so the "n" in an overlapping test is
    # inflated roughly `horizon`-fold and its p-value is correspondingly far too
    # small. Measured directly here: the 20d overlapping test reports p=0.007 on
    # n=1029/1116, while the same data sampled independently gives n=50/54 and
    # p=0.073. The overlapping figure is reported for reference only and must not
    # be used to justify trading anything.
    indep: Dict[str, List[float]] = {"SUPPORTIVE": [], "HOSTILE": [], "NEUTRAL": []}
    for i in range(0, len(rows) - horizon, horizon):
        label = classify(rows[i])
        if label is None:
            continue
        indep[label].append((rows[i + horizon]["gold"] / rows[i]["gold"] - 1.0) * 100)

    from scipy import stats
    sup, hos = np.array(buckets["SUPPORTIVE"]), np.array(buckets["HOSTILE"])
    result = {"horizon": horizon,
              "counts": {k: len(v) for k, v in buckets.items()},
              "mean_fwd_pct": {k: (float(np.mean(v)) if v else None) for k, v in buckets.items()},
              "median_fwd_pct": {k: (float(np.median(v)) if v else None) for k, v in buckets.items()},
              "t": None, "p": None}
    if len(sup) > 30 and len(hos) > 30:
        t, p = stats.ttest_ind(sup, hos, equal_var=False)  # Welch: unequal variances/sizes
        result["t"], result["p"] = float(t), float(p)

    isup, ihos = np.array(indep["SUPPORTIVE"]), np.array(indep["HOSTILE"])
    result["indep_counts"] = {k: len(v) for k, v in indep.items()}
    result["indep_mean_fwd_pct"] = {k: (float(np.mean(v)) if v else None) for k, v in indep.items()}
    result["indep_t"] = result["indep_p"] = None
    if len(isup) > 5 and len(ihos) > 5:
        t, p = stats.ttest_ind(isup, ihos, equal_var=False)
        result["indep_t"], result["indep_p"] = float(t), float(p)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="XAUUSD macro regime filter from DXY + real yields.")
    parser.add_argument("--mom-window", type=int, default=DEFAULT_MOM_WINDOW)
    parser.add_argument("--corr-window", type=int, default=DEFAULT_CORR_WINDOW)
    parser.add_argument("--horizons", type=int, nargs="+", default=[5, 20, 60])
    args = parser.parse_args()

    print("Building macro regime signals (XAUUSD vs DXY + 10y TIPS real yield)...")
    rows = build_signals(mom_window=args.mom_window, corr_window=args.corr_window)
    print(f"{len(rows)} aligned trading days, {rows[0]['date']} -> {rows[-1]['date']}\n")

    latest = rows[-1]
    print(f"Latest ({latest['date']}): gold={latest['gold']:.2f}  DXY={latest['dxy']:.2f}  "
          f"real_yield={latest['real_yield']:.2f}%")
    print(f"  {args.mom_window}d real-yield change: {latest['real_yield_mom']:+.3f} pp")
    print(f"  {args.mom_window}d DXY change:        {latest['dxy_mom']:+.2f}%")
    if latest["corr"] is not None:
        print(f"  {args.corr_window}d DXY/real-yield corr: {latest['corr']:+.3f}")
    print(f"  => REGIME: {classify(latest)}\n")

    print("=" * 78)
    print("SEPARATION TEST -- does the regime actually predict forward gold returns?")
    print("=" * 78)
    for h in args.horizons:
        res = test_separation(rows, horizon=h)
        c, m = res["counts"], res["mean_fwd_pct"]
        print(f"\n  Forward {h}d return by regime:")
        for k in ("SUPPORTIVE", "NEUTRAL", "HOSTILE"):
            mean = f"{m[k]:+.2f}%" if m[k] is not None else "n/a"
            med = res["median_fwd_pct"][k]
            med_s = f"{med:+.2f}%" if med is not None else "n/a"
            print(f"    {k:11} n={c[k]:>5}  mean={mean:>8}  median={med_s:>8}")
        if res["p"] is not None:
            spread = m["SUPPORTIVE"] - m["HOSTILE"]
            print(f"    SUPPORTIVE - HOSTILE = {spread:+.2f}%")
            print(f"      overlapping     (INFLATED, reference only): p={res['p']:.4g}")
        if res["indep_p"] is not None:
            im = res["indep_mean_fwd_pct"]; ic = res["indep_counts"]
            verdict = "SIGNIFICANT" if res["indep_p"] < 0.05 else "NOT significant"
            print(f"      non-overlapping (the honest test): n={ic['SUPPORTIVE']}/{ic['HOSTILE']}  "
                  f"diff={im['SUPPORTIVE'] - im['HOSTILE']:+.2f}%  "
                  f"t={res['indep_t']:.2f} p={res['indep_p']:.4g}  -> {verdict}")


if __name__ == "__main__":
    main()
