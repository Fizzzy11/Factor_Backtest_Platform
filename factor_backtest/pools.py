from __future__ import annotations

from pathlib import Path

import pandas as pd

from factor_backtest.config import POOL_REGISTRY


def load_pool_mask(path: str | Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    if "trade_date" not in raw.columns or "symbol" not in raw.columns:
        raise ValueError("Pool CSV requires trade_date and symbol columns")
    raw = raw[["trade_date", "symbol"]].copy()
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    raw["symbol"] = raw["symbol"].astype(str)
    raw["is_member"] = True
    mask = raw.pivot_table(index="trade_date", columns="symbol", values="is_member", aggfunc="max", fill_value=False)
    mask = mask.astype(bool)
    mask.index.name = "trade_date"
    mask.columns.name = "symbol"
    return mask.sort_index().sort_index(axis=1)


def resolve_selected_pools(
    selected_pools: list[str] | None,
    *,
    pool_source: str = "csv",
    pool_dir: str | Path | None = None,
) -> dict[str, pd.DataFrame | None]:
    names = selected_pools or ["all"]
    out: dict[str, pd.DataFrame | None] = {}
    for name in names:
        if name not in POOL_REGISTRY:
            raise KeyError(f"Unknown pool: {name}")
        definition = POOL_REGISTRY[name]
        if definition.is_virtual:
            out[name] = None
        elif pool_source == "csv":
            out[name] = load_pool_mask(_resolve_pool_path(definition.path, pool_dir))
        elif pool_source == "clickhouse":
            raise NotImplementedError("ClickHouse pool membership loading is not implemented yet")
        else:
            raise ValueError(f"Unknown pool_source: {pool_source}")
    return out


def _resolve_pool_path(path: str | Path, pool_dir: str | Path | None) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute() and pool_dir is not None:
        resolved = Path(pool_dir) / resolved
    return resolved
