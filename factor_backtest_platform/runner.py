from __future__ import annotations

from dataclasses import replace
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from factor_backtest_platform.analytics import (
    compute_daily_group_returns,
    compute_daily_ic,
    compute_ic_stats,
    compute_long_short_returns,
    compute_performance_metrics,
    compute_quality_metrics,
    validate_ic_methods,
)
from factor_backtest_platform.config import BacktestConfig
from factor_backtest_platform.company_diagnostics import export_company_diagnostics
from factor_backtest_platform.filters import compute_tradability_mask
from factor_backtest_platform.handoff import export_factor_backtest_platform_handoff
from factor_backtest_platform.io import ensure_dir, read_json, read_table, write_json, write_table
from factor_backtest_platform.io import format_table_float
from factor_backtest_platform.market_data import MarketDataBundle
from factor_backtest_platform.pools import resolve_selected_pools
from factor_backtest_platform.risk_exposure import resolve_risk_exposure
from factor_backtest_platform.returns import build_return_specs, return_label, return_slug, sort_return_labels
from factor_backtest_platform.result_store import (
    LATEST_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    PreparedRunPaths,
    prepare_run_paths,
    publish_run,
    write_manifest,
    write_result_data,
)
from factor_backtest_platform.sections import DEFAULT_SECTIONS, ReportSection, SectionResult
from factor_backtest_platform.version import __version__


RISK_EXPOSURE_SECTIONS = {
    "factor_style_exposure",
    "style_neutralized_ic",
    "style_industry_neutralized_ic",
    "group_exposure_diagnostics",
    "within_industry_group_return",
}
RISK_EXPOSURE_PANEL_SECTIONS = {
    "factor_style_exposure",
    "style_neutralized_ic",
    "style_industry_neutralized_ic",
    "group_exposure_diagnostics",
    "within_industry_group_return",
}
CORE_SECTIONS = {"data_quality", "cumulative_ic", "group_return"}


@dataclass
class BacktestRunResult:
    run_dir: Path
    latest_dir: Path | None
    latest_index_path: Path | None
    section_status: dict[str, dict[str, SectionResult]]
    warnings: list[str]
    handoff_dir: Path | None = None


