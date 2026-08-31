from __future__ import annotations

import pandas as pd

from factor_backtest_platform.config import ClickHouseConfig, ClickHouseTableConfig, DataSourceConfig
from factor_backtest_platform.market_data import MarketDataBundle


def build_market_data_sql(
    *,
    start_date: str,
    end_date: str,
    tables: ClickHouseTableConfig | None = None,
    table_ohlcv: str | None = None,
    table_shares: str | None = None,
    table_st: str | None = None,
    table_suspended: str | None = None,
) -> str:
    table_cfg = tables or ClickHouseTableConfig()
    table_ohlcv = table_ohlcv or table_cfg.ohlcv
    table_shares = table_shares or table_cfg.shares
    table_st = table_st or table_cfg.st
    table_suspended = table_suspended or table_cfg.suspended
    return f"""
    SELECT
      o.symbol AS symbol,
      o.date AS trade_date,
      o.open_price AS open_price,
      o.close_price AS close_price,
      o.high_price AS high_price,
      o.low_price AS low_price,
      o.volume AS volume,
      o.turnover AS amount,
      o.limit_up AS limit_up,
      o.limit_down AS limit_down,
      sh.circulation_a AS market_cap,
      st.is_st_stock AS is_st,
      sp.is_suspended AS is_suspended
    FROM {table_ohlcv} o
    LEFT JOIN {table_shares} sh
      ON o.symbol = sh.symbol AND o.date = sh.date
    LEFT JOIN {table_st} st
      ON o.symbol = st.symbol AND o.date = st.date
    LEFT JOIN {table_suspended} sp
      ON o.symbol = sp.symbol AND o.date = sp.date
    WHERE o.date >= '{start_date}'
      AND o.date <  '{end_date}'
    """


def create_clickhouse_client(config: ClickHouseConfig | None = None):
    cfg = config or ClickHouseConfig()
    try:
        import clickhouse_connect
    except ImportError as exc:
        raise ImportError("clickhouse_connect is required to load market data from ClickHouse") from exc
    return clickhouse_connect.get_client(
        host=cfg.host,
        port=cfg.port,
        username=cfg.username,
        password=cfg.password,
    )


def load_market_data_from_clickhouse(
    *,
    start_date: str,
    end_date: str,
    client=None,
    config: ClickHouseConfig | DataSourceConfig | None = None,
    tables: ClickHouseTableConfig | None = None,
    verbose: bool = True,
    log_fn=print,
) -> MarketDataBundle:
    _log(verbose, log_fn, f"[v1] loading market data from ClickHouse: {start_date} -> {end_date}")
    clickhouse_config, table_config = _resolve_clickhouse_inputs(config, tables)
    ch_client = client or create_clickhouse_client(clickhouse_config)
    sql = build_market_data_sql(start_date=start_date, end_date=end_date, tables=table_config)
    raw = ch_client.query_df(sql)
    if raw.empty:
        raise RuntimeError("No market data fetched from ClickHouse.")
    _log(verbose, log_fn, f"[v1] market data rows loaded: {len(raw):,}")
    bundle = dataframe_to_market_data(raw)
    _log(verbose, log_fn, f"[v1] market data standardized: dates={len(bundle.open_price.index):,}, symbols={len(bundle.open_price.columns):,}")
    return bundle


def dataframe_to_market_data(raw: pd.DataFrame) -> MarketDataBundle:
    df = _standardize_raw_dataframe(raw)
    return MarketDataBundle(
        open_price=_pivot(df, "open_price"),
        close_price=_pivot(df, "close_price"),
        high_price=_pivot(df, "high_price"),
        low_price=_pivot(df, "low_price"),
        volume=_pivot(df, "volume"),
        amount=_pivot(df, "amount"),
        market_cap=_pivot(df, "market_cap"),
        limit_up_price=_pivot(df, "limit_up"),
        limit_down_price=_pivot(df, "limit_down"),
        is_st=_pivot(df, "is_st"),
        is_suspended=_pivot(df, "is_suspended"),
    )


def _standardize_raw_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "date": "trade_date",
        "turnover": "amount",
        "circulation_a": "market_cap",
        "is_st_stock": "is_st",
    }
    df = raw.rename(columns={k: v for k, v in rename_map.items() if k in raw.columns}).copy()
    required = [
        "symbol",
        "trade_date",
        "open_price",
        "close_price",
        "high_price",
        "low_price",
        "volume",
        "amount",
        "market_cap",
        "limit_up",
        "limit_down",
        "is_st",
        "is_suspended",
    ]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required market data columns: {missing}")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["symbol"] = df["symbol"].astype(str)
    for col in required:
        if col not in {"symbol", "trade_date"}:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values(["symbol", "trade_date"]).reset_index(drop=True)


def _pivot(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    wide = df.pivot_table(index="trade_date", columns="symbol", values=value_col, aggfunc="last")
    wide.index.name = "trade_date"
    wide.columns.name = None
    return wide.sort_index().sort_index(axis=1)


def _log(verbose: bool, log_fn, message: str) -> None:
    if verbose:
        log_fn(message)


def _resolve_clickhouse_inputs(
    config: ClickHouseConfig | DataSourceConfig | None,
    tables: ClickHouseTableConfig | None,
) -> tuple[ClickHouseConfig | None, ClickHouseTableConfig | None]:
    if isinstance(config, DataSourceConfig):
        return config.clickhouse, tables or config.clickhouse_tables
    return config, tables
