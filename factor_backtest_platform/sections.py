from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

import pandas as pd

from factor_backtest_platform.config import DEFAULT_HORIZON_COLORS

from factor_backtest_platform.analytics import (
    compute_daily_ic,
    compute_daily_equivalent_long_short_returns,
    compute_exposure_corr_summary,
    compute_factor_style_exposure_corr,
    compute_factor_style_exposure_corr_from_panel,
    compute_group_exposure_diagnostics,
    compute_group_exposure_diagnostics_from_panel,
    compute_group_turnover,
    compute_ic_stats,
    compute_neutralized_ic_by_exposure_panel,
    compute_performance_metrics,
    compute_yearly_ic_stats,
    compute_within_industry_group_returns,
    compute_within_industry_group_returns_from_panel,
    neutralize_factor_by_exposure,
    neutralize_factor_by_exposure_panel,
)
from factor_backtest_platform.returns import return_label, return_slug, sort_return_labels

LINE_FIGSIZE = (14, 6)
BAR_FIGSIZE = (14, 6)
BAR_WIDTH = 0.85


@dataclass
class SectionResult:
    name: str
    status: str
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    plots: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


class ReportSection:
    name = "base"
    dependencies: list[str] = []

    def compute(self, context) -> SectionResult:
        raise NotImplementedError

    def render(self, context, result: SectionResult) -> SectionResult:
        return result


class DataQualitySection(ReportSection):
    name = "data_quality"

    def compute(self, context) -> SectionResult:
        quality = context["data_quality"]
        count_cols = [col for col in ("pool_stock_count", "valid_factor_count") if col in quality.columns]
        ratio_cols = [col for col in quality.columns if col.endswith("_ratio")]
        tables = {"data_quality": quality}
        if count_cols:
            tables["data_quality_counts"] = quality[count_cols]
        if ratio_cols:
            tables["data_quality_ratios"] = quality[ratio_cols]
        return SectionResult(name=self.name, status="success", tables=tables)

    def render(self, context, result: SectionResult) -> SectionResult:
        if "data_quality_counts" in result.tables:
            _plot_lines(
                result.tables["data_quality_counts"],
                context["plots_dir"] / "data_quality_counts.png",
                "Factor Coverage Counts",
                result,
                enabled=_plots_enabled(context),
            )
        if "data_quality_ratios" in result.tables:
            _plot_lines(
                result.tables["data_quality_ratios"],
                context["plots_dir"] / "data_quality_ratios.png",
                "Factor Coverage and Invalid Value Ratios",
                result,
                enabled=_plots_enabled(context),
                ylim=(0, 1),
            )
        return result


class CumulativeICSection(ReportSection):
    name = "cumulative_ic"

    def compute(self, context) -> SectionResult:
        daily_ic_by_method = context.get("daily_ic_by_method") or {"spearman": context["daily_ic"]}
        tables = {}
        warnings = []
        for method, ic in daily_ic_by_method.items():
            tables[f"daily_ic_{method}"] = ic
            tables[f"cumulative_ic_{method}"] = ic.cumsum()
            stats = compute_ic_stats(
                ic,
                horizon_days=context.get("return_horizon_days"),
            )
            tables[f"ic_stats_{method}"] = stats
            missing_horizon = stats.index[stats["hac_status"] == "missing_horizon_days"].tolist()
            if missing_horizon:
                warnings.append(
                    f"{method} HAC statistics are unavailable without horizon_days: "
                    + ", ".join(map(str, missing_horizon))
                )
        if "spearman" in daily_ic_by_method:
            tables["daily_ic"] = tables["daily_ic_spearman"]
            tables["cumulative_ic"] = tables["cumulative_ic_spearman"]
            tables["ic_stats"] = tables["ic_stats_spearman"]
        elif daily_ic_by_method:
            first_method = next(iter(daily_ic_by_method))
            tables["daily_ic"] = tables[f"daily_ic_{first_method}"]
            tables["cumulative_ic"] = tables[f"cumulative_ic_{first_method}"]
            tables["ic_stats"] = tables[f"ic_stats_{first_method}"]
        return SectionResult(name=self.name, status="success", tables=tables, warnings=warnings)

    def render(self, context, result: SectionResult) -> SectionResult:
        methods = _ic_methods_from_result(result, "cumulative_ic")
        if not methods:
            methods = list((context.get("daily_ic_by_method") or {"spearman": context.get("daily_ic")}).keys())
        for method in methods:
            table_name = f"cumulative_ic_{method}"
            if table_name not in result.tables:
                continue
            title = f"Cumulative {_ic_method_label(method)}"
            _plot_lines(
                result.tables[table_name],
                context["plots_dir"] / f"cumulative_ic_{method}.png",
                title,
                result,
                enabled=_plots_enabled(context),
                horizon_colors=context.get("horizon_colors"),
            )
        return result


