# Factor_Backtest_Platform Dashboard 交互式前端看板开发任务书

## 1. 文档目的

本任务书用于指导 `Factor_Backtest_Dashboard` 独立项目的设计、实现、测试和中文文档编写。

本项目为 `Factor_Backtest_Platform 1.0.0` 及以后兼容版本提供组内只读交互式分析看板。看板负责发现、读取、筛选和展示 Platform 已发布结果，不负责生成因子、不重新执行因子回测，不访问 ClickHouse 或原始因子数据重算指标，也不得改变任何金融计算口径。

执行本任务前必须先审计现有代码和真实结果结构。若任务书中的接口名称、字段名称或文件布局与源码不一致，以源码和现有测试为准，并在实施计划中说明差异。不得为了迎合任务书描述而复制已有逻辑或绕开公共接口。

## 2. 项目关系与路径

### 2.1 核心回测包

本地目录：

```text
D:\hytp\Factor_Backtest_Platform
```

服务器目录：

```text
/app/workspace/zhangyuan/Factor_Backtest_Platform
```

核心回测包职责：

- 因子和市场数据加载。
- 股票池和可交易过滤。
- IC、收益、HAC、分组、换手率、风险暴露等计算。
- Schema 1/2 结果读取。
- `load_backtest_result()`、`LoadedBacktestResult` 和 view API。
- 回测结果存储及元数据管理。

### 2.2 前端看板项目

本地目录：

```text
D:\hytp\Factor_Backtest_Dashboard
```

服务器建议目录：

```text
/app/workspace/zhangyuan/Factor_Backtest_Dashboard
```

前端看板职责：

- 只读发现回测结果。
- 调用 Factor_Backtest_Platform 的公共读取和 view API。
- 管理页面筛选条件和进程内缓存。
- 动态生成图表和表格。
- 提供当前视图的图表及 CSV 下载。
- 展示完整、空缺、失败和损坏状态。

两个项目必须保持独立。核心回测包不得依赖 Dash、Plotly 或浏览器测试依赖。前端看板依赖 `factor-backtest-platform>=1.0.0`，并只从 `factor_backtest_platform` 导入公共读取接口；Classic 的 `factor_backtest` 可以在同一环境共存。

### 2.3 结果目录

正式结果根目录：

```text
/data/zhangyuan/Factor_Backtest_Platform_Result
```

2.4.0 只读验证目录：

```text
/data/zhangyuan/Factor_Backtest_Result_v2_4_0_validation
```

服务器 Python：

```text
/app/workspace/zhangyuan/.venv/bin/python
```

看板不得向以上结果目录写入缓存、图片、日志、临时文件、索引或修复内容。

## 3. 开始前必须审计的内容

至少阅读以下文件：

- `README.md`
- `docs/使用手册.md`
- `docs/superpowers/specs/2026-08-26-result-storage-and-query-design.md`
- `factor_backtest_platform/result_store.py`
- `factor_backtest_platform/result_loader.py`
- `factor_backtest_platform/result_loader_v2.py`
- `factor_backtest_platform/result_views.py`
- `factor_backtest_platform/config.py`
- `factor_backtest_platform/runner.py`
- 与结果存储、读取、视图和 Schema 兼容相关的测试文件

审计时必须回答：

1. Schema 1 和 Schema 2 的真实目录结构分别是什么。
2. `latest.json` 的真实字段、状态和错误处理方式是什么。
3. `load_backtest_result()`、`LoadedBacktestResult` 当前支持哪些查询和日期筛选接口。
4. `ic_view`、`group_return_view`、`quality_view` 等接口的真实签名和返回字段是什么。
5. 哪些结果是每日基础数据，哪些结果是可动态派生的数据。
6. 日期重新设定原点是否已经由 view API 实现。
7. 移动平均 warm-up、重叠收益折算、HAC t-stat 和最大回撤的公共实现位于哪里。
8. Schema 1 是否缺少部分元数据，以及当前兼容层如何推导。
9. failed section、空 section 和缺失模块在 manifest 中如何表示。
10. 当前真实结果目录的文件数量、典型单因子大小和冷读取耗时。
11. Schema 2 的 `available_tables()` 返回整个run的表集合时，如何判断某张表是否包含当前股票池。
12. 外部收益缺少 `horizon_days` 时，现有view API会跳过哪些累计曲线或绩效结果。

审计完成后先形成简短实施计划。若发现公共接口缺失或存在口径歧义，先说明影响，不得直接修改 Factor_Backtest_Platform 核心代码。

## 4. 强制边界

