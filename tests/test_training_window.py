# -*- coding: utf-8 -*-
"""P04 — 학습 기간 후보의 경계.

기존 `SelectionTests.test_live_window_contains_only_recent_past`는 5년 창 하나만 본다.
P04는 2·3·5년과 expanding을 비교하므로 후보 전부에 대해 경계를 고정한다.

핵심은 하나다: **예측일 당일은 어떤 창에서도 학습에 들어가면 안 된다.**
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
from run_model_improvement import WINDOW_CANDIDATES, window_train_indices  # noqa: E402


class WindowBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.DatetimeIndex(pd.bdate_range("2013-01-01", "2026-09-09"))
        self.before = pd.Timestamp("2026-09-09")

    def test_every_candidate_excludes_the_prediction_day(self):
        for name in WINDOW_CANDIDATES:
            with self.subTest(window=name):
                idx = window_train_indices(self.dates, self.before, name)
                self.assertTrue((self.dates[idx] < self.before).all(),
                                f"{name} 창이 예측일 당일을 학습에 넣었다")

    def test_every_candidate_is_contiguous_and_ordered(self):
        for name in WINDOW_CANDIDATES:
            with self.subTest(window=name):
                idx = window_train_indices(self.dates, self.before, name)
                self.assertTrue(len(idx) > 0)
                self.assertTrue((np.diff(idx) == 1).all(), "학습 구간에 구멍이 있다")

    def test_short_windows_start_later_than_long_ones(self):
        sizes = {name: len(window_train_indices(self.dates, self.before, name))
                 for name in ("2y", "3y", "5y", "expanding")}
        self.assertLess(sizes["2y"], sizes["3y"])
        self.assertLess(sizes["3y"], sizes["5y"])
        self.assertLessEqual(sizes["5y"], sizes["expanding"])

    def test_fixed_window_start_is_exactly_the_offset(self):
        idx = window_train_indices(self.dates, self.before, "3y")
        self.assertGreaterEqual(self.dates[idx[0]], pd.Timestamp("2023-09-09"))
        earlier = self.dates[self.dates < pd.Timestamp("2023-09-09")]
        self.assertTrue(len(earlier), "표본이 부족해 경계를 확인할 수 없다")
        self.assertNotIn(earlier[-1], self.dates[idx])

    def test_expanding_uses_everything_before_the_prediction_day(self):
        idx = window_train_indices(self.dates, self.before, "expanding")
        self.assertEqual(self.dates[idx[0]], self.dates[0])
        self.assertEqual(self.dates[idx[-1]], self.dates[self.dates < self.before][-1])

    def test_five_year_matches_the_existing_helper(self):
        """5년 기본 동작이 바뀌면 기준선이 흔들린다."""
        np.testing.assert_array_equal(
            window_train_indices(self.dates, self.before, "5y"),
            fu.rolling_train_indices(self.dates, self.before, years=5))

    def test_boundary_date_itself_is_included_not_dropped(self):
        """창 시작일에 해당하는 날은 포함한다(>= 경계)."""
        dates = pd.DatetimeIndex(["2021-09-09", "2021-09-10", "2026-09-08"])
        idx = window_train_indices(dates, pd.Timestamp("2026-09-09"), "5y")
        self.assertIn(pd.Timestamp("2021-09-09"), dates[idx])

    def test_window_with_no_history_returns_empty_not_error(self):
        dates = pd.DatetimeIndex(["2026-09-09", "2026-09-10"])
        idx = window_train_indices(dates, pd.Timestamp("2026-09-09"), "2y")
        self.assertEqual(len(idx), 0)

    def test_unknown_window_is_rejected(self):
        with self.assertRaises(ValueError):
            window_train_indices(self.dates, self.before, "7y")


class WindowSampleGuardTests(unittest.TestCase):
    """짧은 창에서 표본·클래스가 부족한 경우."""

    def test_insufficient_rows_are_detectable_before_fitting(self):
        dates = pd.DatetimeIndex(pd.bdate_range("2026-01-01", "2026-09-09"))
        idx = window_train_indices(dates, pd.Timestamp("2026-09-09"), "2y")
        self.assertLess(len(idx), 500, "이 표본은 최소 학습 행 기준에 못 미쳐야 한다")

    def test_single_class_window_still_yields_valid_probabilities(self):
        """한 클래스만 있는 창이라도 확률은 유한하고 합이 1이어야 한다."""
        rng = np.random.default_rng(0)
        X = rng.normal(size=(300, 3)).astype(np.float32)
        y = np.ones(300, dtype=int)
        fitted = fu.fit_direction_model(X, y, np.arange(250), "Logistic")
        p = fu.predict_direction_model(fitted, X[250:])
        self.assertTrue(np.isfinite(p).all())
        np.testing.assert_allclose(p.sum(axis=1), 1.)


if __name__ == "__main__":
    unittest.main()
