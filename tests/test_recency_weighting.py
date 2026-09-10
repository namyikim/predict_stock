# -*- coding: utf-8 -*-
"""P05 — 최근 표본 가중 학습.

계획이 요구하는 것은 성능이 아니라 **계약**이다.
  1. 무가중과 "전부 1"이 같은 결과를 낸다(기존 동작 보존).
  2. 잘못된 가중치는 조용히 무시되지 않고 거부된다.
  3. 외부 평가 라벨을 바꿔도 선택 설정과 학습 가중치가 달라지지 않는다.
"""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forecast_utils as fu  # noqa: E402


def _sample(n=700, seed=4):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 3)).astype(np.float32)
    y = np.digitize(X[:, 0] + rng.normal(0, .3, n), [-.5, .5])
    return X, y


class RecencyWeightTests(unittest.TestCase):
    def test_no_half_life_is_all_ones(self):
        np.testing.assert_allclose(fu.recency_weights(50), np.ones(50))

    def test_weights_average_to_one(self):
        for half_life in (126, 252, 504):
            with self.subTest(half_life=half_life):
                self.assertAlmostEqual(float(fu.recency_weights(1000, half_life).mean()), 1.0, places=9)

    def test_recent_rows_weigh_more(self):
        w = fu.recency_weights(600, 252)
        self.assertGreater(w[-1], w[0])
        self.assertTrue((np.diff(w) > 0).all(), "가중치가 단조 증가하지 않는다")

    def test_half_life_halves_the_weight(self):
        """반감기만큼 오래된 관측의 가중치는 절반이다(정규화는 비율을 바꾸지 않는다)."""
        half_life = 100
        w = fu.recency_weights(401, half_life)
        self.assertAlmostEqual(w[-1 - half_life] / w[-1], 0.5, places=9)
        self.assertAlmostEqual(w[-1 - 2 * half_life] / w[-1], 0.25, places=9)

    def test_shorter_half_life_concentrates_more(self):
        recent_share = {}
        for half_life in (126, 504):
            w = fu.recency_weights(1000, half_life)
            recent_share[half_life] = w[-252:].sum() / w.sum()
        self.assertGreater(recent_share[126], recent_share[504])

    def test_invalid_half_life_is_rejected(self):
        for bad in (0, -5, float("nan"), float("inf")):
            with self.subTest(half_life=bad), self.assertRaises(ValueError):
                fu.recency_weights(100, bad)

    def test_zero_length_is_empty_not_error(self):
        self.assertEqual(len(fu.recency_weights(0, 252)), 0)


