# 因子排序回测框架设计文档

## 1. 框架目标

本项目用于构建一个适配因子挖掘体系的因子回测框架。框架不以传统组合调仓、撮合成交或完整交易系统为核心，而是专注于评估单个因子的截面排序能力：

- 因子值较高的股票，未来收益是否系统性高于因子值较低的股票。
- 因子 RankIC 是否长期稳定偏离 0。
- 因子在不同股票池内是否表现一致。
- 因子覆盖率、异常值、缺失值、可交易过滤后的样本是否稳定。

框架主输入是已经生成好的因子文件。框架不审计因子构建过程是否存在未来信息，只保证从因子文件进入回测流程之后，收益标签、股票池过滤、可交易过滤、分组和统计过程不引入未来信息。

## 2. 路径约定

服务器路径约定如下：

```python
project_dir = "/app/workspace/zhangyuan/Factor_Backtest_Platform"
data_root = "/data/zhangyuan"
pool_dir = "/data/zhangyuan/pool"
```

因子文件默认路径规则：

```text
factor_name = "dm_20d"
默认目录 = /data/zhangyuan/factor_dm_20d
默认文件 = /data/zhangyuan/factor_dm_20d/factor_dm_20d.h5
```

框架应支持用户直接传入 `factor_path`。如果传入 `factor_path`，优先使用该路径；否则根据 `factor_name` 自动搜索 `.h5`、`.csv` 等支持格式。

输出目录建议放在：

```text
/data/zhangyuan/Factor_Backtest_Platform_Result
```

缓存目录建议放在：

```text
/data/zhangyuan/Factor_Backtest_Platform_Result/cache
```

## 3. 统一字段命名

新框架内部统一使用以下字段名：

```text
trade_date
symbol
```

旧项目中使用过 `stock_id`，新框架不再沿用该命名。数据库、因子文件、股票池文件在进入回测核心前都应标准化为 `trade_date` 和 `symbol`。

## 4. 因子输入格式

标准因子格式为宽表：

```text
index = trade_date
columns = symbol
values = factor value
```

例如：

```text
factor_dm_20d.loc["2026-05-15", "000001.XSHE"]
```

表示 `000001.XSHE` 在 `2026-05-15` 开盘前可用的因子值。

框架需要支持自动识别以下输入格式。

### 4.1 标准宽表

```text
index = trade_date
columns = symbol
values = factor value
```

处理规则：

- index 转为 `DatetimeIndex`。
- index 名称标准化为 `trade_date`。
- columns 转为字符串格式股票代码。
- values 转为数值。
- 按日期排序。

### 4.2 MultiIndex 长表

可能格式：

```text
index = MultiIndex(date, asset)
columns = factor value
```

处理规则：

- `date` 标准化为 `trade_date`。
- `asset` 标准化为 `symbol`。
- pivot 为标准宽表。

### 4.3 普通长表

可能字段：

```text
date / trade_date
asset / symbol
factor / factor_value / value
```

处理规则：

- 日期字段标准化为 `trade_date`。
- 股票字段标准化为 `symbol`。
- 因子值字段自动识别或由参数指定。
- pivot 为标准宽表。

### 4.4 H5 读取规则

如果 H5 文件只有一个 key，自动读取该 key。

如果 H5 文件包含多个 key，框架应报错并列出可用 key，要求用户显式指定 `factor_h5_key`。不应盲目读取第一个 key。

### 4.5 因子加载校验

加载完成后需要记录并校验：

- 因子文件路径。
- 原始格式：wide / multiindex_long / long。
- 日期范围。
- 日期数量。
- 股票数量。
- NaN 占比。
- Inf 占比。
- 0 值占比。
- 与行情数据 symbol 的交集比例。

如果因子 symbol 与行情 symbol 交集比例过低，应给出 warning 或直接报错。

## 5. 时间语义与收益定义

因子时间语义固定如下：

```text
factor_df.loc[t] 表示 t 日开盘前已经可用的因子值。
该因子由 t-1 日收盘后，基于 t-1 日及以前可见数据生成。
t 日开盘按因子排序建仓。
```

框架收益口径固定为 open-to-open：

```text
future_return_h[t] = open_price[t+h] / open_price[t] - 1
```

默认 horizon：

```python
horizons = [1, 5, 10, 20]
```

含义：

