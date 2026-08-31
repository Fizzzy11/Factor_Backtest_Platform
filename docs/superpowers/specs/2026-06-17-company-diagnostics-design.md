# Company Diagnostics 设计方案

> 历史说明：本文形成于 Classic 2.2.1，文中的 `factor_backtest` 路径属于历史包名；Platform 1.0.0 的现行包名为 `factor_backtest_platform`。2.4.0 起不再复制 `latest/diagnostics/`，diagnostics 只保存在不可变 `runs/<run_id>/diagnostics/`，并由 `latest.json` 指向最新成功 run；本文中的计算和 JSON schema 设计仍然有效。

本文档是 `CompanyDiagnosticsConfig` 与四类正式诊断 JSON 的完整设计说明。目标是让后续开发者在没有聊天上下文的情况下，也可以按本文档重新实现同一功能。

## 1. 背景与目标

公司侧 `company_diagnostics_task_brief.md` 要求 Factor_Backtest 在真实回测完成后，输出四类正式诊断 JSON：

```text
diagnostics/
  spanning.json
  crowding.json
  mechanism_consistency.json
  diversity.json
```

这些 JSON 是 alpha-lab 下游读取的代码契约，字段名、文件名、JSON key 和枚举值必须保持英文原样，不能翻译或重命名。

本功能要回答四个问题：

- `crowding.json`：候选因子相对公司 peer book 是否拥挤。
- `diversity.json`：候选因子相对已有因子库是否足够新。
- `mechanism_consistency.json`：因子表现是否和经济机制假设、市场 regime 一致。
- `spanning.json`：候选因子相对 production book 是否仍有增量 alpha。

## 2. 与 Handoff 的区别

`HandoffConfig` 是 v2.2.0 第一轮平台验收交付包，允许输出 `pending(company)` 占位 diagnostics。它的目标是证明一次真实回测能生成可读取的 `sample_latest/`。

`CompanyDiagnosticsConfig` 是正式诊断输出，只能在真实计算成功时写 `status="computed"`。

正式 company diagnostics 必须遵守：

- 没有计算就不写文件。
- 不写 `pending`。
- 不写空 JSON。
- 不用 `0`、`null` 或占位行填补不存在的 pool/horizon。
- 文件一旦存在，下游就会认为该诊断已经计算完成。

## 3. 输出目录

原任务书只要求 `latest/diagnostics/`。后续确认后，本框架同时保留历史版本和最新版本：

```text
<output_root>/<factor_name>/runs/<run_time>/diagnostics/
<output_root>/<factor_name>/latest/diagnostics/
```

实现方式：

1. runner 先写入 `run_dir/diagnostics/`。
2. 再执行现有 `_sync_latest_dir(run_dir, latest_dir)`。
3. latest 因此自动包含同一份 diagnostics。

## 4. 固定代码契约

### 4.1 固定 pool key

正式 company diagnostics 只应该输出以下 pool key：

```text
all
hs300_pool
zz1000_pool
zz2000_pool
```

如果普通回测选择了其他 pool，例如 `gz1000_pool`、`gz2000_pool`、`miMicrocap_pool`，这些 pool 可以正常生成普通 factor backtest 报告，但不应写入正式 company diagnostics JSON。

实现规则：

```python
CONTRACT_POOLS = {"all", "hs300_pool", "zz1000_pool", "zz2000_pool"}
diagnostic_pool_contexts = {
    pool: ctx for pool, ctx in pool_contexts.items()
    if pool in CONTRACT_POOLS
}
```

如果四个 contract pool 一个都没有可用上下文，则不生成对应诊断文件。

### 4.2 固定 horizon key

正式 company diagnostics 只应该输出以下 horizon key：

```text
"1"
"5"
"10"
"20"
```

这些 key 是字符串，不是整数。

普通 Factor_Backtest 支持外部收益和自定义收益标签，但 company diagnostics 第一版不把任意 external return label 写进正式 JSON。只有能映射为 1/5/10/20 交易日 horizon 的收益才进入 diagnostics。

实现规则：

