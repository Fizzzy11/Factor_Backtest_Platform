from __future__ import annotations

import bisect
from collections.abc import Iterable

import pandas as pd


class TradingCalendar:
    def __init__(self, trade_dates: Iterable) -> None:
        dates = pd.to_datetime(pd.Index(trade_dates)).dropna().unique()
        self.dates = pd.DatetimeIndex(sorted(dates))
        if self.dates.empty:
            raise ValueError("TradingCalendar requires at least one date")

    def shift(self, date, n: int) -> pd.Timestamp:
        ts = pd.Timestamp(date)
        if ts in self.dates:
            pos = self.dates.get_loc(ts)
        else:
            pos = bisect.bisect_left(list(self.dates), ts)
        target = pos + n
        if target < 0 or target >= len(self.dates):
            raise IndexError(f"Cannot shift {date!r} by {n} trading days")
        return pd.Timestamp(self.dates[target])

    def window_ending(self, end_date, n: int) -> list[pd.Timestamp]:
        if n <= 0:
            raise ValueError("n must be positive")
        end = pd.Timestamp(end_date)
        end_pos = bisect.bisect_left(list(self.dates), end) - 1
        if end in self.dates:
            end_pos = self.dates.get_loc(end)
        start_pos = max(0, end_pos - n + 1)
        return [pd.Timestamp(x) for x in self.dates[start_pos : end_pos + 1]]