def run_factor_backtest(
    *,
    factor_df: pd.DataFrame,
    market_data: MarketDataBundle,
    config: BacktestConfig | None = None,
    factor_name: str | None = None,
    external_returns: dict | None = None,
    sections: list[ReportSection] | None = None,
    log_fn=print,
) -> BacktestRunResult:
    cfg = config or BacktestConfig()
    ic_methods = validate_ic_methods(cfg.ic_methods)
    resolved_factor_name = factor_name or cfg.factor_name or "unnamed_factor"
    run_start = perf_counter()
    run_paths = _prepare_run_directories(cfg, resolved_factor_name)
    run_dir = run_paths.working_dir
    latest_dir = run_paths.final_dir if run_paths.latest_index_path is not None else None
    _log(cfg, log_fn, f"[v2] starting backtest: {resolved_factor_name}")
    _log(cfg, log_fn, f"[v2] staging directory: {run_dir}")
    section_list = sections if sections is not None else _resolve_sections(cfg)
    risk_exposure = resolve_risk_exposure(cfg) if _section_list_needs_risk_exposure(section_list) else None
    pools = resolve_selected_pools(
        cfg.selected_pools,
        pool_source=cfg.data_sources.pool_source,
        pool_dir=cfg.paths.pool_dir,
    )
    status: dict[str, dict[str, SectionResult]] = {}
    core_tables: dict[str, dict[str, pd.DataFrame]] = {}
    diagnostic_contexts: dict[str, dict] = {}
    run_warnings: list[str] = list(risk_exposure.warnings) if risk_exposure is not None else []
    timings: dict = {"pools": {}}

    standardize_start = perf_counter()
    factor = _standardize_factor(factor_df)
    timings["standardize_factor_seconds"] = _elapsed(standardize_start)
    _log(cfg, log_fn, f"[v2] computing test returns: horizons={cfg.horizons}")
    returns_start = perf_counter()
    return_specs = build_return_specs(
        open_price=market_data.open_price,
        horizons=cfg.horizons,
        external_returns=external_returns,
    )
    future_returns_all = {label: spec.data for label, spec in return_specs.items()}
    return_horizon_days = {label: spec.horizon_days for label, spec in return_specs.items()}
    timings["build_returns_seconds"] = _elapsed(returns_start)

    for pool_name, pool_mask in pools.items():
        pool_start = perf_counter()
        pool_timing = {
            "stages": {},
            "sections": {},
        }
        timings["pools"][pool_name] = pool_timing
        _log(cfg, log_fn, f"[v2] pool {pool_name}: preparing data")
        prepare_start = perf_counter()
        plots_dir = run_dir / "plots" / pool_name

        pool_factor, pool_bool = _apply_pool(factor, market_data.open_price, pool_mask)
        run_warnings.extend(_pool_coverage_warnings(pool_name, pool_mask, factor.index))
        base_mask = np.isfinite(pool_factor)

        if cfg.tradability_filter and not _has_tradability_data(market_data):
            missing = _missing_tradability_fields(market_data)
            raise ValueError(
                "tradability_filter=True requires market data fields: "
                + ", ".join(missing)
                + ". Set tradability_filter=False to skip entry-day tradability filtering."
            )

        if cfg.tradability_filter:
            _log(cfg, log_fn, f"[v2] pool {pool_name}: applying tradability filter")
            tradability = compute_tradability_mask(
                open_price=_next_trading_day_frame(market_data.open_price, pool_factor),
                high_price=_next_trading_day_frame(market_data.high_price, pool_factor),
                low_price=_next_trading_day_frame(market_data.low_price, pool_factor),
                limit_up_price=_next_trading_day_frame(market_data.limit_up_price, pool_factor),
                limit_down_price=_next_trading_day_frame(market_data.limit_down_price, pool_factor),
                is_st=_next_trading_day_frame(market_data.is_st, pool_factor),
                is_suspended=_next_trading_day_frame(market_data.is_suspended, pool_factor),
                listed_days=_next_trading_day_frame(market_data.listed_days, pool_factor),
                min_listed_days=cfg.min_listed_days,
            )
            valid_mask = base_mask & tradability.mask
            filter_summary = tradability.summary
        else:
            _log(cfg, log_fn, f"[v2] pool {pool_name}: tradability filter disabled")
            valid_mask = base_mask
            filter_summary = pd.DataFrame(index=pool_factor.index)
            filter_summary["after_filter_count"] = valid_mask.sum(axis=1)

        filtered_factor = pool_factor.where(valid_mask)
        future_returns = {h: r.reindex_like(filtered_factor) for h, r in future_returns_all.items()}
        pool_timing["stages"]["prepare_data_seconds"] = _elapsed(prepare_start)

        core_start = perf_counter()
        _log(cfg, log_fn, f"[v2] pool {pool_name}: computing IC: methods={ic_methods}")
        daily_ic_by_method = compute_daily_ic(
            filtered_factor,
            future_returns,
            min_stocks=cfg.min_ic_stocks,
            methods=ic_methods,
        )
        daily_ic = _compat_daily_ic(daily_ic_by_method)
        _log(cfg, log_fn, f"[v2] pool {pool_name}: computing group returns")
        daily_group_returns = compute_daily_group_returns(
            filtered_factor,
            future_returns,
            n_groups=10,
            min_stocks=cfg.min_group_stocks,
        )
        _log(cfg, log_fn, f"[v2] pool {pool_name}: computing long-short returns and data quality")
        daily_long_short = compute_long_short_returns(daily_group_returns)
        data_quality = compute_quality_metrics(pool_factor, pool_bool, valid_mask)
        core_tables[pool_name] = {
            "data_quality": data_quality,
            "daily_group_returns": daily_group_returns,
            **{
                f"daily_ic_{method}": method_table
                for method, method_table in daily_ic_by_method.items()
            },
        }
        risk_exposure_panel = (
            risk_exposure.to_panel(filtered_factor.index, filtered_factor.columns)
            if risk_exposure is not None and _section_list_needs_risk_exposure_panel(section_list)
            else None
        )
        pool_timing["stages"]["core_calculations_seconds"] = _elapsed(core_start)

        context = {
            "pool_name": pool_name,
            "factor": filtered_factor,
            "future_returns": future_returns,
            "daily_ic": daily_ic,
            "daily_ic_by_method": daily_ic_by_method,
            "daily_group_returns": daily_group_returns,
            "daily_long_short_returns": daily_long_short,
            "data_quality": data_quality,
            "filter_summary": filter_summary,
            "plots_dir": plots_dir,
            "plot_index": filtered_factor.index,
            "horizon_colors": cfg.horizon_colors,
            "group_return_windows": cfg.group_return_windows,
            "yearly_ic_min_days": cfg.yearly_ic_min_days,
            "yearly_ic_include_partial_year": cfg.yearly_ic_include_partial_year,
            "return_horizon_days": return_horizon_days,
            "risk_exposure": risk_exposure,
            "risk_exposure_panel": risk_exposure_panel,
            "ic_methods": ic_methods,
            "min_ic_stocks": cfg.min_ic_stocks,
            "min_group_stocks": cfg.min_group_stocks,
            "min_industry_ic_stocks": cfg.min_industry_ic_stocks,
            "render_plots": cfg.render_plots and cfg.export_static_report,
            "write_neutralized_factors": cfg.write_neutralized_factors,
        }
        diagnostic_contexts[pool_name] = {
            "factor": filtered_factor,
            "future_returns": future_returns,
            "daily_group_returns": daily_group_returns,
            "return_horizon_days": return_horizon_days,
        }

        artifacts_start = perf_counter()
        if cfg.artifact_level == "full":
            _log(cfg, log_fn, f"[v2] pool {pool_name}: writing artifacts")
            artifacts_dir = ensure_dir(run_dir / "artifacts" / pool_name)
            write_table(filtered_factor, artifacts_dir / "aligned_factor.parquet")
            write_table(valid_mask.astype(bool), artifacts_dir / "valid_mask.parquet")
            for horizon, returns in future_returns.items():
                write_table(returns, artifacts_dir / f"future_returns_{return_slug(horizon)}.parquet")
            write_table(daily_ic, artifacts_dir / "daily_ic.parquet")
            for method, method_ic in daily_ic_by_method.items():
                write_table(method_ic, artifacts_dir / f"daily_ic_{method}.parquet")
            write_table(daily_group_returns, artifacts_dir / "daily_group_returns.parquet")
            write_table(daily_long_short, artifacts_dir / "daily_long_short_returns.parquet")
            write_table(data_quality, artifacts_dir / "data_quality.parquet")
            write_table(filter_summary, artifacts_dir / "filter_summary.parquet")
        else:
            _log(cfg, log_fn, f"[v2] pool {pool_name}: skipping artifacts")
        pool_timing["stages"]["write_artifacts_seconds"] = _elapsed(artifacts_start)

        status[pool_name] = {}
        for section in section_list:
            _log(cfg, log_fn, f"[v2] pool {pool_name}: running section {section.name}")
            section_start = perf_counter()
            compute_seconds = 0.0
            render_seconds = 0.0
            write_seconds = 0.0
            try:
                compute_start = perf_counter()
                result = section.compute(context)
                compute_seconds = _elapsed(compute_start)
                render_start = perf_counter()
                result = section.render(context, result)
                render_seconds = _elapsed(render_start)
            except Exception as exc:
                if compute_seconds == 0.0:
                    compute_seconds = _elapsed(section_start)
                result = SectionResult(name=section.name, status="failed", error=str(exc))
            status[pool_name][section.name] = result
            if cfg.write_neutralized_factors:
                _write_neutralized_factor_artifacts(run_dir, pool_name, result)
            write_seconds = 0.0
            pool_timing["sections"][section.name] = {
                "compute_seconds": compute_seconds,
                "render_seconds": render_seconds,
                "write_seconds": write_seconds,
                "total_seconds": _elapsed(section_start),
            }
        pool_timing["total_seconds"] = _elapsed(pool_start)

    diagnostics_start = perf_counter()
    diagnostic_warnings = export_company_diagnostics(
        run_dir=run_dir,
        config=cfg,
        pool_contexts=diagnostic_contexts,
    )
    run_warnings.extend(diagnostic_warnings)
    timings["company_diagnostics_seconds"] = _elapsed(diagnostics_start)

    result_write_start = perf_counter()
    compression = None if cfg.parquet_compression == "none" else cfg.parquet_compression
    table_manifest = write_result_data(
        run_dir,
        status,
        compression=compression,
        core_tables=core_tables,
    )
    timings["write_result_data_seconds"] = _elapsed(result_write_start)
    created_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    run_meta = {
        "package_version": __version__,
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "framework_version": cfg.framework_version,
        "run_id": run_paths.run_id,
        "created_at": created_at,
        "factor_name": resolved_factor_name,
        "selected_pools": cfg.selected_pools,
        "horizons": cfg.horizons,
        "horizon_colors": cfg.horizon_colors,
        "return_labels": list(return_specs.keys()),
        "external_return_labels": [label for label, spec in return_specs.items() if spec.source == "external"],
        "return_horizon_days": return_horizon_days,
        "ic_methods": ic_methods,
        "tradability_filter": cfg.tradability_filter,
        "min_listed_days": cfg.min_listed_days,
        "min_ic_stocks": cfg.min_ic_stocks,
        "min_industry_ic_stocks": cfg.min_industry_ic_stocks,
        "min_group_stocks": cfg.min_group_stocks,
        "risk_exposure_source": cfg.data_sources.risk_exposure_source,
        "risk_exposure_path": str(cfg.paths.risk_exposure_path),
        "enabled_sections": cfg.enabled_sections,
        "group_return_windows": cfg.group_return_windows,
        "yearly_ic_min_days": cfg.yearly_ic_min_days,
        "yearly_ic_include_partial_year": cfg.yearly_ic_include_partial_year,
        "output_layout": cfg.output_layout,
        "artifact_level": cfg.artifact_level,
        "render_plots": cfg.render_plots,
        "export_static_report": cfg.export_static_report,
        "parquet_compression": cfg.parquet_compression,
        "write_neutralized_factors": cfg.write_neutralized_factors,
        "company_diagnostics_enabled": cfg.diagnostics.enabled,
    }
    timings["total_seconds"] = _elapsed(run_start)
    write_json(run_meta, run_dir / "run_meta.json")
    section_payload = _status_to_json(status)
    write_json({"warnings": run_warnings, "sections": section_payload, "timings": timings}, run_dir / "run_log.json")
    _validate_core_section_status(status)
    write_manifest(
        run_dir,
        {
            "package_version": __version__,
            "run_id": run_paths.run_id,
            "factor_name": resolved_factor_name,
            "status": "complete",
            "created_at": created_at,
            "selected_pools": list(status),
            "return_labels": list(return_specs.keys()),
            "return_horizon_days": return_horizon_days,
            "ic_methods": ic_methods,
            "date_ranges": {
                name: payload.get("date_range")
                for name, payload in table_manifest.items()
                if payload.get("date_range") is not None
            },
            "sections": section_payload,
            "tables": table_manifest,
        },
    )
    if cfg.export_static_report:
        _write_html_report(run_dir, status, meta=run_meta, warnings=run_warnings)
    handoff_dir = None
    if cfg.handoff.enabled:
        _log(cfg, log_fn, f"[v2] exporting handoff package: {cfg.handoff.target}")
        handoff_dir = export_factor_backtest_platform_handoff(
            run_dir=run_dir,
            config=cfg,
            factor_name=resolved_factor_name,
            factor_index=factor.index,
            section_status=status,
        )
    latest_payload = (
        {
            "schema_version": LATEST_SCHEMA_VERSION,
            "factor_name": resolved_factor_name,
            "run_id": run_paths.run_id,
            "relative_path": run_paths.final_dir.relative_to(run_paths.factor_root).as_posix(),
            "completed_at": created_at,
        }
        if run_paths.latest_index_path is not None
        else None
    )
    final_dir = publish_run(run_paths, latest_payload)
    _rebase_result_plot_paths(status, run_dir, final_dir)
    _log(cfg, log_fn, f"[v2] completed: {final_dir}")
    return BacktestRunResult(
        run_dir=final_dir,
        latest_dir=latest_dir,
        latest_index_path=run_paths.latest_index_path,
        section_status=status,
        warnings=run_warnings,
        handoff_dir=handoff_dir,
    )