class YearlyICSection(ReportSection):
    name = "yearly_ic"

    def compute(self, context) -> SectionResult:
        daily_ic_by_method = context.get("daily_ic_by_method") or {"spearman": context["daily_ic"]}
        tables = {}
        warnings = []
        for method, ic in daily_ic_by_method.items():
            yearly = compute_yearly_ic_stats(
                ic,
                horizon_days=context.get("return_horizon_days"),
                min_days=context.get("yearly_ic_min_days", 60),
                include_partial_year=context.get("yearly_ic_include_partial_year", True),
            )
            tables[f"yearly_ic_stats_{method}"] = yearly
            if not yearly.empty and "meets_min_days" in yearly.columns:
                insufficient = yearly.index[~yearly["meets_min_days"].astype(bool)].tolist()
                if insufficient:
                    warnings.append(
                        f"{method} yearly IC has {len(insufficient)} year-horizon rows below the minimum valid-day threshold"
                    )
        return SectionResult(name=self.name, status="success", tables=tables, warnings=warnings)

    def render(self, context, result: SectionResult) -> SectionResult:
        for method in _ic_methods_from_result(result, "yearly_ic_stats"):
            table_name = f"yearly_ic_stats_{method}"
            yearly = result.tables.get(table_name, pd.DataFrame())
            if yearly.empty:
                continue
            mean_ic = yearly["ic_mean"].unstack("horizon")
            mean_ic = mean_ic.reindex(columns=sort_return_labels(mean_ic.columns))
            result.tables[f"yearly_mean_ic_{method}"] = mean_ic
            _plot_lines(
                mean_ic,
                context["plots_dir"] / f"yearly_mean_ic_{method}.png",
                f"Yearly Mean {_ic_method_label(method)}",
                result,
                enabled=_plots_enabled(context),
                horizon_colors=context.get("horizon_colors"),
                linewidth=1.8,
            )
        return result


class ICOverviewSection(ReportSection):
    name = "ic_overview"

    def compute(self, context) -> SectionResult:
        daily_ic_by_method = context.get("daily_ic_by_method") or {"spearman": context["daily_ic"]}
        tables = {}
        for method, ic in daily_ic_by_method.items():
            tables[f"ic_overview_{method}"] = _ic_overview_table(ic)
        if "spearman" in daily_ic_by_method:
            tables["ic_overview"] = tables["ic_overview_spearman"]
        elif daily_ic_by_method:
            first_method = next(iter(daily_ic_by_method))
            tables["ic_overview"] = tables[f"ic_overview_{first_method}"]
        return SectionResult(name=self.name, status="success", tables=tables)

    def render(self, context, result: SectionResult) -> SectionResult:
        methods = _ic_methods_from_result(result, "ic_overview")
        if not methods:
            methods = list((context.get("daily_ic_by_method") or {"spearman": context.get("daily_ic")}).keys())
        for method in methods:
            table_name = f"ic_overview_{method}"
            if table_name not in result.tables:
                continue
            _plot_lines(
                result.tables[table_name],
                context["plots_dir"] / f"ic_overview_{method}.png",
                f"20-Day Moving Average {_ic_method_label(method)}",
                result,
                enabled=_plots_enabled(context),
                horizon_colors=context.get("horizon_colors"),
            )
        return result


def _ic_overview_table(ic: pd.DataFrame) -> pd.DataFrame:
    cols = []
    if "ic_20d" in ic.columns:
        cols.append("ic_20d")
    external_cols = [col for col in ic.columns if not re.fullmatch(r"ic_\d+d", str(col))]
    cols.extend(col for col in external_cols if col not in cols)
    if not cols:
        cols = [ic.columns[-1]]
    return ic[cols].rolling(20, min_periods=1).mean()


def _ic_method_label(method: str) -> str:
    if method == "spearman":
        return "Spearman RankIC"
    if method == "pearson":
        return "Pearson IC"
    return f"{method.title()} IC"


def _ic_methods_from_result(result: SectionResult, table_prefix: str) -> list[str]:
    methods = []
    prefix = f"{table_prefix}_"
    for table_name in result.tables:
        if table_name.startswith(prefix):
            method = table_name.removeprefix(prefix)
            if method not in methods:
                methods.append(method)
    return methods


