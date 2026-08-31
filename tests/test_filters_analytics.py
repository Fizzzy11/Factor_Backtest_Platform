import numpy as np
import pandas as pd

from factor_backtest.analytics import (
    compute_daily_group_returns,
    compute_daily_equivalent_long_short_returns,
    compute_daily_ic,
    compute_daily_rank_ic,
    compute_future_returns,
    compute_ic_stats,
    compute_long_short_returns,
    compute_newey_west_mean_t_stat,
    compute_performance_metrics,
    compute_quality_metrics,
    compute_yearly_ic_stats,
)
from factor_backtest.filters import compute_tradability_mask


def _dates():
    return pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19", "2026-05-20"])


def test_compute_future_returns_uses_next_open_entry_after_signal_date():
    open_price = pd.DataFrame(
        {"A": [10.0, 11.0, 12.0, 15.0], "B": [20.0, 18.0, 22.0, 24.0]},
        index=_dates(),
    )

    returns = compute_future_returns(open_price, horizons=[1, 2])

    # Signal date 2026-05-15 is evaluated by entering at the next trading day's open.
    assert np.isclose(returns[1].loc[pd.Timestamp("2026-05-15"), "A"], 12.0 / 11.0 - 1.0)
    assert np.isclose(returns[2].loc[pd.Timestamp("2026-05-15"), "A"], 15.0 / 11.0 - 1.0)
    assert pd.isna(returns[1].loc[pd.Timestamp("2026-05-19"), "A"])


def test_newey_west_statistics_use_horizon_minus_one_lag():
    ic = pd.DataFrame(
        {
            "ic_1d": [0.01, 0.02, 0.03, 0.04, 0.05],
            "ic_5d": [0.01, 0.03, 0.02, 0.05, 0.04],
            "ic_external": [0.02, 0.01, 0.03, 0.02, 0.04],
            "ic_30d": [0.01, 0.02, 0.01, 0.02, 0.03],
        }
    )

    stats = compute_ic_stats(ic, horizon_days={"1d": 1, "5d": 5, "external": None, "30d": None})

    assert stats.loc["1d", "hac_lag"] == 0
    assert stats.loc["5d", "hac_lag"] == 4
    assert stats.loc["external", "hac_status"] == "missing_horizon_days"
    assert pd.isna(stats.loc["external", "t_stat_hac"])
    assert stats.loc["30d", "hac_status"] == "missing_horizon_days"
    assert stats.loc["5d", "icir"] == stats.loc["5d", "icir_raw"]
    assert stats.loc["5d", "t_stat"] == stats.loc["5d", "t_stat_naive"]


def test_newey_west_lag_is_capped_by_available_observations():
    t_stat, lag = compute_newey_west_mean_t_stat(pd.Series([0.01, 0.02, 0.03]), lag=20)

    assert lag == 2
    assert np.isfinite(t_stat)


def test_yearly_ic_stats_use_factor_date_year_and_minimum_days():
    dates = pd.to_datetime(["2024-01-02", "2024-06-03", "2024-12-30", "2025-03-03"])
    ic = pd.DataFrame({"ic_5d": [0.01, 0.02, 0.03, 0.04]}, index=dates)

    yearly = compute_yearly_ic_stats(ic, horizon_days={"5d": 5}, min_days=2, include_partial_year=True)

    assert yearly.loc[(2024, "5d"), "valid_days"] == 3
    assert bool(yearly.loc[(2024, "5d"), "is_complete_year"]) is True
    assert bool(yearly.loc[(2025, "5d"), "meets_min_days"]) is False
    assert pd.isna(yearly.loc[(2025, "5d"), "ic_mean"])
    assert yearly.loc[(2024, "5d"), "hac_lag"] == 2


def test_yearly_ic_stats_sort_horizons_by_duration():
    dates = pd.bdate_range("2024-01-02", periods=4)
    ic = pd.DataFrame(
        {
            "ic_1d": [0.01, 0.02, 0.03, 0.04],
            "ic_5d": [0.01, 0.02, 0.03, 0.04],
            "ic_10d": [0.01, 0.02, 0.03, 0.04],
            "ic_20d": [0.01, 0.02, 0.03, 0.04],
        },
        index=dates,
    )

    yearly = compute_yearly_ic_stats(ic, min_days=1)

    assert yearly.index.get_level_values("horizon").tolist() == ["1d", "5d", "10d", "20d"]


def test_daily_equivalent_long_short_is_derived_from_each_edge_group():
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-05-15"), 5, 1),
            (pd.Timestamp("2026-05-15"), 5, 10),
        ],
        names=["trade_date", "horizon", "group"],
    )
    group_returns = pd.DataFrame({"group_return": [0.05, 0.10]}, index=index)

    daily, skipped = compute_daily_equivalent_long_short_returns(
        group_returns,
        horizon_days={"5d": 5},
    )

    expected = (1.10 ** (1 / 5) - 1) - (1.05 ** (1 / 5) - 1)
    assert np.isclose(daily.loc[pd.Timestamp("2026-05-15"), "long_short_5d"], expected)
    assert skipped == []


def test_performance_metrics_marks_overlapping_drawdown_as_not_applicable():
    returns = pd.DataFrame(
        {
            "long_short_1d": [0.01, -0.02, 0.03],
            "long_short_5d": [0.05, -0.04, 0.02],
        }
    )

    metrics = compute_performance_metrics(returns, horizon_days={"1d": 1, "5d": 5})

    assert metrics.loc["long_short_1d", "max_drawdown_applicable"]
    assert np.isfinite(metrics.loc["long_short_1d", "diagnostic_max_drawdown"])
    assert not metrics.loc["long_short_5d", "max_drawdown_applicable"]
    assert pd.isna(metrics.loc["long_short_5d", "diagnostic_max_drawdown"])
    assert metrics.loc["long_short_5d", "not_applicable_reason"] == "overlapping_forward_returns"
    assert metrics.loc["long_short_5d", "hac_lag"] == 2


