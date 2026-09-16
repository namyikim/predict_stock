# -*- coding: utf-8 -*-
"""거시 경제 요약의 판정 아래 '한 문단 요약'(2026-09-16 요청).

각 그림의 해석(insight)이 자료로 만든 짧은 구절을 이어 두세 문장으로 쓰고, 원화 함의를 세어 방향을 한 줄로 적는다.
처음의 네 항목 목록은 거추장스럽다는 지적에 줄였다.
"""
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import build_macro_report as macro  # noqa: E402
import macro_summary as ms  # noqa: E402


def plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


class DigestRenderTests(unittest.TestCase):
    def test_two_or_three_sentences(self):
        html = ms.page_digest_html([
            {"short": "위안화가 1년간 5.8% 올라 원화도 강세 쪽으로 끌리고", "won": 1},
            {"short": "실질금리 우위는 물가 자료가 끊겨 판단 보류이며", "won": 0},
            {"short": "미·일 금리차 축소로 엔화가 오를 여지가 있고", "won": 1},
            {"short": "경상수지 흑자 1,231억 달러로 원화에 우호적입니다", "won": 1},
        ])
        text = plain(html)
        body = text.split("자료가 바뀌면 문장도 바뀝니다", 1)[1].strip()
        self.assertEqual(body, "지금은 위안화가 1년간 5.8% 올라 원화도 강세 쪽으로 끌리고, 실질금리 우위는 물가 자료가 끊겨 "
                               "판단 보류이며, 미·일 금리차 축소로 엔화가 오를 여지가 있고, 경상수지 흑자 1,231억 달러로 "
                               "원화에 우호적입니다. 종합하면 자료가 가리키는 쪽은 원화 강세 쪽입니다(우호적 3개·부담 0개). "
                               "정해진 규칙으로 읽은 방향이며 예측이 아닙니다.")
        self.assertEqual(body.count(". "), 2)                       # 세 문장
        self.assertNotIn("<li", html)

    def test_mixed_signals_say_so(self):
        text = plain(ms.page_digest_html([{"short": "x", "won": 1}, {"short": "y", "won": -1}]))
        self.assertIn("뚜렷한 한 방향이 아닙니다(우호적 1개·부담 1개)", text)

    def test_empty_renders_nothing(self):
        self.assertEqual(ms.page_digest_html([]), "")
        self.assertEqual(ms.page_digest_html([{"short": "", "won": 1}]), "")


def fx(n=240, cny_drift=-.006, seed=3):
    rng = np.random.default_rng(seed)
    common = rng.normal(0, .01, n)
    cny = common * .5 + rng.normal(0, .002, n) + cny_drift
    krw = common * 1.5 + rng.normal(0, .004, n) + cny_drift * 1.5
    index = pd.date_range(end="2026-09-01", periods=n, freq="MS")
    frame = pd.DataFrame({"usdkrw": 1200 * np.exp(np.cumsum(krw)), "cny": 6.8 * np.exp(np.cumsum(cny)),
                          "jpy": 150 * np.exp(np.cumsum(common)), "us10y": 4.87}, index=index)
    frame["rate_gap"] = -0.43
    frame["kr10y"] = frame["rate_gap"] + frame["us10y"]
    frame["real_rate_gap"] = np.linspace(-1.0, 0.8, n)
    return frame


def us_jp(n=440, seed=2):
    rng = np.random.default_rng(seed)
    index = pd.date_range(end="2026-08-01", periods=n, freq="MS")
    gap = 2.5 + np.cumsum(rng.normal(0, .12, n))
    gap[-36:] = np.linspace(3.8, 1.7, 36)
    log_yen = 4.3 + .12 * gap + np.cumsum(rng.normal(0, .004, n))
    log_yen[-12:] += np.linspace(.02, .15, 12)
    return pd.DataFrame({"usdjpy": np.exp(log_yen), "rate_gap": gap}, index=index)


