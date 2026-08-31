# Factor_Backtest_Platform 结果存储与查询设计

> 版本说明：本文最初形成于 `Factor_Backtest 2.4.0` 开发阶段，现随规模化成果迁移到 `Factor_Backtest_Platform 1.0.0`。本文中的结果 Schema 仍为 `2.0`，迁移不改变任何金融计算口径。文末 2.4.0 验收目录是只读历史记录，不属于 Platform 正式结果。

## 1. 背景

当前框架在 `artifact_level="none"` 时虽然不会写入因子矩阵、有效样本矩阵和未来收益矩阵，但仍会为每个股票池保存全部 section 表格和 PNG。表格中同时包含每日基础数据、累计曲线、窗口统计、报告摘要和兼容别名。随后框架又把完整 run 复制到 `latest/`，导致一次约 100 MB 的结果实际占用约 200 MB。

本次改造目标是在不改变任何回测计算口径的前提下，将结果输出改为适合后续网页看板使用的紧凑数据仓库，并保留按需生成静态报告的能力。

## 2. 本次范围

### 2.1 纳入范围

- 每次回测仍执行一次完整计算，不实现日频增量更新。
- 每次成功回测生成一个不可变的 `runs/<run_id>`。
- 使用 `latest.json` 指向最新成功 run，不再复制 `latest/` 实体目录。
- 每个每日基础表跨股票池合并后保存为压缩 Parquet。
- 累计曲线、移动平均、年度统计和窗口摘要从每日基础表动态派生。
- 保留现有 section 模块、参数开关、风险行业诊断、外部收益和公司诊断接口。
- 保留 `artifact_level="full"` 的排查能力。
- 保留按需生成 PNG 和 HTML 静态报告的能力。
- 为后续 FastAPI 和前端看板提供稳定的结果读取接口。

### 2.2 不纳入范围

- 不实现每日增量回测、历史区间 backfill、更新水位或收益成熟调度。
- 不开发前端页面和 HTTP API 服务。
- 不迁移、不删除已有 CSV、PNG、`latest/` 和历史 runs。
- 不修改因子输入格式、行情取数、股票池、可交易过滤和风险暴露取数。
- 不修改 IC、HAC、分组收益、重叠窗口折算、换手率或其他统计公式。

## 3. 结果生命周期

默认 `output_layout="latest_runs"` 时采用以下流程：

1. 创建 `<factor_root>/runs/.staging/<run_id>/`。
2. 在 staging 中完成计算、基础表写入、日志和可选静态报告。
3. 校验必要文件、核心 section 状态和 Parquet 可读性。
4. 将 staging 原子重命名为 `<factor_root>/runs/<run_id>/`。
5. 通过临时 JSON 和 `os.replace()` 原子更新 `<factor_root>/latest.json`。
6. 返回最终 run 路径。

只有完整成功发布的 run 才允许成为 latest。旧 run 不移动、不复制、不覆盖。

`output_layout="timestamp"` 保留历史兼容语义，不创建 `latest.json`。

## 4. 新目录结构

```text
<output_root>/<factor_name>/
  latest.json
  runs/
    <run_id>/
      manifest.json
      run_meta.json
      run_log.json
      data/
        data_quality.parquet
        daily_ic_spearman.parquet
        daily_ic_pearson.parquet
        factor_style_exposure_corr_spearman.parquet
        factor_style_exposure_corr_pearson.parquet
        style_neutralized_ic_spearman.parquet
        style_industry_neutralized_ic_spearman.parquet
        daily_group_returns.parquet
        within_industry_daily_group_returns.parquet
        group_style_exposure_daily.parquet
        group_industry_exposure_daily.parquet
        daily_group_membership_change.parquet
      artifacts/                  # 仅 artifact_level="full"
        <pool>/...
      plots/                      # 仅按需导出静态报告时生成
        <pool>/...
      report.html                 # 仅按需导出静态报告时生成
      diagnostics/                # 公司诊断启用时生成
```

所有股票池写入同一个同类 Parquet，通过显式 `pool` 列区分。Parquet 中索引字段也显式物化，读取后由结果加载器恢复为现有 DataFrame 索引。

## 5. Schema 版本

- Platform 产品和 Python 包版本为 `1.0.0`，distribution 名称为 `factor-backtest-platform`。
- 结果数据结构版本独立定义为 `2.0`。
- `manifest.json` 必须包含 `schema_version`，不能继续使用含义模糊的 `framework_version="v1"` 作为唯一 Schema 标记。
- `framework_version` 暂时保留，避免旧调用脚本立即失效。

