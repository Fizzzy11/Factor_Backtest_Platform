from importlib import metadata
from pathlib import Path
import subprocess
import sys
import tomllib

import factor_backtest_platform


REQUIRED_PUBLIC_API = {
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
    "load_backtest_result",
    "load_risk_exposure_from_csv",
    "normalize_external_returns",
    "normalize_return_dataframe",
    "render_factor_backtest_report",
    "resolve_risk_exposure",
    "run_factor_backtest",
    "run_factor_backtest_data",
    "run_factor_backtest_minimal",
}


def test_platform_uses_independent_distribution_and_import_names():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["name"] == "factor-backtest-platform"
    assert project["project"]["version"] == "1.0.1"
    assert project["tool"]["setuptools"]["packages"]["find"]["include"] == ["factor_backtest_platform*"]
    assert (root / "factor_backtest_platform" / "__init__.py").is_file()
    assert not (root / "factor_backtest").exists()


def test_platform_public_api_is_available_from_new_import_name():
    assert factor_backtest_platform.__version__ == "1.0.1"
    assert REQUIRED_PUBLIC_API <= set(factor_backtest_platform.__all__)
    for name in REQUIRED_PUBLIC_API:
        assert hasattr(factor_backtest_platform, name)


def test_distribution_metadata_uses_platform_name():
    assert metadata.version("factor-backtest-platform") == "1.0.1"


def test_module_entrypoint_reports_platform_identity():
    completed = subprocess.run(
        [sys.executable, "-m", "factor_backtest_platform"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert "distribution: factor-backtest-platform" in completed.stdout
    assert "distribution_version: 1.0.1" in completed.stdout
    assert "package_version: 1.0.1" in completed.stdout
    assert "import_name: factor_backtest_platform" in completed.stdout
