# -*- coding: utf-8 -*-
"""P09 — 갭·장중 별도 학습.

계약:
  1. 종가→종가 = (1+갭)(1+세션)−1 이 기업행사 조정 뒤에도 성립한다.
  2. 아침 예측의 장중(시가→종가) 타깃은 **실제 시가를 정답 계산에만** 쓰고 특징에 넣지 않는다.
  3. 갭·세션 확률을 곱하거나 평균해 종가→종가 확률로 만들지 않는다.
  4. 각 타깃의 라벨은 그 타깃 수익률과 과거 변동성 밴드로만 정한다.
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from run_model_improvement import TARGET_LEGS, decompose_returns, leg_labels, session_pnl_bp  # noqa: E402


def _bars(n=300, seed=2):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, .01, n))
    open_ = close * (1 + rng.normal(0, .004, n))
    idx = pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=n))
    return pd.DataFrame({"open": open_, "close": close, "adj_close": close}, index=idx)


class DecompositionTests(unittest.TestCase):
    def test_legs_are_declared(self):
        self.assertEqual(tuple(TARGET_LEGS), ("close_to_close", "gap", "session"))

    def test_identity_holds(self):
        bars = _bars()
        legs = decompose_returns(bars)
        recon = (1 + legs["gap"]) * (1 + legs["session"]) - 1
        np.testing.assert_allclose(recon.dropna(), legs["close_to_close"].dropna(), rtol=1e-10, atol=1e-12)

    def test_identity_survives_a_split_adjustment(self):
        """액면분할로 open·close·adj_close가 같은 비율로 바뀌면 세 구간 수익률은 그대로다."""
        bars = _bars()
        legs_before = decompose_returns(bars)
        split = bars.copy(); split.iloc[150:] = split.iloc[150:] / 50     # 1:50 분할 이후 가격
        # 분할일 전후 경계 하루만 제외하고 비교한다(그날은 조정계수가 필요하다)
        legs_after = decompose_returns(split)
        for leg in ("gap", "session"):
            np.testing.assert_allclose(legs_after[leg].iloc[151:].to_numpy(),
                                       legs_before[leg].iloc[151:].to_numpy(), rtol=1e-10)

    def test_gap_uses_previous_close_and_todays_open_only(self):
        bars = _bars()
        legs = decompose_returns(bars)
        i = 10
        expected = bars["open"].iloc[i] / bars["close"].iloc[i - 1] - 1
        self.assertAlmostEqual(legs["gap"].iloc[i], expected, places=12)

    def test_session_uses_todays_open_and_close_only(self):
        bars = _bars()
        legs = decompose_returns(bars)
        i = 10
        expected = bars["close"].iloc[i] / bars["open"].iloc[i] - 1
        self.assertAlmostEqual(legs["session"].iloc[i], expected, places=12)


class LabelTests(unittest.TestCase):
    def test_labels_follow_the_band_rule_per_leg(self):
        ret = pd.Series([-.02, -.001, 0., .001, .02])
        band = pd.Series([.005] * 5)
        np.testing.assert_array_equal(leg_labels(ret, band), [0, 1, 1, 1, 2])

    def test_band_from_past_volatility_does_not_use_the_current_day(self):
        """밴드는 전날까지의 변동성이어야 한다. 당일 수익률을 바꿔도 당일 밴드는 그대로."""
        bars = _bars()
        legs = decompose_returns(bars)
        band = legs["close_to_close"].rolling(20).std().shift(1) * .3
        altered = legs["close_to_close"].copy(); altered.iloc[100] += .5
        band_alt = altered.rolling(20).std().shift(1) * .3
        self.assertEqual(band.iloc[100], band_alt.iloc[100])
        self.assertNotEqual(band.iloc[101], band_alt.iloc[101])

    def test_nan_return_gives_nan_label(self):
        ret = pd.Series([np.nan, .01]); band = pd.Series([.005, .005])
        out = leg_labels(ret, band)
        self.assertTrue(np.isnan(out[0])); self.assertEqual(out[1], 2)


class SessionPnlTests(unittest.TestCase):
    def test_pnl_follows_direction_and_subtracts_cost(self):
        session = np.array([.01, -.01, .02, 0.])
        pred = np.array([2, 0, 0, 1])          # 상승·하락·하락·보합
        gross, net = session_pnl_bp(session, pred, cost_bp=20.)
        # 상승 예측 +1%, 하락 예측 +1%(숏), 하락 예측 -2%, 보합은 미거래
        self.assertAlmostEqual(gross, (100 + 100 - 200) / 3)
        self.assertAlmostEqual(net, gross - 20.)

    def test_no_trade_days_are_excluded(self):
        gross, net = session_pnl_bp(np.array([.05, .05]), np.array([1, 1]), cost_bp=20.)
        self.assertTrue(np.isnan(gross) and np.isnan(net))


if __name__ == "__main__":
    unittest.main()
