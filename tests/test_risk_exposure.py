import numpy as np
import pandas as pd

from factor_backtest_platform.analytics import (
    compute_group_exposure_diagnostics,
    compute_group_turnover,
    compute_factor_style_exposure_corr,
    compute_factor_style_exposure_corr_from_panel,
    compute_group_exposure_diagnostics_from_panel,
    compute_neutralized_ic_by_exposure_panel,
    compute_within_industry_group_returns,
    compute_within_industry_group_returns_from_panel,
    compute_daily_ic,
    neutralize_factor_by_exposure,
    neutralize_factor_by_exposure_panel,
)
from factor_backtest_platform.risk_exposure import (
    DEFAULT_STYLE_COLUMNS,
    dataframe_to_risk_exposure,
    load_risk_exposure_from_csv,
    load_risk_exposure_from_file,
)


def _risk_raw() -> pd.DataFrame:
    rows = []
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    industries = ["银行", "计算机"]
    for date_idx, date in enumerate(dates):
        for i in range(24):
            row = {"date": date, "symbol": f"S{i:03d}"}
            for style in DEFAULT_STYLE_COLUMNS:
                row[style] = float(i + date_idx)
            row["comovement"] = 999.0
            row["银行"] = 1 if i < 12 else 0
            row["计算机"] = 1 if i >= 12 else 0
            if date_idx == 1 and i == 0:
                row["银行"] = 0
                row["计算机"] = 1
            if date_idx == 1 and i == 1:
                row["银行"] = 1
                row["计算机"] = 1
            rows.append(row)
    return pd.DataFrame(rows)


def test_risk_exposure_loader_ignores_comovement_and_tracks_daily_industry(tmp_path):
    path = tmp_path / "risk_exposure.csv"
    _risk_raw().to_csv(path, index=False)

    data = load_risk_exposure_from_csv(path)

    assert data.style_columns == DEFAULT_STYLE_COLUMNS
    assert "comovement" not in data.exposures.columns
    assert data.industry_columns == ("银行", "计算机")
    first_day = data.slice_date(pd.Timestamp("2026-01-02"), ["S000", "S001"])
    second_day = data.slice_date(pd.Timestamp("2026-01-05"), ["S000", "S001"])
    assert first_day.loc["S000", "银行"] == 1
    assert first_day.loc["S000", "计算机"] == 0
    assert second_day.loc["S000", "银行"] == 0
    assert second_day.loc["S000", "计算机"] == 1
    assert second_day.loc["S001", ["银行", "计算机"]].sum() == 2


def test_risk_exposure_file_loader_accepts_parquet_path(monkeypatch, tmp_path):
    path = tmp_path / "CNE5_Industry_daily.parquet"

    def fake_read_parquet(read_path):
        assert read_path == path
        return _risk_raw()

    monkeypatch.setattr(pd, "read_parquet", fake_read_parquet)

    data = load_risk_exposure_from_file(path)

    assert data.style_columns == DEFAULT_STYLE_COLUMNS
    assert len(data.industry_columns) == 2


def test_risk_exposure_loader_accepts_date_index_dataframe():
    raw = _risk_raw().set_index("date")

    data = dataframe_to_risk_exposure(raw)

    assert data.slice_date(pd.Timestamp("2026-01-02"), ["S000"]).loc["S000", "银行"] == 1


def test_risk_exposure_loader_accepts_date_symbol_multiindex_dataframe():
    raw = _risk_raw().set_index(["date", "symbol"])

    data = dataframe_to_risk_exposure(raw)

    assert data.exposures.index.names == ["trade_date", "symbol"]
    assert data.slice_date(pd.Timestamp("2026-01-02"), ["S000"]).index.tolist() == ["S000"]


