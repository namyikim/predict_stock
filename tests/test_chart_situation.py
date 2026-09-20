# -*- coding: utf-8 -*-
"""쉬운 요약의 '지금 차트 상황' 카드(2026-09-20 요청).

"이제 반등이 나올 때가 됐나"에 과거 빈도로 답한다. 전날 종가까지의 봉만 쓰고, 예측이 아니라 센 것이다.
점검(2010~2026, 두 종목): 며칠 급하게 빠진 뒤에는 다음 날 상승이 평소 36%에서 43~50%로 잦았고 RSI·볼린저 같은
교과서 지표는 통하지 않았다. 대표 모델이 이미 그 경향을 반영하므로 카드는 따로 더해 읽지 말라고 적는다.
"""
import json
import re
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forecast_utils as fu  # noqa: E402


def plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def bars(returns, start="2020-01-01"):
    index = pd.bdate_range(start, periods=len(returns) + 1)
    close = 100 * np.cumprod(np.r_[1.0, 1 + np.asarray(returns, dtype=float)])
    return pd.DataFrame({"close": close, "adj_close": close}, index=index)


def reverting(n=1800, seed=5):
    """3일 연속 하락 뒤에는 다음 날 크게 오르는 계열."""
    rng = np.random.default_rng(seed)
    r = list(rng.normal(0, .012, 30))
    while len(r) < n:
        if r[-1] < 0 and r[-2] < 0 and r[-3] < 0:
            r.append(abs(rng.normal(.03, .005)))
        else:
            r.append(rng.normal(0, .012))
    return r


class SignalTests(unittest.TestCase):
    def test_signals_use_only_bars_up_to_that_day(self):
        b = bars(reverting())
        before, _ = fu.chart_situation_signals(b["close"])
        changed = b.copy()
        changed.iloc[-50:, :] *= 1.3                           # 뒤 50일만 바꾼다
        after, _ = fu.chart_situation_signals(changed["close"])
        cut = b.index[-52]
        pd.testing.assert_frame_equal(before.loc[:cut], after.loc[:cut])

    def test_streak_and_drop_definitions(self):
        b = bars([.01] * 30 + [-.01, -.01, -.05])
        signals, values = fu.chart_situation_signals(b["close"])
        last = signals.iloc[-1]
        self.assertTrue(last["down_streak"] and last["drop_1d"])
        self.assertFalse(last["up_streak"] or last["rise_1d"])
        self.assertEqual(int(values["down_run"].iloc[-1]), 3)
        self.assertTrue(signals.iloc[-4]["up_streak"])          # 하락 직전까지는 연속 상승


class SituationTests(unittest.TestCase):
    def test_reports_the_next_day_frequency_after_the_same_situation(self):
        r = reverting()
        while not (r[-1] < 0 and r[-2] < 0 and r[-3] < 0):      # 마지막 봉을 3일 연속 하락으로 끝낸다
            r.append(-.006)
        s = fu.chart_situation(bars(r), since="2020-06-01")
        item = next(a for a in s["active"] if a["key"] == "down_streak")
        self.assertTrue(item["enough"])
        self.assertGreater(item["up"], .9)                      # 만들어 둔 반등이 그대로 세어진다
        self.assertTrue(item["up_differs"])
        self.assertAlmostEqual(item["up"] + item["flat"] + item["down"], 1.0)
        self.assertAlmostEqual(sum(s["base"][k] for k in ("up", "flat", "down")), 1.0)
        self.assertIn("3일 연속 하락 중", item["detail"])

    def test_the_last_bar_has_no_outcome_and_is_not_counted(self):
        r = reverting()
        while not (r[-1] < 0 and r[-2] < 0 and r[-3] < 0):
            r.append(-.006)
        b = bars(r)
        n_now = next(a for a in fu.chart_situation(b, since="2020-06-01")["active"] if a["key"] == "down_streak")["n"]
        signals, _ = fu.chart_situation_signals(b["close"])
        self.assertEqual(n_now, int(signals["down_streak"].iloc[:-1][b.index[:-1] >= "2020-06-01"].sum()))

    def test_small_recent_sample_falls_back_to_full_history_and_says_so(self):
        r = reverting()
        while not (r[-1] < 0 and r[-2] < 0 and r[-3] < 0):
            r.append(-.006)
        b = bars(r)
        s = fu.chart_situation(b, since=str(b.index[-40].date()))
        item = next(a for a in s["active"] if a["key"] == "down_streak")
        self.assertIn("전체", item["window"])
        self.assertTrue(item["enough"])

    def test_too_few_bars_gives_none(self):
        self.assertIsNone(fu.chart_situation(bars([.01] * 30)))
        self.assertIsNone(fu.chart_situation(None))


class CardTests(unittest.TestCase):
    def situation(self):
        r = reverting()
        while not (r[-1] < 0 and r[-2] < 0 and r[-3] < 0):
            r.append(-.006)
        return fu.chart_situation(bars(r), since="2020-06-01")

    def test_card_states_it_is_a_count_not_a_forecast(self):
        html = fu.chart_situation_html(self.situation())
        text = plain(html)
        self.assertNotIn("<h3", html)                            # 탭 나누기가 요약을 쪼개지 않게
        self.assertNotIn("nan", text.lower())
        for phrase in ("지금 차트 상황", "예측이 아닙니다", "3일 연속 하락 중", "다음 날 상승이 평소보다 잦았습니다",
                       "이미 들어 있어", "따로 더해 읽지 마세요"):
            self.assertIn(phrase, text)

    def test_quiet_day_is_one_line(self):
        s = fu.chart_situation(bars(list(np.tile([.004, -.003], 200))))
        self.assertEqual(s["active"], [])
        text = plain(fu.chart_situation_html(s))
        self.assertIn("특별한 신호 없음", text)
        self.assertIn("직전 거래일", text)                       # 주말 뒤 보고서에서 '어제'라고 하지 않는다
        self.assertNotIn("1일 연속", text)

    def test_empty_situation_renders_nothing(self):
        self.assertEqual(fu.chart_situation_html(None), "")

    def test_summary_places_the_card_under_the_forecast_cards(self):
        args = dict(name="삼성전자", prediction_date=pd.Timestamp("2026-09-21"), data_date=pd.Timestamp("2026-09-18"),
                    summary={"live": {"p_down": .3, "p_flat": .3, "p_up": .4}, "ensemble": "No macro ensemble"},
                    open_forecast={"signal": "없음"}, price_forecasts=[], review={"n_scored_days": 0})
        without = fu.easy_summary_html(**args)
        with_card = fu.easy_summary_html(**args, situation=self.situation())
        self.assertNotIn("지금 차트 상황", without)
        self.assertLess(with_card.index("종가 방향"), with_card.index("지금 차트 상황"))
        self.assertLess(with_card.index("지금 차트 상황"), with_card.index("지난 예측은 맞았나"))
        self.assertEqual(with_card.count("<h3"), without.count("<h3"))


class NotebookWiringTests(unittest.TestCase):
    def test_report_cell_computes_and_passes_the_situation(self):
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        cell = next("".join(c["source"]) for c in nb["cells"] if "easy_html = easy_summary_html(" in "".join(c["source"]))
        self.assertIn("CHART_SITUATION = chart_situation(sam)", cell)
        self.assertIn('situation=globals().get("CHART_SITUATION")', cell)
        helpers = next("".join(c["source"]) for c in nb["cells"] if "def chart_situation(" in "".join(c["source"]))
        self.assertIn("def chart_situation_html(", helpers)


if __name__ == "__main__":
    unittest.main()
