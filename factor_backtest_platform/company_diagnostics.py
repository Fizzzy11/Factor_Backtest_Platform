from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from factor_backtest_platform.config import BacktestConfig, CompanyDiagnosticsConfig
from factor_backtest_platform.io import ensure_dir, write_json
from factor_backtest_platform.returns import return_label


DATE_COLUMNS = ("date", "trade_date")
SYMBOL_COLUMNS = ("symbol", "stock_id", "asset")
VALUE_COLUMNS = ("value", "factor_value", "factor")
CONTRACT_POOLS = ("all", "hs300_pool", "zz1000_pool", "zz2000_pool")
CONTRACT_HORIZONS = ("1", "5", "10", "20")
FIXED_CROWDING_THRESHOLD = 0.7


@dataclass
class FactorBook:
    factors: dict[str, pd.DataFrame]

    @property
    def factor_ids(self) -> list[str]:
        return list(self.factors)

    def subset(self, factor_ids: list[str]) -> "FactorBook":
        return FactorBook({factor_id: self.factors[factor_id] for factor_id in factor_ids if factor_id in self.factors})


def export_company_diagnostics(
    *,
    run_dir: Path,
    config: BacktestConfig,
    pool_contexts: dict[str, dict[str, Any]],
) -> list[str]:
    diagnostics = config.diagnostics
    if not diagnostics.enabled:
        return []

    warnings: list[str] = []
    diagnostics_dir = ensure_dir(run_dir / "diagnostics")
    pool_contexts = _contract_pool_contexts(pool_contexts)
    if not pool_contexts:
        try:
            diagnostics_dir.rmdir()
        except OSError:
            pass
        return warnings

    production_book = _load_optional_book(
        diagnostics.production_book_path,
        source=diagnostics.production_book_source,
        warnings=warnings,
        label="production_book",
    )
    peer_book = _load_optional_book(
        diagnostics.peer_book_path,
        source=diagnostics.peer_book_source,
        warnings=warnings,
        label="peer_book",
    )
    regime = _load_optional_regime(
        diagnostics.regime_path,
        source=diagnostics.regime_source,
        labels=diagnostics.regime_labels,
        warnings=warnings,
    )

    meta_base = _base_meta(pool_contexts, diagnostics)

    if production_book is not None and production_book.factors:
        payload = compute_spanning_diagnostic(
            pool_contexts=pool_contexts,
            production_book=production_book,
            diagnostics=diagnostics,
            meta_base=meta_base,
        )
        if payload is not None:
            write_json(payload, diagnostics_dir / "spanning.json")

    if peer_book is not None and peer_book.factors:
        crowding_payload = compute_crowding_diagnostic(
            pool_contexts=pool_contexts,
            peer_book=peer_book,
            diagnostics=diagnostics,
            meta_base=meta_base,
        )
        if crowding_payload is not None:
            write_json(crowding_payload, diagnostics_dir / "crowding.json")
        diversity_payload = compute_diversity_diagnostic(
            pool_contexts=pool_contexts,
            peer_book=peer_book,
            diagnostics=diagnostics,
            meta_base=meta_base,
        )
        if diversity_payload is not None:
            write_json(diversity_payload, diagnostics_dir / "diversity.json")

    mechanism_payload = compute_mechanism_consistency_diagnostic(
        pool_contexts=pool_contexts,
        regime=regime,
        diagnostics=diagnostics,
        meta_base=meta_base,
    )
    if mechanism_payload is not None:
        write_json(mechanism_payload, diagnostics_dir / "mechanism_consistency.json")

    if not any(diagnostics_dir.glob("*.json")):
        try:
            diagnostics_dir.rmdir()
        except OSError:
            pass
    return warnings