1. 前端只读已有回测结果，不执行因子回测。
2. 不访问 ClickHouse，不加载原始因子矩阵和市场行情重新计算回测。
3. 不修改 IC、收益、HAC、分组、换手率、风险暴露和中性化计算口径。
4. 不在浏览器 JavaScript 中实现任何金融统计计算。
5. 优先调用 `load_backtest_result()`、`LoadedBacktestResult` 和现有 view API，不复制核心计算函数。
6. 不根据固定文件名猜测普通结果，优先读取 `latest.json`、manifest 和 `LoadedBacktestResult` 元数据。公司诊断仅适用本任务书规定的官方白名单例外。
7. 不向结果目录写入任何内容。
8. 不批量生成 PNG、HTML 或 PDF 静态报告。
9. 不引入 React 独立工程、Redis、数据库缓存、Celery、消息队列或微服务。
10. 暂不实现日频增量回测、高并发、权限系统、在线编辑和多用户协同。
11. 不允许用户在页面输入任意服务器文件路径。
12. 不修改 Factor_Backtest_Platform 版本号。
13. 若必须修复 Factor_Backtest_Platform 公共接口，先提交问题说明和最小修改方案，获得确认后在核心项目单独处理。
14. 不删除、覆盖或迁移任何现有回测结果。
15. 不同步服务器、不提交 Git、不创建 tag、不发布版本，除非后续得到明确指令。
16. 所有新增文档和代码注释使用中文。

## 5. 技术方案

采用以下技术：

- Plotly Dash
- Plotly
- pandas
- pyarrow
- Factor_Backtest_Platform `load_backtest_result()`、`LoadedBacktestResult` 和 view API
- 轻量 CSS
- pytest
- Playwright用于浏览器和视觉验收
- Dash自带测试工具用于回调级测试

不采用：

- React/Vue 独立前端工程
- Redis
- 服务端数据库缓存
- 复杂前后端分离微服务
- 浏览器端金融计算

### 5.1 已确认的核心公共接口契约

当前 `Factor_Backtest_Platform 1.0.0` 不存在名为 `ResultRepository` 的公共类。看板不得假设或自行要求该接口存在，也不得仅为统一命名而修改核心包。

当前真实公共入口为：

- `load_backtest_result()`
- `LoadedBacktestResult`
- `LoadedBacktestResult.available_pools()`
- `LoadedBacktestResult.available_tables()`
- `LoadedBacktestResult.read_table()`
- `LoadedBacktestResult.ic_view()`
- `LoadedBacktestResult.group_return_view()`
- `LoadedBacktestResult.quality_view()`

看板侧应实现边界清晰的 `repository_adapter`，但该适配层只是前端项目内部组件，不得复制金融计算。职责划分如下：

```text
只读结果目录索引
负责因子、latest和历史run枚举

repository_adapter
负责调用load_backtest_result()、统一Schema差异、日期范围和诊断白名单

LoadedBacktestResult和view API
负责读取结果以及IC、分组收益和数据质量等金融视图计算
```

Schema 2 的 `available_tables()` 返回整个run的表集合，不保证每张表都包含当前股票池。展示模块前必须同时检查：

```text
manifest.tables[table_name].pools
```

Schema 1 没有等价的完整表级元数据时，由adapter通过只读探测和异常处理判断模块可用性。

看板作为独立、可安装的 Python 包，建议包名：

```text
factor-dashboard
```

Python 导入和启动模块名：

```text
factor_dashboard
```

初始独立包版本可设为 `0.1.0`。该版本与 Factor_Backtest_Platform 的版本独立。

## 6. 推荐项目结构

```text
Factor_Backtest_Dashboard/
├── pyproject.toml
├── README.md
├── factor_dashboard/
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py
│   ├── cli.py
│   ├── config.py
│   ├── errors.py
│   ├── result_catalog.py
│   ├── repository_adapter.py
│   ├── cache.py
│   ├── formatting.py
│   ├── layouts/
│   │   ├── shell.py
│   │   ├── sidebar.py
│   │   └── tabs.py
│   ├── callbacks/
│   │   ├── catalog.py
│   │   ├── filters.py
│   │   ├── overview.py
│   │   ├── ic_analysis.py
│   │   ├── group_returns.py
│   │   ├── performance.py
│   │   ├── quality.py
│   │   ├── exposure.py
│   │   ├── turnover.py
│   │   ├── company_diagnostics.py
│   │   └── metadata.py
│   ├── figures/
│   │   ├── common.py
│   │   ├── ic.py
│   │   ├── returns.py
│   │   ├── quality.py
│   │   ├── exposure.py
│   │   └── turnover.py
│   ├── tables/
│   │   ├── common.py
│   │   └── downloads.py
│   └── assets/
│       └── dashboard.css
├── tests/
│   ├── fixtures/
│   ├── test_catalog.py
│   ├── test_repository_adapter.py
│   ├── test_cache.py
│   ├── test_date_rebase.py
│   ├── test_callbacks.py
│   ├── test_security.py
│   ├── test_schema_compatibility.py
│   └── test_app_smoke.py
└── docs/
    ├── 设计说明.md
    ├── 安装与启动.md
    ├── 配置参数.md
    ├── 结果结构与兼容性.md
    ├── 缓存策略.md
    ├── 模块扩展指南.md
    ├── 服务器部署.md
    └── 常见问题.md
```

