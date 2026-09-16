# -*- coding: utf-8 -*-
"""거시 경제: 원/달러·위안/달러 그림 아래의 해석(2026-09-16 요청).

자료로 다시 계산한 문장은 매번 달라지고, 전문가 코멘트는 외부 의견으로 구분해 옮긴다.
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


class CommentaryTests(unittest.TestCase):
    def test_moving_together_is_described_and_matches_the_comment(self):
        text = plain(macro.fx_pair_commentary(fx(together=True)))
        self.assertIn("2009년 이후 두 환율의 월간 변화는 강하게 같이 움직였습니다", text)
        self.assertRegex(text, r"최근 3년은 강하게 같이 움직였습니다\(상관 \+0\.\d\d\)")
        self.assertIn("관찰은 최근 3년 자료에서도 확인됩니다", text)
        # 원/달러가 위안/달러보다 세 배쯤 크게, 같은 방향으로 움직이도록 만들었다.
        beta = float(re.search(r"원/달러는 같은 방향으로 평균 (\d\.\d)% 따라 움직였습니다", text).group(1))
        ratio = float(re.search(r"월간 변동폭은 원/달러가 위안/달러의 (\d\.\d)배", text).group(1))
        self.assertGreater(beta, 2.0)
        self.assertGreater(ratio, 2.0)
        self.assertNotIn("1보다 크면", text)

    def test_moving_apart_is_described_honestly(self):
        text = plain(macro.fx_pair_commentary(fx(together=False)))
        self.assertIn("월간 변화는 거의 따로 움직였습니다", text)
        self.assertNotIn("따로 같이", text)
        self.assertIn("최근 3년에는 약해져, 따로 움직인 구간이 있습니다", text)

    def test_twelve_month_direction(self):
        weaker = plain(macro.fx_pair_commentary(fx(krw_drift=.015, cny_drift=.008)))
        self.assertIn("같은 방향으로 올랐습니다(원화·위안화 모두 약세)", weaker)
        stronger = plain(macro.fx_pair_commentary(fx(krw_drift=-.015, cny_drift=-.008)))
        self.assertIn("같은 방향으로 내렸습니다(원화·위안화 모두 강세)", stronger)
        split = plain(macro.fx_pair_commentary(fx(krw_drift=.015, cny_drift=-.008)))
        self.assertIn("방향이 엇갈렸습니다", split)

    def test_cross_rate_position(self):
        frame = fx()
        frame.iloc[-1, frame.columns.get_loc("usdkrw")] *= 1.3        # 마지막 달 원화만 크게 약세
        text = plain(macro.fx_pair_commentary(frame))
        self.assertRegex(text, r"원/위안 환율\(원/달러 ÷ 위안/달러\)은 [\d,.]+원으로 최근 5년 평균 [\d,.]+원 대비 \+\d\.\dσ")
        self.assertIn("원화가 위안화보다 평소보다 약한 편", text)

    def test_expert_comment_is_quoted_and_labelled_as_outside_opinion(self):
        text = plain(macro.fx_pair_commentary(fx()))
        for note in macro.FX_PAIR_EXPERT_NOTES:
            self.assertIn(note, text)
        self.assertIn("외부 의견을 옮긴 것 · 이 페이지의 계산이나 예측이 아닙니다", text)
        self.assertIn("중국 수출 비중(22%)은 코멘트의 수치이며 해마다 다릅니다", text)
        self.assertIn("인과나 예측이 아닙니다", text)
        for banned in ("매수", "매도", "사세요", "파세요"):
            self.assertNotIn(banned, text)

    def test_basis_month_is_stated(self):
        self.assertIn("2026-09 기준 · 그림을 만들 때마다 다시 계산", plain(macro.fx_pair_commentary(fx())))

    def test_short_or_missing_data_writes_nothing(self):
        self.assertEqual(macro.fx_pair_commentary(None), "")
        self.assertEqual(macro.fx_pair_commentary(fx(n=30)), "")
        self.assertEqual(macro.fx_pair_commentary(fx().drop(columns="cny")), "")


class PlacementTests(unittest.TestCase):
    def test_commentary_sits_right_under_the_krw_cny_chart(self):
        page = macro.build_page(datetime(2026, 9, 16, 9, 0, tzinfo=timezone(timedelta(hours=9))), fx_frame=fx())
        chart = page.index("원/달러와 위안/달러 <span")
        comment = page.index("해석 — 자료로 본 지금")
        self.assertLess(chart, comment)
        self.assertLess(comment, page.index("<h3", chart + 10))       # 다음 절 제목보다 앞

    def test_published_page_has_the_commentary(self):
        html = (ROOT / "docs" / "macro" / "index.html").read_text(encoding="utf-8")
        self.assertIn("해석 — 자료로 본 지금", html)
        self.assertIn(macro.FX_PAIR_EXPERT_NOTES[0], html)


if __name__ == "__main__":
    unittest.main()