def compute_crowding_diagnostic(
    *,
    pool_contexts: dict[str, dict[str, Any]],
    peer_book: FactorBook,
    diagnostics: CompanyDiagnosticsConfig,
    meta_base: dict[str, Any],
) -> dict[str, Any] | None:
    pools: dict[str, Any] = {}
    for pool_name, context in pool_contexts.items():
        similarity = _rankcorr_similarity_summary(
            context["factor"],
            peer_book,
            min_stocks=diagnostics.min_similarity_stocks,
        )
        if similarity.empty:
            continue
        best = similarity.iloc[0]
        max_peer = str(best["factor_id"])
        topk_overlap = _topk_overlap(
            context["factor"],
            peer_book.factors[max_peer],
            k=diagnostics.topk_overlap_k,
            min_stocks=diagnostics.min_similarity_stocks,
        )
        cell = {
            "n_peers": int(len(peer_book.factors)),
            "max_abs_rankcorr": _json_float(best["mean_abs_rankcorr"]),
            "peak_abs_rankcorr": _json_float(best["peak_abs_rankcorr"]),
            "max_corr_peer": max_peer,
            "n_peers_above_0.7": int((similarity["mean_abs_rankcorr"] > FIXED_CROWDING_THRESHOLD).sum()),
            "topk_overlap": _json_float(topk_overlap),
            "topk_k": int(diagnostics.topk_overlap_k),
            "n_obs": int(best["n_obs"]),
            "operator_graph_similarity": None,
            "operator_graph_similarity_status": "pending(dsl)",
        }
        return_corr = _max_abs_returncorr(context, max_peer)
        if return_corr is not None:
            cell["max_abs_returncorr"] = return_corr
        pools[pool_name] = _drop_missing(cell, keep_null_keys={"operator_graph_similarity"})
    if not pools:
        return None
    return {
        "status": "computed",
        "meta": _with_meta(meta_base, peer_book_version=diagnostics.peer_book_version, pools=pools.keys()),
        "pool_id": diagnostics.peer_pool_id,
        "pools": pools,
    }


def compute_diversity_diagnostic(
    *,
    pool_contexts: dict[str, dict[str, Any]],
    peer_book: FactorBook,
    diagnostics: CompanyDiagnosticsConfig,
    meta_base: dict[str, Any],
) -> dict[str, Any] | None:
    context = pool_contexts.get("all")
    if context is None:
        return None
    similarity = _rankcorr_similarity_summary(
        context["factor"],
        peer_book,
        min_stocks=diagnostics.min_similarity_stocks,
    )
    if similarity.empty:
        return None
    best = similarity.iloc[0]
    max_similarity = _json_float(best["mean_abs_rankcorr"])
    if max_similarity is None:
        return None
    payload = {
        "status": "computed",
        "meta": _with_meta(meta_base, peer_book_version=diagnostics.peer_book_version, pools=["all"]),
        "version": int(diagnostics.version),
        "max_similarity_to_book": max_similarity,
        "novelty": _json_float(1.0 - max_similarity),
        "max_similarity_peer": str(best["factor_id"]),
        "similarity_type": "mean_abs_rankcorr",
        "n_obs": int(best["n_obs"]),
    }
    if diagnostics.idea_id:
        payload["idea_id"] = diagnostics.idea_id
    return payload


