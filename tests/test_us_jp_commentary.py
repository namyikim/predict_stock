# -*- coding: utf-8 -*-
"""거시 경제: 미·일 금리차·엔/달러 그림 아래의 해석(2026-09-16 요청).

전문가 코멘트의 판단 틀(금리차 축소·엔화 저평가 → 엔화 강세, 동행하면 원화도)을 지금 자료에 적용한다.
자료가 반대로 가면 결론도 반대로, 방향이 엇갈리면 보류한다.
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
    """엔/달러가 금리차를 따라가게 만들고, 최근 3년은 금리차 축소(또는 확대), 최근 1년은 엔화 추가 약세."""
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


def view(html):
    return plain(html).split("전문가 시각", 1)[1].split("판단 틀을 가져온 전문가 코멘트 원문", 1)[0]


class FactTests(unittest.TestCase):
    def test_facts(self):
        text = plain(macro.us_jp_commentary(us_jp(), fx=fx()))
        self.assertRegex(text, r"미·일 금리차와 엔/달러의 상관은 \+0\.\d\d입니다")
        self.assertIn("최근 3년 고점 +3.80%p(2023-09)보다 2.10%p 낮고", text)
        self.assertRegex(text, r"엔/달러는 [\d,.]+엔 — 최근 12개월 \+\d+\.\d%, 최근 10년 중 \d+% 지점")
        self.assertRegex(text, r"지금 금리차에 맞는 엔/달러는 약 [\d,]+엔, 실제는 [\d,]+엔\(\+\d+%\)입니다")
        self.assertIn("수준끼리의 단순 회귀라 참고치입니다", text)
        self.assertRegex(text, r"원/달러와 엔/달러의 월간 변화는 강하게 같이 움직였습니다\(상관 \+0\.\d\d, \d{4}년부터 270개월\)")
        self.assertIn("2026-08 기준 · 그림을 만들 때마다 다시 계산", text)


class ViewTests(unittest.TestCase):
    def test_shrinking_gap_and_cheap_yen_point_to_a_stronger_yen_and_won(self):
        text = view(macro.us_jp_commentary(us_jp(), fx=fx(together=True)))
        self.assertIn("엔/달러를 움직이는 핵심 변수는 미·일 10년물 금리차입니다.", text)
        self.assertIn("금리차가 최근 3년 고점 +3.80%p에서 +1.70%p로 줄어, 엔화 강세 요인이 쌓이고 있습니다", text)
        self.assertIn("그런데도 엔화는 최근 1년 더 약해져, 금리차와 엔화 가치가 벌어져 있습니다", text)
        self.assertRegex(text, r"엔화가 \d+%가량 저평가된 상태로 보입니다")
        self.assertIn("금리차 축소와 저평가가 겹쳐 엔화 가치가 오를 여지가 있습니다. 원화도 엔화와 같은 방향으로 "
                      "움직이는 경향이 있어 원화 가치도 오를 수 있습니다", text)

    def test_weak_co_movement_keeps_the_won_out_of_it(self):
        text = view(macro.us_jp_commentary(us_jp(), fx=fx(together=False)))
        self.assertIn("다만 원화와 엔화의 동행이 약해, 원화까지 함께 오른다고 보기는 어렵습니다", text)

    def test_widening_gap_reverses_the_call(self):
        text = view(macro.us_jp_commentary(us_jp(shrinking=False, weak_yen=False)))
        self.assertRegex(text, r"금리차가 최근 1년 \+\d\.\d\d%p 벌어져 엔화에는 약세 요인입니다")
        self.assertNotIn("엔화 가치가 오를 여지", text)
        self.assertIn("판단은 보류합니다", text)

    def test_mixed_signals_withhold_the_call(self):
        text = view(macro.us_jp_commentary(us_jp(weak_yen=False)))
        self.assertIn("엔화 강세 요인이 쌓이고 있습니다", text)
        self.assertNotIn("저평가된 상태", text)
        self.assertIn("금리차와 엔화 가치가 한 방향을 가리키지 않아, 엔화 방향에 대한 판단은 보류합니다", text)

    def test_weak_rate_link_is_flagged(self):
        data = us_jp()
        rng = np.random.default_rng(7)
        data["usdjpy"] = 110 * np.exp(np.cumsum(rng.normal(0, .03, len(data))))
        self.assertIn("월간 변화로 본 연결은 약한 편이라", view(macro.us_jp_commentary(data)))

    def test_original_comment_is_kept_folded(self):
        html = macro.us_jp_commentary(us_jp())
        self.assertIn("<details", html)
        for note in macro.US_JP_EXPERT_NOTES:
            self.assertIn(note, plain(html))
        self.assertIn("자료가 바뀌면 결론도 바뀝니다", plain(html))

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
