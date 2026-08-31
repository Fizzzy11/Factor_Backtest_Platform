# Factor_Backtest_Platform 1.0.0

项目地址：[https://github.com/Fizzzy11/Factor_Backtest_Platform](https://github.com/Fizzzy11/Factor_Backtest_Platform)

本项目是面向大规模日频因子回测和只读 Dashboard 的平台版。它用于评估因子的截面排序能力，不是传统撮合式交易回测框架；主要关注不同股票池内的 RankIC、分组收益、多空收益、覆盖率和异常值诊断。

当前产品版本为 `1.0.0`，Python distribution 名称为 `factor-backtest-platform`，导入名称为 `factor_backtest_platform`。新运行结果继续使用独立的 Schema 2.0：

```json
{
  "package_version": "1.0.0",
  "framework_version": "v2",
  "result_schema_version": "2.0"
}
```

产品版本、`framework_version` 和结果 `schema_version` 分别管理 Platform 发布版本、运行器代际和磁盘数据结构，三者不要混用。产品从 `1.0.0` 起步表示项目边界发生变化，不表示计算逻辑、因子时间语义或结果 Schema 回退。

## 版本演进与项目边界

- `Factor_Backtest 2.3.0`：经典静态报告版本，默认生成 CSV、PNG 和 HTML，保留供个人分析使用，后续固定在 2.3.x。
- `Factor_Backtest 2.4.0` 开发阶段：在原项目工作区完成紧凑 Parquet、`latest.json`、不可变 runs、动态查询接口和按需报告改造。它是项目拆分前的开发过渡版本，不再作为 Classic 的正式发布版本。
- `Factor_Backtest_Platform 1.0.0`：将上述规模化改造迁移为独立项目，作为平台版首个正式版本；默认服务批量因子回测和 Dashboard，同时保留按需生成静态报告的能力。

Classic 使用 `import factor_backtest`，Platform 使用 `import factor_backtest_platform`。两个 distribution 可以安全安装在同一个虚拟环境，且卸载其中一个不会删除另一个的包文件。Dashboard 只读取 Platform 的已发布结果，不重新运行回测，也不直接访问 ClickHouse 或原始因子数据重算指标。

## Platform 1.0.0 功能

本版本来源于 `Factor_Backtest 2.4.0` 开发阶段，只改造项目边界、结果存储、读取和静态报告生成方式，不修改因子日期语义、open-to-open 收益、IC、HAC、股票池、可交易过滤、风险行业诊断或分组计算公式。

- 每次成功回测只保存一个不可变的 `runs/<run_id>/`；`latest.json` 通过相对路径指向最新成功 run，不再复制一份实体 `latest/`，也不使用软连接。
- 写入先在 `runs/.staging/<run_id>/` 完成，校验核心模块和 Parquet 可读性后再原子发布；同一因子的发布通过文件锁串行化。
- 默认基础结果按逻辑表跨股票池合并为压缩 Parquet，并用显式 `pool` 列区分。默认压缩为 `zstd`，可通过 `parquet_compression` 调整。
- 只持久化每日 IC、每日分组收益、数据质量、风险行业日度诊断和成员变化率等不可廉价替代的基础表。累计曲线、窗口摘要、IC 统计、年度 IC、多空和绩效表按需从基础表重算。
- `LoadedBacktestResult` 同时兼容旧 CSV 结果和新 Parquet 结果，新增 `ic_view()`、`group_return_view()`、`quality_view()`，支持按股票池和日期范围读取。区间累计 IC 从 0 开始，分组累计曲线从 1 开始，20 日 IC 均线自动读取起点前 19 个交易日预热。
- `run_factor_backtest_data()` 只保存数据和元信息，不生成 PNG 或 HTML；需要静态报告时调用 `render_factor_backtest_report(result.run_dir)` 按需重建。
- `run_factor_backtest()` 默认 `export_static_report=False`、`render_plots=False`，不生成 PNG 或 HTML；显式打开两个开关或调用 `render_factor_backtest_report()` 可按需生成完整静态报告。
- `artifact_level="none"` 仍是默认值；`artifact_level="full"` 保留完整因子、有效样本、未来收益和中性化 residual 等排查矩阵。
- 旧结果不会自动迁移或删除；读取 `run="latest"` 时优先解析 `latest.json`，不存在时继续兼容旧 `<factor>/latest/`。

## v2.3.0 更新内容

`v2.3.0` 重点修正多日重叠远期收益的统计解释，并增加年度 IC 跟踪。因子日期语义、open-to-open 收益公式、每日滚动计算频率、股票池、可交易过滤、10 分组和外部收益对齐方式均保持不变。

- IC 和多空均值显著性新增 Newey-West HAC t-stat。内置 1D/5D/10D/20D 默认使用 `lag=horizon_days-1`，即 `0/4/9/19`；外部收益使用显式配置的 `horizon_days-1`。
- IC 统计新增 `icir_raw`、`t_stat_naive`、`t_stat_hac`、`hac_lag`、`valid_days` 和 `hac_status`。报告默认展示 HAC t-stat；旧 `icir`、`t_stat` 字段保留一个兼容周期。
- 明确 `icir_raw` 和多空 `mean_over_std_raw` 均为未年化 `mean/std`。旧 `sharpe` 字段继续保留以兼容现有下游，但报告不再把它解释为年化 Sharpe。
- 新增 `yearly_ic` 模块，按因子日 `trade_date` 所在自然年汇总每日 IC。支持 `yearly_ic_min_days` 和 `yearly_ic_include_partial_year`，并分别输出 Spearman/Pearson 年度表和图。
- 多空累计图改为日等效 spread 诊断：先分别把 G10、G1 的 h 日收益转换为日等效收益，再计算两组之差并累计。该曲线用于跨 horizon 比较，不代表考虑持仓重叠、资金分层和交易成本后的组合净值。
- 对 `h>1` 的重叠远期收益，`diagnostic_max_drawdown` 明确设为不可用，并通过 `not_applicable_reason=overlapping_forward_returns` 说明原因；旧 `max_drawdown` 字段只作为历史兼容字段保留。
- 原 `group_turnover` 明确更名解释为相邻因子日的分组成员变化率。新增 `group_membership_change_*` 表名，旧 `group_turnover_*` 文件名继续保留，避免破坏既有读取脚本。
- 最终 HTML report 已接入年度 IC、HAC 统计、日等效多空曲线、回撤适用性和成员变化率说明；2.4.0 起数据模式从日度基础 Parquet 重渲染完整报告。
- 测试增加 HAC lag、外部收益未知 horizon、年度 IC、自然 horizon 排序、日等效多空 spread、多日回撤限制和报告接入覆盖。

## v2.2.3 更新内容

`v2.2.3` 是向后兼容的因子输入格式扩展，不改变回测时间语义、open-to-open 收益计算、股票池、可交易过滤或统计逻辑。主要变化：

- 因子文件读取支持 `.parquet`，并推荐正式生产因子优先使用 parquet。
- 默认因子发现顺序改为 `.parquet`、`.h5`、`.csv`；同名多格式文件同时存在时优先读取 parquet。
- parquet 支持单文件宽表、单文件长表，以及后缀为 `.parquet` 的按月或按年分区目录。
- 标准因子矩阵仍为 `trade_date × symbol`：`trade_date` 为日频 `DatetimeIndex`，`symbol` 为字符串列名。
- 加载阶段不会填充、缩尾、标准化或翻转方向；`NaN`、`Inf`、`-Inf` 会原样保留，但后续有效样本筛选不会把非有限值纳入 IC 或分组收益计算。
- 分区 parquet 目录只读取直接子级 `*.parquet` 文件，忽略 `manifest.json` 和其他非 parquet 文件；不同分区日期不能重叠，否则会明确报错。日频因子需要每日追加时，推荐按月分区，以减少更新当前数据时的重复 I/O。
- 宽表数值校验改为一次性合并转换后的股票列，避免数千列因子矩阵逐列插入导致 pandas `DataFrame is highly fragmented` 性能警告，同时保持原有非数值报错和 `NaN`、`Inf`、`-Inf` 处理语义不变。

## v2.2.2 更新内容

`v2.2.2` 是小版本修复，不新建 GitHub Release。重点修正 CSV 宽表读取时的日期列识别问题：

- 因子 CSV 宽表现在支持显式 `date` 或 `trade_date` 列。读取时会把该列设为 `trade_date` index，不会再把默认 RangeIndex 误转成 `1970-01-01` 纳秒序列。
- 外部收益 CSV/宽表同样支持显式 `date` 或 `trade_date` 列，修复逻辑与因子宽表一致。
- 仍然兼容原有“日期已经在 DataFrame index 中”的宽表格式，以及长表/MultiIndex 长表格式。
- 新增回归测试覆盖因子宽表 CSV 和外部收益宽表 CSV 的日期列解析。
- 该修复解决了 `factor_macd_delta.csv` 这类 `date,000001.XSHE,...` 文件能读取但回测结果全空的问题。

## v2.2.1 更新内容

`v2.2.1` 是小版本优化，重点补齐公司侧正式 diagnostics 输出流程。该流程由 `CompanyDiagnosticsConfig` 控制，默认关闭；打开后会生成 `spanning.json`、`crowding.json`、`mechanism_consistency.json` 和 `diversity.json`。当时版本会复制到 `latest/diagnostics/`；2.4.0 起只保存在不可变 run 中，并由 `latest.json` 指向。

本次更新包括：
- 新增正式 `CompanyDiagnosticsConfig`，与第一轮 `HandoffConfig` 验收样例包分离。
- 新增 `factor_backtest_platform/company_diagnostics.py`，支持 production book、peer book 和 regime 宽表输入。
- `spanning.json` 支持基于 production book 的迭代 top-k 残差化，并输出 `incremental_ic` 与 `incremental_r2`。
- `crowding.json` 按 pool 输出 peer 相似度、TopK overlap 和拥挤度指标。
- `diversity.json` 基于 `all` pool 输出相对 peer book 的新颖度。
- `mechanism_consistency.json` 按 pool 和 horizon 输出分组单调性，并可在 regime 标签内计算 IC。
- 正式 diagnostics 只写 `status="computed"`；没有足够输入或无法计算的文件不会生成。
- 正式 diagnostics 遵守公司侧固定 key：pool 只保留 `all`、`hs300_pool`、`zz1000_pool`、`zz2000_pool`，horizon 只保留 `"1"`、`"5"`、`"10"`、`"20"`。
- `examples/run_factor_dm_20d.py` 增加 `company_diagnostics_enabled` 开关和完整参数示例。
- README 和使用手册补充正式 diagnostics 的输出路径、输入格式、参数说明和预留项说明。

## v2.2.0 更新内容

`v2.2.0` 新增公司平台 handoff 交付包导出流程，用于回答“本地单因子文件是否能被 Factor_Backtest 跑出真实、非空、机器可读、可复现的 `sample_latest/`”这个第一轮验收问题。该流程默认关闭，不影响常规 `latest/`、`runs/<timestamp>/` 和 HTML 报告输出。

本次更新包括：

- 新增 `HandoffConfig`，通过 `BacktestConfig(handoff=HandoffConfig(enabled=True))` 打开平台交付导出。
- 新增 handoff 导出器，真实回测结束后额外生成 `docs/handoffs/factor_backtest_platform/sample_latest/`。
- 自动整理平台验收要求的 `run_meta.json`、`run_log.json`、5 张核心 CSV 和 4 个 diagnostics JSON。
- 5 张核心 CSV 包括 `ic_stats_spearman.csv`、`group_return_summary.csv`、`performance_metrics.csv`、`group_turnover_edge_summary.csv` 和 `data_quality.csv`。
- 导出前会校验核心 CSV 存在且非空，并额外校验 `ic_stats_spearman.csv` 覆盖当前 `horizons` 对应的 IC 统计。
- 四类 diagnostics 第一轮默认写为 `pending(company)`，明确 reason、owner、required_input 和 next_step，不伪造 computed 指标。
- `examples/run_factor_dm_20d.py` 增加 handoff 开关示例，默认关闭；需要平台验收时只需设置 `handoff_enabled=True`。
- 新增 `tests/test_handoff.py` 覆盖默认关闭、成功导出和缺失 IC horizon 报错场景。

推荐第一轮 handoff 使用最小 section 集合，只跑 `all` 股票池、Spearman IC、基础分组收益、换手率、绩效和数据质量；如果不需要风格/行业输出，可设置 `risk_exposure_source="none"`，避免风险暴露数据缺失影响交付闭环。

## 核心时间语义

因子文件采用宽表格式：

```text
index = trade_date
columns = symbol
values = factor value
```

CSV/parquet 宽表也可以把日期放在显式列中：

```text
date/trade_date, 000001.XSHE, 000002.XSHE, ...
```

框架会自动把 `date` 或 `trade_date` 列转为 `trade_date` index。

`factor_df.loc[t]` 表示 `t` 日收盘后生成、下一交易日开盘前可用的因子值。框架默认在下一交易日开盘按因子排序建仓，未来收益使用 open-to-open：

```text
future_return_h[t] = open_price[t+h+1] / open_price[t+1] - 1
```

这里的 `t+1`、`t+h+1` 是交易日序列上的偏移，不是自然日偏移。

默认 horizon：

```python
[1, 5, 10, 20]
```

## 默认路径

```python
project_dir = "/app/workspace/zhangyuan/Factor_Backtest_Platform"
data_root = "/data/zhangyuan"
pool_dir = "/data/zhangyuan/pool"
output_root = "/data/zhangyuan/Factor_Backtest_Platform_Result"
```

代码目录为 `/app/workspace/zhangyuan/Factor_Backtest_Platform`，调用脚本目录为 `/app/workspace/zhangyuan/Factor_Backtest_Platform_Result`，结果目录为 `/data/zhangyuan/Factor_Backtest_Platform_Result`。共享输入仍位于 `/data/zhangyuan`，股票池仍位于 `/data/zhangyuan/pool`；无需复制因子文件或数据库数据，也不得把 Platform 输出写入 Classic 的 `/data/zhangyuan/Factor_Backtest_Result`。

服务器统一使用 `/app/workspace/zhangyuan/.venv`。Classic 与 Platform 的导入包名不同，可以在同一环境安装：

```bash
/app/workspace/zhangyuan/.venv/bin/python -m pip install -e /app/workspace/zhangyuan/Factor_Backtest
/app/workspace/zhangyuan/.venv/bin/python -m pip install -e /app/workspace/zhangyuan/Factor_Backtest_Platform
```

安装后可以在任意目录调用，例如：

```bash
cd /app/workspace/zhangyuan/Factor_Backtest_Platform_Result
/app/workspace/zhangyuan/.venv/bin/python factor_dm_20d/run_factor_dm_20d.py
```

项目内提供了一个示例脚本：

```text
examples/run_factor_dm_20d.py
```

可以复制到 `/app/workspace/zhangyuan/Factor_Backtest_Platform_Result/factor_dm_20d/run_factor_dm_20d.py` 后按需修改因子名、时间范围和股票池。Dashboard 默认只读 `/data/zhangyuan/Factor_Backtest_Platform_Result`。

如果因子名为 `dm_20d`，默认查找：

```text
/data/zhangyuan/factor_dm_20d/factor_dm_20d.parquet
/data/zhangyuan/factor_dm_20d/factor_dm_20d.h5
/data/zhangyuan/factor_dm_20d/factor_dm_20d.csv
```

也可以直接传入 `factor_path`。

## 因子格式自动识别

框架支持：

- 标准宽表：`trade_date x symbol`
- MultiIndex 长表：`date/asset` 双索引
- 普通长表：`trade_date/date + symbol/asset + value/factor/factor_value`
- 单文件 parquet：宽表或长表均可
- 分区 parquet 目录：后缀为 `.parquet` 的目录，目录内直接放置 `2024-01.parquet`、`2024-02.parquet` 等月度分区文件，也支持 `2024.parquet`、`2025.parquet` 等年度分区文件

内部统一输出为：

```text
index = trade_date
columns = symbol
```

单文件 parquet 示例：

```python
factor_path = "/data/zhangyuan/factor_order_imbalance_v1/factor_order_imbalance_v1.parquet"
factor_df = load_factor_file(factor_path)
```

按月分区 parquet 目录示例（日频因子生产推荐）：

```text
/data/zhangyuan/factor_order_imbalance_v1/
  manifest.json
  factor_order_imbalance_v1.parquet/
    2026-01.parquet
    2026-02.parquet
    2026-03.parquet
```

调用时把 `factor_path` 指向后缀为 `.parquet` 的目录：

```python
factor_path = "/data/zhangyuan/factor_order_imbalance_v1/factor_order_imbalance_v1.parquet"
factor_df = load_factor_file(factor_path)
```

每个月度文件保存当月所有交易日的日频因子。每日更新时只需读取并重写当月文件，无需重写全年数据，因此更适合持续增量生成因子的场景。分区粒度只影响存储和更新方式，不改变因子日期语义或回测计算结果。

分区目录不会递归读取更深层目录。不同分区的股票列可以不完全一致，框架会取列并集并保留缺失位置为 `NaN`；但不同分区不能包含重复 `trade_date`。当前回测加载时仍会读取目录内全部月度文件，月度分区主要优化因子数据的日常写入，不代表按回测日期范围自动跳过无关分区。

## 股票池

框架配置默认：

```python
selected_pools = ["all"]
```

示例脚本 `examples/run_factor_dm_20d.py` 默认运行：

```python
selected_pools = ["all", "hs300_pool", "zz1000_pool", "zz2000_pool"]
```

`all` 是虚拟池，表示全市场，不读取指数成分文件。

支持的指数池名称：

```text
hs300_pool
zz500_pool
zz1000_pool
zz2000_pool
gz1000_pool
gz2000_pool
gzMidsmallcap_pool
miMicrocap_pool
```

股票池 CSV 格式：

```csv
trade_date,symbol
2020-01-02,000001.XSHE
2020-01-02,000002.XSHE
```

所有计算在每个 pool 内独立完成，只有 `cross_pool_summary` 这类模块会读取各 pool 已完成结果做对比。

## 可交易过滤

默认参数：

```python
tradability_filter = True
min_listed_days = 120
```

开启后，仅过滤建仓日截面，不检查退出日是否可卖。过滤规则：

- 当日最高价触及涨停：`high_price >= limit_up`
- 当日最低价触及跌停：`low_price <= limit_down`
- ST
- 停牌
- 上市交易日数小于 120
- 当日开盘价缺失或小于等于 0

## ClickHouse 行情数据读取

框架已经内置 ClickHouse 读取函数。连接信息通过环境变量注入：

```text
FACTOR_BACKTEST_PLATFORM_CLICKHOUSE_HOST
FACTOR_BACKTEST_PLATFORM_CLICKHOUSE_PORT
FACTOR_BACKTEST_PLATFORM_CLICKHOUSE_USERNAME
FACTOR_BACKTEST_PLATFORM_CLICKHOUSE_PASSWORD
```

禁止把生产连接密码写入脚本、文档或 Git；未配置时只使用本机无密码开发默认值。
迁移期间仍兼容旧的 `FACTOR_BACKTEST_CLICKHOUSE_*` 变量；同时存在时，Platform 专用变量优先。

使用示例：

```python
from factor_backtest_platform.clickhouse_adapter import load_market_data_from_clickhouse

market_data = load_market_data_from_clickhouse(
    start_date="2020-01-01",
    end_date="2026-05-02",
)
```

该函数会自动查询：

```text
stock_data.view_stock_qfq_adjusted_ohlcv_v2
cn_stock_fundamentals.shares
cn_stock_fundamentals.is_st_stock
cn_stock_fundamentals.is_suspended
```

并把查询结果转换成 `MarketDataBundle`，包括：

```text
open_price
close_price
high_price
low_price
volume
amount
market_cap
limit_up_price
limit_down_price
is_st
is_suspended
listed_days
```

## 模块化输出

默认执行全部内置模块：

```python
enabled_sections = "all"
```

内置模块包括：

- `data_quality`
- `ic_overview`
- `cumulative_ic`
- `yearly_ic`
- `factor_style_exposure`
- `style_neutralized_ic`
- `style_industry_neutralized_ic`
- `group_exposure_diagnostics`
- `group_return`
- `within_industry_group_return`
- `layered_group_return`
- `long_short`
- `group_turnover`
- `performance_metrics`

每个模块独立计算和渲染。可选诊断失败会记录到 `run_log.json` 和 `manifest.json`，不会影响其他模块；已启用的 `data_quality`、`cumulative_ic` 或 `group_return` 失败时不会发布该 run，也不会更新 `latest.json`。

新增诊断模块时，在 `factor_backtest_platform/sections.py` 中实现 `ReportSection` 子类并返回 `SectionResult`，为模块设置唯一 `name` 和依赖关系，再将实例加入 `DEFAULT_SECTIONS`。如果新模块有不能从现有基础表廉价重算的日度数据，还需在 `factor_backtest_platform/result_store.py` 的持久化白名单中登记，并补充 Parquet 写入、加载、日期过滤和报告重建测试；不得在加载器或 Dashboard 中另写一套金融计算公式。

## 平台 handoff 交付包

如果需要按公司平台验收任务书生成 `docs/handoffs/factor_backtest_platform/sample_latest/`，可以打开 handoff 开关。它不会改变正常 run 和 `latest.json`，只会在真实回测完成后额外整理 5 张核心 CSV、`run_meta.json`、`run_log.json` 和 4 个 pending diagnostics JSON。

```python
from factor_backtest_platform import BacktestConfig, HandoffConfig
from factor_backtest_platform.config import DataSourceConfig

cfg = BacktestConfig(
    factor_name="smoke_factor_v1",
    selected_pools=["all"],
    horizons=[1, 5, 10, 20],
    ic_methods=["spearman"],
    data_sources=DataSourceConfig(risk_exposure_source="none"),
    enabled_sections=[
        "data_quality",
        "cumulative_ic",
        "group_return",
        "long_short",
        "group_turnover",
        "performance_metrics",
    ],
    handoff=HandoffConfig(enabled=True, data_asof="2026-06-10"),
)
```

导出器会校验 `ic_stats_spearman.csv`、`group_return_summary.csv`、`performance_metrics.csv`、`group_turnover_edge_summary.csv` 和 `data_quality.csv` 是否存在且非空；缺表或空表会报错，不会生成伪造指标。详细字段和验收口径见 [使用手册](docs/使用手册.md)。

## 输出结构

默认生成不可变历史 run，并用轻量 `latest.json` 指向最新成功结果。`runs/<run_id>/` 的时间戳固定使用中国时区 `Asia/Shanghai`：

```text
/data/zhangyuan/Factor_Backtest_Platform_Result/
  factor_dm_20d/
    latest.json
    runs/
      .staging/
      20260519_153000_000000/
        manifest.json
        run_meta.json
        run_log.json
        data/
          daily_ic_spearman.parquet
          daily_group_returns.parquet
          data_quality.parquet
        plots/             # 仅生成静态报告时存在
          all/
        report.html        # 仅生成静态报告时存在
        artifacts/         # 仅 artifact_level="full" 时存在
          all/
```

下游模型、notebook 和人工复盘应通过 `load_backtest_result(..., run="latest")` 解析 `latest.json`，不要硬编码 `<factor>/latest/`。如果需要追溯某次历史运行，传入具体 `run_id`。

也可以通过以下配置恢复旧版单时间目录结构：

```python
cfg = BacktestConfig(output_layout="timestamp")
```

输出会先按因子名分目录。运行时可以通过配置传入：

```python
cfg = BacktestConfig(factor_name="factor_dm_20d")
```

或者在运行函数中传入：

```python
run_factor_backtest(..., factor_name="factor_dm_20d")
```

`data/` 保存压缩后的日度基础结果；派生统计不再逐表重复保存 CSV。`plots/` 和 `report.html` 是可选静态输出。默认 `artifact_level="none"`，不会写 `aligned_factor`、`valid_mask` 和 `future_returns_*` 等完整矩阵；需要排查数据对齐或复用中间矩阵时设置 `artifact_level="full"`。

`run_factor_backtest()` 默认 `export_static_report=False`、`render_plots=False`，只写日度基础 Parquet、manifest 和日志，不生成 PNG 或 HTML。显式设置 `export_static_report=True`、`render_plots=True` 可在本次运行中生成完整静态报告；也可先使用默认数据模式，之后调用 `render_factor_backtest_report()` 按需重建。仅设置 `export_static_report=True` 且保持 `render_plots=False` 时生成无图片 HTML。

每次运行完成后可以直接打开单次 run 目录下的 `report.html`。报告会按股票池分别汇总：

- 本次回测参数和 warning
- 关键图表：累计 IC、20D IC 移动平均、年度 IC、10 分组平均收益、按 horizon 拆分的 10 组累计收益线图、分层收益、日等效累计多空 spread、因子覆盖率和分组成员变化率
- 关键统计表：IC statistics、yearly IC statistics、group return summary、layered group return summary、daily-equivalent long-short tail、long-short diagnostics
- 模块状态，以及到 `data/`、`plots/<pool>/`、`artifacts/<pool>/` 的相对链接；`artifacts/` 只有在 `artifact_level="full"` 时存在

如果 `artifact_level="full"` 且服务器安装了 `pyarrow` 或 `fastparquet`，中间结果会保存为 `.parquet`。如果当前 Python 环境缺少 parquet 引擎，框架会明确保存为 `.parquet.pkl`，不会把 pickle 文件伪装成 parquet 后缀。

PNG 图表标题统一使用英文，避免服务器缺少中文字体时出现 `Glyph missing from font(s) DejaVu Sans` warning。HTML 报告和 CSV/JSON 说明仍保留中文。`data_quality` 会拆成 `data_quality_counts.png` 和 `data_quality_ratios.png` 两张图，避免 count 和 ratio 共用同一坐标轴。

1D、5D、10D、20D 的主色调仍分别是蓝、橙、绿、红，但默认使用更柔和的十六进制颜色：`#4C78A8`、`#F58518`、`#54A24B`、`#E45756`。所有 horizon 相关图表都会复用这套颜色。

`long_short_curve.png` 展示日等效多空 spread 的累计和。对 `h>1`，框架先分别把 G10 和 G1 的 h 日收益转换为 `(1 + group_return) ** (1 / h) - 1`，再计算 `daily_equivalent_G10 - daily_equivalent_G1` 并累计。该曲线用于统一不同 horizon 的诊断尺度，不是考虑持仓重叠、资金分层和交易成本后的可交易组合净值。旧表 `cumulative_long_short_returns` 仍保留原始重叠收益的累计和以兼容现有下游，新报告使用 `cumulative_daily_equivalent_long_short_returns`。
`long_short = G10 - G1` 是固定方向的高因子值组减低因子值组 spread 诊断，不会自动判断因子正负方向。若因子方向未知，阅读分组收益、分组暴露和分组换手时应同时看 G1 与 G10。
`run_log.json` 会记录每次回测的 `timings`，包括全局耗时、每个 pool 的准备/核心计算/写 artifacts 阶段耗时，以及每个 section 的 compute/render/write/total 秒数。可以用它跟踪性能瓶颈；当 `artifact_level="none"` 时，写 artifacts 阶段通常接近 0。

最精简版统计输出可以用于批量因子训练：

```python
from factor_backtest_platform import run_factor_backtest_minimal

summary = run_factor_backtest_minimal(
    factor_df=factor_df,
    market_data=market_data,
    config=cfg,
)
```

只跑数据、不画图：

```python
from factor_backtest_platform import render_factor_backtest_report, run_factor_backtest_data

result = run_factor_backtest_data(
    factor_df=factor_df,
    market_data=market_data,
    config=cfg,
)

# 在该不可变 run 内按需生成图和 HTML：
render_factor_backtest_report(result.run_dir)
```

也可以在完整入口里显式设置 `BacktestConfig(export_static_report=False, render_plots=False)`。这个模式保留压缩日度基础表和日志，跳过 PNG 与 HTML，适合批量回测和网页端直接读数据画图。需要静态报告时，再用 `render_factor_backtest_report()` 从 Parquet 重渲染。

读取已有结果：

```python
from factor_backtest_platform import load_backtest_result, render_factor_backtest_report

result = load_backtest_result(factor_name="factor_dm_20d", run="latest")

# 历史 run 使用 latest.json 中记录过的具体 run_id：
historical = load_backtest_result(
    factor_name="factor_dm_20d",
    run="20260519_153000_000000",
)

# 原始日度底表支持 Parquet 条件下推。
daily_ic = result.read_table(
    "all",
    "daily_ic_spearman",
    start_date="2023-01-01",
    end_date="2025-12-31",
)

# 日期范围统计和累计曲线从所选区间重新计算，不是截断全历史累计值。
ic_view = result.ic_view(pool="all", method="spearman", start_date="2023-01-01")
group_view = result.group_return_view(pool="all", start_date="2023-01-01")
quality_view = result.quality_view(pool="all", start_date="2023-01-01")

# 旧派生表名仍可按需读取。
ic_stats = result.read_table("all", "ic_stats")
render_factor_backtest_report(result.run_dir)
```

## IC 方法配置

默认只计算 Spearman RankIC：

```python
cfg = BacktestConfig(ic_methods=["spearman"])
```

也可以只计算 Pearson IC，或同时计算两种 IC：

```python
cfg = BacktestConfig(ic_methods=["pearson"])
cfg = BacktestConfig(ic_methods=["spearman", "pearson"])
```

Spearman 衡量因子排序和未来收益排序的相关性，适合默认的截面排序评价。Pearson 衡量因子原始数值和未来收益数值的线性相关性，可作为可选诊断。兼容表名 `daily_ic`、`cumulative_ic`、`ic_stats` 在包含 Spearman 时指向 Spearman；如果只配置 Pearson，则指向唯一可用的 Pearson。新输出会同时保留带方法名的结果，例如 `daily_ic_spearman`、`ic_stats_spearman`、`cumulative_ic_spearman.png`。如果启用 Pearson，会额外输出 `daily_ic_pearson`、`ic_stats_pearson`、`cumulative_ic_pearson.png` 和 `ic_overview_pearson.png`。

IC 统计默认同时输出普通 t-stat 和 Newey-West HAC t-stat。内置 1D/5D/10D/20D 收益的 HAC lag 分别为 `0/4/9/19`，即 `horizon_days - 1`；外部收益使用其显式配置的 `horizon_days - 1`。外部收益未提供 `horizon_days` 时，HAC t-stat 为 `NaN` 并给出 warning，不猜测持有期。报告默认展示 `t_stat_hac`；`t_stat` 和 `t_stat_naive` 作为旧口径保留。

`icir_raw = ic_mean / ic_std` 是未年化 ICIR。兼容字段 `icir` 暂时保留相同数值，不应与另行计算的年化 ICIR 混用。多空诊断中的 `mean_over_std_raw` 同样未年化，旧字段 `sharpe` 仅作为兼容字段保留，不再在报告中称为 Sharpe。

默认 `yearly_ic` 模块按因子日期 `trade_date` 所在自然年汇总每日 IC，并输出 `yearly_ic_stats_spearman.csv`、可选的 Pearson 表，以及 `yearly_mean_ic_spearman.png`/`yearly_mean_ic_pearson.png`。`yearly_ic_min_days=60` 控制每个“年份 × horizon”的最小有效 IC 天数；不足时保留 `valid_days` 记录，但统计值为 `NaN`。`yearly_ic_include_partial_year=True` 默认保留首尾不完整年份，并通过 `is_complete_year` 标识；设置为 `False` 可排除不完整年份。年份按因子日归属，即使多日收益的退出日在下一年，也仍计入因子日所在年份。

## 风格暴露和行业数据

如需检查因子和 Barra10 风格暴露的关系，或计算风格/行业中性化 IC，可以把风险暴露和行业数据放在：

```text
/data/zhangyuan/risk&industry/CNE5_Industry_daily.parquet
```

配置：

```python
from factor_backtest_platform.config import BacktestConfig, DataSourceConfig, PathConfig

cfg = BacktestConfig(
    paths=PathConfig(
        data_root="/data/zhangyuan",
        risk_exposure_path="risk&industry/CNE5_Industry_daily.parquet",
    ),
    data_sources=DataSourceConfig(
        risk_exposure_source="csv",
    ),
    min_industry_ic_stocks=10,
    artifact_level="none",
    write_neutralized_factors=False,
)
```

本地风险暴露文件支持 `.parquet` 和 `.csv`，默认 parquet 读取依赖 `pyarrow`。文件需要包含 `date`/`trade_date`、`symbol`、Barra10 风格暴露和行业信息。行业信息支持两种格式：

- 单列 `industry`：每个 `date-symbol` 一个行业名或行业码，例如 `0..30` 或 `801760.INDX`。这是推荐格式；框架会在需要行业回归或行业暴露时生成内部 dummy，并在 `within_industry_group_return` 中优先使用 compact industry code 快路径。
- 多列 one-hot 行业 dummy：每个行业一列，属于该行业为 1，否则为 0。

默认风格列为：

```text
size, non_linear_size, momentum, liquidity, book_to_price,
leverage, growth, earnings_yield, beta, residual_volatility
```

`comovement` 当前会被忽略。行业归属按每日 `date-symbol` 动态读取；无行业归属的股票会在需要行业信息的计算中剔除并给 warning。使用 one-hot 格式时，多行业 dummy 为 1 的股票会同时参与这些行业的行业内分组计算，并使用兼容路径；使用单列 `industry` 格式时，每个 `date-symbol` 只属于一个行业，行业内分组收益会使用 compact code 路径以减少逐行业 dummy 扫描。

默认 `risk_exposure_source="csv"`，所以只要文件存在，`enabled_sections="all"` 会额外输出：

- `factor_style_exposure`：每日因子值与 Barra10 暴露的截面相关性，默认 Spearman，也跟随 `ic_methods` 支持 Pearson。
- `style_neutralized_ic`：用 `factor = intercept + Barra10 + residual` 的 residual 计算 IC。
- `style_industry_neutralized_ic`：用 `factor = intercept + Barra10 + industry dummies + residual` 的 residual 计算 IC。
- `group_exposure_diagnostics`：每天按因子分成 G1-G10 后，输出 pool、G1、G10、G10-G1、G1-pool、G10-pool 的风格暴露和行业暴露。这里会同时关注最低组和最高组，不把 G10 视为唯一重点。
- `within_industry_group_return`：每日在每个行业内部按因子分组，再跨行业合并同组股票收益。
- `group_turnover`：计算相邻因子日同一分组的成员变化率 `1 - |previous ∩ current| / |current|`，并单独汇总 G1、G10 和 edge_avg。它不考虑持有期、权重漂移、分层资金或交易成本，因此不是可交易组合的真实换手率。新表别名使用 `group_membership_change_*`，旧 `group_turnover_*` 文件名继续保留以兼容现有调用。

其中 `group_turnover` 不依赖风险暴露数据；即使设置 `risk_exposure_source="none"`，`enabled_sections="all"` 仍会输出分组换手率。其他风格、行业、中性化模块依赖 `risk_exposure`。

行业暴露图的图例会使用 `industry_01` 这类 ASCII 标签，避免服务器缺中文字体时产生 Matplotlib glyph warning；标签和真实行业名的对应关系保存在 `group_industry_exposure_plot_label_map`。

中性化 IC 默认直接在日度 OLS residual 上计算，不再默认写出完整 residual 因子矩阵，以减少大表写入时间。若需要检查或复用 residual 因子值，可以设置 `write_neutralized_factors=True`，此时会额外输出 `style_neutralized_factor` 和 `style_industry_neutralized_factor`。

风险暴露数据在每个 pool 内会先对齐成内部矩阵缓存，供 `factor_style_exposure`、中性化 IC、`group_exposure_diagnostics` 和 `within_industry_group_return` 复用。panel 会同时保留行业 dummy 矩阵和 compact industry code：中性化 IC、行业暴露诊断继续使用 dummy 口径；行业内分组收益在没有多行业重复归属时使用 code 分组，避免 `date × horizon × 行业数` 的重复全股票扫描。

如果当前环境没有风险暴露文件，需要显式关闭：

```python
data_sources=DataSourceConfig(risk_exposure_source="none")
```

关闭后，`enabled_sections="all"` 会跳过这些依赖风险暴露数据的模块，保留基础因子回测模块和不依赖风险暴露的 `group_turnover`。

行业 IC 暂不输出，避免行业数 × horizon × IC 方法导致图表过多。

交互式复盘模板在：

```text
notebooks/analyze_factor_result.ipynb
```

详细参数说明见：

```text
docs/使用手册.md
```

运行时默认会打印轻量进度日志，例如读取因子、读取 ClickHouse 行情、按股票池计算 IC、分组收益、写入或跳过 artifacts、执行报告模块和最终输出目录。可以在调用脚本中设置：

```python
verbose = False
```

关闭日志。

## 本地测试

推荐使用 `pytest` 跑完整测试：

```powershell
& 'C:\Users\fizzz\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests -q
```

兼容入口 `tests/run_tests.py` 也会委托给 pytest，避免旧的手写测试器漏跑或误报：

```powershell
& 'C:\Users\fizzz\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tests\run_tests.py
```

## 外部收益数据

默认收益仍然是 1D/5D/10D/20D open-to-open：

```text
1D  = open[t+2]  / open[t+1]  - 1
5D  = open[t+6]  / open[t+1]  - 1
10D = open[t+11] / open[t+1]  - 1
20D = open[t+21] / open[t+1]  - 1
```

可以通过 `external_returns` 额外传入外部收益。外部收益会和内置收益同级参与由 `ic_methods` 控制的 IC、累计 IC、IC 统计、分组收益、分层收益、多空收益、performance metrics、可选 artifacts 和 report。默认 `ic_methods=["spearman"]` 时仍输出 Spearman RankIC；启用 Pearson 后会额外输出 Pearson IC 相关表和图。

外部收益支持宽表和长表。宽表要求：

```text
index = trade_date
columns = symbol
value = external return
```

CSV 宽表也可以使用显式日期列：

```text
date/trade_date, 000001.XSHE, 000002.XSHE, ...
```

框架会自动将该列作为 `trade_date` index。

长表支持：

```text
trade_date/date + symbol/asset + return/ret/future_return/value/factor_value/factor
```

关键口径：`external_return.loc[t, symbol]` 必须表示用于检验 `factor_df.loc[t, symbol]` 的未来收益标签。这里的 `trade_date=t` 是因子日期，不是收益开始日，也不是收益结束日。

示例：

```python
result = run_factor_backtest(
    factor_df=factor_df,
    market_data=market_data,
    config=cfg,
    external_returns={
        "external_alpha": external_return_df,
        "external_5d": {
            "data": external_5d_return_df,
            "horizon_days": 5,
        },
    },
)
```

`horizon_days` 只影响 10 组累计收益线图。未传 `horizon_days` 时，框架仍计算 IC、分组收益、多空收益等统计，但会跳过该外部收益的 `10-Group Cumulative Return` 图，并在 report warning 中说明原因。传入 `horizon_days` 后，累计线使用 `(1 + group_return) ** (1 / horizon_days) - 1` 转成日等价收益后再复利。

如果只想使用外部收益，不计算内置 1D/5D/10D/20D，可以设置 `horizons=[]`：

```python
cfg = BacktestConfig(
    horizons=[],
    ic_methods=["spearman"],
)

result = run_factor_backtest(
    factor_df=factor_df,
    market_data=market_data,
    config=cfg,
    external_returns={
        "my_return": {
            "data": external_return_df,
            "horizon_days": 5,
        },
    },
)
```

当前接口仍需要传入 `market_data`，因为框架还会用行情日期和股票列做对齐、股票池和可交易性过滤。

## 取数配置集中化

可变取数配置集中在 `factor_backtest_platform.config`。当前行情、ST、停牌等可通过 ClickHouse 获取；pool 暂时仍使用 CSV；因子值长期保留文件和数据库双入口的设计空间；风格暴露和行业数据当前通过本地 parquet/csv 文件读取，并预留 ClickHouse 切换入口。

ClickHouse 连接信息不写入代码库。运行前通过环境变量配置：

```bash
export FACTOR_BACKTEST_PLATFORM_CLICKHOUSE_HOST="clickhouse.example"
export FACTOR_BACKTEST_PLATFORM_CLICKHOUSE_PORT="8123"
export FACTOR_BACKTEST_PLATFORM_CLICKHOUSE_USERNAME="researcher"
export FACTOR_BACKTEST_PLATFORM_CLICKHOUSE_PASSWORD="请从安全凭据系统注入"
```

未设置时使用 `localhost:8123`、用户 `default` 和空密码。`.env` 文件已被 Git 忽略；框架不会自动加载 `.env`，如需使用应由部署系统或进程启动脚本注入环境变量。

```python
from factor_backtest_platform import BacktestConfig
from factor_backtest_platform.config import ClickHouseConfig, ClickHouseTableConfig, DataSourceConfig, PathConfig

cfg = BacktestConfig(
    paths=PathConfig(
        project_dir="/app/workspace/zhangyuan/Factor_Backtest_Platform",
        data_root="/data/zhangyuan",
        pool_dir="/data/zhangyuan/pool",
        risk_exposure_path="risk&industry/CNE5_Industry_daily.parquet",
    ),
    data_sources=DataSourceConfig(
        market_data_source="clickhouse",
        pool_source="csv",
        factor_source="file",
        risk_exposure_source="csv",
        clickhouse=ClickHouseConfig(),
        clickhouse_tables=ClickHouseTableConfig(
            ohlcv="stock_data.view_stock_qfq_adjusted_ohlcv_v2",
            shares="cn_stock_fundamentals.shares",
            st="cn_stock_fundamentals.is_st_stock",
            suspended="cn_stock_fundamentals.is_suspended",
            risk_exposure=None,
        ),
    ),
)
```

现阶段 `pool_source="csv"` 和 `risk_exposure_source="csv"` 是已实现路径；`pool_source="clickhouse"` 和 `risk_exposure_source="clickhouse"` 是未来入库后的统一切换入口，目前会明确报未实现。

读取 ClickHouse 行情时可以直接传入集中配置：

```python
market_data = load_market_data_from_clickhouse(
    start_date="2020-01-01",
    end_date="2026-05-02",
    config=cfg.data_sources,
)
```
## Company Diagnostics 正式诊断 JSON

`CompanyDiagnosticsConfig` 用于生成正式的公司侧诊断结果，默认关闭。它和 `HandoffConfig` 的第一轮 `pending(company)` 占位文件不是同一个流程：handoff 只整理验收样本；company diagnostics 会在真实回测结果目录内写入 `status="computed"` 的 JSON。

输出位置：

```text
<output_root>/<factor_name>/runs/<run_time>/diagnostics/
<output_root>/<factor_name>/latest.json  # 指向上述 run，不复制 diagnostics
```

目前支持四类文件；某类诊断没有足够输入时不会写该文件，不会写 pending 或空占位：

```text
spanning.json
crowding.json
mechanism_consistency.json
diversity.json
```

输入数据接口：

```text
production_book: date/trade_date, symbol/stock_id, factor_id, value/factor_value
peer_book:       date/trade_date, symbol/stock_id, factor_id, value/factor_value
regime:          date/trade_date, bull, bear, high_vol, low_vol
```

`production_book` 用于 `spanning.json`，代表生产/上线因子库；`peer_book` 用于 `crowding.json` 和 `diversity.json`，可以包含 production、promoted、research 等更宽的 peer 因子集合。book 内的 `factor_id` 必须是稳定字符串，因为它会出现在 `max_corr_peer` 和 `top_spanning_factors[].factor_id`。

最小配置示例：

```python
from factor_backtest_platform import BacktestConfig, CompanyDiagnosticsConfig

company_diagnostics_enabled = True

if company_diagnostics_enabled:
    diagnostics_config = CompanyDiagnosticsConfig(
        enabled=True,
        production_book_path="/data/zhangyuan/company_books/production_book.csv",
        peer_book_path="/data/zhangyuan/company_books/peer_book.csv",
        regime_path="/data/zhangyuan/company_books/regime.csv",
        baseline_suite_id="production_book_2026Q2",
        baseline_book_version="production_book_2026Q2",
        peer_book_version="promoted_research_book_2026Q2",
        hypothesis_direction="unknown",
        idea_id="idea_quiet_001",
        version=1,
    )
else:
    diagnostics_config = CompanyDiagnosticsConfig(enabled=False)

cfg = BacktestConfig(
    factor_name="factor_dm_20d",
    selected_pools=["all", "hs300_pool", "zz1000_pool", "zz2000_pool"],
    horizons=[1, 5, 10, 20],
    diagnostics=diagnostics_config,
)
```

`examples/run_factor_dm_20d.py` 已经内置同样的开关式写法。日常回测保持 `company_diagnostics_enabled = False` 即可；需要正式四个 JSON 时改成 `True`，并填写 production book、peer book、regime 等路径。

常用参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `False` | 是否生成正式 company diagnostics；关闭时不读取 book/regime，也不会写 `diagnostics/` |
| `production_book_source` | `"file"` | production book 来源；当前实现支持 `"file"`，`"clickhouse"` 仅预留 |
| `peer_book_source` | `"file"` | peer book 来源；当前实现支持 `"file"`，`"clickhouse"` 仅预留 |
| `regime_source` | `"file"` | regime 来源；当前实现支持 `"file"`，`"clickhouse"` 仅预留 |
| `production_book_path` | `None` | 生产因子库长表路径；提供后可生成 `spanning.json` |
| `peer_book_path` | `None` | peer 因子库长表路径；提供后可生成 `crowding.json` 和 `diversity.json` |
| `factor_meta_path` | `None` | 预留字段；当前不会自动根据 meta 拆分 production/peer |
| `factor_ls_pnl_path` | `None` | 预留字段；当前暂不计算 `max_abs_returncorr` |
| `regime_path` | `None` | regime 布尔宽表路径；提供后 `mechanism_consistency.json` 会包含 `regime_detail` |
| `baseline_suite_id` | `None` | 写入 `spanning.json` 的生产库标识 |
| `baseline_book_version` | `None` | 写入 `spanning.meta.baseline_book_version` |
| `peer_book_version` | `None` | 写入 `crowding/diversity.meta.peer_book_version` |
| `peer_pool_id` | `"research+promoted"` | 写入 `crowding.json` 的 peer 池标识 |
| `hypothesis_direction` | `"unknown"` | 可选 `"high_is_long"`、`"high_is_short"`、`"unknown"`；方向未知时方向匹配字段写 `null` |
| `idea_id` | `None` | 写入 `diversity.json` 的 idea 标识 |
| `version` | `1` | 写入 `diversity.json` 的因子版本号 |
| `regime_labels` | `["bull", "bear", "high_vol", "low_vol"]` | 从 regime 宽表读取的固定标签列 |
| `neutralization_layers` | `["production_book"]` | 写入 `spanning.json` 的中性化/控制层说明 |
| `spanning_topk` | `20` | 每轮残差化选择最相关 production 因子数量 |
| `spanning_rounds` | `3` | 迭代残差化轮数 |
| `top_spanning_factors` | `5` | `top_spanning_factors` 列表保留的最相似 production 因子数量 |
| `topk_overlap_k` | `50` | crowding 多空两端 overlap 的 TopK |
| `crowding_threshold` | `0.7` | 预留配置；当前 `n_peers_above_0.7` 固定按字段名里的 `0.7` 计算 |
| `min_similarity_stocks` | `30` | RankCorr、残差化和增量 R2 的最小有效股票数 |
| `framework_version` | `"Factor_Backtest_Platform_1_0_0"` | 写入 diagnostics `meta.framework_version` |

主要口径：

- `crowding.max_abs_rankcorr`：候选因子和每个 peer 的每日截面绝对 RankCorr 先按交易日取均值，再取最大 peer。
- `diversity.max_similarity_to_book`：与 `crowding` 使用同一套 mean abs RankCorr 口径，只基于 `all` pool 输出一份。
- `spanning.incremental_ic`：候选因子按 `production_book` 中最相关因子迭代残差化后的残差 IC。
- `spanning.incremental_r2`：用已选 production 因子做基准回归，再加入残差候选因子的日度截面 R2 提升。
- `mechanism_consistency.group_monotonicity`：G1-G10 平均分组收益的 Spearman 单调性。
- `mechanism_consistency.regime_detail`：在固定 regime 标签子样本内计算 IC；当 `hypothesis_direction="unknown"` 时，`sign_matches_hypothesis` 和 `regime_sign_consistency` 为 `null`。

正式 diagnostics 遵守公司侧固定 schema：只输出 `all`、`hs300_pool`、`zz1000_pool`、`zz2000_pool` 四个 pool 的交集；horizon 型文件只输出 `"1"`、`"5"`、`"10"`、`"20"`。其他普通回测 pool、external return 或自定义 horizon 可以继续用于常规报告，但不会写入这四类正式 JSON。
