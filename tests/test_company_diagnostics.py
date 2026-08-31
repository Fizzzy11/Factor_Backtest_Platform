import json

import numpy as np
import pandas as pd

from factor_backtest.config import BacktestConfig, CompanyDiagnosticsConfig, DataSourceConfig, PathConfig
from factor_backtest.company_diagnostics import export_company_diagnostics, load_regime_frame
from factor_backtest.market_data import MarketDataBundle
from factor_backtest.runner import run_factor_backtest


def _sample_factor_market_and_books(tmp_path):
    dates = pd.bdate_range("2026-01-01", periods=18)
    factor_dates = dates[:10]
    symbols = [f"S{i:03d}" for i in range(30)]

    base = np.arange(len(dates), dtype=float).reshape(-1, 1)
    slopes = np.linspace(0.01, 0.04, len(symbols)).reshape(1, -1)
    open_price = pd.DataFrame(10 + base * slopes, index=dates, columns=symbols)

    symbol_rank = np.arange(len(symbols), dtype=float)
    factor_values = np.tile(symbol_rank, (len(factor_dates), 1))
    factor_values += np.arange(len(factor_dates), dtype=float).reshape(-1, 1) * 0.05
    factor = pd.DataFrame(factor_values, index=factor_dates, columns=symbols)

    production_rows = []
    peer_rows = []
    for date in factor_dates:
        for idx, symbol in enumerate(symbols):
            candidate = factor.loc[date, symbol]
            production_rows.append(
                {"date": date, "symbol": symbol, "factor_id": "prod_rank_v1", "value": candidate * 0.85}
            )
            production_rows.append(
                {"date": date, "symbol": symbol, "factor_id": "prod_inverse_v1", "value": -candidate}
            )
            peer_rows.append({"date": date, "symbol": symbol, "factor_id": "peer_rank_v1", "value": candidate})
            peer_rows.append(
                {"date": date, "symbol": symbol, "factor_id": "peer_noise_v1", "value": float(idx % 7)}
            )

    production_path = tmp_path / "production_book.csv"
    peer_path = tmp_path / "peer_book.csv"
    pd.DataFrame(production_rows).to_csv(production_path, index=False)
    pd.DataFrame(peer_rows).to_csv(peer_path, index=False)

    regime = pd.DataFrame(
        {
            "date": factor_dates,
            "bull": [True, True, False, False, True, True, False, False, True, False],
            "bear": [False, False, True, True, False, False, True, True, False, True],
            "high_vol": [False, True, False, True, False, True, False, True, False, True],
            "low_vol": [True, False, True, False, True, False, True, False, True, False],
        }
    )
    regime_path = tmp_path / "regime.csv"
    regime.to_csv(regime_path, index=False)

    return factor, MarketDataBundle(open_price=open_price), production_path, peer_path, regime_path


def test_company_diagnostics_disabled_by_default_does_not_write_diagnostics(tmp_path):
    factor, market, production_path, peer_path, regime_path = _sample_factor_market_and_books(tmp_path)
    cfg = BacktestConfig(
        paths=PathConfig(project_dir=tmp_path, data_root=tmp_path, pool_dir=tmp_path),
        output_root=tmp_path / "results",
        selected_pools=["all"],
        horizons=[1],
        factor_name="diagnostic_factor_v1",
        tradability_filter=False,
        render_plots=False,
        data_sources=DataSourceConfig(risk_exposure_source="none"),
        enabled_sections=["data_quality"],
    )

    result = run_factor_backtest(factor_df=factor, market_data=market, config=cfg, log_fn=lambda *_: None)

    assert not (result.run_dir / "diagnostics").exists()
    assert result.latest_dir is not None
    assert not (result.latest_dir / "diagnostics").exists()


