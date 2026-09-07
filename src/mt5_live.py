# -*- coding: utf-8 -*-
"""
mt5_live.py

Connects Hypothesis 2 -- session order blocks
(src/entries_v4_session_ob.py) -- to a live MT5 terminal via
src.mt5_zmq_bridge -- a ZeroMQ socket connector to the companion
mt5_bridge_ea/AmaroZmqBridge.mq5 Expert Advisor, used INSTEAD OF the
Windows-only MetaTrader5 Python package (see mt5_executor.py, which
remains here unused-by-default as a reference/alternative for anyone who
does end up running this on native Windows). This module now runs
entirely natively on macOS/Linux; only the MT5 terminal + EA (the actual
order-execution "hands") still needs an MT5 runtime somewhere.

`bridge.connect()` hard-refuses any account that isn't flagged as a demo
account, and the EA itself refuses to even initialize on a non-demo
account (defense in depth -- see AmaroZmqBridge.mq5's OnInit). Neither
this module nor either half of the bridge exposes any path to a
real-money account. Genuine side benefit of this architecture: no MT5
login credentials ever pass through this module, or any Python code, or
a .env file -- you log into the terminal manually via its own GUI.

Unlike the v1 path this replaced, SL/TP here are NOT an extra live-only
risk layer bolted onto a stopless backtest -- entries_v4_session_ob is a
bracket strategy whose ATR stop and target ARE the exit rule, so live
execution and the backtest share one risk model. latest_signal() was
verified against simulate() on real 2012-2017 data: identical direction,
SL and TP on 15/15 entry bars, 0 false signals across 60 control bars.

Read before trusting results from this: Hypothesis 2 has NOT cleared
statistical significance. Pooled across XAUUSD+EURUSD it stands at 47
trades, PF 1.659, p=0.126, and on EURUSD alone p=0.845 -- i.e. it failed
its cross-asset falsification test. Running it here is forward-testing on
a demo account to accumulate genuinely fresh out-of-sample data, which is
the one thing that could still resolve that question. It is not the
deployment of a proven edge.

Position sizing is risk-based (risk a fixed dollar amount per trade,
sized against the ATR stop distance via bridge.calc_lot_size), which is
different from the backtest's notional-based sizing (trading_robot.py
buys/sells a fixed dollar notional regardless of distance to stop).

Trade decisions are logged to data/mt5_live_trades.csv -- kept separate
from data/ledger.csv (same isolation pattern as live_monitor.py's
paper_trades.csv) so live/demo activity never contaminates the memory
system's backtest-derived history. memory.check_memory is deliberately NOT
consulted on this path -- v4 is measured as an isolated hypothesis, and
layering v1's loss-memory filter over it would change what is being
tested. The memory module is still imported only for DATA_DIR.

*** UNTESTED end-to-end -- see mt5_zmq_bridge.py and
mt5_bridge_ea/AmaroZmqBridge.mq5 for what's independently verified
(the Python<->EA wire protocol) vs. what still needs validating against
a real MT5 terminal (the EA's own MQL5 trade calls). ***

Usage (after MT5_SETUP.md's steps -- terminal running, EA attached and
showing "bound tcp://*:5555..." in its Experts log, connectivity smoke
test via `python -m src.mt5_zmq_bridge` passing first):
    python -m src.mt5_live --symbol XAUUSD --mt5-symbol XAUUSD --risk-pct 1.0
    python -m src.mt5_live --symbol XAUUSD --mt5-symbol XAUUSD.a --max-iterations 1   # smoke test, one poll then exit

Broker symbol names vary (e.g. "XAUUSD" vs "XAUUSD.a" vs "GOLD") -- check
your broker's Market Watch panel for the exact tradeable name and pass it
via --mt5-symbol; --symbol stays as the internal name used for cost
lookups (market_data.py) and memory/ledger tagging so it lines up with
the rest of this project's XAUUSD/USDJPY conventions.
"""

from __future__ import annotations

import csv
import os
import time
from datetime import datetime, timezone
from typing import Optional

from src.entries_v4_session_ob import SessionOBConfig, latest_signal
from src.entries_v2 import Direction
from src import memory
from src import mt5_zmq_bridge as bridge

MAGIC = 202607
LIVE_LOG_PATH = os.path.join(memory.DATA_DIR, "mt5_live_trades.csv")
LIVE_LOG_HEADER = ["timestamp", "symbol", "mt5_symbol", "action", "price", "lot",
                    "sl", "tp", "ticket", "reason", "outcome", "pnl", "slippage", "comment"]

ATR_PERIOD = 14
DEFAULT_ATR_SL_MULT = 2.0
DEFAULT_ATR_TP_MULT = 3.0
DEFAULT_RISK_PCT = 1.0  # % of account balance risked per trade, via calc_lot_size