```text
horizon = 1：t 日开盘买入，t+1 个交易日开盘卖出。
horizon = 5：t 日开盘买入，t+5 个交易日开盘卖出。
horizon = 10：t 日开盘买入，t+10 个交易日开盘卖出。
horizon = 20：t 日开盘买入，t+20 个交易日开盘卖出。
```

所有日期偏移必须基于交易日历，不得使用自然日偏移。

## 6. 交易日历

交易日历来自主行情表 `stock_data.view_stock_qfq_adjusted_ohlcv_v2`：

```text
trade_calendar = sorted(unique(trade_date))
```

交易日历需要提供以下能力：

```python
get_next_trade_date(date, n=1)
get_previous_trade_date(date, n=1)
get_trade_window(end_date, n)
shift_trade_date_index(index, n)
```

以下逻辑都必须使用交易日历：

- future return 的 `t+h`。
- 120 / 250 / 750 交易日窗口回推。
- 上市天数计算。
- 分析窗口边界对齐。

## 7. 数据源与数据包

当前主流程使用 ClickHouse 4 张表：

```text
stock_data.view_stock_qfq_adjusted_ohlcv_v2
cn_stock_fundamentals.shares
cn_stock_fundamentals.is_st_stock
cn_stock_fundamentals.is_suspended
```

关联键：

```text
symbol + date
```

日期字段标准化为：

```text
trade_date
```

默认数据范围：

```text
2006-01-01 到最新数据，右开区间。
```

从主行情表读取：

```text
symbol
date
open_price
close_price
high_price
low_price
volume
turnover
limit_up
limit_down
```

从其他表读取：

```text
shares.circulation_a -> market_cap
is_st_stock.is_st_stock -> is_st
is_suspended.is_suspended -> is_suspended
```

框架内部标准数据包包括：

```text
trade_calendar
open_price_df
close_price_df
high_price_df
low_price_df
volume_df
amount_df
market_cap_df
limit_up_price_df
limit_down_price_df
is_st_df
is_suspended_df
listed_days_df
is_delisted_df
stock_pools
```

其中：

```text
amount_df <- turnover
market_cap_df <- circulation_a
limit_up_price_df <- limit_up
limit_down_price_df <- limit_down
```

所有宽表统一：

```text
index = trade_date
columns = symbol
```

## 8. 上市天数与退市状态

第一版默认从行情主表推导 `listed_days_df`：

```text
对每个 symbol 按 trade_date 排序。
listed_days = cumcount + 1。
```

默认参数：

```python
min_listed_days = 120
listed_days_source = "market_data"
```

后续预留官方上市/退市日期接口：

```python
listed_days_source = "listing_dates"
```

未来上市退市日期表建议格式：

```text
symbol
list_date
delist_date
```

切换到 `listing_dates` 后：

```text
listed_days[t, symbol] = list_date 到 t 之间的交易日数量。
is_delisted[t, symbol] = t >= delist_date。
```

第一版暂不强依赖 `is_delisted_df`。如果未来数据接入，则纳入可交易过滤。

## 9. 可交易过滤

参数：

```python
tradability_filter = True / False
```

默认建议开启，但如果用户已在因子生成阶段过滤，也可以关闭。

开启时，只过滤建仓日 `t` 的截面，不检查退出日 `t+h` 是否可卖。

过滤规则：

```text
high_price[t] >= limit_up[t] 过滤。
low_price[t] <= limit_down[t] 过滤。
is_st[t] == 1 过滤。
is_suspended[t] == 1 过滤。
listed_days[t] < min_listed_days 过滤。
open_price[t] 缺失或 <= 0 过滤。
```

future return 缺失处理：

```text
open_price[t+h] 缺失或 <= 0 时，该股票在对应 horizon 下 future_return 为 NaN。
该股票仅从对应 horizon 的 IC 和分组收益中排除。
```

过滤诊断需要记录：

- 过滤前股票数量。
- 股票池内股票数量。
- ST 过滤数量。
- 停牌过滤数量。
- 涨停触及过滤数量。
- 跌停触及过滤数量。
- 新股/次新股过滤数量。
- open 缺失或异常过滤数量。
- 过滤后有效股票数量。

## 10. 股票池设计

参数：

```python
selected_pools = ["all"]
```

