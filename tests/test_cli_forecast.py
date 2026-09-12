# -*- coding: utf-8 -*-
"""R09 1·2단계 — 선행지수 전망기.

분산비는 '임의보행이면 여기서 멈춘다'는 판단에 쓰인다. 그 값이 틀리면 멈춰야 할 때 계속 가거나
갈 수 있는데 멈춘다. 워크포워드는 미래를 보지 않아야 한다 — 한 칸만 새도 성적이 통째로 거짓이 된다.
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_cli_forecast as cf  # noqa: E402


def monthly(values, start="2000-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"),
                     name="x")


class VarianceRatioTests(unittest.TestCase):
    def test_a_random_walk_gives_a_ratio_near_one(self):
        rng = np.random.default_rng(0)
        walk = np.cumsum(rng.normal(0, 1, 3000))
        result = cf.variance_ratio(walk, 2)
        self.assertAlmostEqual(result["vr"], 1.0, delta=0.12)
        self.assertLess(abs(result["z"]), 2.6)

    def test_a_trending_series_gives_a_ratio_above_one(self):
        rng = np.random.default_rng(1)
        steps = rng.normal(0, 1, 3000)
        for i in range(1, len(steps)):                 # 변화에 양의 자기상관을 넣는다
            steps[i] += 0.6 * steps[i - 1]
        result = cf.variance_ratio(np.cumsum(steps), 2)
        self.assertGreater(result["vr"], 1.2)
        self.assertGreater(result["z"], 2.0)

    def test_the_two_statistics_stay_on_the_same_scale(self):
        """delta 를 표본 수로 한 번 더 곱하면 z 가 sqrt(n) 만큼 작아진다.

        2026-09-12 실제로 그렇게 써서 VR 1.5 인 계열을 '임의보행'으로 잘못 읽었다. 등분산 z 는
        분자가 같으므로, 이분산 z 가 그보다 수십 배 작으면 정규화가 잘못된 것이다.
        """
        rng = np.random.default_rng(2)
        walk = np.cumsum(rng.normal(0, 1, 2000))       # 이분산이 없는 계열
        result = cf.variance_ratio(walk, 2)
        ratio = abs(result["z_homoskedastic"]) / max(abs(result["z"]), 1e-9)
        self.assertLess(ratio, 5.0, f"두 통계량이 {ratio:.1f}배 차이난다 — 정규화를 확인하라")

    def test_too_short_a_series_says_so(self):
        self.assertIsNone(cf.variance_ratio(np.arange(5.0), 12)["vr"])


class ForecastTests(unittest.TestCase):
    def test_a_straight_line_is_extended(self):
        line = np.arange(200, dtype=float)
        for horizon in (1, 2, 3):
            self.assertAlmostEqual(cf.forecast_ar(line, horizon, lags=2), 199.0 + horizon, places=4)

    def test_too_short_a_history_returns_nothing_rather_than_a_guess(self):
        self.assertIsNone(cf.forecast_ar(np.arange(5.0), 1, lags=2))
        self.assertIsNone(cf.forecast_var([monthly([1.0] * 5), monthly([2.0] * 5)], 1, lags=2))

    def test_var_uses_the_first_series_as_the_target(self):
        a = monthly(np.arange(120, dtype=float))
        b = monthly(np.arange(120, dtype=float) * -3.0)
        got = cf.forecast_var([a, b], 1, lags=2)
        self.assertAlmostEqual(got, 120.0, places=3)


class LeakageTests(unittest.TestCase):
    """워크포워드가 미래를 보지 않는가 — 이 시험이 통과하지 못하면 나머지 숫자는 의미가 없다."""

    def test_changing_the_future_does_not_change_earlier_forecasts(self):
        rng = np.random.default_rng(3)
        base = monthly(100 + np.cumsum(rng.normal(0, 0.3, 200)))
        first = cf.walk_forward(base, [], min_train=150, lags=2)
        tampered = base.copy()
        tampered.iloc[180:] = 9999.0                   # 미래를 완전히 바꾼다
        second = cf.walk_forward(tampered, [], min_train=150, lags=2)
        early = first[first["asof"] < pd.Timestamp(base.index[179])]
        late = second[second["asof"] < pd.Timestamp(base.index[179])]
        for column in ("rw", "drift", "ar"):
            merged = early[["asof", "horizon", column]].merge(
                late[["asof", "horizon", column]], on=["asof", "horizon"])
            self.assertTrue(np.allclose(merged[f"{column}_x"], merged[f"{column}_y"]),
                            f"{column} 이 미래 값에 따라 달라졌다")

    def test_the_target_is_the_value_h_months_after_the_asof_month(self):
        line = monthly(np.arange(200, dtype=float))
        table = cf.walk_forward(line, [], min_train=150, lags=2)
        row = table[(table["asof"] == line.index[159]) & (table["horizon"] == 3)].iloc[0]
        self.assertAlmostEqual(row["actual"], 162.0)
        self.assertAlmostEqual(row["rw"], 159.0)


class ScoringTests(unittest.TestCase):
    def test_a_clearly_better_model_shows_a_negative_interval(self):
        rng = np.random.default_rng(4)
        actual = rng.normal(0, 1, 300)
        table = pd.DataFrame({"horizon": 1, "actual": actual,
                              "rw": actual + rng.normal(0, 1.0, 300),
                              "drift": actual + rng.normal(0, 1.0, 300),
                              "ar": actual + rng.normal(0, 0.2, 300),
                              "var": np.nan})
        scores = cf.evaluate(table).set_index("model")
        self.assertLess(scores.loc["ar", "vs_rw_hi"], 0, "확실히 나은 모형은 구간 상한이 0보다 작다")

    def test_a_model_with_no_forecasts_is_dropped(self):
        table = pd.DataFrame({"horizon": 1, "actual": [1.0, 2.0, 3.0], "rw": [1.0, 2.0, 3.0],
                              "drift": [1.0, 2.0, 3.0], "ar": [1.0, 2.0, 3.0], "var": np.nan})
        self.assertNotIn("var", set(cf.evaluate(table)["model"]))


if __name__ == "__main__":
    unittest.main()
