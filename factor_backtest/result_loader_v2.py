from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from factor_backtest.io import read_json, read_table
from factor_backtest.result_store import RESULT_SCHEMA_VERSION, read_result_table, resolve_latest_run
from factor_backtest.result_views import (
    GroupReturnView,
    ICView,
    QualityView,
    build_group_return_view,
    build_ic_view,
    build_quality_view,
    rebuild_report_sections,
)


@dataclass(frozen=True)
class LoadedBacktestResult:
    run_dir: Path
    meta: dict
    log: dict
    manifest: dict | None = None

    @property
    def schema_version(self) -> str:
        return str((self.manifest or {}).get("schema_version", "1.0"))

    @property
    def is_schema_v2(self) -> bool:
        return self.schema_version == RESULT_SCHEMA_VERSION

    def available_pools(self) -> list[str]:
        if self.is_schema_v2:
            pools = self.manifest.get("selected_pools") or self.meta.get("selected_pools", [])
            return [str(pool) for pool in pools]
        pools_dir = self.run_dir / "pools"
        return sorted(path.name for path in pools_dir.iterdir() if path.is_dir()) if pools_dir.exists() else []

    def available_tables(self) -> list[str]:
        if self.is_schema_v2:
            return sorted((self.manifest.get("tables") or {}).keys())
        names = set()
        for pool in self.available_pools():
            names.update(path.stem for path in (self.pool_dir(pool) / "tables").glob("*.csv"))
        return sorted(names)

    def pool_dir(self, pool: str) -> Path:
        return self.run_dir / "pools" / pool

    def table_path(self, pool: str, table_name: str) -> Path:
        if self.is_schema_v2:
            resolved_name = self._resolve_base_table_name(table_name)
            table_meta = (self.manifest.get("tables") or {}).get(resolved_name)
            if table_meta is None:
                raise KeyError(f"Schema 2 基础结果中没有表 {table_name!r}")
            return self.run_dir / table_meta["path"]
        return self.pool_dir(pool) / "tables" / f"{table_name}.csv"

    def artifact_path(self, pool: str, artifact_name: str) -> Path:
        candidates = [
            self.run_dir / "artifacts" / pool / artifact_name,
            self.pool_dir(pool) / "artifacts" / artifact_name,
        ]
        for base in candidates:
            if base.exists():
                return base
            fallback = base.with_suffix(base.suffix + ".pkl")
            if fallback.exists():
                return fallback
        return candidates[0] if self.is_schema_v2 else candidates[1]

    def plot_path(self, pool: str, plot_name: str) -> Path:
        v2 = self.run_dir / "plots" / pool / plot_name
        legacy = self.pool_dir(pool) / "plots" / plot_name
        return v2 if self.is_schema_v2 or v2.exists() else legacy

    def read_table(
        self,
        pool: str,
        table_name: str,
        *,
        start_date=None,
        end_date=None,
    ) -> pd.DataFrame:
        if not self.is_schema_v2:
            return read_table(self.table_path(pool, table_name))
        resolved_name = self._resolve_base_table_name(table_name)
        table_meta = (self.manifest.get("tables") or {}).get(resolved_name)
        if table_meta is not None:
            return read_result_table(
                self.run_dir,
                table_meta,
                pool=pool,
                start_date=start_date,
                end_date=end_date,
            )
        sections = self._rebuild_pool_sections(pool)
        for result in sections.values():
            if table_name in result.tables:
                return _slice_derived_table(result.tables[table_name], start_date=start_date, end_date=end_date)
        raise KeyError(f"结果中没有表 {table_name!r}")

    def read_artifact(self, pool: str, artifact_name: str) -> pd.DataFrame:
        return read_table(self.artifact_path(pool, artifact_name))

    def ic_view(
        self,
        *,
        pool: str,
        method: str = "spearman",
        neutralization: str = "none",
        start_date=None,
        end_date=None,
    ) -> ICView:
        prefixes = {
            "none": "daily_ic_",
            "style": "style_neutralized_ic_",
            "style_industry": "style_industry_neutralized_ic_",
        }
        if neutralization not in prefixes:
            raise ValueError("neutralization must be 'none', 'style', or 'style_industry'")
        table_name = prefixes[neutralization] + str(method).lower()
        daily = self.read_table(pool, table_name)
        return build_ic_view(
            daily,
            start_date=start_date,
            end_date=end_date,
            horizon_days=self.meta.get("return_horizon_days", {}),
            yearly_min_days=int(self.meta.get("yearly_ic_min_days", 60)),
            yearly_include_partial_year=bool(self.meta.get("yearly_ic_include_partial_year", True)),
        )

    def group_return_view(
        self,
        *,
        pool: str,
        start_date=None,
        end_date=None,
        within_industry: bool = False,
    ) -> GroupReturnView:
        table_name = "within_industry_daily_group_returns" if within_industry else "daily_group_returns"
        daily = self.read_table(pool, table_name)
        return build_group_return_view(
            daily,
            start_date=start_date,
            end_date=end_date,
            horizon_days=self.meta.get("return_horizon_days", {}),
        )

    def quality_view(
        self,
        *,
        pool: str,
        start_date=None,
        end_date=None,
    ) -> QualityView:
        daily = self.read_table(pool, "data_quality")
        return build_quality_view(daily, start_date=start_date, end_date=end_date)

    def _resolve_base_table_name(self, table_name: str) -> str:
        tables = self.manifest.get("tables") or {}
        if table_name in tables:
            return table_name
        if table_name == "daily_ic":
            if "daily_ic_spearman" in tables:
                return "daily_ic_spearman"
            methods = sorted(name for name in tables if name.startswith("daily_ic_"))
            if methods:
                return methods[0]
        if table_name == "daily_group_turnover" and "daily_group_membership_change" in tables:
            return "daily_group_membership_change"
        return table_name

    def _rebuild_pool_sections(self, pool: str, *, render_plots: bool = False):
        base_tables = {
            table_name: read_result_table(self.run_dir, table_meta, pool=pool)
            for table_name, table_meta in (self.manifest.get("tables") or {}).items()
            if pool in table_meta.get("pools", [])
        }
        return rebuild_report_sections(
            base_tables,
            meta=self.meta,
            section_log=(self.log.get("sections") or {}).get(pool, {}),
            plots_dir=self.run_dir / "plots" / pool,
            render_plots=render_plots,
        )