class FactorStyleExposureSection(ReportSection):
    name = "factor_style_exposure"

    def compute(self, context) -> SectionResult:
        risk_exposure = context.get("risk_exposure")
        risk_panel = context.get("risk_exposure_panel")
        if risk_exposure is None and risk_panel is None:
            return SectionResult(name=self.name, status="success", warnings=["risk exposure data is not configured"])
        if risk_panel is not None:
            corr_by_method = compute_factor_style_exposure_corr_from_panel(
                context["factor"],
                risk_panel,
                min_stocks=context.get("min_ic_stocks", 30),
                methods=context.get("ic_methods", ["spearman"]),
            )
        else:
            corr_by_method = compute_factor_style_exposure_corr(
                context["factor"],
                risk_exposure,
                min_stocks=context.get("min_ic_stocks", 30),
                methods=context.get("ic_methods", ["spearman"]),
            )
        tables = {}
        for method, corr in corr_by_method.items():
            tables[f"factor_style_exposure_corr_{method}"] = corr
            tables[f"factor_style_exposure_corr_summary_{method}"] = compute_exposure_corr_summary(corr)
        return SectionResult(name=self.name, status="success", tables=tables)

    def render(self, context, result: SectionResult) -> SectionResult:
        prefix = "factor_style_exposure_corr_"
        methods = [
            table_name.removeprefix(prefix)
            for table_name in result.tables
            if table_name.startswith(prefix) and not table_name.startswith("factor_style_exposure_corr_summary_")
        ]
        for method in methods:
            table_name = f"factor_style_exposure_corr_{method}"
            if table_name not in result.tables:
                continue
            _plot_lines(
                result.tables[table_name],
                context["plots_dir"] / f"factor_style_exposure_corr_{method}.png",
                f"Factor Style Exposure Correlation {method.title()}",
                result,
                enabled=_plots_enabled(context),
            )
        return result


class StyleNeutralizedICSection(ReportSection):
    name = "style_neutralized_ic"

    def compute(self, context) -> SectionResult:
        risk_exposure = context.get("risk_exposure")
        risk_panel = context.get("risk_exposure_panel")
        if risk_exposure is None and risk_panel is None:
            return SectionResult(name=self.name, status="success", warnings=["risk exposure data is not configured"])
        if risk_panel is not None:
            ic_by_method, warnings = compute_neutralized_ic_by_exposure_panel(
                context["factor"],
                context["future_returns"],
                risk_panel,
                include_styles=True,
                include_industries=False,
                min_stocks=context.get("min_ic_stocks", 30),
                methods=context.get("ic_methods", ["spearman"]),
            )
            neutralized = None
        else:
            neutralized, warnings = neutralize_factor_by_exposure(
                context["factor"],
                risk_exposure,
                include_styles=True,
                include_industries=False,
                min_stocks=context.get("min_ic_stocks", 30),
            )
            ic_by_method = compute_daily_ic(
                neutralized,
                context["future_returns"],
                min_stocks=context.get("min_ic_stocks", 30),
                methods=context.get("ic_methods", ["spearman"]),
            )
        tables = {}
        if context.get("write_neutralized_factors", False):
            if neutralized is None:
                neutralized, extra_warnings = neutralize_factor_by_exposure_panel(
                    context["factor"],
                    risk_panel,
                    include_styles=True,
                    include_industries=False,
                    min_stocks=context.get("min_ic_stocks", 30),
                )
                warnings.extend(warning for warning in extra_warnings if warning not in warnings)
            tables["style_neutralized_factor"] = neutralized
        for method, ic in ic_by_method.items():
            tables[f"style_neutralized_ic_{method}"] = ic
            tables[f"cumulative_style_neutralized_ic_{method}"] = ic.cumsum()
            tables[f"style_neutralized_ic_stats_{method}"] = compute_ic_stats(
                ic,
                horizon_days=context.get("return_horizon_days"),
            )
        return SectionResult(name=self.name, status="success", tables=tables, warnings=warnings)

    def render(self, context, result: SectionResult) -> SectionResult:
        for method in _ic_methods_from_result(result, "cumulative_style_neutralized_ic"):
            table_name = f"cumulative_style_neutralized_ic_{method}"
            if table_name not in result.tables:
                continue
            _plot_lines(
                result.tables[table_name],
                context["plots_dir"] / f"cumulative_style_neutralized_ic_{method}.png",
                f"Cumulative Style Neutralized {_ic_method_label(method)}",
                result,
                enabled=_plots_enabled(context),
                horizon_colors=context.get("horizon_colors"),
            )
        return result