可以根据实际代码规模适当合并模块，但不得把全部页面、回调和图表堆入单个文件。

## 7. 安装与依赖

前端项目的 `pyproject.toml` 独立声明 Dash、Plotly、测试和浏览器依赖，不修改 Factor_Backtest_Platform 的依赖列表。

本地开发时先安装核心包，再安装看板：

```powershell
pip install -e D:\hytp\Factor_Backtest_Platform
pip install -e D:\hytp\Factor_Backtest_Dashboard
```

服务器统一使用 `/app/workspace/zhangyuan/.venv`，并在同一环境安装核心包和看板：

```bash
/app/workspace/zhangyuan/.venv/bin/python -m pip install -e /app/workspace/zhangyuan/Factor_Backtest_Platform
/app/workspace/zhangyuan/.venv/bin/python -m pip install -e /app/workspace/zhangyuan/Factor_Backtest_Dashboard
```

不得在源码中硬编码服务器账号、密码、令牌或数据库凭据。

## 8. 启动方式与配置

必须提供以下启动方式：

```bash
python -m factor_dashboard \
  --result-root /data/zhangyuan/Factor_Backtest_Platform_Result \
  --host 127.0.0.1 \
  --port 8050
```

至少支持以下命令行参数：

- `--result-root`
- `--host`
- `--port`
- `--cache-max-entries`
- `--cache-ttl-seconds`
- `--log-level`
- `--debug`

同时支持对应环境变量，建议命名：

- `FACTOR_DASHBOARD_RESULT_ROOT`
- `FACTOR_DASHBOARD_HOST`
- `FACTOR_DASHBOARD_PORT`
- `FACTOR_DASHBOARD_CACHE_MAX_ENTRIES`
- `FACTOR_DASHBOARD_CACHE_TTL_SECONDS`
- `FACTOR_DASHBOARD_LOG_LEVEL`

配置优先级应为：

```text
命令行参数 > 环境变量 > 安全默认值
```

生产启动默认 `debug=False`。不得默认监听公网地址。组内初版建议监听 `127.0.0.1` 并通过 SSH 隧道访问。

## 9. 结果发现与运行版本

Schema 2 标准结构：

```text
<result_root>/<factor_name>/latest.json
<result_root>/<factor_name>/runs/<run_id>/
```

当前 `latest.json` 的真实字段为：

- `schema_version`
- `factor_name`
- `run_id`
- `relative_path`
- `completed_at`

`latest.json` 不包含运行状态。run级状态读取 `manifest.status`，模块状态读取 `manifest.sections` 和 `run_log.json`。

要求：

1. 自动发现所有合法因子结果。
2. 默认解析 `latest.json`，再固定打开其指向的具体 `run_id`。解析完成后，本次整页查询不得继续使用动态latest路径。
3. 历史 run 默认隐藏在高级选项中。
4. 显示 factor、run_id、Factor_Backtest_Platform 版本、Schema 版本、运行时间、实际日期范围和运行状态。
5. 忽略 `.staging`、临时目录和未完成运行。
6. `latest.json` 缺失、损坏或目标不存在时，只影响对应因子，不导致应用整体崩溃。
7. 不修复、不覆盖、不重新生成 `latest.json`。
8. 不使用软链接作为结果发现依据。
9. 提供“刷新结果目录”按钮。
10. 刷新后保持当前选择；若当前选择已不存在，回退到有效默认值并提示。
11. 扫描只读取必要层级和元数据，不递归遍历每个结果文件。
12. Schema 1 历史结果通过现有兼容接口加载，不在前端自行猜测旧文件名。
13. `latest.json` 发生变化时，新页面查询可以切换到新run；已经开始的整页查询必须继续绑定原run或整体作废重试，不能混用两个run。
14. `latest.json` 只用于目录发现和latest变更检测，结果缓存版本以 `run_id + manifest指纹` 为准。
15. Schema 1 缺失包版本、运行时间或状态时，界面允许显示“未知”或“由目录推断”，并注明来源。

## 10. 路径安全

路径安全是强制验收项：

- 因子和 run 只能来自目录索引生成的合法选项。
- 页面不提供自由输入服务器路径的控件。
- 所有候选路径必须使用 `Path.resolve()` 规范化。
- 规范化结果必须仍位于配置的 `result_root` 下。
- 拒绝绝对路径注入、`..`、符号链接越界和编码后的路径穿越。
- 下载文件名必须清理路径分隔符和控制字符。
- 不读取 manifest 指向的结果根目录外文件。
- 任何拒绝都应记录安全日志并向页面返回简洁错误，不暴露服务器敏感路径。

### 10.1 公司诊断白名单例外

