# -*- coding: utf-8 -*-
"""
export_nas100_m5.py

Fetches NAS100 (Dukascopy USATECHIDXUSD) M5 bars via this project's hardened
fetcher and exports them in the CSV format the NASDAQ repo's loader expects
(`datetime,open,high,low,close,volume`, UTC).

Why this exists: the NASDAQ repo (github.com/Ngaakudzwe2/NASDAQ) implements a
NAS100 5-minute opening-momentum strategy and a backtester, but ships with no
data and has never been run -- Yahoo only serves 5-minute bars for ~60 days,
which is why. Its own data/README names Dukascopy as the source for 2019+ M5
index data. AMARO already has a circuit-breaker-protected Dukascopy fetcher, so
this connects the two.

Point value was verified by decoding real ticks, not assumed: raw ask 29172002
/ 1000 = 29172.00, with the sibling indices decoding to 7613.6 (S&P 500) and
52011.8 (Dow 30) on the same divisor -- three plausible 2026 levels, which is
the cross-check.

Also measured while doing this: the real NAS100 tick spread is ~0.89 points
(ask 29172.002 / bid 29171.109). The NASDAQ repo's config assumes 1.5 points
round trip, so for once a cost assumption is CONSERVATIVE rather than
optimistic -- the opposite of the $0.40-vs-$0.65 gold spread error.

Usage:
    python3 scripts/export_nas100_m5.py --fetch --start 2019-01-01
    python3 scripts/export_nas100_m5.py --out /path/to/NASDAQ/data/NAS100_M5.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import data_dukascopy

SYMBOL = "USATECHIDXUSD"


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch/export NAS100 M5 for the NASDAQ backtester.")
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default=None, help="exclusive; default = now")
    ap.add_argument("--fetch", action="store_true",
                     help="Fetch missing hours first (long-running; safe to interrupt and re-run).")
    ap.add_argument("--out", default="data/NAS100_M5.csv")
    ap.add_argument("--max-workers", type=int, default=4)
    args = ap.parse_args()

    if args.fetch:
        print(f"Fetching {SYMBOL} {args.start} -> {args.end or 'now'} "
              f"(circuit breaker active; already-done hours are skipped)...", flush=True)
        try:
            print(data_dukascopy.fetch_and_cache_range(
                SYMBOL, args.start, args.end, max_workers=args.max_workers, log=print), flush=True)
        except data_dukascopy.CircuitBreakerTripped as e:
            print(f"Circuit breaker stopped the fetch: {e}\nRe-run later to resume.", flush=True)

    m5 = data_dukascopy.load_m5_candles(args.start, args.end, symbol=SYMBOL)
    if not m5:
        print("No cached bars for that range -- run with --fetch first.")
        raise SystemExit(1)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["datetime", "open", "high", "low", "close", "volume"])
        for c in m5:
            dt = datetime.fromtimestamp(c.open_time / 1000, tz=timezone.utc)
            w.writerow([dt.strftime("%Y-%m-%d %H:%M:%S"), f"{c.open:.2f}", f"{c.high:.2f}",
                         f"{c.low:.2f}", f"{c.close:.2f}", int(c.volume)])

    first = datetime.fromtimestamp(m5[0].open_time / 1000, tz=timezone.utc)
    last = datetime.fromtimestamp(m5[-1].open_time / 1000, tz=timezone.utc)
    print(f"\n{len(m5):,} M5 bars -> {out}")
    print(f"  {first} -> {last}")
    print(f"  close range {min(c.close for c in m5):.2f} - {max(c.close for c in m5):.2f}")
    print("\nTimezone note: this export is UTC. Run the backtester with --tz UTC, "
          "or the 09:30 New York signal candle will be the wrong bar.")


if __name__ == "__main__":
    main()