class StyleIndustryNeutralizedICSection(ReportSection):
    name = "style_industry_neutralized_ic"

    def compute(self, context) -> SectionResult:
        risk_exposure = context.get("risk_exposure")
        risk_panel = context.get("risk_exposure_panel")
        if risk_exposure is None and risk_panel is None:
            return SectionResult(name=self.name, status="success", warnings=["risk exposure data is not configured"])
        if risk_panel is not None:
            ic_by_method, warnings = compute_neutralized_ic_by_exposure_panel(
                context["factor"],
                context["future_returns"],
                risk_panel,
                include_styles=True,
                include_industries=True,
                min_stocks=context.get("min_ic_stocks", 30),
                methods=context.get("ic_methods", ["spearman"]),
            )
            neutralized = None
        else:
            neutralized, warnings = neutralize_factor_by_exposure(
                context["factor"],
                risk_exposure,
                include_styles=True,
                include_industries=True,
                min_stocks=context.get("min_ic_stocks", 30),
            )
            ic_by_method = compute_daily_ic(
                neutralized,
                context["future_returns"],
                min_stocks=context.get("min_ic_stocks", 30),
                methods=context.get("ic_methods", ["spearman"]),
            )
        tables = {}
        if context.get("write_neutralized_factors", False):
            if neutralized is None:
                neutralized, extra_warnings = neutralize_factor_by_exposure_panel(
                    context["factor"],
                    risk_panel,
                    include_styles=True,
                    include_industries=True,
                    min_stocks=context.get("min_ic_stocks", 30),
                )
                warnings.extend(warning for warning in extra_warnings if warning not in warnings)
            tables["style_industry_neutralized_factor"] = neutralized
        for method, ic in ic_by_method.items():
            tables[f"style_industry_neutralized_ic_{method}"] = ic
            tables[f"cumulative_style_industry_neutralized_ic_{method}"] = ic.cumsum()
            tables[f"style_industry_neutralized_ic_stats_{method}"] = compute_ic_stats(
                ic,
                horizon_days=context.get("return_horizon_days"),
            )
        return SectionResult(name=self.name, status="success", tables=tables, warnings=warnings)

    def render(self, context, result: SectionResult) -> SectionResult:
        for method in _ic_methods_from_result(result, "cumulative_style_industry_neutralized_ic"):
            table_name = f"cumulative_style_industry_neutralized_ic_{method}"
            if table_name not in result.tables:
                continue
            _plot_lines(
                result.tables[table_name],
                context["plots_dir"] / f"cumulative_style_industry_neutralized_ic_{method}.png",
                f"Cumulative Style + Industry Neutralized {_ic_method_label(method)}",
                result,
                enabled=_plots_enabled(context),
                horizon_colors=context.get("horizon_colors"),
            )
        return result


class GroupExposureDiagnosticsSection(ReportSection):
    name = "group_exposure_diagnostics"

    def compute(self, context) -> SectionResult:
        risk_exposure = context.get("risk_exposure")
        risk_panel = context.get("risk_exposure_panel")
        if risk_exposure is None and risk_panel is None:
            return SectionResult(name=self.name, status="success", warnings=["risk exposure data is not configured"])
        if risk_panel is not None:
            diagnostics = compute_group_exposure_diagnostics_from_panel(
                context["factor"],
                risk_panel,
                n_groups=10,
                min_stocks=context.get("min_group_stocks", 10),
            )
        else:
            diagnostics = compute_group_exposure_diagnostics(
                context["factor"],
                risk_exposure,
                n_groups=10,
                min_stocks=context.get("min_group_stocks", 10),
            )
        return SectionResult(
            name=self.name,
            status="success",
            tables={
                "group_style_exposure_daily": diagnostics["style_daily"],
                "group_style_exposure_summary": diagnostics["style_summary"],
                "group_industry_exposure_daily": diagnostics["industry_daily"],
                "group_industry_exposure_summary": diagnostics["industry_summary"],
            },
        )

    def render(self, context, result: SectionResult) -> SectionResult:
        style_daily = result.tables.get("group_style_exposure_daily", pd.DataFrame())
        industry_daily = result.tables.get("group_industry_exposure_daily", pd.DataFrame())
        industry_label_rows = []
        for leg in ["G1", "G10", "G10_minus_G1", "G1_minus_pool", "G10_minus_pool"]:
            style_wide = _long_exposure_table_to_wide(style_daily, leg=leg)
            if not style_wide.empty:
                _plot_lines(
                    style_wide,
                    context["plots_dir"] / f"group_style_exposure_{_leg_slug(leg)}.png",
                    f"Group Style Exposure {leg}",
                    result,
                    enabled=_plots_enabled(context),
                    linewidth=1.3,
                )
            industry_wide = _long_exposure_table_to_wide(industry_daily, leg=leg)
            if not industry_wide.empty:
                industry_wide, label_map = _ascii_plot_columns(industry_wide, prefix="industry")
                industry_label_rows.extend(label_map)
                _plot_lines(
                    industry_wide,
                    context["plots_dir"] / f"group_industry_exposure_{_leg_slug(leg)}.png",
                    f"Group Industry Exposure {leg}",
                    result,
                    enabled=_plots_enabled(context),
                    linewidth=1.1,
                )
        if industry_label_rows:
            label_table = pd.DataFrame(industry_label_rows).drop_duplicates().set_index("plot_label").sort_index()
            result.tables["group_industry_exposure_plot_label_map"] = label_table
        return result


