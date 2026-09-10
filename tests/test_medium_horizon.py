# -*- coding: utf-8 -*-
"""중기(5·20거래일) 실험 러너의 계약: 설계 행렬·누수 검사·평가 계약·고정 입력 로더·재개.

노트북을 돌리지 않는다. 노트북 네임스페이스와 같은 모양의 작은 합성 자료로 동작·누수만 검사한다
(합성 자료의 성능은 어떤 주장에도 쓰지 않는다).
"""
import hashlib
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


def synthetic_namespace(n=900, seed=0, holidays=()):
    """노트북이 남기는 이름 중 러너가 읽는 것만 흉내 낸다."""
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex(pd.bdate_range("2019-01-01", periods=n + len(holidays)))
    idx = idx[~idx.isin(pd.DatetimeIndex(list(holidays)))][:n]
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
    return {"feat": feat, "sam": sam, "sam_raw_close": sam["close"], "feature_cols": feature_cols,
            "live_row": feat.iloc[[-1]], "prediction_date": idx[-1] + pd.Timedelta(days=1),
            "last_samsung_date": idx[-1], "DATA_SNAPSHOT_HASH": "synthetic", "VERSIONS": {}, "MACRO_ACTIVE": False,
            "QUICK_MODE": True, "BOOTSTRAP_B": 50, "VOL_MODEL": "simple", "BAND_COVERAGE": .8,
            "price_forecast_stats": {}}


