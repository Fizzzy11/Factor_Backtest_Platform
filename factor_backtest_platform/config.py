from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Literal


DEFAULT_HORIZON_COLORS = {
    1: "#4C78A8",
    5: "#F58518",
    10: "#54A24B",
    20: "#E45756",
}


def _clickhouse_env(name: str, default: str) -> str:
    """优先读取 Platform 专用变量，并兼容迁移前的变量名。"""
    platform_name = f"FACTOR_BACKTEST_PLATFORM_CLICKHOUSE_{name}"
    legacy_name = f"FACTOR_BACKTEST_CLICKHOUSE_{name}"
    return os.getenv(platform_name, os.getenv(legacy_name, default))


@dataclass(frozen=True)
class PathConfig:
    project_dir: Path = Path("/app/workspace/zhangyuan/Factor_Backtest_Platform")
    data_root: Path = Path("/data/zhangyuan")
    pool_dir: Path = Path("/data/zhangyuan/pool")
    risk_exposure_path: Path = Path("risk&industry/CNE5_Industry_daily.parquet")

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_dir", Path(self.project_dir))
        object.__setattr__(self, "data_root", Path(self.data_root))
        object.__setattr__(self, "pool_dir", Path(self.pool_dir))
        object.__setattr__(self, "risk_exposure_path", Path(self.risk_exposure_path))


@dataclass(frozen=True)
class ClickHouseConfig:
    """从环境变量读取 ClickHouse 连接信息，避免在代码库中保存凭据。

    Platform 专用变量优先；迁移期继续兼容 ``FACTOR_BACKTEST_CLICKHOUSE_*``。
    """

    host: str = field(default_factory=lambda: _clickhouse_env("HOST", "localhost"))
    port: int = field(default_factory=lambda: int(_clickhouse_env("PORT", "8123")))
    username: str = field(default_factory=lambda: _clickhouse_env("USERNAME", "default"))
    password: str = field(default_factory=lambda: _clickhouse_env("PASSWORD", ""))


@dataclass(frozen=True)
class ClickHouseTableConfig:
    ohlcv: str = "stock_data.view_stock_qfq_adjusted_ohlcv_v2"
    shares: str = "cn_stock_fundamentals.shares"
    st: str = "cn_stock_fundamentals.is_st_stock"
    suspended: str = "cn_stock_fundamentals.is_suspended"
    pool_membership: str | None = None
    factor_values: str | None = None
    risk_exposure: str | None = None


@dataclass(frozen=True)
class DataSourceConfig:
    market_data_source: Literal["clickhouse"] = "clickhouse"
    pool_source: Literal["csv", "clickhouse"] = "csv"
    factor_source: Literal["file", "clickhouse"] = "file"
    risk_exposure_source: Literal["none", "csv", "clickhouse"] = "csv"
    clickhouse: ClickHouseConfig = field(default_factory=ClickHouseConfig)
    clickhouse_tables: ClickHouseTableConfig = field(default_factory=ClickHouseTableConfig)


@dataclass(frozen=True)
class HandoffConfig:
    enabled: bool = False
    target: Literal["factor_backtest_platform"] = "factor_backtest_platform"
    output_dir: Path = Path("docs/handoffs/factor_backtest_platform")
    pool: str = "all"
    factor_direction: str = "high_is_long"
    entry: str = "next_open"
    data_asof: str | None = None
    data_access_provider: str = "legacy_clickhouse_adapter"
    data_access_mapping_version: str = "pending(it)"
    data_access_namespace: str | None = None
    data_access_logical_fields: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_dir", Path(self.output_dir))


@dataclass(frozen=True)
class CompanyDiagnosticsConfig:
    enabled: bool = False
    production_book_source: Literal["file", "clickhouse"] = "file"
    peer_book_source: Literal["file", "clickhouse"] = "file"
    regime_source: Literal["file", "clickhouse"] = "file"
    production_book_path: Path | None = None
    peer_book_path: Path | None = None
    factor_meta_path: Path | None = None
    factor_ls_pnl_path: Path | None = None
    regime_path: Path | None = None
    baseline_suite_id: str | None = None
    baseline_book_version: str | None = None
    peer_book_version: str | None = None
    peer_pool_id: str = "research+promoted"
    hypothesis_direction: Literal["high_is_long", "high_is_short", "unknown"] = "unknown"
    idea_id: str | None = None
    version: int = 1
    regime_labels: list[str] = field(default_factory=lambda: ["bull", "bear", "high_vol", "low_vol"])
    neutralization_layers: list[str] = field(default_factory=lambda: ["production_book"])
    spanning_topk: int = 20
    spanning_rounds: int = 3
    top_spanning_factors: int = 5
    topk_overlap_k: int = 50
    crowding_threshold: float = 0.7
    min_similarity_stocks: int = 30
    framework_version: str = "Factor_Backtest_Platform_1_0_0"

    def __post_init__(self) -> None:
        for name in ("production_book_path", "peer_book_path", "factor_meta_path", "factor_ls_pnl_path", "regime_path"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Path(value))
        if self.spanning_topk <= 0:
            raise ValueError("spanning_topk must be positive")
        if self.spanning_rounds <= 0:
            raise ValueError("spanning_rounds must be positive")
        if self.top_spanning_factors <= 0:
            raise ValueError("top_spanning_factors must be positive")
        if self.topk_overlap_k <= 0:
            raise ValueError("topk_overlap_k must be positive")
        if self.min_similarity_stocks <= 1:
            raise ValueError("min_similarity_stocks must be greater than 1")


