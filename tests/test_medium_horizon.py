# -*- coding: utf-8 -*-
"""중기(5·20거래일) 실험 러너의 계약: 설계 행렬·누수 검사·진단 도구·재개.

노트북을 돌리지 않는다. 노트북 네임스페이스와 같은 모양의 작은 합성 자료로 동작·누수만 검사한다
(합성 자료의 성능은 어떤 주장에도 쓰지 않는다).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import run_medium_horizon as mh  # noqa: E402
import forecast_utils as fu  # noqa: E402


def synthetic_namespace(n=900, seed=0):
    """노트북이 남기는 이름 중 러너가 읽는 것만 흉내 낸다."""
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex(pd.bdate_range("2019-01-01", periods=n))
    ret = rng.normal(0, .015, n)
    close = 100 * np.cumprod(1 + ret)
    sam = pd.DataFrame({"open": close * (1 + rng.normal(0, .003, n)), "high": close * 1.01, "low": close * .99,
                        "close": close, "adj_close": close * .98, "volume": rng.integers(1e5, 1e6, n)}, index=idx)
    feat = pd.DataFrame(index=idx)
    r = sam["adj_close"].pct_change()
    feat["sam_ret_1"], feat["sam_ret_5"] = r.shift(1), sam["adj_close"].pct_change(5).shift(1)
    feat["sam_vol_20"] = r.rolling(20).std().shift(1)
    feat["noise"] = rng.normal(size=n)
    feature_cols = ["sam_ret_1", "sam_ret_5", "noise"]

    def make_price_model():
        return Pipeline([("scaler", StandardScaler()), ("model", Ridge(alpha=1e4))])

    def block_bootstrap_ci(date_index, stat_fn, b=50, seed=42, alpha=.05):
        key = pd.PeriodIndex(pd.DatetimeIndex(date_index), freq="M")
        blocks = [np.where(key == m)[0] for m in key.unique()]
        rng2 = np.random.default_rng(seed)
        draws = [stat_fn(np.concatenate([blocks[i] for i in rng2.integers(0, len(blocks), len(blocks))])) for _ in range(b)]
        return tuple(np.percentile(draws, [2.5, 97.5]))

    return {"feat": feat, "sam": sam, "sam_raw_close": sam["close"], "feature_cols": feature_cols,
            "har_sigma_forecast": fu.har_sigma_forecast, "make_price_model": make_price_model,
            "block_bootstrap_ci": block_bootstrap_ci, "calibrate_price_forecast": fu.calibrate_price_forecast,
            "BAND_COVERAGE": .8, "price_forecast_stats": {}, "prediction_date": idx[-1] + pd.Timedelta(days=1),
            "last_samsung_date": idx[-1], "DATA_SNAPSHOT_HASH": "synthetic", "VERSIONS": {}, "MACRO_ACTIVE": False,
            "QUICK_MODE": True, "BOOTSTRAP_B": 50, "VOL_MODEL": "simple"}


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.ns = synthetic_namespace()

    def test_target_is_next_h_sessions_from_previous_close(self):
        reg, cols = mh.price_design(self.ns, 5)
        sam = self.ns["sam"]
        d = reg.index[100]
        p = sam.index.get_loc(d)
        expected = sam["close"].iloc[p + 4] / sam["close"].iloc[p - 1] - 1
        self.assertAlmostEqual(reg.loc[d, "future_return"], expected, places=12)
        self.assertEqual(cols, self.ns["feature_cols"])

    def test_future_prices_do_not_change_past_features_or_labels(self):
        reg_before, _ = mh.price_design(self.ns, 20)
        ns2 = synthetic_namespace()
        cut = ns2["sam"].index[-40]
        ns2["sam"].loc[cut:, "close"] *= 1.3          # 마지막 40일 가격을 바꾼다
        ns2["sam_raw_close"] = ns2["sam"]["close"]
        reg_after, _ = mh.price_design(ns2, 20)
        safe = reg_before.index[reg_before.index < cut - pd.Timedelta(days=45)]   # 만기가 cut 앞인 행
        pd.testing.assert_frame_equal(reg_before.loc[safe, ["sam_ret_1", "future_return"]],
                                      reg_after.loc[safe, ["sam_ret_1", "future_return"]])

    def test_har_warmup_rows_are_dropped_like_the_notebook(self):
        reg, _ = mh.price_design(self.ns, 5)
        self.assertLess(len(reg), len(self.ns["sam"]) - 400)


class PurgeTests(unittest.TestCase):
    def setUp(self):
        self.ns = synthetic_namespace()
        self.reg, _ = mh.price_design(self.ns, 20)

    def test_notebook_split_gap_passes_the_date_check(self):
        from sklearn.model_selection import TimeSeriesSplit
        splits = list(TimeSeriesSplit(n_splits=3, gap=19).split(self.reg))
        self.assertEqual(mh.purge_check(self.reg.index, self.ns["sam"].index, 20, splits), [])

    def test_missing_gap_is_reported_with_dates(self):
        from sklearn.model_selection import TimeSeriesSplit
        splits = list(TimeSeriesSplit(n_splits=3, gap=0).split(self.reg))
        bad = mh.purge_check(self.reg.index, self.ns["sam"].index, 20, splits)
        self.assertEqual(len(bad), 3)
        self.assertGreater(bad[0]["train_label_matures"], bad[0]["test_origin"])

    def test_calibration_stages_are_purged_for_both_horizons(self):
        for h in (5, 20):
            reg, _ = mh.price_design(self.ns, h)
            n = len(reg)
            cal, gate, ev = mh.calibration_splits(n, h)
            self.assertEqual(mh.purge_check(reg.index, self.ns["sam"].index, h, [(cal, gate), (gate, ev), (cal, ev)]), [])

    def test_rows_with_unmatured_labels_are_rejected(self):
        with self.assertRaises(ValueError):
            mh.purge_check(self.ns["sam"].index, self.ns["sam"].index, 5, [])   # 마지막 4행은 만기가 없다


class DiagnosticToolTests(unittest.TestCase):
    def test_interval_score_penalises_misses_and_width(self):
        y = np.array([0., 0., 0.])
        inside = mh.interval_score(y, np.array([-1, -1, -1.]), np.array([1, 1, 1.]))
        self.assertTrue(np.allclose(inside, 2.))
        miss = mh.interval_score(np.array([2.]), np.array([-1.]), np.array([1.]))
        self.assertAlmostEqual(float(miss[0]), 2 + (2 / .2) * 1)

    def test_contiguous_block_ci_brackets_the_mean_and_is_reproducible(self):
        x = np.random.default_rng(1).normal(.5, 1, 400)
        lo, hi = mh.contiguous_block_ci(len(x), lambda i: float(x[i].mean()), 20, b=300)
        self.assertLess(lo, x.mean()); self.assertGreater(hi, x.mean())
        self.assertEqual((lo, hi), mh.contiguous_block_ci(len(x), lambda i: float(x[i].mean()), 20, b=300))
        self.assertEqual(mh.contiguous_block_ci(0, lambda i: 0., 20), (np.nan, np.nan))

    def test_nonoverlap_covers_every_offset(self):
        ev = np.arange(100, 160)
        y, raw, shrunk = np.zeros(200), np.zeros(200), np.zeros(200)
        rows = mh.nonoverlap_table(ev, y, raw, shrunk, 5)
        self.assertEqual([r["offset"] for r in rows], [0, 1, 2, 3, 4])
        self.assertEqual(sum(r["n"] for r in rows), 60)

    def test_regimes_are_cut_on_the_calibration_window(self):
        sigma_cal = np.array([1., 2., 3., 4., 5., 6.])
        sigma_eval = np.array([1.5, 3.5, 5.5])
        rows = mh.regime_table(sigma_cal, sigma_eval, np.zeros(3), np.zeros(3), np.zeros(3), np.ones(3, dtype=bool))
        self.assertEqual([r["n"] for r in rows], [1, 1, 1])

    def test_failure_type_marks_hypotheses(self):
        stats = {"beats_baseline": False, "band_coverage_realized": .8}
        self.assertIn("신호 부족", mh.failure_type(stats, (-.001, .001), [], {"flag": False})[0])
        self.assertIn("과도한 축소 의심", mh.failure_type(stats, (-.002, -.001), [], {"flag": False})[0])
        kinds = mh.failure_type({"beats_baseline": True, "band_coverage_realized": .6}, (np.nan, np.nan), [{"x": 1}], {"flag": False})
        self.assertIn("데이터·채점 결함", kinds[0]); self.assertTrue(any("구간 오차" in k for k in kinds))

    def test_boundary_anomalies_flag_a_split_like_jump(self):
        ns = synthetic_namespace()
        clean = mh.boundary_anomalies(ns["sam"], 5, np.zeros(10))
        self.assertFalse(clean["flag"])
        sam = ns["sam"].copy(); sam.loc[sam.index[300]:, "close"] /= 50
        self.assertTrue(mh.boundary_anomalies(sam, 5, np.zeros(10))["flag"])


class FakeNotebook:
    def __init__(self):
        self.calls = 0

    def __call__(self, storage, targets=None, quick=False, use_cache=False, no_macro=False):
        self.calls += 1
        return {targets: synthetic_namespace()}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Path(self._tmp.name) / "runs"
        self.results = Path(self._tmp.name) / "results"
        cache = self.storage / "samsung" / "data_cache"
        cache.mkdir(parents=True)
        (cache / "target.parquet").write_bytes(b"snapshot-v1")
        self._publish = os.environ.get("PREDICT_STOCK_PUBLISH")

    def tearDown(self):
        if self._publish is None:
            os.environ.pop("PREDICT_STOCK_PUBLISH", None)
        else:
            os.environ["PREDICT_STOCK_PUBLISH"] = self._publish

    def test_quick_run_writes_the_contract_files_and_never_publishes(self):
        os.environ["PREDICT_STOCK_PUBLISH"] = "true"
        fake = FakeNotebook()
        state = mh.execute("M00", "samsung", "quick", self.storage, self.results, False, run_notebook_fn=fake)
        self.assertEqual(os.environ["PREDICT_STOCK_PUBLISH"], "false")
        for name in ("manifest.json", "metrics.csv", "comparisons.csv", "checkpoint.json",
                     "summary_samsung_h5.json", "summary_samsung_h20.json"):
            self.assertTrue((state.run_dir / name).is_file(), name)
        self.assertEqual(state.completed(), ["samsung:h5", "samsung:h20"])
        metrics = pd.read_csv(state.run_dir / "metrics.csv")
        self.assertEqual(sorted(metrics["horizon"].unique()), [5, 20])
        self.assertIn("hold_current", set(metrics["candidate"]))
        self.assertTrue((self.storage / "samsung" / "M00_h5_quick_oof.csv").is_file())

    def test_resume_skips_completed_units_and_new_data_starts_a_new_run(self):
        fake = FakeNotebook()
        first = mh.execute("M00", "samsung", "quick", self.storage, self.results, False, run_notebook_fn=fake)
        again = mh.execute("M00", "samsung", "quick", self.storage, self.results, True, run_notebook_fn=fake)
        self.assertEqual(fake.calls, 1, "재개가 노트북을 다시 돌렸다")
        self.assertEqual(first.run_dir, again.run_dir)
        (self.storage / "samsung" / "data_cache" / "target.parquet").write_bytes(b"snapshot-v2")
        third = mh.execute("M00", "samsung", "quick", self.storage, self.results, True, run_notebook_fn=fake)
        self.assertNotEqual(first.run_dir, third.run_dir)
        self.assertEqual(fake.calls, 2)

    def test_unknown_task_and_missing_snapshot_are_refused(self):
        with self.assertRaises(SystemExit):
            mh.execute("M99", "samsung", "quick", self.storage, self.results, False, run_notebook_fn=FakeNotebook())
        with self.assertRaises(SystemExit):
            mh.execute("M00", "sk_hynix", "quick", self.storage, self.results, False, run_notebook_fn=FakeNotebook())


if __name__ == "__main__":
    unittest.main()
