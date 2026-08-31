import json
from pathlib import Path

import numpy as np
import pandas as pd

from factor_backtest.config import BacktestConfig, DataSourceConfig, HandoffConfig, PathConfig
from factor_backtest.handoff import export_factor_backtest_platform_handoff
from factor_backtest.market_data import MarketDataBundle
from factor_backtest.runner import run_factor_backtest


def _sample_factor_and_market():
    dates = pd.bdate_range("2026-01-01", periods=32)
    factor_dates = dates[:8]
    symbols = [f"S{i:03d}" for i in range(40)]
    base = np.arange(len(dates), dtype=float).reshape(-1, 1)
    symbol_slope = np.arange(1, len(symbols) + 1, dtype=float).reshape(1, -1)
    open_price = pd.DataFrame(10 + base * (0.01 * symbol_slope), index=dates, columns=symbols)
    factor_values = np.tile(np.arange(len(symbols), dtype=float), (len(factor_dates), 1))
    factor_values += np.arange(len(factor_dates), dtype=float).reshape(-1, 1) * 0.1
    factor = pd.DataFrame(factor_values, index=factor_dates, columns=symbols)
    return factor, MarketDataBundle(open_price=open_price)


def test_handoff_disabled_by_default_does_not_create_handoff_dir(tmp_path):
    factor, market = _sample_factor_and_market()
    cfg = BacktestConfig(
        paths=PathConfig(project_dir=tmp_path, data_root=tmp_path, pool_dir=tmp_path),
        output_root=tmp_path / "results",
        selected_pools=["all"],
        factor_name="smoke_factor_v1",
        tradability_filter=False,
        render_plots=False,
        data_sources=DataSourceConfig(risk_exposure_source="none"),
        enabled_sections=[
            "data_quality",
            "cumulative_ic",
            "group_return",
            "long_short",
            "group_turnover",
            "performance_metrics",
        ],
    )

    result = run_factor_backtest(factor_df=factor, market_data=market, config=cfg, log_fn=lambda *_: None)

    assert result.handoff_dir is None
    assert not (tmp_path / "docs" / "handoffs" / "factor_backtest_platform").exists()


def test_handoff_enabled_exports_required_minimal_package(tmp_path):
    factor, market = _sample_factor_and_market()
    cfg = BacktestConfig(
        paths=PathConfig(project_dir=tmp_path, data_root=tmp_path, pool_dir=tmp_path),
        output_root=tmp_path / "results",
        selected_pools=["all"],
        horizons=[1, 5, 10, 20],
        ic_methods=["spearman"],
        factor_name="smoke_factor_v1",
        tradability_filter=False,
        render_plots=False,
        data_sources=DataSourceConfig(risk_exposure_source="none"),
        enabled_sections=[
            "data_quality",
            "cumulative_ic",
            "group_return",
            "long_short",
            "group_turnover",
            "performance_metrics",
        ],
        handoff=HandoffConfig(enabled=True, data_asof="2026-01-12"),
    )

    result = run_factor_backtest(factor_df=factor, market_data=market, config=cfg, log_fn=lambda *_: None)

    handoff_dir = tmp_path / "docs" / "handoffs" / "factor_backtest_platform"
    assert result.handoff_dir == handoff_dir
    assert (handoff_dir / "README.md").exists()
    assert (handoff_dir / "platform_schema_notes.md").exists()
    assert (handoff_dir / "factor_backtest_commit.txt").read_text(encoding="utf-8").strip()
    sample_dir = handoff_dir / "sample_latest"
    meta = json.loads((sample_dir / "run_meta.json").read_text(encoding="utf-8"))
    log = json.loads((sample_dir / "run_log.json").read_text(encoding="utf-8"))
    assert meta["factor_name"] == "smoke_factor_v1"
    assert meta["data_asof"] == "2026-01-12"
    assert meta["pools"] == ["all"]
    assert meta["horizons"] == [1, 5, 10, 20]
    assert meta["entry"] == "next_open"
    assert meta["factor_direction"] == "high_is_long"
    assert meta["data_access"]["provider"] == "legacy_clickhouse_adapter"
    assert log["status"] == "ok"
    assert set(log["sections"]["all"]) == {"ic", "group_return", "performance", "turnover", "data_quality"}

    tables_dir = sample_dir / "pools" / "all" / "tables"
    for filename in [
        "ic_stats_spearman.csv",
        "group_return_summary.csv",
        "performance_metrics.csv",
        "group_turnover_edge_summary.csv",
        "data_quality.csv",
    ]:
        table = pd.read_csv(tables_dir / filename)
        assert not table.empty

    for filename in ["spanning.json", "crowding.json", "mechanism_consistency.json", "diversity.json"]:
        payload = json.loads((sample_dir / "diagnostics" / filename).read_text(encoding="utf-8"))
        assert payload["status"] == "pending(company)"


def test_handoff_export_rejects_ic_stats_missing_required_horizons(tmp_path):
    run_dir = tmp_path / "run"
    tables_dir = run_dir / "pools" / "all" / "tables"
    tables_dir.mkdir(parents=True)
    pd.DataFrame({"horizon": ["1d"], "ic_mean": [0.1]}).to_csv(tables_dir / "ic_stats_spearman.csv", index=False)
    for filename in [
        "group_return_summary.csv",
        "performance_metrics.csv",
        "group_turnover_edge_summary.csv",
        "data_quality.csv",
    ]:
        pd.DataFrame({"metric": ["x"], "value": [1.0]}).to_csv(tables_dir / filename, index=False)
    (run_dir / "run_meta.json").write_text(json.dumps({"horizons": [1, 5]}), encoding="utf-8")
    (run_dir / "run_log.json").write_text(json.dumps({"warnings": [], "sections": {"all": {}}}), encoding="utf-8")
    cfg = BacktestConfig(
        paths=PathConfig(project_dir=tmp_path, data_root=tmp_path, pool_dir=tmp_path),
        handoff=HandoffConfig(enabled=True),
    )

    try:
        export_factor_backtest_platform_handoff(
            run_dir=run_dir,
            config=cfg,
            factor_name="smoke_factor_v1",
            factor_index=pd.to_datetime(["2026-01-01"]),
        )
    except ValueError as exc:
        assert "include horizons" in str(exc)
        assert "5d" in str(exc)
    else:
        raise AssertionError("handoff export should reject missing IC horizons")
