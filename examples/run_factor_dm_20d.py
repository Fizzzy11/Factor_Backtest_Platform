from factor_backtest.clickhouse_adapter import load_market_data_from_clickhouse
from factor_backtest.config import BacktestConfig, CompanyDiagnosticsConfig, DataSourceConfig, HandoffConfig, PathConfig
from factor_backtest.factor_loader import load_factor_file, resolve_factor_path
from factor_backtest.runner import run_factor_backtest


def main() -> None:
    # ===== 1. 因子文件参数 =====
    # 因子文件可以是单个 .parquet 文件、后缀为 .parquet 的分区目录、
    # 原有 H5 文件或 CSV 文件。factor_path=None 时按默认优先级自动发现：
    # /data/zhangyuan/factor_dm_20d/factor_dm_20d.parquet
    # /data/zhangyuan/factor_dm_20d/factor_dm_20d.h5
    # /data/zhangyuan/factor_dm_20d/factor_dm_20d.csv
    data_root = "/data/zhangyuan"
    factor_name_for_path = "dm_20d"
    factor_display_name = "factor_dm_20d"
    # 也可以显式指定：
    # factor_path = "/data/zhangyuan/factor_dm_20d/factor_dm_20d.parquet"
    # factor_path = "/data/zhangyuan/factor_dm_20d/factor_dm_20d.h5"
    # factor_path = "/data/zhangyuan/factor_dm_20d/factor_dm_20d.csv"
    factor_path = None
    # 只在读取 H5 时生效；CSV 和 parquet 会忽略该参数。
    factor_h5_key = None

    # ===== 2. 行情数据参数 =====
    # 结束日期为右开区间。最后一个因子日之后至少保留 max(horizons) + 1 个
    # 未来交易日开盘价，供下一交易日开盘建仓和 open-to-open 收益计算使用。
    market_start_date = "2020-01-01"
    market_end_date = "2026-05-02"

    # ===== 3. 股票池参数 =====
    # 示例默认运行全市场、沪深300、中证1000和中证2000。
    # 其他示例：["all"]、["gz1000_pool", "gz2000_pool"]。
    selected_pools = ["all", "hs300_pool", "zz1000_pool", "zz2000_pool"]

    # ===== 4. 回测核心参数 =====
    horizons = [1, 5, 10, 20]
    min_listed_days = 120
    tradability_filter = True
    # IC 方法可选 ["spearman"]、["pearson"] 或同时启用两者。
    ic_methods = ["spearman", "pearson"]
    min_ic_stocks = 30
    # 供分组收益、分组暴露诊断和分组成员变化率使用。
    min_group_stocks = 10
    analysis_windows = [120, 250, 750]
    # 年度 IC 每个“年份 × 收益标签”至少需要的有效 IC 天数。
    yearly_ic_min_days = 60
    # True 保留回测首尾可能不完整的年份，并在结果中标识完整性。
    yearly_ic_include_partial_year = True
    group_return_windows = {"6m": 120, "1y": 250, "3y": 750, "5y": 1250}

    # ===== 5. 因子预处理参数 =====
    # 默认保留原始因子排序，不做缩尾或标准化。
    winsorize_factor = False
    standardize_factor = False

    # ===== 6. 报告模块参数 =====
    # "all" 表示运行全部内置模块。
    # 子集示例：["data_quality", "ic_overview", "cumulative_ic", "group_turnover"]。
    enabled_sections = "all"
    verbose = True

    # ===== 7. 风险暴露和行业数据 =====
    # 默认读取配置的本地 parquet/csv 文件；文件不可用时设为 "none"。
    # /data/zhangyuan/risk&industry/CNE5_Industry_daily.parquet
    risk_exposure_source = "csv"
    risk_exposure_path = "risk&industry/CNE5_Industry_daily.parquet"
    # 供行业内分组收益使用。
    min_industry_ic_stocks = 10

    # ===== 8. 输出参数 =====
    # 推荐调用脚本路径：
    # /app/workspace/zhangyuan/Factor_Backtest_Platform_Result/factor_dm_20d/run_factor_dm_20d.py
    # 输出路径：
    # /data/zhangyuan/Factor_Backtest_Platform_Result/factor_dm_20d/latest.json
    # /data/zhangyuan/Factor_Backtest_Platform_Result/factor_dm_20d/runs/<run_id>/
    output_root = "/data/zhangyuan/Factor_Backtest_Platform_Result"
    output_layout = "latest_runs"
    artifact_level = "none"
    # Platform 默认使用数据模式，不生成 PNG 和 HTML；如需静态报告，
    # 可把两个开关显式设为 True，或在运行后调用
    # render_factor_backtest_report(result.run_dir) 按需重建。
    export_static_report = False
    render_plots = False
    parquet_compression = "zstd"

    # ===== 9. 平台 handoff 参数 =====
    # 默认关闭。仅在需要导出平台验收目录
    # docs/handoffs/factor_backtest_platform/sample_latest/ 时打开。
    # 第一轮 handoff 可采用以下精简配置：
    # selected_pools = ["all"]
    # ic_methods = ["spearman"]
    # risk_exposure_source = "none"
    # enabled_sections = [
    #     "data_quality",
    #     "cumulative_ic",
    #     "group_return",
    #     "long_short",
    #     "group_turnover",
    #     "performance_metrics",
    # ]
    handoff_enabled = False
    handoff_factor_direction = "high_is_long"
    # 为 None 时，handoff 的 data_asof 使用 factor_df.index 最大日期。
    handoff_data_asof = None

    # ===== 10. 公司 diagnostics 参数 =====
    # 正式公司 diagnostics 不同于第一轮 handoff 的 pending 文件。
    # 默认关闭。打开后，成功计算的 JSON 只写入对应不可变 run：
    # /data/zhangyuan/Factor_Backtest_Platform_Result/factor_dm_20d/runs/<run_time>/diagnostics/
    # latest.json 会指向该 run，不再复制第二份 diagnostics。
    #
    # book 输入为长表：
    # date/trade_date, symbol/stock_id, factor_id, value/factor_value
    # regime 输入为布尔宽表：
    # date/trade_date, bull, bear, high_vol, low_vol
    company_diagnostics_enabled = False

    company_diagnostics_production_book_source = "file"
    company_diagnostics_peer_book_source = "file"
    company_diagnostics_regime_source = "file"
    production_book_path = None
    peer_book_path = None
    factor_meta_path = None
    factor_ls_pnl_path = None
    regime_path = None

    baseline_suite_id = None
    baseline_book_version = None
    peer_book_version = None
    peer_pool_id = "research+promoted"

    hypothesis_direction = "unknown"  # "high_is_long", "high_is_short", or "unknown"
    idea_id = None
    idea_version = 1
    regime_labels = ["bull", "bear", "high_vol", "low_vol"]
    neutralization_layers = ["production_book"]

    spanning_topk = 20
    spanning_rounds = 3
    top_spanning_factors = 5
    topk_overlap_k = 50
    min_similarity_stocks = 30

    resolved_factor_path = factor_path or resolve_factor_path(
        data_root=data_root,
        factor_name=factor_name_for_path,
    )
    if verbose:
        print(f"[v2] loading factor: {resolved_factor_path}")
    factor_df = load_factor_file(
        resolved_factor_path,
        h5_key=factor_h5_key,
    )
    if verbose:
        print(f"[v2] factor loaded: dates={len(factor_df.index):,}, symbols={len(factor_df.columns):,}")

    market_data = load_market_data_from_clickhouse(
        start_date=market_start_date,
        end_date=market_end_date,
        verbose=verbose,
    )

    if company_diagnostics_enabled:
        diagnostics_config = CompanyDiagnosticsConfig(
            enabled=True,
            production_book_source=company_diagnostics_production_book_source,
            peer_book_source=company_diagnostics_peer_book_source,
            regime_source=company_diagnostics_regime_source,
            production_book_path=production_book_path,
            peer_book_path=peer_book_path,
            factor_meta_path=factor_meta_path,
            factor_ls_pnl_path=factor_ls_pnl_path,
            regime_path=regime_path,
            baseline_suite_id=baseline_suite_id,
            baseline_book_version=baseline_book_version,
            peer_book_version=peer_book_version,
            peer_pool_id=peer_pool_id,
            hypothesis_direction=hypothesis_direction,
            idea_id=idea_id,
            version=idea_version,
            regime_labels=regime_labels,
            neutralization_layers=neutralization_layers,
            spanning_topk=spanning_topk,
            spanning_rounds=spanning_rounds,
            top_spanning_factors=top_spanning_factors,
            topk_overlap_k=topk_overlap_k,
            min_similarity_stocks=min_similarity_stocks,
        )
    else:
        diagnostics_config = CompanyDiagnosticsConfig(enabled=False)

    cfg = BacktestConfig(
        paths=PathConfig(data_root=data_root, risk_exposure_path=risk_exposure_path),
        data_sources=DataSourceConfig(risk_exposure_source=risk_exposure_source),
        factor_name=factor_display_name,
        output_root=output_root,
        selected_pools=selected_pools,
        horizons=horizons,
        min_listed_days=min_listed_days,
        tradability_filter=tradability_filter,
        ic_methods=ic_methods,
        min_ic_stocks=min_ic_stocks,
        min_group_stocks=min_group_stocks,
        min_industry_ic_stocks=min_industry_ic_stocks,
        analysis_windows=analysis_windows,
        yearly_ic_min_days=yearly_ic_min_days,
        yearly_ic_include_partial_year=yearly_ic_include_partial_year,
        group_return_windows=group_return_windows,
        winsorize_factor=winsorize_factor,
        standardize_factor=standardize_factor,
        enabled_sections=enabled_sections,
        output_layout=output_layout,
        artifact_level=artifact_level,
        export_static_report=export_static_report,
        render_plots=render_plots,
        parquet_compression=parquet_compression,
        handoff=HandoffConfig(
            enabled=handoff_enabled,
            factor_direction=handoff_factor_direction,
            data_asof=handoff_data_asof,
        ),
        diagnostics=diagnostics_config,
        verbose=verbose,
    )

    result = run_factor_backtest(
        factor_df=factor_df,
        market_data=market_data,
        config=cfg,
    )
    print(f"单次结果目录：{result.run_dir}")
    print(f"latest 索引：{result.latest_index_path}")
    if result.handoff_dir is not None:
        print(result.handoff_dir)


if __name__ == "__main__":
    main()
