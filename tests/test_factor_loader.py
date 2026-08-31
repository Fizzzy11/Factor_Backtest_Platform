from pathlib import Path
import tempfile
import warnings

import numpy as np
import pandas as pd
import pytest

from factor_backtest_platform.factor_loader import load_factor_file, normalize_factor_dataframe, resolve_factor_path


def test_normalize_wide_factor_dataframe_keeps_trade_date_by_symbol_shape():
    raw = pd.DataFrame(
        {
            "000001.XSHE": [1.0, np.nan],
            "600000.XSHG": [2.0, 3.0],
        },
        index=["2026-05-15", "2026-05-18"],
    )

    out = normalize_factor_dataframe(raw)

    assert out.index.name == "trade_date"
    assert list(out.columns) == ["000001.XSHE", "600000.XSHG"]
    assert out.index.tolist() == [pd.Timestamp("2026-05-15"), pd.Timestamp("2026-05-18")]
    assert out.loc[pd.Timestamp("2026-05-15"), "600000.XSHG"] == 2.0


def test_normalize_wide_factor_dataframe_uses_date_column_as_index():
    raw = pd.DataFrame(
        {
            "date": ["2026-05-15", "2026-05-18"],
            "000001.XSHE": [1.0, np.nan],
            "600000.XSHG": [2.0, 3.0],
        }
    )

    out = normalize_factor_dataframe(raw)

    assert out.index.name == "trade_date"
    assert list(out.columns) == ["000001.XSHE", "600000.XSHG"]
    assert out.index.tolist() == [pd.Timestamp("2026-05-15"), pd.Timestamp("2026-05-18")]
    assert out.loc[pd.Timestamp("2026-05-18"), "600000.XSHG"] == 3.0
    assert "date" not in out.columns


def test_normalize_multiindex_factor_dataframe_pivots_date_asset_to_wide():
    idx = pd.MultiIndex.from_tuples(
        [
            ("2026-05-15", "000001.XSHE"),
            ("2026-05-15", "600000.XSHG"),
            ("2026-05-18", "000001.XSHE"),
        ],
        names=["date", "asset"],
    )
    raw = pd.DataFrame({"factor": [1.0, 2.0, 3.0]}, index=idx)

    out = normalize_factor_dataframe(raw)

    assert out.index.name == "trade_date"
    assert out.columns.name == "symbol"
    assert out.loc[pd.Timestamp("2026-05-15"), "000001.XSHE"] == 1.0
    assert pd.isna(out.loc[pd.Timestamp("2026-05-18"), "600000.XSHG"])


def test_normalize_long_factor_dataframe_uses_symbol_and_value_columns():
    raw = pd.DataFrame(
        {
            "trade_date": ["2026-05-15", "2026-05-15", "2026-05-18"],
            "symbol": ["000001.XSHE", "600000.XSHG", "000001.XSHE"],
            "value": [1.0, 2.0, 4.0],
        }
    )

    out = normalize_factor_dataframe(raw)

    assert out.loc[pd.Timestamp("2026-05-18"), "000001.XSHE"] == 4.0
    assert list(out.columns) == ["000001.XSHE", "600000.XSHG"]


def test_resolve_factor_path_prefers_explicit_path_and_discovers_default():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        explicit = tmp_path / "custom.csv"
        explicit.write_text("trade_date,symbol,value\n2026-05-15,000001.XSHE,1\n", encoding="utf-8")

        assert resolve_factor_path(data_root=tmp_path, factor_path=explicit) == explicit

        factor_dir = tmp_path / "factor_dm_20d"
        factor_dir.mkdir()
        discovered = factor_dir / "factor_dm_20d.csv"
        discovered.write_text("trade_date,symbol,value\n2026-05-15,000001.XSHE,1\n", encoding="utf-8")

        assert resolve_factor_path(data_root=tmp_path, factor_name="dm_20d") == discovered


def test_load_wide_parquet_with_datetime_index(tmp_path):
    path = tmp_path / "factor.parquet"
    raw = pd.DataFrame(
        {
            "600000.XSHG": [2.0, 3.0],
            "000001.XSHE": [1.0, np.nan],
        },
        index=pd.DatetimeIndex(["2026-05-18", "2026-05-15"], name="trade_date"),
    )
    raw.to_parquet(path)

    out = load_factor_file(path)

    assert out.index.name == "trade_date"
    assert out.columns.name == "symbol"
    assert out.index.tolist() == [pd.Timestamp("2026-05-15"), pd.Timestamp("2026-05-18")]
    assert list(out.columns) == ["000001.XSHE", "600000.XSHG"]
    assert out.loc[pd.Timestamp("2026-05-15"), "000001.XSHE"] is np.nan or pd.isna(out.loc[pd.Timestamp("2026-05-15"), "000001.XSHE"])