def test_risk_exposure_loader_accepts_single_industry_column_and_expands_to_dummies():
    raw = _risk_raw()
    industry_names = ["银行", "计算机"]
    compact = raw.drop(columns=industry_names).copy()
    compact["industry"] = np.where(raw["银行"] == 1, "银行", "计算机")
    data = dataframe_to_risk_exposure(compact)

    assert data.industry_columns == ("银行", "计算机")
    assert "industry" not in data.exposures.columns
    first_day = data.slice_date(pd.Timestamp("2026-01-02"), ["S000", "S013"])
    assert first_day.loc["S000", "银行"] == 1.0
    assert first_day.loc["S000", "计算机"] == 0.0
    assert first_day.loc["S013", "银行"] == 0.0
    assert first_day.loc["S013", "计算机"] == 1.0
    panel = data.to_panel(pd.to_datetime(["2026-01-02"]), ["S000", "S013"])
    assert panel.industry_values.shape == (1, 2, 2)
    assert panel.industry_codes.shape == (1, 2)
    assert panel.industry_codes.tolist() == [[0, 1]]
    assert not panel.has_multi_industry_membership


def test_risk_exposure_loader_sorts_numeric_industry_codes():
    raw = _risk_raw()
    compact = raw.drop(columns=["银行", "计算机"]).copy()
    compact["industry"] = np.where(raw["银行"] == 1, 10, 2)

    data = dataframe_to_risk_exposure(compact)

    assert data.industry_columns == ("2", "10")
    first_day = data.slice_date(pd.Timestamp("2026-01-02"), ["S000", "S013"])
    assert first_day.loc["S000", "10"] == 1.0
    assert first_day.loc["S013", "2"] == 1.0


def test_factor_style_exposure_corr_supports_spearman_and_pearson():
    risk = dataframe_to_risk_exposure(_risk_raw())
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    symbols = [f"S{i:03d}" for i in range(24)]
    factor = pd.DataFrame([range(24), range(23, -1, -1)], index=dates, columns=symbols, dtype=float)

    result = compute_factor_style_exposure_corr(
        factor,
        risk,
        min_stocks=10,
        methods=["spearman", "pearson"],
    )

    assert set(result) == {"spearman", "pearson"}
    assert list(result["spearman"].columns) == list(DEFAULT_STYLE_COLUMNS)
    assert np.isclose(result["spearman"].loc[dates[0], "size"], 1.0)
    assert np.isclose(result["spearman"].loc[dates[1], "size"], -1.0)
    assert np.isclose(result["pearson"].loc[dates[0], "size"], 1.0)


def test_risk_exposure_panel_matches_style_corr_and_neutralization_outputs():
    risk = dataframe_to_risk_exposure(_risk_raw())
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    symbols = [f"S{i:03d}" for i in range(24)]
    factor = pd.DataFrame([range(24), range(23, -1, -1)], index=dates, columns=symbols, dtype=float)
    panel = risk.to_panel(dates, symbols)

    slow_corr = compute_factor_style_exposure_corr(
        factor,
        risk,
        min_stocks=10,
        methods=["spearman", "pearson"],
    )
    fast_corr = compute_factor_style_exposure_corr_from_panel(
        factor,
        panel,
        min_stocks=10,
        methods=["spearman", "pearson"],
    )

    for method in ["spearman", "pearson"]:
        pd.testing.assert_frame_equal(fast_corr[method], slow_corr[method])

    slow_residual, slow_warnings = neutralize_factor_by_exposure(
        factor,
        risk,
        include_styles=True,
        include_industries=True,
        min_stocks=10,
    )
    fast_residual, fast_warnings = neutralize_factor_by_exposure_panel(
        factor,
        panel,
        include_styles=True,
        include_industries=True,
        min_stocks=10,
    )

    pd.testing.assert_frame_equal(fast_residual, slow_residual)
    assert fast_warnings == slow_warnings

    slow_group_exposure = compute_group_exposure_diagnostics(factor, risk, n_groups=10, min_stocks=10)
    fast_group_exposure = compute_group_exposure_diagnostics_from_panel(factor, panel, n_groups=10, min_stocks=10)
    for key in ["style_daily", "style_summary", "industry_daily", "industry_summary"]:
        pd.testing.assert_frame_equal(fast_group_exposure[key], slow_group_exposure[key])