```python
CONTRACT_HORIZONS = {"1", "5", "10", "20"}
```

对于内置 horizon：

```python
1d  -> "1"
5d  -> "5"
10d -> "10"
20d -> "20"
```

对于 external return：

- 如果 `return_horizon_days` 是 `1/5/10/20`，可以映射到对应 key。
- 如果没有 `horizon_days` 或不属于 `1/5/10/20`，不进入正式 diagnostics。

## 5. 输入数据

第一版只实现文件输入，保留 source 字段给后续数据库接入。支持 CSV 和 Parquet。

### 5.1 候选因子输入

候选因子来自当前 Factor_Backtest 的标准输入：

```text
index = trade_date
columns = symbol
values = factor value
```

因子值语义遵守项目 AGENTS.md：

```text
每日因子值默认是收盘后构建，当天所有可用数据均已可见。
```

因此框架用下一交易日开盘进入，未来收益仍然是 open-to-open。

### 5.2 Production Book

用于 `spanning.json`，代表已上线/在用的生产因子库。

文件格式是长表，等价于多张因子宽表按 `factor_id` 叠在一起：

```csv
date,symbol,factor_id,value
2026-01-02,000001.XSHE,rev_20d_v3,0.32
2026-01-02,000002.XSHE,rev_20d_v3,-0.18
2026-01-02,000001.XSHE,liq_amihud_v2,1.41
```

兼容列名：

```text
date:   date / trade_date
symbol: symbol / stock_id / asset
value:  value / factor_value / factor
id:     factor_id
```

内部加载后转换为：

```python
FactorBook.factors: dict[str, DataFrame]
# factor_id -> wide frame [trade_date x symbol]
```

### 5.3 Peer Book

用于 `crowding.json` 和 `diversity.json`。

peer book 通常比 production book 更宽，可以包含：

```text
production + promoted + research
```

格式同 production book。

### 5.4 Factor Meta

公司侧推荐的完整内部结构是：

```text
factor_values: (date, stock_id, factor_id) -> value
factor_meta:   factor_id -> {status: production|promoted|research, direction, version, asof}
factor_ls_pnl: (date, factor_id) -> ls_return
```

第一版简化为由调用方提前拆好：

- `production_book_path`：只包含 production 因子。
- `peer_book_path`：包含 peer 因子集合。

`factor_meta_path` 已在配置中预留，但第一版不使用它自动过滤 status。后续可以扩展为：

```python
production_book = factor_values where factor_meta.status == "production"
peer_book = factor_values where factor_meta.status in {"production", "promoted", "research"}
```

### 5.5 Factor LS PnL

`factor_ls_pnl_path` 已预留，用于未来计算 `max_abs_returncorr`。

第一版不接入该数据，因此：

- 不输出 `max_abs_returncorr`。
- 不写 `max_abs_returncorr: null`。

后续接入时建议格式：

```csv
date,factor_id,ls_return
2026-01-02,rev_20d_v3,0.0031
```

### 5.6 Regime

用于 `mechanism_consistency.json`。

输入是布尔宽表：

```csv
date,bull,bear,high_vol,low_vol
2026-01-02,true,false,false,true
2026-01-05,true,false,true,false
```

设计约束：

- regime key 是固定离散标签。
- 值是布尔值，表示该日期是否属于该 regime。
- 标签可以重叠，例如某天可以同时是 `bull` 和 `high_vol`。
- 后续如果原始 regime 是连续变量，需要先离散化成固定标签后再输入。

默认标签：

```python
["bull", "bear", "high_vol", "low_vol"]
```

## 6. 配置设计

新增配置类：

```python
CompanyDiagnosticsConfig(
    enabled=False,
    production_book_source="file",
    peer_book_source="file",
    regime_source="file",
    production_book_path=None,
    peer_book_path=None,
    factor_meta_path=None,
    factor_ls_pnl_path=None,
    regime_path=None,
    baseline_suite_id=None,
    baseline_book_version=None,
    peer_book_version=None,
    peer_pool_id="research+promoted",
    hypothesis_direction="unknown",
    idea_id=None,
    version=1,
    regime_labels=["bull", "bear", "high_vol", "low_vol"],
    neutralization_layers=["production_book"],
    spanning_topk=20,
    spanning_rounds=3,
    top_spanning_factors=5,
    topk_overlap_k=50,
    crowding_threshold=0.7,
    min_similarity_stocks=30,
    framework_version="Factor_Backtest_2_2_1",
)
```

