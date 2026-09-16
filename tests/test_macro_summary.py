# -*- coding: utf-8 -*-
"""거시 경제 페이지 '한눈에 보는 쉬운 요약'(2026-09-16).

이 저장소의 다른 요약과 같은 태도: 템플릿 문장, 인과 단정 없음, 매매 단어 없음, 오래된 자료는
판단에서 제외. 판정(좋음/개선/보통/주의/나쁨)은 2026-09-16 요청으로 넣었고, 정해진 규칙으로만 내며
근거와 관점을 함께 적는다.
"""
import re
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import macro_summary as ms  # noqa: E402

NOW = pd.Timestamp("2026-09-16")


def monthly(n=36, end="2026-09-01", **cols):
    index = pd.date_range(end=end, periods=n, freq="MS")
    return pd.DataFrame({k: (v if hasattr(v, "__len__") else [v] * n) for k, v in cols.items()}, index=index)


class RuleTests(unittest.TestCase):
    def test_position_is_within_last_twelve_months(self):
        s = pd.Series(range(24), index=pd.date_range("2024-10-01", periods=24, freq="MS"))
        self.assertAlmostEqual(ms.position(s), 1.0)          # 마지막 값이 12개월 최고
        self.assertIsNone(ms.position(s.iloc[:6]))            # 자료 부족

    def test_direction_is_three_month_change(self):
        s = pd.Series([1, 2, 3, 4, 5, 6.0], index=pd.date_range("2026-04-01", periods=6, freq="MS"))
        self.assertAlmostEqual(ms.direction(s), 3.0)

    def test_stale_series_is_flagged_and_excluded(self):
        fx = monthly(usdkrw=1300.0, dxy=100.0, rate_gap=-0.5)
        fx["real_rate_gap"] = np.nan
        fx.loc[fx.index < "2024-01-01", "real_rate_gap"] = -0.8
        items = ms.build_items(fx, None, None, NOW)
        real = next(it for it in items if it["label"] == "한·미 실질금리차")
        self.assertTrue(real["stale"])
        self.assertIn("업데이트 지연", real["text"])

    def test_daily_input_is_reduced_to_monthly(self):
        daily = pd.DataFrame({"hy_spread": 3.0}, index=pd.date_range("2025-01-01", "2026-09-15", freq="D"))
        reduced = ms.monthly(daily)
        self.assertLess(len(reduced), 30)
        self.assertEqual(reduced.index.freqstr, "MS")

    def test_change_formatting_never_prints_zero_percent(self):
        # '0% 올랐습니다' 같은 문장이 나오면 안 된다. 금리 변화는 %p 로 소수 둘째 자리.
        self.assertIn("0.38%p", ms.trend_word(0.38, "%", 0.2))
        self.assertIn("1.0", ms.trend_word(-1.0, "", 0.5))
        self.assertEqual(ms.trend_word(0.05, "%p", 0.15), "최근 석 달 거의 그대로")


class StageRuleTests(unittest.TestCase):
    """판정 규칙: 1년 범위 안의 위치 × 최근 석 달 방향."""

    def test_every_combination(self):
        cases = {  # (위치, 변화) → 단계. 오를수록 유리한 지표, flat=1
            (0.9, 0.0): "좋음", (0.9, 2.0): "좋음", (0.9, -2.0): "주의",
            (0.5, 2.0): "개선", (0.5, 0.0): "보통", (0.5, -2.0): "주의",
            (0.1, 2.0): "개선", (0.1, 0.0): "나쁨", (0.1, -2.0): "나쁨",
        }
        for (pos, chg), expected in cases.items():
            with self.subTest(pos=pos, chg=chg):
                self.assertEqual(ms.stage_of(pos, chg, 1, 1.0), expected)

    def test_lower_is_better_indicators_are_mirrored(self):
        # 금리·스프레드처럼 내릴수록 유리한 지표: 1년 최고에서 더 오르면 나쁨, 최저에서 머물면 좋음.
        self.assertEqual(ms.stage_of(1.0, 0.5, -1, 0.2), "나쁨")
        self.assertEqual(ms.stage_of(0.0, 0.0, -1, 0.2), "좋음")
        self.assertEqual(ms.stage_of(1.0, -0.5, -1, 0.2), "개선")

    def test_neutral_or_missing_values_are_not_judged(self):
        self.assertIsNone(ms.stage_of(0.5, 1.0, 0, 1.0))
        self.assertIsNone(ms.stage_of(None, 1.0, 1, 1.0))
        self.assertIsNone(ms.stage_of(0.5, None, 1, 1.0))

    def test_improving_backdrop_reads_good(self):
        fx = monthly(usdkrw=np.linspace(1450, 1300, 36), dxy=np.linspace(106, 98, 36),
                     rate_gap=np.linspace(-1.5, -0.2, 36))
        us_market = monthly(hy_spread=np.linspace(4.0, 2.6, 36), us10y=np.linspace(4.9, 3.9, 36),
                            nasdaq=np.linspace(15000, 24000, 36))
        stage, counts = ms.overall(ms.build_items(fx, None, us_market, NOW))
        self.assertEqual(stage, "좋음")
        self.assertEqual(counts["good"], counts["n"])

    def test_too_few_indicators_withholds_the_verdict(self):
        fx = monthly(usdkrw=1300.0, dxy=100.0)
        page = ms.summary_html(fx, None, None, NOW)
        self.assertIn("판단 보류", page)
        self.assertIn("2개뿐이라(3개 이상 필요)", page)


