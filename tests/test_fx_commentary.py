# -*- coding: utf-8 -*-
"""거시 경제: 원/달러·위안/달러 그림 아래의 해석(2026-09-16 요청).

전문가 코멘트의 판단 틀을 지금 자료에 적용해 결론을 쓴다. 그림이 바뀌면 결론도 바뀌어야 한다 —
고정 인용은 시간이 지나면 틀린 말로 남는다(2026-09-16 지적). 원문은 작성 시점 기록으로 접어 둔다.
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


def fx(together=True, n=200, seed=3, krw_drift=0.0, cny_drift=0.0):
    rng = np.random.default_rng(seed)
    common = rng.normal(0, .01, n)
    cny = common * .5 + rng.normal(0, .002, n) + cny_drift
    krw = (common if together else rng.normal(0, .01, n)) * 1.5 + rng.normal(0, .004, n) + krw_drift
    index = pd.date_range(end="2026-09-01", periods=n, freq="MS")
    return pd.DataFrame({"usdkrw": 1200 * np.exp(np.cumsum(krw)), "cny": 6.8 * np.exp(np.cumsum(cny))}, index=index)


def plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))


def view(html):
    """'전문가 시각' 부분의 글자만."""
    text = plain(html)
    return text.split("전문가 시각", 1)[1].split("판단 틀을 가져온 전문가 코멘트 원문", 1)[0]


class FactTests(unittest.TestCase):
    def test_facts_are_recomputed_from_the_data(self):
        text = plain(macro.fx_pair_commentary(fx(together=True)))
        self.assertIn("2009년 이후 두 환율의 월간 변화는 강하게 같이 움직였습니다", text)
        self.assertRegex(text, r"최근 3년은 강하게 같이 움직였습니다\(상관 \+0\.\d\d\)")
        beta = float(re.search(r"원/달러는 같은 방향으로 평균 (\d\.\d)% 따라 움직였습니다", text).group(1))
        ratio = float(re.search(r"월간 변동폭은 원/달러가 위안/달러의 (\d\.\d)배", text).group(1))
        self.assertGreater(beta, 2.0)
        self.assertGreater(ratio, 2.0)
        self.assertRegex(text, r"최근 12개월 원/달러 [+-]\d+\.\d%, 위안/달러 [+-]\d+\.\d%")
        self.assertRegex(text, r"원/위안 환율\(원/달러 ÷ 위안/달러\)은 [\d,.]+원으로 최근 5년 평균 [\d,.]+원 대비 [+-]\d\.\dσ")
        self.assertIn("2026-09 기준 · 그림을 만들 때마다 다시 계산", text)


class ViewTests(unittest.TestCase):
    """같은 판단 틀이라도 자료가 달라지면 결론이 달라져야 한다."""

    def test_strengthening_yuan_with_co_movement(self):
        text = view(macro.fx_pair_commentary(fx(together=True, krw_drift=-.012, cny_drift=-.008)))
        self.assertIn("원/달러와 위안/달러는 같은 방향으로 움직이고 있습니다", text)
        self.assertRegex(text, r"위안화 가치가 최근 1년 \d+\.\d% 올랐습니다")
        self.assertIn("불균형을 줄이는 방향(달러 약세·위안 강세)과 맞는 흐름입니다", text)
        self.assertIn("원화 가치도 함께 오를 수 있습니다", text)

    def test_weakening_yuan_reverses_the_conclusion(self):
        text = view(macro.fx_pair_commentary(fx(together=True, krw_drift=.012, cny_drift=.008)))
        self.assertRegex(text, r"위안화 가치가 최근 1년 \d+\.\d% 떨어졌습니다")
        self.assertIn("불균형 해소 방향(위안 강세)과 반대로 가고 있고, 동행이 유지되면 원화에도 약세 압력입니다", text)
        self.assertNotIn("원화 가치도 함께 오를 수 있습니다", text)

    def test_weak_co_movement_is_not_passed_on_to_the_won(self):
        text = view(macro.fx_pair_commentary(fx(together=False, cny_drift=-.008)))
        self.assertIn("동행이 약해, 위안화 흐름만으로 원화를 읽기는 어렵습니다", text)
        self.assertIn("원화가 함께 강해진다고 보기는 어렵습니다", text)

    def test_flat_yuan_has_no_directional_call(self):
        frame = fx()
        frame.iloc[-13:, frame.columns.get_loc("cny")] = frame["cny"].iloc[-13]
        text = view(macro.fx_pair_commentary(frame))
        self.assertIn("위안화는 최근 1년 뚜렷한 방향이 없어", text)

    def test_cheap_won_against_the_yuan(self):
        frame = fx()
        frame.iloc[-1, frame.columns.get_loc("usdkrw")] *= 1.3
        self.assertIn("원화가 따라잡을(강세) 여지가 있습니다", view(macro.fx_pair_commentary(frame)))

    def test_original_comment_is_kept_folded_as_a_dated_record(self):
        html = macro.fx_pair_commentary(fx())
        self.assertIn("<details", html)
        self.assertIn("판단 틀을 가져온 전문가 코멘트 원문 (2026-09 작성 · 수치는 작성 시점 기준)", html)
        for note in macro.FX_PAIR_EXPERT_NOTES:
            self.assertIn(note, plain(html))
        self.assertNotIn("22%", view(html))                     # 고정 수치는 결론에 쓰지 않는다
        self.assertIn("자료가 바뀌면 결론도 바뀝니다 · 예측이나 매매 판단이 아닙니다", plain(html))
        for banned in ("매수", "매도", "사세요", "파세요"):
            self.assertNotIn(banned, plain(html))

    def test_short_or_missing_data_writes_nothing(self):
        self.assertEqual(macro.fx_pair_commentary(None), "")
        self.assertEqual(macro.fx_pair_commentary(fx(n=30)), "")
        self.assertEqual(macro.fx_pair_commentary(fx().drop(columns="cny")), "")


class PlacementTests(unittest.TestCase):
    def test_commentary_sits_right_under_the_krw_cny_chart(self):
        page = macro.build_page(datetime(2026, 9, 16, 9, 0, tzinfo=timezone(timedelta(hours=9))), fx_frame=fx())
        chart = page.index("원/달러와 위안/달러 <span")
        comment = page.index("2009년 이후 두 환율의 월간 변화는")
        self.assertLess(chart, comment)
        next_heading = page.find("<h3", chart + 10)          # 마지막 그림이면 다음 제목이 없다
        self.assertLess(comment, next_heading if next_heading != -1 else len(page))

    def test_published_page_has_the_commentary(self):
        html = (ROOT / "docs" / "macro" / "index.html").read_text(encoding="utf-8")
        self.assertIn("2009년 이후 두 환율의 월간 변화는", html)
        self.assertIn("전문가 시각", html)
        self.assertIn(macro.FX_PAIR_EXPERT_NOTES[0], html)


if __name__ == "__main__":
    unittest.main()