挂载到：

```python
BacktestConfig.diagnostics
```

默认关闭：

```python
BacktestConfig().diagnostics.enabled is False
```

## 7. Runner 集成设计

该功能不是 report section。

原因：

- 它不生成 HTML 图表。
- 它不属于单个 section 的表格/图形展示。
- 它需要跨 pool 统一组织 JSON。
- 它是机器可读的正式交付物。

runner 集成点：

```text
run_factor_backtest
  1. resolve config
  2. build returns
  3. for each pool:
       - apply pool
       - apply tradability filter
       - compute daily IC
       - compute group returns
       - compute long-short
       - run selected report sections
       - save minimal diagnostic context
  4. export_company_diagnostics(run_dir, cfg, diagnostic_contexts)
  5. write run_meta.json / run_log.json
  6. write report.html
  7. sync latest
  8. optional handoff export
```

每个 pool 只缓存 diagnostics 必需上下文：

```python
diagnostic_contexts[pool_name] = {
    "factor": filtered_factor,
    "future_returns": future_returns,
    "daily_group_returns": daily_group_returns,
    "return_horizon_days": return_horizon_days,
}
```

该设计不会改变现有 section 的计算结果、表名、图名和 report 结构。

## 8. 通用 JSON 规则

每个已生成文件必须包含：

```json
{
  "status": "computed",
  "meta": {}
}
```

`meta` 基础字段：

```json
{
  "as_of_start": "2020-01-02",
  "as_of_end": "2026-06-10",
  "framework_version": "Factor_Backtest_2_2_1",
  "n_trading_days": 1500
}
```

按文件补充：

- `spanning.meta.baseline_book_version`
- `crowding.meta.peer_book_version`
- `diversity.meta.peer_book_version`

建议 coverage：

```json
{
  "coverage": {
    "pools": ["all", "hs300_pool"],
    "horizons": ["1", "5", "10", "20"]
  }
}
```

规则：

- JSON number 必须是真正数字，不是字符串。
- NaN、inf、-inf 不能写入 JSON。
- `null` 只用于明确允许的字段：
  - `operator_graph_similarity`
  - `sign_matches_hypothesis` when direction unknown
  - `regime_sign_consistency` when direction unknown
- 其他缺失字段直接省略。

## 9. Similarity 统一口径

`crowding` 和 `diversity` 必须使用同一套 similarity 口径。

每日截面相似度：

```python
rankcorr_t = corr(rank(candidate_t), rank(peer_t))
abs_rankcorr_t = abs(rankcorr_t)
```

跨日期聚合：

```python
mean_abs_rankcorr = mean(abs_rankcorr_t over valid dates)
peak_abs_rankcorr = max(abs_rankcorr_t over valid dates)
n_obs = number of valid dates
```

peer 间选择：

```python
max_corr_peer = peer with largest mean_abs_rankcorr
max_abs_rankcorr = max(mean_abs_rankcorr)
```

有效日期要求：

- 候选因子和 peer 因子当日对齐。
- 对齐后有效股票数大于等于 `min_similarity_stocks`。
- 候选值、peer 值都必须是有限数。

## 10. spanning.json

### 10.1 输出目的

判断候选因子在控制 production book 后，是否仍有增量 alpha。

### 10.2 输出结构