def run_factor_backtest_minimal(
    *,
    factor_df: pd.DataFrame,
    market_data: MarketDataBundle,
    config: BacktestConfig | None = None,
    external_returns: dict | None = None,
) -> pd.DataFrame:
    cfg = config or BacktestConfig()
    ic_methods = validate_ic_methods(cfg.ic_methods)
    factor = _standardize_factor(factor_df)
    return_specs = build_return_specs(
        open_price=market_data.open_price,
        horizons=cfg.horizons,
        external_returns=external_returns,
    )
    future_returns_all = {label: spec.data for label, spec in return_specs.items()}
    return_horizon_days = {label: spec.horizon_days for label, spec in return_specs.items()}
    rows = []
    for pool_name, pool_mask in resolve_selected_pools(
        cfg.selected_pools,
        pool_source=cfg.data_sources.pool_source,
        pool_dir=cfg.paths.pool_dir,
    ).items():
        pool_factor, pool_bool = _apply_pool(factor, market_data.open_price, pool_mask)
        base_mask = np.isfinite(pool_factor)
        if cfg.tradability_filter:
            if not _has_tradability_data(market_data):
                missing = _missing_tradability_fields(market_data)
                raise ValueError("tradability_filter=True requires market data fields: " + ", ".join(missing))
            tradability = compute_tradability_mask(
                open_price=_next_trading_day_frame(market_data.open_price, pool_factor),
                high_price=_next_trading_day_frame(market_data.high_price, pool_factor),
                low_price=_next_trading_day_frame(market_data.low_price, pool_factor),
                limit_up_price=_next_trading_day_frame(market_data.limit_up_price, pool_factor),
                limit_down_price=_next_trading_day_frame(market_data.limit_down_price, pool_factor),
                is_st=_next_trading_day_frame(market_data.is_st, pool_factor),
                is_suspended=_next_trading_day_frame(market_data.is_suspended, pool_factor),
                listed_days=_next_trading_day_frame(market_data.listed_days, pool_factor),
                min_listed_days=cfg.min_listed_days,
            )
            valid_mask = base_mask & tradability.mask
        else:
            valid_mask = base_mask
        filtered_factor = pool_factor.where(valid_mask)
        future_returns = {h: r.reindex_like(filtered_factor) for h, r in future_returns_all.items()}
        daily_ic_by_method = compute_daily_ic(
            filtered_factor,
            future_returns,
            min_stocks=cfg.min_ic_stocks,
            methods=ic_methods,
        )
        daily_ic = _compat_daily_ic(daily_ic_by_method)
        group_returns = compute_daily_group_returns(
            filtered_factor,
            future_returns,
            n_groups=10,
            min_stocks=cfg.min_group_stocks,
        )
        long_short = compute_long_short_returns(group_returns)
        quality = compute_quality_metrics(pool_factor, pool_bool, valid_mask)
        ic_stats = compute_ic_stats(daily_ic, horizon_days=return_horizon_days)
        perf = compute_performance_metrics(long_short, horizon_days=return_horizon_days)
        row = {
            "factor_name": cfg.factor_name,
            "pool": pool_name,
            "start_date": filtered_factor.index.min() if len(filtered_factor.index) else pd.NaT,
            "end_date": filtered_factor.index.max() if len(filtered_factor.index) else pd.NaT,
            "coverage_mean": quality["coverage_ratio"].mean() if "coverage_ratio" in quality else np.nan,
            "valid_factor_count_mean": quality["valid_factor_count"].mean() if "valid_factor_count" in quality else np.nan,
        }
        for horizon in sort_return_labels(future_returns.keys()):
            h_key = return_label(horizon)
            ic_row = ic_stats.loc[h_key] if h_key in ic_stats.index else pd.Series(dtype=float)
            row[f"ic_mean_{h_key}"] = ic_row.get("ic_mean", np.nan)
            row[f"icir_{h_key}"] = ic_row.get("icir", np.nan)
            row[f"ic_t_stat_{h_key}"] = ic_row.get("t_stat", np.nan)
            row[f"icir_raw_{h_key}"] = ic_row.get("icir_raw", np.nan)
            row[f"ic_t_stat_hac_{h_key}"] = ic_row.get("t_stat_hac", np.nan)
            ls_key = f"long_short_{h_key}"
            ls_row = perf.loc[ls_key] if ls_key in perf.index else pd.Series(dtype=float)
            row[f"long_short_mean_{h_key}"] = ls_row.get("mean", np.nan)
            row[f"long_short_sharpe_{h_key}"] = ls_row.get("sharpe", np.nan)
            row[f"long_short_t_stat_{h_key}"] = ls_row.get("t_stat", np.nan)
            row[f"long_short_mean_over_std_raw_{h_key}"] = ls_row.get("mean_over_std_raw", np.nan)
            row[f"long_short_t_stat_hac_{h_key}"] = ls_row.get("t_stat_hac", np.nan)
        rows.append(row)
    return pd.DataFrame(rows).set_index(["factor_name", "pool"])


def run_factor_backtest_data(
    *,
    factor_df: pd.DataFrame,
    market_data: MarketDataBundle,
    config: BacktestConfig | None = None,
    factor_name: str | None = None,
    external_returns: dict | None = None,
    sections: list[ReportSection] | None = None,
    log_fn=print,
) -> BacktestRunResult:
    cfg = replace(
        config or BacktestConfig(),
        render_plots=False,
        export_static_report=False,
    )
    return run_factor_backtest(
        factor_df=factor_df,
        market_data=market_data,
        config=cfg,
        factor_name=factor_name,
        external_returns=external_returns,
        sections=sections,
        log_fn=log_fn,
    )


