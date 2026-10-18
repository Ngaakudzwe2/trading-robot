# -*- coding: utf-8 -*-
"""
universe_screen.py

Wide-universe screen for ANY tradeable statistical structure, with multiple-
comparison correction applied. Added 2026-10-18.

WHY THE CORRECTION IS THE POINT. The 2026-09-10 screen tested 12 instruments
(66 pairs) at an uncorrected alpha=0.05, found 3 "cointegrated" pairs, and
nominated one for development. Chance alone predicts 3.3 false positives at that
threshold, so the screen found exactly its own noise floor and the nomination
was an artifact -- confirmed when AUDJPY/AUDNZD failed Johansen and ADF outright
on 2018-2026. This module exists so that mistake cannot recur: it screens 31
instruments (465 pairs) and reports every result against Bonferroni and
Benjamini-Hochberg thresholds alongside the raw p-value.

At 465 tests an uncorrected alpha=0.05 produces ~23 false positives BY
CONSTRUCTION. Any finding here that does not survive correction is noise, and
saying so is the module's main job.

  Bonferroni (family-wise): alpha / m. Controls the probability of ANY false
      positive. Strict; with m=465 the threshold is 0.000108.
  Benjamini-Hochberg (FDR): sort p ascending, find the largest k with
      p(k) <= k/m * alpha, reject all up to k. Controls the expected PROPORTION
      of false discoveries among rejections. More permissive than Bonferroni and
      the appropriate test for a screen whose purpose is generating candidates.

Both are reported because they answer different questions, and because a result
that passes FDR but fails Bonferroni is a genuine "worth a look", while one that
passes neither is nothing.

Tests run:
  Per instrument  Hurst exponent (variance-ratio) and ADF, on LOG PRICES.
                  Log prices, not returns: returns are stationary for every
                  tradeable instrument, so ADF on returns trivially rejects for
                  all of them and means nothing.
  Per pair        Engle-Granger cointegration and Johansen (trace, r=0), on log
                  prices. Johansen's statistic must EXCEED its critical value to
                  reject no-cointegration; ADF/EG p-values must fall BELOW
                  threshold. The two run in opposite directions.

Usage:
    python3 -m src.universe_screen
    python3 -m src.universe_screen --start 2018-01-01 --alpha 0.05
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import combinations
from typing import Dict, List, Optional, Tuple

import numpy as np
from statsmodels.tsa.stattools import adfuller, coint
from statsmodels.tsa.vector_ar.vecm import coint_johansen

from src import data_macro
from src.screening import hurst_exponent

MIN_OBS = 500


def benjamini_hochberg(pvalues: List[float], alpha: float = 0.05) -> Tuple[Optional[float], int]:
    """Returns (largest p that passes, number of rejections). The BH threshold is
    adaptive: it depends on the whole set of p-values, so it cannot be applied to a
    single result in isolation -- which is exactly why the earlier screen's p=0.0254
    looked meaningful on its own."""
    m = len(pvalues)
    if m == 0:
        return None, 0
    ordered = sorted(pvalues)
    passing = [p for k, p in enumerate(ordered, start=1) if p <= k / m * alpha]
    if not passing:
        return None, 0
    cutoff = max(passing)
    return cutoff, sum(1 for p in pvalues if p <= cutoff)


def screen_instruments(series: Dict[str, Dict[str, float]], alpha: float) -> dict:
    rows = []
    for name, s in series.items():
        closes = np.array([s[d] for d in sorted(s)], dtype=float)
        lp = np.log(closes[closes > 0])
        if len(lp) < MIN_OBS:
            continue
        try:
            stat, p, _, _, crit, _ = adfuller(lp, autolag="AIC")
        except Exception:
            continue
        rows.append({"name": name, "n": len(lp), "hurst": hurst_exponent(lp),
                      "adf_stat": float(stat), "adf_p": float(p)})
    ps = [r["adf_p"] for r in rows]
    bh_cut, bh_n = benjamini_hochberg(ps, alpha)
    bonf = alpha / len(ps) if ps else None
    for r in rows:
        r["passes_bonferroni"] = bonf is not None and r["adf_p"] <= bonf
        r["passes_fdr"] = bh_cut is not None and r["adf_p"] <= bh_cut
    return {"rows": sorted(rows, key=lambda r: r["adf_p"]), "m": len(ps),
            "bonferroni": bonf, "bh_cutoff": bh_cut, "bh_rejections": bh_n}


def screen_pairs(series: Dict[str, Dict[str, float]], alpha: float, log=print) -> dict:
    names = sorted(series)
    rows = []
    total = len(names) * (len(names) - 1) // 2
    for i, (a, b) in enumerate(combinations(names, 2), start=1):
        if i % 50 == 0:
            log(f"    ...{i}/{total} pairs")
        aligned = data_macro.align(series[a], series[b])
        if len(aligned) < MIN_OBS:
            continue
        x = np.log(np.array([r[1] for r in aligned], dtype=float))
        y = np.log(np.array([r[2] for r in aligned], dtype=float))
        if not (np.isfinite(x).all() and np.isfinite(y).all()):
            continue
        try:
            eg_p = float(coint(x, y)[1])
            j = coint_johansen(np.column_stack([x, y]), det_order=0, k_ar_diff=1)
            trace, crit95 = float(j.lr1[0]), float(j.cvt[0, 1])
        except Exception:
            continue
        beta = float(np.polyfit(x, y, 1)[0])
        rows.append({"pair": f"{a}~{b}", "n": len(aligned), "eg_p": eg_p,
                      "johansen_trace": trace, "johansen_crit95": crit95,
                      "johansen_rejects": trace > crit95, "beta": beta,
                      "spread_hurst": hurst_exponent(y - beta * x)})
    ps = [r["eg_p"] for r in rows]
    bh_cut, bh_n = benjamini_hochberg(ps, alpha)
    bonf = alpha / len(ps) if ps else None
    for r in rows:
        r["passes_bonferroni"] = bonf is not None and r["eg_p"] <= bonf
        r["passes_fdr"] = bh_cut is not None and r["eg_p"] <= bh_cut
        # A real stat-arb candidate needs BOTH tests agreeing AND a reverting spread.
        r["full_candidate"] = bool(r["passes_fdr"] and r["johansen_rejects"]
                                    and r["spread_hurst"] is not None and r["spread_hurst"] < 0.45)
    return {"rows": sorted(rows, key=lambda r: r["eg_p"]), "m": len(ps),
            "bonferroni": bonf, "bh_cutoff": bh_cut, "bh_rejections": bh_n}


def main() -> None:
    ap = argparse.ArgumentParser(description="Wide-universe FDR-corrected screen.")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--json-out", default="data/universe_screen.json")
    args = ap.parse_args()

    universe = data_macro.SCREEN_UNIVERSE
    print(f"Loading {len(universe)} instruments, daily, from {args.start}...")
    raw = data_macro.fetch_many(universe, start=args.start, log=lambda *_: None)
    series = {}
    for n, rows in raw.items():
        s = {d: v for d, v in data_macro.to_series(rows).items() if d >= args.start}
        if len(s) >= MIN_OBS:
            series[n] = s
    print(f"{len(series)} instruments with >= {MIN_OBS} daily bars\n")

    print("=" * 92)
    print("1. PER-INSTRUMENT: mean reversion vs trending (Hurst + ADF on log prices)")
    print("=" * 92)
    inst = screen_instruments(series, args.alpha)
    print(f"  {inst['m']} tests | Bonferroni alpha = {inst['bonferroni']:.6f} | "
          f"BH cutoff = {inst['bh_cutoff'] if inst['bh_cutoff'] else 'none passes'}")
    print(f"\n  {'instrument':12}{'n':>6}{'Hurst':>8}{'ADF p':>9}  {'Bonf':>5} {'FDR':>5}")
    for r in inst["rows"][:12]:
        h = f"{r['hurst']:.3f}" if r["hurst"] is not None else "n/a"
        print(f"  {r['name']:12}{r['n']:>6}{h:>8}{r['adf_p']:>9.4f}  "
              f"{'YES' if r['passes_bonferroni'] else '-':>5} {'YES' if r['passes_fdr'] else '-':>5}")
    n_bonf = sum(1 for r in inst["rows"] if r["passes_bonferroni"])
    print(f"\n  Survive Bonferroni: {n_bonf}   Survive FDR: {inst['bh_rejections']}   of {inst['m']}")
    hs = [r["hurst"] for r in inst["rows"] if r["hurst"] is not None]
    print(f"  Hurst range {min(hs):.3f} to {max(hs):.3f} (0.50 = random walk), "
          f"median {np.median(hs):.3f}")

    print("\n" + "=" * 92)
    print("2. PAIRWISE COINTEGRATION (Engle-Granger + Johansen on log prices)")
    print("=" * 92)
    pairs = screen_pairs(series, args.alpha)
    exp_fp = pairs["m"] * args.alpha
    raw_hits = sum(1 for r in pairs["rows"] if r["eg_p"] < args.alpha)
    print(f"  {pairs['m']} pair tests | Bonferroni alpha = {pairs['bonferroni']:.6f} | "
          f"BH cutoff = {pairs['bh_cutoff'] if pairs['bh_cutoff'] else 'none passes'}")
    print(f"\n  Raw hits at uncorrected p<{args.alpha}: {raw_hits}")
    print(f"  Expected false positives by chance:  {exp_fp:.1f}")
    print(f"  Survive Bonferroni: {sum(1 for r in pairs['rows'] if r['passes_bonferroni'])}")
    print(f"  Survive FDR:        {pairs['bh_rejections']}")
    print(f"  FULL candidates (FDR + Johansen agree + spread Hurst < 0.45): "
          f"{sum(1 for r in pairs['rows'] if r['full_candidate'])}")

    print(f"\n  {'pair':24}{'n':>6}{'EG p':>9}{'Johansen':>18}{'sprH':>7}  {'Bonf':>5} {'FDR':>5} {'FULL':>5}")
    for r in pairs["rows"][:15]:
        sh = f"{r['spread_hurst']:.3f}" if r["spread_hurst"] is not None else "n/a"
        jj = f"{r['johansen_trace']:.1f}/{r['johansen_crit95']:.1f}"
        print(f"  {r['pair']:24}{r['n']:>6}{r['eg_p']:>9.5f}{jj:>18}{sh:>7}  "
              f"{'YES' if r['passes_bonferroni'] else '-':>5} "
              f"{'YES' if r['passes_fdr'] else '-':>5} "
              f"{'YES' if r['full_candidate'] else '-':>5}")

    out = {"generated": datetime.now(timezone.utc).isoformat(), "start": args.start,
           "alpha": args.alpha, "instruments": inst, "pairs": pairs}
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nFull results -> {args.json_out}")


if __name__ == "__main__":
    main()
