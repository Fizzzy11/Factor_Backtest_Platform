from pathlib import Path
import tempfile

import pandas as pd

from factor_backtest.config import BacktestConfig
from factor_backtest.market_data import MarketDataBundle
from factor_backtest.runner import run_factor_backtest
from factor_backtest.returns import build_return_specs, normalize_return_dataframe


def _sample_factor_and_market():
    dates = pd.bdate_range("2026-05-01", periods=8)
    symbols = [f"S{i}" for i in range(40)]
    factor = pd.DataFrame([range(40) for _ in dates], index=dates, columns=symbols, dtype=float)
    open_price = pd.DataFrame(
        [[10 + i + date_idx for i in range(40)] for date_idx in range(len(dates))],
        index=dates,
        columns=symbols,
        dtype=float,
    )
    return dates, symbols, factor, MarketDataBundle(open_price=open_price)


def test_external_return_dataframe_runs_through_core_outputs_without_cumulative_plot_when_horizon_missing():
    dates, symbols, factor, market = _sample_factor_and_market()
    external_return = pd.DataFrame(
        [[symbol_idx / 1000 for symbol_idx in range(len(symbols))] for _ in dates],
        index=dates,
        columns=symbols,
    )

    with tempfile.TemporaryDirectory() as tmp:
        cfg = BacktestConfig(
            output_root=Path(tmp),
            factor_name="factor_dm_20d",
            selected_pools=["all"],
            horizons=[1],
            tradability_filter=False,
            enabled_sections=["cumulative_ic", "group_return", "layered_group_return", "long_short", "performance_metrics"],
            export_static_report=True,
            render_plots=True,
        )

        result = run_factor_backtest(
            factor_df=factor,
            market_data=market,
            config=cfg,
            external_returns={"external_alpha": external_return},
        )

        pool = result.section_status["all"]
        assert "ic_external_alpha" in pool["cumulative_ic"].tables["daily_ic"].columns
        assert "external_alpha" in pool["group_return"].tables["group_return_summary"].columns
        assert "long_short_external_alpha" in pool["long_short"].tables["daily_long_short_returns"].columns
        assert "group_cumulative_return_external_alpha.png" not in pool["group_return"].plots
        assert any("external_alpha" in warning and "horizon_days" in warning for warning in pool["group_return"].warnings)


def test_external_return_with_horizon_days_gets_cumulative_group_return_plot():
    dates, symbols, factor, market = _sample_factor_and_market()
    external_long = pd.DataFrame(
        [
            {"trade_date": date, "asset": symbol, "return": symbol_idx / 1000}
            for date in dates
            for symbol_idx, symbol in enumerate(symbols)
        ]
    )

    with tempfile.TemporaryDirectory() as tmp:
        cfg = BacktestConfig(
            output_root=Path(tmp),
            factor_name="factor_dm_20d",
            selected_pools=["all"],
            horizons=[1],
            tradability_filter=False,
            enabled_sections=["group_return"],
            export_static_report=True,
            render_plots=True,
        )

        result = run_factor_backtest(
            factor_df=factor,
            market_data=market,
            config=cfg,
            external_returns={"external_5d": {"data": external_long, "horizon_days": 5}},
        )

        group_result = result.section_status["all"]["group_return"]
        assert "group_cumulative_returns_external_5d" in group_result.tables
        assert "group_cumulative_return_external_5d.png" in group_result.plots


def test_normalize_wide_external_return_dataframe_uses_date_column_as_index():
    raw = pd.DataFrame(
        {
            "trade_date": ["2026-05-15", "2026-05-18"],
            "000001.XSHE": [0.01, 0.02],
            "600000.XSHG": [0.03, 0.04],
        }
    )

    out = normalize_return_dataframe(raw)

    assert out.index.name == "trade_date"
    assert list(out.columns) == ["000001.XSHE", "600000.XSHG"]
    assert out.loc[pd.Timestamp("2026-05-18"), "600000.XSHG"] == 0.04
    assert "trade_date" not in out.columns


def test_external_return_label_cannot_overwrite_builtin_return_label():
    dates, symbols, _, market = _sample_factor_and_market()
    external_return = pd.DataFrame(0.01, index=dates, columns=symbols)

    try:
        build_return_specs(open_price=market.open_price, horizons=[1], external_returns={"1d": external_return})
    except ValueError as exc:
        assert "duplicates an existing return label" in str(exc)
    else:
        raise AssertionError("Expected duplicate external return label to raise ValueError")
