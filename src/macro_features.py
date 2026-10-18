# -*- coding: utf-8 -*-
"""
macro_features.py

Cross-asset feature engine: gold H1 execution bars carrying aligned DXY and
US10Y macro state. Added 2026-10-18 for the pivot off single-asset OHLC
patterns.

WHY THIS IS NOT PURE H1, which was the brief. Yahoo refuses intraday history
beyond 730 days -- it says so explicitly ("The requested range must be within
the last 730 days"), so H1 for DXY/US10Y starts 2024-05-26, not 2018, and a
2018-2026 walk-forward on hourly macro data cannot be built from it at any
price. ^TNX hourly is also thin (4,189 bars over 730 days against gold's
13,745 -- yields simply trade fewer hours), so even inside that window an
hourly yield correlation would be measuring gaps as much as moves.

What is used instead, and why it is a fair substitute rather than a
compromise:
  * Gold H1 from this project's own Dukascopy cache (2012-2026, complete) --
    the execution timeframe, unchanged.
  * DXY and US10Y DAILY from Yahoo, 2012-2026, aligned onto each H1 bar by
    medfreq's no-lookahead pointer-advance (an H1 bar only ever sees the most
    recently CLOSED daily macro bar). This is exactly the architecture
    entries_v2 already uses for its daily trend filter.
  * The 10y TIPS real yield (FRED DFII10) alongside nominal US10Y. The brief
    named US10Y, so nominal is computed and used as specified; real yield is
    computed too because gold's opportunity cost is the real rate, and a
    nominal move driven by inflation expectations pushes gold the OPPOSITE way
    to one driven by real rates. Both are reported so the difference is
    visible rather than assumed.

WINDOW TRANSLATION, stated plainly: the brief asked for a 24-HOUR rolling
correlation and slope. On daily macro series a 24-hour window holds one
observation, which no correlation can be computed from. `corr_window` and
`slope_window` are therefore counted in DAILY bars (default 24), which keeps
the short horizon the brief intended. A literal 1-day change is also available
(slope_window=1) and is reported in the prototype for comparison.

Slope is the ordinary least-squares gradient over the window, normalised to
percent-of-level per day for DXY so it is comparable across the period (a 0.3
move on a 90 index is not the same as on a 110 one). Yield momentum is the
change in percentage points, which is already level-independent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from src import data_dukascopy, data_macro
from src.candle import Candle
from src.htf_align import align_htf_to_m5

DAILY_MINUTES = 1440
DEFAULT_SLOPE_WINDOW = 24   # daily bars -- see module docstring on the hour/day translation
DEFAULT_CORR_WINDOW = 24


def _ols_slope(values: np.ndarray) -> Optional[float]:
    """Least-squares gradient per step. None on a degenerate window rather than 0.0,
    so 'no information' is never mistaken for 'flat'."""
    n = len(values)
    if n < 3 or not np.isfinite(values).all():
        return None
    x = np.arange(n, dtype=float)
    return float(np.polyfit(x, values, 1)[0])


def build_daily_macro(start: str = "2012-01-01") -> List[dict]:
    """
    Daily macro frame on the dates where gold, DXY and both yield series all
    trade. Joined BY DATE: these four have different calendars (CME gold, ICE
    dollar index, CBOE yields, FRED's US-holiday schedule), so pairing by row
    position would quietly compare different days.
    """
    raw = data_macro.fetch_many(["XAUUSD", "DXY", "US10Y", "US10Y_REAL"],
                                 start=start, log=lambda *_: None)
    gold = data_macro.to_series(raw["XAUUSD"])
    dxy = data_macro.to_series(raw["DXY"])
    # ^TNX is quoted in PERCENT already (4.81 == 4.81%), same as FRED's DFII10 --
    # verified directly against the feed 2026-10-18. An earlier version of this line
    # divided by 10 on the belief that Yahoo quotes it x10, which reported a 0.48%
    # ten-year yield against a 2.43% real yield: an impossibility (it implies -1.95%
    # inflation expectations) and the tell that caught the bug. Scaling would not have
    # flipped any momentum SIGN, so the strategy rule below would have looked fine while
    # every reported level and any magnitude threshold was wrong by 10x.
    nominal = data_macro.to_series(raw["US10Y"])
    real = data_macro.to_series(raw["US10Y_REAL"])

    # align() returns (date, *values) in the order the series were passed.
    rows = data_macro.align(gold, dxy, nominal, real)
    return [{"date": d, "gold": g, "dxy": x, "us10y": n, "us10y_real": r}
            for d, g, x, n, r in rows]


def compute_features(daily: List[dict], slope_window: int = DEFAULT_SLOPE_WINDOW,
                      corr_window: int = DEFAULT_CORR_WINDOW) -> List[dict]:
    """
    Adds, per daily row (None until its window has filled -- never back-filled):
      dxy_slope_pct   OLS gradient of DXY over slope_window, as % of level per day
      us10y_mom       change in nominal 10y yield over slope_window, percentage points
      real_mom        same for the 10y TIPS real yield
      dxy_1d, us10y_1d  the literal one-day changes, for the comparison arm
      corr_*          rolling correlations between the three daily return/change series
    """
    out: List[dict] = []
    for i, row in enumerate(daily):
        rec = dict(row)
        rec.update({k: None for k in ("dxy_slope_pct", "us10y_mom", "real_mom",
                                       "dxy_1d", "us10y_1d",
                                       "corr_dxy_us10y", "corr_dxy_gold", "corr_us10y_gold")})
        if i >= 1:
            rec["dxy_1d"] = (row["dxy"] / daily[i - 1]["dxy"] - 1.0) * 100
            rec["us10y_1d"] = row["us10y"] - daily[i - 1]["us10y"]
        if i >= slope_window:
            w = daily[i - slope_window:i + 1]
            s = _ols_slope(np.array([x["dxy"] for x in w]))
            if s is not None:
                rec["dxy_slope_pct"] = s / row["dxy"] * 100
            rec["us10y_mom"] = row["us10y"] - w[0]["us10y"]
            rec["real_mom"] = row["us10y_real"] - w[0]["us10y_real"]
        if i >= corr_window:
            w = daily[i - corr_window:i + 1]
            g = np.diff([x["gold"] for x in w]) / np.array([x["gold"] for x in w[:-1]])
            d = np.diff([x["dxy"] for x in w]) / np.array([x["dxy"] for x in w[:-1]])
            y = np.diff([x["us10y"] for x in w])
            for key, (a, b) in {"corr_dxy_us10y": (d, y), "corr_dxy_gold": (d, g),
                                 "corr_us10y_gold": (y, g)}.items():
                if np.std(a) > 0 and np.std(b) > 0:
                    rec[key] = float(np.corrcoef(a, b)[0, 1])
        out.append(rec)
    return out


def correlation_matrix(features: List[dict]) -> Dict[str, Dict[str, Optional[float]]]:
    """Latest rolling correlation matrix, as a 3x3 for reporting."""
    latest = next((f for f in reversed(features) if f["corr_dxy_us10y"] is not None), None)
    names = ["GOLD", "DXY", "US10Y"]
    if latest is None:
        return {a: {b: None for b in names} for a in names}
    pairs = {("DXY", "US10Y"): latest["corr_dxy_us10y"],
             ("DXY", "GOLD"): latest["corr_dxy_gold"],
             ("US10Y", "GOLD"): latest["corr_us10y_gold"]}
    m: Dict[str, Dict[str, Optional[float]]] = {a: {b: None for b in names} for a in names}
    for a in names:
        m[a][a] = 1.0
    for (a, b), v in pairs.items():
        m[a][b] = m[b][a] = v
    return m


def load_h1_with_macro(start: str = "2012-01-01", slope_window: int = DEFAULT_SLOPE_WINDOW,
                        corr_window: int = DEFAULT_CORR_WINDOW
                        ) -> Tuple[List[Candle], Dict[str, List[Optional[float]]], List[dict]]:
    """
    Returns (gold H1 candles, {feature name: value aligned per H1 bar}, daily feature rows).

    Alignment is the no-lookahead pointer-advance from htf_align: an H1 bar sees only
    the most recently CLOSED daily macro bar. Without that, a bar would read macro
    state published after it traded -- which would manufacture an edge out of nothing,
    the single easiest way to fake a good backtest.
    """
    m5 = data_dukascopy.load_m5_candles(start)
    h1 = data_dukascopy.resample(m5, 60)

    daily = compute_features(build_daily_macro(start), slope_window, corr_window)
    # Synthetic daily candles carrying the macro dates, so align_htf_to_m5 can advance
    # its pointer on real close times.
    dcandles = [Candle(open_time=int(datetime.strptime(d["date"], "%Y-%m-%d")
                                      .replace(tzinfo=timezone.utc).timestamp() * 1000),
                        open=d["gold"], high=d["gold"], low=d["gold"], close=d["gold"], volume=0.0)
                for d in daily]

    keys = ["dxy_slope_pct", "us10y_mom", "real_mom", "dxy_1d", "us10y_1d",
            "corr_dxy_us10y", "corr_dxy_gold", "corr_us10y_gold"]
    aligned = {k: align_htf_to_m5(h1, dcandles, [d[k] for d in daily], DAILY_MINUTES) for k in keys}
    return h1, aligned, daily


if __name__ == "__main__":
    h1, aligned, daily = load_h1_with_macro()
    print(f"Gold H1 bars: {len(h1)}   daily macro rows: {len(daily)}")
    print(f"Macro span: {daily[0]['date']} -> {daily[-1]['date']}")
    covered = sum(1 for v in aligned["dxy_slope_pct"] if v is not None)
    print(f"H1 bars with macro state: {covered} / {len(h1)} ({covered / len(h1) * 100:.1f}%)\n")

    m = correlation_matrix(daily)
    print(f"Rolling {DEFAULT_CORR_WINDOW}-day correlation matrix (latest):")
    names = ["GOLD", "DXY", "US10Y"]
    print("            " + "".join(f"{n:>9}" for n in names))
    for a in names:
        cells = "".join(f"{m[a][b]:>9.3f}" if m[a][b] is not None else f"{'n/a':>9}" for b in names)
        print(f"  {a:10}{cells}")

    last = daily[-1]
    print(f"\nLatest ({last['date']}): gold={last['gold']:.2f} dxy={last['dxy']:.2f} "
          f"us10y={last['us10y']:.2f}% real={last['us10y_real']:.2f}%")
    print(f"  {DEFAULT_SLOPE_WINDOW}d DXY slope: {last['dxy_slope_pct']:+.4f} %/day")
    print(f"  {DEFAULT_SLOPE_WINDOW}d US10Y momentum: {last['us10y_mom']:+.3f} pp")
    print(f"  {DEFAULT_SLOPE_WINDOW}d real-yield momentum: {last['real_mom']:+.3f} pp")