class InsightTests(unittest.TestCase):
    def test_each_chart_yields_a_short_clause_and_a_won_sign(self):
        pair = macro.fx_pair_insight(fx())
        self.assertEqual(pair["won"], 1)
        self.assertRegex(pair["short"], r"^위안화가 1년간 \d+\.\d% 올라 원화도 강세 쪽으로 끌리고$")
        real = macro.real_rate_insight(fx())
        self.assertEqual(real["won"], 1)
        self.assertEqual(real["short"], "실질금리는 한국이 0.80%p 높아 원화에 우호적이며")
        yen = macro.us_jp_insight(us_jp(), fx=fx())
        self.assertIn("엔화가 오를 여지", yen["short"])
        self.assertIn(yen["won"], (0, 1))
        for insight in (pair, real, yen):
            self.assertFalse(insight["short"].endswith("."))
            self.assertLess(len(insight["short"]), 60)

    def test_stale_real_rate_withholds(self):
        frame = fx()
        frame.loc[frame.index > "2023-11-01", "real_rate_gap"] = np.nan
        real = macro.real_rate_insight(frame)
        self.assertEqual(real["won"], 0)
        self.assertEqual(real["short"], "실질금리 우위는 물가 자료가 끊겨 판단 보류(명목은 미국이 0.43%p 높음)이며")

    def test_saving_insight(self):
        frame = pd.DataFrame({"saving_rate": [34.0, 35.1], "investment_rate": [30.0, 28.6],
                              "current_account": [999.7, 1230.6]}, index=pd.Index([2024, 2025], name="year"))
        insight = macro.saving_insight(frame)
        self.assertEqual(insight["won"], 1)
        self.assertEqual(insight["short"], "2025년 경상수지는 저축이 투자보다 6.5%p 많아 1,231억 달러 흑자로 원화에 우호적입니다")
        deficit = macro.saving_insight(frame.assign(current_account=[-10.0, -28.0], saving_rate=[30.0, 27.0]))
        self.assertEqual(deficit["won"], -1)
        self.assertIn("적자로 원화에 부담입니다", deficit["short"])
        self.assertIsNone(macro.saving_insight(None))

    def test_missing_data_gives_none(self):
        self.assertIsNone(macro.fx_pair_insight(None))
        self.assertIsNone(macro.real_rate_insight(fx().drop(columns="real_rate_gap")))
        self.assertIsNone(macro.us_jp_insight(us_jp().iloc[:10]))


class PageWiringTests(unittest.TestCase):
    def test_digest_sits_under_the_verdict_and_above_the_indicator_list(self):
        saving = pd.DataFrame({"saving_rate": np.linspace(39, 35, 36), "investment_rate": np.linspace(40, 29, 36),
                               "current_account": np.linspace(-30, 1230, 36)}, index=pd.Index(range(1990, 2026), name="year"))
        page = macro.build_page(datetime(2026, 9, 16, 9, 0, tzinfo=timezone(timedelta(hours=9))),
                                fx_frame=fx(), us_jp_frame=us_jp(), saving_frame=saving)
        verdict = page.index("지금 거시 환경")
        digest = page.index("한 문단 요약")
        self.assertLess(verdict, digest)
        self.assertLess(digest, page.index("지표별로 보면"))
        text = plain(page[digest:page.index("지표별로 보면")])
        self.assertTrue(text.split("바뀝니다", 1)[1].strip().startswith("지금은 위안화가"))
        self.assertIn("경상수지는 저축이 투자보다", text)
        self.assertIn("종합하면 자료가 가리키는 쪽은", text)

    def test_published_page_has_the_digest(self):
        html = (ROOT / "docs" / "macro" / "index.html").read_text(encoding="utf-8")
        self.assertIn("한 문단 요약", html)
        self.assertIn("종합하면 자료가 가리키는 쪽은", html)


if __name__ == "__main__":
    unittest.main()