class GroupTurnoverSection(ReportSection):
    name = "group_turnover"

    def compute(self, context) -> SectionResult:
        daily, summary, edge = compute_group_turnover(
            context["factor"],
            n_groups=10,
            min_stocks=context.get("min_group_stocks", 10),
        )
        return SectionResult(
            name=self.name,
            status="success",
            tables={
                "daily_group_turnover": daily,
                "group_turnover_summary": summary,
                "group_turnover_edge_summary": edge,
                "daily_group_membership_change": daily,
                "group_membership_change_summary": summary,
                "group_membership_change_edge_summary": edge,
            },
        )

    def render(self, context, result: SectionResult) -> SectionResult:
        daily = result.tables.get("daily_group_turnover", pd.DataFrame())
        if not daily.empty:
            _plot_lines(
                daily,
                context["plots_dir"] / "group_turnover.png",
                "10-Group Daily Membership Change",
                result,
                enabled=_plots_enabled(context),
                colors=_group_colors(len(daily.columns)),
                linewidth=1.4,
                zero_line=False,
            )
            edge_cols = [col for col in ["G1", "G10"] if col in daily.columns]
            if edge_cols:
                edge_daily = daily.loc[:, edge_cols].copy()
                if len(edge_cols) == 2:
                    edge_daily["edge_avg"] = edge_daily[edge_cols].mean(axis=1)
                _plot_lines(
                    edge_daily,
                    context["plots_dir"] / "group_turnover_edges.png",
                    "Daily G1 and G10 Membership Change",
                    result,
                    enabled=_plots_enabled(context),
                    linewidth=1.8,
                    zero_line=False,
                    ylim=(0, 1),
                )
        return result


class GroupReturnSection(ReportSection):
    name = "group_return"

    def compute(self, context) -> SectionResult:
        return SectionResult(name=self.name, status="success", tables={"daily_group_returns": context["daily_group_returns"]})

    def render(self, context, result: SectionResult) -> SectionResult:
        daily = result.tables["daily_group_returns"]
        if not daily.empty:
            summary = daily.groupby(["horizon", "group"])["group_return"].mean().unstack("horizon")
            summary = summary.reindex(columns=sort_return_labels(summary.columns))
            result.tables["group_return_summary"] = summary
            _plot_bars(
                summary,
                context["plots_dir"] / "group_return_bar.png",
                "10-Group Forward Returns",
                result,
                enabled=_plots_enabled(context),
                horizon_colors=context.get("horizon_colors"),
            )
            horizon_days_map = context.get("return_horizon_days", {})
            for horizon in sort_return_labels(daily.index.get_level_values("horizon").unique()):
                label = return_label(horizon)
                horizon_days = horizon_days_map.get(label) or _infer_horizon_days(horizon)
                if horizon_days is None:
                    result.warnings.append(
                        f"{label} has no horizon_days; skipped 10-group cumulative return plot to avoid misleading compounding"
                    )
                    continue
                cumulative = _group_cumulative_return_table(
                    daily,
                    horizon,
                    horizon_days=int(horizon_days),
                    plot_index=context.get("plot_index"),
                )
                slug = return_slug(horizon)
                table_name = f"group_cumulative_returns_{slug}"
                result.tables[table_name] = cumulative
                _plot_lines(
                    cumulative,
                    context["plots_dir"] / f"group_cumulative_return_{slug}.png",
                    f"10-Group Cumulative Return {label.upper()}",
                    result,
                    enabled=_plots_enabled(context),
                    colors=_group_colors(len(cumulative.columns)),
                    linewidth=1.8,
                    zero_line=False,
                )
        return result


