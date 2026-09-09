# -*- coding: utf-8 -*-
"""
data_macro.py

Cross-asset / macro data feeds, added 2026-09-10 for the pivot away from
single-asset OHLC pattern strategies. Every prior strategy in this project
derived its signals from XAUUSD bars alone; ten rounds of that produced no
validated edge (see data/performance_report.md). This module supplies the
genuinely different INPUTS that pivot needs.

Two real sources, no synthetic data:

  Yahoo Finance (via yfinance) -- DXY, nominal Treasury yields, and the
      cross-currency pairs. Daily bars back to 2012 verified available for
      every ticker in MACRO_TICKERS.
  FRED (St. Louis Fed, CSV endpoint, no API key) -- DFII10, the 10-year
      TIPS constant-maturity REAL yield, and T10YIE, 10-year breakeven
      inflation. Real yields matter here specifically: gold's textbook
      driver is the real rate (the opportunity cost of holding a
      zero-coupon asset), not the nominal one. Using nominal ^TNX as a
      stand-in would conflate a rate move with an inflation-expectation
      move -- which are opposite in sign for gold -- so the real series is
      fetched rather than approximated.

FRED needs explicit certifi verification (its chain does not resolve
against the system store on this machine -- plain urllib fails with
CERTIFICATE_VERIFY_FAILED), so requests+certifi is used, matching how
data_deriv.py already handles SSL in this project.

Concurrency: fetch_many() runs each series in a thread pool. These are all
blocking network calls, so this keeps a multi-series pull from serialising
into minutes of dead time. Threads (not asyncio) because yfinance and
requests are both synchronous libraries.

Caching: every series is cached to data/macro_cache/<name>.csv and reused
unless force_refresh=True, so repeated research runs don't re-hammer either
provider.
"""

from __future__ import annotations

import csv
import io
import os
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List, Optional

import certifi
import requests

from src.candle import Candle

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "macro_cache")

# Yahoo tickers, all verified 2026-09-10 to return >3,600 daily bars from 2012.
MACRO_TICKERS: Dict[str, str] = {
    # Macro drivers
    "DXY": "DX-Y.NYB",        # US Dollar Index
    "US10Y": "^TNX",          # 10y nominal yield (x10, i.e. 42.5 == 4.25%)
    "US5Y": "^FVX",
    "US30Y": "^TYX",
    "VIX": "^VIX",
    "SPX": "^GSPC",
    # Instruments
    "XAUUSD": "GC=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    # Non-USD / cross pairs -- the diversification universe
    "EURJPY": "EURJPY=X",
    "AUDCAD": "AUDCAD=X",
    "GBPJPY": "GBPJPY=X",
    "EURGBP": "EURGBP=X",
    "AUDNZD": "AUDNZD=X",
    "EURCHF": "EURCHF=X",
    "CADJPY": "CADJPY=X",
    "AUDJPY": "AUDJPY=X",
}

# FRED series: name -> series id. Daily, no API key required.
FRED_SERIES: Dict[str, str] = {
    "US10Y_REAL": "DFII10",    # 10y TIPS real yield -- the gold-relevant rate
    "BREAKEVEN_10Y": "T10YIE",  # 10y breakeven inflation expectation
}

CROSS_PAIR_UNIVERSE = ["EURJPY", "AUDCAD", "GBPJPY", "EURGBP", "AUDNZD", "EURCHF", "CADJPY", "AUDJPY"]


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"{name}.csv")


def _write_cache(name: str, rows: List[dict]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not rows:
        return
    with open(_cache_path(name), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _read_cache(name: str) -> Optional[List[dict]]:
    p = _cache_path(name)
    if not os.path.exists(p):
        return None
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch_fred(name: str, force_refresh: bool = False) -> List[dict]:
    """One FRED series as [{"date","value"}]. Rows FRED marks "." (no observation --
    holidays, non-trading days) are dropped rather than forward-filled: an invented
    value here would silently become a fake signal downstream."""
    if not force_refresh:
        cached = _read_cache(name)
        if cached:
            return cached
    series_id = FRED_SERIES[name]
    resp = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}",
                         timeout=60, verify=certifi.where())
    resp.raise_for_status()
    reader = csv.reader(io.StringIO(resp.text))
    next(reader, None)  # header: observation_date,<SERIES_ID>
    rows = [{"date": r[0], "value": r[1]} for r in reader if len(r) > 1 and r[1] not in (".", "")]
    _write_cache(name, rows)
    return rows


