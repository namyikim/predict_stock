# -*- coding: utf-8 -*-
"""장 회고 영상(2026-10-01)과 비교해 더한 것의 계약.

  1. 이동평균선 지지·저항·회복·이탈은 전일까지의 선으로 판정한다(장중에 보이던 선).
  2. '그날 함께 관찰된 것'은 수급 자료가 없어도 나온다 — 시장·업종·이동평균선·시간외·장중 해외 지표.
  3. 시간외 반응은 정규장 등락에 안 보이는 움직임이 있을 때만, 범위는 종가 기준으로 보인다.
  4. 실적 발표일 반응 통계는 과거 빈도와 '예측이 아님'을 함께 적는다.
  5. 다가오는 일정에 한국 증시 휴장일이 들어간다.
"""
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import build_session_review as R  # noqa: E402
import forecast_utils as F  # noqa: E402


def _daily(last_low, last_high, last_close, base=100.0, n=30):
    idx = pd.bdate_range("2026-08-01", periods=n)
    close = [base] * (n - 1) + [last_close]
    low = [base] * (n - 1) + [last_low]
    high = [base] * (n - 1) + [last_high]
    return pd.DataFrame({"close": close, "low": low, "high": high}, index=idx)


class MaTouchTests(unittest.TestCase):
    def test_support_when_low_touches_and_close_above(self):
        touches = R.ma_touches(_daily(99.5, 103, 102), 29)
        self.assertEqual({t["kind"] for t in touches}, {"support"})
        self.assertEqual({t["ma"] for t in touches}, {5, 20})

    def test_resistance_when_high_touches_and_close_below(self):
        touches = R.ma_touches(_daily(97, 100.5, 98), 29)
        self.assertEqual({t["kind"] for t in touches}, {"resistance"})

    def test_reclaim_and_fail(self):
        self.assertEqual({t["kind"] for t in R.ma_touches(_daily(97, 101, 100.5), 29)}, {"reclaim"})
        self.assertEqual({t["kind"] for t in R.ma_touches(_daily(99, 103, 99.2), 29)}, {"fail"})

    def test_no_touch_far_from_line(self):
        self.assertEqual(R.ma_touches(_daily(104, 108, 107), 29), [])


class MarketObservationTests(unittest.TestCase):
    SUMMARY = {"c2c": .0279, "kospi_c2c": .0195, "peer_c2c": .0321, "prior_5d": -.02,
               "ma_touches": [{"ma": 20, "level": 263300.0, "kind": "support", "price": 264500.0}],
               "micron_ah": {"ret": .0014, "low_ret": -.0155, "high_ret": .0162},
               "nvidia_ah": {"ret": .0055, "low_ret": 0.0, "high_ret": .0065},
               "session_cross": {"NQ=F": {"label": "나스닥100 선물", "ret": .01, "after_high": .0004},
                                 "ZN=F": {"label": "미 10년물 국채 선물", "ret": -.002}},
               "from_high": -.025}

    def test_lines_without_flows(self):
        seen = F.market_observations(self.SUMMARY, peer_name="SK하이닉스")
        text = "\n".join(seen)
        self.assertIn("코스피도 +1.95%", text)
        self.assertIn("20일선(263,300원)", text)
        self.assertIn("마이크론은 미국 정규장 마감 뒤 시간외", text)
        self.assertIn("-1.55%~+1.62%", text)
        self.assertNotIn("엔비디아", text)             # 시간외 움직임이 작으면 적지 않는다
        self.assertIn("나스닥100 선물 +1.00%", text)
        self.assertIn("(금리 상승)", text)               # 국채 선물 가격 하락 = 금리 상승
        self.assertIn("고점 이후", text)                  # 고가에서 2% 넘게 밀린 날만
        self.assertIn("장중 고가보다 -2.50%", text)

    def test_flow_story_reuses_market_lines(self):
        story = F.flow_story({"foreign_net": -1000, "inst_net": 300, "indiv_net": 700}, None, self.SUMMARY, 270000,
                             peer_name="SK하이닉스")
        self.assertTrue(any("20일선" in x for x in story["observations"]))

    def test_review_shows_observations_without_flows(self):
        import json
        stored = ROOT / "forecast_history" / "samsung" / "reviews" / "2026-09-29.json"
        review = json.loads(stored.read_text(encoding="utf-8"))
        review["flow_story"] = None
        review["summary"] = dict(review["summary"], **self.SUMMARY)
        html = F.review_section_html(review)
        self.assertIn("그날 함께 관찰된 것", html)
        self.assertIn("투자자별 수급은 아직 집계 전", html)
        self.assertIn("20일선 지지 후 반등", html)
        self.assertNotIn("누가 팔고 샀나", html)


class EarningsReactionHtmlTests(unittest.TestCase):
    def test_box_states_frequency_not_forecast(self):
        stats = {"n": 40, "down": 22, "mean": -.003, "first": "2016-01-08", "last": "2026-07-07",
                 "prior_up": {"n": 24, "down": 15, "mean": -.006},
                 "prior_down": {"n": 16, "down": 7, "mean": .001, "next5_n": 16, "next5_up": 9},
                 "rows": [{"date": "2026-07-07", "ret": -.012, "prior20": .05, "next5": .02}]}
        html = F.earnings_reactions_html(stats, "삼성전자")
        self.assertIn("발표날 하락 22/40번", html)
        self.assertIn("발표 전 20거래일 오른 뒤: 발표날 하락 15/24번", html)
        self.assertIn("그 뒤 5거래일 상승 9/16번", html)
        self.assertIn("예측이 아닙니다", html)
        self.assertEqual(F.earnings_reactions_html(None), "")


class HolidayEventTests(unittest.TestCase):
    def test_upcoming_weekday_holidays_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "h.csv"
            path.write_text("date,name,note\n2026-10-03,개천절,토요일\n2026-10-05,개천절 대체공휴일,\n"
                            "2026-10-09,한글날,\n2026-12-25,성탄절,\n", encoding="utf-8")
            events = F._krx_holiday_events("2026-10-01", days=7, path=str(path))
        self.assertEqual([e["date"] for e in events], ["2026-10-05"])
        self.assertIn("한국 증시 휴장(개천절 대체공휴일)", events[0]["label"])


if __name__ == "__main__":
    unittest.main()