当前 Factor_Backtest_Platform 没有公司诊断枚举或读取公共接口，相关文件也未列入 Schema 2 manifest。为避免仅为前端修改核心公共接口，明确允许看板在完成路径规范化和越界校验后，只读以下目录：

```text
<run_dir>/diagnostics/
```

仅允许读取四个官方白名单JSON：

- `spanning.json`
- `crowding.json`
- `mechanism_consistency.json`
- `diversity.json`

要求：

- 文件路径必须位于当前固定run的 `diagnostics` 目录内。
- 不递归扫描子目录。
- 不跟随指向run目录外的符号链接。
- 只接受普通JSON文件。
- 单个文件缺失时显示对应空状态。
- JSON损坏时只影响对应诊断模块。
- 除以上四个文件外，不允许根据其他固定文件名读取结果。
- 该白名单应集中定义在 `repository_adapter`，不得散落在各页面回调中。

## 11. 全局筛选器

左侧固定筛选区域至少包括：

- 因子：Dropdown，单选，可搜索。
- 运行版本：默认 latest，可选历史 run。
- 股票池：根据当前结果动态发现。
- IC 方法：动态发现，如 Spearman、Pearson。
- 收益周期或收益标签：动态发现，不写死 1D、5D、10D、20D。
- 日期快捷选项：最近1年、3年、5年、全部、自定义。
- 自定义开始日期和结束日期。
- 刷新结果目录按钮。
- 当前结果元数据入口。

筛选器联动规则：

1. 因子变化后重新发现 run。
2. run 变化后重新发现股票池、IC 方法、收益标签和有效日期。
3. 股票池变化后重新计算并限制有效日期范围。不得直接把 `manifest.date_ranges` 当作股票池级日期范围。
4. IC 方法和收益标签缺失时选择第一个有效值。
5. 日期不得超出当前 factor、run、pool 和 view 的有效范围。
6. 超出范围时自动裁剪，并显示裁剪前后范围和原因。
7. 若开始日期晚于结束日期，不发起查询并显示输入错误。
8. 快捷年份按交易结果中的最后有效日期向前推算，不使用系统当前日期。
9. 自定义日期应映射到结果中最近的有效交易日，并明确提示。

### 11.1 股票池级日期范围契约

Schema 2 的 `manifest.date_ranges` 是每张跨股票池合并表的整体日期范围，不是每个股票池单独的日期范围。

看板adapter应按以下规则确定实际范围：

- Schema 2：按pool读取当前目标基础表，根据实际索引计算范围并缓存。
- Schema 1：读取目标表后由adapter统一补充日期切片和范围计算。
- 金融统计和累计曲线仍交给view API，不在adapter复制公式。

全局日期范围默认按以下核心数据交集确定：

```text
所选IC方法及收益标签的有效日期
∩
所选分组收益标签的有效日期
```

Overview和核心收益页面使用该交集。风格、行业、换手率和公司诊断等可选模块使用各自有效日期，在全局选择范围内独立展示数据或空状态，不得用可选模块的缺失反向缩短核心日期范围。

## 12. 日期重新计算原则

这是核心验收项。

用户选择开始日期后，累计结果必须以该日期重新作为原点计算，不能截取全样本累计曲线：

- 累计 RankIC 从所选起点重新累计。
- 分组累计收益从所选起点重新归一化。
- 多空累计收益从所选起点重新计算。
- 区间 IC 统计按选择范围重新计算。
- 年度统计仅使用选择范围内的有效数据。
- Sharpe、最大回撤等绩效指标按选择范围重新计算。
- 移动平均允许读取所选开始日期之前的 warm-up 数据，但图中只显示选择范围。

20日移动平均默认读取开始日期之前最多19个有效交易日作为 warm-up。warm-up 数据不得计入区间 IC 统计。

优先调用现有 view API并传入日期参数。不得在 Dash callback、Plotly figure 函数或浏览器 JavaScript 中复制金融计算。

必须通过自动测试证明：

```text
按所选日期重新计算的累计结果 != 截断全样本已有累计曲线
```

页面必须采用前者。

### 12.1 IC标题与收益标签联动

IC图表名称必须跟随所选方法：

- Spearman显示 `RankIC`。
- Pearson显示 `Pearson IC`。
- 其他未来扩展方法显示其真实方法名，不统一误称为RankIC。

全局所选收益标签控制：

- 累计IC中的目标收益序列。
- 累计分组收益。
- 多空累计收益。
- 区间绩效指标。
- 依赖收益标签的年度统计。

专门用于横向比较收益标签的分组平均收益柱状图可以同时显示全部可用标签，但必须在标题和图例中明确标注为“收益标签比较”。除此之外，返回依赖型图表默认只展示全局所选标签，避免筛选器语义不一致。

## 13. 金融口径保护

看板必须继承 Factor_Backtest_Platform 的既有口径：

