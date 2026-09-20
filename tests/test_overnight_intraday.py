# -*- coding: utf-8 -*-
"""R02 — 야간/장중 분해 (guides/research-candidates-plan.md R02, R02c 는 model-improvement-plan 로그).

계약:
  1. 그룹 D(누적 야간·장중, 5/20/60)는 행 d 에 d−1 세션까지만 들어간다. d 이후 봉을 바꿔도 행 d 는 그대로다.
  2. 야간·장중 성분의 정의는 Π(open_t/close_{t−1})−1, Π(close_t/open_t)−1 이고 차 열은 둘의 차다.
  3. h일 라벨의 분해(로그 야간 합 + 로그 장중 합)는 log(1+future_return) 과 1e-12 안에서 같다.
  4. 성분 라벨은 d+h−1 봉까지만 쓴다 — 그 뒤 봉을 바꿔도 행 d 의 성분은 변하지 않는다.
  5. 새 열은 기존 특징 이름과 겹치지 않는다.
  6. R02(중기)·R02c(방향) 러너는 합성 입력에서 계약 파일을 남긴다(성능은 어떤 주장에도 쓰지 않는다).
"""
import json
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
import run_medium_horizon as mh  # noqa: E402
import run_model_improvement as runner  # noqa: E402
import forecast_utils as fu  # noqa: E402
from test_medium_horizon import synthetic_namespace  # noqa: E402

EXISTING_FEATURES = ["sam_ret_1", "sam_ret_5", "sam_ret_20", "sam_vol_20", "sam_gap_1", "sam_range_1",
                     "kospi_ret_1", "kospi_ret_5", "kospi_vol_20", "sam_cum_60", "kospi_cum_20", "sam_rel_kospi_5"]


def _bars(n=400, seed=3, start="2020-01-01"):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, .012, n))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, .005, n))
    idx = pd.DatetimeIndex(pd.bdate_range(start, periods=n))
    return pd.DataFrame({"open": open_, "close": close}, index=idx)


def _calendar(bars):
    return bars.index.union(pd.DatetimeIndex([bars.index[-1] + pd.offsets.BDay(1)]))


