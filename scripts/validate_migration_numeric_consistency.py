"""使用同一组合成数据验证源 2.4.0 与 Platform 1.0.0 的数值一致性。"""

from __future__ import annotations

import argparse
import hashlib
import os
import pickle
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd


REQUIRED_TABLES = {
    "all/cumulative_ic/daily_ic_spearman",
    "all/cumulative_ic/daily_ic_pearson",
    "all/cumulative_ic/ic_stats_spearman",
    "all/cumulative_ic/cumulative_ic_spearman",
    "all/yearly_ic/yearly_ic_stats_spearman",
    "all/group_return/daily_group_returns",
    "all/group_return/group_cumulative_returns_1d",
    "all/long_short/daily_long_short_returns",
    "all/performance_metrics/performance_metrics",
    "all/data_quality/data_quality",
    "all/group_turnover/daily_group_membership_change",
    "all/factor_style_exposure/factor_style_exposure_corr_spearman",
    "all/style_neutralized_ic/style_neutralized_ic_spearman",
    "all/style_industry_neutralized_ic/style_industry_neutralized_ic_spearman",
}


def _sha256_frame(frame: pd.DataFrame) -> str:
    """对合成因子宽表生成稳定摘要，用于确认运行前后未被修改。"""
    payload = pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes()
    payload += "\x1f".join(map(str, frame.columns)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _build_inputs():
    """构造覆盖 IC、分组、风格、行业和中性化诊断的确定性数据。"""
    from factor_backtest_platform.market_data import MarketDataBundle

    dates = pd.bdate_range("2024-01-02", periods=90, name="trade_date")
    symbols = [f"S{i:03d}" for i in range(100)]
    factor = pd.DataFrame(
        [
            [float(((i * 17 + day * 11) % 101) - 50) + day * 0.001 for i in range(len(symbols))]
            for day in range(len(dates))
        ],
        index=dates,
        columns=symbols,
    )
    open_price = pd.DataFrame(
        [
            [
                20.0
                + i * 0.03
                + day * (0.002 + (i % 13) * 0.0003)
                + ((day + i) % 7) * 0.0002
                for i in range(len(symbols))
            ]
            for day in range(len(dates))
        ],
        index=dates,
        columns=symbols,
    )
    return factor, MarketDataBundle(open_price=open_price)


def _write_risk_input(path: Path, dates: pd.DatetimeIndex, symbols: list[str]) -> None:
    """写入每次工作进程都完全相同的风格和行业暴露输入。"""
    from factor_backtest_platform.risk_exposure import DEFAULT_STYLE_COLUMNS

    rows = []
    for day, date in enumerate(dates):
        for i, symbol in enumerate(symbols):
            row = {"date": date, "symbol": symbol}
            for style_index, style in enumerate(DEFAULT_STYLE_COLUMNS):
                row[style] = float(((i * (style_index + 3) + day) % 37) - 18)
            row["银行"] = 1 if i < 50 else 0
            row["计算机"] = 1 if i >= 50 else 0
            rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _worker(output_dir: Path) -> None:
    """在当前 PYTHONPATH 所指向的项目中运行一次并保存内存结果快照。"""
    import factor_backtest_platform
    from factor_backtest_platform.config import BacktestConfig, DataSourceConfig, PathConfig
    from factor_backtest_platform.runner import run_factor_backtest

    output_dir.mkdir(parents=True, exist_ok=True)
    factor, market = _build_inputs()
    factor_hash_before = _sha256_frame(factor)
    risk_path = output_dir / "inputs" / "risk_exposure.csv"
    _write_risk_input(risk_path, factor.index, list(factor.columns))
    config = BacktestConfig(
        paths=PathConfig(
            project_dir=Path(factor_backtest_platform.__file__).resolve().parents[1],
            data_root=output_dir,
            pool_dir=output_dir / "pool",
            risk_exposure_path=risk_path,
        ),
        data_sources=DataSourceConfig(risk_exposure_source="csv"),
        output_root=output_dir / "results",
        factor_name="migration_numeric_consistency",
        selected_pools=["all"],
        horizons=[1, 5, 10, 20],
        tradability_filter=False,
        ic_methods=["spearman", "pearson"],
        min_ic_stocks=30,
        min_group_stocks=10,
        min_industry_ic_stocks=10,
        enabled_sections="all",
        output_layout="latest_runs",
        artifact_level="none",
        export_static_report=False,
        render_plots=False,
        parquet_compression="zstd",
        verbose=False,
    )
    result = run_factor_backtest(
        factor_df=factor,
        market_data=market,
        config=config,
        log_fn=lambda *_: None,
    )
    tables = {}
    for pool, section_results in result.section_status.items():
        for section_name, section_result in section_results.items():
            for table_name, table in section_result.tables.items():
                if isinstance(table, (pd.DataFrame, pd.Series)):
                    tables[f"{pool}/{section_name}/{table_name}"] = table

    missing = sorted(REQUIRED_TABLES - set(tables))
    if missing:
        raise AssertionError(f"数值快照缺少必验表：{missing}")
    snapshot = {
        "package_version": factor_backtest_platform.__version__,
        "package_file": str(Path(factor_backtest_platform.__file__).resolve()),
        "factor_hash_before": factor_hash_before,
        "factor_hash_after": _sha256_frame(factor),
        "tables": tables,
    }
    with (output_dir / "snapshot.pkl").open("wb") as stream:
        pickle.dump(snapshot, stream, protocol=pickle.HIGHEST_PROTOCOL)


def _run_worker(project_root: Path, output_dir: Path) -> dict:
    """用同一解释器、不同项目优先路径启动隔离工作进程。"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--output-dir",
        str(output_dir),
    ]
    subprocess.run(command, check=True, env=environment)
    with (output_dir / "snapshot.pkl").open("rb") as stream:
        return pickle.load(stream)


def _compare_snapshots(source: dict, target: dict, source_root: Path, target_root: Path) -> None:
    """比较全部内存表，并只放行项目版本和导入路径差异。"""
    source_file = Path(source["package_file"])
    target_file = Path(target["package_file"])
    if source_root.resolve() not in source_file.parents:
        raise AssertionError(f"源导入路径错误：{source_file}")
    if target_root.resolve() not in target_file.parents:
        raise AssertionError(f"Platform 导入路径错误：{target_file}")
    if source["factor_hash_before"] != source["factor_hash_after"]:
        raise AssertionError("源运行修改了因子输入")
    if target["factor_hash_before"] != target["factor_hash_after"]:
        raise AssertionError("Platform 运行修改了因子输入")
    if source["factor_hash_before"] != target["factor_hash_before"]:
        raise AssertionError("两次运行使用的合成因子不同")

    source_tables = source["tables"]
    target_tables = target["tables"]
    if set(source_tables) != set(target_tables):
        only_source = sorted(set(source_tables) - set(target_tables))
        only_target = sorted(set(target_tables) - set(source_tables))
        raise AssertionError(f"结果表集合不同；仅源={only_source}；仅 Platform={only_target}")
    for name in sorted(source_tables):
        left = source_tables[name]
        right = target_tables[name]
        if isinstance(left, pd.DataFrame):
            pd.testing.assert_frame_equal(left, right, check_exact=True, obj=name)
        else:
            pd.testing.assert_series_equal(left, right, check_exact=True, obj=name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--target-root", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    if args.worker:
        if args.output_dir is None:
            parser.error("worker 模式必须提供 --output-dir")
        _worker(args.output_dir)
        return
    if args.source_root is None or args.target_root is None:
        parser.error("必须提供 --source-root 和 --target-root")

    with tempfile.TemporaryDirectory(prefix="factor_platform_numeric_", dir=args.target_root) as temporary:
        temporary_root = Path(temporary)
        source = _run_worker(args.source_root, temporary_root / "source")
        target = _run_worker(args.target_root, temporary_root / "target")
        _compare_snapshots(source, target, args.source_root, args.target_root)
        print(
            "数值一致性通过："
            f"源版本={source['package_version']}，Platform版本={target['package_version']}，"
            f"比较表数={len(source['tables'])}，全部精确一致。"
        )


if __name__ == "__main__":
    main()
