import pandas as pd

from factor_backtest.clickhouse_adapter import build_market_data_sql, dataframe_to_market_data, load_market_data_from_clickhouse
from factor_backtest.config import ClickHouseConfig, ClickHouseTableConfig, DataSourceConfig


def test_build_market_data_sql_contains_required_tables_and_fields():
    sql = build_market_data_sql(start_date="2020-01-01", end_date="2020-02-01")

    assert "stock_data.view_stock_qfq_adjusted_ohlcv_v2" in sql
    assert "cn_stock_fundamentals.shares" in sql
    assert "cn_stock_fundamentals.is_st_stock" in sql
    assert "cn_stock_fundamentals.is_suspended" in sql
    assert "o.high_price AS high_price" in sql
    assert "o.low_price AS low_price" in sql
    assert "o.limit_up AS limit_up" in sql
    assert "o.limit_down AS limit_down" in sql
    assert "o.date >= '2020-01-01'" in sql
    assert "o.date <  '2020-02-01'" in sql


def test_build_market_data_sql_accepts_centralized_table_config():
    tables = ClickHouseTableConfig(
        ohlcv="db.custom_ohlcv",
        shares="db.custom_shares",
        st="db.custom_st",
        suspended="db.custom_suspended",
    )

    sql = build_market_data_sql(start_date="2020-01-01", end_date="2020-02-01", tables=tables)

    assert "FROM db.custom_ohlcv o" in sql
    assert "LEFT JOIN db.custom_shares sh" in sql
    assert "LEFT JOIN db.custom_st st" in sql
    assert "LEFT JOIN db.custom_suspended sp" in sql


def test_dataframe_to_market_data_pivots_clickhouse_rows_to_wide_bundle():
    raw = pd.DataFrame(
        {
            "symbol": ["000001.XSHE", "600000.XSHG", "000001.XSHE"],
            "trade_date": ["2026-05-15", "2026-05-15", "2026-05-18"],
            "open_price": [10.0, 20.0, 11.0],
            "close_price": [10.5, 19.5, 11.5],
            "high_price": [10.8, 20.2, 11.7],
            "low_price": [9.9, 19.0, 10.8],
            "volume": [100, 200, 110],
            "amount": [1000.0, 4000.0, 1210.0],
            "market_cap": [10000.0, 20000.0, 11000.0],
            "limit_up": [11.0, 22.0, 12.1],
            "limit_down": [9.0, 18.0, 9.9],
            "is_st": [0, 1, 0],
            "is_suspended": [0, 0, 1],
        }
    )

    bundle = dataframe_to_market_data(raw)

    assert bundle.open_price.loc[pd.Timestamp("2026-05-15"), "000001.XSHE"] == 10.0
    assert bundle.limit_up_price.loc[pd.Timestamp("2026-05-18"), "000001.XSHE"] == 12.1
    assert bundle.is_st.loc[pd.Timestamp("2026-05-15"), "600000.XSHG"] == 1
    assert bundle.is_suspended.loc[pd.Timestamp("2026-05-18"), "000001.XSHE"] == 1
    assert bundle.listed_days.loc[pd.Timestamp("2026-05-18"), "000001.XSHE"] == 2


def test_load_market_data_from_clickhouse_uses_injected_client():
    class FakeClient:
        def __init__(self):
            self.sql = None

        def query_df(self, sql):
            self.sql = sql
            return pd.DataFrame(
                {
                    "symbol": ["000001.XSHE"],
                    "trade_date": ["2026-05-15"],
                    "open_price": [10.0],
                    "close_price": [10.2],
                    "high_price": [10.3],
                    "low_price": [9.8],
                    "volume": [100],
                    "amount": [1000.0],
                    "market_cap": [10000.0],
                    "limit_up": [11.0],
                    "limit_down": [9.0],
                    "is_st": [0],
                    "is_suspended": [0],
                }
            )

    client = FakeClient()
    messages = []
    bundle = load_market_data_from_clickhouse(
        start_date="2026-05-01",
        end_date="2026-06-01",
        client=client,
        verbose=True,
        log_fn=messages.append,
    )

    assert "o.date >= '2026-05-01'" in client.sql
    assert bundle.open_price.loc[pd.Timestamp("2026-05-15"), "000001.XSHE"] == 10.0
    assert any("loading market data from ClickHouse" in message for message in messages)


def test_load_market_data_accepts_data_source_config_for_connection_and_tables():
    class FakeClient:
        def __init__(self):
            self.sql = None

        def query_df(self, sql):
            self.sql = sql
            return pd.DataFrame(
                {
                    "symbol": ["000001.XSHE"],
                    "trade_date": ["2026-05-15"],
                    "open_price": [10.0],
                    "close_price": [10.2],
                    "high_price": [10.3],
                    "low_price": [9.8],
                    "volume": [100],
                    "amount": [1000.0],
                    "market_cap": [10000.0],
                    "limit_up": [11.0],
                    "limit_down": [9.0],
                    "is_st": [0],
                    "is_suspended": [0],
                }
            )

    data_sources = DataSourceConfig(
        clickhouse=ClickHouseConfig(host="example"),
        clickhouse_tables=ClickHouseTableConfig(ohlcv="db.ohlcv", shares="db.shares", st="db.st", suspended="db.suspended"),
    )
    client = FakeClient()

    load_market_data_from_clickhouse(
        start_date="2026-05-01",
        end_date="2026-06-01",
        client=client,
        config=data_sources,
        verbose=False,
    )

    assert "FROM db.ohlcv o" in client.sql
    assert "LEFT JOIN db.shares sh" in client.sql