def test_company_diagnostics_writes_computed_json_to_run_and_latest(tmp_path):
    factor, market, production_path, peer_path, regime_path = _sample_factor_market_and_books(tmp_path)
    cfg = BacktestConfig(
        paths=PathConfig(project_dir=tmp_path, data_root=tmp_path, pool_dir=tmp_path),
        output_root=tmp_path / "results",
        selected_pools=["all"],
        horizons=[1, 5],
        factor_name="diagnostic_factor_v1",
        tradability_filter=False,
        render_plots=False,
        data_sources=DataSourceConfig(risk_exposure_source="none"),
        enabled_sections=["data_quality", "group_return"],
        diagnostics=CompanyDiagnosticsConfig(
            enabled=True,
            production_book_path=production_path,
            peer_book_path=peer_path,
            regime_path=regime_path,
            baseline_suite_id="production_book_test",
            baseline_book_version="production_book_test",
            peer_book_version="peer_book_test",
            hypothesis_direction="unknown",
            idea_id="idea_test",
            version=1,
            spanning_topk=1,
            spanning_rounds=2,
            topk_overlap_k=5,
            min_similarity_stocks=10,
        ),
    )

    result = run_factor_backtest(factor_df=factor, market_data=market, config=cfg, log_fn=lambda *_: None)

    for base_dir in [result.run_dir, result.latest_dir]:
        diagnostics_dir = base_dir / "diagnostics"
        assert diagnostics_dir.exists()
        assert {path.name for path in diagnostics_dir.glob("*.json")} == {
            "spanning.json",
            "crowding.json",
            "mechanism_consistency.json",
            "diversity.json",
        }

        spanning = json.loads((diagnostics_dir / "spanning.json").read_text(encoding="utf-8"))
        assert spanning["status"] == "computed"
        assert spanning["baseline_suite_id"] == "production_book_test"
        assert spanning["spanning_method"] == {
            "type": "iterative_topk_residualization",
            "topk": 1,
            "rounds": 2,
        }
        assert "all" in spanning["pools"]
        assert set(spanning["pools"]["all"]["horizons"]) == {"1", "5"}
        one_day = spanning["pools"]["all"]["horizons"]["1"]
        assert isinstance(one_day["incremental_ic"], float)
        assert isinstance(one_day["incremental_r2"], float)
        assert one_day["n_obs"] > 0
        assert one_day["residualization_rounds"]
        assert spanning["top_spanning_factors"][0]["factor_id"] in {"prod_rank_v1", "prod_inverse_v1"}

        crowding = json.loads((diagnostics_dir / "crowding.json").read_text(encoding="utf-8"))
        assert crowding["status"] == "computed"
        assert crowding["pool_id"] == "research+promoted"
        assert crowding["pools"]["all"]["max_corr_peer"] == "peer_rank_v1"
        assert crowding["pools"]["all"]["topk_k"] == 5
        assert crowding["pools"]["all"]["n_peers"] == 2
        assert crowding["pools"]["all"]["operator_graph_similarity"] is None
        assert crowding["pools"]["all"]["operator_graph_similarity_status"] == "pending(dsl)"

        mechanism = json.loads((diagnostics_dir / "mechanism_consistency.json").read_text(encoding="utf-8"))
        assert mechanism["status"] == "computed"
        assert mechanism["hypothesis_direction"] == "unknown"
        assert mechanism["pools"]["all"]["horizons"]["1"]["regime_sign_consistency"] is None
        assert set(mechanism["pools"]["all"]["horizons"]["1"]["regime_detail"]) == {
            "bull",
            "bear",
            "high_vol",
            "low_vol",
        }

        diversity = json.loads((diagnostics_dir / "diversity.json").read_text(encoding="utf-8"))
        assert diversity["status"] == "computed"
        assert diversity["idea_id"] == "idea_test"
        assert diversity["version"] == 1
        assert diversity["max_similarity_peer"] == "peer_rank_v1"
        assert diversity["novelty"] == 1 - diversity["max_similarity_to_book"]


def test_company_diagnostics_skips_files_for_missing_inputs(tmp_path):
    factor, market, production_path, peer_path, regime_path = _sample_factor_market_and_books(tmp_path)
    cfg = BacktestConfig(
        paths=PathConfig(project_dir=tmp_path, data_root=tmp_path, pool_dir=tmp_path),
        output_root=tmp_path / "results",
        selected_pools=["all"],
        horizons=[1],
        factor_name="diagnostic_factor_v1",
        tradability_filter=False,
        render_plots=False,
        data_sources=DataSourceConfig(risk_exposure_source="none"),
        enabled_sections=["data_quality"],
        diagnostics=CompanyDiagnosticsConfig(enabled=True, regime_path=regime_path, hypothesis_direction="unknown"),
    )

    result = run_factor_backtest(factor_df=factor, market_data=market, config=cfg, log_fn=lambda *_: None)

    diagnostics_dir = result.run_dir / "diagnostics"
    assert diagnostics_dir.exists()
    assert {path.name for path in diagnostics_dir.glob("*.json")} == {"mechanism_consistency.json"}
    payload = json.loads((diagnostics_dir / "mechanism_consistency.json").read_text(encoding="utf-8"))
    assert payload["status"] == "computed"


