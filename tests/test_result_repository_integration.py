import json
from pathlib import Path

import pandas as pd
import pytest

from factor_backtest_platform.config import BacktestConfig
from factor_backtest_platform.market_data import MarketDataBundle
from factor_backtest_platform.result_loader import load_backtest_result
from factor_backtest_platform.runner import render_factor_backtest_report, run_factor_backtest, run_factor_backtest_data
from factor_backtest_platform.sections import ReportSection


class _FailingCoreSection(ReportSection):
    name = "cumulative_ic"

    def compute(self, context):
        raise RuntimeError("核心模块测试失败")


def _sample_inputs():
    dates = pd.bdate_range("2026-01-02", periods=35, name="trade_date")
    symbols = [f"S{i:03d}" for i in range(40)]
    factor = pd.DataFrame(
        [[float((i + day) % 40) for i in range(40)] for day in range(len(dates))],
        index=dates,
        columns=symbols,
    )
    open_price = pd.DataFrame(
        [[10.0 + i * 0.05 + day * (0.01 + i * 0.0002) for i in range(40)] for day in range(len(dates))],
        index=dates,
        columns=symbols,
    )
    return factor, MarketDataBundle(open_price=open_price)


def test_schema2_data_run_is_compact_queryable_and_rerenderable(tmp_path):
    factor, market = _sample_inputs()
    config = BacktestConfig(
        output_root=tmp_path,
        factor_name="factor_schema2_test",
        selected_pools=["all"],
        horizons=[1, 5],
        tradability_filter=False,
        enabled_sections=[
            "data_quality",
            "ic_overview",
            "cumulative_ic",
            "group_return",
            "long_short",
            "performance_metrics",
        ],
    )

    result = run_factor_backtest_data(
        factor_df=factor,
        market_data=market,
        config=config,
        log_fn=lambda *_: None,
    )

    factor_root = tmp_path / "factor_schema2_test"
    assert result.run_dir.parent == factor_root / "runs"
    assert not (factor_root / "latest").exists()
    assert not (result.run_dir / "report.html").exists()
    assert not list(result.run_dir.rglob("*.png"))
    assert not list(result.run_dir.rglob("*.csv"))
    latest = json.loads((factor_root / "latest.json").read_text(encoding="utf-8"))
    assert latest["relative_path"] == f"runs/{result.run_dir.name}"
    manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
    meta = json.loads((result.run_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "2.0"
    assert manifest["package_version"] == "1.0.0"
    assert meta["package_version"] == "1.0.0"

    loaded = load_backtest_result(factor_name="factor_schema2_test", output_root=tmp_path)
    assert loaded.run_dir == result.run_dir.resolve()
    assert loaded.schema_version == "2.0"
    assert loaded.available_pools() == ["all"]

    start_date = factor.index[10]
    end_date = factor.index[20]
    ic_view = loaded.ic_view(pool="all", start_date=start_date, end_date=end_date)
    group_view = loaded.group_return_view(pool="all", start_date=start_date, end_date=end_date)
    quality_view = loaded.quality_view(pool="all", start_date=start_date, end_date=end_date)
    assert ic_view.cumulative.iloc[0].dropna().eq(0.0).all()
    assert all(table.iloc[0].dropna().eq(1.0).all() for table in group_view.cumulative_by_horizon.values())
    assert quality_view.daily.index.min() == start_date
    assert quality_view.daily.index.max() == end_date
    assert list(quality_view.counts.columns) == ["pool_stock_count", "valid_factor_count"]

    expected_ic_stats = result.section_status["all"]["cumulative_ic"].tables["ic_stats_spearman"]
    expected_group_summary = result.section_status["all"]["group_return"].tables["group_return_summary"]
    pd.testing.assert_frame_equal(loaded.read_table("all", "ic_stats_spearman"), expected_ic_stats)
    pd.testing.assert_frame_equal(loaded.read_table("all", "group_return_summary"), expected_group_summary)

    report_path = render_factor_backtest_report(result.run_dir)
    assert report_path.exists()
    assert loaded.plot_path("all", "cumulative_ic_spearman.png").exists()
    assert loaded.plot_path("all", "group_cumulative_return_5d.png").exists()
    html = report_path.read_text(encoding="utf-8")
    assert "plots/all/cumulative_ic_spearman.png" in html


def test_platform_default_run_is_data_only_and_does_not_modify_factor_input(tmp_path):
    factor, market = _sample_inputs()
    original_factor = factor.copy(deep=True)
    config = BacktestConfig(
        output_root=tmp_path,
        factor_name="factor_platform_default",
        selected_pools=["all"],
        horizons=[1],
        tradability_filter=False,
        enabled_sections=["data_quality"],
    )

    result = run_factor_backtest(
        factor_df=factor,
        market_data=market,
        config=config,
        log_fn=lambda *_: None,
    )

    assert not (result.run_dir / "report.html").exists()
    assert not list(result.run_dir.rglob("*.png"))
    pd.testing.assert_frame_equal(factor, original_factor)
    meta = json.loads((result.run_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["export_static_report"] is False
    assert meta["render_plots"] is False


def test_core_section_failure_does_not_publish_latest(tmp_path):
    factor, market = _sample_inputs()
    config = BacktestConfig(
        output_root=tmp_path,
        factor_name="factor_core_failure",
        selected_pools=["all"],
        horizons=[1],
        tradability_filter=False,
        export_static_report=False,
    )

    with pytest.raises(RuntimeError, match="拒绝发布 latest"):
        run_factor_backtest(
            factor_df=factor,
            market_data=market,
            config=config,
            sections=[_FailingCoreSection()],
            log_fn=lambda *_: None,
        )

    factor_root = tmp_path / "factor_core_failure"
    assert not (factor_root / "latest.json").exists()
    published = [path for path in (factor_root / "runs").iterdir() if path.name != ".staging"]
    assert published == []
