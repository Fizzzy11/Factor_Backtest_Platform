from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class TradabilityResult:
    mask: pd.DataFrame
    summary: pd.DataFrame


def compute_tradability_mask(
    *,
    open_price: pd.DataFrame,
    high_price: pd.DataFrame,
    low_price: pd.DataFrame,
    limit_up_price: pd.DataFrame,
    limit_down_price: pd.DataFrame,
    is_st: pd.DataFrame,
    is_suspended: pd.DataFrame,
    listed_days: pd.DataFrame,
    min_listed_days: int,
) -> TradabilityResult:
    frames = [
        high_price,
        low_price,
        limit_up_price,
        limit_down_price,
        is_st,
        is_suspended,
        listed_days,
    ]
    frames = [f.reindex_like(open_price) for f in frames]
    high, low, limit_up, limit_down, st, suspended, listed = frames
    open_valid = open_price.notna() & (open_price > 0)
    limit_up_touch = high >= limit_up
    limit_down_touch = low <= limit_down
    st_flag = st.fillna(1).astype(float) == 1
    suspended_flag = suspended.fillna(1).astype(float) == 1
    new_stock = listed < min_listed_days

    mask = open_valid & ~limit_up_touch & ~limit_down_touch & ~st_flag & ~suspended_flag & ~new_stock
    summary = pd.DataFrame(index=open_price.index)
    summary["limit_up_touch_count"] = limit_up_touch.sum(axis=1)
    summary["limit_down_touch_count"] = limit_down_touch.sum(axis=1)
    summary["st_count"] = st_flag.sum(axis=1)
    summary["suspended_count"] = suspended_flag.sum(axis=1)
    summary["new_stock_filtered_count"] = new_stock.sum(axis=1)
    summary["open_invalid_count"] = (~open_valid).sum(axis=1)
    summary["after_filter_count"] = mask.sum(axis=1)
    return TradabilityResult(mask=mask.astype(bool), summary=summary)