class GroupDFeatureTests(unittest.TestCase):
    def setUp(self):
        self.bars = _bars()
        self.kospi = _bars(seed=4)
        self.cal = _calendar(self.bars)
        self.f = mh.group_d_features({"sam": self.bars, "kospi": self.kospi}, self.cal)

    def test_columns_and_shape(self):
        self.assertEqual(len(self.f.columns), 2 * 3 * 3)
        for p in ("sam", "kospi"):
            for k in (5, 20, 60):
                for kind in ("ovn", "intra", "ovn_minus_intra"):
                    self.assertIn(f"{p}_{kind}_{k}", self.f.columns)
        self.assertTrue(self.f.index.equals(self.cal))

    def test_overnight_is_the_product_of_open_over_previous_close_up_to_the_previous_session(self):
        d = self.bars.index[100]
        p = self.bars.index.get_loc(d)
        sessions = range(p - 5, p)                  # d−5 .. d−1 (d 자체는 아직 열리지 않았다)
        expected = np.prod([self.bars["open"].iloc[t] / self.bars["close"].iloc[t - 1] for t in sessions]) - 1
        self.assertAlmostEqual(self.f.loc[d, "sam_ovn_5"], expected, places=12)

    def test_intraday_and_difference_definitions(self):
        d = self.bars.index[100]
        p = self.bars.index.get_loc(d)
        expected = np.prod([self.bars["close"].iloc[t] / self.bars["open"].iloc[t] for t in range(p - 20, p)]) - 1
        self.assertAlmostEqual(self.f.loc[d, "sam_intra_20"], expected, places=12)
        self.assertAlmostEqual(self.f.loc[d, "sam_ovn_minus_intra_20"],
                               self.f.loc[d, "sam_ovn_20"] - self.f.loc[d, "sam_intra_20"], places=12)

    def test_future_bars_do_not_change_earlier_rows(self):
        cut = self.bars.index[250]
        bumped = self.bars.copy()
        bumped.loc[cut:, ["open", "close"]] *= 1.5          # cut 봉부터 크게 바꾼다
        after = mh.group_d_features({"sam": bumped, "kospi": self.kospi}, self.cal)
        safe = self.cal[self.cal <= cut]                     # 행 cut 은 cut−1 세션까지만 보므로 그대로여야 한다
        pd.testing.assert_frame_equal(self.f.loc[safe], after.loc[safe])
        nxt = self.cal[self.cal > cut][0]
        self.assertNotEqual(self.f.loc[nxt, "sam_ovn_5"], after.loc[nxt, "sam_ovn_5"])

    def test_prediction_date_row_uses_the_last_bar(self):
        last, pred = self.bars.index[-1], self.cal[-1]
        p = len(self.bars)
        expected = np.prod([self.bars["open"].iloc[t] / self.bars["close"].iloc[t - 1] for t in range(p - 5, p)]) - 1
        self.assertAlmostEqual(self.f.loc[pred, "sam_ovn_5"], expected, places=12)
        self.assertTrue(pd.isna(self.f.loc[self.bars.index[3], "sam_ovn_5"]))   # 워밍업 전은 결측(채우지 않는다)
        self.assertGreater(pred, last)

    def test_columns_do_not_collide_with_existing_features(self):
        self.assertFalse(set(self.f.columns) & set(EXISTING_FEATURES))

    def test_missing_asset_is_skipped(self):
        f = mh.group_d_features({"sam": self.bars, "kospi": None}, self.cal)
        self.assertFalse(any(c.startswith("kospi_") for c in f.columns))
        self.assertEqual(len(f.columns), 9)

    def test_kospi_gap_in_its_own_calendar_gives_a_missing_row_not_a_fabricated_one(self):
        kospi = self.kospi.drop(self.kospi.index[150])
        f = mh.group_d_features({"sam": self.bars, "kospi": kospi}, self.cal)
        nxt = self.cal[self.cal > self.kospi.index[150]][0]
        self.assertTrue(pd.isna(f.loc[nxt, "kospi_ovn_5"]))


class LegLabelTests(unittest.TestCase):
    def test_log_components_sum_to_the_label_within_1e_12(self):
        bars = _bars()
        for h in (5, 20):
            legs = mh.horizon_leg_labels(bars, h)
            close = bars["close"]
            future = close.shift(-(h - 1)) / close.shift(1) - 1        # forecast_utils.price_design_frame 과 같은 정의
            ok = legs.notna().all(axis=1) & future.notna()
            err = np.abs(np.log1p(future[ok]) - legs.loc[ok, "log_overnight_h"] - legs.loc[ok, "log_intraday_h"])
            self.assertLess(float(err.max()), 1e-12)
            self.assertGreater(int(ok.sum()), 300)

    def test_components_use_sessions_d_to_d_plus_h_minus_1_only(self):
        bars, h = _bars(), 5
        d = bars.index[100]
        p = bars.index.get_loc(d)
        before = mh.horizon_leg_labels(bars, h)
        bumped = bars.copy()
        bumped.iloc[p + h:, :] *= 1.3                                  # d+h 봉부터 바꾼다 → 행 d 는 그대로
        after = mh.horizon_leg_labels(bumped, h)
        self.assertEqual(before.loc[d, "log_overnight_h"], after.loc[d, "log_overnight_h"])
        self.assertEqual(before.loc[d, "log_intraday_h"], after.loc[d, "log_intraday_h"])
        bumped2 = bars.copy()
        bumped2.iloc[p + h - 1, bumped2.columns.get_loc("close")] *= 1.3     # d+h−1 종가는 라벨에 들어간다
        after2 = mh.horizon_leg_labels(bumped2, h)
        self.assertNotEqual(before.loc[d, "log_intraday_h"], after2.loc[d, "log_intraday_h"])
        self.assertEqual(before.loc[d, "log_overnight_h"], after2.loc[d, "log_overnight_h"])

    def test_overnight_component_is_the_sum_of_log_gaps(self):
        bars, h = _bars(), 5
        d = bars.index[100]
        p = bars.index.get_loc(d)
        legs = mh.horizon_leg_labels(bars, h)
        expected = sum(np.log(bars["open"].iloc[t] / bars["close"].iloc[t - 1]) for t in range(p, p + h))
        self.assertAlmostEqual(legs.loc[d, "log_overnight_h"], expected, places=12)