`all` 是虚拟股票池，不对应任何文件，表示全市场：

```text
不做指数成分筛选，使用因子、行情和可交易过滤后的 symbol 交集。
```

如果用户传入：

```python
selected_pools = ["hs300_pool", "gz1000_pool"]
```

则所有计算在这两个股票池内分别独立完成。

每个指数池一个 CSV 文件，放在：

```text
/data/zhangyuan/pool
```

CSV 标准格式：

```csv
trade_date,symbol
2020-01-02,000001.XSHE
2020-01-02,000002.XSHE
```

`trade_date` 表示该日开盘前有效的指数成分。

股票池文件覆盖期：

```text
2020-01-01 到 2026-05-01
```

如果用户指定窗口超出某股票池覆盖期：

```text
实际输出窗口 = 用户窗口 与 pool 数据覆盖窗口的交集。
报告和图表注明实际使用范围。
warning 用户缺失了部分时间的股票池数据。
```

例如：

```text
用户窗口：2018-01-01 到 2020-12-31
pool 数据：2020-01-01 到 2026-05-01
实际输出：2020-01-01 到 2020-12-31
warning：2018-01-01 到 2019-12-31 无该 pool 成分数据，已跳过。
```

### 10.1 股票池注册表

```python
POOL_REGISTRY = {
    # 全市场：不使用指数成分股文件，不做股票池筛选；
    # 直接使用因子宽表、行情数据和可交易过滤后的股票交集
    "all": {
        "path": None,
        "display_name": "全市场",
        "is_virtual": True,
    },

    # 沪深300：标准指数
    "hs300_pool": {
        "path": "/data/zhangyuan/pool/hs300_pool.csv",
        "display_name": "沪深300",
        "is_virtual": False,
    },

    # 中证500：剔除沪深300指数样本及总市值前300名后，
    # 总市值排名靠前的500只股票构成
    "zz500_pool": {
        "path": "/data/zhangyuan/pool/zz500_pool.csv",
        "display_name": "中证500",
        "is_virtual": False,
    },

    # 中证1000：选取中证800指数样本以外的规模偏小且流动性好的
    # 1000只证券作为指数样本，与沪深300和中证500等指数形成互补
    "zz1000_pool": {
        "path": "/data/zhangyuan/pool/zz1000_pool.csv",
        "display_name": "中证1000",
        "is_virtual": False,
    },

    # 国证1000：由沪深北交易所中市值大、流动性好的1000只证券构成，
    # 反映市场大中盘证券的价格变动趋势
    "gz1000_pool": {
        "path": "/data/zhangyuan/pool/gz1000_pool.csv",
        "display_name": "国证1000",
        "is_virtual": False,
    },

    # 国证2000：由扣除总市值排名前1000只证券后市值大、
    # 流动性好的2000只证券组成，反映沪深北交易所小型证券的价格变动趋势
    "gz2000_pool": {
        "path": "/data/zhangyuan/pool/gz2000_pool.csv",
        "display_name": "国证2000",
        "is_virtual": False,
    },

    # 国证中小盘800：399401.XSHE，中小盘指数由巨潮中盘指数样本股
    # 与巨潮小盘指数样本股组合而成，用以反映沪深北交易所中小盘证券整体走势情况
    "gzMidsmallcap_pool": {
        "path": "/data/zhangyuan/pool/gzMidsmallcap_pool.csv",
        "display_name": "国证中小盘800",
        "is_virtual": False,
    },

    # 米筐微盘：866006.RI，米筐微盘股指数是由A股中市值居于后400的
    # 极小市值个股等权构建而成
    "miMicrocap_pool": {
        "path": "/data/zhangyuan/pool/miMicrocap_pool.csv",
        "display_name": "米筐微盘",
        "is_virtual": False,
    },
}
```

## 11. 分析窗口

日期区间采用右开：

```text
[start_date, end_date)
```

如果用户指定 `start_date` 和 `end_date`：

```text
按指定窗口分析。
```

如果用户不指定 `start_date`：

```text
必须指定 end_date。
默认输出 120 / 250 / 750 个交易日窗口。
窗口从 end_date 往前推。
```

默认：

```python
analysis_windows = [120, 250, 750]
```

如果 `end_date` 不是交易日，则把 `end_date` 作为右开边界，取 `< end_date` 的最后一个交易日作为窗口结束交易日。

