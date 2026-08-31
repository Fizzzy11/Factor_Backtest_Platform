from pathlib import Path
import tempfile

import pandas as pd

from factor_backtest_platform.io import write_table


def test_write_table_does_not_disguise_pickle_as_parquet_when_engine_missing():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "data.parquet"

        original = pd.DataFrame.to_parquet

        def raise_import_error(self, *args, **kwargs):
            raise ImportError("missing parquet engine")

        pd.DataFrame.to_parquet = raise_import_error
        try:
            written = write_table(pd.DataFrame({"a": [1]}), path)
        finally:
            pd.DataFrame.to_parquet = original

        assert written == Path(tmp) / "data.parquet.pkl"
        assert written.exists()
        assert not path.exists()


def test_write_table_csv_limits_float_output_to_four_decimal_places():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "table.csv"

        write_table(pd.DataFrame({"value": [1 / 3, 1.2, -0.00001]}), path)

        text = path.read_text(encoding="utf-8")
        assert "0.3333" in text
        assert "1.2" in text
        assert "-0.0000" not in text
        assert "0.333333" not in text