def test_load_wide_parquet_with_explicit_trade_date_column(tmp_path):
    path = tmp_path / "factor.parquet"
    raw = pd.DataFrame(
        {
            "trade_date": ["2026-05-18", "2026-05-15"],
            "600000.XSHG": [3.0, 2.0],
            "000001.XSHE": [np.nan, 1.0],
        }
    )
    raw.to_parquet(path, index=False)

    out = load_factor_file(path)

    assert out.index.tolist() == [pd.Timestamp("2026-05-15"), pd.Timestamp("2026-05-18")]
    assert list(out.columns) == ["000001.XSHE", "600000.XSHG"]
    assert "trade_date" not in out.columns


def test_load_long_parquet_to_wide(tmp_path):
    path = tmp_path / "factor.parquet"
    raw = pd.DataFrame(
        {
            "date": ["2026-05-15", "2026-05-15", "2026-05-18"],
            "asset": ["600000.XSHG", "000001.XSHE", "000001.XSHE"],
            "factor": [2.0, 1.0, 4.0],
        }
    )
    raw.to_parquet(path, index=False)

    out = load_factor_file(path)

    assert list(out.columns) == ["000001.XSHE", "600000.XSHG"]
    assert out.loc[pd.Timestamp("2026-05-15"), "000001.XSHE"] == 1.0
    assert pd.isna(out.loc[pd.Timestamp("2026-05-18"), "600000.XSHG"])


def test_load_long_parquet_with_explicit_value_column(tmp_path):
    path = tmp_path / "factor.parquet"
    raw = pd.DataFrame(
        {
            "trade_date": ["2026-05-15", "2026-05-15"],
            "symbol": ["000001.XSHE", "600000.XSHG"],
            "custom_alpha": [1.5, 2.5],
        }
    )
    raw.to_parquet(path, index=False)

    out = load_factor_file(path, value_column="custom_alpha")

    assert out.loc[pd.Timestamp("2026-05-15"), "000001.XSHE"] == 1.5
    assert out.loc[pd.Timestamp("2026-05-15"), "600000.XSHG"] == 2.5


def test_load_partitioned_parquet_directory(tmp_path):
    factor_dir = tmp_path / "factor_order_imbalance_v1.parquet"
    factor_dir.mkdir()
    pd.DataFrame(
        {"trade_date": ["2024-01-02"], "000001.XSHE": [1.0], "600000.XSHG": [2.0]}
    ).to_parquet(factor_dir / "2024.parquet", index=False)
    pd.DataFrame(
        {"trade_date": ["2025-01-02"], "000001.XSHE": [3.0], "600000.XSHG": [4.0]}
    ).to_parquet(factor_dir / "2025.parquet", index=False)

    out = load_factor_file(factor_dir)

    assert out.index.tolist() == [pd.Timestamp("2024-01-02"), pd.Timestamp("2025-01-02")]
    assert out.loc[pd.Timestamp("2025-01-02"), "600000.XSHG"] == 4.0


def test_partitioned_parquet_uses_symbol_union(tmp_path):
    factor_dir = tmp_path / "factor_order_imbalance_v1.parquet"
    factor_dir.mkdir()
    pd.DataFrame({"trade_date": ["2024-01-02"], "000001.XSHE": [1.0]}).to_parquet(
        factor_dir / "2024.parquet", index=False
    )
    pd.DataFrame({"trade_date": ["2025-01-02"], "600000.XSHG": [4.0]}).to_parquet(
        factor_dir / "2025.parquet", index=False
    )

    out = load_factor_file(factor_dir)

    assert list(out.columns) == ["000001.XSHE", "600000.XSHG"]
    assert pd.isna(out.loc[pd.Timestamp("2024-01-02"), "600000.XSHG"])
    assert pd.isna(out.loc[pd.Timestamp("2025-01-02"), "000001.XSHE"])


def test_partitioned_parquet_ignores_non_parquet_files(tmp_path):
    factor_dir = tmp_path / "factor_order_imbalance_v1.parquet"
    factor_dir.mkdir()
    (factor_dir / "manifest.json").write_text('{"factor": "order_imbalance"}', encoding="utf-8")
    (factor_dir / "notes.txt").write_text("ignore", encoding="utf-8")
    pd.DataFrame({"trade_date": ["2024-01-02"], "000001.XSHE": [1.0]}).to_parquet(
        factor_dir / "2024.parquet", index=False
    )

    out = load_factor_file(factor_dir)

    assert out.shape == (1, 1)
    assert out.loc[pd.Timestamp("2024-01-02"), "000001.XSHE"] == 1.0


def test_empty_partitioned_parquet_directory_errors(tmp_path):
    factor_dir = tmp_path / "factor_order_imbalance_v1.parquet"
    factor_dir.mkdir()
    (factor_dir / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="no parquet files"):
        load_factor_file(factor_dir)