为了计算未来收益，底层行情数据需要额外拉取 `max(horizons)` 个未来交易日的数据。但报告中的分析窗口仍然只显示用户指定或推导出的分析区间。

## 12. 因子值处理

默认处理规则：

```text
NaN 排除。
Inf / -Inf 排除。
0 值不排除，只统计占比。
默认不 winsorize。
默认不 zscore。
默认不做因子方向翻转。
```

保留可选参数：

```python
winsorize_factor = False
standardize_factor = False
```

第一版默认不启用，避免改变原始因子排序含义。

## 13. IC 计算

IC 默认使用 Spearman RankIC：

```text
IC_h[t] = corr_rank(factor[t], future_return_h[t])
```

默认 horizon：

```text
1D / 5D / 10D / 20D
```

最小样本数：

```python
min_ic_stocks = 30
```

如果某日某股票池内有效股票数低于 `min_ic_stocks`，当日 IC 记为 NaN。

IC 概览图：

```text
只展示 20D RankIC 的 20 日移动平均线。
```

累计 IC：

```text
对 1D / 5D / 10D / 20D 的每日 IC 分别做 cumsum。
四条线画在同一张图。
```

IC 统计表：

```text
IC mean
IC std
ICIR = mean / std
IC positive ratio
t-stat
```

## 14. 分组收益与多空收益

每日在每个股票池内独立按因子值分 10 组：

```text
G1 = 因子值最低组。
G10 = 因子值最高组。
```

不做因子方向自动翻转：

```text
如果 long_short 为负，保留负值。
```

分组方式：

```text
使用 rank 后 qcut，避免原始因子大量重复值导致 qcut 失败。
```

最小样本数：

```python
min_group_stocks = 10
```

组收益计算：

```text
先计算每日每组内股票等权平均 future_return_h。
再对选定时间窗口内的每日组收益做时间平均。
```

多空收益：

```text
long_short_h[t] = group_return_G10_h[t] - group_return_G1_h[t]
```

多空曲线：

```text
对 long_short_h[t] 构造累计曲线。
```

说明：

```text
horizon > 1 的多空收益序列是重叠样本评价曲线，不等同于严格单一组合净值。
```

## 15. 图表颜色

horizon 颜色固定：

```python
horizon_colors = {
    1: "blue",
    5: "orange",
    10: "green",
    20: "red",
}
```

累计 IC 图、分组收益柱状图、多空收益图都使用同一套颜色映射。

## 16. 数据质量诊断

每日统计：

```text
zero_ratio
nan_ratio
inf_ratio
valid_factor_count
pool_stock_count
after_filter_count
coverage_ratio = valid_factor_count / pool_stock_count
```

如果开启可交易过滤，还需要统计：

```text
limit_up_touch_count
limit_down_touch_count
st_count
suspended_count
new_stock_filtered_count
open_invalid_count
```

这些统计以时间序列形式保存，并在报告中画线图。

## 17. 模块化报告架构

默认执行全部内置模块：

```python
enabled_sections = "all"
```

也支持用户指定：

```python
enabled_sections = [
    "data_quality",
    "ic_overview",
    "cumulative_ic",
    "group_return",
    "long_short",
    "performance_metrics",
    "cross_pool_summary",
]
```

每个模块必须独立执行。除非后续模块明确依赖前面生成的中间数据，否则一个模块失败不能影响其他模块。

模块执行规则：

```text
每个 section 独立 compute。
每个 section 独立 render。
每个 section 捕获异常。
失败后写入 run_log，不中断主流程。
最终报告展示成功、跳过、失败和 warning 信息。
```

建议接口：

```python
class ReportSection:
    name: str
    dependencies: list[str]

    def compute(self, context):
        ...

    def render(self, context, result):
        ...
```

内置模块：

```text
data_quality
ic_overview
cumulative_ic
group_return
long_short
performance_metrics
cross_pool_summary
```

预留扩展模块：

```text
factor_correlation
barra_exposure
neutralized_ic
liquidity_diagnostics
industry_diagnostics
```

后续新增模块只需要注册，不应修改主流程调度逻辑。

## 18. Pool 内独立计算

所有核心计算必须在每个 pool 内独立完成。

如果：

```python
selected_pools = ["hs300_pool", "gz1000_pool"]
```

