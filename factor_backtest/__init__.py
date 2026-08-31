"""因子排序回测框架公开接口。"""

from factor_backtest.config import (
    BacktestConfig,
    ClickHouseConfig,
    ClickHouseTableConfig,
    CompanyDiagnosticsConfig,
    DataSourceConfig,
    HandoffConfig,
)
from factor_backtest.result_loader import LoadedBacktestResult, load_backtest_result
from factor_backtest.result_views import GroupReturnView, ICView, QualityView
from factor_backtest.risk_exposure import RiskExposureData, RiskExposurePanel, load_risk_exposure_from_csv, resolve_risk_exposure
from factor_backtest.returns import ReturnSpec, normalize_external_returns, normalize_return_dataframe
from factor_backtest.runner import (
    render_factor_backtest_report,
    run_factor_backtest,
    run_factor_backtest_data,
    run_factor_backtest_minimal,
)
from factor_backtest.version import __version__

__all__ = [
    "BacktestConfig",
    "ClickHouseConfig",
    "ClickHouseTableConfig",
    "CompanyDiagnosticsConfig",
    "DataSourceConfig",
    "HandoffConfig",
    "GroupReturnView",
    "ICView",
    "QualityView",
    "LoadedBacktestResult",
    "RiskExposureData",
    "RiskExposurePanel",
    "ReturnSpec",
    "__version__",
    "load_backtest_result",
    "load_risk_exposure_from_csv",
    "normalize_external_returns",
    "normalize_return_dataframe",
    "render_factor_backtest_report",
    "resolve_risk_exposure",
    "run_factor_backtest",
    "run_factor_backtest_data",
    "run_factor_backtest_minimal",
]
