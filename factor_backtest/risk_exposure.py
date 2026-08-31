from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from factor_backtest.config import BacktestConfig, DataSourceConfig


DEFAULT_STYLE_COLUMNS = (
    "size",
    "non_linear_size",
    "momentum",
    "liquidity",
    "book_to_price",
    "leverage",
    "growth",
    "earnings_yield",
    "beta",
    "residual_volatility",
)

IGNORED_EXPOSURE_COLUMNS = ("comovement",)
DATE_COLUMNS = ("trade_date", "date")
INDUSTRY_CODE_COLUMN = "industry"


@dataclass(frozen=True)
class RiskExposureData:
    exposures: pd.DataFrame
    style_columns: tuple[str, ...] = DEFAULT_STYLE_COLUMNS
    industry_columns: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_panel(self, dates: Iterable, symbols: Iterable[str]) -> "RiskExposurePanel":
        date_index = pd.DatetimeIndex(pd.to_datetime(list(dates)), name="trade_date")
        symbol_index = pd.Index([str(symbol) for symbol in symbols], name="symbol")
        target_index = pd.MultiIndex.from_product([date_index, symbol_index], names=["trade_date", "symbol"])
        aligned = self.exposures.reindex(target_index)
        t_count = len(date_index)
        n_count = len(symbol_index)
        style_values = aligned.loc[:, self.style_columns].to_numpy(dtype="float64").reshape(
            t_count,
            n_count,
            len(self.style_columns),
        )
        industry_values = aligned.loc[:, self.industry_columns].to_numpy(dtype="float64").reshape(
            t_count,
            n_count,
            len(self.industry_columns),
        )
        industry_codes, has_multi_industry_membership = _industry_codes_from_values(industry_values)
        return RiskExposurePanel(
            dates=date_index,
            symbols=symbol_index,
            style_columns=self.style_columns,
            industry_columns=self.industry_columns,
            style_values=style_values,
            industry_values=industry_values,
            industry_codes=industry_codes,
            has_multi_industry_membership=has_multi_industry_membership,
        )

    def slice_date(self, trade_date, symbols: Iterable[str]) -> pd.DataFrame:
        date = pd.Timestamp(trade_date)
        symbol_index = pd.Index([str(symbol) for symbol in symbols], name="symbol")
        if date not in self.exposures.index.get_level_values("trade_date"):
            return pd.DataFrame(index=symbol_index, columns=self.exposures.columns, dtype="float64")
        daily = self.exposures.xs(date, level="trade_date")
        return daily.reindex(symbol_index)

    def wide_style(self, column: str, dates: Iterable, symbols: Iterable[str]) -> pd.DataFrame:
        return self._wide_column(column, dates, symbols)

    def wide_industry(self, column: str, dates: Iterable, symbols: Iterable[str]) -> pd.DataFrame:
        return self._wide_column(column, dates, symbols)

    def _wide_column(self, column: str, dates: Iterable, symbols: Iterable[str]) -> pd.DataFrame:
        date_index = pd.DatetimeIndex(pd.to_datetime(list(dates)), name="trade_date")
        symbol_index = pd.Index([str(symbol) for symbol in symbols])
        values = []
        for date in date_index:
            daily = self.slice_date(date, symbol_index)
            values.append(pd.to_numeric(daily[column], errors="coerce") if column in daily else pd.Series(index=symbol_index, dtype="float64"))
        return pd.DataFrame(values, index=date_index, columns=symbol_index)


@dataclass(frozen=True)
class RiskExposurePanel:
    dates: pd.DatetimeIndex
    symbols: pd.Index
    style_columns: tuple[str, ...]
    industry_columns: tuple[str, ...]
    style_values: np.ndarray
    industry_values: np.ndarray
    industry_codes: np.ndarray
    has_multi_industry_membership: bool


def load_risk_exposure_from_csv(
    path: str | Path,
    *,
    style_columns: Iterable[str] = DEFAULT_STYLE_COLUMNS,
    ignored_columns: Iterable[str] = IGNORED_EXPOSURE_COLUMNS,
) -> RiskExposureData:
    raw = pd.read_csv(path)
    return dataframe_to_risk_exposure(raw, style_columns=style_columns, ignored_columns=ignored_columns)


def load_risk_exposure_from_file(
    path: str | Path,
    *,
    style_columns: Iterable[str] = DEFAULT_STYLE_COLUMNS,
    ignored_columns: Iterable[str] = IGNORED_EXPOSURE_COLUMNS,
) -> RiskExposureData:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        raw = pd.read_parquet(path)
    elif suffix == ".csv":
        raw = pd.read_csv(path)
    else:
        raise ValueError(f"Unsupported risk exposure file type: {path.suffix}")
    return dataframe_to_risk_exposure(raw, style_columns=style_columns, ignored_columns=ignored_columns)


def resolve_risk_exposure(config: BacktestConfig) -> RiskExposureData | None:
    source = config.data_sources.risk_exposure_source
    if source == "none":
        return None
    if source == "csv":
        path = Path(config.paths.risk_exposure_path)
        if not path.is_absolute():
            path = Path(config.paths.data_root) / path
        return load_risk_exposure_from_file(path)
    if source == "clickhouse":
        return load_risk_exposure_from_clickhouse(config=config.data_sources)
    raise ValueError(f"Unknown risk_exposure_source: {source}")