## 6. 持久化数据边界

### 6.1 必须持久化的每日基础表

| 表名 | 来源 | 用途 |
|---|---|---|
| `data_quality` | `DataQualitySection` | 覆盖率、有效股票数、NaN、Inf 和零值统计 |
| `daily_ic_<method>` | `CumulativeICSection` | 累计 IC、移动平均、IC 统计、年度 IC |
| `factor_style_exposure_corr_<method>` | `FactorStyleExposureSection` | 风格暴露时间序列和摘要 |
| `style_neutralized_ic_<method>` | `StyleNeutralizedICSection` | 风格中性化 IC 全部派生结果 |
| `style_industry_neutralized_ic_<method>` | `StyleIndustryNeutralizedICSection` | 风格行业中性化 IC 全部派生结果 |
| `daily_group_returns` | `GroupReturnSection` | 分组摘要、累计分组收益、多空和绩效诊断 |
| `within_industry_daily_group_returns` | `WithinIndustryGroupReturnSection` | 行业内分组摘要和累计收益 |
| `group_style_exposure_daily` | `GroupExposureDiagnosticsSection` | 分组风格暴露时间序列和摘要 |
| `group_industry_exposure_daily` | `GroupExposureDiagnosticsSection` | 分组行业暴露时间序列和摘要 |
| `daily_group_membership_change` | `GroupTurnoverSection` | 分组名单变化曲线和摘要 |

启用公司诊断或 handoff 时，其专用输出保持现有职责，不混入上述通用前端数据表。

### 6.2 默认不持久化的派生表

- `daily_ic`、`cumulative_ic`、`ic_stats` 等兼容别名或累计表。
- `ic_overview_*`。
- `yearly_ic_stats_*`、`yearly_mean_ic_*`。
- `group_return_summary`、`group_cumulative_returns_*`。
- `layered_group_return_summary`。
- `daily_long_short_returns` 和全部累计多空表；它们可从 `daily_group_returns` 派生。
- `performance_metrics` 和 `performance_diagnostics`。
- `group_*_exposure_summary`。
- `group_turnover_*` 旧名称及摘要。
- `data_quality_counts`、`data_quality_ratios`。
- 绘图标签映射等报告专用表。

`artifact_level="full"` 仍可显式保存完整中间矩阵，不受基础结果白名单限制。

## 7. Parquet 约定

- 引擎固定使用项目现有依赖 `pyarrow`。
- 默认压缩使用 `zstd`，压缩等级采用低等级默认值。
- 每张逻辑表对应一个 Parquet 文件，不按日期拆成大量小文件。
- 每个文件必须包含 `pool` 列。
- 时间列使用 Arrow 日期时间类型，不保存为字符串。
- `NaN`、`Inf`、`-Inf` 原样保留。
- 写入采用临时文件后 `os.replace()`，避免半文件被读取。
- 生产结果缺少 pyarrow 时明确失败，不允许回退为 `.parquet.pkl`。
- 旧通用 `write_table()` 的 pickle 回退仅为历史 artifact 兼容，不用于 Schema 2.0 结果仓库。

## 8. manifest.json

至少包含：

```json
{
  "schema_version": "2.0",
  "package_version": "1.0.0",
  "run_id": "20260826_160000_000000",
  "factor_name": "factor_dm_20d",
  "status": "complete",
  "created_at": "2026-08-26T16:00:00+08:00",
  "selected_pools": ["all", "hs300_pool"],
  "return_labels": ["1d", "5d", "10d", "20d"],
  "return_horizon_days": {"1d": 1, "5d": 5, "10d": 10, "20d": 20},
  "ic_methods": ["spearman"],
  "date_ranges": {},
  "sections": {},
  "tables": {}
}
```

每张表记录路径、行数、列名、索引字段、股票池、日期范围和来源 section。每个 section 记录 `success`、`failed`、错误和 warning。

## 9. latest.json

```json
{
  "schema_version": "1.0",
  "factor_name": "factor_dm_20d",
  "run_id": "20260826_160000_000000",
  "relative_path": "runs/20260826_160000_000000",
  "completed_at": "2026-08-26T16:00:00+08:00"
}
```

`relative_path` 必须是因子结果根目录内的相对路径。加载器解析后要校验最终路径仍位于因子目录内，避免路径穿越。

