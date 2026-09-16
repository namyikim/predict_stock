# -*- coding: utf-8 -*-
"""거시 경제 페이지 '한눈에 보는 쉬운 요약'(2026-09-16).

이 저장소의 다른 요약과 같은 태도: 판정 없음, 템플릿 문장, 인과 단정 없음, 매매 단어 없음,
오래된 자료는 판단에서 제외.
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


class TextPolicyTests(unittest.TestCase):
    def html(self):
        fx = monthly(usdkrw=np.linspace(1400, 1350, 36), dxy=100.0, rate_gap=-0.5, real_rate_gap=-0.8)
        us_jp = monthly(rate_gap=1.7)
        us10y = np.r_[np.full(33, 4.3), 4.5, 4.7, 4.85]          # 마지막 석 달에 +0.55%p
        nasdaq = np.r_[np.full(33, 24000.0), 23500, 22800, 22000]  # 마지막 석 달에 -2000
        us_market = monthly(hy_spread=2.7, us10y=us10y, nasdaq=nasdaq)
        return ms.summary_html(fx, us_jp, us_market, NOW)

    def test_no_verdict_labels(self):
        html = self.html()
        for banned in ("위험 상승", "안정 ", "주의 ", "종합 상태"):
            self.assertNotIn(banned, html)

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
