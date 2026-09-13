# -*- coding: utf-8 -*-
"""장기 전망 탭의 쉬운 요약: 이번 분기 영업이익 강조, 앞으로의 흐름, 가격 도달 시점(2026-09-13 요청)."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forecast_utils as fu  # noqa: E402


def closes(n=400, sigma=.02, last=259500., mean=0.0, seed=1):
    rng = np.random.default_rng(seed)
    path = np.exp(np.cumsum(rng.normal(mean, sigma, n)))
    return pd.Series(path / path[-1] * last, index=pd.bdate_range(end="2026-09-11", periods=n))


EARNINGS = {
    "quarter": "2026년 3분기", "quarter_code": "2026Q3", "months_included": "7월, 8월, 9월",
    "flash_applied": [{"month": "2026-09", "days": 10, "yoy": 2.7}],
    "point": 122.898e12, "low": 118.866e12, "high": 130.742e12, "no_point_reason": "",
    "last_actual": 89.492e12, "last_actual_quarter": "2026Q2", "evaluation": {"beats_baselines": True},
    "provisional_info": {"2026Q3": {"reason": "잠정실적 공시가 아직 없습니다"}},
    "next_quarter": {"quarter": "2026년 4분기", "point": 163.726e12, "low": 159.827e12, "high": 213.565e12,
                     "evaluation": {"beats_baselines": True}, "no_point_reason": ""},
    "generated_at": "2026-09-13 15:45 KST",
}
LONGTERM = {
    "as_of": "2026-08-31",
    "current": {"phase": "확장(YoY>0, 가속)", "price_to_exports_z": 1.29, "mom_12m": 1.329},
    "evaluation": {"3": {"beats_zero": False}, "6": {"beats_zero": False}, "12": {"beats_zero": False}},
    "forecast": {"3": {"point": .073}, "6": {"point": .103}, "12": {"point": .290}},
    "duration": {"phase": "확장(YoY>0, 가속)", "months_so_far": 14, "n_past": 13, "n_conditional": 1},
    "phases": {"12": [{"phase": "확장(YoY>0, 가속)", "n": 107, "median": -.0158, "positive_share": .458},
                      {"phase": "침체(YoY<0, 감속)", "n": 53, "median": .328, "positive_share": .92}]},
    "cli_outlook": {"change_6m": -1.12},
}


class PriceLevelTests(unittest.TestCase):
    def test_round_levels_above_the_price(self):
        self.assertEqual(fu.round_price_levels(259500), [300000.0, 400000.0])
        self.assertEqual(fu.round_price_levels(1812000), [2000000.0, 3000000.0])
        self.assertEqual(fu.round_price_levels(300000), [400000.0, 500000.0])   # 이미 닿은 가격은 빼고
        for bad in (None, 0, -1, float("nan")):
            self.assertEqual(fu.round_price_levels(bad), [])


class ReachOddsTests(unittest.TestCase):
    def test_curve_is_a_probability_that_only_rises(self):
        out = fu.level_reach_odds(closes(), [300000, 400000], n_paths=3000)
        for row in out["levels"]:
            self.assertEqual(row["curve"].size, 504)
            self.assertTrue(np.all(np.diff(row["curve"]) >= 0))
            self.assertTrue(0 <= row["curve"][0] <= row["curve"][-1] <= 1)

    def test_higher_level_is_less_likely(self):
        near, far = fu.level_reach_odds(closes(), [300000, 400000], n_paths=3000)["levels"]
        self.assertTrue(np.all(near["curve"] >= far["curve"]))

    def test_same_seed_same_answer(self):
        a = fu.level_reach_odds(closes(), [300000], n_paths=2000, seed=3)["levels"][0]["curve"]
        b = fu.level_reach_odds(closes(), [300000], n_paths=2000, seed=3)["levels"][0]["curve"]
        np.testing.assert_array_equal(a, b)

    def test_upward_drift_raises_the_odds(self):
        flat = fu.level_reach_odds(closes(), [400000], n_paths=3000)["levels"][0]["curve"]
        rising = fu.level_reach_odds(closes(), [400000], daily_drift=.001, n_paths=3000)["levels"][0]["curve"]
        self.assertGreater(rising[251], flat[251] + .05)

    def test_past_trend_is_not_extrapolated(self):
        """지난 1년의 추세는 빼고 변동만 쓴다. 같은 등락에 추세만 다른 두 이력은 같은 답이어야 한다."""
        calm = fu.level_reach_odds(closes(mean=0.0), [350000], n_paths=2000)["levels"][0]["curve"]
        hot = fu.level_reach_odds(closes(mean=.005), [350000], n_paths=2000)["levels"][0]["curve"]
        np.testing.assert_allclose(calm, hot)

    def test_matches_the_brownian_first_passage_formula(self):
        """추세가 없으면 1년 안 도달 확률 ≈ 2(1-Φ(b/σ√T)). 하루 한 번 보는 보정으로 경계를 0.5826σ 올린다."""
        series = closes(n=400, sigma=.02)
        out = fu.level_reach_odds(series, [series.iloc[-1] * 1.2], n_paths=20000)
        sigma = out["annual_vol"] / math.sqrt(252)
        barrier = math.log(1.2) + .5826 * sigma
        theory = 2 * (1 - .5 * (1 + math.erf(barrier / (sigma * math.sqrt(252)) / math.sqrt(2))))
        self.assertAlmostEqual(out["levels"][0]["curve"][251], theory, delta=.03)

    def test_levels_already_passed_count_as_reached(self):
        row = fu.level_reach_odds(closes(), [200000], n_paths=500)["levels"][0]
        self.assertTrue(np.all(row["curve"] == 1))
        self.assertEqual(row["half_day"], 1)
        self.assertLess(row["change"], 0)

    def test_short_history_or_no_levels_returns_none(self):
        self.assertIsNone(fu.level_reach_odds(closes(n=40), [300000]))
        self.assertIsNone(fu.level_reach_odds(closes(), []))


class LongtermSummaryTests(unittest.TestCase):
    def render(self, **changes):
        args = dict(name="삼성전자", price_date=pd.Timestamp("2026-09-11"), close=closes(),
                    longterm=LONGTERM, earnings=EARNINGS, levels=(300000, 400000), n_paths=3000)
        args.update(changes)
        return fu.longterm_easy_summary_html(**args)

    def test_profit_estimate_is_the_emphasised_first_block(self):
        html = self.render()
        self.assertRegex(html, r"font-size:26px[^>]*>122\.9조 원<")
        profit = html.index("2026년 3분기 영업이익 추정")
        self.assertLess(profit, html.index("앞으로 어떻게 될까"))
        self.assertLess(html.index("앞으로 어떻게 될까"), html.index("30만원·40만원은 언제쯤?"))

    def test_profit_block_gives_the_context(self):
        html = self.render()
        for text in ("80% 구간 118.9~130.7조 원", "89.5조 원", "이번 분기 추정은 이보다 +37.3%", "163.7조 원",
                     "이번 분기 추정 대비 +33.2%", "9월은 1~10일 관세청 속보", "잠정실적 공시가 아직 없습니다",
                     "자체 모델 추정"):
            self.assertIn(text, html)

    def test_failed_gate_hides_the_number(self):
        html = self.render(earnings=dict(EARNINGS, evaluation={"beats_baselines": False}))
        self.assertNotIn("122.9조", html)
        self.assertIn("예측하기 어렵습니다", html)

    def test_band_above_the_point_is_explained(self):
        next_q = dict(EARNINGS["next_quarter"], point=113.1e12, low=115.7e12, high=138.4e12)
        html = self.render(earnings=dict(EARNINGS, next_quarter=next_q))
        self.assertIn("구간이 추정보다 위에 있습니다", html)

    def test_outlook_lists_the_signals_already_computed(self):
        html = self.render()
        for text in ("이익 — 2026년 3분기 추정이 직전 분기보다 +37%, 2026년 4분기 추정은 그보다 +33%",
                     "3·6·12개월 모두 &#x27;변화 없음&#x27;보다 낫다는 근거가 없어",
                     "확장 국면 14개월째", "과거 13번 중 이보다 길었던 것은 1번뿐",
                     "1년 뒤 주가 중앙값 -2%", "6개월 동안 1.1포인트 내릴", "+1.3σ로 비싼 편",
                     "지난 1년 주가 +278%", "좋은 신호 1개 · 조심할 신호 3개", "매수·매도 의견이 아닙니다"):
            self.assertIn(text, html)

    def test_validated_model_horizon_is_reported(self):
        longterm = dict(LONGTERM, evaluation={"3": {"beats_zero": True}, "6": {}, "12": {}},
                        forecast={"3": {"point": .0809}})
        html = self.render(longterm=longterm)
        self.assertIn("3개월 뒤 +8.4% 예상(검증 통과), 6·12개월은 판단 근거 부족", html)
        self.assertIn("검증을 통과한 3개월 주가 모델이 있지만", html)

    def test_price_level_block(self):
        html = self.render()
        for text in ("30만원·40만원은 언제쯤?", "30만원 · 지금보다 +15.6%", "40만원 · 지금보다 +54.1%",
                     "6개월 안", "1년 안", "2년 안", "(추세 없음)", "과거 같은 국면의 1년 흐름(-2%)이 이어지면",
                     "연 변동성", "한 번이라도 그 가격에 닿을 확률", "목표가나 매수·매도 의견이 아닙니다"):
            self.assertIn(text, html)
        self.assertRegex(html, r"가능성이 절반을 넘는 때: (한 달 안|약 \d+개월 뒤) \(20\d\d년 \d+월경\)"
                               r"|2년 안에는 가능성이 절반을 넘지 않습니다")

    def test_volatility_window_is_three_years_and_the_recent_year_is_disclosed(self):
        """1년 변동성이 이전보다 훨씬 큰 해만 쓰면 도달 시점이 앞당겨진다. 3년 창을 쓰고 1년 값은 함께 적는다."""
        rng = np.random.default_rng(5)
        returns = np.concatenate([rng.normal(0, .01, 600), rng.normal(0, .04, 252)])
        path = np.exp(np.cumsum(returns))
        series = pd.Series(path / path[-1] * 259500, index=pd.bdate_range(end="2026-09-11", periods=852))
        html = self.render(close=series)
        self.assertIn("최근 3년 일간 등락", html)
        self.assertRegex(html, r"연 변동성 3\d%, 최근 1년만 보면 6\d%")

    def test_level_already_passed(self):
        self.assertIn("이미 넘었습니다", self.render(levels=(200000, 300000)))

    def test_default_levels_follow_the_price(self):
        html = self.render(close=closes(last=1812000.), levels=None)
        self.assertIn("200만원·300만원은 언제쯤?", html)

    def test_missing_inputs_do_not_break(self):
        html = self.render(longterm=None, earnings=None, close=closes(n=30))
        self.assertIn("한눈에 보는 장기 전망 요약", html)
        self.assertIn("영업이익 추정 자료를 확인하지 못했습니다", html)
        self.assertIn("장기 자료를 확인하지 못했습니다", html)
        self.assertIn("계산하지 않았습니다", html)
        self.assertNotIn("nan", html.lower())

    def test_only_one_heading(self):
        """탭 나누기가 h3 로 절을 자른다. 요약 안에 h3 가 더 있으면 탭 구조가 쪼개진다."""
        self.assertEqual(self.render().count("<h3"), 1)


class NotebookWiringTests(unittest.TestCase):
    def test_report_places_the_summary_first_in_the_long_term_tab(self):
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        report = next("".join(c["source"]) for c in nb["cells"]
                      if "def build_summary():" in "".join(c.get("source", [])))
        self.assertIn("longterm_easy_html = longterm_easy_summary_html(", report)
        self.assertIn('PRICE_LEVELS = {"samsung": (300_000, 400_000)}.get(TARGET)', report)
        self.assertLess(report.index("f'{longterm_easy_html}'"), report.index("f'{longterm_html}'"))
        self.assertLess(report.index("f'{longterm_html}'"), report.index("f'{earnings_html}'"))


if __name__ == "__main__":
    unittest.main()
