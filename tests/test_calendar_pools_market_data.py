import pandas as pd
import tempfile
from pathlib import Path

from factor_backtest_platform.calendar import TradingCalendar
from factor_backtest_platform.config import POOL_REGISTRY, PoolDefinition
from factor_backtest_platform.market_data import derive_listed_days_from_open
from factor_backtest_platform.pools import load_pool_mask, resolve_selected_pools


def test_trading_calendar_shifts_by_trading_days_not_natural_days():
    cal = TradingCalendar(["2026-05-15", "2026-05-18", "2026-05-19", "2026-05-20"])

    assert cal.shift("2026-05-15", 1) == pd.Timestamp("2026-05-18")
    assert cal.shift("2026-05-15", 3) == pd.Timestamp("2026-05-20")
    assert cal.window_ending("2026-05-21", 2) == [
        pd.Timestamp("2026-05-19"),
        pd.Timestamp("2026-05-20"),
    ]


def test_derive_listed_days_counts_available_market_rows_per_symbol():
    open_price = pd.DataFrame(
        {
            "000001.XSHE": [10.0, 10.2, 10.3],
            "600000.XSHG": [None, 8.1, 8.2],
        },
        index=pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"]),
    )

    listed_days = derive_listed_days_from_open(open_price)

    assert listed_days.loc[pd.Timestamp("2026-05-15"), "000001.XSHE"] == 1
    assert listed_days.loc[pd.Timestamp("2026-05-18"), "000001.XSHE"] == 2
    assert pd.isna(listed_days.loc[pd.Timestamp("2026-05-15"), "600000.XSHG"])
    assert listed_days.loc[pd.Timestamp("2026-05-18"), "600000.XSHG"] == 1


def test_load_pool_mask_from_long_csv():
    with tempfile.TemporaryDirectory() as tmp:
        pool_file = Path(tmp) / "hs300_pool.csv"
        pool_file.write_text(
            "trade_date,symbol\n"
            "2026-05-15,000001.XSHE\n"
            "2026-05-15,600000.XSHG\n"
            "2026-05-18,000001.XSHE\n",
            encoding="utf-8",
        )

        mask = load_pool_mask(pool_file)

        assert bool(mask.loc[pd.Timestamp("2026-05-15"), "000001.XSHE"]) is True
        assert bool(mask.loc[pd.Timestamp("2026-05-18"), "600000.XSHG"]) is False


def test_resolve_selected_pools_defaults_to_all():
    resolved = resolve_selected_pools(None)

    assert list(resolved) == ["all"]
    assert resolved["all"] is None


def test_resolve_selected_pools_uses_configured_pool_dir_for_relative_registry_paths():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pool_file = tmp_path / "tmp_pool.csv"
        pool_file.write_text("trade_date,symbol\n2026-05-15,000001.XSHE\n", encoding="utf-8")
        POOL_REGISTRY["tmp_relative_pool"] = PoolDefinition(path=Path("tmp_pool.csv"), display_name="tmp")
        try:
            resolved = resolve_selected_pools(["tmp_relative_pool"], pool_dir=tmp_path)
        finally:
            POOL_REGISTRY.pop("tmp_relative_pool", None)

    assert bool(resolved["tmp_relative_pool"].loc[pd.Timestamp("2026-05-15"), "000001.XSHE"]) is True
