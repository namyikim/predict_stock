# -*- coding: utf-8 -*-
"""P16 — 시가 확정 후(09:37) 종가 방향 재예측 (guides/model-improvement-plan.md P16).

이 실험의 유일한 위험은 **누수**다. t0900 은 "모델이 더 낫다"가 아니라 "정보 마감 시각이 늦다"를
재는 것이므로, 늦은 정보가 시가 **하나**로 제한되는지 기계로 고정해야 숫자가 의미를 갖는다.

계약:
  1. 행 d 의 당일 입력은 시가 하나뿐이다 — d일 종가·고가·저가·거래량을 바꿔도 행 d 의 그룹 G 는 그대로다.
  2. d일 시가를 바꾸면 그룹 G 여섯 열이 **모두** 바뀌고, 그룹 G 밖의 열은 하나도 바뀌지 않는다.
  3. d+1 이후 봉을 바꿔도 행 d 는 그대로다.
  4. 예측일 행은 그날 봉의 시가로 계산되고, 그날 봉이 아직 없으면 앞 값을 끌어오지 않고 결측이다.
  5. 시가가 없는 날(거래정지·유령봉)은 두 후보에서 **함께** 빠진다(쌍체 유지). 제외 건수는 기록한다.
  6. gap_rule 은 모델 없는 규칙이고 확률은 학습 구간에서만 만든다(외부 라벨 불가침).
  7. 러너는 합성 입력에서 계약 파일을 남긴다(성능은 어떤 주장에도 쓰지 않는다).
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
sys.path.insert(0, str(ROOT / "tests"))
import run_model_improvement as runner  # noqa: E402
import forecast_utils as fu  # noqa: E402
from test_overnight_intraday import _prediction_frame, _summarize, _paired_delta_ci  # noqa: E402


def _bars(n=400, seed=7, start="2019-01-01"):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, .012, n))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, .006, n))
    idx = pd.DatetimeIndex(pd.bdate_range(start, periods=n))
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.01,
                         "low": np.minimum(open_, close) * .99, "close": close,
                         "volume": rng.integers(1_000, 9_000, n).astype(float)}, index=idx)


def _band(bars, mult=.3):
    ret = bars["close"].pct_change()
    return (mult * ret.rolling(20).std()).shift(1)


class GroupGFeatureTests(unittest.TestCase):
    """계약 1~4 — 늦은 정보는 시가 하나뿐이다."""

    def setUp(self):
        self.bars = _bars()
        self.band = _band(self.bars)
        self.cal = self.bars.index
        self.g = runner.p16_gap_features(self.bars, self.band, self.cal)
        self.day = self.bars.index[300]

    def test_columns_and_definition(self):
        self.assertEqual(list(self.g.columns), list(runner.P16_GAP_COLUMNS))
        p = self.bars.index.get_loc(self.day)
        expected = self.bars["open"].iloc[p] / self.bars["close"].iloc[p - 1] - 1
        self.assertAlmostEqual(self.g.loc[self.day, "gap_0"], expected, places=12)
        self.assertAlmostEqual(self.g.loc[self.day, "gap_abs"], abs(expected), places=12)
        self.assertAlmostEqual(self.g.loc[self.day, "gap_over_band"],
                               expected / self.band.loc[self.day], places=12)

    def test_zscore_uses_only_gaps_through_the_previous_session(self):
        p = self.bars.index.get_loc(self.day)
        gap = self.bars["open"] / self.bars["close"].shift(1) - 1
        window = gap.iloc[p - runner.P16_GAP_Z_WINDOW:p]           # d−60 .. d−1, d 자체는 빠진다
        expected = (gap.iloc[p] - window.mean()) / window.std()
        self.assertAlmostEqual(self.g.loc[self.day, "gap_z60"], expected, places=12)

    def test_todays_close_high_low_volume_do_not_change_todays_group_g(self):
        for column in ("close", "high", "low", "volume"):
            bumped = self.bars.copy()
            bumped.loc[self.day:, column] *= 1.5                   # d 봉부터 끝까지 바꾼다
            after = runner.p16_gap_features(bumped, _band(bumped) if column == "close" else self.band, self.cal)
            pd.testing.assert_series_equal(self.g.loc[self.day], after.loc[self.day],
                                           check_names=False, obj=f"{column} 변경 후 행 d")

    def test_todays_open_changes_the_group_g_row_and_no_other_row(self):
        bumped = self.bars.copy()
        bumped.loc[self.day, "open"] *= 1.05
        after = runner.p16_gap_features(bumped, self.band, self.cal)
        for column in ("gap_0", "gap_over_band", "gap_z60", "gap_abs"):
            self.assertNotEqual(self.g.loc[self.day, column], after.loc[self.day, column], column)
        # 이전 날짜는 한 칸도 바뀌지 않는다 — 미래 시가가 과거 행으로 새지 않는다.
        past = self.cal[self.cal < self.day]
        pd.testing.assert_frame_equal(self.g.loc[past], after.loc[past])
        # 이후 날짜에서 바뀌는 것은 gap_z60 뿐이다. d+1 시점에는 gap_d 가 이미 **과거**이므로
        # 60세션 갭 분포에 들어가는 것이 맞다(누수가 아니라 정의다).
        future = self.cal[self.cal > self.day]
        pd.testing.assert_frame_equal(self.g.loc[future].drop(columns=["gap_z60"]),
                                      after.loc[future].drop(columns=["gap_z60"]))

    def test_the_band_buckets_follow_todays_open(self):
        flat = [d for d in self.bars.index[100:-1]
                if self.g.loc[d, "gap_up_band"] == 0 and self.g.loc[d, "gap_down_band"] == 0]
        day = flat[0]
        for factor, up, down in ((1.5, 1., 0.), (.5, 0., 1.)):
            bumped = self.bars.copy()
            bumped.loc[day, "open"] *= factor
            after = runner.p16_gap_features(bumped, self.band, self.cal)
            self.assertEqual(after.loc[day, "gap_up_band"], up)
            self.assertEqual(after.loc[day, "gap_down_band"], down)

    def test_future_bars_do_not_change_earlier_rows(self):
        cut = self.bars.index[320]
        bumped = self.bars.copy()
        bumped.loc[cut:, ["open", "close"]] *= 1.4
        after = runner.p16_gap_features(bumped, self.band, self.cal)
        safe = self.cal[self.cal < cut]
        pd.testing.assert_frame_equal(self.g.loc[safe], after.loc[safe])

    def test_prediction_date_row_uses_that_days_open_and_is_missing_without_a_bar(self):
        pred = self.bars.index[-1] + pd.offsets.BDay(1)
        calendar = self.bars.index.union(pd.DatetimeIndex([pred]))
        # 봉이 아직 없으면 결측이어야 한다(앞 값을 끌어오면 그날 갭을 지어내는 것이다).
        empty = runner.p16_gap_features(self.bars, self.band, calendar)
        self.assertTrue(empty.loc[pred].isna().all())
        # 09:37 실행처럼 그날 시가가 붙은 봉이 있으면 그 시가로 계산된다.
        with_open = self.bars.copy()
        with_open.loc[pred] = {"open": float(self.bars["close"].iloc[-1]) * 1.02, "high": np.nan,
                               "low": np.nan, "close": np.nan, "volume": np.nan}
        band = self.band.reindex(calendar).ffill()
        filled = runner.p16_gap_features(with_open, band, calendar)
        self.assertAlmostEqual(filled.loc[pred, "gap_0"], .02, places=12)

    def test_a_day_without_an_open_is_missing_not_filled(self):
        holed = self.bars.copy()
        holed.loc[self.day, "open"] = np.nan
        after = runner.p16_gap_features(holed, self.band, self.cal)
        self.assertTrue(after.loc[self.day].isna().all())
        nxt = self.cal[self.cal > self.day][0]
        self.assertTrue(np.isfinite(after.loc[nxt, "gap_0"]))       # 다음 날은 전일 종가만 쓰므로 멀쩡하다

    def test_band_buckets_are_signed_indicators(self):
        gap, band = self.g["gap_0"], self.band.reindex(self.cal)
        ok = gap.notna() & band.notna()
        pd.testing.assert_series_equal(self.g.loc[ok, "gap_up_band"], (gap[ok] > band[ok]).astype(float),
                                       check_names=False)
        pd.testing.assert_series_equal(self.g.loc[ok, "gap_down_band"], (gap[ok] < -band[ok]).astype(float),
                                       check_names=False)
        self.assertTrue(((self.g.loc[ok, "gap_up_band"] + self.g.loc[ok, "gap_down_band"]) <= 1).all())


class GapRuleTests(unittest.TestCase):
    """계약 6 — 모델 없는 기준. 확률은 학습 구간에서만 만든다."""

    def test_rule_classes_follow_the_band(self):
        gap = np.array([.05, -.05, .0, .01, np.nan])
        band = np.array([.02, .02, .02, .02, .02])
        out = runner.gap_rule_labels(gap, band)
        np.testing.assert_array_equal(out[:4], [2., 0., 1., 1.])
        self.assertTrue(np.isnan(out[4]))
        self.assertTrue(np.isnan(runner.gap_rule_labels([.05], [np.nan])[0]))

    def test_probabilities_are_valid_and_come_from_the_training_buckets(self):
        rng = np.random.default_rng(0)
        rule_train = rng.integers(0, 3, 600)
        y_train = np.where(rng.random(600) < .7, rule_train, rng.integers(0, 3, 600))
        rule_test = np.array([0, 1, 2, 2])
        probs, table = runner.gap_rule_probabilities(rule_train, y_train, rule_test)
        self.assertEqual(probs.shape, (4, 3))
        np.testing.assert_allclose(probs.sum(axis=1), 1.)
        self.assertTrue((probs > 0).all())
        for bucket in (0, 1, 2):
            self.assertEqual(int(np.argmax(table[str(bucket)])), bucket)   # 규칙이 맞는 쪽이 가장 크다
        np.testing.assert_allclose(probs[2], probs[3])                     # 같은 버킷은 같은 확률

    def test_outer_labels_cannot_change_the_probabilities(self):
        rng = np.random.default_rng(1)
        rule_train, y_train = rng.integers(0, 3, 400), rng.integers(0, 3, 400)
        rule_test = rng.integers(0, 3, 50)
        a, _ = runner.gap_rule_probabilities(rule_train, y_train, rule_test)
        b, _ = runner.gap_rule_probabilities(rule_train, y_train, rule_test)
        np.testing.assert_array_equal(a, b)      # 외부 라벨은 인자에 없다 — 구조적으로 못 본다

    def test_a_thin_bucket_falls_back_to_the_training_prior(self):
        rule_train = np.r_[np.ones(300, dtype=int), np.full(5, 2)]
        y_train = np.r_[np.ones(300, dtype=int), np.full(5, 2)]
        _, table = runner.gap_rule_probabilities(rule_train, y_train, np.array([2]))
        prior = np.bincount(y_train, minlength=3) + 1.
        np.testing.assert_allclose(table["2"], prior / prior.sum())


class FakePostOpenNotebook:
    """러너가 읽는 이름만 흉내 낸다. 시세 봉·밴드·특징·폴드·대표 모델 OOF 를 준다."""

    def __init__(self, n=1100, seed=5):
        rng = np.random.default_rng(seed)
        bars = _bars(n=n, seed=seed, start="2015-01-05")
        bars["adj_close"] = bars["close"]
        pred_date = bars.index[-1] + pd.offsets.BDay(1)
        calendar = bars.index.union(pd.DatetimeIndex([pred_date]))
        ret = bars["close"].pct_change()
        band = (.3 * ret.rolling(20).std()).shift(1)
        y = np.where(ret < -band, 0, np.where(ret > band, 2, 1)).astype(float)
        y[~(ret.notna() & band.notna()).to_numpy()] = np.nan
        feat = pd.DataFrame(index=calendar)
        for name in ("sox_ret_1", "nasdaq_ret_1", "usdkrw_level_z60", "kospi_ret_1", "sam_ret_1", "macro_lead"):
            feat[name] = rng.normal(0, 1, len(feat))
        feat["cal_dow"] = feat.index.dayofweek.astype(float)
        feat["band"] = band.reindex(calendar)
        self.feature_cols = [c for c in feat.columns if c != "band"]
        self.feat = feat
        self.market_idx = [i for i, c in enumerate(self.feature_cols) if not c.startswith("macro_")]
        keep = np.isfinite(y)
        self.dates = pd.DatetimeIndex(bars.index[keep])
        self.y = y[keep].astype(int)
        self.X = feat.loc[self.dates, self.feature_cols].to_numpy(dtype=np.float32)
        self.market_X = self.X[:, self.market_idx]
        self.raw = {"target": bars}
        folds, fold_no = [], 0
        for start in (pd.Timestamp("2017-07-01"), pd.Timestamp("2018-01-01")):
            end = start + pd.DateOffset(months=6)
            tr = np.flatnonzero((self.dates >= start - pd.DateOffset(years=5)) & (self.dates < start))
            te = np.flatnonzero((self.dates >= start) & (self.dates < end))
            folds.append({"fold": fold_no, "train_idx": tr, "test_idx": te})
            fold_no += 1
        self.folds = folds
        frames = []
        for f in folds:
            te = f["test_idx"]
            frames.append(_prediction_frame("No macro ensemble", self.dates[te], self.y[te],
                                            rng.dirichlet(np.ones(3) * 2, size=len(te)), f["fold"]))
            prior = np.bincount(self.y[f["train_idx"]], minlength=3) + 1.
            frames.append(_prediction_frame("Always flat", self.dates[te], self.y[te],
                                            np.tile(prior / prior.sum(), (len(te), 1)), f["fold"],
                                            y_pred=np.ones(len(te), dtype=int)))
        self.predictions = pd.concat(frames, ignore_index=True)

    def __call__(self, storage, targets=None, quick=False, use_cache=False, no_macro=False):
        ns = {"folds": self.folds, "dates": self.dates, "y": self.y, "SEED": 42, "SELECTION_METRIC": "log_loss",
              "HEADLINE_MODEL": "No macro ensemble", "predictions": self.predictions, "feat": self.feat,
              "feature_cols": self.feature_cols, "market_feature_idx": self.market_idx,
              "market_X": self.market_X, "raw": self.raw, "VOL_BAND_MULT": .3,
              "fit_direction_model": fu.fit_direction_model,
              "predict_direction_model": fu.predict_direction_model,
              "prediction_frame": _prediction_frame, "summarize_predictions": _summarize,
              "paired_delta_ci": _paired_delta_ci}
        return {targets: ns}


class PostOpenRunnerTests(unittest.TestCase):
    """계약 5·7 — 러너 종단. 성능 수치는 합성 입력이라 어떤 주장에도 쓰지 않는다."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.storage = Path(cls.tmp.name)
        cache = cls.storage / "samsung" / "data_cache"
        cache.mkdir(parents=True)
        (cache / "target.parquet").write_bytes(b"snapshot-v1")
        cls.fake = FakePostOpenNotebook()
        os.environ["PREDICT_STOCK_PUBLISH"] = "true"
        cls.state = runner.execute("P16", "samsung", "quick", cls.storage, resume=False, run_notebook_fn=cls.fake)
        cls.detail = runner.read_json(cls.state.run_dir / "p16_samsung.json")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_both_candidates_are_scored_on_the_same_dates(self):
        metrics = pd.read_csv(self.state.run_dir / "metrics.csv")
        for leg in runner.P16_LABEL_TARGETS:
            sub = metrics[metrics["target_mode"] == leg]
            self.assertIn(runner.P16_T0700, set(sub["model"]))
            self.assertIn(runner.P16_T0900, set(sub["model"]))
            self.assertEqual(sub["n"].nunique(), 1, f"{leg}: 모델마다 평가일이 다르면 쌍체 비교가 아니다")
            self.assertEqual(self.detail["by_label_target"][leg]["common_evaluation_days"],
                             int(sub["n"].iloc[0]))
        self.assertEqual(self.detail["n_t0900_features"],
                         self.detail["n_t0700_features"] + len(runner.P16_GAP_COLUMNS))

    def test_the_trivial_gap_rule_is_scored_only_on_the_primary_target(self):
        metrics = pd.read_csv(self.state.run_dir / "metrics.csv")
        primary = metrics[metrics["target_mode"] == "close_to_close"]
        self.assertIn(runner.P16_GAP_RULE, set(primary["model"]))
        self.assertNotIn(runner.P16_GAP_RULE, set(metrics[metrics["target_mode"] == "session"]["model"]))
        comparisons = pd.read_csv(self.state.run_dir / "comparisons.csv")
        self.assertIn(f"{runner.P16_T0900} − {runner.P16_GAP_RULE}", set(comparisons["comparison"]))

    def test_comparisons_cover_both_targets_with_leg_auc(self):
        comparisons = pd.read_csv(self.state.run_dir / "comparisons.csv")
        for leg in runner.P16_LABEL_TARGETS:
            main = comparisons[(comparisons["target_mode"] == leg)
                               & (comparisons["comparison"] == f"{runner.P16_T0900} − {runner.P16_T0700}")]
            self.assertEqual(set(main["metric"]),
                             {"log_loss", "balanced_accuracy", "accuracy", "auc_gap", "auc_session"})
            self.assertTrue((main["ci_low"] <= main["delta"]).all() and (main["delta"] <= main["ci_high"]).all())

    def test_dropped_rows_are_reported_and_shared_by_both_candidates(self):
        for leg in runner.P16_LABEL_TARGETS:
            info = self.detail["by_label_target"][leg]
            self.assertGreaterEqual(info["rows_missing_group_g"], 0)
            for fold in info["folds"]:
                # 같은 폴드에서 두 후보가 같은 학습·시험 행을 쓴다(제외는 한 번만 센다).
                self.assertGreaterEqual(fold["train_rows_dropped"], 0)
                self.assertGreaterEqual(fold["test_rows_dropped"], 0)
        self.assertIn("dates_without_open", self.detail)
        self.assertIn("dates_without_bar", self.detail)

    def test_selective_table_and_publishing_guard(self):
        selective = pd.read_csv(self.state.run_dir / "selective.csv")
        at_half = selective[(selective["threshold"].astype(str) == "0.5")
                            & (selective["target_mode"] == "close_to_close")]
        self.assertTrue({runner.P16_T0700, runner.P16_T0900, runner.P16_GAP_RULE} <= set(at_half["model"]))
        self.assertEqual(os.environ.get("PREDICT_STOCK_PUBLISH"), "false")
        self.assertEqual(self.state.completed(), ["samsung:post_open"])
        self.assertEqual(self.state.manifest["status"], "completed")
        self.assertEqual(self.state.manifest["config"]["candidate"], runner.P16_T0900)


if __name__ == "__main__":
    unittest.main()
