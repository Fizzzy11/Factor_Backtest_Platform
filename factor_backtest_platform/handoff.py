from __future__ import annotations

from datetime import datetime
from pathlib import Path
import platform
import shutil
import subprocess
from zoneinfo import ZoneInfo

import pandas as pd

from factor_backtest_platform.config import BacktestConfig, HandoffConfig
from factor_backtest_platform.io import ensure_dir, read_json, write_json, write_table


REQUIRED_CORE_TABLES = [
    "ic_stats_spearman.csv",
    "group_return_summary.csv",
    "performance_metrics.csv",
    "group_turnover_edge_summary.csv",
    "data_quality.csv",
]

REQUIRED_DIAGNOSTICS = [
    "spanning.json",
    "crowding.json",
    "mechanism_consistency.json",
    "diversity.json",
]

SECTION_STATUS_MAP = {
    "ic": "cumulative_ic",
    "group_return": "group_return",
    "performance": "performance_metrics",
    "turnover": "group_turnover",
    "data_quality": "data_quality",
}

HANDOFF_TABLE_MAP = {
    "ic_stats_spearman.csv": ("cumulative_ic", "ic_stats_spearman"),
    "group_return_summary.csv": ("group_return", "group_return_summary"),
    "performance_metrics.csv": ("performance_metrics", "performance_metrics"),
    "group_turnover_edge_summary.csv": ("group_turnover", "group_turnover_edge_summary"),
    "data_quality.csv": ("data_quality", "data_quality"),
}

# 这两个名称属于既有 handoff 磁盘契约。为保证历史消费者可读，Platform 1.0.0 不改名。
HANDOFF_COMMIT_FILENAME = "factor_backtest_commit.txt"
HANDOFF_COMMIT_KEY = "factor_backtest_commit"