## 10. 核心发布校验

核心计算包括：

- 因子与行情标准化和对齐。
- 未来收益构建。
- 每日 IC。
- 每日十组收益。
- 数据质量。

只要这些核心计算发生未捕获异常，run 不得发布。对于已启用 section：

- `data_quality`、`cumulative_ic`、`group_return` 失败时不得更新 latest。
- 风格、行业等可选诊断失败时允许发布，但 manifest 必须记录失败，前端不得混用旧 run 的同名数据。

## 11. 动态查询接口

`LoadedBacktestResult` 需要同时支持 Schema 1 和 Schema 2：

- Schema 1：读取旧 `latest/` 和 `pools/<pool>/tables/*.csv`。
- Schema 2：解析 `latest.json` 和 `data/*.parquet`。

通用接口：

```python
result = load_backtest_result(..., run="latest")
result.available_pools()
result.available_tables()
result.read_table("all", "daily_ic_spearman", start_date=..., end_date=...)
```

新增动态派生接口：

```python
result.ic_view(pool="all", method="spearman", start_date=..., end_date=...)
result.group_return_view(pool="all", start_date=..., end_date=...)
result.quality_view(pool="all", start_date=..., end_date=...)
```

`ic_view()` 返回选择范围内每日 IC、从零开始的累计 IC、20日移动平均、IC 统计和年度统计。移动平均允许读取开始日期之前19个交易日预热，但只返回选择范围。

`group_return_view()` 返回选择范围内每日分组收益、日等效累计分组收益、动态窗口摘要、多空序列和绩效诊断。所有计算复用现有 analytics/sections 公式，不在加载器中复制另一套统计逻辑。

## 12. 静态报告兼容

- `render_plots` 保留为旧参数。
- `export_static_report` 和 `render_plots` 在 Platform 中默认 `False`。
- `run_factor_backtest_data()` 强制 `export_static_report=False` 且不写 PNG。
- `render_factor_backtest_report(run_dir)` 从 Schema 2 基础表动态重建派生表、PNG 和 HTML。
- 静态报告生成的派生表只存在于内存，不重新写入 Parquet 或 CSV。
- 报告链接改为 Schema 2 的 `data/`、`plots/<pool>/` 和可选 `artifacts/<pool>/`。

## 13. 兼容策略

- 旧结果加载保持可用。
- 旧 CSV 表名读取保持可用。
- 对 Schema 2 请求旧派生表名时，加载器按需计算并返回，避免下游立即中断。
- `BacktestRunResult.latest_dir` 暂时保留，但在 Schema 2 中返回 latest 所指向的最终 run 目录；新增 `latest_index_path` 明确表示索引文件。
- README 和使用手册明确不再保证 `<factor>/latest/report.html` 实体路径。
- 不创建软连接。
- `output_layout="timestamp"` 保持兼容。

## 14. 故障与并发

- 同一因子完整回测使用简单文件锁，防止同时发布两个 latest。
- staging 目录不参与结果发现。
- 进程在 latest 更新前失败时，旧 latest 保持不变。
- 新 run 完成但 latest 更新失败时，新 run 仍可通过 run_id 访问，可再次执行索引修复。
- 初版不自动删除 staging 和历史 run，避免误删；后续可增加显式清理命令。

## 15. 测试计划

### 15.1 单元测试

- Parquet 跨股票池合并、索引恢复和日期过滤。
- NaN、Inf、-Inf 保留。
- manifest 和 latest.json 原子写入。
- latest 路径穿越校验。
- staging 不会被 latest 加载器发现。
- 核心 section 失败不发布 latest。
- 可选 section 失败可发布且状态正确。
- Schema 1 旧结果继续可读。
- Schema 2 旧派生表名可动态读取。
- 选择日期后累计 IC 从零开始。
- 20日移动平均使用19日预热。
- 选择日期后累计分组收益从净值1开始。
- 外部收益及无 `horizon_days` warning 保持现有行为。
- 数据模式不生成 PNG 和 HTML。
- 按需重渲染能生成完整报告。

### 15.2 回归测试

- 全部现有测试继续通过或只因新目录契约进行有依据的更新。
- `python -m compileall -q factor_backtest tests examples` 通过。
- 使用固定随机种子的小型数据，对比改造前后所有内存 section 表，数值完全一致。
- 对比 Schema 1 CSV 与 Schema 2 Parquet 的基础表，允许差异仅来自旧 CSV 四位小数截断；以内存原值为权威。