def compute_spanning_diagnostic(
    *,
    pool_contexts: dict[str, dict[str, Any]],
    production_book: FactorBook,
    diagnostics: CompanyDiagnosticsConfig,
    meta_base: dict[str, Any],
) -> dict[str, Any] | None:
    pools: dict[str, Any] = {}
    covered_horizons: list[str] = []
    global_top = None
    for pool_name, context in pool_contexts.items():
        residualization = _iterative_residualize(
            context["factor"],
            production_book,
            topk=diagnostics.spanning_topk,
            rounds=diagnostics.spanning_rounds,
            top_factor_count=diagnostics.top_spanning_factors,
            min_stocks=diagnostics.min_similarity_stocks,
        )
        if not residualization["selected_factor_ids"]:
            continue
        if pool_name == "all" or global_top is None:
            global_top = residualization["top_factors"]

        horizons: dict[str, Any] = {}
        for horizon, returns, key in _contract_horizon_items(context):
            ic_series = _daily_spearman_ic(
                residualization["final_residual"],
                returns,
                min_stocks=diagnostics.min_similarity_stocks,
            )
            ic_stats = _series_mean_t(ic_series)
            r2_series = _daily_incremental_r2(
                candidate=residualization["final_residual"],
                returns=returns,
                book=production_book,
                factor_ids=residualization["selected_factor_ids"],
                min_stocks=diagnostics.min_similarity_stocks,
            )
            r2_stats = _series_mean_t(r2_series)
            if ic_stats["n_obs"] == 0 and r2_stats["n_obs"] == 0:
                continue
            cell = {
                "incremental_ic": _json_float(ic_stats["mean"]),
                "incremental_ic_t": _json_float(ic_stats["t_stat"]),
                "incremental_r2": _json_float(r2_stats["mean"]),
                "n_obs": int(max(ic_stats["n_obs"], r2_stats["n_obs"])),
                "residualization_rounds": _rounds_for_horizon(
                    residualization["rounds"],
                    returns,
                    min_stocks=diagnostics.min_similarity_stocks,
                ),
            }
            horizons[key] = _drop_missing(cell)
        if horizons:
            covered_horizons = _merge_ordered_horizons(covered_horizons, horizons.keys())
            pools[pool_name] = {
                "top_spanning_factors": residualization["top_factors"],
                "horizons": horizons,
            }
    if not pools:
        return None
    baseline_id = diagnostics.baseline_suite_id or diagnostics.baseline_book_version or "production_book"
    return {
        "status": "computed",
        "meta": _with_meta(
            meta_base,
            baseline_book_version=diagnostics.baseline_book_version or baseline_id,
            pools=pools.keys(),
            horizons=covered_horizons,
        ),
        "baseline_suite_id": baseline_id,
        "neutralization_layers": diagnostics.neutralization_layers,
        "spanning_method": {
            "type": "iterative_topk_residualization",
            "topk": int(diagnostics.spanning_topk),
            "rounds": int(diagnostics.spanning_rounds),
        },
        "top_spanning_factors": global_top or [],
        "pools": pools,
    }


def compute_mechanism_consistency_diagnostic(
    *,
    pool_contexts: dict[str, dict[str, Any]],
    regime: pd.DataFrame | None,
    diagnostics: CompanyDiagnosticsConfig,
    meta_base: dict[str, Any],
) -> dict[str, Any] | None:
    pools: dict[str, Any] = {}
    covered_horizons: list[str] = []
    for pool_name, context in pool_contexts.items():
        horizon_cells: dict[str, Any] = {}
        for horizon, returns, key in _contract_horizon_items(context):
            cell = _mechanism_horizon_cell(
                factor=context["factor"],
                returns=returns,
                daily_group_returns=context["daily_group_returns"],
                horizon=horizon,
                regime=regime,
                diagnostics=diagnostics,
            )
            if cell is not None:
                horizon_cells[key] = cell
        if horizon_cells:
            covered_horizons = _merge_ordered_horizons(covered_horizons, horizon_cells.keys())
            pools[pool_name] = {"horizons": horizon_cells}
    if not pools:
        return None
    payload = {
        "status": "computed",
        "meta": _with_meta(meta_base, pools=pools.keys(), horizons=covered_horizons),
        "hypothesis_direction": diagnostics.hypothesis_direction,
        "pools": pools,
    }
    if regime is not None:
        payload["regimes"] = list(regime.columns)
    return payload