class WithinIndustryGroupReturnSection(ReportSection):
    name = "within_industry_group_return"

    def compute(self, context) -> SectionResult:
        risk_exposure = context.get("risk_exposure")
        risk_panel = context.get("risk_exposure_panel")
        if risk_exposure is None and risk_panel is None:
            return SectionResult(name=self.name, status="success", warnings=["risk exposure data is not configured"])
        if risk_panel is not None:
            daily = compute_within_industry_group_returns_from_panel(
                context["factor"],
                context["future_returns"],
                risk_panel,
                n_groups=10,
                min_industry_stocks=context.get("min_industry_ic_stocks", 10),
            )
        else:
            daily = compute_within_industry_group_returns(
                context["factor"],
                context["future_returns"],
                risk_exposure,
                n_groups=10,
                min_industry_stocks=context.get("min_industry_ic_stocks", 10),
            )
        return SectionResult(name=self.name, status="success", tables={"within_industry_daily_group_returns": daily})

    def render(self, context, result: SectionResult) -> SectionResult:
        daily = result.tables["within_industry_daily_group_returns"]
        if not daily.empty:
            summary = daily.groupby(["horizon", "group"])["group_return"].mean().unstack("horizon")
            summary = summary.reindex(columns=sort_return_labels(summary.columns))
            result.tables["within_industry_group_return_summary"] = summary
            _plot_bars(
                summary,
                context["plots_dir"] / "within_industry_group_return_bar.png",
                "Within-Industry 10-Group Forward Returns",
                result,
                enabled=_plots_enabled(context),
                horizon_colors=context.get("horizon_colors"),
            )
            horizon_days_map = context.get("return_horizon_days", {})
            for horizon in sort_return_labels(daily.index.get_level_values("horizon").unique()):
                label = return_label(horizon)
                horizon_days = horizon_days_map.get(label) or _infer_horizon_days(horizon)
                if horizon_days is None:
                    result.warnings.append(
                        f"{label} has no horizon_days; skipped within-industry cumulative return plot"
                    )
                    continue
                cumulative = _group_cumulative_return_table(
                    daily,
                    horizon,
                    horizon_days=int(horizon_days),
                    plot_index=context.get("plot_index"),
                )
                slug = return_slug(horizon)
                result.tables[f"within_industry_group_cumulative_returns_{slug}"] = cumulative
                _plot_lines(
                    cumulative,
                    context["plots_dir"] / f"within_industry_group_cumulative_return_{slug}.png",
                    f"Within-Industry 10-Group Cumulative Return {label.upper()}",
                    result,
                    enabled=_plots_enabled(context),
                    colors=_group_colors(len(cumulative.columns)),
                    linewidth=1.8,
                    zero_line=False,
                )
        return result


class LayeredGroupReturnSection(ReportSection):
    name = "layered_group_return"

    def compute(self, context) -> SectionResult:
        daily = context["daily_group_returns"]
        windows = context.get("group_return_windows", {})
        if daily.empty:
            return SectionResult(name=self.name, status="success", tables={"layered_group_return_summary": pd.DataFrame()})
        records = []
        dates = daily.index.get_level_values("trade_date")
        max_date = dates.max()
        for window_name, window_size in windows.items():
            window_dates = sorted(pd.unique(dates))[-int(window_size) :]
            window_data = daily.loc[daily.index.get_level_values("trade_date").isin(window_dates)]
            summary = window_data.groupby(["horizon", "group"])["group_return"].mean()
            for (horizon, group), value in summary.items():
                records.append(
                    {
                        "window": window_name,
                        "window_size": int(window_size),
                        "end_date": max_date,
                        "horizon": horizon,
                        "group": int(group),
                        "group_return": float(value),
                    }
                )
        table = (
            pd.DataFrame(records).set_index(["window", "horizon", "group"])
            if records
            else pd.DataFrame(columns=["window_size", "end_date", "group_return"])
        )
        return SectionResult(name=self.name, status="success", tables={"layered_group_return_summary": table})

    def render(self, context, result: SectionResult) -> SectionResult:
        summary = result.tables["layered_group_return_summary"]
        if summary.empty:
            return result
        for window in summary.index.get_level_values("window").unique():
            window_data = summary[summary.index.get_level_values("window") == window]
            window_summary = window_data["group_return"].unstack("horizon")
            window_summary = window_summary.sort_index().reindex(columns=sort_return_labels(window_summary.columns))
            _plot_bars(
                window_summary,
                context["plots_dir"] / f"group_return_bar_{window}.png",
                f"10-Group Forward Returns {window}",
                result,
                enabled=_plots_enabled(context),
                horizon_colors=context.get("horizon_colors"),
            )
        return result