class WeightedFitContractTests(unittest.TestCase):
    def setUp(self):
        self.X, self.y = _sample()
        self.train = np.arange(600)
        self.test = np.arange(600, 700)

    def test_ones_weight_matches_no_weight(self):
        """전부 1인 가중치는 무가중과 같아야 한다. 아니면 기존 기준선이 조용히 바뀐다."""
        a = fu.fit_direction_model(self.X, self.y, self.train, "Logistic")
        b = fu.fit_direction_model(self.X, self.y, self.train, "Logistic",
                                   sample_weight=np.ones(len(self.y)))
        np.testing.assert_allclose(fu.predict_direction_model(a, self.X[self.test]),
                                   fu.predict_direction_model(b, self.X[self.test]),
                                   rtol=1e-6, atol=1e-7)
        self.assertEqual(a["selection"]["params"], b["selection"]["params"])

    def test_ones_weight_matches_no_weight_for_lightgbm(self):
        a = fu.fit_direction_model(self.X, self.y, self.train, "LightGBM")
        b = fu.fit_direction_model(self.X, self.y, self.train, "LightGBM",
                                   sample_weight=np.ones(len(self.y)))
        np.testing.assert_allclose(fu.predict_direction_model(a, self.X[self.test]),
                                   fu.predict_direction_model(b, self.X[self.test]),
                                   rtol=1e-6, atol=1e-7)

    def test_real_weights_change_the_model(self):
        """가중치가 실제로 전달되는지 확인한다. 조용히 무시되면 실험이 무의미해진다."""
        weights = np.ones(len(self.y))
        weights[:300] = 0.05                       # 오래된 절반을 거의 무시
        a = fu.fit_direction_model(self.X, self.y, self.train, "Logistic")
        b = fu.fit_direction_model(self.X, self.y, self.train, "Logistic", sample_weight=weights)
        pa = fu.predict_direction_model(a, self.X[self.test])
        pb = fu.predict_direction_model(b, self.X[self.test])
        self.assertGreater(float(np.abs(pa - pb).max()), 1e-6, "가중치가 적합에 반영되지 않았다")

    def test_selection_records_that_weights_were_used(self):
        fitted = fu.fit_direction_model(self.X, self.y, self.train, "Logistic",
                                        sample_weight=fu.recency_weights(len(self.y), 252))
        self.assertTrue(fitted["selection"]["weighted"])
        plain = fu.fit_direction_model(self.X, self.y, self.train, "Logistic")
        self.assertFalse(plain["selection"]["weighted"])

    # ---- 거부 ------------------------------------------------------------
    def test_wrong_length_is_rejected(self):
        with self.assertRaises(ValueError):
            fu.fit_direction_model(self.X, self.y, self.train, "Logistic",
                                   sample_weight=np.ones(len(self.y) - 1))

    def test_negative_weight_is_rejected(self):
        weights = np.ones(len(self.y)); weights[10] = -1.0
        with self.assertRaises(ValueError):
            fu.fit_direction_model(self.X, self.y, self.train, "Logistic", sample_weight=weights)

    def test_non_finite_weight_is_rejected(self):
        for bad in (np.nan, np.inf):
            weights = np.ones(len(self.y)); weights[3] = bad
            with self.subTest(value=bad), self.assertRaises(ValueError):
                fu.fit_direction_model(self.X, self.y, self.train, "Logistic", sample_weight=weights)

    def test_all_zero_weight_is_rejected(self):
        with self.assertRaises(ValueError):
            fu.fit_direction_model(self.X, self.y, self.train, "Logistic",
                                   sample_weight=np.zeros(len(self.y)))

    # ---- 누수 ------------------------------------------------------------
    def test_outer_labels_do_not_change_selection_or_weights(self):
        """외부 평가 라벨을 바꿔도 선택 설정과 예측이 같아야 한다."""
        weights = fu.recency_weights(len(self.y), 252)
        a = fu.fit_direction_model(self.X, self.y, self.train, "Logistic", sample_weight=weights)
        altered = self.y.copy()
        altered[600:] = (altered[600:] + 1) % 3
        b = fu.fit_direction_model(self.X, altered, self.train, "Logistic", sample_weight=weights)
        self.assertEqual(a["selection"], b["selection"])
        np.testing.assert_allclose(fu.predict_direction_model(a, self.X[self.test]),
                                   fu.predict_direction_model(b, self.X[self.test]))

    def test_weights_outside_the_training_window_are_ignored(self):
        """학습 구간 밖의 가중치를 바꿔도 결과가 같아야 한다(train_indices로만 자른다)."""
        base = fu.recency_weights(len(self.y), 252)
        altered = base.copy()
        altered[600:] = 99.0
        a = fu.fit_direction_model(self.X, self.y, self.train, "Logistic", sample_weight=base)
        b = fu.fit_direction_model(self.X, self.y, self.train, "Logistic", sample_weight=altered)
        np.testing.assert_allclose(fu.predict_direction_model(a, self.X[self.test]),
                                   fu.predict_direction_model(b, self.X[self.test]))


class NotebookMirrorTests(unittest.TestCase):
    def test_notebook_copy_has_the_weighting_helper(self):
        text = (ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8")
        self.assertIn("def recency_weights", text, "노트북 사본 동기화가 빠졌다")
        self.assertIn("sample_weight=None", text)


if __name__ == "__main__":
    unittest.main()