def load_factor_book(path: str | Path) -> FactorBook:
    raw = _read_frame(path)
    columns = set(raw.columns)
    date_col = _pick_column(columns, DATE_COLUMNS)
    symbol_col = _pick_column(columns, SYMBOL_COLUMNS)
    value_col = _pick_column(columns, VALUE_COLUMNS)
    if date_col is None or symbol_col is None or "factor_id" not in raw.columns or value_col is None:
        raise ValueError("Factor book requires date/trade_date, symbol/stock_id, factor_id, and value/factor_value columns")
    frame = raw[[date_col, symbol_col, "factor_id", value_col]].copy()
    frame = frame.rename(columns={date_col: "date", symbol_col: "symbol", value_col: "value"})
    frame["date"] = pd.to_datetime(frame["date"])
    frame["symbol"] = frame["symbol"].astype(str)
    frame["factor_id"] = frame["factor_id"].astype(str)
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    factors = {}
    for factor_id, sub in frame.groupby("factor_id", sort=True):
        wide = sub.pivot_table(index="date", columns="symbol", values="value", aggfunc="mean")
        wide.index.name = "trade_date"
        wide.columns.name = None
        factors[str(factor_id)] = wide.sort_index().sort_index(axis=1)
    return FactorBook(factors)


def load_regime_frame(path: str | Path, labels: list[str]) -> pd.DataFrame:
    raw = _read_frame(path)
    columns = set(raw.columns)
    date_col = _pick_column(columns, DATE_COLUMNS)
    if date_col is None:
        raise ValueError("Regime data requires date or trade_date column")
    missing = [label for label in labels if label not in raw.columns]
    if missing:
        raise ValueError(f"Regime data missing configured labels: {missing}")
    frame = raw[[date_col, *labels]].copy().rename(columns={date_col: "date"})
    frame["date"] = pd.to_datetime(frame["date"])
    for label in labels:
        frame[label] = _parse_bool_series(frame[label], label=label)
    return frame.set_index("date").sort_index()


def _load_optional_book(
    path: Path | None,
    *,
    source: str,
    warnings: list[str],
    label: str,
) -> FactorBook | None:
    if path is None:
        return None
    if source != "file":
        warnings.append(f"{label} source={source} is reserved but not implemented; skipped")
        return None
    try:
        return load_factor_book(path)
    except FileNotFoundError:
        warnings.append(f"{label} file not found: {path}")
        return None
    except (ValueError, ImportError) as exc:
        warnings.append(f"{label} could not be loaded: {exc}")
        return None


def _load_optional_regime(
    path: Path | None,
    *,
    source: str,
    labels: list[str],
    warnings: list[str],
) -> pd.DataFrame | None:
    if path is None:
        return None
    if source != "file":
        warnings.append(f"regime source={source} is reserved but not implemented; skipped")
        return None
    try:
        return load_regime_frame(path, labels)
    except FileNotFoundError:
        warnings.append(f"regime file not found: {path}")
        return None
    except (ValueError, ImportError) as exc:
        warnings.append(f"regime could not be loaded: {exc}")
        return None