class TextPolicyTests(unittest.TestCase):
    def html(self):
        fx = monthly(usdkrw=np.linspace(1400, 1350, 36), dxy=100.0, rate_gap=-0.5, real_rate_gap=-0.8)
        us_jp = monthly(rate_gap=1.7)
        us10y = np.r_[np.full(33, 4.3), 4.5, 4.7, 4.85]          # 마지막 석 달에 +0.55%p
        nasdaq = np.r_[np.full(33, 24000.0), 23500, 22800, 22000]  # 마지막 석 달에 -2000
        us_market = monthly(hy_spread=2.7, us10y=us10y, nasdaq=nasdaq)
        return ms.summary_html(fx, us_jp, us_market, NOW)

    def test_overall_verdict_is_rule_based_and_shows_its_basis(self):
        """2026-09-16 요청: 종목 보고서처럼 지금이 좋은지 나쁜지 한눈에. 판정은 규칙으로만, 근거를 함께."""
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", self.html()))
        # 원/달러만 유리한 편, 미국 10년물·나스닥은 불리한 편이고 둘 다 나빠지는 중 → 위치는 가운데, 방향은 나빠짐.
        self.assertIn("지금 거시 환경 · 한국 금융시장·위험자산 기준", text)
        self.assertIn("주의 나빠지는 쪽으로 움직이고 있습니다", text)
        self.assertIn("판정에 쓴 지표 7개 · 1년 범위로 보면 유리한 쪽 1개 · 불리한 쪽 2개 · 가운데 4개", text)
        self.assertIn("나아진 것 0개 · 나빠진 것 2개 · 그대로 5개", text)
        self.assertIn("눈여겨볼 지표 — 나쁨: 미국 10년물 · 나쁨: 나스닥", text)
        self.assertIn("관점이 바뀌면 판정도 바뀝니다", text)
        self.assertIn("예측이 아닙니다", text)

    def test_verdict_comes_before_the_indicator_list(self):
        html = self.html()
        self.assertLess(html.index("지금 거시 환경"), html.index("지표별로 보면"))

    def test_neutral_and_stale_indicators_are_not_judged(self):
        self.assertRegex(self.html(), r">참고</span></span><span><b>미·일 10년물 금리차")
        fx = monthly(usdkrw=1300.0, dxy=100.0, rate_gap=-0.5)
        fx["real_rate_gap"] = np.nan
        fx.loc[fx.index < "2024-01-01", "real_rate_gap"] = -0.8
        page = ms.summary_html(fx, None, None, NOW)
        self.assertRegex(page, r">판단 제외</span></span><span><b>한·미 실질금리차")
        self.assertIn("판정에 쓴 지표 3개", page)

    def test_no_trading_words(self):
        text = re.sub(r"<[^>]+>", "", self.html())
        for banned in ("매수", "매도", "보유", "사세요", "파세요"):
            self.assertNotIn(banned, text)

    def test_interpretation_is_hedged_not_causal(self):
        text = re.sub(r"<[^>]+>", "", self.html())
        self.assertIn("흔히", text)
        self.assertIn("인과 주장이 아닙니다", text)
        self.assertNotIn("때문에", text)

    def test_conditional_watch_point_appears_for_rate_up_nasdaq_down(self):
        text = re.sub(r"<[^>]+>", "", self.html())
        self.assertIn("장기금리가 오르는 동안 나스닥이 내렸고", text)
        self.assertIn("함께 오르기 시작하면", text)

    def test_states_basis_dates_and_that_it_is_not_a_forecast(self):
        html = self.html()
        self.assertIn("자료 기준", html)
        self.assertIn("예측이나 매매 판단이 아닙니다", html)

    def test_same_data_gives_same_text(self):
        # 템플릿이므로 결정적이어야 한다.
        self.assertEqual(self.html(), self.html())

    def test_empty_input_renders_nothing(self):
        self.assertEqual(ms.summary_html(None, None, None, NOW), "")


if __name__ == "__main__":
    unittest.main()
