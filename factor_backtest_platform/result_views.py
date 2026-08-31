from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from factor_backtest_platform.analytics import (
    compute_daily_equivalent_long_short_returns,
    compute_exposure_corr_summary,
    compute_exposure_value_summary,
    compute_ic_stats,
    compute_long_short_returns,
    compute_performance_metrics,
    compute_turnover_summary,
    compute_yearly_ic_stats,
)
from factor_backtest_platform.sections import (
    CumulativeICSection,
    DataQualitySection,
    FactorStyleExposureSection,
    GroupExposureDiagnosticsSection,
    GroupReturnSection,
    GroupTurnoverSection,
    ICOverviewSection,
    LayeredGroupReturnSection,
    LongShortSection,
    PerformanceMetricsSection,
    SectionResult,
    StyleIndustryNeutralizedICSection,
    StyleNeutralizedICSection,
    WithinIndustryGroupReturnSection,
    YearlyICSection,
    _group_cumulative_return_table,
)


@dataclass(frozen=True)
class ICView:
    daily: pd.DataFrame
    cumulative: pd.DataFrame
    moving_average_20d: pd.DataFrame
    stats: pd.DataFrame
    yearly_stats: pd.DataFrame


@dataclass(frozen=True)
class GroupReturnView:
    daily: pd.DataFrame
    summary: pd.DataFrame
    cumulative_by_horizon: dict[str, pd.DataFrame]
    daily_long_short: pd.DataFrame
    daily_equivalent_long_short: pd.DataFrame
    cumulative_daily_equivalent_long_short: pd.DataFrame
    performance_metrics: pd.DataFrame
    warnings: list[str]


@dataclass(frozen=True)
class QualityView:
    daily: pd.DataFrame
    counts: pd.DataFrame
    ratios: pd.DataFrame


def build_ic_view(
    daily_ic: pd.DataFrame,
    *,
    start_date=None,
    end_date=None,
    horizon_days: dict[str, int | None] | None = None,
    yearly_min_days: int = 60,
    yearly_include_partial_year: bool = True,
) -> ICView:
    """根据保存的每日 IC 构建任意日期范围的前端视图。"""
    source = _normalize_daily_index(daily_ic)
    selected = _slice_datetime_index(source, start_date=start_date, end_date=end_date)
    warm_source = source
    if start_date is not None and not source.empty:
        start = pd.Timestamp(start_date)
        prior = source.loc[source.index < start].tail(19)
        warm_source = pd.concat([prior, source.loc[source.index >= start]])
    if end_date is not None:
        warm_source = warm_source.loc[warm_source.index <= pd.Timestamp(end_date)]
    moving = warm_source.rolling(20, min_periods=1).mean().reindex(selected.index)
    cumulative = _rebase_additive_curve(selected.cumsum())
    stats = compute_ic_stats(selected, horizon_days=horizon_days)
    yearly = compute_yearly_ic_stats(
        selected,
        horizon_days=horizon_days,
        min_days=yearly_min_days,
        include_partial_year=yearly_include_partial_year,
    )
    return ICView(
        daily=selected,
        cumulative=cumulative,
        moving_average_20d=moving,
        stats=stats,
        yearly_stats=yearly,
    )


