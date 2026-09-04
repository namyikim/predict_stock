import json
import unittest
from pathlib import Path


NOTEBOOK_PATH = Path(__file__).resolve().parents[1] / "samsung_direction_model_colab.ipynb"


def _strip_magics(source):
    return "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith(("%", "!"))
    )


class NotebookStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
        cls.code_cells = [
            "".join(cell.get("source", []))
            for cell in cls.notebook["cells"]
            if cell.get("cell_type") == "code"
        ]
        cls.source = "\n".join(cls.code_cells)

    def test_every_code_cell_is_valid_python(self):
        for index, cell in enumerate(self.code_cells):
            compile(_strip_magics(cell), f"cell-{index}", "exec")

    def test_target_and_band_modes_are_configurable(self):
        for setting in ("TARGET_MODE", "BAND_MODE", "VOL_BAND_MULT", "LIVE_OPEN_PRICE"):
            self.assertIn(f"{setting} = ", self.source)
        self.assertIn('if TARGET_MODE == "open_to_close":', self.source)
        self.assertIn('if BAND_MODE == "vol_scaled":', self.source)
        # 변동성 밴드는 전일까지의 정보만 사용해야 한다.
        self.assertIn("sam_ret.rolling(20).std()).reindex(all_dates).shift(1)", self.source)
        # band 열은 특징이 아니라 라벨 정의에만 쓰인다.
        self.assertIn('c not in ["target", "target_return", "band"]', self.source)

    def test_stacking_uses_only_previous_folds(self):
        self.assertIn('STACK_BASE_MODELS = ["Logistic", "LightGBM", "Transformer"]', self.source)
        self.assertIn("train_mask = (fold_by_date < fold_id).to_numpy()", self.source)
        self.assertIn("stack_meta_full", self.source)

    def test_live_ensemble_weights_use_full_oof_window(self):
        self.assertIn("MIN_OOF_DAYS_FOR_WEIGHT", self.source)
        self.assertIn('native_metrics.loc[name, "log_loss"]', self.source)

    def test_defines_week_and_month_as_trading_day_horizons(self):
        self.assertIn('FORECAST_HORIZONS = {"1주일": 5, "1개월": 20}', self.source)

    def test_trains_direct_return_regressors_without_future_feature_leakage(self):
        self.assertIn("from sklearn.linear_model import LogisticRegression, Ridge", self.source)
        self.assertIn("from lightgbm import LGBMClassifier, LGBMRegressor", self.source)
        self.assertIn('sam_close = sam["adj_close"]', self.source)
        self.assertIn(
            "sam_close.shift(-(horizon - 1)) / sam_close.shift(1) - 1",
            self.source,
        )
        self.assertIn("TimeSeriesSplit(n_splits=n_splits, gap=horizon - 1)", self.source)

    def test_price_forecast_reports_zero_return_baseline(self):
        self.assertIn('validation_mae["Zero-return baseline"]', self.source)
        self.assertIn('"zero_baseline_mae"', self.source)

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
        for key in (
            '"multi_horizon_price_forecast.csv"',
            '"forecast_horizons"',
            '"price_forecast_validation_mae"',
            '"target_mode"',
            '"band_mode"',
            '"stacking_base_models"',
        ):
            self.assertIn(key, self.source)


if __name__ == "__main__":
    unittest.main()