- 因子日期语义不变。
- open-to-open 未来收益时间语义不变。
- IC 方法和有效样本过滤不变。
- NaN、+Inf、-Inf 不进入有效回测样本，但展示时不得静默转换为0。
- 重叠 horizon 收益的累计折算不变。
- 普通 t-stat 和 HAC t-stat 定义不变。
- ICIR 和 Sharpe 的年化与否以 view API 返回及文档为准。
- 换手率仍采用现有定义，不在前端改名为真实组合交易换手。
- G1 为因子最低组，G10 为因子最高组，不自动判断或翻转因子方向。

外部收益标签可能没有 `horizon_days`。现有view API在缺少该字段时会跳过无法定义的日等效累计分组收益、日等效多空曲线或相关绩效结果，并返回warning。看板必须原样呈现该限制，不得自行猜测持有天数、默认为1天或强行生成累计曲线。

容易误解的指标必须通过 tooltip 说明口径，不能只显示无解释的字段名。

## 14. 页面信息架构

第一屏直接展示分析看板，不制作营销式首页。

建议标签页：

1. Overview
2. IC Analysis
3. Group Returns
4. Long-Short & Performance
5. Data Quality
6. Style & Industry
7. Turnover
8. Company Diagnostics
9. Run Metadata

没有对应结果的模块显示简洁空状态，说明“当前运行未生成该模块”或具体失败原因。不得抛出全页异常，也不得显示没有坐标和数据的空白图。

Overview 中的核心图表顺序固定为：

1. Cumulative RankIC
2. 20-Day Moving Average RankIC
3. 10-Group Cumulative Return
4. 10-Group Forward Returns
5. Cumulative Long-Short Return
6. Factor Coverage Counts
7. Factor Coverage and Invalid Value Ratios

以上顺序中的 `RankIC` 是Spearman选择下的显示名称。选择Pearson时，相应标题必须动态改为 `Pearson IC`，不能继续固定显示RankIC。

页面布局应适合1440×900常用桌面视口，同时兼容1920×1080和1024×768。

## 15. 图表要求

至少支持：

- 累计 RankIC。
- 20日 RankIC 移动平均。
- IC mean、std、ICIR、positive ratio、普通 t-stat、HAC t-stat。
- 不同年份的 IC 统计及趋势。
- G1至G10累计收益线。
- 各时间窗口十组平均收益柱状图。
- 多空累计收益和绩效指标。
- 数据覆盖数量。
- NaN、0、Inf等比例，count和ratio必须分图。
- 分组换手率。
- 风格暴露。
- 风格中性化 IC。
- 风格与行业中性化 IC。
- 分组暴露诊断。
- 行业内分组收益。
- 公司诊断数据，如当前结果存在。

颜色要求：

- 1D：柔和蓝色，建议 `#4C78A8`。
- 5D：柔和橙色，建议 `#F28E2B`。
- 10D：柔和绿色，建议 `#59A14F`。
- 20D：柔和红色，建议 `#E15759`。
- G1使用红色，G2至中间组逐渐变浅。
- G10使用蓝色，G9至中间组逐渐变浅。
- 外部收益使用稳定、可区分且不与默认四个 horizon 混淆的扩展色板。

交互要求：

- hover。
- 缩放。
- 框选。
- 恢复视图。
- 下载当前图表。
- 图例可点击隐藏或显示序列。
- 长时间序列合理减少刻度密度。
- 图表标题、坐标、图例和工具栏不得重叠。
- 柱状图宽度应便于比较，不使用过细柱体。

## 16. 视觉与交互设计

- 中文界面优先使用 Microsoft YaHei、PingFang SC、Noto Sans CJK SC 等字体回退。
- 页面应安静、紧凑，适合反复使用和数据比较。
- 不使用大面积渐变、装饰性卡片、嵌套卡片、大尺寸营销标题或无意义装饰。
- 卡片圆角不超过8px。
- 页面区块使用清晰分隔和对齐，不把所有内容包在浮动卡片中。
- 筛选器在桌面视口固定或保持易访问。
- 较窄视口允许筛选器折叠，但不得遮挡图表。
- 按钮优先使用熟悉图标并提供 tooltip。
- loading、empty、warning和error状态必须视觉可区分。
- 所有文字和控件不得溢出、重叠或被截断。
- 颜色不能作为唯一区分手段，图例和标签必须明确。

## 17. 表格与解释

统计表必须支持：

- 排序。
- 固定表头。
- 合理的小数和百分比格式。
- CSV下载。
- 长表分页或虚拟滚动。
- 空值和异常值明确展示。

显示规则：

- NaN显示为 `NaN`。
- 正无穷显示为 `+Inf`。
- 负无穷显示为 `-Inf`。
- 不得将上述值转换为0。
- 默认小数最多4位，百分比采用一致格式。
- 下载内容应保留原始数值精度，不以页面格式化字符串替代原始值。

