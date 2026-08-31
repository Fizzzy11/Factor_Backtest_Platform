import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from factor_backtest.result_store import (
    RESULT_SCHEMA_VERSION,
    _factor_publish_lock,
    prepare_run_paths,
    publish_run,
    read_result_table,
    resolve_latest_run,
    should_persist_table,
    write_result_data,
)
from factor_backtest.result_views import build_group_return_view, build_ic_view, build_quality_view
from factor_backtest.sections import SectionResult


def test_staging_run_is_moved_to_immutable_run_and_updates_latest(tmp_path):
    paths = prepare_run_paths(
        output_root=tmp_path,
        factor_name="factor_publish_test",
        run_id="run_001",
        output_layout="latest_runs",
    )
    (paths.working_dir / "manifest.json").write_text("{}", encoding="utf-8")
    latest_payload = {
        "schema_version": "1.0",
        "factor_name": "factor_publish_test",
        "run_id": "run_001",
        "relative_path": "runs/run_001",
    }

    final_dir = publish_run(paths, latest_payload)

    assert final_dir == paths.final_dir
    assert final_dir.is_dir()
    assert not paths.working_dir.exists()
    assert json.loads(paths.latest_index_path.read_text(encoding="utf-8")) == latest_payload
    assert not (paths.factor_root / ".publish.lock").exists()


def test_factor_publish_lock_rejects_second_publisher_and_releases_lock(tmp_path):
    factor_root = tmp_path / "factor_lock_test"

    with _factor_publish_lock(factor_root):
        assert (factor_root / ".publish.lock").is_dir()
        with pytest.raises(TimeoutError, match="发布锁超时"):
            with _factor_publish_lock(factor_root, timeout_seconds=0):
                pass

    assert not (factor_root / ".publish.lock").exists()


def test_result_store_merges_pools_and_restores_multiindex(tmp_path):
    dates = pd.date_range("2026-05-01", periods=2, freq="B", name="trade_date")
    index = pd.MultiIndex.from_product(
        [dates, ["1d"], [1, 2]],
        names=["trade_date", "horizon", "group"],
    )
    all_table = pd.DataFrame({"group_return": np.arange(len(index), dtype=float)}, index=index)
    hs300_table = all_table + 10
    status = {
        "all": {
            "group_return": SectionResult(
                name="group_return",
                status="success",
                tables={"daily_group_returns": all_table},
            )
        },
        "hs300_pool": {
            "group_return": SectionResult(
                name="group_return",
                status="success",
                tables={"daily_group_returns": hs300_table},
            )
        },
    }

    tables = write_result_data(tmp_path, status)

    assert tables["daily_group_returns"]["pools"] == ["all", "hs300_pool"]
    loaded = read_result_table(tmp_path, tables["daily_group_returns"], pool="hs300_pool")
    pd.testing.assert_frame_equal(loaded, hs300_table)


def test_result_store_preserves_nan_and_infinities(tmp_path):
    dates = pd.date_range("2026-05-01", periods=4, freq="B", name="trade_date")
    table = pd.DataFrame({"ic_1d": [np.nan, np.inf, -np.inf, 0.1]}, index=dates)
    status = {
        "all": {
            "cumulative_ic": SectionResult(
                name="cumulative_ic",
                status="success",
                tables={"daily_ic_spearman": table},
            )
        }
    }

    tables = write_result_data(tmp_path, status)
    loaded = read_result_table(tmp_path, tables["daily_ic_spearman"], pool="all")

    assert np.isnan(loaded.iloc[0, 0])
    assert np.isposinf(loaded.iloc[1, 0])
    assert np.isneginf(loaded.iloc[2, 0])
    assert loaded.iloc[3, 0] == pytest.approx(0.1)


def test_result_store_filters_dates_without_loading_other_pool(tmp_path):
    dates = pd.date_range("2026-05-01", periods=5, freq="B", name="trade_date")
    table = pd.DataFrame({"ic_1d": np.arange(5, dtype=float)}, index=dates)
    status = {
        pool: {
            "cumulative_ic": SectionResult(
                name="cumulative_ic",
                status="success",
                tables={"daily_ic_spearman": table + offset},
            )
        }
        for pool, offset in (("all", 0), ("hs300_pool", 100))
    }
    tables = write_result_data(tmp_path, status)

    loaded = read_result_table(
        tmp_path,
        tables["daily_ic_spearman"],
        pool="all",
        start_date=dates[1],
        end_date=dates[3],
    )

    assert list(loaded.index) == list(dates[1:4])
    assert loaded["ic_1d"].tolist() == [1.0, 2.0, 3.0]