```json
{
  "status": "computed",
  "meta": {
    "as_of_start": "2020-01-02",
    "as_of_end": "2026-06-10",
    "baseline_book_version": "production_book_2026Q2",
    "framework_version": "Factor_Backtest_2_2_1",
    "n_trading_days": 1500,
    "coverage": {
      "pools": ["all"],
      "horizons": ["1", "5", "10", "20"]
    }
  },
  "baseline_suite_id": "production_book_2026Q2",
  "neutralization_layers": ["production_book"],
  "spanning_method": {
    "type": "iterative_topk_residualization",
    "topk": 20,
    "rounds": 3
  },
  "top_spanning_factors": [
    {
      "factor_id": "rev_20d_v3",
      "contribution": 0.62,
      "contribution_type": "abs_rankcorr"
    }
  ],
  "pools": {
    "all": {
      "top_spanning_factors": [],
      "horizons": {
        "1": {
          "incremental_ic": 0.012,
          "incremental_ic_t": 3.8,
          "incremental_r2": 0.001,
          "n_obs": 500,
          "residualization_rounds": []
        }
      }
    }
  }
}
```

### 10.3 算法

对每个 contract pool 独立计算。

步骤：

1. 从候选因子 residual 开始：

```python
residual = candidate_factor
selected_factor_ids = []
```

2. 每轮在尚未选择的 production 因子中，计算 residual 与每个 production 因子的 mean abs RankCorr。

3. 选择最相关的 `spanning_topk` 个 production 因子。

4. 对每个日期、每个 pool 内做等权截面 OLS：

```text
residual_t ~ selected_production_factors_t
```

5. 用 OLS residual 更新 residual。

6. 重复 `spanning_rounds` 轮。

7. 最终 residual 用来计算每个 contract horizon 的：

```python
incremental_ic = mean(daily_spearman_ic(final_residual, future_return_h))
incremental_ic_t = t_stat(daily_spearman_ic)
```

8. `incremental_r2`：

对每个日期做两个回归：

```text
baseline: future_return_t ~ selected_production_factors_t
full:     future_return_t ~ selected_production_factors_t + final_residual_t
```

然后：

```python
incremental_r2_t = r2_full_t - r2_baseline_t
incremental_r2 = mean(incremental_r2_t)
```

### 10.4 top_spanning_factors

`top_spanning_factors` 的 `contribution` 使用候选因子与 production 因子的 `mean_abs_rankcorr`。

必须写：

```json
"contribution_type": "abs_rankcorr"
```

全局 `top_spanning_factors` 基于 `all` pool；如果没有 `all`，使用第一个可计算 contract pool。

每个 pool 内也保留自己的 `top_spanning_factors`。

## 11. crowding.json

### 11.1 输出目的

判断候选因子相对 peer book 是否拥挤。

### 11.2 输出粒度

按 contract pool 输出，不按 horizon 输出。

### 11.3 输出结构

```json
{
  "status": "computed",
  "meta": {
    "as_of_start": "2020-01-02",
    "as_of_end": "2026-06-10",
    "peer_book_version": "promoted_research_book_2026Q2",
    "framework_version": "Factor_Backtest_2_2_1",
    "n_trading_days": 1500,
    "coverage": {
      "pools": ["all", "hs300_pool"]
    }
  },
  "pool_id": "research+promoted",
  "pools": {
    "all": {
      "n_peers": 37,
      "max_abs_rankcorr": 0.62,
      "peak_abs_rankcorr": 0.78,
      "max_corr_peer": "rev_20d_v3",
      "n_peers_above_0.7": 2,
      "topk_overlap": 0.41,
      "topk_k": 50,
      "n_obs": 500,
      "operator_graph_similarity": null,
      "operator_graph_similarity_status": "pending(dsl)"
    }
  }
}
```

### 11.4 topk_overlap

对 `max_corr_peer` 计算。

每日：

```python
candidate_top = highest K candidate stocks
candidate_bottom = lowest K candidate stocks
peer_top = highest K peer stocks
peer_bottom = lowest K peer stocks

top_overlap = len(candidate_top & peer_top) / K
bottom_overlap = len(candidate_bottom & peer_bottom) / K
daily_overlap = (top_overlap + bottom_overlap) / 2
```

最终：

```python
topk_overlap = mean(daily_overlap over valid dates)
```

如果有效股票数小于 `2K`，则使用：

```python
K_eff = min(topk_overlap_k, floor(n_valid / 2))
```

## 12. diversity.json