至少为以下字段提供 tooltip：

- ICIR。
- 普通 t-stat。
- HAC t-stat。
- 分组名单变化率或换手率。
- 外部收益标签。
- 重叠 horizon 的累计收益。
- 有效样本数。

页面不放置大段方法说明。完整解释放入中文文档，页面仅保留短 tooltip 和元数据入口。

## 18. 缓存策略

仅实现有界进程内 LRU 缓存，不使用磁盘缓存或 Redis。

至少分为：

1. 结果目录索引缓存。
2. `LoadedBacktestResult`或运行元数据缓存。
3. view查询结果缓存。

view缓存键至少包含：

```text
factor
run_id
pool
IC method
return label
start
end
view type
manifest指纹
Schema版本
```

其中factor和run_id共同标识固定结果，manifest指纹建议使用小型manifest内容哈希，或经过验证的修改时间、文件大小和关键字段组合。`latest.json`修改时间仅用于发现latest是否变化，不作为已固定run结果缓存的唯一版本标识。

要求：

- 缓存容量可配置。
- 缓存TTL可配置。
- 达到容量后按LRU淘汰。
- 缓存不得无限增长。
- `latest.json` 更新后必须使对应因子的latest别名和目录索引缓存失效；已经以旧run_id为键的不可变历史结果可以保留到TTL或LRU淘汰，但不得再作为latest返回。
- 手动刷新必须重新扫描目录并清理受影响缓存。
- 缓存中的DataFrame不得被下游原地修改。
- 多个回调并发访问缓存时需要基本线程安全。
- 不把完整原始矩阵缓存或发送到浏览器。
- `dcc.Store` 只保存轻量筛选状态和元数据，不保存大表。

## 19. 性能目标

以服务器约10至11 MiB的单因子 Schema 2 结果为基准：

- 因子目录扫描尽量控制在1秒内。
- 普通日期、股票池、IC方法或收益标签切换尽量控制在1秒内。
- 冷读取尽量控制在3秒内。
- 不一次加载所有因子的全部数据。
- 不将完整原始矩阵发送到浏览器。
- 不为每个页面标签预先加载全部表格。

性能测试必须区分：

- 冷目录扫描。
- 热目录扫描。
- 冷view查询。
- 缓存命中查询。
- 日期范围切换。
- 股票池切换。
- 大型行业暴露查询。

若未达到目标，需要输出真实测量值、瓶颈和后续建议，不得通过减少正确性检查或改变金融口径规避。

初版不要求通过子进程或线程强制终止慢查询。应记录每次目录扫描和view查询耗时，标记超过性能目标的慢查询，并捕获底层库已经抛出的超时、I/O和内存异常。若以后需要强制超时，应另行确定超时时间、取消机制和执行模型。

## 20. 错误、空状态和一致性

页面必须处理：

- 空结果根目录。
- 因子目录无合法run。
- `latest.json`缺失。
- `latest.json`损坏。
- `latest.json`指向不存在目录。
- run状态未完成。
- Schema版本不支持。
- manifest缺少字段。
- 某section失败。
- 某股票池无有效数据。
- 某IC方法或收益标签不存在。
- 日期范围无交集。
- Parquet或CSV读取错误。
- 底层库抛出的查询超时、I/O错误或内存错误。

错误应限制在相关模块或当前因子范围。应用外壳、筛选器和其他可用模块应继续工作。

同一次页面查询必须绑定同一个factor、run_id和manifest版本，避免刷新过程中混用两个run的数据。读取期间 `latest.json` 发生变化时，已经固定到旧run的查询可以一致地完成；完成后提示存在新latest并允许用户刷新。不得在一次页面响应中自动切换一部分模块到新run。

## 21. Schema兼容性

必须验证：

- Schema 2 latest结果。
- Schema 2历史run。
- Schema 1历史CSV结果。
- Spearman-only结果。
- Spearman和Pearson同时存在的结果。
- 多股票池。
- 外部收益标签。
- 缺失风险暴露模块。
- failed section。
- 损坏 `latest.json`。
- 空结果目录。
- 日期范围超出股票池有效范围。
- 部分日期或部分模块数据缺失。

Schema 1无法提供的功能应显示明确空状态，不得伪造数据。Schema 1缺少包版本、运行时间或状态时，允许显示“未知”或“由目录推断”，并在tooltip或元数据页注明来源。Schema 1的 `read_table(start_date, end_date)` 不保证应用日期参数，adapter必须对直接表读取统一补充日期切片；IC、分组收益和质量统计仍通过view API完成。兼容层应集中在repository adapter，不得在各页面回调中散布Schema判断。

## 22. 只读下载与导出

初版至少支持：

- Plotly客户端下载当前图表。
- 表格CSV下载。

下载按当前factor、run、pool、方法、收益标签和日期范围生成。