### 15.3 性能与体量测试

- 记录旧 CSV、PNG、latest 镜像总大小。
- 记录新 Parquet 和可选静态报告总大小。
- 测试单股票池、1年/3年/5年和自定义区间读取时间。
- 常用查询目标为本地或服务器低并发环境下约1秒内完成；大型行业暴露查询目标为3秒内完成。

## 16. 服务器验收

本地测试全部通过后：

1. 获得明确授权后，将 Platform 文件上传到 `/app/workspace/zhangyuan/Factor_Backtest_Platform`，不得改动服务器 Classic 目录。
2. 使用 `/app/workspace/zhangyuan/.venv_factor_backtest_platform/bin/python` 编译和运行测试，确认 `factor_backtest.__file__` 指向 Platform。
3. 从已有结果中随机选择至少3个因子，覆盖：
   - 普通单一 Spearman 回测。
   - 多股票池回测。
   - 启用风格行业诊断或外部收益的回测。
4. 从旧 `run_meta.json` 读取原参数，确保因子路径、日期、股票池、过滤、IC 方法、sections 和风险数据配置一致。
5. 新结果写入 `/data/zhangyuan/Factor_Backtest_Platform_Result` 下的独立验收因子目录，不能覆盖 Classic 或历史验证结果。
6. 对比每日 IC、IC 统计、分组收益、行业内收益、换手和风险暴露基础表。
7. 汇总最大绝对误差、缺失位置差异、行列范围、运行时间和目录体量。
8. 只有数值一致且目录结构、加载和报告重渲染通过后，才确认服务器验收完成。

## 17. 回滚

- 本次不修改旧结果。
- 服务器验收使用独立输出根目录。
- 代码更新前记录服务器当前 Git commit。
- 若验收失败，恢复服务器代码到原 commit；新验收目录保留用于排查，不切换生产调用脚本。

## 18. 完成标准

- 新 run 不再复制 `latest/`。
- `artifact_level="none"` 时只保存每日基础 Parquet、元数据和可选静态报告。
- 新结果可在不读取因子矩阵和行情矩阵的情况下按任意日期范围重建主要报告。
- 所有现有诊断能力仍可通过基础数据或按需派生获得。
- 本地测试、编译、体量测试和服务器真实因子对照全部通过。

## 19. 实施验收记录

2026-08-26 完成 2.4.0 本地和服务器验收：

- 本地 750 交易日 × 1000 股票 × 4 horizon 合成回测使用数据模式时，单次 run 共 7 个文件、0.344 MiB；一年范围的 IC、分组和质量视图分别约 0.014、0.033、0.006 秒，未生成 CSV、PNG 或 HTML。
- 服务器使用同步前 2.3.0 控制代码和 2.4.0 新代码，在同一实时数据快照、同一 `factor_macd_delta`、同一四股票池和同一参数下比较 64 个“股票池 × 基础/派生表”。全部通过，最大绝对误差为 `5.0e-5`，等于旧 CSV 四位小数截断上限。
- `factor_macd_delta` 的 2.3 控制单 run 为 62.204 MiB，新数据模式为 10.268 MiB，减少 83.49%；新结果写入约 1.03 秒。
- `factor_dm_20d` 双 IC 数据模式为 11.056 MiB，旧 latest 为 110.563 MiB，减少 90.0%；Spearman/Pearson 三年视图分别约 0.035/0.029 秒。
- `factor_macd_slope_diff` 数据模式为 10.213 MiB，旧 latest 为 106.900 MiB，减少 90.45%；三年 IC/分组视图分别约 0.058/0.114 秒。
- 从 `factor_dm_20d` 的真实 Schema 2 Parquet 按需重建报告耗时约 43.745 秒，生成 164 张 PNG 和一份约 474 KiB 的 HTML。生成图片后目录约 63 MiB，说明静态图片仍是主要空间来源。
- 旧历史 latest 与当前重跑结果存在数值差异，但表日期和行数一致。使用同快照 2.3 控制代码后差异消失，因此确认来源是 ClickHouse、动态池或引用数据的历史修订，而不是 2.4.0 计算逻辑变化。

服务器验收结果使用独立目录：

```text
/data/zhangyuan/Factor_Backtest_Result_v2_4_0_validation
/data/zhangyuan/Factor_Backtest_Result_v2_3_0_control
```

生产结果目录未被覆盖或删除。