则框架分别构建：

```text
hs300_pool context
gz1000_pool context
```

每个 context 独立完成：

```text
股票池过滤
可交易过滤
因子有效值过滤
future return 对齐
RankIC
IC 移动平均
累计 IC
10 分组收益
多空收益
绩效指标
数据质量诊断
```

唯一跨 pool 的模块是：

```text
cross_pool_summary
```

它只读取各 pool 已经计算好的结果做比较，不把不同 pool 的股票混在一起重新计算。

## 19. 中间结果与展示层

流程固定为：

```text
先计算并保存中间结果。
再基于中间结果画图和生成报告。
```

推荐目录结构：

```text
run_dir/
  run_meta.json
  run_log.json
  report.html
  pools/
    all/
      artifacts/
        aligned_factor.parquet
        valid_mask.parquet
        future_returns_1d.parquet
        future_returns_5d.parquet
        future_returns_10d.parquet
        future_returns_20d.parquet
        daily_ic.parquet
        daily_group_returns.parquet
        daily_long_short_returns.parquet
        data_quality.parquet
        filter_summary.parquet
      tables/
        ic_stats.csv
        performance_metrics.csv
        group_return_summary.csv
      plots/
        ic_overview.png
        cumulative_ic.png
        group_return_bar.png
        long_short_curve.png
        data_quality.png
  cross_pool_summary/
    tables/
      pool_summary.csv
    plots/
      pool_comparison.png
```

中间结果是第一优先级，图表和 HTML 看板是展示层。后续应支持：

```python
render_from_artifacts(run_dir)
```

用于不重新读取 ClickHouse、不重新计算基础结果，只基于中间结果重新画图和生成报告。

## 20. 输出形式

第一版建议输出：

```text
HTML 报告
CSV / Parquet 中间结果和统计表
PNG 图表
JSON run_meta
JSON run_log
```

文档、报告标题、图表标题、warning 信息使用中文。

代码变量名、文件名、目录名使用英文，避免路径和编码问题。

图表标题示例：

```text
20日RankIC移动平均
累计RankIC
10分组未来收益
多空收益曲线
因子覆盖率与异常值占比
跨股票池指标对比
```

文件名示例：

```text
ic_overview.png
cumulative_ic.png
group_return_bar.png
long_short_curve.png
data_quality.png
```

## 21. 绩效指标

第一版在 `performance_metrics` 模块中计算：

```text
annualized return
volatility
sharpe
max drawdown
win rate
mean
std
t-stat
```

这些指标主要用于描述多空收益序列的表现。需要在报告中注明：

```text
horizon > 1 的多空序列为重叠样本评价序列，不等同于真实每日换仓组合净值。
```

## 22. 默认参数汇总

```python
selected_pools = ["all"]
horizons = [1, 5, 10, 20]
min_listed_days = 120
listed_days_source = "market_data"
tradability_filter = True
min_ic_stocks = 30
min_group_stocks = 10
analysis_windows = [120, 250, 750]
enabled_sections = "all"
winsorize_factor = False
standardize_factor = False
horizon_colors = {
    1: "blue",
    5: "orange",
    10: "green",
    20: "red",
}
```

## 23. 后续扩展方向

后续可能增加的模块包括：

### 23.1 因子相关性

用于评估当前因子和库内已有因子的相关性：

- 全样本相关性。
- 滚动相关性。
- 高相关因子列表。
- 因子冗余度诊断。

### 23.2 Barra 风格暴露

后续接入 Barra 或自建风格因子后，增加：

- 市值暴露。
- beta 暴露。
- 动量暴露。
- 波动率暴露。
- 流动性暴露。
- 行业暴露。
- 多空组合暴露随时间变化。

### 23.3 中性化 IC

支持行业、市值或 Barra 风格中性化后的 IC：

- raw RankIC。
- industry neutral RankIC。
- size neutral RankIC。
- Barra neutral RankIC。

### 23.4 流动性诊断

支持按成交额、成交量、换手率分层观察因子表现。

## 24. 关键设计原则

本框架需要坚持以下原则：

```text
默认保守。
日期透明。
股票池动态化。
pool 内独立计算。
计算和展示解耦。
中间结果优先落盘。
模块失败不影响整体输出。
主结果和扩展探索结果分离。
中文解释文档，英文代码命名。
```