def build_group_return_view(
    daily_group_returns: pd.DataFrame,
    *,
    start_date=None,
    end_date=None,
    horizon_days: dict[str, int | None] | None = None,
) -> GroupReturnView:
    """根据每日分组收益构建区间内分组、多空和绩效视图。"""
    selected = _slice_trade_date_level(daily_group_returns, start_date=start_date, end_date=end_date)
    if selected.empty:
        return GroupReturnView(
            daily=selected,
            summary=pd.DataFrame(),
            cumulative_by_horizon={},
            daily_long_short=pd.DataFrame(),
            daily_equivalent_long_short=pd.DataFrame(),
            cumulative_daily_equivalent_long_short=pd.DataFrame(),
            performance_metrics=pd.DataFrame(),
            warnings=[],
        )
    summary = selected.groupby(["horizon", "group"])["group_return"].mean().unstack("horizon")
    cumulative_by_horizon = {}
    warnings = []
    for horizon in selected.index.get_level_values("horizon").unique():
        label = str(horizon)
        days = (horizon_days or {}).get(label)
        if days is None:
            warnings.append(f"{label} 缺少 horizon_days，无法生成日等效累计分组收益")
            continue
        cumulative = _group_cumulative_return_table(selected, horizon, int(days))
        cumulative_by_horizon[label] = _rebase_multiplicative_curve(cumulative)
    daily_long_short = compute_long_short_returns(selected)
    daily_equivalent, skipped = compute_daily_equivalent_long_short_returns(
        selected,
        horizon_days=horizon_days,
    )
    warnings.extend(f"{label} 缺少 horizon_days，无法生成日等效多空曲线" for label in skipped)
    cumulative_long_short = _rebase_additive_curve(daily_equivalent.fillna(0.0).cumsum())
    performance = compute_performance_metrics(daily_long_short, horizon_days=horizon_days)
    return GroupReturnView(
        daily=selected,
        summary=summary,
        cumulative_by_horizon=cumulative_by_horizon,
        daily_long_short=daily_long_short,
        daily_equivalent_long_short=daily_equivalent,
        cumulative_daily_equivalent_long_short=cumulative_long_short,
        performance_metrics=performance,
        warnings=warnings,
    )


def build_quality_view(
    data_quality: pd.DataFrame,
    *,
    start_date=None,
    end_date=None,
) -> QualityView:
    """按日期范围返回数据质量原表、数量表和比例表。"""
    selected = _slice_datetime_index(
        _normalize_daily_index(data_quality),
        start_date=start_date,
        end_date=end_date,
    )
    count_columns = [
        column
        for column in ("pool_stock_count", "valid_factor_count")
        if column in selected.columns
    ]
    ratio_columns = [column for column in selected.columns if str(column).endswith("_ratio")]
    return QualityView(
        daily=selected,
        counts=selected.loc[:, count_columns].copy(),
        ratios=selected.loc[:, ratio_columns].copy(),
    )