class LongShortSection(ReportSection):
    name = "long_short"

    def compute(self, context) -> SectionResult:
        daily = context["daily_long_short_returns"]
        legacy_cumulative = daily.cumsum() if not daily.empty else daily
        daily_equivalent, skipped = compute_daily_equivalent_long_short_returns(
            context["daily_group_returns"],
            horizon_days=context.get("return_horizon_days"),
        )
        plot_index = context.get("plot_index")
        if plot_index is not None:
            cumulative_daily_equivalent = daily_equivalent.reindex(pd.DatetimeIndex(plot_index)).fillna(0.0).cumsum()
        else:
            cumulative_daily_equivalent = daily_equivalent.fillna(0.0).cumsum()
        warnings = []
        if skipped:
            warnings.append(
                "Skipped daily-equivalent long-short curves without horizon_days: " + ", ".join(skipped)
            )
        return SectionResult(
            name=self.name,
            status="success",
            tables={
                "daily_long_short_returns": daily,
                "cumulative_long_short_returns": legacy_cumulative,
                "daily_equivalent_long_short_returns": daily_equivalent,
                "cumulative_daily_equivalent_long_short_returns": cumulative_daily_equivalent,
            },
            warnings=warnings,
        )

    def render(self, context, result: SectionResult) -> SectionResult:
        _plot_lines(
            result.tables["cumulative_daily_equivalent_long_short_returns"],
            context["plots_dir"] / "long_short_curve.png",
            "Cumulative Daily-Equivalent Long-Short Spread (Diagnostic)",
            result,
            enabled=_plots_enabled(context),
            horizon_colors=context.get("horizon_colors"),
        )
        return result


class PerformanceMetricsSection(ReportSection):
    name = "performance_metrics"

    def compute(self, context) -> SectionResult:
        metrics = compute_performance_metrics(
            context["daily_long_short_returns"],
            horizon_days=context.get("return_horizon_days"),
        )
        diagnostic_columns = [
            "raw_mean",
            "raw_std",
            "mean_over_std_raw",
            "positive_ratio",
            "t_stat_hac",
            "hac_lag",
            "valid_days",
            "diagnostic_max_drawdown",
            "max_drawdown_applicable",
            "not_applicable_reason",
        ]
        diagnostic = metrics.reindex(columns=diagnostic_columns)
        warnings = []
        missing_horizon = metrics.index[metrics.get("hac_status", pd.Series(dtype=str)) == "missing_horizon_days"].tolist()
        if missing_horizon:
            warnings.append(
                "HAC statistics are unavailable without horizon_days: " + ", ".join(map(str, missing_horizon))
            )
        return SectionResult(
            name=self.name,
            status="success",
            tables={
                "performance_metrics": metrics,
                "performance_diagnostics": diagnostic,
            },
            warnings=warnings,
        )


DEFAULT_SECTIONS: list[ReportSection] = [
    DataQualitySection(),
    ICOverviewSection(),
    CumulativeICSection(),
    YearlyICSection(),
    FactorStyleExposureSection(),
    StyleNeutralizedICSection(),
    StyleIndustryNeutralizedICSection(),
    GroupExposureDiagnosticsSection(),
    GroupReturnSection(),
    WithinIndustryGroupReturnSection(),
    LayeredGroupReturnSection(),
    LongShortSection(),
    GroupTurnoverSection(),
    PerformanceMetricsSection(),
]


def select_plot_title(chinese_title: str, english_title: str, has_cjk_font: bool | None = None) -> str:
    return english_title


def _plots_enabled(context: dict) -> bool:
    return bool(context.get("render_plots", True))


def _plot_lines(
    df: pd.DataFrame,
    path,
    title: str,
    result: SectionResult,
    *,
    enabled: bool = True,
    horizon_colors: dict[int, str] | None = None,
    colors: list[str] | None = None,
    ylim: tuple[float, float] | None = None,
    linewidth: float | None = None,
    zero_line: bool = True,
) -> None:
    if not enabled:
        return
    if df.empty:
        result.warnings.append(f"{title} has no plottable data")
        return
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        path.parent.mkdir(parents=True, exist_ok=True)
        plot_colors = colors if colors is not None else _colors_for_columns(df.columns, horizon_colors)
        plot_kwargs = {"figsize": LINE_FIGSIZE, "title": title, "color": plot_colors}
        if linewidth is not None:
            plot_kwargs["linewidth"] = linewidth
        ax = df.plot(**plot_kwargs)
        if zero_line:
            ax.axhline(0, color="#333333", linewidth=0.8)
        if ylim is not None:
            ax.set_ylim(*ylim)
        ax.figure.tight_layout()
        ax.figure.savefig(path, dpi=150)
        plt.close(ax.figure)
        result.plots[path.name] = str(path)
    except Exception as exc:
        result.warnings.append(f"{title} plotting failed: {exc}")


