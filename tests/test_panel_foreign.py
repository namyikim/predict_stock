# -*- coding: utf-8 -*-
"""P10b — 해외 자산(종목 공통) 특징을 붙인 pooled 패널의 정렬·누수 계약.

  1. 공통 특징은 대표 모델 입력에서 종목 고유 열(sam_·peer_·GDR)을 뺀 것이다.
  2. 패널 행 d에는 d **뒤의** 첫 예측일 특징만 붙는다. 같은 날짜나 이전 날짜는 절대 쓰지 않는다.
  3. 라벨을 완성하는 봉이 특징 날짜보다 앞서는 행(결과가 난 뒤의 정보)은 버린다.
  4. 폴드 학습 행은 라벨이 시험 시작 전에 완성된 행뿐이고, 시험 행은 대상 종목의 그 구간 행뿐이다.
  5. 내부 검증 분할은 날짜 블록이라 같은 날짜의 종목 행이 갈라지지 않는다.
  6. 러너 P10b는 대표 모델을 재학습하지 않고, 세 모델을 같은 날짜·같은 정답으로 채점한다.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "experiments" / "model_improvement"))
import forecast_utils as fu  # noqa: E402
import panel_data as pdm  # noqa: E402
import panel_foreign as pf  # noqa: E402
import run_model_improvement as runner  # noqa: E402


def _bars(start, n, seed):
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex(pd.bdate_range(start, periods=n))
    close = 100 * np.cumprod(1 + rng.normal(0, .015, n))
    return pd.DataFrame({"open": close * (1 + rng.normal(0, .003, n)), "close": close,
                         "volume": rng.integers(1e5, 1e6, n)}, index=idx)


class CommonFeatureTests(unittest.TestCase):
    def test_target_specific_columns_are_excluded(self):
        cols = ["sam_ret_1", "peer_ret_1", "sox_ret_1", "usdkrw_level_z60", "kospi_ret_5",
                "target_gdr_ret_1", "gdr_overnight_signal", "cal_dow", "macro_x", "nsi_y"]
        market_idx = [i for i, c in enumerate(cols) if not c.startswith(("macro_", "nsi_"))]
        self.assertEqual(pf.common_feature_columns(cols, market_idx),
                         ["sox_ret_1", "usdkrw_level_z60", "kospi_ret_5", "cal_dow"])


class AlignmentTests(unittest.TestCase):
    def test_features_come_from_the_next_calendar_date_only(self):
        cal = pd.bdate_range("2024-01-01", periods=10)
        common = pd.DataFrame({"sox_ret_1": np.arange(10, dtype=float)}, index=cal)
        panel = pd.DataFrame({"date": [cal[0], cal[3], cal[9], pd.Timestamp("2024-01-06")],   # 마지막은 토요일
                              "instrument": ["A", "A", "B", "B"]})
        out = pf.attach_common_features(panel, common)
        self.assertEqual(out["pred_date"].iloc[0], cal[1])
        self.assertEqual(out["pred_date"].iloc[1], cal[4])
        self.assertTrue(pd.isna(out["pred_date"].iloc[2]))          # 달력 끝 → 뒤 날짜 없음
        self.assertTrue(np.isnan(out["sox_ret_1"].iloc[2]))
        self.assertEqual(out["pred_date"].iloc[3], pd.Timestamp("2024-01-08"))   # 주말 → 다음 월요일
        self.assertEqual(out["sox_ret_1"].iloc[0], 1.0)              # 같은 날짜(0)가 아니라 다음 날짜(1)
        self.assertEqual(out["sox_ret_1"].iloc[1], 4.0)
        self.assertTrue((out["pred_date"].dropna() > out["date"][out["pred_date"].notna()]).all())

    def test_rows_whose_label_completes_before_the_feature_date_are_dropped(self):
        d = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
        panel = pd.DataFrame({"date": d, "instrument": "A",
                              "label_date": pd.to_datetime(["2024-01-03", "2024-01-04", pd.NaT]),
                              "pred_date": pd.to_datetime(["2024-01-03", "2024-01-08", "2024-01-05"])})
        kept, dropped = pf.drop_rows_with_features_after_label(panel)
        self.assertEqual(dropped, 1)
        self.assertEqual(list(kept["date"]), [d[0], d[2]])

    def test_label_dates_are_per_instrument_next_bar(self):
        panel = pd.DataFrame({"date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-02", "2024-01-05"]),
                              "instrument": ["A", "A", "B", "B"]})
        got = pf.label_dates(panel)
        self.assertEqual(got.iloc[0], pd.Timestamp("2024-01-03"))
        self.assertEqual(got.iloc[2], pd.Timestamp("2024-01-05"))   # B는 3·4일 거래 없음 → 라벨은 5일 종가
        self.assertTrue(pd.isna(got.iloc[1]) and pd.isna(got.iloc[3]))


class FoldTests(unittest.TestCase):
    def test_train_rows_have_labels_completed_before_the_test_starts(self):
        pred = pd.to_datetime(["2020-12-30", "2020-12-31", "2021-01-04", "2021-01-05", "2021-01-05"])
        label = pd.to_datetime(["2020-12-31", "2021-01-04", "2021-01-05", "2021-01-06", "2021-01-06"])
        is_target = [True, True, True, True, False]
        train, test = pf.fold_rows(pred, label, is_target, "2021-01-04", "2021-06-30")
        self.assertEqual(list(train), [0])           # 행 1은 라벨이 시험 첫날(01-04) 종가로 완성 → 제외
        self.assertEqual(list(test), [2, 3])         # 대상 종목만, 예측일이 구간 안
        self.assertTrue(all(label[i] < pd.Timestamp("2021-01-04") for i in train))

    def test_training_window_is_limited_by_prediction_date(self):
        pred = pd.to_datetime(["2015-01-05", "2016-06-01", "2020-01-02"])
        label = pred + pd.Timedelta(days=1)
        train, _ = pf.fold_rows(pred, label, [True] * 3, "2021-01-04", "2021-06-30", window_years=5)
        self.assertEqual(list(train), [1, 2])

    def test_inner_splits_keep_a_date_on_one_side(self):
        dates = np.repeat(pd.bdate_range("2020-01-01", periods=400).to_numpy(), 3)   # 하루 3종목
        splits = pf.date_block_splits(dates, n_splits=3, block_dates=50)
        self.assertEqual(len(splits), 3)
        for tr, va in splits:
            self.assertEqual(len(va), 150)
            self.assertLess(dates[tr].max(), dates[va].min())
            self.assertFalse(set(dates[tr]) & set(dates[va]))
        # 검증 블록은 뒤로 갈수록 늦은 날짜다(확장 학습).
        self.assertLess(dates[splits[0][1]].max(), dates[splits[1][1]].min())


# ---------------------------------------------------------------------------
# 러너 P10b 종단 계약 — 노트북 대역은 forecast_utils 의 실제 추정기 함수와 최소 프레임 함수를 준다.
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


class FakeNotebook:
    """대표 모델의 OOF 확률과 특징 프레임을 주는 노트북 대역. 재학습하지 않는다."""

    def __init__(self, target_bars, common_calendar):
        rng = np.random.default_rng(7)
        panel = pdm.build_panel({"005930.KS": target_bars}).dropna(subset=["y"])
        # 노트북 행 e = 패널 행 d의 다음 봉. y 는 같은 규칙이므로 한 칸 뒤로 옮기면 된다.
        nb_dates = pd.DatetimeIndex(target_bars.index)
        pos = nb_dates.searchsorted(pd.DatetimeIndex(panel["date"]), side="right")
        ok = pos < len(nb_dates)
        y = pd.Series(panel["y"].to_numpy()[ok].astype(int), index=nb_dates[pos[ok]])
        y = y[y.index >= "2015-01-01"]
        self.dates, self.y = y.index, y.to_numpy()
        feat = pd.DataFrame(index=common_calendar)
        for name in ("sox_ret_1", "nasdaq_ret_1", "usdkrw_level_z60", "kospi_ret_1"):
            feat[name] = rng.normal(0, 1, len(feat))
        feat["cal_dow"] = feat.index.dayofweek.astype(float)
        feat["sam_ret_1"] = rng.normal(0, 1, len(feat))
        feat["peer_ret_1"] = rng.normal(0, 1, len(feat))
        feat["macro_lead"] = rng.normal(0, 1, len(feat))
        self.feat = feat
        self.feature_cols = list(feat.columns)
        self.market_idx = [i for i, c in enumerate(self.feature_cols) if not c.startswith("macro_")]
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
              "TARGET_SPEC": {"ticker": "005930.KS"},
              "direction_estimator": fu.direction_estimator, "aligned_probabilities": fu.aligned_probabilities,
              "probability_loss": fu.probability_loss, "temperature_probabilities": fu.temperature_probabilities,
              "prediction_frame": _prediction_frame, "summarize_predictions": _summarize,
              "paired_delta_ci": _paired_delta_ci}
        return {targets: ns}


class RunnerP10bTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.storage = Path(cls.tmp.name)
        cache = cls.storage / "panel_cache"
        cache.mkdir()
        cls.bars = {}
        for i, (ticker, _, _) in enumerate(pdm.PANEL_UNIVERSE):
            start = "2022-07-15" if ticker in ("403870.KS", "240810.KQ", "357780.KQ") else "2014-06-02"
            frame = _bars(start, 1100 if start < "2020" else 300, seed=i)
            frame.to_csv(cache / f"{ticker.replace('.', '_')}.csv")
            cls.bars[ticker] = frame
        target = cls.bars["005930.KS"]
        cls.fake = FakeNotebook(target, pd.DatetimeIndex(target.index))
        os.environ["PREDICT_STOCK_PUBLISH"] = "true"
        cls.state = runner.execute("P10b", "samsung", "full", cls.storage, resume=False, run_notebook_fn=cls.fake)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_three_models_are_scored_on_the_same_dates_with_the_official_labels(self):
        metrics = pd.read_csv(self.state.run_dir / "metrics.csv")
        self.assertEqual(set(metrics["model"]),
                         {"panel pooled + foreign", "panel single + foreign", "No macro ensemble", "Always flat"})
        self.assertEqual(metrics["n"].nunique(), 1)
        detail = runner.read_json(self.state.run_dir / "panel_foreign_samsung.json")
        self.assertEqual(detail["label_agreement_target_rows"], 1.0)
        self.assertEqual(detail["headline_model"], "No macro ensemble")
        self.assertEqual(detail["common_evaluation_days"], int(metrics["n"].iloc[0]))

    def test_common_features_exclude_target_specific_columns(self):
        detail = runner.read_json(self.state.run_dir / "panel_foreign_samsung.json")
        self.assertEqual(detail["common_features"], ["sox_ret_1", "nasdaq_ret_1", "usdkrw_level_z60", "kospi_ret_1", "cal_dow"])
        self.assertIn("inst_005930_KS", detail["features"])
        self.assertEqual(len(detail["instruments"]), 9)

    def test_comparisons_and_selective_tables_follow_the_contract(self):
        comparisons = pd.read_csv(self.state.run_dir / "comparisons.csv")
        self.assertEqual(len(comparisons), 12)
        self.assertTrue(comparisons["comparison"].str.contains("대표\\(No macro ensemble\\)").any())
        selective = pd.read_csv(self.state.run_dir / "selective.csv")
        self.assertEqual(set(selective["model"]), {"panel pooled + foreign", "panel single + foreign", "No macro ensemble"})
        at_half = selective[selective["threshold"].astype(str) == "0.5"]
        self.assertEqual(len(at_half), 3)
        self.assertTrue(((at_half["coverage"] >= 0) & (at_half["coverage"] <= 1)).all())
        self.assertEqual(runner.read_json(self.state.run_dir / "panel_foreign_samsung.json")["issue_min_prob"],
                         fu.DIRECTION_ISSUE_MIN_PROB)

    def test_publishing_is_disabled_and_unit_is_recorded(self):
        self.assertEqual(os.environ.get("PREDICT_STOCK_PUBLISH"), "false")
        self.assertEqual(self.state.completed(), ["samsung:pooled_foreign"])
        self.assertEqual(self.state.manifest["status"], "completed")


if __name__ == "__main__":
    unittest.main()
