# -*- coding: utf-8 -*-
"""P06 — 재학습 주기.

계약은 셋이다.
  1. 재학습 시점은 달력일이 아니라 **실제 입력 거래일 인덱스**로 정한다. 휴일이 끼어도 밀리지 않는다.
  2. 재학습하지 않는 날에는 기존 모델·온도를 그대로 쓰고 그날의 새 특징으로만 예측한다.
     하루 틀렸다고 그날 다시 학습하지 않는다.
  3. 재개해도 같은 시점에서 재학습한다(주기는 위치의 결정적 함수).
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import forecast_utils as fu  # noqa: E402
from run_model_improvement import RETRAIN_CANDIDATES, ScheduledPredictor, retrain_positions  # noqa: E402


class SchedulePositionTests(unittest.TestCase):
    def test_first_day_always_retrains(self):
        for every in RETRAIN_CANDIDATES:
            self.assertIn(0, retrain_positions(50, every))

    def test_every_one_retrains_each_trading_day(self):
        self.assertEqual(retrain_positions(7, 1), [0, 1, 2, 3, 4, 5, 6])

    def test_positions_count_trading_days_not_calendar_days(self):
        """5거래일 주기는 위치 0·5·10…이다. 사이에 휴일이 며칠 있든 같다."""
        self.assertEqual(retrain_positions(12, 5), [0, 5, 10])
        self.assertEqual(retrain_positions(45, 21), [0, 21, 42])

    def test_schedule_is_deterministic_for_resume(self):
        self.assertEqual(retrain_positions(300, 21), retrain_positions(300, 21))

    def test_invalid_period_is_rejected(self):
        for bad in (0, -1, 7):
            with self.subTest(every=bad), self.assertRaises(ValueError):
                retrain_positions(10, bad)


def _synthetic(n=900, seed=1):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 3)).astype(np.float32)
    y = np.digitize(X[:, 0] + rng.normal(0, .3, n), [-.5, .5])
    dates = pd.DatetimeIndex(pd.bdate_range("2021-01-01", periods=n))
    return X, y, dates


class ScheduledPredictorTests(unittest.TestCase):
    def setUp(self):
        self.X, self.y, self.dates = _synthetic()
        self.train = np.arange(600)
        self.test = np.arange(600, 660)

    def _run(self, every):
        predictor = ScheduledPredictor(self.X, self.y, self.dates, "Logistic",
                                       every=every, window="5y", seed=42)
        return predictor.predict_window(self.train, self.test)

    def test_hyperparameters_are_frozen_at_fold_start(self):
        """주기 내내 같은 params·온도를 쓴다. 매일 다시 튜닝하면 주기 비교가 아니다."""
        probs, log = self._run(5)
        params = {tuple(sorted(r["params"].items())) for r in log}
        temps = {r["temperature"] for r in log}
        self.assertEqual(len(params), 1)
        self.assertEqual(len(temps), 1)

    def test_no_retrain_day_reuses_the_model(self):
        _, log = self._run(21)
        trained = [r["position"] for r in log if r["retrained"]]
        self.assertEqual(trained, [0, 21, 42])
        # 재학습하지 않은 날의 모델 버전은 직전 재학습 시점과 같다
        versions = {r["position"]: r["model_version"] for r in log}
        self.assertEqual(versions[3], versions[0])
        self.assertEqual(versions[25], versions[21])
        self.assertNotEqual(versions[21], versions[0])

    def test_trained_until_never_reaches_the_prediction_day(self):
        _, log = self._run(1)
        for r in log:
            self.assertLess(r["trained_until"], self.dates[600 + r["position"]],
                            "예측일 당일 자료가 학습에 들어갔다")

    def test_a_wrong_day_does_not_trigger_retraining(self):
        """틀린 날 다음에도 예정된 시점까지 재학습하지 않는다."""
        _, log = self._run(21)
        # 위치 1~20은 어떤 결과가 나왔든 재학습 없음
        self.assertFalse(any(r["retrained"] for r in log if 1 <= r["position"] <= 20))

    def test_actual_training_count_matches_schedule(self):
        for every in (1, 5, 21):
            with self.subTest(every=every):
                _, log = self._run(every)
                self.assertEqual(sum(r["retrained"] for r in log), len(retrain_positions(60, every)))

    def test_probabilities_are_valid(self):
        probs, _ = self._run(5)
        self.assertEqual(probs.shape, (60, 3))
        self.assertTrue(np.isfinite(probs).all())
        np.testing.assert_allclose(probs.sum(axis=1), 1.)

    def test_baseline_equals_single_fit_at_fold_start(self):
        """'재학습 없음'(주기가 창보다 길면)은 기존 방식(폴드 시작 1회 학습)과 같아야 한다."""
        predictor = ScheduledPredictor(self.X, self.y, self.dates, "Logistic",
                                       every=21, window="5y", seed=42)
        probs, log = predictor.predict_window(self.train, self.test[:20])
        fitted = fu.fit_direction_model(self.X, self.y, self.train, "Logistic", seed=42)
        np.testing.assert_allclose(probs, fu.predict_direction_model(fitted, self.X[self.test[:20]]),
                                   rtol=1e-6, atol=1e-7)

    def test_resume_reproduces_the_same_predictions(self):
        a, _ = self._run(5)
        b, _ = self._run(5)
        np.testing.assert_allclose(a, b)


if __name__ == "__main__":
    unittest.main()