class MediumRunnerR02Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = Path(tempfile.mkdtemp())
        storage, results = tmp / "runs", tmp / "results"
        cache = storage / "samsung" / "data_cache"; cache.mkdir(parents=True)
        (cache / "target.parquet").write_bytes(b"snapshot-v1")
        ns = synthetic_namespace(n=2000)
        rng = np.random.default_rng(11)
        sam = ns["sam"]
        kospi_close = sam["close"] * (1 + rng.normal(0, .01, len(sam)))
        pd.DataFrame({"Open": kospi_close * (1 + rng.normal(0, .003, len(sam))), "Close": kospi_close,
                      "Adj Close": kospi_close}, index=sam.index).to_parquet(cache / "kospi.parquet")
        cls.state = mh.execute("R02", "samsung", "quick", storage, results, False,
                               run_notebook_fn=lambda *a, **k: {"samsung": ns})
        cls.summary = json.loads((cls.state.run_dir / "summary_samsung_h5.json").read_text(encoding="utf-8"))

    def test_contract_files_and_candidates(self):
        self.assertTrue((self.state.run_dir / "metrics.csv").is_file())
        rows = pd.read_csv(self.state.run_dir / "comparisons.csv")
        for label in ("full_plus_D - current_full", "decomposed - current_full",
                      "overnight_leg - hold_zero", "intraday_leg - hold_zero"):
            self.assertIn(label, set(rows["comparison"]))
        self.assertEqual(self.summary["purge_violations"], [])
        self.assertEqual(len(self.summary["group_d_columns"]), 18)
        self.assertTrue(self.summary["group_d_assets_available"]["kospi"])

    def test_identity_and_verdicts_are_recorded(self):
        self.assertLessEqual(self.summary["leg_identity_max_abs_error"], 1e-12)
        self.assertIn(self.summary["verdict_r02a"], ("후보가 유의하게 우위", "후보가 유의하게 열위", "동률(CI가 0 포함)"))
        self.assertIn("overnight", self.summary["legs"]); self.assertIn("intraday", self.summary["legs"])
        metrics = pd.read_csv(self.state.run_dir / "metrics.csv")
        legs = metrics[metrics["evaluation_stage"] == "dev_common_leg"]
        self.assertEqual(set(legs["candidate"]), {"overnight_leg", "intraday_leg"})

    def test_decomposed_prediction_is_the_sum_of_leg_predictions(self):
        oof = pd.read_csv(self.summary["oof_file"])
        self.assertIn("raw_decomposed", oof.columns)
        # 성분 라벨의 합은 라벨의 로그와 같다(OOF 파일에서도)
        err = np.abs(np.log1p(oof["y"]) - oof["log_overnight_h"] - oof["log_intraday_h"])
        self.assertLess(float(err.max()), 1e-12)


# ---------------------------------------------------------------------------
# R02c 러너 종단 — 노트북 대역은 forecast_utils 의 실제 방향 모델 함수와 최소 프레임 함수를 준다.
# ---------------------------------------------------------------------------
def _prediction_frame(model_name, row_dates, y_true, probs, fold_id=None, y_pred=None, extra=None):
    probs = np.asarray(probs, dtype=float)
    probs = np.clip(probs, 1e-7, 1 - 1e-7)
    probs = probs / probs.sum(axis=1, keepdims=True)
    return pd.DataFrame({"date": pd.to_datetime(row_dates), "y_true": np.asarray(y_true, dtype=int),
                         "y_pred": probs.argmax(axis=1) if y_pred is None else np.asarray(y_pred, dtype=int),
                         "p_down": probs[:, 0], "p_flat": probs[:, 1], "p_up": probs[:, 2],
                         "model": model_name, "fold": fold_id})


