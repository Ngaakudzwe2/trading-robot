# -*- coding: utf-8 -*-
"""
htf_align.py

No-lookahead higher-timeframe alignment. Extracted verbatim from
medfreq_strategy.py (2026-09-07) so the active strategy chain no longer has
to import a retired strategy module just to reach this one function --
entries_v2 (and therefore entries_v4_session_ob and the live paper daemon)
depended on it, which was the only thing keeping MEDFREQ in the workspace.
The implementation is unchanged; this is a move, not a rewrite, precisely so
the behaviour of every existing result stays byte-identical.
"""

from __future__ import annotations

from typing import List, Optional

from src.candle import Candle


def align_htf_to_m5(m5_candles: List[Candle], htf_candles: List[Candle],
                     htf_values: List[Optional[float]], htf_minutes: int) -> List[Optional[float]]:
    """
    For each M5 candle, returns the higher-timeframe indicator value from
    the most recently CLOSED htf bar (close time <= the M5 bar's open
    time) -- never the still-forming htf bar covering that same moment.
    None until the first htf bar has actually closed.

    Despite the name it is not M5-specific: the "m5_candles" argument is
    simply the lower timeframe being aligned onto (entries_v2 passes H1),
    and htf_minutes is the higher timeframe's bar duration in minutes
    (1440 for daily, 10080 for weekly, 240 for H4).
    """
    htf_duration_ms = htf_minutes * 60_000
    aligned: List[Optional[float]] = [None] * len(m5_candles)
    j = 0
    last_closed_value: Optional[float] = None
    for i, c in enumerate(m5_candles):
        while j < len(htf_candles) and htf_candles[j].open_time + htf_duration_ms <= c.open_time:
            last_closed_value = htf_values[j]
            j += 1
        aligned[i] = last_closed_value
    return aligned