def test_company_diagnostics_filters_contract_pools_and_horizons(tmp_path):
    factor, market, production_path, peer_path, regime_path = _sample_factor_market_and_books(tmp_path)
    valid_return = factor.rank(axis=1) * 0.001
    non_contract_return = factor.rank(axis=1) * -0.001
    context = {
        "factor": factor,
        "future_returns": {
            "1d": valid_return,
            "3d": non_contract_return,
            "custom_alpha": non_contract_return,
        },
        "daily_group_returns": pd.DataFrame(
            columns=["group_return"],
            index=pd.MultiIndex.from_arrays(
                [[], [], []],
                names=["trade_date", "horizon", "group"],
            ),
        ),
        "return_horizon_days": {"1d": 1, "3d": 3, "custom_alpha": None},
    }
    cfg = BacktestConfig(
        paths=PathConfig(project_dir=tmp_path, data_root=tmp_path, pool_dir=tmp_path),
        diagnostics=CompanyDiagnosticsConfig(
            enabled=True,
            production_book_path=production_path,
            peer_book_path=peer_path,
            regime_path=regime_path,
            baseline_book_version="production_book_test",
            peer_book_version="peer_book_test",
            min_similarity_stocks=10,
        ),
    )

    export_company_diagnostics(
        run_dir=tmp_path / "run",
        config=cfg,
        pool_contexts={"all": context, "gz1000_pool": context, "zz2000_pool": context},
    )

    diagnostics_dir = tmp_path / "run" / "diagnostics"
    spanning = json.loads((diagnostics_dir / "spanning.json").read_text(encoding="utf-8"))
    crowding = json.loads((diagnostics_dir / "crowding.json").read_text(encoding="utf-8"))
    mechanism = json.loads((diagnostics_dir / "mechanism_consistency.json").read_text(encoding="utf-8"))

    assert set(spanning["pools"]) == {"all", "zz2000_pool"}
    assert set(crowding["pools"]) == {"all", "zz2000_pool"}
    assert set(mechanism["pools"]) == {"all", "zz2000_pool"}
    assert set(spanning["pools"]["all"]["horizons"]) == {"1"}
    assert set(mechanism["pools"]["all"]["horizons"]) == {"1"}
    assert spanning["meta"]["coverage"] == {"pools": ["all", "zz2000_pool"], "horizons": ["1"]}
    assert mechanism["meta"]["coverage"] == {"pools": ["all", "zz2000_pool"], "horizons": ["1"]}
    assert crowding["meta"]["coverage"] == {"pools": ["all", "zz2000_pool"]}


def test_regime_loader_parses_string_booleans(tmp_path):
    regime_path = tmp_path / "regime.csv"
    pd.DataFrame(
        {
            "date": ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"],
            "bull": ["true", "False", "1", "0"],
            "bear": ["FALSE", "true", "0", "1"],
        }
    ).to_csv(regime_path, index=False)

    regime = load_regime_frame(regime_path, ["bull", "bear"])

    assert regime["bull"].tolist() == [True, False, True, False]
    assert regime["bear"].tolist() == [False, True, False, True]


def test_crowding_above_0_7_count_uses_fixed_threshold(tmp_path):
    factor, market, production_path, peer_path, regime_path = _sample_factor_market_and_books(tmp_path)
    cfg = BacktestConfig(
        paths=PathConfig(project_dir=tmp_path, data_root=tmp_path, pool_dir=tmp_path),
        output_root=tmp_path / "results",
        selected_pools=["all"],
        horizons=[1],
        factor_name="diagnostic_factor_v1",
        tradability_filter=False,
        render_plots=False,
        data_sources=DataSourceConfig(risk_exposure_source="none"),
        enabled_sections=["data_quality"],
        diagnostics=CompanyDiagnosticsConfig(
            enabled=True,
            peer_book_path=peer_path,
            peer_book_version="peer_book_test",
            crowding_threshold=0.1,
            min_similarity_stocks=10,
        ),
    )

    result = run_factor_backtest(factor_df=factor, market_data=market, config=cfg, log_fn=lambda *_: None)

    crowding = json.loads((result.run_dir / "diagnostics" / "crowding.json").read_text(encoding="utf-8"))
    assert crowding["pools"]["all"]["n_peers_above_0.7"] == 1
