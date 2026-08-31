from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class MarketDataBundle:
    open_price: pd.DataFrame
    close_price: pd.DataFrame | None = None
    high_price: pd.DataFrame | None = None
    low_price: pd.DataFrame | None = None
    volume: pd.DataFrame | None = None
    amount: pd.DataFrame | None = None
    market_cap: pd.DataFrame | None = None
    limit_up_price: pd.DataFrame | None = None
    limit_down_price: pd.DataFrame | None = None
    is_st: pd.DataFrame | None = None
    is_suspended: pd.DataFrame | None = None
    listed_days: pd.DataFrame | None = None
    is_delisted: pd.DataFrame | None = None

    def __post_init__(self) -> None:
        self.open_price = _standardize_wide(self.open_price)
        for name in (
            "close_price",
            "high_price",
            "low_price",
            "volume",
            "amount",
            "market_cap",
            "limit_up_price",
            "limit_down_price",
            "is_st",
            "is_suspended",
            "listed_days",
            "is_delisted",
        ):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, _standardize_wide(value).reindex_like(self.open_price))
        if self.listed_days is None:
            self.listed_days = derive_listed_days_from_open(self.open_price)


def derive_listed_days_from_open(open_price: pd.DataFrame) -> pd.DataFrame:
    open_price = _standardize_wide(open_price)
    out = pd.DataFrame(index=open_price.index, columns=open_price.columns, dtype="float64")
    for col in open_price.columns:
        valid = open_price[col].notna()
        counts = valid.cumsum().astype(float)
        out[col] = counts.where(valid)
    return out


def _standardize_wide(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.index = pd.to_datetime(out.index)
    out.index.name = "trade_date"
    out.columns = [str(c) for c in out.columns]
    return out.sort_index().sort_index(axis=1)
