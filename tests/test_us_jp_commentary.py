# -*- coding: utf-8 -*-
"""거시 경제: 미·일 금리차·엔/달러 그림 아래의 해석(2026-09-16 요청).

코멘트의 판단(금리차 축소, 엔화 저평가, 원화 동반 강세 가능성)은 자료로 확인될 때만 맞다고 적는다.
"""
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_macro_report as macro  # noqa: E402


def us_jp(n=440, seed=2, shrinking=True, weak_yen=True):
    """엔/달러가 금리차를 따라가게 만들고, 최근 3년은 금리차 축소 · 최근 1년은 엔화 추가 약세."""
    rng = np.random.default_rng(seed)
    index = pd.date_range(end="2026-08-01", periods=n, freq="MS")
    gap = 2.5 + np.cumsum(rng.normal(0, .12, n))
    gap[-36:] = np.linspace(3.8, 1.7, 36) if shrinking else np.linspace(1.7, 3.8, 36)
    log_yen = 4.3 + .12 * gap + np.cumsum(rng.normal(0, .004, n))
    if weak_yen:
        log_yen[-12:] += np.linspace(.02, .15, 12)
    return pd.DataFrame({"usdjpy": np.exp(log_yen), "rate_gap": gap}, index=index)


def fx(together=True, n=270, seed=4):
    rng = np.random.default_rng(seed)
    common = rng.normal(0, .02, n)
    jpy = common + rng.normal(0, .005, n)
    krw = (common if together else rng.normal(0, .02, n)) + rng.normal(0, .005, n)
    index = pd.date_range(end="2026-09-01", periods=n, freq="MS")
    return pd.DataFrame({"usdkrw": 1200 * np.exp(np.cumsum(krw)), "jpy": 130 * np.exp(np.cumsum(jpy))}, index=index)


def plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))


class CommentaryTests(unittest.TestCase):
    def test_relation_and_shrinking_gap(self):
        text = plain(macro.us_jp_commentary(us_jp()))
        self.assertRegex(text, r"미·일 금리차와 엔/달러의 상관은 \+0\.\d\d입니다")
        self.assertIn("양(+)이면 금리차가 벌어질 때 엔/달러가 오르는(엔화 약세) 관계", text)
        self.assertIn("최근 3년 고점 +3.80%p(2023-09)보다 2.10%p 낮고", text)
        self.assertIn("코멘트의 '금리차가 많이 축소되고 있다'와 맞습니다", text)

    def test_widening_gap_does_not_confirm_the_comment(self):
        text = plain(macro.us_jp_commentary(us_jp(shrinking=False, weak_yen=False)))
        self.assertIn("최근에는 금리차가 줄지 않아 코멘트의 '축소되고 있다'와 맞지 않습니다", text)
        self.assertNotIn("두 선이 벌어져 있습니다", text)

    def test_weak_yen_against_a_narrower_gap_reads_as_undervalued(self):
        text = plain(macro.us_jp_commentary(us_jp()))
        self.assertRegex(text, r"엔/달러는 [\d,.]+엔으로 최근 12개월 \+\d+\.\d%, 최근 10년 중 \d+% 지점입니다")
        self.assertIn("금리차가 줄었는데도 엔/달러는 올라(엔화 약세) 두 선이 벌어져 있습니다", text)
        self.assertRegex(text, r"지금 금리차에 맞는 엔/달러는 약 [\d,]+엔이고 실제는 [\d,]+엔\(\+\d+%\)")
        self.assertIn("엔화가 금리차로 설명되는 수준보다 약합니다. 코멘트의 '엔화 저평가'와 같은 방향입니다", text)
        self.assertIn("수준끼리의 단순 회귀라 참고치입니다", text)

    def test_no_extra_weakness_is_not_called_undervalued(self):
        text = plain(macro.us_jp_commentary(us_jp(weak_yen=False)))
        self.assertNotIn("'엔화 저평가'와 같은 방향", text)

    def test_won_link_needs_the_fx_frame(self):
        self.assertNotIn("원/달러와 엔/달러", plain(macro.us_jp_commentary(us_jp())))
        together = plain(macro.us_jp_commentary(us_jp(), fx=fx(together=True)))
        self.assertRegex(together, r"원/달러와 엔/달러의 월간 변화는 강하게 같이 움직였습니다\(상관 \+0\.\d\d, \d{4}년부터 270개월\)")
        self.assertIn("코멘트의 '원화 가치도 오를 수 있다'는 이 동행에 기댄 판단입니다", together)
        apart = plain(macro.us_jp_commentary(us_jp(), fx=fx(together=False)))
        self.assertIn("동행이 약해 원화가 엔화를 따라간다고 단정하기는 어렵습니다", apart)

    def test_expert_comment_is_quoted_and_labelled(self):
        text = plain(macro.us_jp_commentary(us_jp()))
        for note in macro.US_JP_EXPERT_NOTES:
            self.assertIn(note, text)
        self.assertIn("외부 의견을 옮긴 것 · 이 페이지의 계산이나 예측이 아닙니다", text)
        self.assertIn("2026-08 기준 · 그림을 만들 때마다 다시 계산", text)
        self.assertIn("인과나 예측이 아닙니다", text)

    def test_short_or_missing_data_writes_nothing(self):
        self.assertEqual(macro.us_jp_commentary(None), "")
        self.assertEqual(macro.us_jp_commentary(us_jp(n=40)), "")
        self.assertEqual(macro.us_jp_commentary(us_jp().drop(columns="rate_gap")), "")


class WiringTests(unittest.TestCase):
    def test_commentary_sits_right_under_the_us_jp_chart_and_uses_the_fx_frame(self):
        page = macro.build_page(datetime(2026, 9, 16, 9, 0, tzinfo=timezone(timedelta(hours=9))),
                                fx_frame=fx(), us_jp_frame=us_jp())
        chart = page.index("미·일 금리차와 엔/달러 <span")
        comment = page.index("미·일 금리차와 엔/달러의 상관은")
        self.assertLess(chart, comment)
        self.assertLess(comment, page.index("<h3", chart + 10))
        self.assertIn("원/달러와 엔/달러의 월간 변화는", page)

    def test_published_page_has_the_commentary(self):
        html = (ROOT / "docs" / "macro" / "index.html").read_text(encoding="utf-8")
        self.assertIn("미·일 금리차와 엔/달러의 상관은", html)
        self.assertIn(macro.US_JP_EXPERT_NOTES[0], html)


if __name__ == "__main__":
    unittest.main()