def _ensure_live_log() -> None:
    os.makedirs(memory.DATA_DIR, exist_ok=True)
    if not os.path.exists(LIVE_LOG_PATH):
        with open(LIVE_LOG_PATH, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(LIVE_LOG_HEADER)


def _log_live_trade(symbol: str, mt5_symbol: str, action: str, price: Optional[float], lot: Optional[float],
                     sl: Optional[float], tp: Optional[float], ticket: Optional[int], reason: str,
                     outcome: str, pnl: float, comment: str, slippage: Optional[float] = None) -> None:
    _ensure_live_log()
    ts = datetime.now(timezone.utc).isoformat()
    with open(LIVE_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([ts, symbol, mt5_symbol, action,
                                 f"{price:.5f}" if price is not None else "",
                                 f"{lot:.2f}" if lot is not None else "",
                                 f"{sl:.5f}" if sl is not None else "",
                                 f"{tp:.5f}" if tp is not None else "",
                                 ticket if ticket is not None else "",
                                 reason, outcome, f"{pnl:.2f}",
                                 f"{slippage:.5f}" if slippage is not None else "",
                                 comment])


def run_live(symbol: str = "XAUUSD", mt5_symbol: Optional[str] = None, notional: float = 10_000.0,
             risk_pct: float = DEFAULT_RISK_PCT, config: Optional[SessionOBConfig] = None,
             h1_history: int = 1500, h4_history: int = 600, poll_seconds: float = 900.0,
             max_iterations: Optional[int] = None, host: str = bridge.DEFAULT_HOST,
             port: int = bridge.DEFAULT_PORT, log=print) -> None:
    """
    Live MT5 (demo-only) execution of Hypothesis 2 -- session order blocks
    (src/entries_v4_session_ob.py). Replaced the legacy v1 daily-crossover +
    memory logic on 2026-09-07.

    Signals come from entries_v4_session_ob.latest_signal(), which shares
    _build_order_blocks() and the zone/trigger rules with the backtest's
    simulate() -- verified on real 2012-2017 data to agree exactly on
    direction, stop-loss and take-profit (15/15 entry bars, 0 false signals on
    60 control bars) when handed the same fill price. That parity is the whole
    point of routing through that function rather than re-deriving entries here.

    Risk model, and how it differs from the old v1 path:
      * Stop-loss and take-profit are the STRATEGY's own ATR-derived levels
        (atr_sl_mult / atr_tp_mult on SessionOBConfig), anchored to the live
        ask/bid actually being paid -- not to module-level multipliers. The old
        DEFAULT_ATR_SL_MULT/DEFAULT_ATR_TP_MULT constants no longer influence
        execution; the config owns them.
      * Position size still comes from risk_pct of balance divided by the stop
        distance, via bridge.calc_lot_size -- unchanged, and independent of
        `notional` (which is only used for log bookkeeping).
      * Exits are NOT managed here. SL/TP are attached to the market order, so
        the broker closes the position. That is why this loop has no SELL
        branch: v4 is a bracket-order strategy, unlike v1 which exited on an
        opposite crossover.
      * memory.check_memory is deliberately NOT consulted. v4 is being
        forward-tested as an isolated hypothesis; layering v1's loss-memory
        filter on top would change what is being measured.

    Polls H1 (execution) and H4 (order-block identification) and acts only on a
    newly-closed H1 bar. Skips entry when a position with this MAGIC is already
    open -- see latest_signal()'s documented divergence about zone consumption.
    """
    mt5_symbol = mt5_symbol or symbol
    config = config or SessionOBConfig()

    info = bridge.connect(host=host, port=port)
    log(f"[MT5 LIVE] Connected via ZeroMQ bridge. Login {info.get('login')} on {info.get('server')} "
        f"(demo, balance {info.get('balance')} {info.get('currency')}). Trading {mt5_symbol} as '{symbol}'.")
    log(f"[MT5 LIVE] Strategy: session order blocks (v4) {config.as_dict()}")
    log(f"[MT5 LIVE] Risk: {risk_pct}% of balance per trade; SL/TP from the strategy's ATR levels.\n")

    try:
        h1 = bridge.get_rates(mt5_symbol, "H1", start_pos=1, count=h1_history)
        h4 = bridge.get_rates(mt5_symbol, "H4", start_pos=1, count=h4_history)
        log(f"[MT5 LIVE] Seeded {len(h1)} H1 and {len(h4)} H4 candles through "
            f"{datetime.fromtimestamp(h1[-1].open_time / 1000, tz=timezone.utc)}.")

        last_seen_open_time = h1[-1].open_time
        iteration = 0

        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            latest = bridge.get_rates(mt5_symbol, "H1", start_pos=1, count=1)
            if latest and latest[0].open_time > last_seen_open_time:
                # Refetch both series rather than appending: an H4 bar may have closed
                # too, and order-block state depends on the H4 series being current.
                h1 = bridge.get_rates(mt5_symbol, "H1", start_pos=1, count=h1_history)
                h4 = bridge.get_rates(mt5_symbol, "H4", start_pos=1, count=h4_history)
                last_seen_open_time = h1[-1].open_time
                bar_dt = datetime.fromtimestamp(h1[-1].open_time / 1000, tz=timezone.utc)
                log(f"[MT5 LIVE] New H1 bar {bar_dt}  C{h1[-1].close}")

                existing = bridge.get_open_position(mt5_symbol, magic=MAGIC)
                if existing is not None:
                    log(f"  => Position already open (ticket {existing['ticket']}, "
                        f"{existing['volume']} lots) -- SL/TP manage it; no new entry.\n")
                elif latest_signal(h1, h4, config) is None:
                    log("  => No order-block entry on this bar.\n")
                else:
                    tick = bridge.get_tick(mt5_symbol)
                    probe = latest_signal(h1, h4, config)
                    entry_est = tick["ask"] if probe["direction"] == Direction.LONG else tick["bid"]
                    # Re-derive with the real fill so SL/TP measure risk from what we pay.
                    sig = latest_signal(h1, h4, config, entry_price=entry_est)
                    side = "buy" if sig["direction"] == Direction.LONG else "sell"
                    stop_distance = abs(entry_est - sig["sl"])
                    reason = (f"v4-session-ob {side} zone[{sig['zone_low']:.2f},{sig['zone_high']:.2f}] "
                              f"atr={sig['atr']:.2f}")

                    if stop_distance <= 0:
                        log("  => SKIPPED: non-positive stop distance.\n")
                    else:
                        balance = bridge.get_balance()
                        risk_amount = balance * (risk_pct / 100.0)
                        lot = bridge.calc_lot_size(mt5_symbol, risk_amount, stop_distance)
                        result = bridge.place_market_order(mt5_symbol, side, lot, sl=sig["sl"], tp=sig["tp"],
                                                            magic=MAGIC, expected_price=entry_est)
                        if result.success:
                            slip_str = (f", slippage {result.slippage:+.5f} vs quoted {entry_est:.5f}"
                                        if result.slippage is not None else "")
                            log(f"  => [MT5 LIVE] {side.upper()} {lot} lots @ {result.price} "
                                f"(SL {sig['sl']:.2f} / TP {sig['tp']:.2f}, ticket {result.ticket}{slip_str}).\n")
                        else:
                            log(f"  => [MT5 LIVE] {side.upper()} FAILED: {result.comment} "
                                f"(retcode {result.retcode}).\n")
                        _log_live_trade(symbol, mt5_symbol, side.upper(), result.price, lot,
                                         sig["sl"], sig["tp"], result.ticket, reason,
                                         "OPEN" if result.success else "FAILED", 0.0, result.comment,
                                         slippage=result.slippage)

            if max_iterations is None or iteration < max_iterations:
                time.sleep(poll_seconds)

    finally:
        bridge.disconnect()
        log("[MT5 LIVE] Disconnected.")


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Live MT5 demo execution of Hypothesis 2 (session order blocks, entries_v4_session_ob), "
                    "via the ZeroMQ bridge EA. Refuses non-demo accounts.")
    parser.add_argument("--symbol", default="XAUUSD", help="Internal symbol name for cost/ledger tagging.")
    parser.add_argument("--mt5-symbol", default=None,
                         help="Broker's actual tradeable symbol name if different (e.g. XAUUSD.a) -- check Market Watch.")
    parser.add_argument("--notional", type=float, default=10_000.0, help="Log bookkeeping only; does not size orders.")
    parser.add_argument("--risk-pct", type=float, default=DEFAULT_RISK_PCT,
                         help="Percent of account balance risked per trade (position sizing).")
    # SL/TP multipliers belong to the strategy config, not the CLI -- exposing them here
    # would invite tuning the live risk model away from the backtested one.
    parser.add_argument("--session-start-hour", type=int, default=None,
                         help="Override the order-block session window start (UTC). Default: strategy config (7).")
    parser.add_argument("--session-end-hour", type=int, default=None,
                         help="Override the order-block session window end (UTC). Default: strategy config (10).")
    parser.add_argument("--h1-history", type=int, default=1500,
                         help="H1 bars to pull per poll. Zone state is rebuilt by replay, so do not shrink casually.")
    parser.add_argument("--h4-history", type=int, default=600, help="H4 bars to pull per poll.")
    parser.add_argument("--poll-seconds", type=float, default=900.0, help="How often to check for a new H1 bar.")
    parser.add_argument("--max-iterations", type=int, default=None, help="Stop after N polls (omit to run until Ctrl+C).")
    parser.add_argument("--host", default=bridge.DEFAULT_HOST, help="Bridge EA host (127.0.0.1, or a VM's private IP).")
    parser.add_argument("--port", type=int, default=bridge.DEFAULT_PORT, help="Bridge EA ZeroMQ port.")
    args = parser.parse_args()

    cfg = SessionOBConfig()
    if args.session_start_hour is not None:
        cfg.session_start_hour = args.session_start_hour
    if args.session_end_hour is not None:
        cfg.session_end_hour = args.session_end_hour

    run_live(symbol=args.symbol, mt5_symbol=args.mt5_symbol, notional=args.notional, risk_pct=args.risk_pct,
              config=cfg, h1_history=args.h1_history, h4_history=args.h4_history,
              poll_seconds=args.poll_seconds, max_iterations=args.max_iterations, host=args.host, port=args.port)


if __name__ == "__main__":
    _cli()