不得：

- 每次回测批量生成图片。
- 在结果目录生成导出文件。
- 自动生成完整HTML或PDF报告。

若未来增加完整报告导出，应作为独立按需功能，不纳入本次默认运行流程。

## 23. 访问方式与低并发假设

看板用于组内小范围人员，暂不考虑高并发。

推荐初版监听：

```text
127.0.0.1:8050
```

本地通过SSH隧道访问：

```powershell
ssh -L 8050:127.0.0.1:8050 hytp_server
```

浏览器访问：

```text
http://127.0.0.1:8050
```

若以后监听 `0.0.0.0`，必须确认服务器处于可信内网并配置防火墙。当前任务不实现登录、鉴权或公网部署，因此不得默认暴露公网端口。

## 24. 自动测试

单元测试不得依赖正式服务器数据，必须使用 `tmp_path` 和合成Schema 1/2结果。

至少增加：

1. 结果目录发现测试。
2. latest和历史run选择测试。
3. `.staging`和未完成run忽略测试。
4. Schema 1/2兼容测试。
5. 股票池动态发现测试。
6. IC方法动态发现测试。
7. 收益标签和外部收益动态发现测试。
8. 日期有效范围和自动裁剪测试。
9. 日期重新设定累计原点测试。
10. 移动平均warm-up测试。
11. 缓存命中和LRU淘汰测试。
12. `latest.json`更新后的缓存失效测试。
13. 空目录、损坏manifest和损坏latest测试。
14. failed section和缺失模块测试。
15. 页面回调测试。
16. CSV下载格式测试。
17. NaN、+Inf、-Inf显示及下载测试。
18. 路径穿越和符号链接越界防护测试。
19. 不向结果目录写入文件的测试。
20. 应用启动冒烟测试。
21. `latest.json`真实字段解析和固定run_id测试。
22. latest变化期间整页查询不混用run测试。
23. `run_id + manifest指纹`缓存版本测试。
24. Schema 2表存在但不包含当前pool的空状态测试。
25. Schema 1直接表日期切片测试。
26. 公司诊断四文件白名单读取测试。
27. 公司诊断非白名单、损坏JSON和符号链接越界拒绝测试。
28. Spearman与Pearson标题动态切换测试。
29. 全局收益标签与比较柱状图联动测试。
30. 外部收益缺少 `horizon_days` 时warning和空状态测试。
31. 核心日期交集和可选诊断独立缺失测试。
32. 慢查询耗时记录和底层超时异常展示测试。

必须执行：

```bash
pytest -q
python -m compileall -q factor_dashboard tests
```

## 25. 浏览器和视觉测试

启动页面后使用Playwright检查：

- 1440×900。
- 1920×1080。
- 1024×768。

至少验证：

- 首屏非空。
- 筛选器可操作。
- 切换因子、run和股票池后内容更新。
- 日期快捷选项和自定义日期有效。
- 图表有有效数据轨迹或正确空状态。
- 标题、图例、工具栏和坐标不重叠。
- 页面不存在明显横向溢出。
- loading状态可见。
- 错误状态不会破坏整个页面。
- 窄视口筛选器可用。
- Plotly图表不是空白画布。

对动态绘图除截图外，还应检查Plotly trace数量和数据点数量，避免仅凭截图误判。

## 26. 真实结果只读验证

在单元测试全部通过后，使用以下目录做只读冒烟测试：

```text
/data/zhangyuan/Factor_Backtest_Result_v2_4_0_validation
```

访问服务器、读取真实结果或安装依赖前必须说明目的并获得权限。

验证内容：

- 能发现合法因子。
- 能解析latest和历史run。
- 能动态发现股票池、IC方法和收益标签。
- Overview和各诊断标签页可显示。
- 日期重新设定原点正确。
- 缺失模块显示空状态。
- 未写入任何结果文件。
- 记录冷读取、热读取和页面切换耗时。

验证前后应比较结果目录文件数量和修改时间，证明看板没有写入。

## 27. 中文文档

新增：

- 前端看板设计说明。
- 安装和启动说明。
- 配置参数说明。
- 结果目录和Schema兼容说明。
- 缓存策略。
- 常见错误排查。
- `repository_adapter`、`LoadedBacktestResult`和view API调用说明。
- 新增诊断模块指南。
- 服务器部署说明，但暂不实际部署。

README至少说明：

- 项目用途。
- Factor_Backtest_Platform 1.0.0 是最低推荐版本。
- 安装方式。
- 最小启动命令。
- 只读和安全边界。
- 支持的Schema版本。
- 不包含回测计算功能。

## 28. 实施阶段

### 阶段一：审计和契约确认

- 阅读核心源码和测试。
- 审计Schema 1/2。
- 列出可复用view API。
- 确认使用 `LoadedBacktestResult` 和 `load_backtest_result()`，不得等待或虚构 `ResultRepository`。
- 确认 `latest.json`、manifest和公司诊断白名单的真实契约。
- 识别缺口和口径风险。
- 提交简短实施计划。

