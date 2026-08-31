# Factor_Backtest_Platform 1.0.0 迁移说明

## 迁移目的

本项目由 `D:\hytp\Factor_Backtest` 中尚未提交的 2.4.0 开发成果迁移而来，目标是形成独立的 `Factor_Backtest_Platform 1.0.0`。迁移只改变项目边界、产品版本、默认路径和默认输出开关，不改变金融计算口径、日频因子时间语义或结果 Schema。

## 源项目基线

- 源目录：`D:\hytp\Factor_Backtest`
- Git HEAD：`16c2a6b675d5191e3aca216adf7f8b27caab2df7`
- Git 描述：`v2.3.0-dirty`
- 已跟踪修改：14 个文件
- 未跟踪新增：8 个文件
- 源目录在迁移、测试和验收期间保持只读；本任务不执行 reset、checkout、clean、恢复或删除。

未跟踪的 2.4.0 文件已全部迁移：

```text
docs/Factor_Backtest_Dashboard_开发任务书.md
docs/superpowers/specs/2026-08-26-result-storage-and-query-design.md
factor_backtest/result_loader_v2.py
factor_backtest/result_store.py
factor_backtest/result_views.py
factor_backtest/version.py
tests/test_result_repository_integration.py
tests/test_result_store.py
```

迁移同时复制源仓库的以下 40 个 Git 已跟踪文件，因此源工作树中的 README、使用手册、示例、配置、运行器、兼容加载器和测试修改均已包含：

```text
.gitignore
README.md
docs/superpowers/plans/2026-05-19-factor-backtest-framework-implementation.md
docs/superpowers/specs/2026-05-19-factor-backtest-framework-design.md
docs/superpowers/specs/2026-06-17-company-diagnostics-design.md
docs/使用手册.md
docs/外部收益与取数配置.md
examples/run_factor_dm_20d.py
factor_backtest/__init__.py
factor_backtest/analytics.py
factor_backtest/calendar.py
factor_backtest/clickhouse_adapter.py
factor_backtest/company_diagnostics.py
factor_backtest/config.py
factor_backtest/factor_loader.py
factor_backtest/filters.py
factor_backtest/handoff.py
factor_backtest/io.py
factor_backtest/market_data.py
factor_backtest/pools.py
factor_backtest/result_loader.py
factor_backtest/returns.py
factor_backtest/risk_exposure.py
factor_backtest/runner.py
factor_backtest/sections.py
notebooks/analyze_factor_result.ipynb
pyproject.toml
tests/run_tests.py
tests/test_calendar_pools_market_data.py
tests/test_clickhouse_adapter.py
tests/test_company_diagnostics.py
tests/test_config.py
tests/test_external_returns.py
tests/test_factor_loader.py
tests/test_filters_analytics.py
tests/test_handoff.py
tests/test_io.py
tests/test_result_loader.py
tests/test_risk_exposure.py
tests/test_runner_sections.py
```

`.git` 通过不共享对象的本地克隆建立独立基线，未覆盖任何既有目标仓库。Platform 迁移阶段另外新增本说明文档和 `scripts/validate_migration_numeric_consistency.py` 数值对照工具。

以下本地依赖、缓存和产物未迁移：

```text
.installers/
.pytest_cache/
.tmp_pytest/
.tmp_test_deps/
__pycache__/
*.pyc
参考/
```

## 项目边界

| 项目 | 定位 | 本地代码 | 服务器代码 | 结果目录 |
| --- | --- | --- | --- | --- |
| Factor_Backtest | Classic 2.3.x 静态报告版 | `D:\hytp\Factor_Backtest` | `/app/workspace/zhangyuan/Factor_Backtest` | `/data/zhangyuan/Factor_Backtest_Result` |
| Factor_Backtest_Platform | 1.0.0 平台版 | `D:\hytp\Factor_Backtest_Platform` | `/app/workspace/zhangyuan/Factor_Backtest_Platform` | `/data/zhangyuan/Factor_Backtest_Platform_Result` |
| Factor_Backtest_Dashboard | Platform 只读前端 | `D:\hytp\Factor_Backtest_Dashboard` | `/app/workspace/zhangyuan/Factor_Backtest_Dashboard` | 只读 Platform 结果 |

Platform 调用脚本放在 `/app/workspace/zhangyuan/Factor_Backtest_Platform_Result/<factor_name>/`。Dashboard 默认读取 `/data/zhangyuan/Factor_Backtest_Platform_Result`，不重新运行回测，也不从 ClickHouse 或原始因子数据重算指标。

## 版本与 Schema

- Python distribution：`factor-backtest-platform`
- Python 导入名称：`factor_backtest`
- Platform 产品版本：`1.0.0`
- `manifest.json` 结果 Schema：`2.0`
- `latest.json` Schema：保持 2.4.0 开发阶段的既有定义
- 未来首个 Platform Git tag：`v1.0.0`

产品版本从 1.0.0 开始表示 Classic 与 Platform 的产品边界发生变化，不表示金融计算逻辑或结果 Schema 回退。

## 默认行为与共享输入

Platform 默认配置为：

```python
output_layout = "latest_runs"
artifact_level = "none"
parquet_compression = "zstd"
export_static_report = False
render_plots = False
```

