import json
import unittest
from pathlib import Path


NOTEBOOK_PATH = Path(__file__).resolve().parents[1] / "samsung_direction_model_colab.ipynb"


class MultiHorizonNotebookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
        cls.source = "\n".join(
            "".join(cell.get("source", [])) for cell in cls.notebook["cells"]
        )

    def test_defines_week_and_month_as_trading_day_horizons(self):
        self.assertIn('FORECAST_HORIZONS = {"1주일": 5, "1개월": 20}', self.source)

    def test_trains_direct_return_regressors_without_future_feature_leakage(self):
        self.assertIn("from sklearn.linear_model import LogisticRegression, Ridge", self.source)
        self.assertIn("from lightgbm import LGBMClassifier, LGBMRegressor", self.source)
        self.assertIn('price_close = sam["close"]', self.source)
        self.assertIn(
            "price_close.shift(-(horizon - 1)) / price_close.shift(1) - 1",
            self.source,
        )
        self.assertIn("TimeSeriesSplit", self.source)

    def test_final_report_contains_price_and_return_forecasts(self):
        for column in (
            '"horizon"',
            '"target_date"',
            '"current_close"',
            '"predicted_return"',
            '"predicted_close"',
        ):
            self.assertIn(column, self.source)
        self.assertIn("price_forecast_table", self.source)

    def test_forecast_is_exported_with_reproducibility_metadata(self):
        self.assertIn('"multi_horizon_price_forecast.csv"', self.source)
        self.assertIn('"forecast_horizons"', self.source)
        self.assertIn('"price_forecast_validation_mae"', self.source)

    def test_new_forecast_cell_is_valid_python(self):
        matching_cells = [
            cell
            for cell in self.notebook["cells"]
            if 'FORECAST_HORIZONS = {"1주일": 5, "1개월": 20}'
            in "".join(cell.get("source", []))
            and cell.get("cell_type") == "code"
        ]
        self.assertEqual(len(matching_cells), 1)
        compile("".join(matching_cells[0]["source"]), "forecast-cell", "exec")


if __name__ == "__main__":
    unittest.main()
