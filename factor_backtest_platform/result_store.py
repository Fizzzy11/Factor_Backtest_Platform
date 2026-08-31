from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from uuid import uuid4

import pandas as pd

from factor_backtest_platform.sections import SectionResult


RESULT_SCHEMA_VERSION = "2.0"
LATEST_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class PreparedRunPaths:
    factor_root: Path
    run_id: str
    working_dir: Path
    final_dir: Path
    latest_index_path: Path | None


def prepare_run_paths(
    *,
    output_root: str | Path,
    factor_name: str,
    run_id: str,
    output_layout: str,
) -> PreparedRunPaths:
    """创建隔离的 staging 路径和最终发布路径。"""
    factor_root = Path(output_root) / factor_name
    if output_layout == "latest_runs":
        final_dir = factor_root / "runs" / run_id
        working_dir = factor_root / "runs" / ".staging" / run_id
        latest_index_path = factor_root / "latest.json"
    elif output_layout == "timestamp":
        final_dir = factor_root / run_id
        working_dir = factor_root / ".staging" / run_id
        latest_index_path = None
    else:
        raise ValueError("output_layout must be 'latest_runs' or 'timestamp'")
    if working_dir.exists() or final_dir.exists():
        raise FileExistsError(f"回测 run_id 已存在：{run_id}")
    working_dir.mkdir(parents=True, exist_ok=False)
    return PreparedRunPaths(
        factor_root=factor_root,
        run_id=run_id,
        working_dir=working_dir,
        final_dir=final_dir,
        latest_index_path=latest_index_path,
    )


def should_persist_table(table_name: str) -> bool:
    """判断 section 表是否属于前端和报告重建所需的每日基础数据。"""
    exact_names = {
        "data_quality",
        "daily_group_returns",
        "within_industry_daily_group_returns",
        "group_style_exposure_daily",
        "group_industry_exposure_daily",
        "daily_group_membership_change",
    }
    if table_name in exact_names:
        return True
    if table_name.startswith("daily_ic_"):
        return table_name != "daily_ic"
    if table_name.startswith("factor_style_exposure_corr_"):
        return not table_name.startswith("factor_style_exposure_corr_summary_")
    if table_name.startswith("style_neutralized_ic_"):
        return "_stats_" not in table_name
    if table_name.startswith("style_industry_neutralized_ic_"):
        return "_stats_" not in table_name
    return False


def write_result_data(
    run_dir: str | Path,
    status: dict[str, dict[str, SectionResult]],
    *,
    compression: str | None = "zstd",
    core_tables: dict[str, dict[str, pd.DataFrame]] | None = None,
) -> dict[str, dict[str, Any]]:
    """跨股票池合并并写入 Schema 2 每日基础 Parquet。"""
    run_path = Path(run_dir)
    data_dir = run_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    collected: dict[str, list[tuple[str, str, pd.DataFrame]]] = {}
    for pool, tables in (core_tables or {}).items():
        for table_name, table in tables.items():
            if should_persist_table(table_name):
                collected.setdefault(table_name, []).append((pool, "__core__", table))
    for pool, sections in status.items():
        for section_name, result in sections.items():
            if result.status != "success":
                continue
            for table_name, table in result.tables.items():
                if should_persist_table(table_name) and not any(
                    existing_pool == pool for existing_pool, _section, _table in collected.get(table_name, [])
                ):
                    collected.setdefault(table_name, []).append((pool, section_name, table))

    manifest: dict[str, dict[str, Any]] = {}
    for table_name, entries in sorted(collected.items()):
        frames = []
        index_columns: list[str] | None = None
        data_columns: list[str] | None = None
        source_sections = []
        for pool, section_name, table in entries:
            materialized, current_index_columns, current_data_columns = _materialize_table(pool, table)
            if index_columns is None:
                index_columns = current_index_columns
                data_columns = current_data_columns
            elif current_index_columns != index_columns:
                raise ValueError(
                    f"结果表 {table_name!r} 在不同股票池中的索引结构不一致："
                    f"{index_columns} != {current_index_columns}"
                )
            frames.append(materialized)
            if section_name not in source_sections:
                source_sections.append(section_name)
        combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
        path = data_dir / f"{table_name}.parquet"
        _write_parquet_atomic(combined, path, compression=compression)
        manifest[table_name] = {
            "path": path.relative_to(run_path).as_posix(),
            "rows": int(len(combined)),
            "columns": [str(column) for column in combined.columns],
            "index_columns": index_columns or [],
            "data_columns": data_columns or [],
            "pools": sorted(str(value) for value in combined.get("pool", pd.Series(dtype=str)).dropna().unique()),
            "date_range": _date_range_payload(combined),
            "source_sections": source_sections,
            "compression": compression,
        }
    return manifest


