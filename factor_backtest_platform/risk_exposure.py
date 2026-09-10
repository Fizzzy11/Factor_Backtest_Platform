from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable

import numpy as np
import pandas as pd

from factor_backtest_platform.config import BacktestConfig, DataSourceConfig


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
DEFAULT_INDUSTRY_COLUMNS = (
    "801010.INDX",
    "801030.INDX",
    "801040.INDX",
    "801050.INDX",
    "801080.INDX",
    "801110.INDX",
    "801120.INDX",
    "801130.INDX",
    "801140.INDX",
    "801150.INDX",
    "801160.INDX",
    "801170.INDX",
    "801180.INDX",
    "801200.INDX",
    "801210.INDX",
    "801230.INDX",
    "801710.INDX",
    "801720.INDX",
    "801730.INDX",
    "801740.INDX",
    "801750.INDX",
    "801760.INDX",
    "801770.INDX",
    "801780.INDX",
    "801790.INDX",
    "801880.INDX",
    "801890.INDX",
    "801950.INDX",
    "801960.INDX",
    "801970.INDX",
    "801980.INDX",
)
_CLICKHOUSE_TABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


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


def build_risk_exposure_sql(
    *,
    start_date,
    end_date,
    table: str = "cn_stock_fundamentals.factor_exposure",
    style_columns: Iterable[str] = DEFAULT_STYLE_COLUMNS,
    industry_columns: Iterable[str] = DEFAULT_INDUSTRY_COLUMNS,
) -> str:
    """构造按因子日期范围读取风险暴露表的 ClickHouse 查询。"""
    if not _CLICKHOUSE_TABLE_PATTERN.fullmatch(table):
        raise ValueError(f"Invalid ClickHouse risk exposure table name: {table!r}")
    start = _format_query_date(start_date, name="start_date")
    end = _format_query_date(end_date, name="end_date")
    if start > end:
        raise ValueError("start_date must not be later than end_date")
    styles = tuple(style_columns)
    industries = tuple(industry_columns)
    selected_columns = [
        "date AS trade_date",
        "symbol",
        *[_quote_identifier(name) for name in styles],
        *[_quote_identifier(name) for name in industries],
    ]
    return (
        "SELECT\n  "
        + ",\n  ".join(selected_columns)
        + f"\nFROM {table} FINAL"
        + f"\nWHERE date >= '{start}'"
        + f"\n  AND date <= '{end}'"
    )


def resolve_risk_exposure(
    config: BacktestConfig,
    *,
    start_date=None,
    end_date=None,
    client=None,
    log_fn=print,
) -> RiskExposureData | None:
    source = config.data_sources.risk_exposure_source
    if source == "none":
        return None
    if source == "clickhouse":
        if start_date is None or end_date is None:
            raise ValueError("ClickHouse risk exposure loading requires start_date and end_date")
        return load_risk_exposure_from_clickhouse(
            config=config.data_sources,
            start_date=start_date,
            end_date=end_date,
            client=client,
            verbose=config.verbose,
            log_fn=log_fn,
        )
    raise ValueError(f"Unknown risk_exposure_source: {source}")


def load_risk_exposure_from_clickhouse(
    *,
    config: DataSourceConfig,
    start_date,
    end_date,
    client=None,
    verbose: bool = True,
    log_fn=print,
) -> RiskExposureData:
    """从 ClickHouse 读取指定日期范围内的风格与行业暴露。"""
    table = config.clickhouse_tables.risk_exposure
    if not table:
        raise ValueError("risk_exposure_source='clickhouse' requires clickhouse_tables.risk_exposure")
    from factor_backtest_platform.clickhouse_adapter import create_clickhouse_client

    sql = build_risk_exposure_sql(start_date=start_date, end_date=end_date, table=table)
    if verbose:
        start = _format_query_date(start_date, name="start_date")
        end = _format_query_date(end_date, name="end_date")
        log_fn(f"[v2] 从 ClickHouse 读取风险暴露：{start} -> {end}")
    clickhouse_client = client or create_clickhouse_client(config.clickhouse)
    raw = clickhouse_client.query_df(sql)
    if raw.empty:
        raise RuntimeError("ClickHouse 未返回指定日期范围内的风险暴露数据")
    data = dataframe_to_risk_exposure(raw)
    if verbose:
        log_fn(
            f"[v2] 风险暴露读取完成：rows={len(data.exposures):,}, "
            f"styles={len(data.style_columns)}, industries={len(data.industry_columns)}"
        )
    return data


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
    duplicate_keys = out.duplicated(["trade_date", "symbol"], keep=False)
    if duplicate_keys.any():
        sample = out.loc[duplicate_keys, ["trade_date", "symbol"]].head(5).to_dict("records")
        raise ValueError(f"Risk exposure data has duplicate date-symbol keys: {sample}")
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


def _format_query_date(value, *, name: str) -> str:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError(f"{name} must be a valid date")
    return timestamp.strftime("%Y-%m-%d")


def _quote_identifier(value: str) -> str:
    if "`" in value:
        raise ValueError(f"Invalid ClickHouse column name: {value!r}")
    return f"`{value}`"


def _pick_column(columns, candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None