def synthetic_inputs(**kw):
    inputs = mh.extract_inputs(synthetic_namespace(**kw))
    inputs["from_cache"] = False
    return inputs


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.inputs = synthetic_inputs()

    def test_target_is_next_h_sessions_from_previous_close(self):
        reg, cols = mh.price_design(self.inputs, 5)
        sam = self.inputs["sam"]
        d = reg.index[100]
        p = sam.index.get_loc(d)
        expected = sam["close"].iloc[p + 4] / sam["close"].iloc[p - 1] - 1
        self.assertAlmostEqual(reg.loc[d, "future_return"], expected, places=12)
        self.assertEqual(cols, self.inputs["feature_cols"])

    def test_future_prices_do_not_change_past_features_or_labels(self):
        reg_before, _ = mh.price_design(self.inputs, 20)
        other = synthetic_inputs()
        cut = other["sam"].index[-40]
        other["sam"].loc[cut:, "close"] *= 1.3          # 마지막 40일 가격을 바꾼다
        other["sam_raw_close"] = other["sam"]["close"]
        reg_after, _ = mh.price_design(other, 20)
        safe = reg_before.index[reg_before.index < cut - pd.Timedelta(days=45)]   # 만기가 cut 앞인 행
        pd.testing.assert_frame_equal(reg_before.loc[safe, ["sam_ret_1", "future_return"]],
                                      reg_after.loc[safe, ["sam_ret_1", "future_return"]])

    def test_har_warmup_rows_are_dropped_like_the_notebook(self):
        reg, _ = mh.price_design(self.inputs, 5)
        self.assertLess(len(reg), len(self.inputs["sam"]) - 400)

    def test_helper_functions_match_the_inline_notebook_recipe(self):
        """forecast_utils 로 옮긴 함수가 노트북에 있던 계산과 같은 값을 낸다(고정 입력 동등성)."""
        from sklearn.base import clone
        from sklearn.model_selection import TimeSeriesSplit
        ns = synthetic_namespace()
        feat, sam, cols, raw = ns["feat"], ns["sam"], ns["feature_cols"], ns["sam_raw_close"]
        h = 5
        future_return = raw.shift(-(h - 1)) / raw.shift(1) - 1
        har_series, _ = fu.har_sigma_forecast(raw.pct_change(), h)
        reg = feat.loc[sam.index, cols].copy()
        reg["future_return"] = future_return.reindex(reg.index)
        reg["sigma_simple"] = feat.loc[sam.index, "sam_vol_20"] * np.sqrt(h)
        reg["sigma_har"] = har_series.reindex(reg.index)
        reg = reg.replace([np.inf, -np.inf], np.nan).dropna()
        X, y, s = reg[cols].to_numpy(np.float32), reg["future_return"].to_numpy(float), reg["sigma_simple"].to_numpy(float)
        z = y / np.maximum(s, 1e-6)
        oof = np.full(len(z), np.nan)
        for tr, va in TimeSeriesSplit(n_splits=3, gap=h - 1).split(X):
            oof[va] = clone(fu.make_price_model()).fit(X[tr], z[tr]).predict(X[va]) * s[va]
        reg2, _ = fu.price_design_frame(feat, sam.index, cols, raw, feat["sam_vol_20"], h)
        pd.testing.assert_frame_equal(reg, reg2)
        oof2, folds = fu.price_oof_predictions(X, y, s, h, fu.make_price_model(), 3)
        np.testing.assert_allclose(oof, oof2, equal_nan=True)
        self.assertEqual(len(folds), 3)

    def test_notebook_calls_the_shared_helpers(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        code = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
        self.assertIn("price_design_frame(feat, sam.index, feature_cols, sam_raw_close", code)
        self.assertIn("price_oof_predictions(X_reg, y_reg, sigma_h, horizon, template, n_splits)", code)
        self.assertEqual(code.count("def make_price_model("), 1, "노트북 안에 정의가 둘이면 어느 것이 도는지 알 수 없다")
        self.assertIn("def price_design_frame(", code)     # 헬퍼 셀에 동기화됨


class PurgeTests(unittest.TestCase):
    def setUp(self):
        self.inputs = synthetic_inputs()
        self.reg, _ = mh.price_design(self.inputs, 20)

    def test_notebook_split_gap_passes_the_date_check(self):
        from sklearn.model_selection import TimeSeriesSplit
        splits = list(TimeSeriesSplit(n_splits=3, gap=19).split(self.reg))
        self.assertEqual(mh.purge_check(self.reg.index, self.inputs["sam"].index, 20, splits), [])

    def test_missing_gap_is_reported_with_dates(self):
        from sklearn.model_selection import TimeSeriesSplit
        splits = list(TimeSeriesSplit(n_splits=3, gap=0).split(self.reg))
        bad = mh.purge_check(self.reg.index, self.inputs["sam"].index, 20, splits)
        self.assertEqual(len(bad), 3)
        self.assertGreater(bad[0]["train_label_matures"], bad[0]["test_origin"])

    def test_calibration_stages_are_purged_for_both_horizons(self):
        for h in (5, 20):
            reg, _ = mh.price_design(self.inputs, h)
            cal, gate, ev = mh.calibration_splits(len(reg), h)
            self.assertEqual(mh.purge_check(reg.index, self.inputs["sam"].index, h, [(cal, gate), (gate, ev), (cal, ev)]), [])

    def test_rows_with_unmatured_labels_are_rejected(self):
        with self.assertRaises(ValueError):
            mh.purge_check(self.inputs["sam"].index, self.inputs["sam"].index, 5, [])   # 마지막 4행은 만기가 없다

    def test_maturity_counts_sessions_not_calendar_days(self):
        """휴장일을 건너뛴 봉 위치로 만기를 잡는다. 달력 일수로 대체하면 만기가 앞당겨진다."""
        holidays = ("2022-03-02", "2022-03-03", "2022-03-04")
        inputs = synthetic_inputs(holidays=holidays)
        sam = inputs["sam"]
        d = pd.Timestamp("2022-02-25")
        reg, _ = mh.price_design(inputs, 5)
        p = sam.index.get_loc(d)
        pos, mature = mh.maturity_positions(reg.index, sam.index, 5)
        i = reg.index.get_loc(d)
        self.assertEqual(sam.index[mature[i]], sam.index[p + 4])
        self.assertGreater(sam.index[mature[i]], d + pd.Timedelta(days=7))     # 휴장 3일만큼 뒤로 밀린다
        self.assertAlmostEqual(reg.loc[d, "future_return"], sam["close"].iloc[p + 4] / sam["close"].iloc[p - 1] - 1)

    def test_calibration_and_gate_ignore_the_evaluation_window(self):
        """보정 기울기·구간 폭·발행 판정은 평가 구간 정답을 보지 않는다."""
        rng = np.random.default_rng(3)
        n = 600
        dates = pd.DatetimeIndex(pd.bdate_range("2021-01-01", periods=n))
        y = rng.normal(0, .02, n)
        pred = .3 * y + rng.normal(0, .02, n)
        sigma = np.full(n, .02)
        ci = lambda d, fn: mh.month_block_ci(d, fn, b=30)       # noqa: E731
        a = fu.calibrate_price_forecast(y, pred, sigma, dates, 5, ci)
        y2 = y.copy()
        y2[n * 3 // 4:] += .5                                   # 평가 구간 정답만 바꾼다
        b = fu.calibrate_price_forecast(y2, pred, sigma, dates, 5, ci)
        for key in ("oof_slope", "band_q", "beats_baseline", "selection_mae_diff_lo", "selection_mae_diff_hi"):
            self.assertEqual(a[key], b[key], key)
        self.assertNotEqual(a["zero_baseline_mae"], b["zero_baseline_mae"])


class EvaluationContractTests(unittest.TestCase):
    def setUp(self):
        self.inputs = synthetic_inputs(n=1800)         # 2019-01 ~ 2025-11
        self.reg, _ = mh.price_design(self.inputs, 20)
        self.folds, self.lock_start = mh.evaluation_folds(self.reg.index, self.inputs["sam"].index, 20)

    def test_dev_folds_are_six_months_from_2021_and_lock_is_the_last_year(self):
        names = [f["name"] for f in self.folds]
        self.assertEqual(names[-1], "lock")
        self.assertEqual(self.folds[0]["test_start"], "2021-01-01")
        self.assertEqual(self.folds[0]["test_end"], "2021-06-30")
        last = self.reg.index[-1]
        self.assertEqual(self.lock_start, (last - pd.DateOffset(months=12) + pd.Timedelta(days=1)).normalize())
        dev = [f for f in self.folds if f["name"].startswith("dev")]
        self.assertTrue(all(pd.Timestamp(f["test_end"]) < self.lock_start for f in dev), "개발 폴드가 잠금 구간을 침범")
        self.assertEqual(self.folds[-1]["test_start"], str(self.lock_start.date()))

    def test_every_fold_and_inner_block_is_purged_by_dates(self):
        splits = [(f["train"], f["test"]) for f in self.folds if not f.get("excluded")]
        inner = [(i["train"], i["test"]) for f in self.folds for i in f.get("inner", []) if not i.get("excluded")]
        self.assertTrue(inner)
        self.assertEqual(mh.purge_check(self.reg.index, self.inputs["sam"].index, 20, splits + inner), [])
        f = next(f for f in self.folds if not f.get("excluded"))
        train_dates = self.reg.index[f["train"]]
        self.assertLess(train_dates.max(), pd.Timestamp(f["test_start"]) - pd.Timedelta(days=25), "20일 만기가 purge 되지 않았다")

    def test_inner_blocks_precede_their_outer_fold(self):
        f = self.folds[1]
        for i in f["inner"]:
            self.assertLessEqual(pd.Timestamp(i["test_end"]), pd.Timestamp(f["test_start"]))
            if not i.get("excluded"):
                self.assertLess(self.reg.index[i["train"]].max(), pd.Timestamp(i["test_start"]))

    def test_short_tail_fold_is_excluded_but_lock_is_kept(self):
        reg, _ = mh.price_design(self.inputs, 5)
        folds, _ = mh.evaluation_folds(reg.index, self.inputs["sam"].index, 5)
        dev = [f for f in folds if f["name"].startswith("dev")]
        short = [f for f in dev if f["test_rows"] < mh.MIN_TEST_ROWS]
        self.assertTrue(all(f.get("excluded") for f in short))
        self.assertTrue(all(f["test_rows"] >= mh.MIN_TEST_ROWS for f in dev if not f.get("excluded")))
        self.assertFalse(folds[-1].get("excluded"))

    def test_small_training_sets_are_excluded_with_a_reason(self):
        inputs = synthetic_inputs(n=700)              # 2019-01 ~ 2021-09 → 2021-01 폴드의 학습 행이 500 미만
        reg, _ = mh.price_design(inputs, 5)
        folds, _ = mh.evaluation_folds(reg.index, inputs["sam"].index, 5, lock_months=3)
        self.assertTrue(any("학습 행" in f.get("excluded", "") for f in folds))
        self.assertTrue(all(f["train_rows"] >= 500 for f in folds if not f.get("excluded")))


class DiagnosticToolTests(unittest.TestCase):
    def test_interval_score_penalises_misses_and_width(self):
        inside = mh.interval_score(np.zeros(3), np.array([-1, -1, -1.]), np.array([1, 1, 1.]))
        self.assertTrue(np.allclose(inside, 2.))
        miss = mh.interval_score(np.array([2.]), np.array([-1.]), np.array([1.]))
        self.assertAlmostEqual(float(miss[0]), 2 + (2 / .2) * 1)

    def test_contiguous_block_ci_brackets_the_mean_and_is_reproducible(self):
        x = np.random.default_rng(1).normal(.5, 1, 400)
        lo, hi = mh.contiguous_block_ci(len(x), lambda i: float(x[i].mean()), 20, b=300)
        self.assertLess(lo, x.mean()); self.assertGreater(hi, x.mean())
        self.assertEqual((lo, hi), mh.contiguous_block_ci(len(x), lambda i: float(x[i].mean()), 20, b=300))
        self.assertEqual(mh.contiguous_block_ci(0, lambda i: 0., 20), (np.nan, np.nan))

    def test_month_block_ci_matches_the_notebook_recipe(self):
        dates = pd.DatetimeIndex(pd.bdate_range("2022-01-01", periods=250))
        x = np.random.default_rng(2).normal(0, 1, 250)
        key = pd.PeriodIndex(dates, freq="M")
        blocks = [np.where(key == m)[0] for m in key.unique()]
        rng = np.random.default_rng(42)
        draws = [float(x[np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))])].mean()) for _ in range(100)]
        expected = tuple(float(v) for v in np.percentile(draws, [2.5, 97.5]))
        self.assertEqual(mh.month_block_ci(dates, lambda i: float(x[i].mean()), b=100), expected)

    def test_nonoverlap_covers_every_offset(self):
        rows = mh.nonoverlap_table(np.arange(100, 160), np.zeros(200), np.zeros(200), np.zeros(200), 5)
        self.assertEqual([r["offset"] for r in rows], [0, 1, 2, 3, 4])
        self.assertEqual(sum(r["n"] for r in rows), 60)

    def test_regimes_are_cut_on_the_calibration_window(self):
        rows = mh.regime_table(np.array([1., 2., 3., 4., 5., 6.]), np.array([1.5, 3.5, 5.5]),
                               np.zeros(3), np.zeros(3), np.zeros(3), np.ones(3, dtype=bool))
        self.assertEqual([r["n"] for r in rows], [1, 1, 1])

    def test_failure_type_marks_hypotheses(self):
        stats = {"beats_baseline": False, "band_coverage_realized": .8}
        self.assertIn("신호 부족", mh.failure_type(stats, (-.001, .001), [], {"flag": False})[0])
        self.assertIn("과도한 축소 의심", mh.failure_type(stats, (-.002, -.001), [], {"flag": False})[0])
        kinds = mh.failure_type({"beats_baseline": True, "band_coverage_realized": .6}, (np.nan, np.nan), [{"x": 1}], {"flag": False})
        self.assertIn("데이터·채점 결함", kinds[0]); self.assertTrue(any("구간 오차" in k for k in kinds))

    def test_boundary_anomalies_flag_a_split_like_jump(self):
        inputs = synthetic_inputs()
        self.assertFalse(mh.boundary_anomalies(inputs["sam"], 5, np.zeros(10))["flag"])
        sam = inputs["sam"].copy(); sam.loc[sam.index[300]:, "close"] /= 50
        self.assertTrue(mh.boundary_anomalies(sam, 5, np.zeros(10))["flag"])