def load_risk_exposure_from_clickhouse(*, config: DataSourceConfig) -> RiskExposureData:
    table = config.clickhouse_tables.risk_exposure
    if not table:
        raise ValueError("risk_exposure_source='clickhouse' requires clickhouse_tables.risk_exposure")
    raise NotImplementedError("ClickHouse risk exposure loading is not implemented yet")


def dataframe_to_risk_exposure(
    raw: pd.DataFrame,
    *,
    style_columns: Iterable[str] = DEFAULT_STYLE_COLUMNS,
    ignored_columns: Iterable[str] = IGNORED_EXPOSURE_COLUMNS,
) -> RiskExposureData:
    style_cols = tuple(style_columns)
    ignored = set(ignored_columns)
    df = _standardize_risk_exposure_dataframe(raw)
    missing_styles = [col for col in style_cols if col not in df.columns]
    if missing_styles:
        raise ValueError(f"Missing required style exposure columns: {missing_styles}")

    metadata_cols = {"trade_date", "symbol", *ignored}
    compact_industry = INDUSTRY_CODE_COLUMN in df.columns
    if compact_industry:
        industry_dummies, industry_cols = _expand_industry_column(df[INDUSTRY_CODE_COLUMN])
        df = pd.concat([df.drop(columns=[INDUSTRY_CODE_COLUMN]), industry_dummies], axis=1)
    else:
        industry_cols = tuple(col for col in df.columns if col not in metadata_cols and col not in style_cols)
    if not industry_cols:
        raise ValueError("Risk exposure data requires industry column or at least one industry dummy column")

    keep_cols = [*style_cols, *industry_cols]
    out = df[["trade_date", "symbol", *keep_cols]].copy()
    for col in keep_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["symbol"] = out["symbol"].astype(str)
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    out = out.sort_values(["trade_date", "symbol"])
    exposures = out.set_index(["trade_date", "symbol"])[keep_cols].sort_index()

    warnings = _industry_membership_warnings(exposures, industry_cols)
    return RiskExposureData(
        exposures=exposures,
        style_columns=style_cols,
        industry_columns=industry_cols,
        warnings=tuple(warnings),
    )


def _standardize_risk_exposure_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    index_names = set(name for name in getattr(raw.index, "names", []) if name is not None)
    has_named_index_key = bool(index_names.intersection({*DATE_COLUMNS, "symbol"}))
    columns = set(raw.columns)
    has_date_column = any(col in columns for col in DATE_COLUMNS)
    if has_named_index_key and ("symbol" not in columns or not has_date_column):
        df = raw.reset_index()
    elif raw.index.name in DATE_COLUMNS and not any(col in raw.columns for col in DATE_COLUMNS):
        df = raw.reset_index()
    else:
        df = raw.copy()
    date_col = _pick_column(df.columns, DATE_COLUMNS)
    if date_col is None:
        unnamed_cols = [col for col in df.columns if str(col).startswith("Unnamed")]
        if unnamed_cols:
            date_col = unnamed_cols[0]
    if date_col is None or "symbol" not in df.columns:
        raise ValueError("Risk exposure data requires date/trade_date index or column and symbol column")
    return df.rename(columns={date_col: "trade_date"})


def _industry_membership_warnings(exposures: pd.DataFrame, industry_columns: tuple[str, ...]) -> list[str]:
    industry_sum = exposures.loc[:, industry_columns].fillna(0).sum(axis=1)
    missing = int((industry_sum == 0).sum())
    multiple = int((industry_sum > 1).sum())
    warnings = []
    if missing:
        warnings.append(f"risk exposure has {missing} date-symbol rows with missing industry membership")
    if multiple:
        warnings.append(f"risk exposure has {multiple} date-symbol rows with multiple industry memberships")
    return warnings


def _industry_codes_from_values(industry_values: np.ndarray) -> tuple[np.ndarray, bool]:
    if industry_values.shape[2] == 0:
        return np.full(industry_values.shape[:2], -1, dtype="int32"), False
    clean = np.nan_to_num(industry_values, nan=0.0)
    membership = clean > 0
    counts = membership.sum(axis=2)
    codes = np.full(industry_values.shape[:2], -1, dtype="int32")
    single = counts == 1
    if single.any():
        codes[single] = membership.argmax(axis=2)[single].astype("int32")
    return codes, bool((counts > 1).any())


def _expand_industry_column(industry: pd.Series) -> tuple[pd.DataFrame, tuple[str, ...]]:
    raw_labels = industry.dropna()
    if pd.api.types.is_numeric_dtype(raw_labels):
        valid_labels = [_format_industry_label(label) for label in sorted(raw_labels.unique())]
    else:
        valid_labels = [str(label) for label in pd.unique(raw_labels.astype(str))]
    labels = industry.astype("string")
    if not valid_labels:
        return pd.DataFrame(index=industry.index), tuple()
    dummies = pd.DataFrame(0.0, index=industry.index, columns=valid_labels)
    for label in valid_labels:
        dummies.loc[labels == label, label] = 1.0
    return dummies, tuple(valid_labels)


def _format_industry_label(label) -> str:
    if isinstance(label, (int, np.integer)):
        return str(int(label))
    if isinstance(label, (float, np.floating)) and float(label).is_integer():
        return str(int(label))
    return str(label)


def _pick_column(columns, candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None