def test_direct_neutralized_ic_matches_residual_then_ic_path():
    risk = dataframe_to_risk_exposure(_risk_raw())
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    symbols = [f"S{i:03d}" for i in range(24)]
    factor = pd.DataFrame(
        [[float((i + day) % 24) for i in range(24)] for day, _ in enumerate(dates)],
        index=dates,
        columns=symbols,
    )
    returns = {
        1: pd.DataFrame(
            [[i / 100.0 for i in range(24)], [np.nan if i == 3 else i / 90.0 for i in range(24)]],
            index=dates,
            columns=symbols,
            dtype=float,
        ),
        "external_alpha": pd.DataFrame(
            [[(23 - i) / 100.0 for i in range(24)], [i / 120.0 for i in range(24)]],
            index=dates,
            columns=symbols,
            dtype=float,
        ),
    }
    panel = risk.to_panel(dates, symbols)

    for include_industries in [False, True]:
        residual, residual_warnings = neutralize_factor_by_exposure_panel(
            factor,
            panel,
            include_styles=True,
            include_industries=include_industries,
            min_stocks=10,
        )
        expected = compute_daily_ic(
            residual,
            returns,
            min_stocks=10,
            methods=["spearman", "pearson"],
        )

        actual, actual_warnings = compute_neutralized_ic_by_exposure_panel(
            factor,
            returns,
            panel,
            include_styles=True,
            include_industries=include_industries,
            min_stocks=10,
            methods=["spearman", "pearson"],
        )

        assert actual_warnings == residual_warnings
        for method in ["spearman", "pearson"]:
            pd.testing.assert_frame_equal(actual[method], expected[method])


def test_neutralize_factor_by_exposure_removes_style_component_and_warns_on_missing_industry():
    raw = _risk_raw()
    raw.loc[(raw["date"] == pd.Timestamp("2026-01-05")) & (raw["symbol"] == "S002"), ["银行", "计算机"]] = 0
    risk = dataframe_to_risk_exposure(raw)
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    symbols = [f"S{i:03d}" for i in range(24)]
    style = risk.wide_style("size", dates, symbols)
    factor = 2.0 * style + 5.0
    factor.loc[dates[1], "S005"] += 3.0

    residual, warnings = neutralize_factor_by_exposure(
        factor,
        risk,
        include_styles=True,
        include_industries=True,
        min_stocks=10,
    )

    assert abs(residual.loc[dates[0]].dropna()).max() < 1e-10
    assert np.isfinite(residual.loc[dates[1], "S005"])
    assert pd.isna(residual.loc[dates[1], "S002"])
    assert any("missing industry" in warning for warning in warnings)


def test_within_industry_group_returns_uses_daily_membership_and_repeats_multi_industry_members():
    raw = _risk_raw()
    risk = dataframe_to_risk_exposure(raw)
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    symbols = [f"S{i:03d}" for i in range(24)]
    factor = pd.DataFrame([range(24), range(24)], index=dates, columns=symbols, dtype=float)
    returns = {
        1: pd.DataFrame(
            [[i / 100.0 for i in range(24)], [i / 100.0 for i in range(24)]],
            index=dates,
            columns=symbols,
            dtype=float,
        )
    }

    result = compute_within_industry_group_returns(
        factor,
        returns,
        risk,
        n_groups=3,
        min_industry_stocks=3,
    )

    assert result.index.names == ["trade_date", "horizon", "group"]
    assert set(result.index.get_level_values("group")) == {1, 2, 3}
    day2 = result.xs((dates[1], 1), level=("trade_date", "horizon"))
    assert day2.loc[1, "group_return"] < day2.loc[3, "group_return"]


def test_within_industry_group_returns_from_panel_matches_original_path():
    raw = _risk_raw()
    risk = dataframe_to_risk_exposure(raw)
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    symbols = [f"S{i:03d}" for i in range(24)]
    factor = pd.DataFrame([range(24), range(24)], index=dates, columns=symbols, dtype=float)
    returns = {
        1: pd.DataFrame(
            [[i / 100.0 for i in range(24)], [i / 100.0 for i in range(24)]],
            index=dates,
            columns=symbols,
            dtype=float,
        ),
        5: pd.DataFrame(
            [[i / 200.0 for i in range(24)], [np.nan if i == 3 else i / 200.0 for i in range(24)]],
            index=dates,
            columns=symbols,
            dtype=float,
        ),
    }
    panel = risk.to_panel(dates, symbols)
    assert panel.has_multi_industry_membership

    slow = compute_within_industry_group_returns(
        factor,
        returns,
        risk,
        n_groups=3,
        min_industry_stocks=3,
    )
    fast = compute_within_industry_group_returns_from_panel(
        factor,
        returns,
        panel,
        n_groups=3,
        min_industry_stocks=3,
    )

    pd.testing.assert_frame_equal(fast, slow)