def _plot_bars(
    df: pd.DataFrame,
    path,
    title: str,
    result: SectionResult,
    *,
    enabled: bool = True,
    horizon_colors: dict[int, str] | None = None,
) -> None:
    if not enabled:
        return
    if df.empty:
        result.warnings.append(f"{title} has no plottable data")
        return
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        path.parent.mkdir(parents=True, exist_ok=True)
        colors = _colors_for_columns(df.columns, horizon_colors)
        ax = df.plot(kind="bar", figsize=BAR_FIGSIZE, title=title, color=colors, width=BAR_WIDTH)
        ax.axhline(0, color="#333333", linewidth=0.8)
        ax.figure.tight_layout()
        ax.figure.savefig(path, dpi=150)
        plt.close(ax.figure)
        result.plots[path.name] = str(path)
    except Exception as exc:
        result.warnings.append(f"{title} plotting failed: {exc}")


def _colors_for_columns(columns, horizon_colors: dict[int, str] | None = None) -> list[str]:
    colors = horizon_colors or DEFAULT_HORIZON_COLORS
    fallback = ["#4C78A8", "#72B7B2", "#F58518", "#54A24B", "#B279A2", "#E45756", "#9D755D", "#BAB0AC"]
    out = []
    for idx, col in enumerate(columns):
        horizon = _extract_horizon(col)
        out.append(colors.get(horizon, fallback[idx % len(fallback)]))
    return out


def _group_cumulative_return_table(
    daily_group_returns: pd.DataFrame,
    horizon,
    horizon_days: int,
    plot_index: pd.Index | None = None,
) -> pd.DataFrame:
    horizon_data = daily_group_returns.xs(horizon, level="horizon")["group_return"]
    wide = horizon_data.unstack("group").sort_index().sort_index(axis=1)
    wide = wide.rename(columns={group: f"G{int(group)}" for group in wide.columns})
    if plot_index is not None:
        wide = wide.reindex(pd.DatetimeIndex(pd.to_datetime(plot_index)))
    daily_equivalent = _horizon_return_to_daily_equivalent(wide, horizon_days)
    return (1.0 + daily_equivalent.fillna(0.0)).cumprod()


def _long_exposure_table_to_wide(daily: pd.DataFrame, *, leg: str) -> pd.DataFrame:
    if daily.empty or not isinstance(daily.index, pd.MultiIndex) or "leg" not in daily.index.names:
        return pd.DataFrame()
    if leg not in daily.index.get_level_values("leg"):
        return pd.DataFrame()
    value = daily.xs(leg, level="leg")["value"]
    exposure_level = value.index.names[-1]
    wide = value.unstack(exposure_level).sort_index()
    return wide


def _leg_slug(leg: str) -> str:
    return leg.lower().replace("_", "-").replace("-", "_")


def _ascii_plot_columns(df: pd.DataFrame, *, prefix: str) -> tuple[pd.DataFrame, list[dict[str, str]]]:
    renamed = df.copy()
    used = set()
    columns = []
    rows = []
    for idx, col in enumerate(df.columns, start=1):
        text = str(col)
        if text.isascii():
            label = text
        else:
            label = f"{prefix}_{idx:02d}"
        while label in used:
            label = f"{prefix}_{idx:02d}_{len(used) + 1}"
        used.add(label)
        columns.append(label)
        rows.append({"plot_label": label, "industry": text})
    renamed.columns = columns
    return renamed, rows


def _horizon_return_to_daily_equivalent(returns: pd.DataFrame, horizon: int) -> pd.DataFrame:
    if horizon <= 1:
        return returns
    valid = returns.where(returns > -1.0)
    return (1.0 + valid) ** (1.0 / horizon) - 1.0


def _infer_horizon_days(value) -> int | None:
    horizon = _extract_horizon(value)
    return int(horizon) if horizon is not None else None


def _group_colors(n: int) -> list[str]:
    palette = [
        "#9E0142",
        "#D53E4F",
        "#F46D43",
        "#FDAE61",
        "#FEE08B",
        "#E0F3F8",
        "#ABD9E9",
        "#74ADD1",
        "#4575B4",
        "#313695",
    ]
    return [palette[idx % len(palette)] for idx in range(n)]


def _extract_horizon(value) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    match = re.search(r"(\d+)d\b", str(value))
    return int(match.group(1)) if match else None
