import json
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from factor_backtest_platform.result_loader import load_backtest_result


def test_load_backtest_result_reads_latest_metadata_and_tables():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run_dir = root / "factor_dm_20d" / "latest"
        table_dir = run_dir / "pools" / "all" / "tables"
        table_dir.mkdir(parents=True)
        (run_dir / "run_meta.json").write_text(json.dumps({"factor_name": "factor_dm_20d"}), encoding="utf-8")
        (run_dir / "run_log.json").write_text(json.dumps({"warnings": []}), encoding="utf-8")
        pd.DataFrame({"ic_mean": [0.1]}, index=["1d"]).to_csv(table_dir / "ic_stats.csv")

        result = load_backtest_result(factor_name="factor_dm_20d", output_root=root)

        assert result.meta["factor_name"] == "factor_dm_20d"
        table = result.read_table("all", "ic_stats")
        assert float(table.loc["1d", "ic_mean"]) == 0.1


def test_named_run_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="越出因子目录"):
        load_backtest_result(
            factor_name="factor_dm_20d",
            output_root=tmp_path,
            run="../../outside",
        )