def test_within_industry_group_returns_from_panel_uses_compact_industry_codes():
    raw = _risk_raw()
    industry_names = [
        col
        for col in raw.columns
        if col not in {"date", "symbol", "comovement", *DEFAULT_STYLE_COLUMNS}
    ]
    compact = raw.drop(columns=industry_names).copy()
    compact["industry"] = np.where(raw[industry_names[0]] == 1, "801760.INDX", "801780.INDX")
    risk = dataframe_to_risk_exposure(compact)
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    symbols = [f"S{i:03d}" for i in range(24)]
    factor = pd.DataFrame([range(24), range(24)], index=dates, columns=symbols, dtype=float)
    returns = {
        1: pd.DataFrame(
            [[i / 100.0 for i in range(24)], [i / 100.0 for i in range(24)]],
            index=dates,
            columns=symbols,
            dtype=float,
        )
    }
    panel = risk.to_panel(dates, symbols)
    code_only_panel = panel.__class__(
        dates=panel.dates,
        symbols=panel.symbols,
        style_columns=panel.style_columns,
        industry_columns=panel.industry_columns,
        style_values=panel.style_values,
        industry_values=np.full_like(panel.industry_values, np.nan),
        industry_codes=panel.industry_codes,
        has_multi_industry_membership=panel.has_multi_industry_membership,
    )

    expected = compute_within_industry_group_returns(
        factor,
        returns,
        risk,
        n_groups=3,
        min_industry_stocks=3,
    )
    result = compute_within_industry_group_returns_from_panel(
        factor,
        returns,
        code_only_panel,
        n_groups=3,
        min_industry_stocks=3,
    )

    pd.testing.assert_frame_equal(result, expected)


def test_group_exposure_diagnostics_reports_both_edge_groups_and_spread():
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    symbols = [f"S{i:02d}" for i in range(20)]
    rows = []
    for date in dates:
        for i, symbol in enumerate(symbols):
            row = {"date": date, "symbol": symbol}
            for style in DEFAULT_STYLE_COLUMNS:
                row[style] = float(i)
            row["bank"] = 1 if i < 10 else 0
            row["tech"] = 1 if i >= 10 else 0
            rows.append(row)
    risk = dataframe_to_risk_exposure(pd.DataFrame(rows))
    factor = pd.DataFrame([range(20), range(19, -1, -1)], index=dates, columns=symbols, dtype=float)

    result = compute_group_exposure_diagnostics(factor, risk, n_groups=10, min_stocks=10)

    style = result["style_daily"]
    industry = result["industry_daily"]
    assert {"pool", "G1", "G10", "G10_minus_G1", "G1_minus_pool", "G10_minus_pool"}.issubset(
        set(style.index.get_level_values("leg"))
    )
    assert np.isclose(style.loc[(dates[0], "G1", "size"), "value"], 0.5)
    assert np.isclose(style.loc[(dates[0], "G10", "size"), "value"], 18.5)
    assert np.isclose(style.loc[(dates[0], "G10_minus_G1", "size"), "value"], 18.0)
    assert np.isclose(industry.loc[(dates[0], "G1", "bank"), "value"], 1.0)
    assert np.isclose(industry.loc[(dates[0], "G10", "tech"), "value"], 1.0)


def test_group_turnover_includes_all_groups_and_edge_average():
    dates = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
    symbols = [f"S{i:02d}" for i in range(20)]
    factor = pd.DataFrame(
        [
            range(20),
            range(20),
            list(range(2, 20)) + [0, 1],
        ],
        index=dates,
        columns=symbols,
        dtype=float,
    )

    daily, summary, edge = compute_group_turnover(factor, n_groups=10, min_stocks=10)

    assert list(daily.columns) == [f"G{i}" for i in range(1, 11)]
    assert pd.isna(daily.loc[dates[0], "G1"])
    assert daily.loc[dates[1], "G1"] == 0.0
    assert daily.loc[dates[2], "G1"] == 1.0
    assert {"G1", "G10", "edge_avg"}.issubset(set(edge.index))
    assert np.isclose(edge.loc["edge_avg", "mean"], (summary.loc["G1", "mean"] + summary.loc["G10", "mean"]) / 2)