### 12.1 输出目的

判断候选因子相对已有 peer book 的新颖度。

### 12.2 输出粒度

只基于 `all` pool 输出一份。

如果本次回测没有 `all` pool，不生成 `diversity.json`。

### 12.3 输出结构

```json
{
  "status": "computed",
  "meta": {
    "as_of_start": "2020-01-02",
    "as_of_end": "2026-06-10",
    "peer_book_version": "promoted_research_book_2026Q2",
    "framework_version": "Factor_Backtest_2_2_1",
    "n_trading_days": 1500,
    "coverage": {
      "pools": ["all"]
    }
  },
  "idea_id": "idea_quiet_001",
  "version": 1,
  "max_similarity_to_book": 0.66,
  "novelty": 0.34,
  "max_similarity_peer": "rev_20d_v3",
  "similarity_type": "mean_abs_rankcorr",
  "n_obs": 500
}
```

### 12.4 算法

使用与 crowding 完全一致的 similarity 口径：

```python
max_similarity_to_book = max_abs_rankcorr on all pool
novelty = 1 - max_similarity_to_book
```

如果 peer book 为空或没有有效相似度，不生成 `diversity.json`，不能硬填 `novelty=1`。

`avg_pairwise_similarity` 第一版不计算。同一 idea 历史版本比较由后续 alpha-lab 或扩展功能处理；没有输入时省略该字段。

## 13. mechanism_consistency.json

### 13.1 输出目的

判断因子表现是否和机制假设方向、市场 regime 一致。

### 13.2 输出粒度

按 contract pool 和 contract horizon 输出。

### 13.3 输出结构

```json
{
  "status": "computed",
  "meta": {
    "as_of_start": "2020-01-02",
    "as_of_end": "2026-06-10",
    "framework_version": "Factor_Backtest_2_2_1",
    "n_trading_days": 1500,
    "coverage": {
      "pools": ["all"],
      "horizons": ["1", "5", "10", "20"]
    }
  },
  "hypothesis_direction": "unknown",
  "regimes": ["bull", "bear", "high_vol", "low_vol"],
  "pools": {
    "all": {
      "horizons": {
        "1": {
          "group_monotonicity": 0.93,
          "n_obs": 500,
          "regime_detail": {
            "bull": {
              "ic": 0.018,
              "sign_matches_hypothesis": null,
              "n_obs": 220
            }
          },
          "regime_sign_consistency": null
        }
      }
    }
  }
}
```

### 13.4 group_monotonicity

取当前 pool、当前 horizon 的日度分组收益：

```text
daily_group_returns[trade_date, horizon, group] -> group_return
```

先对每个 group 取时间均值：

```python
mean_return_g = mean(group_return over dates)
```

然后计算：

```python
group_monotonicity = corr(rank(group_number), rank(mean_return_g))
```

这是 Spearman 单调性。

### 13.5 regime_detail

先计算当前 pool、当前 horizon 的每日 Spearman IC：

```python
daily_ic_t = corr(rank(candidate_factor_t), rank(future_return_t))
```

对每个 regime label：

```python
regime_dates = dates where regime[label] is True
regime_ic = mean(daily_ic_t on regime_dates)
n_obs = number of valid IC dates in that regime
```

`hypothesis_direction`：

```text
high_is_long  -> regime_ic > 0 is match
high_is_short -> regime_ic < 0 is match
unknown       -> sign_matches_hypothesis = null
```

如果 direction 是 `unknown`：

```json
"regime_sign_consistency": null
```

如果 direction 已知：

```python
regime_sign_consistency = mean(sign_matches_hypothesis over regimes with valid IC)
```

### 13.6 event_window_sign

原任务书提到 `event_window_sign` 仅事件驱动型因子填写。第一版不做事件驱动识别，也没有事件窗口输入，因此不输出该字段。

## 14. 错误处理

原则：company diagnostics 不应该因为某一类外部诊断输入缺失而阻断普通回测。

规则：