def _summarize(predictions, with_ci=False):
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, log_loss
    rows = []
    for name, g in predictions.groupby("model"):
        p = g[["p_down", "p_flat", "p_up"]].to_numpy()
        rows.append({"model": name, "n": len(g), "accuracy": accuracy_score(g["y_true"], g["y_pred"]),
                     "balanced_accuracy": balanced_accuracy_score(g["y_true"], g["y_pred"]),
                     "log_loss": log_loss(g["y_true"], p, labels=[0, 1, 2]),
                     "brier": float(np.mean(np.sum((p - np.eye(3)[g["y_true"]]) ** 2, axis=1)))})
    return pd.DataFrame(rows).set_index("model")


def _paired_delta_ci(predictions, a, b, metric="balanced_accuracy"):
    from sklearn.metrics import balanced_accuracy_score, log_loss
    fa = predictions[predictions["model"] == a].set_index("date").sort_index()
    fb = predictions[predictions["model"] == b].set_index("date").sort_index()
    common = fa.index.intersection(fb.index)
    fa, fb = fa.loc[common], fb.loc[common]

    def stat(f):
        if metric == "accuracy":
            return float(np.mean(f["y_true"] == f["y_pred"]))
        if metric == "balanced_accuracy":
            return balanced_accuracy_score(f["y_true"], f["y_pred"])
        return log_loss(f["y_true"], f[["p_down", "p_flat", "p_up"]].to_numpy(), labels=[0, 1, 2])
    delta = stat(fa) - stat(fb)
    return {"delta": delta, "lo": delta - .05, "hi": delta + .05, "n": len(common)}


class FakeDirectionNotebook:
    """방향 모델 러너가 읽는 이름만 흉내 낸다. 시세 봉(시가·종가)·특징·폴드·대표 모델 OOF 를 준다."""

    def __init__(self, n=900, seed=5):
        rng = np.random.default_rng(seed)
        bars = _bars(n=n, seed=seed, start="2015-01-05")
        bars["adj_close"] = bars["close"]
        kospi = _bars(n=n, seed=seed + 1, start="2015-01-05")
        pred_date = bars.index[-1] + pd.offsets.BDay(1)
        calendar = bars.index.union(pd.DatetimeIndex([pred_date]))
        ret = bars["close"].pct_change()
        band = ret.rolling(20).std().shift(1) * .3
        y = np.where(ret < -band, 0, np.where(ret > band, 2, 1)).astype(float)
        y[~(ret.notna() & band.notna()).to_numpy()] = np.nan
        feat = pd.DataFrame(index=calendar)
        for name in ("sox_ret_1", "nasdaq_ret_1", "usdkrw_level_z60", "kospi_ret_1", "sam_ret_1", "macro_lead"):
            feat[name] = rng.normal(0, 1, len(feat))
        feat["cal_dow"] = feat.index.dayofweek.astype(float)
        self.feat, self.feature_cols = feat, list(feat.columns)
        self.market_idx = [i for i, c in enumerate(self.feature_cols) if not c.startswith("macro_")]
        keep = np.isfinite(y)
        self.dates = pd.DatetimeIndex(bars.index[keep])
        self.y = y[keep].astype(int)
        self.X = feat.loc[self.dates, self.feature_cols].to_numpy(dtype=np.float32)
        self.market_X = self.X[:, self.market_idx]
        self.raw = {"target": bars, "kospi": kospi}
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
              "feature_cols": self.feature_cols, "market_feature_idx": self.market_idx, "market_X": self.market_X,
              "raw": self.raw, "fit_direction_model": fu.fit_direction_model,
              "predict_direction_model": fu.predict_direction_model,
              "prediction_frame": _prediction_frame, "summarize_predictions": _summarize,
              "paired_delta_ci": _paired_delta_ci}
        return {targets: ns}