def render_factor_backtest_report(run_dir: str | Path) -> Path:
    run_path = Path(run_dir)
    meta = read_json(run_path / "run_meta.json")
    log = read_json(run_path / "run_log.json")
    status: dict[str, dict[str, SectionResult]] = {}
    manifest_path = run_path / "manifest.json"
    if manifest_path.exists():
        from factor_backtest_platform.result_loader import LoadedBacktestResult

        loaded = LoadedBacktestResult(
            run_dir=run_path,
            meta=meta,
            log=log,
            manifest=read_json(manifest_path),
        )
        for pool in loaded.available_pools():
            status[pool] = loaded._rebuild_pool_sections(pool, render_plots=True)
    else:
        for pool_dir in sorted((run_path / "pools").iterdir()):
            if not pool_dir.is_dir():
                continue
            status[pool_dir.name] = _render_loaded_section_results(
                pool_dir,
                _load_section_results_from_disk(pool_dir),
                meta,
            )
    report_meta = dict(meta)
    report_meta["render_plots"] = True
    report_meta["export_static_report"] = True
    _write_html_report(run_path, status, meta=report_meta, warnings=log.get("warnings", []))
    return run_path / "report.html"


def _standardize_factor(factor_df: pd.DataFrame) -> pd.DataFrame:
    out = factor_df.copy()
    out.index = pd.to_datetime(out.index)
    out.index.name = "trade_date"
    out.columns = [str(c) for c in out.columns]
    return out.sort_index().sort_index(axis=1).apply(pd.to_numeric, errors="coerce")