def export_factor_backtest_platform_handoff(
    *,
    run_dir: Path,
    config: BacktestConfig,
    factor_name: str,
    factor_index,
    section_status: dict | None = None,
) -> Path:
    """Export a minimal company handoff package from a real backtest run."""
    handoff = config.handoff
    if not handoff.enabled:
        raise ValueError("handoff.enabled must be True to export a handoff package")
    if handoff.target != "factor_backtest_platform":
        raise ValueError(f"Unsupported handoff target: {handoff.target}")

    source_meta = read_json(run_dir / "run_meta.json")

    output_dir = _resolve_handoff_output_dir(config, handoff)
    sample_dir = output_dir / "sample_latest"
    if sample_dir.exists():
        shutil.rmtree(sample_dir)
    tables_dir = ensure_dir(sample_dir / "pools" / handoff.pool / "tables")
    diagnostics_dir = ensure_dir(sample_dir / "diagnostics")

    if section_status is None:
        source_tables = run_dir / "pools" / handoff.pool / "tables"
        _validate_core_tables(source_tables, expected_horizons=source_meta.get("horizons", []))
        for table_name in REQUIRED_CORE_TABLES:
            shutil.copy2(source_tables / table_name, tables_dir / table_name)
    else:
        _write_handoff_tables(
            section_status,
            pool=handoff.pool,
            tables_dir=tables_dir,
            expected_horizons=source_meta.get("horizons", []),
        )

    source_log = read_json(run_dir / "run_log.json")
    commit = _resolve_git_commit(config.paths.project_dir)
    run_time = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S")
    data_asof = handoff.data_asof or _infer_data_asof(factor_index)

    write_json(
        _build_handoff_meta(
            source_meta=source_meta,
            handoff=handoff,
            factor_name=factor_name,
            run_time=run_time,
            data_asof=data_asof,
            commit=commit,
        ),
        sample_dir / "run_meta.json",
    )
    write_json(_build_handoff_log(source_log=source_log, handoff=handoff), sample_dir / "run_log.json")

    for diagnostic_name in REQUIRED_DIAGNOSTICS:
        write_json(_pending_diagnostic_payload(diagnostic_name), diagnostics_dir / diagnostic_name)

    ensure_dir(output_dir)
    (output_dir / HANDOFF_COMMIT_FILENAME).write_text(commit + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(_handoff_readme(), encoding="utf-8")
    (output_dir / "platform_schema_notes.md").write_text(_schema_notes(handoff), encoding="utf-8")
    return output_dir


def _write_handoff_tables(
    section_status: dict,
    *,
    pool: str,
    tables_dir: Path,
    expected_horizons: list[int],
) -> None:
    pool_status = section_status.get(pool)
    if pool_status is None:
        raise ValueError(f"handoff export missing pool {pool!r}")
    missing = []
    for filename, (section_name, table_name) in HANDOFF_TABLE_MAP.items():
        result = pool_status.get(section_name)
        if result is None or result.status != "success" or table_name not in result.tables:
            missing.append(f"{section_name}.{table_name}")
            continue
        write_table(result.tables[table_name], tables_dir / filename)
    if missing:
        raise ValueError(f"handoff export missing required in-memory tables: {missing}")
    _validate_core_tables(tables_dir, expected_horizons=expected_horizons)


def _resolve_handoff_output_dir(config: BacktestConfig, handoff: HandoffConfig) -> Path:
    if handoff.output_dir.is_absolute():
        return handoff.output_dir
    return config.paths.project_dir / handoff.output_dir


def _validate_core_tables(source_tables: Path, expected_horizons: list[int]) -> None:
    missing = [name for name in REQUIRED_CORE_TABLES if not (source_tables / name).exists()]
    if missing:
        raise ValueError(f"handoff export missing required tables in {source_tables}: {missing}")
    empty = []
    for table_name in REQUIRED_CORE_TABLES:
        table = pd.read_csv(source_tables / table_name)
        if table.empty or len(table.columns) == 0:
            empty.append(table_name)
    if empty:
        raise ValueError(f"handoff export requires non-empty core CSV tables: {empty}")
    _validate_ic_stats_horizons(source_tables / "ic_stats_spearman.csv", expected_horizons)


def _validate_ic_stats_horizons(path: Path, expected_horizons: list[int]) -> None:
    if not expected_horizons:
        return
    table = pd.read_csv(path)
    if "horizon" not in table.columns:
        raise ValueError(f"handoff export requires {path.name} to include a horizon column")
    required = {f"{horizon}d" for horizon in expected_horizons}
    present = {str(value) for value in table["horizon"].dropna()}
    missing = sorted(required - present, key=lambda label: int(label[:-1]))
    if missing:
        raise ValueError(f"handoff export requires {path.name} to include horizons: {missing}")


def _build_handoff_meta(
    *,
    source_meta: dict,
    handoff: HandoffConfig,
    factor_name: str,
    run_time: str,
    data_asof: str,
    commit: str,
) -> dict:
    data_access = {
        "provider": handoff.data_access_provider,
        "mapping_version": handoff.data_access_mapping_version,
    }
    if handoff.data_access_namespace:
        data_access["namespace"] = handoff.data_access_namespace
    if handoff.data_access_logical_fields:
        data_access["logical_fields"] = list(handoff.data_access_logical_fields)
    return {
        "run_time": run_time,
        "factor_name": factor_name,
        "data_asof": data_asof,
        HANDOFF_COMMIT_KEY: commit,
        "env": {
            "python": platform.python_version(),
            "host": platform.node(),
            "platform": platform.platform(),
        },
        "pools": [handoff.pool],
        "horizons": source_meta.get("horizons", []),
        "entry": handoff.entry,
        "factor_direction": handoff.factor_direction,
        "data_access": data_access,
        "source_run_meta": source_meta,
    }


def _build_handoff_log(*, source_log: dict, handoff: HandoffConfig) -> dict:
    source_sections = source_log.get("sections", {}).get(handoff.pool, {})
    sections = {}
    errors = []
    warnings = list(source_log.get("warnings", []))
    for handoff_name, source_name in SECTION_STATUS_MAP.items():
        source_status = source_sections.get(source_name)
        if source_status is None:
            status = {"status": "missing"}
            errors.append(f"required section {source_name!r} is missing for pool {handoff.pool!r}")
        else:
            raw_status = source_status.get("status", "unknown")
            status = {"status": "ok" if raw_status == "success" else raw_status}
            if source_status.get("warnings"):
                status["warnings"] = source_status["warnings"]
            if source_status.get("error"):
                status["error"] = source_status["error"]
                errors.append(f"{source_name}: {source_status['error']}")
        sections[handoff_name] = status
    return {
        "status": "ok" if not errors else "failed",
        "warnings": warnings,
        "errors": errors,
        "sections": {handoff.pool: sections},
        "source_run_log": source_log,
    }


def _pending_diagnostic_payload(filename: str) -> dict:
    name = Path(filename).stem
    return {
        "status": "pending(company)",
        "reason": f"当前 Factor_Backtest_Platform 第一轮 handoff 尚未产出 {name} 诊断的 computed 数值。",
        "owner": "回测组/风控组/IT组",
        "required_input": ["上游诊断定义", "必要的风险/持仓/因子库数据", "公司侧验收口径"],
        "next_step": "第一轮验收通过后，根据公司侧 parser 和诊断口径补齐真实计算值。",
    }


def _infer_data_asof(factor_index) -> str:
    index = pd.to_datetime(pd.Index(factor_index))
    if len(index) == 0:
        return ""
    return index.max().strftime("%Y-%m-%d")


def _resolve_git_commit(project_dir: Path) -> str:
    project_dir = Path(project_dir)
    try:
        result = subprocess.run(
            ["git", "-C", str(project_dir), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return _resolve_git_commit_from_dot_git(project_dir)


def _resolve_git_commit_from_dot_git(project_dir: Path) -> str:
    git_dir = project_dir / ".git"
    head_path = git_dir / "HEAD"
    if not head_path.exists():
        return "unknown"
    head = head_path.read_text(encoding="utf-8").strip()
    if head.startswith("ref:"):
        ref = head.split(" ", 1)[1]
        ref_path = git_dir / ref
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()
        packed_refs = git_dir / "packed-refs"
        if packed_refs.exists():
            for line in packed_refs.read_text(encoding="utf-8").splitlines():
                if line and not line.startswith("#") and line.endswith(f" {ref}"):
                    return line.split(" ", 1)[0]
        return "unknown"
    return head


def _handoff_readme() -> str:
    return """# Factor_Backtest_Platform Handoff

本目录是公司 Factor_Backtest_Platform 给 Alpha-Lab/平台侧的第一轮最小闭环交付包。

`sample_latest/` 来自一次真实 Factor_Backtest_Platform 运行，目录内只保留验收所需的核心 JSON、CSV 和 diagnostics 占位文件。四类 diagnostics 第一轮允许为 `pending(company)`，不会伪造 computed 指标。

核心文件：

- `sample_latest/run_meta.json`
- `sample_latest/run_log.json`
- `sample_latest/pools/all/tables/ic_stats_spearman.csv`
- `sample_latest/pools/all/tables/group_return_summary.csv`
- `sample_latest/pools/all/tables/performance_metrics.csv`
- `sample_latest/pools/all/tables/group_turnover_edge_summary.csv`
- `sample_latest/pools/all/tables/data_quality.csv`
- `sample_latest/diagnostics/*.json`

生成方式：在 `BacktestConfig` 中设置 `handoff=HandoffConfig(enabled=True)`，完成真实回测后框架会额外导出本目录。
"""


def _schema_notes(handoff: HandoffConfig) -> str:
    return f"""# Platform Schema Notes

## 1. 输入因子文件格式

Factor_Backtest_Platform 的标准日频因子输入是宽表：index 为交易日期，columns 为股票代码，每个单元格是一只股票在一个交易日的因子值。日期会按 pandas datetime 处理，股票代码需要与行情、股票池、风险暴露数据的代码格式一致。CSV、parquet、HDF 或已经读取好的 DataFrame 均可接入，最终进入回测核心时都会对齐成该宽表结构。

## 2. entry=next_open 口径

当前交付声明 `entry={handoff.entry}`。项目约定日频因子值在信号日收盘后构建，信号日当日所有可见数据已经可见；回测收益使用后续交易日开盘价计算，因此避免使用未来开盘价作为当日信号输入。

## 3. pool 口径

本轮 handoff 只交付 `{handoff.pool}`。`all` 是虚拟全市场池，含义是在行情和因子对齐后，不额外使用成分股 CSV 过滤。其他股票池如 hs300、zz1000、zz2000 可以在第二轮补充。

## 4. horizon 口径

`1/5/10/20` 表示从入场开盘价开始向后持有对应交易日数的未来收益，属于重叠持有期收益序列。外部收益也可以接入，但本 handoff 的核心验收表以 run_meta 中记录的 horizons 为准。

## 5. G1/G10 方向

分组默认按因子值从低到高排序。G1 是低因子值组，G10 是高因子值组。当前 handoff 声明 `factor_direction={handoff.factor_direction}`；如果未来某个因子方向相反，需要在该字段中明确。

## 6. 5 张核心表

- `ic_stats_spearman.csv`：Spearman Rank IC 统计表，按收益 horizon 输出均值、标准差、未年化 ICIR、普通 t-stat、Newey-West HAC t-stat、正值比例和有效天数；多日重叠窗口应优先使用 HAC t-stat。
- `group_return_summary.csv`：分组收益汇总，至少包含 G1/G10，通常也包含 G10-G1；收益字段为持有期收益序列的统计结果。
- `performance_metrics.csv`：基于原始多空远期收益的诊断指标。`mean_over_std_raw` 和兼容字段 `sharpe` 均未年化；`h>1` 的重叠收益不提供可实现组合最大回撤。
- `group_turnover_edge_summary.csv`：兼容文件名，实际是相邻因子日的分组成员变化率汇总，重点覆盖 G1、G10 和 edge average；它不是考虑持有期和权重后的真实换手率。
- `data_quality.csv`：因子覆盖率、过滤后有效股票数等数据质量字段，用于确认样本不是空跑。

## 7. 停牌、涨跌停、ST、新股、缺失值

当 `tradability_filter=True` 时，框架会在入场日检查停牌、ST、涨跌停可交易性和上市天数，过滤不可交易股票。缺失因子值、缺失行情和无效收益会在每日截面计算中剔除。`min_listed_days`、`min_ic_stocks` 和 `min_group_stocks` 控制新股过滤和每日最小样本数。

## 8. 已知限制和后续补齐项

第一轮 handoff 目标是证明真实、非空、可解析、可追溯。四类 diagnostics 目前可以是 `pending(company)`，不伪造指标。后续需要补齐正式 IT 统一取数映射版本、多股票池样本、spanning/crowding/mechanism/diversity 的真实计算值，以及更完整的风格/行业中性附加表。
"""
