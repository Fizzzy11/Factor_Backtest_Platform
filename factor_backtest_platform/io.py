from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

TABLE_FLOAT_DECIMALS = 4


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_table(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    ensure_dir(path.parent)
    if path.suffix == ".csv":
        df.to_csv(path, float_format=format_table_float)
        return path
    elif path.suffix == ".parquet":
        try:
            df.to_parquet(path)
            return path
        except ImportError:
            fallback = path.with_suffix(path.suffix + ".pkl")
            df.to_pickle(fallback)
            return fallback
    df.to_pickle(path)
    return path


def write_json(payload: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix == ".csv":
        return pd.read_csv(path, index_col=0)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    if path.suffix == ".pkl" or path.name.endswith(".parquet.pkl"):
        return pd.read_pickle(path)
    raise ValueError(f"Unsupported table file: {path}")


def format_table_float(value) -> str:
    if pd.isna(value):
        return ""
    rounded = round(float(value), TABLE_FLOAT_DECIMALS)
    if rounded == 0:
        return "0"
    return f"{rounded:.{TABLE_FLOAT_DECIMALS}f}".rstrip("0").rstrip(".")