def rebuild_report_sections(
    base_tables: dict[str, pd.DataFrame],
    *,
    meta: dict,
    section_log: dict[str, dict] | None,
    plots_dir: str | Path,
    render_plots: bool,
) -> dict[str, SectionResult]:
    """从 Schema 2 每日基础表重建旧报告需要的全部派生 section。"""
    context = {
        "plots_dir": Path(plots_dir),
        "render_plots": bool(render_plots),
        "plot_index": _infer_plot_index(base_tables),
        "horizon_colors": _normalize_horizon_colors(meta.get("horizon_colors")),
        "group_return_windows": meta.get("group_return_windows", {}),
        "yearly_ic_min_days": meta.get("yearly_ic_min_days", 60),
        "yearly_ic_include_partial_year": meta.get("yearly_ic_include_partial_year", True),
        "return_horizon_days": meta.get("return_horizon_days", {}),
        "ic_methods": meta.get("ic_methods", ["spearman"]),
    }
    logged = section_log or {}
    out: dict[str, SectionResult] = {}

    quality = base_tables.get("data_quality")
    if quality is not None and _section_was_enabled(logged, "data_quality"):
        out["data_quality"] = _compute_and_render(DataQualitySection(), {**context, "data_quality": quality})

    daily_ic_by_method = {
        name.removeprefix("daily_ic_"): table
        for name, table in base_tables.items()
        if name.startswith("daily_ic_")
    }
    if daily_ic_by_method:
        ic_context = {
            **context,
            "daily_ic_by_method": daily_ic_by_method,
            "daily_ic": daily_ic_by_method.get("spearman", next(iter(daily_ic_by_method.values()))),
        }
        for section in (ICOverviewSection(), CumulativeICSection(), YearlyICSection()):
            if _section_was_enabled(logged, section.name):
                out[section.name] = _compute_and_render(section, ic_context)

    style_tables = {
        name: table
        for name, table in base_tables.items()
        if name.startswith("factor_style_exposure_corr_")
    }
    if style_tables and _section_was_enabled(logged, "factor_style_exposure"):
        tables = dict(style_tables)
        for name, table in style_tables.items():
            method = name.removeprefix("factor_style_exposure_corr_")
            tables[f"factor_style_exposure_corr_summary_{method}"] = compute_exposure_corr_summary(table)
        result = SectionResult(name="factor_style_exposure", status="success", tables=tables)
        out[result.name] = FactorStyleExposureSection().render(context, result)

    _rebuild_neutralized_section(
        out,
        base_tables,
        context,
        logged,
        section_name="style_neutralized_ic",
        prefix="style_neutralized_ic_",
        section=StyleNeutralizedICSection(),
    )
    _rebuild_neutralized_section(
        out,
        base_tables,
        context,
        logged,
        section_name="style_industry_neutralized_ic",
        prefix="style_industry_neutralized_ic_",
        section=StyleIndustryNeutralizedICSection(),
    )

    style_daily = base_tables.get("group_style_exposure_daily")
    industry_daily = base_tables.get("group_industry_exposure_daily")
    if (style_daily is not None or industry_daily is not None) and _section_was_enabled(
        logged, "group_exposure_diagnostics"
    ):
        style_daily = style_daily if style_daily is not None else pd.DataFrame()
        industry_daily = industry_daily if industry_daily is not None else pd.DataFrame()
        result = SectionResult(
            name="group_exposure_diagnostics",
            status="success",
            tables={
                "group_style_exposure_daily": style_daily,
                "group_style_exposure_summary": compute_exposure_value_summary(
                    style_daily, exposure_level="exposure"
                ),
                "group_industry_exposure_daily": industry_daily,
                "group_industry_exposure_summary": compute_exposure_value_summary(
                    industry_daily, exposure_level="industry"
                ),
            },
        )
        out[result.name] = GroupExposureDiagnosticsSection().render(context, result)

    daily_group = base_tables.get("daily_group_returns")
    if daily_group is not None:
        group_context = {**context, "daily_group_returns": daily_group}
        if _section_was_enabled(logged, "group_return"):
            out["group_return"] = _compute_and_render(GroupReturnSection(), group_context)
        if _section_was_enabled(logged, "layered_group_return"):
            out["layered_group_return"] = _compute_and_render(LayeredGroupReturnSection(), group_context)
        long_short_context = {
            **group_context,
            "daily_long_short_returns": compute_long_short_returns(daily_group),
        }
        if _section_was_enabled(logged, "long_short"):
            out["long_short"] = _compute_and_render(LongShortSection(), long_short_context)
        if _section_was_enabled(logged, "performance_metrics"):
            out["performance_metrics"] = _compute_and_render(PerformanceMetricsSection(), long_short_context)

    within_daily = base_tables.get("within_industry_daily_group_returns")
    if within_daily is not None and _section_was_enabled(logged, "within_industry_group_return"):
        result = SectionResult(
            name="within_industry_group_return",
            status="success",
            tables={"within_industry_daily_group_returns": within_daily},
        )
        out[result.name] = WithinIndustryGroupReturnSection().render(context, result)

    membership = base_tables.get("daily_group_membership_change")
    if membership is not None and _section_was_enabled(logged, "group_turnover"):
        summary = compute_turnover_summary(membership)
        edge = summary.reindex(["G1", "G10"]).copy()
        if {"G1", "G10"}.issubset(membership.columns):
            edge_avg = membership[["G1", "G10"]].mean(axis=1).to_frame("edge_avg")
            edge = pd.concat([edge, compute_turnover_summary(edge_avg)])
        result = SectionResult(
            name="group_turnover",
            status="success",
            tables={
                "daily_group_turnover": membership,
                "group_turnover_summary": summary,
                "group_turnover_edge_summary": edge,
                "daily_group_membership_change": membership,
                "group_membership_change_summary": summary,
                "group_membership_change_edge_summary": edge,
            },
        )
        out[result.name] = GroupTurnoverSection().render(context, result)

    return _merge_failed_section_status(out, logged)


