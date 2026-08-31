from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd


DATE_COLUMNS = ("trade_date", "date")
SYMBOL_COLUMNS = ("symbol", "asset")
VALUE_COLUMNS = ("factor_value", "factor", "value")


def resolve_factor_path(
    *,
    data_root: str | Path,
    factor_name: str | None = None,
    factor_path: str | Path | None = None,
    suffixes: Iterable[str] = (".parquet", ".h5", ".csv"),
) -> Path:
    if factor_path is not None:
        path = Path(factor_path)
        if not path.exists():
            raise FileNotFoundError(path)
        return path
    if not factor_name:
        raise ValueError("factor_name or factor_path is required")

    root = Path(data_root)
    factor_dir = root / f"factor_{factor_name}"
    stem = f"factor_{factor_name}"
    for suffix in suffixes:
        candidate = factor_dir / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No factor file found for {factor_name!r} in {factor_dir}")


def normalize_factor_dataframe(df: pd.DataFrame, value_column: str | None = None) -> pd.DataFrame:
    if isinstance(df.index, pd.MultiIndex):
        return _normalize_multiindex_long(df, value_column=value_column)
    if _looks_like_long_dataframe(df):
        return _normalize_long_dataframe(df, value_column=value_column)
    return _normalize_wide_dataframe(df)


def load_factor_file(path: str | Path, h5_key: str | None = None, value_column: str | None = None) -> pd.DataFrame:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        raw = pd.read_csv(path)
    elif suffix == ".parquet":
        if path.is_dir():
            return _load_partitioned_parquet(path, value_column=value_column)
        raw = pd.read_parquet(path)
    elif suffix in {".h5", ".hdf", ".hdf5"}:
        if h5_key is None:
            with pd.HDFStore(path, mode="r") as store:
                keys = store.keys()
            if len(keys) != 1:
                raise ValueError(f"H5 file has multiple keys; specify h5_key. Available keys: {keys}")
            h5_key = keys[0]
        raw = pd.read_hdf(path, key=h5_key)
    else:
        raise ValueError(f"Unsupported factor file suffix: {path.suffix}")
    return normalize_factor_dataframe(raw, value_column=value_column)


def _load_partitioned_parquet(path: Path, value_column: str | None) -> pd.DataFrame:
    partition_paths = sorted(child for child in path.iterdir() if child.is_file() and child.suffix.lower() == ".parquet")
    if not partition_paths:
        raise ValueError(f"Partitioned parquet factor directory has no parquet files: {path}")
    frames = [normalize_factor_dataframe(pd.read_parquet(partition), value_column=value_column) for partition in partition_paths]
    _validate_partition_dates(frames, partition_paths)
    out = pd.concat(frames, axis=0, join="outer", sort=False)
    return _finalize_standard_factor(out)


def _validate_partition_dates(frames: list[pd.DataFrame], paths: list[Path]) -> None:
    seen: dict[pd.Timestamp, Path] = {}
    duplicates: list[str] = []
    for frame, path in zip(frames, paths):
        for date in frame.index:
            if date in seen:
                duplicates.append(f"{date.date()}: {seen[date].name}, {path.name}")
            else:
                seen[date] = path
    if duplicates:
        detail = "; ".join(duplicates[:10])
        if len(duplicates) > 10:
            detail += f"; ... total={len(duplicates)}"
        raise ValueError(f"Partitioned parquet factor data has duplicate trade_date across files: {detail}")


def _normalize_wide_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    date_col = _pick_column(set(out.columns), DATE_COLUMNS)
    if date_col is not None:
        out = out.set_index(date_col)
    out = _coerce_trade_date_index(out)
    out.columns = _coerce_symbol_columns(out.columns)
    out = _coerce_numeric_values(out)
    return _finalize_standard_factor(out)


def _normalize_multiindex_long(df: pd.DataFrame, value_column: str | None) -> pd.DataFrame:
    reset = df.reset_index()
    return _normalize_long_dataframe(reset, value_column=value_column)