默认运行只写紧凑 Parquet、manifest、运行元数据、日志、`latest.json` 和不可变 run，不生成 PNG 或 HTML。显式设置 `export_static_report=True`、`render_plots=True`，或随后调用 `render_factor_backtest_report(run_dir)`，可以生成静态报告。

Classic 与 Platform 只读共享 `/data/zhangyuan` 下的因子、行情、风险暴露和 `/data/zhangyuan/pool` 股票池输入。迁移不复制数据库数据，不修改数据库表，也不移动、转换或覆盖历史验证结果。

## 虚拟环境隔离

本地环境使用 `D:\hytp\Factor_Backtest_Platform\.venv`。服务器建议使用 `/app/workspace/zhangyuan/.venv_factor_backtest_platform`，并执行：

```bash
/app/workspace/zhangyuan/.venv_factor_backtest_platform/bin/python \
  -m pip install -e /app/workspace/zhangyuan/Factor_Backtest_Platform
```

安装后必须核对 distribution 名称和版本、`factor_backtest.__version__`，以及 `factor_backtest.__file__` 是否指向 Platform。Classic 与 Platform 的导入名称相同，因此不得安装到同一虚拟环境。

## 本地验收记录

2026-08-31 的本地验收结果：

- `.venv` 中 distribution 名称为 `factor-backtest-platform`，distribution 和导入包版本均为 `1.0.0`。
- `factor_backtest.__file__` 指向 `D:\hytp\Factor_Backtest_Platform\factor_backtest\__init__.py`。
- `pytest -q`：133 个测试全部通过。
- `python -m compileall -q factor_backtest tests scripts examples`：通过。
- `git diff --check`：通过。
- 49 个 Python、Markdown、TOML 和 notebook 文件均可按 UTF-8 严格解码，无替换字符。
- 源 2.4.0 与 Platform 1.0.0 使用同一组合成数据运行，共比较 65 张内存结果表，全部逐值精确一致；两边运行前后因子输入摘要均未变化。
- 默认模式不生成 PNG 或 HTML；显式报告模式和 `render_factor_backtest_report(run_dir)` 均可生成报告。

关键文件迁移前后 SHA256 如下。12 个文件完全一致；`result_loader_v2.py` 仅因默认输出根目录从 Classic 调整为 Platform 而不同，逐行差异只有这一处路径。

| 文件 | 源 SHA256 | Platform SHA256 | 结论 |
| --- | --- | --- | --- |
| `analytics.py` | `3563C14823EF850954A2F97D4FEBC11C7E565BDD4C24841B0DD2EC50F3507177` | 同源 | 一致 |
| `returns.py` | `0C5FB26DE53F785D006E511768E12C95AED974EBF7F830ADDDC19E8B9DE5C2DA` | 同源 | 一致 |
| `filters.py` | `A8C2262888328849B8F5BCD2C1EAC80182BD6A436A800189504D670A00C70ABD` | 同源 | 一致 |
| `pools.py` | `B99C8EBF35BBC869E79D4AB78A30B48C24C7C8BDDF26E8EDB176A1F122E03598` | 同源 | 一致 |
| `market_data.py` | `58B11E505050FB337990F8AA23BE5813983D67A68F706570FE6863429CAED5F4` | 同源 | 一致 |
| `factor_loader.py` | `BFBB6E3572D8B703CE06C72C4610D2839743FDBE0F62DD0E077D2CF26579851F` | 同源 | 一致 |
| `risk_exposure.py` | `159BFF6B6D005E1726E8495AC9381B5F5EDCEFC1A33F0007D446B581FD85EE62` | 同源 | 一致 |
| `company_diagnostics.py` | `5D2072D113D9395A712B974BCE04F91D9D04C3C3750B869CCB377E724FF76BA3` | 同源 | 一致 |
| `clickhouse_adapter.py` | `B18959F84B073BABCA9AE0894EE33632784D8081AD61D27DF536E9D9EE2643C8` | 同源 | 一致 |
| `sections.py` | `8CC6793B3D73F976DE773EED2A92E99E23084F1ACD2A3990C37439458EC4DA68` | 同源 | 一致 |
| `result_store.py` | `4EAC606FA589EC4CF8AA6AB1716C4072503899C01ED686DE343B9A444191C9AE` | 同源 | 一致 |
| `result_loader_v2.py` | `04E2FC2DF22D2981BF7B7ECB2F0B4951EDEC9FB28EF7A26B8092FC05009A725D` | `9FA96F8C49350396C772E5B2F17D640E45C302A7D36D7EB019199037D08E4B3E` | 仅默认输出路径不同 |
| `result_views.py` | `73BB3FE617D8D0E315C82DA9D8717DAD0D335C0955F72F403CD6D82EB5133AA8` | 同源 | 一致 |

## Git 与服务器边界

本地迁移阶段不上传服务器、不修改服务器项目或数据、不创建服务器虚拟环境、不 commit、不 push、不创建 tag、不创建 release，也不修改远程仓库。服务器同步和 Git 发布必须在本地验收完成后另行授权。

原 `Factor_Backtest` 只有在 Platform 的测试、编译、哈希、数值一致性和源目录复核全部完成后，才可以由后续独立任务恢复到 `v2.3.0`；本任务只给出是否可安全恢复的结论，不执行恢复。