def _rebuild_neutralized_section(
    out: dict[str, SectionResult],
    base_tables: dict[str, pd.DataFrame],
    context: dict,
    logged: dict[str, dict],
    *,
    section_name: str,
    prefix: str,
    section,
) -> None:
    if not _section_was_enabled(logged, section_name):
        return
    daily_by_method = {
        name.removeprefix(prefix): table
        for name, table in base_tables.items()
        if name.startswith(prefix)
    }
    if not daily_by_method:
        return
    tables = {}
    for method, daily in daily_by_method.items():
        tables[f"{prefix}{method}"] = daily
        tables[f"cumulative_{prefix}{method}"] = daily.cumsum()
        tables[f"{prefix}stats_{method}"] = compute_ic_stats(
            daily,
            horizon_days=context.get("return_horizon_days"),
        )
    result = SectionResult(name=section_name, status="success", tables=tables)
    out[section_name] = section.render(context, result)


def _compute_and_render(section, context: dict) -> SectionResult:
    result = section.compute(context)
    return section.render(context, result)


def _section_was_enabled(logged: dict[str, dict], section_name: str) -> bool:
    return not logged or section_name in logged


def _merge_failed_section_status(
    rebuilt: dict[str, SectionResult],
    logged: dict[str, dict],
) -> dict[str, SectionResult]:
    if not logged:
        return rebuilt
    ordered: dict[str, SectionResult] = {}
    for name, payload in logged.items():
        if name in rebuilt:
            result = rebuilt[name]
            result.warnings = list(dict.fromkeys([*payload.get("warnings", []), *result.warnings]))
            ordered[name] = result
        else:
            ordered[name] = SectionResult(
                name=name,
                status=payload.get("status", "failed"),
                error=payload.get("error"),
                warnings=list(payload.get("warnings", [])),
            )
    return ordered


def _normalize_daily_index(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.index = pd.DatetimeIndex(pd.to_datetime(out.index), name="trade_date")
    return out.sort_index()


def _slice_datetime_index(frame: pd.DataFrame, *, start_date=None, end_date=None) -> pd.DataFrame:
    out = frame
    if start_date is not None:
        out = out.loc[out.index >= pd.Timestamp(start_date)]
    if end_date is not None:
        out = out.loc[out.index <= pd.Timestamp(end_date)]
    return out.copy()


def _slice_trade_date_level(frame: pd.DataFrame, *, start_date=None, end_date=None) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    if not isinstance(frame.index, pd.MultiIndex) or "trade_date" not in frame.index.names:
        raise ValueError("每日分组收益必须使用包含 trade_date 的 MultiIndex")
    dates = pd.to_datetime(frame.index.get_level_values("trade_date"))
    mask = pd.Series(True, index=range(len(frame)))
    if start_date is not None:
        mask &= dates >= pd.Timestamp(start_date)
    if end_date is not None:
        mask &= dates <= pd.Timestamp(end_date)
    return frame.iloc[mask.to_numpy()].copy()


def _rebase_additive_curve(curve: pd.DataFrame) -> pd.DataFrame:
    out = curve.copy()
    for column in out.columns:
        valid = out[column].dropna()
        if not valid.empty:
            out[column] = out[column] - valid.iloc[0]
    return out


def _rebase_multiplicative_curve(curve: pd.DataFrame) -> pd.DataFrame:
    out = curve.copy()
    for column in out.columns:
        valid = out[column].dropna()
        if not valid.empty and valid.iloc[0] != 0:
            out[column] = out[column] / valid.iloc[0]
    return out


def _infer_plot_index(base_tables: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    for name in ("data_quality", "daily_ic_spearman", "daily_ic_pearson"):
        table = base_tables.get(name)
        if table is not None and not table.empty:
            return pd.DatetimeIndex(pd.to_datetime(table.index), name="trade_date")
    group = base_tables.get("daily_group_returns")
    if group is not None and not group.empty and isinstance(group.index, pd.MultiIndex):
        return pd.DatetimeIndex(
            sorted(pd.to_datetime(group.index.get_level_values("trade_date")).unique()),
            name="trade_date",
        )
    return pd.DatetimeIndex([], name="trade_date")


def _normalize_horizon_colors(raw_colors) -> dict[int, str] | None:
    if not raw_colors:
        return None
    normalized = {}
    for key, value in raw_colors.items():
        try:
            normalized[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    return normalized or None
