from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import pandas as pd


RETURN_DATE_COLUMNS = ("trade_date", "date")
RETURN_SYMBOL_COLUMNS = ("symbol", "asset")
RETURN_VALUE_COLUMNS = ("return", "ret", "future_return", "value", "factor_value", "factor")


@dataclass(frozen=True)
class ReturnSpec:
    label: str
    data: pd.DataFrame
    horizon_days: int | None = None
    source: str = "external"


def build_return_specs(
    *,
    open_price: pd.DataFrame,
    horizons: list[int],
    external_returns: dict[str, Any] | None = None,
) -> dict[str, ReturnSpec]:
    specs: dict[str, ReturnSpec] = {}
    for horizon, returns in _compute_builtin_future_returns(open_price, horizons).items():
        label = return_label(horizon)
        specs[label] = ReturnSpec(label=label, data=returns, horizon_days=int(horizon), source="builtin")
    external_specs = normalize_external_returns(external_returns)
    duplicate_labels = sorted(set(specs).intersection(external_specs))
    if duplicate_labels:
        raise ValueError(f"external return label duplicates an existing return label: {duplicate_labels}")
    specs.update(external_specs)
    return specs


def normalize_external_returns(external_returns: dict[str, Any] | None) -> dict[str, ReturnSpec]:
    if not external_returns:
        return {}
    specs: dict[str, ReturnSpec] = {}
    for label, value in external_returns.items():
        if isinstance(value, dict):
            if "data" not in value:
                raise ValueError(f"external_returns[{label!r}] requires a 'data' field")
            data = value["data"]
            horizon_days = value.get("horizon_days")
            value_column = value.get("value_column")
            h5_key = value.get("h5_key")
        else:
            data = value
            horizon_days = None
            value_column = None
            h5_key = None
        if horizon_days is not None:
            horizon_days = int(horizon_days)
            if horizon_days <= 0:
                raise ValueError(f"external_returns[{label!r}].horizon_days must be positive")
        frame = load_return_file(data, h5_key=h5_key, value_column=value_column) if isinstance(data, (str, Path)) else normalize_return_dataframe(data, value_column=value_column)
        specs[str(label)] = ReturnSpec(label=str(label), data=frame, horizon_days=horizon_days, source="external")
    return specs


def normalize_return_dataframe(df: pd.DataFrame, value_column: str | None = None) -> pd.DataFrame:
    if isinstance(df.index, pd.MultiIndex):
        return _normalize_long_return_dataframe(df.reset_index(), value_column=value_column)
    if _looks_like_long_dataframe(df):
        return _normalize_long_return_dataframe(df, value_column=value_column)
    return _normalize_wide_return_dataframe(df)


def load_return_file(path: str | Path, h5_key: str | None = None, value_column: str | None = None) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        raw = pd.read_csv(path)
    elif path.suffix.lower() in {".h5", ".hdf", ".hdf5"}:
        if h5_key is None:
            with pd.HDFStore(path, mode="r") as store:
                keys = store.keys()
            if len(keys) != 1:
                raise ValueError(f"H5 file has multiple keys; specify h5_key. Available keys: {keys}")
            h5_key = keys[0]
        raw = pd.read_hdf(path, key=h5_key)
    else:
        raise ValueError(f"Unsupported return file suffix: {path.suffix}")
    return normalize_return_dataframe(raw, value_column=value_column)


def return_label(value: Any) -> str:
    if isinstance(value, int):
        return f"{value}d"
    if isinstance(value, float) and value.is_integer():
        return f"{int(value)}d"
    return str(value)


def return_slug(value: Any) -> str:
    label = return_label(value)
    slug = re.sub(r"[^0-9A-Za-z_.-]+", "_", label).strip("_")
    return slug or "return"


def sort_return_labels(values) -> list:
    labels = list(values)

    def key(value):
        label = return_label(value)
        match = re.fullmatch(r"(\d+)d", label)
        return (0, int(match.group(1))) if match else (1, label)

    return sorted(labels, key=key)


def _normalize_wide_return_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    date_col = _pick_column(set(out.columns), RETURN_DATE_COLUMNS)
    if date_col is not None:
        out = out.set_index(date_col)
    out.index = pd.to_datetime(out.index)
    out.index.name = "trade_date"
    out.columns = pd.Index([str(c) for c in out.columns], name=None)
    out = out.apply(pd.to_numeric, errors="coerce")
    return out.sort_index().sort_index(axis=1)


def _normalize_long_return_dataframe(df: pd.DataFrame, value_column: str | None) -> pd.DataFrame:
    columns = set(df.columns)
    date_col = _pick_column(columns, RETURN_DATE_COLUMNS)
    symbol_col = _pick_column(columns, RETURN_SYMBOL_COLUMNS)
    value_col = value_column or _pick_column(columns, RETURN_VALUE_COLUMNS)
    if date_col is None or symbol_col is None or value_col is None:
        raise ValueError("Long external return data requires date/trade_date, symbol/asset, and return/value column")
    long_df = df[[date_col, symbol_col, value_col]].copy()
    long_df = long_df.rename(columns={date_col: "trade_date", symbol_col: "symbol", value_col: "return"})
    long_df["trade_date"] = pd.to_datetime(long_df["trade_date"])
    long_df["symbol"] = long_df["symbol"].astype(str)
    long_df["return"] = pd.to_numeric(long_df["return"], errors="coerce")
    out = long_df.pivot(index="trade_date", columns="symbol", values="return")
    out.index.name = "trade_date"
    out.columns.name = "symbol"
    return out.sort_index().sort_index(axis=1)


def _looks_like_long_dataframe(df: pd.DataFrame) -> bool:
    columns = set(df.columns)
    return _pick_column(columns, RETURN_DATE_COLUMNS) is not None and _pick_column(columns, RETURN_SYMBOL_COLUMNS) is not None


def _pick_column(columns: set, candidates) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _compute_builtin_future_returns(open_price: pd.DataFrame, horizons: list[int]) -> dict[int, pd.DataFrame]:
    out: dict[int, pd.DataFrame] = {}
    entry_open = open_price.shift(-1)
    for horizon in horizons:
        exit_open = open_price.shift(-(int(horizon) + 1))
        ret = exit_open / entry_open - 1.0
        ret = ret.where((exit_open > 0) & (entry_open > 0))
        out[int(horizon)] = ret
    return out
