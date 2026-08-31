"""Platform 包身份与安装状态检查入口。"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from factor_backtest_platform.version import __version__


DISTRIBUTION_NAME = "factor-backtest-platform"


def main() -> None:
    """输出 distribution、版本和当前导入路径，便于部署后核对。"""
    try:
        installed_version = version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        installed_version = "未安装（当前从源码目录导入）"

    package_path = Path(__file__).resolve().parent
    print(f"distribution: {DISTRIBUTION_NAME}")
    print(f"distribution_version: {installed_version}")
    print(f"package_version: {__version__}")
    print(f"import_name: factor_backtest_platform")
    print(f"package_path: {package_path}")


if __name__ == "__main__":
    main()
