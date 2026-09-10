# -*- coding: utf-8 -*-
"""P07 — 소수 모델 앙상블.

확률 결합의 계약과 시간 분리를 고정한다.
  1. 클래스 순서 [하락, 보합, 상승]이 유지되고, 합이 1이며, 전부 유한하다.
  2. 후보가 빠지면 남은 후보로만 결합한다(fallback). 전부 빠지면 명확히 실패한다.
  3. 결합 가중치는 격자 {0, .25, .5, .75, 1}에서만 고르고, 외부 라벨을 바꿔도 안 바뀐다.
  4. 같은 모델의 이름만 다른 중복 후보는 제외한다.
"""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from run_model_improvement import (  # noqa: E402
    ENSEMBLE_WEIGHT_GRID, combine_probabilities, dedupe_candidates, select_pair_weight,
)


def _probs(n, seed):
    rng = np.random.default_rng(seed)
    raw = rng.dirichlet(np.ones(3), size=n)
    return raw


class CombineTests(unittest.TestCase):
    def test_simple_average_keeps_class_order_and_sums_to_one(self):
        a, b = _probs(50, 1), _probs(50, 2)
        out = combine_probabilities({"a": a, "b": b})
        np.testing.assert_allclose(out, (a + b) / 2)
        np.testing.assert_allclose(out.sum(axis=1), 1.)
        self.assertEqual(out.shape, (50, 3))

    def test_weighted_pair(self):
        a, b = _probs(30, 3), _probs(30, 4)
        out = combine_probabilities({"a": a, "b": b}, weights={"a": .75, "b": .25})
        np.testing.assert_allclose(out, .75 * a + .25 * b)

    def test_missing_candidate_falls_back_to_the_rest(self):
        a = _probs(20, 5)
        out = combine_probabilities({"a": a, "b": None})
        np.testing.assert_allclose(out, a)

    def test_all_missing_is_an_error(self):
        with self.assertRaises(ValueError):
            combine_probabilities({"a": None, "b": None})

    def test_non_finite_input_is_rejected(self):
        a = _probs(10, 6); a[3, 1] = np.nan
        with self.assertRaises(ValueError):
            combine_probabilities({"a": a})

    def test_output_is_renormalised_when_weights_do_not_sum_to_one(self):
        a, b = _probs(10, 7), _probs(10, 8)
        out = combine_probabilities({"a": a, "b": b}, weights={"a": 2., "b": 2.})
        np.testing.assert_allclose(out.sum(axis=1), 1.)
        np.testing.assert_allclose(out, (a + b) / 2)

    def test_mismatched_lengths_are_rejected(self):
        with self.assertRaises(ValueError):
            combine_probabilities({"a": _probs(10, 1), "b": _probs(11, 2)})


class DedupeTests(unittest.TestCase):
    def test_identical_predictions_under_two_names_keep_one(self):
        a = _probs(40, 9)
        kept = dedupe_candidates({"headline": a, "alias": a.copy(), "other": _probs(40, 10)})
        self.assertEqual(set(kept), {"headline", "other"})

    def test_distinct_predictions_are_all_kept(self):
        kept = dedupe_candidates({"x": _probs(5, 1), "y": _probs(5, 2), "z": _probs(5, 3)})
        self.assertEqual(len(kept), 3)


class PairWeightSelectionTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(11)
        self.y = rng.integers(0, 3, 400)
        # a는 정답에 가깝고 b는 잡음. 내부 검증은 a 쪽 가중을 골라야 한다.
        onehot = np.eye(3)[self.y]
        self.a = np.clip(.7 * onehot + .3 * _probs(400, 12), 1e-6, 1); self.a /= self.a.sum(axis=1, keepdims=True)
        self.b = _probs(400, 13)

    def test_grid_is_the_five_declared_points(self):
        self.assertEqual(tuple(ENSEMBLE_WEIGHT_GRID), (0., .25, .5, .75, 1.))

    def test_selection_uses_only_inner_rows(self):
        inner = np.arange(300)
        w = select_pair_weight(self.a, self.b, self.y, inner)
        self.assertIn(w, ENSEMBLE_WEIGHT_GRID)
        self.assertGreaterEqual(w, .75, "정답에 가까운 후보에 더 큰 가중을 줘야 한다")

    def test_outer_labels_do_not_change_the_weight(self):
        inner = np.arange(300)
        w1 = select_pair_weight(self.a, self.b, self.y, inner)
        altered = self.y.copy(); altered[300:] = (altered[300:] + 1) % 3
        w2 = select_pair_weight(self.a, self.b, altered, inner)
        self.assertEqual(w1, w2)

    def test_too_few_inner_rows_returns_simple_average(self):
        self.assertEqual(select_pair_weight(self.a, self.b, self.y, np.arange(5)), .5)


if __name__ == "__main__":
    unittest.main()