def _read_frame(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    raise ValueError(f"Unsupported diagnostics input suffix: {path.suffix}")


def _rankcorr_similarity_summary(candidate: pd.DataFrame, book: FactorBook, *, min_stocks: int) -> pd.DataFrame:
    rows = []
    for factor_id, peer in book.factors.items():
        values = []
        for date in candidate.index.intersection(peer.index):
            c = pd.to_numeric(candidate.loc[date], errors="coerce")
            p = pd.to_numeric(peer.loc[date].reindex(candidate.columns), errors="coerce")
            good = c.notna() & p.notna() & np.isfinite(c) & np.isfinite(p)
            if int(good.sum()) < min_stocks:
                continue
            corr = c.loc[good].rank().corr(p.loc[good].rank())
            if pd.notna(corr) and np.isfinite(corr):
                values.append(abs(float(corr)))
        if values:
            rows.append(
                {
                    "factor_id": factor_id,
                    "mean_abs_rankcorr": float(np.mean(values)),
                    "peak_abs_rankcorr": float(np.max(values)),
                    "n_obs": int(len(values)),
                }
            )
    if not rows:
        return pd.DataFrame(columns=["factor_id", "mean_abs_rankcorr", "peak_abs_rankcorr", "n_obs"])
    return pd.DataFrame(rows).sort_values(["mean_abs_rankcorr", "factor_id"], ascending=[False, True]).reset_index(
        drop=True
    )


def _topk_overlap(candidate: pd.DataFrame, peer: pd.DataFrame, *, k: int, min_stocks: int) -> float | None:
    overlaps = []
    for date in candidate.index.intersection(peer.index):
        c = pd.to_numeric(candidate.loc[date], errors="coerce")
        p = pd.to_numeric(peer.loc[date].reindex(candidate.columns), errors="coerce")
        good = c.notna() & p.notna() & np.isfinite(c) & np.isfinite(p)
        n_good = int(good.sum())
        if n_good < min_stocks:
            continue
        k_eff = min(int(k), max(1, n_good // 2))
        c_good = c.loc[good]
        p_good = p.loc[good]
        c_top = set(c_good.nlargest(k_eff).index)
        c_bottom = set(c_good.nsmallest(k_eff).index)
        p_top = set(p_good.nlargest(k_eff).index)
        p_bottom = set(p_good.nsmallest(k_eff).index)
        overlaps.append(((len(c_top & p_top) / k_eff) + (len(c_bottom & p_bottom) / k_eff)) / 2.0)
    if not overlaps:
        return None
    return float(np.mean(overlaps))


def _iterative_residualize(
    candidate: pd.DataFrame,
    book: FactorBook,
    *,
    topk: int,
    rounds: int,
    top_factor_count: int,
    min_stocks: int,
) -> dict[str, Any]:
    residual = candidate.copy()
    selected: list[str] = []
    round_payloads: list[dict[str, Any]] = []
    top_factors: list[dict[str, Any]] = []
    for round_no in range(1, rounds + 1):
        remaining = FactorBook({factor_id: frame for factor_id, frame in book.factors.items() if factor_id not in selected})
        similarity = _rankcorr_similarity_summary(residual, remaining, min_stocks=min_stocks)
        if similarity.empty:
            break
        picked = similarity.head(topk)
        picked_ids = picked["factor_id"].astype(str).tolist()
        if round_no == 1:
            top_factors = [
                {
                    "factor_id": str(row["factor_id"]),
                    "contribution": _json_float(row["mean_abs_rankcorr"]),
                    "contribution_type": "abs_rankcorr",
                }
                for _, row in similarity.head(top_factor_count).iterrows()
            ]
        selected.extend(picked_ids)
        residual = _residualize_against_book(residual, book.subset(picked_ids), min_stocks=min_stocks)
        round_payloads.append(
            {
                "round": int(round_no),
                "n_factors_removed": int(len(picked_ids)),
                "factor_ids": picked_ids,
                "residual_factor": residual.copy(),
            }
        )
    return {
        "final_residual": residual,
        "selected_factor_ids": selected,
        "rounds": round_payloads,
        "top_factors": top_factors,
    }


def _residualize_against_book(candidate: pd.DataFrame, book: FactorBook, *, min_stocks: int) -> pd.DataFrame:
    residual = pd.DataFrame(index=candidate.index, columns=candidate.columns, dtype="float64")
    for date in candidate.index:
        y = pd.to_numeric(candidate.loc[date], errors="coerce")
        x_cols = []
        for factor_id in book.factor_ids:
            peer = book.factors[factor_id]
            if date in peer.index:
                x_cols.append(pd.to_numeric(peer.loc[date].reindex(candidate.columns), errors="coerce"))
        if not x_cols:
            residual.loc[date] = y
            continue
        x = pd.concat(x_cols, axis=1)
        good = y.notna() & np.isfinite(y) & x.notna().all(axis=1) & np.isfinite(x).all(axis=1)
        if int(good.sum()) < max(min_stocks, x.shape[1] + 2):
            residual.loc[date] = y
            continue
        design = np.column_stack([np.ones(int(good.sum())), x.loc[good].to_numpy(dtype="float64")])
        y_values = y.loc[good].to_numpy(dtype="float64")
        beta, *_ = np.linalg.lstsq(design, y_values, rcond=None)
        fitted = design @ beta
        residual.loc[date, good.index[good]] = y_values - fitted
    return residual


def _daily_spearman_ic(candidate: pd.DataFrame, returns: pd.DataFrame, *, min_stocks: int) -> pd.Series:
    values = {}
    aligned_returns = returns.reindex_like(candidate)
    for date in candidate.index.intersection(aligned_returns.index):
        f = pd.to_numeric(candidate.loc[date], errors="coerce")
        r = pd.to_numeric(aligned_returns.loc[date], errors="coerce")
        good = f.notna() & r.notna() & np.isfinite(f) & np.isfinite(r)
        if int(good.sum()) < min_stocks:
            continue
        corr = f.loc[good].rank().corr(r.loc[good].rank())
        if pd.notna(corr) and np.isfinite(corr):
            values[date] = float(corr)
    return pd.Series(values, dtype="float64")


def _daily_incremental_r2(
    *,
    candidate: pd.DataFrame,
    returns: pd.DataFrame,
    book: FactorBook,
    factor_ids: list[str],
    min_stocks: int,
) -> pd.Series:
    values = {}
    aligned_returns = returns.reindex_like(candidate)
    selected_book = book.subset(factor_ids)
    for date in candidate.index.intersection(aligned_returns.index):
        y = pd.to_numeric(aligned_returns.loc[date], errors="coerce")
        c = pd.to_numeric(candidate.loc[date], errors="coerce")
        x_cols = []
        for factor_id in selected_book.factor_ids:
            peer = selected_book.factors[factor_id]
            if date in peer.index:
                x_cols.append(pd.to_numeric(peer.loc[date].reindex(candidate.columns), errors="coerce"))
        x = pd.concat(x_cols, axis=1) if x_cols else pd.DataFrame(index=candidate.columns)
        good = y.notna() & c.notna() & np.isfinite(y) & np.isfinite(c)
        if not x.empty:
            good &= x.notna().all(axis=1) & np.isfinite(x).all(axis=1)
        if int(good.sum()) < max(min_stocks, x.shape[1] + 3):
            continue
        y_values = y.loc[good].to_numpy(dtype="float64")
        baseline_x = x.loc[good].to_numpy(dtype="float64") if not x.empty else np.empty((len(y_values), 0))
        full_x = np.column_stack([baseline_x, c.loc[good].to_numpy(dtype="float64")])
        baseline_r2 = _ols_r2(y_values, baseline_x)
        full_r2 = _ols_r2(y_values, full_x)
        if baseline_r2 is not None and full_r2 is not None:
            values[date] = float(full_r2 - baseline_r2)
    return pd.Series(values, dtype="float64")


def _rounds_for_horizon(rounds: list[dict[str, Any]], returns: pd.DataFrame, *, min_stocks: int) -> list[dict[str, Any]]:
    out = []
    for item in rounds:
        ic_stats = _series_mean_t(_daily_spearman_ic(item["residual_factor"], returns, min_stocks=min_stocks))
        payload = {
            "round": int(item["round"]),
            "n_factors_removed": int(item["n_factors_removed"]),
            "factor_ids": item["factor_ids"],
            "residual_ic": _json_float(ic_stats["mean"]),
            "residual_ic_t": _json_float(ic_stats["t_stat"]),
            "n_obs": int(ic_stats["n_obs"]),
        }
        out.append(_drop_missing(payload))
    return out


def _mechanism_horizon_cell(
    *,
    factor: pd.DataFrame,
    returns: pd.DataFrame,
    daily_group_returns: pd.DataFrame,
    horizon: Any,
    regime: pd.DataFrame | None,
    diagnostics: CompanyDiagnosticsConfig,
) -> dict[str, Any] | None:
    group_monotonicity = _group_monotonicity(daily_group_returns, horizon)
    ic_series = _daily_spearman_ic(factor, returns, min_stocks=diagnostics.min_similarity_stocks)
    if group_monotonicity is None and ic_series.empty:
        return None
    cell: dict[str, Any] = {
        "group_monotonicity": _json_float(group_monotonicity),
        "n_obs": int(len(ic_series)),
    }
    if regime is not None:
        regime_detail = {}
        sign_matches = []
        for label in regime.columns:
            dates = regime.index[regime[label]].intersection(ic_series.index)
            if len(dates) == 0:
                continue
            regime_ic = float(ic_series.loc[dates].mean())
            match = _sign_matches_hypothesis(regime_ic, diagnostics.hypothesis_direction)
            if match is not None:
                sign_matches.append(match)
            regime_detail[label] = {
                "ic": _json_float(regime_ic),
                "sign_matches_hypothesis": match,
                "n_obs": int(len(dates)),
            }
        if regime_detail:
            cell["regime_detail"] = regime_detail
        cell["regime_sign_consistency"] = (
            _json_float(float(np.mean(sign_matches))) if sign_matches and diagnostics.hypothesis_direction != "unknown" else None
        )
    return _drop_missing(cell, keep_null_keys={"regime_sign_consistency", "sign_matches_hypothesis"})


def _group_monotonicity(daily_group_returns: pd.DataFrame, horizon: Any) -> float | None:
    if daily_group_returns.empty:
        return None
    try:
        summary = daily_group_returns.xs(horizon, level="horizon")["group_return"].groupby("group").mean()
    except KeyError:
        return None
    if len(summary.dropna()) < 3:
        return None
    group_rank = pd.Series(summary.index.astype(float), index=summary.index).rank()
    return_rank = summary.astype(float).rank()
    corr = group_rank.corr(return_rank)
    return float(corr) if pd.notna(corr) and np.isfinite(corr) else None


def _sign_matches_hypothesis(value: float, direction: str) -> bool | None:
    if direction == "unknown" or not np.isfinite(value):
        return None
    if direction == "high_is_long":
        return bool(value > 0)
    if direction == "high_is_short":
        return bool(value < 0)
    return None


def _max_abs_returncorr(context: dict[str, Any], max_peer: str) -> float | None:
    return None


def _ols_r2(y: np.ndarray, x: np.ndarray) -> float | None:
    if len(y) <= x.shape[1] + 1:
        return None
    design = np.column_stack([np.ones(len(y)), x])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    fitted = design @ beta
    ss_total = float(np.sum((y - y.mean()) ** 2))
    if ss_total <= 0:
        return None
    ss_resid = float(np.sum((y - fitted) ** 2))
    return 1.0 - ss_resid / ss_total


def _series_mean_t(series: pd.Series) -> dict[str, float | int]:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return {"mean": math.nan, "t_stat": math.nan, "n_obs": 0}
    mean = float(s.mean())
    std = float(s.std(ddof=1)) if len(s) > 1 else math.nan
    t_stat = mean / (std / math.sqrt(len(s))) if std and not math.isnan(std) and len(s) > 1 else math.nan
    return {"mean": mean, "t_stat": t_stat, "n_obs": int(len(s))}


def _base_meta(pool_contexts: dict[str, dict[str, Any]], diagnostics: CompanyDiagnosticsConfig) -> dict[str, Any]:
    indexes = [context["factor"].index for context in pool_contexts.values() if not context["factor"].empty]
    if indexes:
        all_dates = indexes[0]
        for index in indexes[1:]:
            all_dates = all_dates.union(index)
        as_of_start = all_dates.min().date().isoformat()
        as_of_end = all_dates.max().date().isoformat()
        n_trading_days = int(len(all_dates))
    else:
        as_of_start = None
        as_of_end = None
        n_trading_days = 0
    return {
        "as_of_start": as_of_start,
        "as_of_end": as_of_end,
        "framework_version": diagnostics.framework_version,
        "n_trading_days": n_trading_days,
    }


def _with_meta(
    meta_base: dict[str, Any],
    *,
    baseline_book_version: str | None = None,
    peer_book_version: str | None = None,
    pools=None,
    horizons=None,
) -> dict[str, Any]:
    meta = dict(meta_base)
    if baseline_book_version:
        meta["baseline_book_version"] = baseline_book_version
    if peer_book_version:
        meta["peer_book_version"] = peer_book_version
    coverage = {}
    if pools is not None:
        coverage["pools"] = _ordered_contract_pools(pools)
    if horizons is not None:
        coverage["horizons"] = _ordered_contract_horizons(horizons)
    if coverage:
        meta["coverage"] = coverage
    return _drop_missing(meta)


def _contract_pool_contexts(pool_contexts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {pool: pool_contexts[pool] for pool in CONTRACT_POOLS if pool in pool_contexts}


def _contract_horizon_items(context: dict[str, Any]) -> list[tuple[Any, pd.DataFrame, str]]:
    items = []
    seen = set()
    return_horizon_days = context.get("return_horizon_days", {})
    for horizon, returns in context["future_returns"].items():
        key = _horizon_key(horizon, return_horizon_days)
        if key not in CONTRACT_HORIZONS or key in seen:
            continue
        seen.add(key)
        items.append((horizon, returns, key))
    return sorted(items, key=lambda item: CONTRACT_HORIZONS.index(item[2]))


def _horizon_key(horizon: Any, return_horizon_days: dict[str, Any]) -> str:
    label = return_label(horizon)
    days = return_horizon_days.get(label)
    if days is not None:
        return str(int(days))
    if label.endswith("d") and label[:-1].isdigit():
        return label[:-1]
    return label


def _ordered_contract_pools(pools) -> list[str]:
    selected = set(pools)
    return [pool for pool in CONTRACT_POOLS if pool in selected]


def _ordered_contract_horizons(horizons) -> list[str]:
    selected = {str(horizon) for horizon in horizons}
    return [horizon for horizon in CONTRACT_HORIZONS if horizon in selected]


def _merge_ordered_horizons(current: list[str], new_horizons) -> list[str]:
    selected = set(current).union(str(horizon) for horizon in new_horizons)
    return _ordered_contract_horizons(selected)


def _parse_bool_series(series: pd.Series, *, label: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)
    if pd.api.types.is_numeric_dtype(series):
        return series.fillna(0).astype(float) != 0

    true_values = {"true", "t", "1", "yes", "y"}
    false_values = {"false", "f", "0", "no", "n", ""}
    parsed = []
    for value in series:
        if pd.isna(value):
            parsed.append(False)
            continue
        normalized = str(value).strip().lower()
        if normalized in true_values:
            parsed.append(True)
        elif normalized in false_values:
            parsed.append(False)
        else:
            raise ValueError(f"Regime column {label!r} contains unsupported boolean value: {value!r}")
    return pd.Series(parsed, index=series.index, dtype=bool)


def _json_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(out):
        return None
    return out


def _drop_missing(payload: dict[str, Any], keep_null_keys: set[str] | None = None) -> dict[str, Any]:
    keep_null_keys = keep_null_keys or set()
    out = {}
    for key, value in payload.items():
        if value is None and key not in keep_null_keys:
            continue
        if isinstance(value, dict):
            out[key] = _drop_missing(value, keep_null_keys=keep_null_keys)
        else:
            out[key] = value
    return out


def _pick_column(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None