class FakeNotebook:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def __call__(self, storage, targets=None, quick=False, use_cache=False, no_macro=False):
        self.calls += 1
        if self.fail:
            raise RuntimeError("시세 조회 실패(가짜)")
        return {targets: synthetic_namespace()}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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

    def run_task(self, task, fake, resume=False, target="samsung"):
        return mh.execute(task, target, "quick", self.storage, self.results, resume, run_notebook_fn=fake)

    def test_quick_run_writes_the_contract_files_and_never_publishes(self):
        os.environ["PREDICT_STOCK_PUBLISH"] = "true"
        state = self.run_task("M00", FakeNotebook())
        self.assertEqual(os.environ["PREDICT_STOCK_PUBLISH"], "false")
        for name in ("manifest.json", "metrics.csv", "comparisons.csv", "checkpoint.json",
                     "summary_samsung_h5.json", "summary_samsung_h20.json"):
            self.assertTrue((state.run_dir / name).is_file(), name)
        self.assertEqual(state.completed(), ["samsung:h5", "samsung:h20"])
        metrics = pd.read_csv(state.run_dir / "metrics.csv")
        self.assertEqual(sorted(metrics["horizon"].unique()), [5, 20])
        self.assertIn("hold_current", set(metrics["candidate"]))
        self.assertTrue((self.storage / "samsung" / "M00_h5_quick_oof.csv").is_file())

    def test_fixed_inputs_are_loaded_once_across_tasks(self):
        fake = FakeNotebook()
        self.run_task("M00", fake)
        self.assertEqual(fake.calls, 1)
        self.assertTrue(list((self.storage / "samsung").glob("inputs_quick_*.pkl")))
        state = self.run_task("M01", fake)
        self.assertEqual(fake.calls, 1, "M01이 고정 입력 대신 노트북을 다시 돌렸다")
        self.assertTrue(state.manifest["notebook"]["inputs_from_cache"])
        rows = pd.read_csv(state.run_dir / "metrics.csv")
        self.assertIn("lock", set(rows["fold"]))
        self.assertTrue((rows["fold"].str.startswith("dev")).any())

    def test_new_snapshot_invalidates_the_input_cache(self):
        fake = FakeNotebook()
        first = self.run_task("M00", fake)
        (self.storage / "samsung" / "data_cache" / "target.parquet").write_bytes(b"snapshot-v2")
        third = self.run_task("M00", fake, resume=True)
        self.assertNotEqual(first.run_dir, third.run_dir)
        self.assertEqual(fake.calls, 2)

    def test_resume_skips_completed_units_but_recomputes_tampered_artifacts(self):
        fake = FakeNotebook()
        first = self.run_task("M00", fake)
        again = self.run_task("M00", fake, resume=True)
        self.assertEqual(first.run_dir, again.run_dir)
        self.assertEqual(fake.calls, 1, "재개가 노트북을 다시 돌렸다")
        oof = self.storage / "samsung" / "M00_h20_quick_oof.csv"
        oof.write_text("tampered\n", encoding="utf-8")
        before = _sha(first.run_dir / "summary_samsung_h20.json")
        self.run_task("M00", fake, resume=True)
        self.assertNotEqual(_sha(oof), hashlib.sha256(b"tampered\n").hexdigest(), "훼손된 OOF 를 다시 계산하지 않았다")
        self.assertEqual(fake.calls, 1, "재계산에 필요한 입력은 pkl 캐시에서 읽는다")
        self.assertNotEqual(_sha(first.run_dir / "summary_samsung_h20.json"), before, "요약이 다시 쓰이지 않았다")

    def test_interrupted_run_resumed_equals_a_single_run(self):
        fake = FakeNotebook()
        original = mh.analyse_horizon
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("중단(가짜)")
            return original(*args, **kwargs)
        mh.analyse_horizon = flaky
        try:
            with self.assertRaises(RuntimeError):
                self.run_task("M00", fake)
            resumed = self.run_task("M00", fake, resume=True)
        finally:
            mh.analyse_horizon = original
        clean_results = Path(self._tmp.name) / "clean"
        clean = mh.execute("M00", "samsung", "quick", self.storage, clean_results, False, run_notebook_fn=fake)
        a = pd.read_csv(resumed.run_dir / "metrics.csv")
        b = pd.read_csv(clean.run_dir / "metrics.csv")
        pd.testing.assert_frame_equal(a, b)
        self.assertEqual(resumed.manifest["status"], "completed")

    def test_failed_run_records_the_failure_and_leaves_the_ledger_alone(self):
        ledger = ROOT / "forecast_history" / "samsung" / "forecast_log.csv"
        before = _sha(ledger) if ledger.is_file() else None
        with self.assertRaises(RuntimeError):
            self.run_task("M00", FakeNotebook(fail=True))
        after = _sha(ledger) if ledger.is_file() else None
        self.assertEqual(before, after, "실험 실행이 공식 원장을 바꿨다")
        manifests = list((self.results / "M00").glob("*/manifest.json"))
        self.assertEqual(len(manifests), 1)
        import json
        self.assertEqual(json.loads(manifests[0].read_text(encoding="utf-8"))["status"], "failed")

    def test_unknown_task_and_missing_snapshot_are_refused(self):
        with self.assertRaises(SystemExit):
            self.run_task("M99", FakeNotebook())
        with self.assertRaises(SystemExit):
            self.run_task("M00", FakeNotebook(), target="sk_hynix")


if __name__ == "__main__":
    unittest.main()
