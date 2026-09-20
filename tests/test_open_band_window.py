# -*- coding: utf-8 -*-
"""R01 운영 반영(2026-09-20): 시초가 구간의 q 를 최근 확정 잔차에서 잡는다. 종가·금속은 예전 그대로.

여덟 칸 중 이 방식이 Winkler 점수를 유의하게 낮춘 것은 삼성 시초가뿐이었다(포함률 86.6%→81.8%). 일괄 교체는
금속 포함률을 77%→74%로 떨어뜨려 되돌렸다 — 그래서 기본값은 None 이고 시초가 호출만 창을 준다.
"""
import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forecast_utils as fu  # noqa: E402


def oof(n=1600, early_scale=2.0, seed=3):
    """앞 절반의 잔차가 두 배 큰 OOF — 자료가 적은 초기 폴드를 흉내낸다."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2015-04-01", periods=n)
    sigma = np.full(n, .015)
    y = rng.normal(0, 1, n) * sigma
    y[: n // 2] *= early_scale
    return y, np.zeros(n), sigma, dates, (lambda d, fn: (-1., 1.))


class DefaultIsUnchangedTests(unittest.TestCase):
    def test_without_a_window_the_old_rule_holds(self):
        y, p, sigma, dates, ci = oof()
        out = fu.calibrate_price_forecast(y, p, sigma, dates, 1, ci)
        self.assertEqual(out["band_q_method"], "oldest_half")
        self.assertEqual(out["band_q"], out["band_q_oldest_half"])
        n = len(y)
        self.assertAlmostEqual(out["band_q"], float(np.quantile(np.abs(y[: n // 2]) / sigma[: n // 2], .8)))
        self.assertGreater(out["band_coverage_realized"], .93)       # 부풀린 초기 잔차 때문에 과하게 넓다


class TrailingWindowTests(unittest.TestCase):
    def test_recent_residuals_bring_coverage_back_to_nominal(self):
        y, p, sigma, dates, ci = oof()
        out = fu.calibrate_price_forecast(y, p, sigma, dates, 1, ci, band_window=250)
        self.assertEqual(out["band_q_method"], "trailing_250")
        self.assertLess(out["band_q"], out["band_q_oldest_half"] * .7)
        self.assertLess(abs(out["band_coverage_realized"] - .8), .06)
        n = len(y)
        self.assertAlmostEqual(out["band_q"], float(np.quantile(np.abs(y[n - 250:]) / sigma[n - 250:], .8)))

    def test_reported_coverage_is_sequential_not_in_sample(self):
        """평가 구간의 어느 날 q 도 그 날과 그 뒤의 정답을 보지 않는다."""
        y, p, sigma, dates, ci = oof()
        a = fu.calibrate_price_forecast(y, p, sigma, dates, 1, ci, band_window=250)
        changed = y.copy()
        changed[-1] *= 1000                                        # 마지막 날 정답만 터무니없이 키운다
        b = fu.calibrate_price_forecast(changed, p, sigma, dates, 1, ci, band_window=250)
        m = len(y) - len(y) * 3 // 4
        self.assertLessEqual(abs(a["band_coverage_realized"] - b["band_coverage_realized"]), 1 / m + 1e-12)
        self.assertAlmostEqual(a["band_halfwidth_mean"], b["band_halfwidth_mean"])   # 지난 날들의 폭은 그대로

    def test_unmatured_labels_are_not_used_for_longer_horizons(self):
        y, p, sigma, dates, ci = oof()
        a = fu.calibrate_price_forecast(y, p, sigma, dates, 5, ci, band_window=250)
        changed = y.copy()
        changed[-4:] *= 50                                         # h=5 에서 마지막 4행은 아직 정답이 없다
        b = fu.calibrate_price_forecast(changed, p, sigma, dates, 5, ci, band_window=250)
        self.assertEqual(a["band_q"], b["band_q"])

    def test_short_history_falls_back_to_the_old_q(self):
        y, p, sigma, dates, ci = oof(n=200)
        out = fu.calibrate_price_forecast(y, p, sigma, dates, 1, ci, band_window=5000)
        self.assertGreater(out["band_q"], 0)


class NotebookWiringTests(unittest.TestCase):
    def test_only_the_open_forecast_uses_the_window(self):
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        cells = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
        cell = next(s for s in cells if "def open_gap_forecast(live_features):" in s)
        self.assertIn("OPEN_BAND_WINDOW = 250", cell)
        self.assertEqual(cell.count("band_window=OPEN_BAND_WINDOW"), 1)
        callers = [s for s in cells if "band_window=" in s and "def calibrate_price_forecast" not in s]
        self.assertEqual(len(callers), 1, "종가 예측은 band_window 를 주지 않는다")

    def test_metals_report_is_untouched(self):
        source = (ROOT / "tools" / "build_metals_report.py").read_text(encoding="utf-8")
        self.assertNotIn("band_window", source)


if __name__ == "__main__":
    unittest.main()