def load_backtest_result(
    *,
    factor_name: str,
    output_root: str | Path = "/data/zhangyuan/Factor_Backtest_Platform_Result",
    run: str = "latest",
) -> LoadedBacktestResult:
    factor_root = Path(output_root) / factor_name
    run_dir = resolve_latest_run(factor_root) if run == "latest" else _resolve_named_run(factor_root, run)
    if not run_dir.exists():
        raise FileNotFoundError(f"Backtest result directory does not exist: {run_dir}")
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else None
    return LoadedBacktestResult(
        run_dir=run_dir,
        meta=read_json(run_dir / "run_meta.json"),
        log=read_json(run_dir / "run_log.json"),
        manifest=manifest,
    )


def _resolve_named_run(factor_root: Path, run: str) -> Path:
    root = factor_root.resolve()
    primary = (root / "runs" / str(run)).resolve()
    legacy_timestamp = (root / str(run)).resolve()
    if root not in primary.parents or root not in legacy_timestamp.parents:
        raise ValueError(f"run 路径越出因子目录：{run}")
    return primary if primary.exists() or not legacy_timestamp.exists() else legacy_timestamp


def _slice_derived_table(frame: pd.DataFrame, *, start_date=None, end_date=None) -> pd.DataFrame:
    if start_date is None and end_date is None:
        return frame
    if isinstance(frame.index, pd.MultiIndex) and "trade_date" in frame.index.names:
        dates = pd.to_datetime(frame.index.get_level_values("trade_date"))
    elif isinstance(frame.index, pd.DatetimeIndex):
        dates = frame.index
    else:
        return frame
    mask = pd.Series(True, index=range(len(frame)))
    if start_date is not None:
        mask &= dates >= pd.Timestamp(start_date)
    if end_date is not None:
        mask &= dates <= pd.Timestamp(end_date)
    return frame.iloc[mask.to_numpy()].copy()