def test_compute_tradability_mask_applies_entry_day_filters_only():
    dates = pd.to_datetime(["2026-05-15"])
    cols = ["A", "B", "C", "D", "E"]
    high = pd.DataFrame([[10, 10, 10, 10, 10]], index=dates, columns=cols)
    low = pd.DataFrame([[9, 9, 9, 9, 9]], index=dates, columns=cols)
    limit_up = pd.DataFrame([[11, 10, 11, 11, 11]], index=dates, columns=cols)
    limit_down = pd.DataFrame([[8, 8, 9, 8, 8]], index=dates, columns=cols)
    is_st = pd.DataFrame([[0, 0, 0, 1, 0]], index=dates, columns=cols)
    is_suspended = pd.DataFrame([[0, 0, 0, 0, 1]], index=dates, columns=cols)
    listed_days = pd.DataFrame([[121, 121, 121, 121, 121]], index=dates, columns=cols)
    open_price = pd.DataFrame([[10, 10, 10, 10, 10]], index=dates, columns=cols)

    result = compute_tradability_mask(
        open_price=open_price,
        high_price=high,
        low_price=low,
        limit_up_price=limit_up,
        limit_down_price=limit_down,
        is_st=is_st,
        is_suspended=is_suspended,
        listed_days=listed_days,
        min_listed_days=120,
    )

    assert bool(result.mask.loc[dates[0], "A"]) is True
    assert bool(result.mask.loc[dates[0], "B"]) is False
    assert bool(result.mask.loc[dates[0], "C"]) is False
    assert bool(result.mask.loc[dates[0], "D"]) is False
    assert bool(result.mask.loc[dates[0], "E"]) is False
    assert result.summary.loc[dates[0], "limit_up_touch_count"] == 1
    assert result.summary.loc[dates[0], "limit_down_touch_count"] == 1
    assert result.summary.loc[dates[0], "st_count"] == 1
    assert result.summary.loc[dates[0], "suspended_count"] == 1


def test_daily_rank_ic_group_returns_and_long_short_are_pool_local():
    dates = pd.to_datetime(["2026-05-15", "2026-05-18"])
    symbols = [f"S{i}" for i in range(40)]
    factor = pd.DataFrame([range(40), range(40)], index=dates, columns=symbols, dtype=float)
    future = pd.DataFrame([range(40), range(40)], index=dates, columns=symbols, dtype=float)

    ic = compute_daily_rank_ic(factor, {1: future}, min_stocks=30)
    groups = compute_daily_group_returns(factor, {1: future}, n_groups=10, min_stocks=10)
    long_short = compute_long_short_returns(groups)

    assert ic.loc[dates[0], "ic_1d"] == 1.0
    assert groups.loc[(dates[0], 1, 1), "group_return"] < groups.loc[(dates[0], 1, 10), "group_return"]
    assert long_short.loc[dates[0], "long_short_1d"] > 0


def test_compute_daily_ic_supports_spearman_and_pearson_outputs():
    dates = pd.to_datetime(["2026-05-15"])
    symbols = ["A", "B", "C", "D"]
    factor = pd.DataFrame([[1.0, 2.0, 3.0, 100.0]], index=dates, columns=symbols)
    future = pd.DataFrame([[1.0, 4.0, 9.0, 16.0]], index=dates, columns=symbols)

    result = compute_daily_ic(
        factor,
        {"custom": future},
        min_stocks=4,
        methods=["spearman", "pearson"],
    )

    assert set(result) == {"spearman", "pearson"}
    assert result["spearman"].loc[dates[0], "ic_custom"] == 1.0
    assert result["pearson"].loc[dates[0], "ic_custom"] < 1.0


def test_compute_daily_ic_rejects_unknown_methods():
    dates = pd.to_datetime(["2026-05-15"])
    factor = pd.DataFrame([[1.0, 2.0]], index=dates, columns=["A", "B"])
    future = pd.DataFrame([[1.0, 2.0]], index=dates, columns=["A", "B"])

    try:
        compute_daily_ic(factor, {1: future}, min_stocks=2, methods=["kendall"])
    except ValueError as exc:
        assert "Unsupported IC methods" in str(exc)
    else:
        raise AssertionError("Expected unsupported IC method to raise ValueError")


def test_quality_metrics_and_ic_stats():
    factor = pd.DataFrame(
        {"A": [0.0, np.nan], "B": [np.inf, 2.0], "C": [3.0, 0.0]},
        index=pd.to_datetime(["2026-05-15", "2026-05-18"]),
    )
    pool_mask = pd.DataFrame(True, index=factor.index, columns=factor.columns)
    valid_mask = np.isfinite(factor)

    quality = compute_quality_metrics(factor, pool_mask, valid_mask)

    assert quality.loc[pd.Timestamp("2026-05-15"), "zero_ratio"] == 1 / 3
    assert quality.loc[pd.Timestamp("2026-05-15"), "inf_ratio"] == 1 / 3
    assert quality.loc[pd.Timestamp("2026-05-18"), "nan_ratio"] == 1 / 3

    ic = pd.DataFrame({"ic_1d": [0.1, 0.2, -0.1, 0.0]})
    stats = compute_ic_stats(ic)

    assert np.isclose(stats.loc["1d", "ic_mean"], 0.05)
    assert stats.loc["1d", "ic_positive_ratio"] == 0.5