class DirectionRunnerR02cTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.storage = Path(cls.tmp.name)
        cache = cls.storage / "samsung" / "data_cache"; cache.mkdir(parents=True)
        (cache / "target.parquet").write_bytes(b"snapshot-v1")
        cls.fake = FakeDirectionNotebook()
        os.environ["PREDICT_STOCK_PUBLISH"] = "true"
        cls.state = runner.execute("R02c", "samsung", "quick", cls.storage, resume=False, run_notebook_fn=cls.fake)
        cls.detail = runner.read_json(cls.state.run_dir / "r02c_samsung.json")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_both_candidates_are_scored_on_the_same_dates(self):
        metrics = pd.read_csv(self.state.run_dir / "metrics.csv")
        self.assertEqual(set(metrics["model"]),
                         {runner.R02C_CURRENT, runner.R02C_CANDIDATE, "notebook OOF (No macro ensemble)", "Always flat"})
        self.assertEqual(metrics["n"].nunique(), 1)
        self.assertEqual(self.detail["common_evaluation_days"], int(metrics["n"].iloc[0]))
        self.assertEqual(self.detail["n_candidate_features"], self.detail["n_current_features"] + 18)
        self.assertEqual(sorted(self.detail["assets"]), ["kospi", "sam"])
        self.assertTrue(all(c in metrics.columns for c in ("auc_gap", "auc_session")))

    def test_comparisons_include_leg_auc_and_selective_table(self):
        comparisons = pd.read_csv(self.state.run_dir / "comparisons.csv")
        main = comparisons[comparisons["comparison"] == f"{runner.R02C_CANDIDATE} − {runner.R02C_CURRENT}"]
        self.assertEqual(set(main["metric"]), {"log_loss", "balanced_accuracy", "accuracy", "auc_gap", "auc_session"})
        self.assertTrue((main["ci_low"] <= main["delta"]).all() and (main["delta"] <= main["ci_high"]).all())
        selective = pd.read_csv(self.state.run_dir / "selective.csv")
        at_half = selective[selective["threshold"].astype(str) == "0.5"]
        self.assertEqual(set(at_half["model"]), {runner.R02C_CURRENT, runner.R02C_CANDIDATE, "notebook OOF (No macro ensemble)"})
        self.assertEqual(self.state.manifest["config"]["candidate"], runner.R02C_CANDIDATE)

    def test_publishing_is_disabled_and_unit_is_recorded(self):
        self.assertEqual(os.environ.get("PREDICT_STOCK_PUBLISH"), "false")
        self.assertEqual(self.state.completed(), ["samsung:group_d"])
        self.assertEqual(self.state.manifest["status"], "completed")


class LegAucTests(unittest.TestCase):
    def test_perfect_score_gives_auc_one_and_small_samples_give_nan(self):
        leg = np.r_[np.linspace(-.02, .02, 60)]
        leg = leg[leg != 0]
        self.assertAlmostEqual(runner.leg_sign_auc(leg, leg), 1.0)
        self.assertAlmostEqual(runner.leg_sign_auc(-leg, leg), 0.0)
        self.assertTrue(np.isnan(runner.leg_sign_auc(leg[:10], leg[:10])))
        self.assertTrue(np.isnan(runner.leg_sign_auc(leg, np.abs(leg))))      # 부호가 한쪽뿐

    def test_paired_delta_ci_brackets_the_point_estimate_and_is_reproducible(self):
        rng = np.random.default_rng(0)
        dates = pd.DatetimeIndex(pd.bdate_range("2021-01-01", periods=300))
        leg = rng.normal(0, .01, 300)
        a, b = leg + rng.normal(0, .02, 300), rng.normal(0, 1, 300)
        d1 = runner.paired_leg_auc_delta(dates, a, b, leg, b=200)
        d2 = runner.paired_leg_auc_delta(dates, a, b, leg, b=200)
        self.assertEqual(d1, d2)
        self.assertLessEqual(d1["lo"], d1["delta"]); self.assertLessEqual(d1["delta"], d1["hi"])
        self.assertEqual(d1["n"], 300)


if __name__ == "__main__":
    unittest.main()