- `diagnostics.enabled=False`：完全跳过，不创建 `diagnostics/`。
- book path 为 `None`：跳过依赖该 book 的诊断。
- 文件不存在：写入 `run_log.warnings`，跳过对应诊断。
- 文件格式错误：写入 `run_log.warnings`，跳过对应诊断。
- `source="clickhouse"`：第一版写 warning 并跳过，因为数据库接入尚未实现。
- 没有任何 JSON 被写出：删除空 `diagnostics/` 目录。

## 15. 性能设计

第一版采用清晰优先的实现：

- book 长表加载后按 `factor_id` pivot 成多个宽表。
- similarity 按 factor_id 循环。
- residualization 按日期做等权 OLS。
- spanning 按 pool × horizon 计算。

复杂度主要来自：

```text
candidate vs all peer/production factors 的 RankCorr
```

后续优化方向：

- 对 book 做日期/股票对齐缓存。
- 对 rank 矩阵预计算。
- 按 factor_id 分块计算 similarity。
- 对 selected production factors 的截面矩阵复用。
- 如果库特别大，可先只对 `all` pool 粗筛 top factors，再对各 pool 精算。

## 16. 当前实现状态

已实现：

- `CompanyDiagnosticsConfig`
- `factor_backtest/company_diagnostics.py`
- CSV/Parquet book loader
- CSV/Parquet regime loader
- run/latest 双目录输出
- missing input skip
- computed-only JSON rule
- spanning incremental IC / incremental R2
- crowding mean abs RankCorr / peak / topk overlap
- diversity all-pool novelty
- mechanism group monotonicity / regime detail / unknown direction null
- example 参数
- README 和使用手册说明
- pytest 覆盖

需要补充或修正：

- 正式 diagnostics 输出时过滤到固定 contract pools。
- 正式 diagnostics 输出时过滤到固定 contract horizons。
- `coverage` 中补充 `horizons`。
- 如需 `max_abs_returncorr`，接入 `factor_ls_pnl_path`。
- 如需自动拆 production/peer，接入 `factor_meta_path`。

## 17. 测试设计

新增测试应覆盖：

1. 默认关闭时，不生成 `diagnostics/`。
2. production/peer/regime 输入齐全时，生成四个 computed JSON。
3. `runs/<run_time>/diagnostics/` 和 `latest/diagnostics/` 同时存在。
4. 缺少 production/peer book 时，只生成可计算的 `mechanism_consistency.json`。
5. 输出 JSON 不包含 pending。
6. `operator_graph_similarity` 可以为 `null`。
7. `hypothesis_direction="unknown"` 时方向字段为 `null`。
8. 只输出 contract pools。
9. 只输出 contract horizons。
10. external return 不应误写非 contract horizon key。

验证命令：

```bash
python -m pytest tests -q
```

本地最近一次验证结果：

```text
86 passed
```

## 18. 实现状态更新

以下设计约束已经在当前实现中补齐：

- 正式 diagnostics 只输出固定 contract pools：`all`、`hs300_pool`、`zz1000_pool`、`zz2000_pool`。
- `spanning.json` 和 `mechanism_consistency.json` 只输出固定 contract horizons：`"1"`、`"5"`、`"10"`、`"20"`。
- 非 contract pool 仍可正常参与普通 Factor_Backtest，但不会进入正式 company diagnostics JSON。
- 非 contract horizon 或无法映射到 1/5/10/20 的 external return 不会进入正式 company diagnostics JSON。
- horizon 型 diagnostics 的 `meta.coverage` 会同时写 `pools` 和 `horizons`。
- `regime` 输入支持字符串布尔值解析，例如 `true/false/1/0/yes/no`；无法识别的字符串会报格式 warning 并跳过该 regime 输入。
- `n_peers_above_0.7` 固定按 `0.7` 阈值计算，不受 `crowding_threshold` 配置影响，避免字段名和计算口径不一致。

仍未实现、保持预留：

- `factor_ls_pnl_path` 尚未接入，因此不输出 `max_abs_returncorr`。
- `factor_meta_path` 尚未用于自动拆分 production/peer book，当前仍要求调用方提前传入 `production_book_path` 和 `peer_book_path`。