### 阶段二：基础设施

- 创建独立包和CLI。
- 实现配置加载。
- 实现结果目录索引。
- 实现路径安全。
- 实现repository adapter。
- 实现股票池级有效日期推导和Schema 1直接表日期切片。
- 实现公司诊断官方白名单只读适配。
- 实现有界LRU缓存。

### 阶段三：核心页面

- 实现应用外壳和筛选器。
- 实现Overview。
- 实现IC Analysis。
- 实现Group Returns。
- 实现Long-Short & Performance。
- 实现Data Quality。

### 阶段四：诊断页面

- 实现Style & Industry。
- 实现Turnover。
- 实现Company Diagnostics。
- 实现Run Metadata。

### 阶段五：测试和优化

- 完成单元和回调测试。
- 完成路径安全测试。
- 完成性能测试。
- 完成Playwright多视口检查。
- 修复视觉重叠和空状态问题。

### 阶段六：文档和只读验证

- 完成中文文档。
- 使用验证目录做只读冒烟测试。
- 汇总测试、性能和兼容性结果。

## 29. 停止并确认的条件

遇到以下情况不得自行扩大范围：

- 需要修改 Factor_Backtest_Platform 金融计算逻辑。
- 需要修改 Factor_Backtest_Platform 公共接口或版本号。
- Schema源码、设计文档和真实结果互相冲突。
- 需要写入或迁移正式结果目录。
- 需要访问ClickHouse或其他数据库。
- 需要引入Redis、数据库缓存、独立React工程或权限系统。
- 需要开放公网端口。
- 真实结果包含未预期的敏感数据。
- 无法通过公共view API保证日期重新计算口径。

应说明发现、影响、可选方案和推荐方案，等待明确决定。

## 30. 验收标准

功能验收：

- 一个看板可以发现和查看多个因子结果。
- latest默认有效，历史run可选。
- 股票池、IC方法和收益标签全部动态发现。
- 只展示当前股票池实际包含的结果表和模块。
- 日期快捷选项和自定义日期正常。
- 核心日期范围按所选IC和收益标签交集裁剪，可选诊断独立处理缺失。
- 累计曲线按选择日期重新设定原点。
- Spearman和Pearson图表标题正确动态切换。
- 外部收益缺少 `horizon_days` 时显示明确warning，不生成无定义曲线。
- 公司诊断仅从四个官方白名单JSON读取。
- 所有任务书要求的可用模块均能展示。
- 缺失模块有明确空状态。

正确性验收：

- 不修改任何回测口径。
- 页面结果与相同参数下view API结果一致。
- 同一次页面响应固定使用一个run_id和manifest版本。
- warm-up不进入区间统计。
- NaN和正负Inf不被转换为0。
- Schema 1/2均有测试覆盖。

安全验收：

- 结果目录保持只读。
- 路径穿越测试通过。
- 页面不接受任意文件路径。
- 不硬编码凭据。
- 默认不暴露公网端口。

性能验收：

- 目录扫描、冷读取和热查询有实测记录。
- 缓存有容量上限和失效机制。
- latest别名缓存失效与固定run结果缓存版本语义正确。
- 浏览器不接收完整原始矩阵。
- 常用切换达到或尽量接近目标耗时。

质量验收：

- `pytest -q`通过。
- `compileall`通过。
- 三个目标视口截图检查通过。
- 图表非空且无明显重叠。
- 中文文档完整。

## 31. 最终交付说明

最终回复必须说明：

- 技术架构和项目目录结构。
- 新增和修改的文件。
- 页面和筛选功能。
- 安装与启动方式。
- 日期重新计算如何保证口径正确。
- warm-up如何处理。
- 缓存键、容量和失效机制。
- 目录扫描和查询性能结果。
- 自动测试数量和结果。
- Schema 1/2兼容情况。
- `LoadedBacktestResult`、目录索引和adapter的职责边界。
- 股票池级日期范围的计算方式。
- 公司诊断白名单读取情况。
- latest解析、固定run和manifest指纹策略。
- 外部收益缺少 `horizon_days` 时的页面行为。
- Playwright视口和截图检查结果。
- 真实结果只读验证结果。
- 是否修改过 Factor_Backtest_Platform 核心项目。
- 是否写入过正式结果目录。
- 尚未解决的问题和残余风险。

## 32. 交付纪律

- 先审计，后设计，再实现。
- 有明确缺口时先说明，不用猜测掩盖。
- 实现必须完成测试、视觉检查和文档，不只停留在代码。
- 不修改用户现有回测结果。
- 不同步服务器、不提交Git、不发布版本，除非后续明确要求。
- 所有操作必须服从本任务书的只读结果边界和金融口径保护要求。