def test_partitioned_parquet_duplicate_trade_date_errors(tmp_path):
    factor_dir = tmp_path / "factor_order_imbalance_v1.parquet"
    factor_dir.mkdir()
    pd.DataFrame({"trade_date": ["2024-01-02"], "000001.XSHE": [1.0]}).to_parquet(
        factor_dir / "2024.parquet", index=False
    )
    pd.DataFrame({"trade_date": ["2024-01-02"], "600000.XSHG": [2.0]}).to_parquet(
        factor_dir / "2025.parquet", index=False
    )

    with pytest.raises(ValueError, match="duplicate trade_date across files"):
        load_factor_file(factor_dir)


def test_long_factor_duplicate_trade_date_symbol_errors(tmp_path):
    path = tmp_path / "factor.parquet"
    raw = pd.DataFrame(
        {
            "trade_date": ["2026-05-15", "2026-05-15"],
            "symbol": ["000001.XSHE", "000001.XSHE"],
            "value": [1.0, 2.0],
        }
    )
    raw.to_parquet(path, index=False)

    with pytest.raises(ValueError, match="duplicate trade_date \\+ symbol"):
        load_factor_file(path)


def test_parquet_preserves_nan_and_inf_values(tmp_path):
    path = tmp_path / "factor.parquet"
    raw = pd.DataFrame(
        {
            "trade_date": ["2026-05-15", "2026-05-18"],
            "000001.XSHE": [np.nan, np.inf],
            "600000.XSHG": [-np.inf, 0.0],
        }
    )
    raw.to_parquet(path, index=False)

    out = load_factor_file(path)

    assert pd.isna(out.loc[pd.Timestamp("2026-05-15"), "000001.XSHE"])
    assert np.isposinf(out.loc[pd.Timestamp("2026-05-18"), "000001.XSHE"])
    assert np.isneginf(out.loc[pd.Timestamp("2026-05-15"), "600000.XSHG"])


def test_factor_numeric_index_errors():
    raw = pd.DataFrame({"000001.XSHE": [1.0, 2.0]})

    with pytest.raises(ValueError, match="numeric indexes"):
        normalize_factor_dataframe(raw)


def test_factor_intraday_timestamp_errors():
    raw = pd.DataFrame(
        {"000001.XSHE": [1.0]},
        index=pd.DatetimeIndex(["2026-05-15 09:30:00"], name="trade_date"),
    )

    with pytest.raises(ValueError, match="intraday"):
        normalize_factor_dataframe(raw)


def test_factor_non_numeric_values_error():
    raw = pd.DataFrame(
        {
            "trade_date": ["2026-05-15"],
            "000001.XSHE": ["bad-value"],
        }
    )

    with pytest.raises(ValueError, match="must be numeric"):
        normalize_factor_dataframe(raw)


def test_wide_factor_numeric_validation_does_not_fragment_dataframe():
    dates = pd.DatetimeIndex(["2026-05-15", "2026-05-18"], name="trade_date")
    columns = [f"S{i:04d}" for i in range(256)]
    raw = pd.DataFrame(
        np.arange(len(dates) * len(columns), dtype="float64").reshape(len(dates), len(columns)),
        index=dates,
        columns=columns,
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", pd.errors.PerformanceWarning)
        out = normalize_factor_dataframe(raw)

    pd.testing.assert_frame_equal(out, raw.rename_axis(columns="symbol"))


def test_factor_symbol_duplicates_after_string_conversion_error():
    raw = pd.DataFrame(
        [[1.0, 2.0]],
        index=pd.DatetimeIndex(["2026-05-15"], name="trade_date"),
        columns=pd.Index([1, "1"]),
    )

    with pytest.raises(ValueError, match="duplicates after string conversion"):
        normalize_factor_dataframe(raw)


def test_resolve_factor_path_discovers_parquet_file(tmp_path):
    factor_dir = tmp_path / "factor_dm_20d"
    factor_dir.mkdir()
    discovered = factor_dir / "factor_dm_20d.parquet"
    discovered.write_bytes(b"")

    assert resolve_factor_path(data_root=tmp_path, factor_name="dm_20d") == discovered


def test_resolve_factor_path_prefers_parquet_over_h5_and_csv(tmp_path):
    factor_dir = tmp_path / "factor_dm_20d"
    factor_dir.mkdir()
    parquet_path = factor_dir / "factor_dm_20d.parquet"
    h5_path = factor_dir / "factor_dm_20d.h5"
    csv_path = factor_dir / "factor_dm_20d.csv"
    parquet_path.mkdir()
    h5_path.write_bytes(b"")
    csv_path.write_text("trade_date,symbol,value\n2026-05-15,000001.XSHE,1\n", encoding="utf-8")

    assert resolve_factor_path(data_root=tmp_path, factor_name="dm_20d") == parquet_path