@dataclass(frozen=True)
class PoolDefinition:
    path: Path | None
    display_name: str
    is_virtual: bool = False


POOL_REGISTRY: dict[str, PoolDefinition] = {
    "all": PoolDefinition(path=None, display_name="全市场", is_virtual=True),
    "hs300_pool": PoolDefinition(path=Path("hs300_pool.csv"), display_name="沪深300"),
    "zz500_pool": PoolDefinition(path=Path("zz500_pool.csv"), display_name="中证500"),
    "zz1000_pool": PoolDefinition(path=Path("zz1000_pool.csv"), display_name="中证1000"),
    "zz2000_pool": PoolDefinition(path=Path("zz2000_pool.csv"), display_name="中证2000"),
    "gz1000_pool": PoolDefinition(path=Path("gz1000_pool.csv"), display_name="国证1000"),
    "gz2000_pool": PoolDefinition(path=Path("gz2000_pool.csv"), display_name="国证2000"),
    "gzMidsmallcap_pool": PoolDefinition(
        path=Path("gzMidsmallcap_pool.csv"),
        display_name="国证中小盘800",
    ),
    "miMicrocap_pool": PoolDefinition(
        path=Path("miMicrocap_pool.csv"),
        display_name="米筐微盘",
    ),
}


@dataclass
class BacktestConfig:
    framework_version: str = "v2"
    paths: PathConfig = field(default_factory=PathConfig)
    data_sources: DataSourceConfig = field(default_factory=DataSourceConfig)
    output_root: Path | None = None
    factor_name: str | None = None
    selected_pools: list[str] = field(default_factory=lambda: ["all"])
    horizons: list[int] = field(default_factory=lambda: [1, 5, 10, 20])
    horizon_colors: dict[int, str] = field(default_factory=lambda: dict(DEFAULT_HORIZON_COLORS))
    min_listed_days: int = 120
    listed_days_source: Literal["market_data", "listing_dates"] = "market_data"
    tradability_filter: bool = True
    ic_methods: list[str] = field(default_factory=lambda: ["spearman"])
    min_ic_stocks: int = 30
    min_group_stocks: int = 10
    min_industry_ic_stocks: int = 10
    analysis_windows: list[int] = field(default_factory=lambda: [120, 250, 750])
    yearly_ic_min_days: int = 60
    yearly_ic_include_partial_year: bool = True
    group_return_windows: dict[str, int] = field(
        default_factory=lambda: {"6m": 120, "1y": 250, "3y": 750, "5y": 1250}
    )
    enabled_sections: str | list[str] = "all"
    artifact_level: Literal["full", "none"] = "none"
    winsorize_factor: bool = False
    standardize_factor: bool = False
    output_layout: Literal["latest_runs", "timestamp"] = "latest_runs"
    render_plots: bool = False
    export_static_report: bool = False
    parquet_compression: Literal["zstd", "snappy", "gzip", "none"] = "zstd"
    write_neutralized_factors: bool = False
    handoff: HandoffConfig = field(default_factory=HandoffConfig)
    diagnostics: CompanyDiagnosticsConfig = field(default_factory=CompanyDiagnosticsConfig)
    verbose: bool = True

    def __post_init__(self) -> None:
        if self.artifact_level not in {"full", "none"}:
            raise ValueError("artifact_level must be 'full' or 'none'")
        if self.parquet_compression not in {"zstd", "snappy", "gzip", "none"}:
            raise ValueError("parquet_compression must be 'zstd', 'snappy', 'gzip', or 'none'")
        if self.yearly_ic_min_days <= 0:
            raise ValueError("yearly_ic_min_days must be positive")
        if self.output_root is None:
            self.output_root = self.paths.data_root / "Factor_Backtest_Platform_Result"
        else:
            self.output_root = Path(self.output_root)