def fetch_yahoo(name: str, start: str = "2012-01-01", end: Optional[str] = None,
                 interval: str = "1d", force_refresh: bool = False) -> List[dict]:
    """One Yahoo series as [{"date","open","high","low","close"}]."""
    cache_name = name if interval == "1d" else f"{name}_{interval}"
    if not force_refresh:
        cached = _read_cache(cache_name)
        if cached:
            return cached
    import yfinance as yf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.download(MACRO_TICKERS[name], start=start, end=end, interval=interval,
                          progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError(f"Yahoo returned no data for {name} ({MACRO_TICKERS[name]}) interval={interval}")
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)  # yfinance returns a MultiIndex for single tickers
    rows = []
    for ts, r in df.iterrows():
        try:
            rows.append({"date": ts.strftime("%Y-%m-%d %H:%M:%S"), "open": float(r["Open"]),
                          "high": float(r["High"]), "low": float(r["Low"]), "close": float(r["Close"])})
        except (ValueError, TypeError, KeyError):
            continue  # a NaN row (market holiday) -- skipped, never zero-filled
    _write_cache(cache_name, rows)
    return rows


def fetch_many(names: List[str], start: str = "2012-01-01", end: Optional[str] = None,
                interval: str = "1d", force_refresh: bool = False,
                max_workers: int = 6, log=print) -> Dict[str, List[dict]]:
    """
    Fetches several series concurrently. Accepts a mix of Yahoo names
    (MACRO_TICKERS) and FRED names (FRED_SERIES) and routes each to the right
    source. One series failing does NOT abort the rest -- it is reported and
    omitted, so a partial provider outage degrades the universe rather than
    killing the run.
    """
    out: Dict[str, List[dict]] = {}

    def _one(nm: str):
        if nm in FRED_SERIES:
            return nm, fetch_fred(nm, force_refresh=force_refresh)
        return nm, fetch_yahoo(nm, start=start, end=end, interval=interval, force_refresh=force_refresh)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_one, n): n for n in names}
        for fut in as_completed(futures):
            nm = futures[fut]
            try:
                nm, rows = fut.result()
                out[nm] = rows
                log(f"  {nm:16} {len(rows):>5} rows")
            except Exception as e:
                log(f"  {nm:16} FAILED: {type(e).__name__}: {e}")
    return out


def to_candles(rows: List[dict]) -> List[Candle]:
    """Yahoo rows -> this project's Candle type, so macro series can flow through the
    same indicator/backtest code paths as Dukascopy data."""
    out = []
    for r in rows:
        dt = datetime.strptime(r["date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        out.append(Candle(open_time=int(dt.timestamp() * 1000), open=float(r["open"]), high=float(r["high"]),
                           low=float(r["low"]), close=float(r["close"]), volume=0.0))
    return sorted(out, key=lambda c: c.open_time)


def to_series(rows: List[dict], value_key: str = "close") -> Dict[str, float]:
    """Rows -> {YYYY-MM-DD: value}. Date-keyed so series with different trading
    calendars (FX trades days the CME doesn't, FRED skips US holidays) can be
    aligned by intersection rather than by position -- aligning by index would
    silently pair up different dates."""
    key = "value" if "value" in (rows[0] if rows else {}) else value_key
    return {r["date"][:10]: float(r[key]) for r in rows if r.get(key) not in (None, "", ".")}


def align(*series: Dict[str, float]) -> List[tuple]:
    """Inner-joins several date-keyed series on their common dates, ascending."""
    if not series:
        return []
    common = set(series[0])
    for s in series[1:]:
        common &= set(s)
    return [(d,) + tuple(s[d] for s in series) for d in sorted(common)]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fetch cross-asset/macro series into data/macro_cache/.")
    parser.add_argument("--names", nargs="+",
                         default=list(MACRO_TICKERS) + list(FRED_SERIES))
    parser.add_argument("--start", default="2012-01-01")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()

    print(f"Fetching {len(args.names)} series (interval={args.interval}) concurrently...")
    got = fetch_many(args.names, start=args.start, interval=args.interval,
                      force_refresh=args.force_refresh)
    print(f"\n{len(got)}/{len(args.names)} series cached in {CACHE_DIR}")