def _normalize_long_dataframe(df: pd.DataFrame, value_column: str | None) -> pd.DataFrame:
    columns = set(df.columns)
    date_col = _pick_column(columns, DATE_COLUMNS)
    symbol_col = _pick_column(columns, SYMBOL_COLUMNS)
    value_col = value_column or _pick_column(columns, VALUE_COLUMNS)
    if date_col is None or symbol_col is None or value_col is None:
        raise ValueError("Long factor data requires date/trade_date, symbol/asset, and value column")
    if value_col not in columns:
        raise ValueError(f"Long factor data value_column not found: {value_col}")

    long_df = df[[date_col, symbol_col, value_col]].copy()
    long_df = long_df.rename(columns={date_col: "trade_date", symbol_col: "symbol", value_col: "factor_value"})
    long_df["trade_date"] = _parse_trade_dates(long_df["trade_date"])
    long_df["symbol"] = long_df["symbol"].astype(str)
    long_df["factor_value"] = _coerce_numeric_series(long_df["factor_value"], column_name=value_col)
    duplicated = long_df.duplicated(subset=["trade_date", "symbol"], keep=False)
    if duplicated.any():
        sample = long_df.loc[duplicated, ["trade_date", "symbol"]].drop_duplicates().head(10)
        detail = ", ".join(f"({row.trade_date.date()}, {row.symbol})" for row in sample.itertuples(index=False))
        raise ValueError(f"Long factor data has duplicate trade_date + symbol rows: {detail}")
    out = long_df.pivot(index="trade_date", columns="symbol", values="factor_value")
    return _finalize_standard_factor(out)


def _looks_like_long_dataframe(df: pd.DataFrame) -> bool:
    columns = set(df.columns)
    return _pick_column(columns, DATE_COLUMNS) is not None and _pick_column(columns, SYMBOL_COLUMNS) is not None


def _pick_column(columns: set, candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _coerce_trade_date_index(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.index, pd.RangeIndex) or pd.api.types.is_numeric_dtype(df.index):
        raise ValueError("Factor trade_date index must be date-like; numeric indexes are not allowed")
    out = df.copy()
    out.index = _parse_trade_dates(out.index)
    return out


def _parse_trade_dates(values) -> pd.DatetimeIndex:
    parsed = pd.to_datetime(values, errors="coerce")
    index = pd.DatetimeIndex(parsed)
    if index.isna().any():
        raise ValueError("Factor trade_date contains values that cannot be parsed as dates")
    if not (index == index.normalize()).all():
        raise ValueError("Daily factor trade_date must not contain intraday timestamps")
    return index


def _coerce_symbol_columns(columns: pd.Index) -> pd.Index:
    symbol_columns = pd.Index([str(column) for column in columns], name="symbol")
    if symbol_columns.has_duplicates:
        duplicates = symbol_columns[symbol_columns.duplicated()].unique().tolist()
        raise ValueError(f"Factor symbols have duplicates after string conversion: {duplicates}")
    return symbol_columns


def _coerce_numeric_values(df: pd.DataFrame) -> pd.DataFrame:
    converted_columns = [
        _coerce_numeric_series(df[column], column_name=str(column))
        for column in df.columns
    ]
    out = pd.concat(converted_columns, axis=1) if converted_columns else df.copy()
    out.columns = df.columns
    return out


def _coerce_numeric_series(series: pd.Series, *, column_name: str) -> pd.Series:
    converted = pd.to_numeric(series, errors="coerce")
    newly_missing = converted.isna() & series.notna()
    if newly_missing.any():
        raise ValueError(f"Factor values must be numeric; column {column_name!r} contains non-numeric values")
    return converted


def _finalize_standard_factor(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out = _coerce_trade_date_index(out)
    out.columns = _coerce_symbol_columns(pd.Index(out.columns))
    if out.index.has_duplicates:
        duplicates = out.index[out.index.duplicated()].unique()
        detail = ", ".join(str(date.date()) for date in duplicates[:10])
        raise ValueError(f"Factor trade_date contains duplicate dates: {detail}")
    out = _coerce_numeric_values(out)
    out.index.name = "trade_date"
    out.columns.name = "symbol"
    return out.sort_index().sort_index(axis=1)