def _compat_daily_ic(daily_ic_by_method: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if "spearman" in daily_ic_by_method:
        return daily_ic_by_method["spearman"]
    first_method = next(iter(daily_ic_by_method))
    return daily_ic_by_method[first_method]


def _prepare_run_directories(cfg: BacktestConfig, factor_name: str) -> PreparedRunPaths:
    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S_%f")
    return prepare_run_paths(
        output_root=cfg.output_root,
        factor_name=factor_name,
        run_id=timestamp,
        output_layout=cfg.output_layout,
    )


def _validate_core_section_status(status: dict[str, dict[str, SectionResult]]) -> None:
    failures = []
    for pool, sections in status.items():
        for section_name in CORE_SECTIONS.intersection(sections):
            result = sections[section_name]
            if result.status != "success":
                failures.append(f"{pool}.{section_name}: {result.error or result.status}")
    if failures:
        raise RuntimeError("核心回测模块失败，拒绝发布 latest：\n" + "\n".join(failures))


def _write_neutralized_factor_artifacts(run_dir: Path, pool_name: str, result: SectionResult) -> None:
    for table_name in ("style_neutralized_factor", "style_industry_neutralized_factor"):
        table = result.tables.get(table_name)
        if table is not None:
            write_table(table, ensure_dir(run_dir / "artifacts" / pool_name) / f"{table_name}.parquet")


def _rebase_result_plot_paths(
    status: dict[str, dict[str, SectionResult]],
    old_root: Path,
    new_root: Path,
) -> None:
    for sections in status.values():
        for result in sections.values():
            for plot_name, raw_path in list(result.plots.items()):
                path = Path(raw_path)
                try:
                    relative = path.relative_to(old_root)
                except ValueError:
                    continue
                result.plots[plot_name] = str(new_root / relative)


def _apply_pool(
    factor: pd.DataFrame,
    open_price: pd.DataFrame,
    pool_mask: pd.DataFrame | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    common_dates = factor.index.intersection(open_price.index)
    common_symbols = factor.columns.intersection(open_price.columns)
    out = factor.loc[common_dates, common_symbols]
    if pool_mask is None:
        pool_bool = pd.DataFrame(True, index=out.index, columns=out.columns)
        return out, pool_bool
    pool_bool = (
        pool_mask.reindex(index=out.index, columns=out.columns)
        .astype("boolean")
        .fillna(False)
        .astype(bool)
    )
    return out.where(pool_bool), pool_bool


def _next_trading_day_frame(source: pd.DataFrame, like: pd.DataFrame) -> pd.DataFrame:
    return source.reindex(index=like.index, columns=like.columns).shift(-1)


def _pool_coverage_warnings(pool_name: str, pool_mask: pd.DataFrame | None, factor_dates: pd.Index) -> list[str]:
    if pool_mask is None or pool_mask.empty or len(factor_dates) == 0:
        return []
    factor_idx = pd.DatetimeIndex(pd.to_datetime(factor_dates)).sort_values()
    pool_idx = pd.DatetimeIndex(pd.to_datetime(pool_mask.index)).sort_values()
    warnings: list[str] = []
    if factor_idx.min() < pool_idx.min():
        warnings.append(
            f"股票池 {pool_name} 的成分数据起始日为 {pool_idx.min().date()}，"
            f"早于该日期的因子日期不会进入该股票池有效样本。"
        )
    if factor_idx.max() > pool_idx.max():
        warnings.append(
            f"股票池 {pool_name} 的成分数据结束日为 {pool_idx.max().date()}，"
            f"晚于该日期的因子日期不会进入该股票池有效样本。"
        )
    return warnings


def _has_tradability_data(market_data: MarketDataBundle) -> bool:
    return not _missing_tradability_fields(market_data)


def _missing_tradability_fields(market_data: MarketDataBundle) -> list[str]:
    required = ("high_price", "low_price", "limit_up_price", "limit_down_price", "is_st", "is_suspended", "listed_days")
    return [name for name in required if getattr(market_data, name) is None]


def _resolve_sections(cfg: BacktestConfig) -> list[ReportSection]:
    if cfg.enabled_sections == "all":
        if cfg.data_sources.risk_exposure_source == "none":
            return [section for section in DEFAULT_SECTIONS if section.name not in RISK_EXPOSURE_SECTIONS]
        return DEFAULT_SECTIONS
    if not isinstance(cfg.enabled_sections, list):
        raise ValueError("enabled_sections must be 'all' or a list of section names")
    sections_by_name = {section.name: section for section in DEFAULT_SECTIONS}
    unknown = [name for name in cfg.enabled_sections if name not in sections_by_name]
    if unknown:
        raise KeyError(f"Unknown report sections: {unknown}")
    return [sections_by_name[name] for name in cfg.enabled_sections]


def _section_list_needs_risk_exposure(sections: list[ReportSection]) -> bool:
    return any(section.name in RISK_EXPOSURE_SECTIONS for section in sections)


def _section_list_needs_risk_exposure_panel(sections: list[ReportSection]) -> bool:
    return any(section.name in RISK_EXPOSURE_PANEL_SECTIONS for section in sections)


def _status_to_json(status: dict[str, dict[str, SectionResult]]) -> dict:
    return {
        pool: {
            name: {"status": result.status, "error": result.error, "warnings": result.warnings}
            for name, result in sections.items()
        }
        for pool, sections in status.items()
    }


def _elapsed(start: float) -> float:
    return round(perf_counter() - start, 6)


def _load_section_results_from_disk(pool_dir: Path) -> dict[str, SectionResult]:
    tables_dir = pool_dir / "tables"
    plots_dir = pool_dir / "plots"
    group_return_tables = ["daily_group_returns", "group_return_summary"]
    if tables_dir.exists():
        group_return_tables.extend(sorted(path.stem for path in tables_dir.glob("group_cumulative_returns_*.csv")))
    ic_overview_tables = ["ic_overview"]
    cumulative_ic_tables = ["daily_ic", "cumulative_ic", "ic_stats"]
    yearly_ic_tables = []
    if tables_dir.exists():
        ic_overview_tables.extend(sorted(path.stem for path in tables_dir.glob("ic_overview_*.csv")))
        cumulative_ic_tables.extend(sorted(path.stem for path in tables_dir.glob("daily_ic_*.csv")))
        cumulative_ic_tables.extend(sorted(path.stem for path in tables_dir.glob("cumulative_ic_*.csv")))
        cumulative_ic_tables.extend(sorted(path.stem for path in tables_dir.glob("ic_stats_*.csv")))
        yearly_ic_tables.extend(sorted(path.stem for path in tables_dir.glob("yearly_ic_stats_*.csv")))
        yearly_ic_tables.extend(sorted(path.stem for path in tables_dir.glob("yearly_mean_ic_*.csv")))
        ic_overview_tables = list(dict.fromkeys(ic_overview_tables))
        cumulative_ic_tables = list(dict.fromkeys(cumulative_ic_tables))
    factor_style_tables = []
    style_neutralized_tables = []
    style_industry_neutralized_tables = []
    group_exposure_tables = [
        "group_style_exposure_daily",
        "group_style_exposure_summary",
        "group_industry_exposure_daily",
        "group_industry_exposure_summary",
    ]
    within_industry_group_tables = ["within_industry_daily_group_returns", "within_industry_group_return_summary"]
    if tables_dir.exists():
        factor_style_tables.extend(sorted(path.stem for path in tables_dir.glob("factor_style_exposure_corr*.csv")))
        style_neutralized_tables.extend(sorted(path.stem for path in tables_dir.glob("style_neutralized_*.csv")))
        style_neutralized_tables.extend(sorted(path.stem for path in tables_dir.glob("cumulative_style_neutralized_*.csv")))
        style_industry_neutralized_tables.extend(sorted(path.stem for path in tables_dir.glob("style_industry_neutralized_*.csv")))
        style_industry_neutralized_tables.extend(
            sorted(path.stem for path in tables_dir.glob("cumulative_style_industry_neutralized_*.csv"))
        )
        group_exposure_tables.extend(sorted(path.stem for path in tables_dir.glob("group_*_exposure_*.csv")))
        within_industry_group_tables.extend(
            sorted(path.stem for path in tables_dir.glob("within_industry_group_cumulative_returns_*.csv"))
        )
    table_map = {
        "data_quality": ["data_quality", "data_quality_counts", "data_quality_ratios"],
        "ic_overview": ic_overview_tables,
        "cumulative_ic": cumulative_ic_tables,
        "yearly_ic": list(dict.fromkeys(yearly_ic_tables)),
        "factor_style_exposure": list(dict.fromkeys(factor_style_tables)),
        "style_neutralized_ic": list(dict.fromkeys(style_neutralized_tables)),
        "style_industry_neutralized_ic": list(dict.fromkeys(style_industry_neutralized_tables)),
        "group_exposure_diagnostics": list(dict.fromkeys(group_exposure_tables)),
        "group_return": group_return_tables,
        "within_industry_group_return": list(dict.fromkeys(within_industry_group_tables)),
        "layered_group_return": ["layered_group_return_summary"],
        "long_short": [
            "daily_long_short_returns",
            "cumulative_long_short_returns",
            "daily_equivalent_long_short_returns",
            "cumulative_daily_equivalent_long_short_returns",
        ],
        "group_turnover": [
            "daily_group_turnover",
            "group_turnover_summary",
            "group_turnover_edge_summary",
            "daily_group_membership_change",
            "group_membership_change_summary",
            "group_membership_change_edge_summary",
        ],
        "performance_metrics": ["performance_metrics", "performance_diagnostics"],
    }
    plot_map = {
        "data_quality": ["data_quality_counts.png", "data_quality_ratios.png"],
        "ic_overview": sorted(p.name for p in plots_dir.glob("ic_overview*.png")) if plots_dir.exists() else ["ic_overview.png"],
        "cumulative_ic": sorted(p.name for p in plots_dir.glob("cumulative_ic*.png")) if plots_dir.exists() else ["cumulative_ic.png"],
        "yearly_ic": sorted(p.name for p in plots_dir.glob("yearly_mean_ic_*.png")) if plots_dir.exists() else [],
        "factor_style_exposure": sorted(p.name for p in plots_dir.glob("factor_style_exposure_corr*.png")) if plots_dir.exists() else [],
        "style_neutralized_ic": sorted(p.name for p in plots_dir.glob("cumulative_style_neutralized_ic*.png"))
        if plots_dir.exists()
        else [],
        "style_industry_neutralized_ic": sorted(p.name for p in plots_dir.glob("cumulative_style_industry_neutralized_ic*.png"))
        if plots_dir.exists()
        else [],
        "group_exposure_diagnostics": sorted(p.name for p in plots_dir.glob("group_*_exposure*.png")) if plots_dir.exists() else [],
        "group_return": sorted(p.name for p in plots_dir.glob("group_cumulative_return_*.png")) + ["group_return_bar.png"]
        if plots_dir.exists()
        else ["group_return_bar.png"],
        "within_industry_group_return": sorted(p.name for p in plots_dir.glob("within_industry_group*.png")) if plots_dir.exists() else [],
        "layered_group_return": sorted(p.name for p in plots_dir.glob("group_return_bar_*.png")) if plots_dir.exists() else [],
        "long_short": ["long_short_curve.png"],
        "group_turnover": sorted(p.name for p in plots_dir.glob("group_turnover*.png")) if plots_dir.exists() else [],
    }
    out: dict[str, SectionResult] = {}
    for section_name, table_names in table_map.items():
        tables = {}
        for table_name in table_names:
            path = tables_dir / f"{table_name}.csv"
            if path.exists():
                tables[table_name] = _read_section_table(path, table_name)
        plots = {}
        for plot_name in plot_map.get(section_name, []):
            path = plots_dir / plot_name
            if path.exists():
                plots[plot_name] = str(path)
        if tables or plots:
            out[section_name] = SectionResult(name=section_name, status="success", tables=tables, plots=plots)
    return out


def _read_section_table(path: Path, table_name: str) -> pd.DataFrame:
    if table_name == "daily_group_returns":
        df = pd.read_csv(path, index_col=[0, 1, 2])
        df.index = pd.MultiIndex.from_arrays(
            [
                pd.to_datetime(df.index.get_level_values(0)),
                df.index.get_level_values(1),
                df.index.get_level_values(2).astype(int),
            ],
            names=["trade_date", "horizon", "group"],
        )
        return df
    if table_name == "within_industry_daily_group_returns":
        df = pd.read_csv(path, index_col=[0, 1, 2])
        df.index = pd.MultiIndex.from_arrays(
            [
                pd.to_datetime(df.index.get_level_values(0)),
                df.index.get_level_values(1),
                df.index.get_level_values(2).astype(int),
            ],
            names=["trade_date", "horizon", "group"],
        )
        return df
    if table_name in {"group_style_exposure_daily", "group_industry_exposure_daily"}:
        df = pd.read_csv(path, index_col=[0, 1, 2])
        exposure_level = "exposure" if table_name == "group_style_exposure_daily" else "industry"
        df.index = pd.MultiIndex.from_arrays(
            [
                pd.to_datetime(df.index.get_level_values(0)),
                df.index.get_level_values(1),
                df.index.get_level_values(2),
            ],
            names=["trade_date", "leg", exposure_level],
        )
        return df
    if table_name in {"group_style_exposure_summary", "group_industry_exposure_summary"}:
        df = pd.read_csv(path, index_col=[0, 1])
        exposure_level = "exposure" if table_name == "group_style_exposure_summary" else "industry"
        df.index = pd.MultiIndex.from_arrays(
            [df.index.get_level_values(0), df.index.get_level_values(1)],
            names=["leg", exposure_level],
        )
        return df
    if table_name in {"daily_group_turnover", "daily_group_membership_change"}:
        df = pd.read_csv(path, index_col=0)
        df.index = pd.to_datetime(df.index)
        return df
    if table_name.startswith("yearly_ic_stats_"):
        df = pd.read_csv(path, index_col=[0, 1])
        df.index = pd.MultiIndex.from_arrays(
            [
                df.index.get_level_values(0).astype(int),
                df.index.get_level_values(1),
            ],
            names=["year", "horizon"],
        )
        return df
    if table_name == "layered_group_return_summary":
        df = pd.read_csv(path, index_col=[0, 1, 2])
        df.index = pd.MultiIndex.from_arrays(
            [
                df.index.get_level_values(0),
                df.index.get_level_values(1),
                df.index.get_level_values(2).astype(int),
            ],
            names=["window", "horizon", "group"],
        )
        return df
    return read_table(path)


def _render_loaded_section_results(pool_dir: Path, sections: dict[str, SectionResult], meta: dict | None = None) -> dict[str, SectionResult]:
    section_classes = {section.name: section for section in DEFAULT_SECTIONS}
    aligned_factor_path = pool_dir / "artifacts" / "aligned_factor.parquet"
    fallback_factor_path = aligned_factor_path.with_suffix(aligned_factor_path.suffix + ".pkl")
    if aligned_factor_path.exists():
        plot_index = read_table(aligned_factor_path).index
    elif fallback_factor_path.exists():
        plot_index = read_table(fallback_factor_path).index
    else:
        plot_index = None
    context = {
        "plots_dir": ensure_dir(pool_dir / "plots"),
        "plot_index": plot_index,
        "horizon_colors": BacktestConfig().horizon_colors,
        "return_horizon_days": (meta or {}).get("return_horizon_days", {}),
    }
    rendered = {}
    for name, result in sections.items():
        section = section_classes.get(name)
        if section is None:
            rendered[name] = result
            continue
        try:
            rendered[name] = section.render(context, result)
        except Exception as exc:
            result.status = "failed"
            result.error = str(exc)
            rendered[name] = result
    return rendered


def _write_html_report(
    run_dir: Path,
    status: dict[str, dict[str, SectionResult]],
    *,
    meta: dict,
    warnings: list[str],
) -> None:
    rows = [_render_report_header(meta, warnings)]
    for pool, sections in status.items():
        rows.append(f"<section><h2>{escape(pool)}</h2>")
        rows.append(
            _render_pool_links(
                pool,
                schema_v2=str(meta.get("result_schema_version", "")) == RESULT_SCHEMA_VERSION,
            )
        )
        rows.append(_render_key_plots(run_dir, sections))
        rows.append(_render_key_tables(sections))
        rows.append(_render_status_table(sections))
        rows.append("</section>")
    body = "\n".join(rows)
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>因子回测报告</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; color: #222; line-height: 1.45; }
    h1 { margin-bottom: 8px; }
    h2 { border-bottom: 2px solid #333; padding-bottom: 6px; margin-top: 32px; }
    h3 { margin-top: 22px; }
    table { border-collapse: collapse; width: 100%; margin: 12px 0 24px; font-size: 13px; }
    th, td { border: 1px solid #ddd; padding: 7px 8px; text-align: left; vertical-align: top; }
    th { background: #f2f2f2; }
    img { max-width: 100%; border: 1px solid #ddd; margin: 8px 0 20px; }
    .meta, .links, .warning { background: #f7f7f7; padding: 12px 14px; margin: 12px 0; }
    .plot-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 18px; }
    .plot-card h4 { margin: 0 0 6px; }
    .muted { color: #666; }
    a { color: #245b9d; text-decoration: none; }
  </style>
</head>
<body>
  <h1>因子回测报告</h1>
  __BODY__
</body>
</html>
""".replace("__BODY__", body)
    (run_dir / "report.html").write_text(html, encoding="utf-8")


def _render_report_header(meta: dict, warnings: list[str]) -> str:
    meta_rows = "".join(
        f"<tr><th>{escape(str(key))}</th><td>{escape(str(value))}</td></tr>"
        for key, value in meta.items()
    )
    artifact_note = (
        "完整中间矩阵已按 artifact_level=full 写入各 pool 目录下的 artifacts。"
        if meta.get("artifact_level") == "full"
        else "当前 artifact_level=none，不写 aligned_factor、future_returns 等大矩阵。"
    )
    plot_note = (
        "本次回测已生成 PNG 图并由 report.html 引用。"
        if meta.get("render_plots", True)
        else "当前 render_plots=False，本次不写 PNG；每日基础 Parquet 可用于后续重渲染图表。"
    )
    parts = [
        "<p class=\"muted\">本报告汇总单次回测中各股票池的关键图表、核心统计表和模块运行状态。"
        f"{artifact_note}{plot_note}</p>",
        "<p class=\"muted\">多日 horizon 使用逐日重叠远期收益。IC 与多空均值显著性默认查看 Newey-West "
        "HAC t-stat（lag=horizon_days-1）；ICIR 与 mean_over_std_raw 均为未年化 mean/std。"
        "分组和多空累计线是日等效诊断曲线，不代表可直接交易的组合净值；多日重叠收益不报告可实现最大回撤。"
        "当前所谓换手仅表示相邻因子日的分组成员变化率，不是持仓权重换手率。</p>",
        f"<div class=\"meta\"><table>{meta_rows}</table></div>",
    ]
    if warnings:
        warning_items = "".join(f"<li>{escape(w)}</li>" for w in warnings)
        parts.append(f"<div class=\"warning\"><strong>Warnings</strong><ul>{warning_items}</ul></div>")
    return "\n".join(parts)


def _render_pool_links(pool: str, *, schema_v2: bool = False) -> str:
    safe_pool = escape(pool)
    if schema_v2:
        return (
            "<div class=\"links\">"
            f"<a href=\"plots/{safe_pool}/\">plots</a> | "
            "<a href=\"data/\">data</a> | "
            f"<a href=\"artifacts/{safe_pool}/\">artifacts</a>"
            "</div>"
        )
    base = f"pools/{safe_pool}"
    return (
        "<div class=\"links\">"
        f"<a href=\"{base}/plots/\">plots</a> | "
        f"<a href=\"{base}/tables/\">tables</a> | "
        f"<a href=\"{base}/artifacts/\">artifacts</a>"
        "</div>"
    )


def _render_key_plots(run_dir: Path, sections: dict[str, SectionResult]) -> str:
    ic_plot_order = []
    cumulative_result = sections.get("cumulative_ic")
    if cumulative_result is not None:
        for method, title in (("spearman", "Cumulative Spearman RankIC"), ("pearson", "Cumulative Pearson IC")):
            plot_name = f"cumulative_ic_{method}.png"
            if plot_name in cumulative_result.plots:
                ic_plot_order.append(("cumulative_ic", plot_name, title))
        if not ic_plot_order and "cumulative_ic.png" in cumulative_result.plots:
            ic_plot_order.append(("cumulative_ic", "cumulative_ic.png", "Cumulative RankIC"))
        for plot_name in sorted(cumulative_result.plots):
            if not plot_name.startswith("cumulative_ic_") or plot_name in {p for _, p, _ in ic_plot_order}:
                continue
            method = plot_name.removeprefix("cumulative_ic_").removesuffix(".png")
            ic_plot_order.append(("cumulative_ic", plot_name, f"Cumulative {method.title()} IC"))
    overview_plot_order = []
    overview_result = sections.get("ic_overview")
    if overview_result is not None:
        for method, title in (
            ("spearman", "20-Day Moving Average Spearman RankIC"),
            ("pearson", "20-Day Moving Average Pearson IC"),
        ):
            plot_name = f"ic_overview_{method}.png"
            if plot_name in overview_result.plots:
                overview_plot_order.append(("ic_overview", plot_name, title))
        if not overview_plot_order and "ic_overview.png" in overview_result.plots:
            overview_plot_order.append(("ic_overview", "ic_overview.png", "20-Day Moving Average RankIC"))
        for plot_name in sorted(overview_result.plots):
            if not plot_name.startswith("ic_overview_") or plot_name in {p for _, p, _ in overview_plot_order}:
                continue
            method = plot_name.removeprefix("ic_overview_").removesuffix(".png")
            overview_plot_order.append(("ic_overview", plot_name, f"20-Day Moving Average {method.title()} IC"))
    yearly_plot_order = []
    yearly_result = sections.get("yearly_ic")
    if yearly_result is not None:
        for method, title in (
            ("spearman", "Yearly Mean Spearman RankIC"),
            ("pearson", "Yearly Mean Pearson IC"),
        ):
            plot_name = f"yearly_mean_ic_{method}.png"
            if plot_name in yearly_result.plots:
                yearly_plot_order.append(("yearly_ic", plot_name, title))
    builtin_group_plots = [
        ("group_return", "group_cumulative_return_1d.png", "10-Group Cumulative Return 1D"),
        ("group_return", "group_cumulative_return_5d.png", "10-Group Cumulative Return 5D"),
        ("group_return", "group_cumulative_return_10d.png", "10-Group Cumulative Return 10D"),
        ("group_return", "group_cumulative_return_20d.png", "10-Group Cumulative Return 20D"),
    ]
    group_result = sections.get("group_return")
    known_group_plot_names = {plot_name for _, plot_name, _ in builtin_group_plots}
    extra_group_plots = []
    if group_result is not None:
        for plot_name in sorted(group_result.plots):
            if not plot_name.startswith("group_cumulative_return_") or plot_name in known_group_plot_names:
                continue
            label = plot_name.removeprefix("group_cumulative_return_").removesuffix(".png")
            extra_group_plots.append(("group_return", plot_name, f"10-Group Cumulative Return {label.upper()}"))
    diagnostic_plot_order = [
        (
            "factor_style_exposure",
            "factor_style_exposure_corr_spearman.png",
            "Factor Style Exposure Correlation Spearman",
        ),
        (
            "factor_style_exposure",
            "factor_style_exposure_corr_pearson.png",
            "Factor Style Exposure Correlation Pearson",
        ),
        (
            "style_neutralized_ic",
            "cumulative_style_neutralized_ic_spearman.png",
            "Cumulative Style Neutralized Spearman RankIC",
        ),
        (
            "style_neutralized_ic",
            "cumulative_style_neutralized_ic_pearson.png",
            "Cumulative Style Neutralized Pearson IC",
        ),
        (
            "style_industry_neutralized_ic",
            "cumulative_style_industry_neutralized_ic_spearman.png",
            "Cumulative Style + Industry Neutralized Spearman RankIC",
        ),
        (
            "style_industry_neutralized_ic",
            "cumulative_style_industry_neutralized_ic_pearson.png",
            "Cumulative Style + Industry Neutralized Pearson IC",
        ),
        ("group_exposure_diagnostics", "group_style_exposure_g1.png", "Group Style Exposure G1"),
        ("group_exposure_diagnostics", "group_style_exposure_g10.png", "Group Style Exposure G10"),
        (
            "group_exposure_diagnostics",
            "group_style_exposure_g10_minus_g1.png",
            "Group Style Exposure G10 - G1",
        ),
        ("group_exposure_diagnostics", "group_industry_exposure_g1.png", "Group Industry Exposure G1"),
        ("group_exposure_diagnostics", "group_industry_exposure_g10.png", "Group Industry Exposure G10"),
        (
            "group_exposure_diagnostics",
            "group_industry_exposure_g10_minus_g1.png",
            "Group Industry Exposure G10 - G1",
        ),
        (
            "within_industry_group_return",
            "within_industry_group_return_bar.png",
            "Within-Industry 10-Group Forward Returns",
        ),
        (
            "within_industry_group_return",
            "within_industry_group_cumulative_return_1d.png",
            "Within-Industry 10-Group Cumulative Return 1D",
        ),
        (
            "within_industry_group_return",
            "within_industry_group_cumulative_return_5d.png",
            "Within-Industry 10-Group Cumulative Return 5D",
        ),
        (
            "within_industry_group_return",
            "within_industry_group_cumulative_return_10d.png",
            "Within-Industry 10-Group Cumulative Return 10D",
        ),
        (
            "within_industry_group_return",
            "within_industry_group_cumulative_return_20d.png",
            "Within-Industry 10-Group Cumulative Return 20D",
        ),
        ("group_turnover", "group_turnover_edges.png", "Daily G1 and G10 Membership Change"),
        ("group_turnover", "group_turnover.png", "10-Group Daily Membership Change"),
    ]
    plot_order = [
        *ic_plot_order,
        *overview_plot_order,
        *yearly_plot_order,
        *builtin_group_plots,
        *extra_group_plots,
        ("group_return", "group_return_bar.png", "10-Group Forward Returns"),
        ("layered_group_return", "group_return_bar_6m.png", "10-Group Forward Returns 6M"),
        ("layered_group_return", "group_return_bar_1y.png", "10-Group Forward Returns 1Y"),
        ("layered_group_return", "group_return_bar_3y.png", "10-Group Forward Returns 3Y"),
        ("layered_group_return", "group_return_bar_5y.png", "10-Group Forward Returns 5Y"),
        (
            "long_short",
            "long_short_curve.png",
            "Cumulative Daily-Equivalent Long-Short Spread (Diagnostic)",
        ),
        ("data_quality", "data_quality_counts.png", "Factor Coverage Counts"),
        ("data_quality", "data_quality_ratios.png", "Factor Coverage and Invalid Value Ratios"),
        *diagnostic_plot_order,
    ]
    cards = []
    for section_name, plot_name, title in plot_order:
        result = sections.get(section_name)
        if result is None or plot_name not in result.plots:
            continue
        rel = _relative_report_path(run_dir, Path(result.plots[plot_name]))
        cards.append(
            f"<div class=\"plot-card\"><h4>{escape(title)}</h4>"
            f"<a href=\"{rel}\"><img src=\"{rel}\" alt=\"{escape(title)}\"></a></div>"
        )
    if not cards:
        return "<h3>关键图表</h3><p class=\"muted\">本次运行没有可嵌入图表，请检查 plots 目录或模块 warning。</p>"
    return "<h3>关键图表</h3><div class=\"plot-grid\">" + "\n".join(cards) + "</div>"


def _render_key_tables(sections: dict[str, SectionResult]) -> str:
    cumulative_result = sections.get("cumulative_ic")
    ic_specs = []
    if cumulative_result is not None:
        has_pearson = "ic_stats_pearson" in cumulative_result.tables
        if has_pearson:
            if "ic_stats_spearman" in cumulative_result.tables:
                ic_specs.append(("cumulative_ic", "ic_stats_spearman", "Spearman RankIC Statistics"))
            ic_specs.append(("cumulative_ic", "ic_stats_pearson", "Pearson IC Statistics"))
        else:
            ic_specs.append(("cumulative_ic", "ic_stats", "IC Statistics"))
    yearly_specs = []
    yearly_result = sections.get("yearly_ic")
    if yearly_result is not None:
        if "yearly_ic_stats_spearman" in yearly_result.tables:
            yearly_specs.append(("yearly_ic", "yearly_ic_stats_spearman", "Yearly Spearman RankIC Statistics"))
        if "yearly_ic_stats_pearson" in yearly_result.tables:
            yearly_specs.append(("yearly_ic", "yearly_ic_stats_pearson", "Yearly Pearson IC Statistics"))
    style_exposure_specs = []
    style_exposure_result = sections.get("factor_style_exposure")
    if style_exposure_result is not None:
        if "factor_style_exposure_corr_summary_spearman" in style_exposure_result.tables:
            style_exposure_specs.append(
                ("factor_style_exposure", "factor_style_exposure_corr_summary_spearman", "Spearman Style Exposure Correlation Summary")
            )
        if "factor_style_exposure_corr_summary_pearson" in style_exposure_result.tables:
            style_exposure_specs.append(
                ("factor_style_exposure", "factor_style_exposure_corr_summary_pearson", "Pearson Style Exposure Correlation Summary")
            )
    neutralized_specs = []
    style_neutralized_result = sections.get("style_neutralized_ic")
    if style_neutralized_result is not None:
        if "style_neutralized_ic_stats_spearman" in style_neutralized_result.tables:
            neutralized_specs.append(
                ("style_neutralized_ic", "style_neutralized_ic_stats_spearman", "Style Neutralized Spearman RankIC Statistics")
            )
        if "style_neutralized_ic_stats_pearson" in style_neutralized_result.tables:
            neutralized_specs.append(("style_neutralized_ic", "style_neutralized_ic_stats_pearson", "Style Neutralized Pearson IC Statistics"))
    style_industry_neutralized_result = sections.get("style_industry_neutralized_ic")
    if style_industry_neutralized_result is not None:
        if "style_industry_neutralized_ic_stats_spearman" in style_industry_neutralized_result.tables:
            neutralized_specs.append(
                (
                    "style_industry_neutralized_ic",
                    "style_industry_neutralized_ic_stats_spearman",
                    "Style + Industry Neutralized Spearman RankIC Statistics",
                )
            )
        if "style_industry_neutralized_ic_stats_pearson" in style_industry_neutralized_result.tables:
            neutralized_specs.append(
                (
                    "style_industry_neutralized_ic",
                    "style_industry_neutralized_ic_stats_pearson",
                    "Style + Industry Neutralized Pearson IC Statistics",
                )
            )
    specs = [
        *ic_specs,
        *yearly_specs,
        *style_exposure_specs,
        *neutralized_specs,
        ("group_exposure_diagnostics", "group_style_exposure_summary", "Group Style Exposure Summary"),
        ("group_exposure_diagnostics", "group_industry_exposure_summary", "Group Industry Exposure Summary"),
        ("group_return", "group_return_summary", "Group Return Summary"),
        ("within_industry_group_return", "within_industry_group_return_summary", "Within-Industry Group Return Summary"),
        ("layered_group_return", "layered_group_return_summary", "Layered Group Return Summary"),
        (
            "long_short",
            "cumulative_daily_equivalent_long_short_returns",
            "Cumulative Daily-Equivalent Long-Short Spread Tail (Diagnostic)",
        ),
        ("group_turnover", "group_membership_change_edge_summary", "Group Membership Change Edge Summary"),
        ("group_turnover", "group_membership_change_summary", "Group Membership Change Summary"),
        ("performance_metrics", "performance_diagnostics", "Long-Short Diagnostics"),
    ]
    if sections.get("long_short") is not None and "cumulative_daily_equivalent_long_short_returns" not in sections[
        "long_short"
    ].tables:
        specs.append(("long_short", "cumulative_long_short_returns", "Legacy Cumulative Long-Short Return Tail"))
    if sections.get("group_turnover") is not None and "group_membership_change_summary" not in sections[
        "group_turnover"
    ].tables:
        specs.extend(
            [
                ("group_turnover", "group_turnover_edge_summary", "Legacy Group Turnover Edge Summary"),
                ("group_turnover", "group_turnover_summary", "Legacy Group Turnover Summary"),
            ]
        )
    if sections.get("performance_metrics") is not None and "performance_diagnostics" not in sections[
        "performance_metrics"
    ].tables:
        specs.append(("performance_metrics", "performance_metrics", "Legacy Performance Metrics"))
    parts = ["<h3>关键统计表</h3>"]
    rendered = 0
    for section_name, table_name, title in specs:
        result = sections.get(section_name)
        if result is None or table_name not in result.tables:
            continue
        table = result.tables[table_name]
        if table.empty:
            continue
        if table_name.startswith("yearly_ic_stats"):
            view = table
        elif "ic_stats" in table_name:
            report_columns = [
                "ic_mean",
                "ic_std",
                "icir_raw",
                "ic_positive_ratio",
                "t_stat_hac",
                "hac_lag",
                "valid_days",
            ]
            view = table.reindex(columns=[col for col in report_columns if col in table.columns])
        elif table_name in {
            "cumulative_long_short_returns",
            "cumulative_daily_equivalent_long_short_returns",
        }:
            view = table.tail(10)
        else:
            view = table
        parts.append(f"<h4>{escape(title)}</h4>")
        parts.append(
            _dataframe_to_html(
                view,
                max_rows=20
                if table_name
                in {"cumulative_long_short_returns", "cumulative_daily_equivalent_long_short_returns"}
                else None,
            )
        )
        rendered += 1
    if rendered == 0:
        parts.append("<p class=\"muted\">本次运行没有可展示的关键统计表，请检查 tables 目录或模块 warning。</p>")
    return "\n".join(parts)


def _render_status_table(sections: dict[str, SectionResult]) -> str:
    rows = ["<h3>模块状态</h3><table><thead><tr><th>模块</th><th>状态</th><th>错误</th><th>Warning</th></tr></thead><tbody>"]
    for name, result in sections.items():
        section_warnings = "<br>".join(escape(w) for w in result.warnings)
        rows.append(
            f"<tr><td>{escape(name)}</td><td>{escape(result.status)}</td>"
            f"<td>{escape(result.error or '')}</td><td>{section_warnings}</td></tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _dataframe_to_html(df: pd.DataFrame, *, max_rows: int | None = 20, max_cols: int | None = 12) -> str:
    return df.to_html(max_rows=max_rows, max_cols=max_cols, border=0, escape=True, float_format=format_table_float)


def _relative_report_path(run_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(run_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _log(cfg: BacktestConfig, log_fn, message: str) -> None:
    if cfg.verbose:
        log_fn(message)