def test_latest_json_rejects_path_outside_factor_root(tmp_path):
    factor_root = tmp_path / "factor"
    factor_root.mkdir()
    (factor_root / "latest.json").write_text(
        json.dumps({"relative_path": "../outside"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="之外"):
        resolve_latest_run(factor_root)


def test_latest_loader_keeps_legacy_directory_compatible(tmp_path):
    factor_root = tmp_path / "factor"
    legacy = factor_root / "latest"
    legacy.mkdir(parents=True)

    assert resolve_latest_run(factor_root) == legacy.resolve()


def test_persistence_whitelist_excludes_derived_and_compatibility_tables():
    assert should_persist_table("daily_ic_spearman")
    assert should_persist_table("daily_group_returns")
    assert should_persist_table("group_industry_exposure_daily")
    assert not should_persist_table("daily_ic")
    assert not should_persist_table("cumulative_ic_spearman")
    assert not should_persist_table("ic_stats_spearman")
    assert not should_persist_table("group_return_summary")
    assert not should_persist_table("daily_group_turnover")


def test_ic_view_rebases_selected_range_and_uses_warmup_history():
    dates = pd.date_range("2026-01-01", periods=30, freq="B", name="trade_date")
    daily = pd.DataFrame({"ic_20d": np.arange(30, dtype=float)}, index=dates)

    view = build_ic_view(
        daily,
        start_date=dates[20],
        end_date=dates[24],
        horizon_days={"20d": 20},
        yearly_min_days=1,
    )

    assert view.cumulative.iloc[0, 0] == pytest.approx(0.0)
    assert view.moving_average_20d.iloc[0, 0] == pytest.approx(np.mean(np.arange(1, 21)))
    assert view.stats.loc["20d", "valid_days"] == 5


def test_group_return_view_rebases_each_cumulative_curve():
    dates = pd.date_range("2026-05-01", periods=3, freq="B")
    records = [
        {"trade_date": date, "horizon": "5d", "group": group, "group_return": group / 100}
        for date in dates
        for group in range(1, 11)
    ]
    daily = pd.DataFrame(records).set_index(["trade_date", "horizon", "group"])

    view = build_group_return_view(daily, horizon_days={"5d": 5})

    cumulative = view.cumulative_by_horizon["5d"]
    assert (cumulative.iloc[0] == 1.0).all()
    assert list(view.daily_long_short.columns) == ["long_short_5d"]
    assert view.performance_metrics.loc["long_short_5d", "valid_days"] == 3


def test_quality_view_filters_dates_and_separates_counts_from_ratios():
    dates = pd.date_range("2026-05-01", periods=4, freq="B", name="trade_date")
    quality = pd.DataFrame(
        {
            "pool_stock_count": [100, 101, 102, 103],
            "valid_factor_count": [80, 81, 82, 83],
            "coverage_ratio": [0.8, 0.81, 0.82, 0.83],
            "nan_ratio": [0.2, 0.19, 0.18, 0.17],
        },
        index=dates,
    )

    view = build_quality_view(quality, start_date=dates[1], end_date=dates[2])

    assert list(view.daily.index) == list(dates[1:3])
    assert list(view.counts.columns) == ["pool_stock_count", "valid_factor_count"]
    assert list(view.ratios.columns) == ["coverage_ratio", "nan_ratio"]


def test_unnamed_datetime_index_is_materialized_as_trade_date(tmp_path):
    dates = pd.date_range("2026-05-01", periods=4, freq="B")
    daily = pd.DataFrame({"G1": [np.nan, 0.1, 0.2, 0.3]}, index=dates)
    status = {
        "all": {
            "group_turnover": SectionResult(
                name="group_turnover",
                status="success",
                tables={"daily_group_membership_change": daily},
            )
        }
    }

    tables = write_result_data(tmp_path, status)
    table_meta = tables["daily_group_membership_change"]
    loaded = read_result_table(
        tmp_path,
        table_meta,
        pool="all",
        start_date=dates[1],
        end_date=dates[2],
    )

    assert table_meta["index_columns"] == ["trade_date"]
    assert loaded.index.name == "trade_date"
    assert list(loaded.index) == list(dates[1:3])
