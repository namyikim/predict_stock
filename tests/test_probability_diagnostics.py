# -*- coding: utf-8 -*-
"""P08 — 확률 보정과 예측 보류.

계약:
  1. 온도 보정은 argmax를 바꾸지 않는다. 보정으로 "정확도가 올랐다"고 적으면 거짓이다.
  2. 보류 임계치는 {없음, .50, .60, .70}만. 선택 건수가 0이면 정확도는 NaN이지 오류가 아니다.
  3. 신뢰도 구간 표는 구간별 평균 확률·실제 적중률·건수를 함께 낸다.
"""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import forecast_utils as fu  # noqa: E402
from run_model_improvement import ABSTAIN_THRESHOLDS, abstention_table, reliability_bins  # noqa: E402


def _probs(n, seed):
    rng = np.random.default_rng(seed)
    return rng.dirichlet(np.ones(3) * 2, size=n)


class TemperatureTests(unittest.TestCase):
    def test_temperature_keeps_argmax(self):
        p = _probs(300, 1)
        for t in (.5, .75, 1.5, 2., 4.):
            with self.subTest(t=t):
                np.testing.assert_array_equal(fu.temperature_probabilities(p, t).argmax(axis=1), p.argmax(axis=1))

    def test_temperature_one_is_identity(self):
        p = _probs(50, 2)
        np.testing.assert_allclose(fu.temperature_probabilities(p, 1.), p)

    def test_higher_temperature_flattens(self):
        p = _probs(50, 3)
        flat = fu.temperature_probabilities(p, 3.)
        self.assertLess(flat.max(axis=1).mean(), p.max(axis=1).mean())


class AbstentionTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(5)
        self.y = rng.integers(0, 3, 500)
        self.p = _probs(500, 6)

    def test_thresholds_are_the_declared_four(self):
        self.assertEqual(tuple(ABSTAIN_THRESHOLDS), (None, .5, .6, .7))

    def test_none_threshold_covers_everything(self):
        rows = abstention_table(self.p, self.y)
        none = next(r for r in rows if r["threshold"] == "none")
        self.assertEqual(none["selected"], 500)
        self.assertAlmostEqual(none["coverage"], 1.0)
        self.assertAlmostEqual(none["overall_accuracy"], none["selected_accuracy"])

    def test_coverage_shrinks_as_threshold_rises(self):
        rows = {r["threshold"]: r for r in abstention_table(self.p, self.y)}
        self.assertGreaterEqual(rows["0.5"]["coverage"], rows["0.6"]["coverage"])
        self.assertGreaterEqual(rows["0.6"]["coverage"], rows["0.7"]["coverage"])

    def test_empty_selection_is_nan_not_error(self):
        p = np.full((40, 3), 1 / 3)               # 최대 확률 0.333 → 0.5 이상 없음
        rows = {r["threshold"]: r for r in abstention_table(p, np.zeros(40, dtype=int))}
        self.assertEqual(rows["0.5"]["selected"], 0)
        self.assertTrue(np.isnan(rows["0.5"]["selected_accuracy"]))
        self.assertAlmostEqual(rows["0.5"]["coverage"], 0.)

    def test_overall_accuracy_is_reported_alongside(self):
        for r in abstention_table(self.p, self.y):
            self.assertIn("overall_accuracy", r)
            self.assertIn("selected", r)


class ReliabilityTests(unittest.TestCase):
    def test_bins_have_mean_confidence_and_hit_rate(self):
        rng = np.random.default_rng(8)
        y = rng.integers(0, 3, 600)
        p = _probs(600, 9)
        bins = reliability_bins(p, y, n_bins=5)
        self.assertEqual(sum(b["count"] for b in bins), 600)
        for b in bins:
            if b["count"]:
                self.assertTrue(0 <= b["mean_confidence"] <= 1)
                self.assertTrue(0 <= b["hit_rate"] <= 1)

    def test_perfectly_calibrated_input_is_close_to_diagonal(self):
        rng = np.random.default_rng(10)
        p = _probs(20000, 11)
        # 확률대로 라벨을 뽑으면 신뢰도 구간에서 평균 확률 ≈ 적중률
        y = np.array([rng.choice(3, p=row) for row in p])
        for b in reliability_bins(p, y, n_bins=5):
            if b["count"] > 200:
                self.assertLess(abs(b["mean_confidence"] - b["hit_rate"]), .05)


if __name__ == "__main__":
    unittest.main()
