"""시간순 모델 선택과 일별 예측 원장의 실제 동작을 검증한다."""
import json
import uuid
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import forecast_utils as fu


class ForecastLedgerTests(unittest.TestCase):
    def setUp(self):
        self.bars = pd.DataFrame({
            "open": [100., 102., 104.], "close": [100., 101., 105.],
            "adj_close": [50., 50.5, 52.5],
        }, index=pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"]))
        self.record = dict(record_id="a", run_id="r1", model="m", kind="direction",
                           prediction_date="2026-09-02", target_date="2026-09-02",
                           as_of_date="2026-09-01", created_at_utc="2026-09-01T22:00:00Z",
                           target_mode="close_to_close", band=0.005, config_hash="c",
                           horizon_days=1, p_down=.1, p_flat=.1, p_up=.8)

    def score(self, rows, now="2026-09-04T00:00:00Z"):
        return fu.evaluate_forecasts(pd.DataFrame(rows), self.bars, now=now)

    def test_labels_use_each_records_band_and_target_mode(self):
        rows = [self.record, dict(self.record, record_id="b", band=.02),
                dict(self.record, record_id="c", target_mode="open_to_close")]
        got = self.score(rows)
        self.assertEqual(got.actual_class.tolist(), [2, 1, 0])
        self.assertEqual(got.direction_correct.tolist(), [1., 0., 0.])
        self.assertAlmostEqual(got.actual_return.iloc[0], .01)

    def test_unclosed_or_future_targets_remain_pending(self):
        result = self.score([self.record], now="2026-09-02T02:00:00Z")
        self.assertEqual(result.status.iloc[0], "pending")
        self.assertTrue(pd.isna(result.actual_close.iloc[0]))
        future = dict(self.record, target_date="2026-09-07")
        self.assertEqual(self.score([future]).status.iloc[0], "pending")

    def test_price_error_and_interval_are_saved(self):
        rec = dict(self.record, kind="price", target_date="2026-09-03", horizon_days=2,
                   current_close=100., predicted_close=103., center_close=103.,
                   predicted_return=.03, low_close=99., high_close=104.)
        got = self.score([rec]).iloc[0]
        self.assertEqual(got.price_error, -2.)  # prediction - actual
        self.assertEqual(got.absolute_price_error, 2.)
        self.assertAlmostEqual(got.return_error, -.02)
        self.assertEqual(got.interval_hit, 0.)

    def test_no_signal_does_not_turn_into_a_point_prediction(self):
        rec = dict(self.record, kind="price", current_close=100., predicted_close=np.nan,
                   predicted_return=np.nan, center_close=100., low_close=99., high_close=103.)
        got = self.score([rec]).iloc[0]
        self.assertTrue(pd.isna(got.price_error))
        self.assertEqual(got.center_price_error, -1.)
        self.assertEqual(got.interval_hit, 1.)

    def test_append_is_idempotent_and_never_replaces_a_prediction(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "forecast_log.csv"
            fu.append_forecasts(path, pd.DataFrame([self.record]))
            fu.append_forecasts(path, pd.DataFrame([dict(self.record, p_up=.2)]))
            fu.append_forecasts(path, pd.DataFrame([dict(self.record, record_id="b", run_id="r2")]))
            got = pd.read_csv(path)
            self.assertEqual(len(got), 2)
            self.assertAlmostEqual(got.p_up.iloc[0], .8)

    def test_daily_report_uses_first_prospective_run_only(self):
        early = dict(self.record, record_id="early", created_at_utc="2026-09-01T21:00:00Z")
        late = dict(self.record, record_id="late", created_at_utc="2026-09-02T01:00:00Z")
        got = fu.daily_comparison(self.score([self.record, early, late]))
        self.assertEqual(got.record_id.tolist(), ["early"])
        self.assertFalse(self.score([late]).is_prospective.iloc[0])

    def test_legacy_rows_survive_but_are_not_prospective_evidence(self):
        legacy = {k: v for k, v in self.record.items() if k not in ["created_at_utc", "record_id"]}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "forecast_log.csv"
            pd.DataFrame([legacy]).to_csv(path, index=False)
            fu.append_forecasts(path, pd.DataFrame([self.record]))
            scored = self.score(pd.read_csv(path).to_dict("records"))
            self.assertEqual(len(scored), 2)
            self.assertFalse(scored.is_prospective.iloc[0])

    def test_legacy_only_log_can_be_updated_before_new_prediction(self):
        legacy = {k: v for k, v in self.record.items() if k not in
                  ["created_at_utc", "record_id", "kind", "target_date", "config_hash", "horizon_days"]}
        daily = fu.daily_comparison(self.score([legacy]))
        self.assertEqual(len(daily), 0)
        self.assertEqual(len(fu.summarize_daily(daily)), 0)

    def test_missing_adjusted_close_cannot_be_scored_as_flat(self):
        self.bars.loc[pd.Timestamp("2026-09-02"), "adj_close"] = np.nan
        got = self.score([self.record]).iloc[0]
        self.assertEqual(got.status, "missing_actual")
        self.assertTrue(pd.isna(got.actual_class))

    def test_saved_pending_record_is_scored_on_a_later_run(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "forecast_log.csv"
            pending = self.score([self.record], now="2026-09-02T02:00:00Z")
            fu.atomic_csv(pending, path)
            later = self.score(pd.read_csv(path).to_dict("records"))
            fu.atomic_csv(later, path)
            saved = pd.read_csv(path).iloc[0]
            self.assertEqual(saved.record_id, "a")
            self.assertAlmostEqual(saved.p_up, .8)
            self.assertEqual(saved.status, "scored")
            self.assertEqual(saved.actual_close, 101.)
            self.assertEqual(saved.direction_correct, 1.)


class SelectionTests(unittest.TestCase):
    def test_live_window_contains_only_recent_past(self):
        dates = pd.bdate_range("2015-01-01", "2026-09-04")
        idx = fu.rolling_train_indices(dates, "2026-09-04", years=5)
        self.assertTrue((dates[idx] < pd.Timestamp("2026-09-04")).all())
        self.assertTrue((dates[idx] >= pd.Timestamp("2021-09-04")).all())

    def test_outer_test_labels_cannot_change_selected_model(self):
        rng = np.random.default_rng(4)
        X = rng.normal(size=(700, 3)).astype(np.float32)
        y = np.digitize(X[:, 0] + rng.normal(0, .3, 700), [-.5, .5])
        a = fu.fit_direction_model(X, y, np.arange(600), "Logistic")
        altered = y.copy(); altered[600:] = (altered[600:] + 1) % 3
        b = fu.fit_direction_model(X, altered, np.arange(600), "Logistic")
        self.assertEqual(a["selection"], b["selection"])
        np.testing.assert_allclose(fu.predict_direction_model(a, X[600:]),
                                   fu.predict_direction_model(b, X[600:]))
        self.assertLess(a["selection"]["last_validation_position"], 600)

    def test_probabilities_are_finite_with_a_missing_training_class(self):
        rng = np.random.default_rng(6)
        X = rng.normal(size=(600, 3)); y = (X[:, 0] > 0).astype(int)
        model = fu.fit_direction_model(X, y, np.arange(600), "Logistic")
        p = fu.predict_direction_model(model, X[-3:])
        self.assertEqual(p.shape, (3, 3))
        self.assertTrue(np.isfinite(p).all())
        np.testing.assert_allclose(p.sum(axis=1), 1.)


class PriceCalibrationTests(unittest.TestCase):
    def test_final_labels_do_not_change_calibration_or_gate(self):
        rng = np.random.default_rng(2)
        p = rng.normal(0, .02, 800)
        y = .6 * p + rng.normal(0, .01, 800)
        sigma = np.full(800, .02)
        dates = pd.bdate_range("2021-01-01", periods=800)
        def ci(dates, fn):
            value = fn(np.arange(len(dates)))
            return value - .0001, value + .0001
        a = fu.calibrate_price_forecast(y, p, sigma, dates, 20, ci)
        changed = y.copy(); changed[650:] += .5
        b = fu.calibrate_price_forecast(changed, p, sigma, dates, 20, ci)
        self.assertEqual(a["oof_slope"], b["oof_slope"])
        self.assertEqual(a["band_q"], b["band_q"])
        self.assertEqual(a["beats_baseline"], b["beats_baseline"])
        self.assertGreater(a["band_coverage_realized"], b["band_coverage_realized"])
        self.assertLess(a["calibration_end"] + 19, a["gate_start"])
        self.assertLess(a["gate_end"] + 19, a["evaluation_start"])


class NotebookEmbeddingTests(unittest.TestCase):
    def test_notebook_helpers_match_tested_module(self):
        root = Path(__file__).resolve().parents[1]
        nb = json.loads((root / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        embedded = [c for c in nb["cells"] if "forecast_utils" in c.get("metadata", {}).get("tags", [])]
        self.assertEqual(len(embedded), 1)
        self.assertEqual("".join(embedded[0]["source"]), (root / "forecast_utils.py").read_text(encoding="utf-8"))

    def test_retraining_in_same_runtime_starts_a_new_run(self):
        root = Path(__file__).resolve().parents[1]
        nb = json.loads((root / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        live = next("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"
                    and 'live_X = live_row' in "".join(c["source"]))
        start = live.split('live_X = live_row')[0]
        ns = {"pd": pd, "uuid": uuid, "STORAGE_ROOT": Path("unused")}
        exec(start, ns)
        first = ns["RUN_ID"]
        exec(start, ns)
        self.assertNotEqual(ns["RUN_ID"], first)
        self.assertEqual(ns["OUTPUT_DIR"].name, ns["RUN_ID"])


if __name__ == "__main__":
    unittest.main()