def read_result_table(
    run_dir: str | Path,
    table_meta: dict[str, Any],
    *,
    pool: str,
    start_date=None,
    end_date=None,
) -> pd.DataFrame:
    """按股票池和可选日期范围读取一张 Schema 2 基础表。"""
    run_path = Path(run_dir).resolve()
    path = (run_path / str(table_meta["path"])).resolve()
    if not _is_relative_to(path, run_path):
        raise ValueError(f"结果表路径越出 run 目录：{path}")
    filters: list[tuple[str, str, Any]] = [("pool", "==", str(pool))]
    columns = set(table_meta.get("columns", []))
    if "trade_date" in columns:
        if start_date is not None:
            filters.append(("trade_date", ">=", pd.Timestamp(start_date)))
        if end_date is not None:
            filters.append(("trade_date", "<=", pd.Timestamp(end_date)))
    frame = pd.read_parquet(path, engine="pyarrow", filters=filters)
    if frame.empty and str(pool) not in set(table_meta.get("pools", [])):
        raise KeyError(f"结果表 {path.name} 不包含股票池 {pool!r}")
    if "pool" in frame.columns:
        frame = frame.drop(columns=["pool"])
    index_columns = list(table_meta.get("index_columns", []))
    if index_columns:
        missing = [column for column in index_columns if column not in frame.columns]
        if missing:
            raise ValueError(f"结果表 {path.name} 缺少索引字段：{missing}")
        frame = frame.set_index(index_columns)
    data_columns = [column for column in table_meta.get("data_columns", []) if column in frame.columns]
    if data_columns:
        frame = frame.loc[:, data_columns]
    return frame.sort_index()


def write_manifest(run_dir: str | Path, payload: dict[str, Any]) -> Path:
    body = dict(payload)
    body["schema_version"] = RESULT_SCHEMA_VERSION
    return write_json_atomic(body, Path(run_dir) / "manifest.json")


def publish_run(paths: PreparedRunPaths, latest_payload: dict[str, Any] | None) -> Path:
    """发布完整 run，并在需要时原子更新 latest.json。"""
    with _factor_publish_lock(paths.factor_root):
        if paths.final_dir.exists():
            raise FileExistsError(f"目标 run 已存在：{paths.final_dir}")
        paths.final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(paths.working_dir, paths.final_dir)
        if paths.latest_index_path is not None:
            if latest_payload is None:
                raise ValueError("latest_runs 布局发布时必须提供 latest_payload")
            write_json_atomic(latest_payload, paths.latest_index_path)
    return paths.final_dir


def resolve_latest_run(factor_root: str | Path) -> Path:
    """优先解析 Schema 2 latest.json，同时兼容旧 latest/ 目录。"""
    root = Path(factor_root).resolve()
    index_path = root / "latest.json"
    if index_path.exists():
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        schema_version = str(payload.get("schema_version", LATEST_SCHEMA_VERSION))
        if schema_version != LATEST_SCHEMA_VERSION:
            raise ValueError(f"不支持的 latest.json schema_version：{schema_version}")
        relative_path = payload.get("relative_path")
        if not relative_path:
            raise ValueError(f"latest.json 缺少 relative_path：{index_path}")
        resolved = (root / str(relative_path)).resolve()
        if not _is_relative_to(resolved, root):
            raise ValueError(f"latest.json 指向因子目录之外：{resolved}")
        if not resolved.is_dir():
            raise FileNotFoundError(f"latest.json 指向的 run 不存在：{resolved}")
        return resolved
    legacy = root / "latest"
    if legacy.is_dir():
        return legacy
    raise FileNotFoundError(f"没有找到 latest.json 或旧 latest/：{root}")


def write_json_atomic(payload: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return target


@contextmanager
def _factor_publish_lock(factor_root: Path, *, timeout_seconds: float = 30.0):
    """用原子建目录串行化同一因子的 run 发布和 latest 更新。"""
    lock_dir = factor_root / ".publish.lock"
    factor_root.mkdir(parents=True, exist_ok=True)
    deadline = monotonic() + timeout_seconds
    while True:
        try:
            lock_dir.mkdir()
            break
        except FileExistsError:
            if monotonic() >= deadline:
                raise TimeoutError(f"等待因子结果发布锁超时：{lock_dir}")
            sleep(0.05)
    try:
        yield
    finally:
        lock_dir.rmdir()


def _materialize_table(pool: str, table: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    if not isinstance(table, pd.DataFrame):
        raise TypeError(f"结果表必须是 DataFrame，实际为 {type(table)!r}")
    frame = table.copy()
    original_columns = [str(column) for column in frame.columns]
    frame.columns = original_columns
    index_names = _safe_index_names(frame)
    frame.index = frame.index.set_names(index_names)
    materialized = frame.reset_index()
    if "pool" in materialized.columns:
        raise ValueError("结果表已经包含保留字段 pool")
    materialized.insert(0, "pool", str(pool))
    return materialized, index_names, original_columns


def _safe_index_names(frame: pd.DataFrame) -> list[str]:
    used = {str(column) for column in frame.columns}
    names = []
    for position, raw_name in enumerate(frame.index.names):
        if raw_name is not None:
            base = str(raw_name)
        elif position == 0 and isinstance(frame.index, pd.DatetimeIndex):
            base = "trade_date"
        else:
            base = f"__index_level_{position}__"
        name = base
        suffix = 1
        while name in used or name == "pool":
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        names.append(name)
    return names


def _write_parquet_atomic(frame: pd.DataFrame, path: Path, *, compression: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, engine="pyarrow", compression=compression, index=False)
        pd.read_parquet(temporary, engine="pyarrow", columns=[])
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _date_range_payload(frame: pd.DataFrame) -> dict[str, str] | None:
    if "trade_date" not in frame.columns or frame.empty:
        return None
    dates = pd.to_datetime(frame["trade_date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return {"start": dates.min().isoformat(), "end": dates.max().isoformat()}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
