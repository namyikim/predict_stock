# -*- coding: utf-8 -*-
"""거시 경제: 원/달러·한·미 실질금리차 그림 아래의 해석(2026-09-16 요청).

전문가 코멘트의 판단 틀('명목보다 실질금리차, 한국 실질금리가 높으면 원화 강세')을 지금 자료에 적용한다.
실질금리차가 끊겨 있으면 판단을 보류하고 그 사유를 적는다.
"""
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import build_macro_report as macro  # noqa: E402

REASON = "ECOS RuntimeError: ECOS API 조회 실패(HTTP 500) → FRED OECD 한국 CPI(2023-11까지)로 대체"


def frame(n=240, seed=5, stale_after=None, real_level=0.8, kr=4.44, us=4.87):
    """원/달러가 실질금리차 변화에 반대로 반응하도록 만든 월별 자료."""
    rng = np.random.default_rng(seed)
    index = pd.date_range(end="2026-09-01", periods=n, freq="MS")
    nominal_gap = np.cumsum(rng.normal(0, .08, n))
    inflation_gap = np.cumsum(rng.normal(0, .15, n))
    real_gap = nominal_gap - inflation_gap
    real_gap = real_gap - real_gap[-1] + real_level
    krw = np.exp(np.cumsum(-.03 * np.diff(real_gap, prepend=real_gap[0]) + rng.normal(0, .004, n))) * 1200
    data = pd.DataFrame({"usdkrw": krw, "rate_gap": nominal_gap - nominal_gap[-1] + (kr - us),
                         "real_rate_gap": real_gap, "us10y": np.full(n, us)}, index=index)
    data["kr10y"] = data["rate_gap"] + data["us10y"]
    if stale_after is not None:
        data.loc[data.index > stale_after, "real_rate_gap"] = np.nan
    return data


def plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))


def view(html):
    return plain(html).split("전문가 시각", 1)[1].split("판단 틀을 가져온 전문가 코멘트 원문", 1)[0]


class FactTests(unittest.TestCase):
    def test_facts(self):
        text = plain(macro.real_rate_commentary(frame()))
        self.assertRegex(text, r"원/달러와 실질금리차의 상관은 -0\.\d\d, 같은 기간 명목금리차와는 [+-]0\.\d\d입니다")
        self.assertIn("2026-09 명목 10년물은 한국 4.44%, 미국 4.87%(명목금리차 -0.43%p)", text)
        self.assertIn("실질금리차(10년물 − 소비자물가 상승률, 한국 − 미국) 최신값은 +0.80%p(2026-09)입니다", text)

    def test_stale_real_gap_and_its_reason(self):
        text = plain(macro.real_rate_commentary(frame(stale_after="2023-11-01"),
                                                info={"notes": {"korea_cpi": REASON}}))
        self.assertRegex(text, r"최신값은 [+-]\d\.\d\d%p\(2023-11\)입니다")
        self.assertIn("그 뒤 34개월은 한국 물가 자료가 끊겨 계산하지 못했습니다", text)
        self.assertIn("HTTP 500", text)


class ViewTests(unittest.TestCase):
    def test_real_gap_matters_more(self):
        text = view(macro.real_rate_commentary(frame()))
        self.assertIn("원/달러에는 명목금리보다 물가를 뺀 실질금리 차이가 더 크게 작용해 왔습니다.", text)
        self.assertNotIn("약한 편", text)
        self.assertIn("명목금리는 미국이 0.43%p 높습니다 — 명목만 보면 원화에 불리한 조건입니다", text)

    def test_korea_real_rate_higher_supports_the_won(self):
        text = view(macro.real_rate_commentary(frame(real_level=0.8)))
        self.assertIn("물가를 빼면 한국의 실질금리가 0.80%p 높습니다. 명목금리는 미국이 높지만 실질 기준으로는 "
                      "원화 가치가 오를 수 있는 조건입니다", text)

    def test_us_real_rate_higher_is_a_burden(self):
        text = view(macro.real_rate_commentary(frame(real_level=-0.5)))
        self.assertIn("물가를 빼도 미국의 실질금리가 0.50%p 높아, 금리 면에서는 원화에 부담입니다", text)
        self.assertNotIn("원화 가치가 오를 수 있는 조건입니다", text)

    def test_stale_data_withholds_the_call(self):
        text = view(macro.real_rate_commentary(frame(stale_after="2023-11-01")))
        self.assertIn("최신 물가 자료가 없어 지금 어느 나라의 실질금리가 더 높은지는 판단하지 못합니다", text)
        self.assertIn("한국 물가상승률이 미국보다 0.43%p 넘게 낮다면 한국의 실질금리가 더 높아지고", text)
        self.assertNotIn("물가를 빼면", text)

    def test_one_year_shift_in_the_real_gap(self):
        data = frame(real_level=0.8)
        data.iloc[-13:, data.columns.get_loc("real_rate_gap")] = np.linspace(-0.2, 0.8, 13)
        text = view(macro.real_rate_commentary(data))
        self.assertIn("최근 1년 실질금리차가 한국 쪽으로 1.00%p 움직여 원화에 우호적인 방향입니다", text)

    def test_nominal_gap_link_can_win(self):
        data = frame()
        rng = np.random.default_rng(9)
        data["usdkrw"] = 1200 * np.exp(np.cumsum(-.05 * data["rate_gap"].diff().fillna(0)
                                                 + rng.normal(0, .002, len(data))))
        text = view(macro.real_rate_commentary(data))
        self.assertIn("명목금리 차이가 더 뚜렷하게 연결됐습니다", text)

    def test_original_comment_is_kept_folded(self):
        html = macro.real_rate_commentary(frame())
        self.assertIn("<details", html)
        for note in macro.REAL_RATE_EXPERT_NOTES:
            self.assertIn(note, plain(html))
        self.assertNotIn("5%", view(html))                       # 작성 시점 수치는 결론에 쓰지 않는다

    def test_short_or_missing_data_writes_nothing(self):
        self.assertEqual(macro.real_rate_commentary(None), "")
        self.assertEqual(macro.real_rate_commentary(frame(n=20)), "")
        self.assertEqual(macro.real_rate_commentary(frame().drop(columns="real_rate_gap")), "")


class WiringTests(unittest.TestCase):
    def test_commentary_sits_right_under_the_real_rate_chart(self):
        page = macro.build_page(datetime(2026, 9, 16, 9, 0, tzinfo=timezone(timedelta(hours=9))),
                                fx_frame=frame().assign(cny=6.8), fx_info={"notes": {"korea_cpi": REASON}})
        chart = page.index("원/달러와 한·미 실질금리차 <span")
        comment = page.index("원/달러와 실질금리차의 상관")
        self.assertLess(chart, comment)
        next_heading = page.find("<h3", chart + 10)          # 마지막 그림이면 다음 제목이 없다
        self.assertLess(comment, next_heading if next_heading != -1 else len(page))

    def test_ecos_cpi_failure_is_recorded_instead_of_swallowed(self):
        """ECOS 한국 물가가 실패해 FRED(2023-11까지)로 넘어갈 때 그 사유를 남긴다(2026-09-16)."""
        from data_sources import ecos, fred, fx_inputs
        seen = {}

        def fake_build(**kwargs):
            seen["cpi"] = kwargs["korea_cpi_fn"]("2000-01-01", pd.Timestamp("2026-09-01"))
            return pd.DataFrame(), {"failed": {}}

        with patch.object(fx_inputs, "build_fx_inputs", side_effect=fake_build), \
             patch.object(ecos, "fetch_korea_cpi_monthly",
                          side_effect=RuntimeError("ECOS API 조회 실패(HTTP 500)")), \
             patch.object(fred, "fetch_fred", return_value=pd.Series([1.0])):
            _, info = macro.load_fx(fetch=True)
        self.assertEqual(list(seen["cpi"]), [1.0])
        note = info["notes"]["korea_cpi"]
        self.assertIn("ECOS", note)
        self.assertIn("HTTP 500", note)
        self.assertIn("FRED", note)

    def test_published_page_has_the_commentary(self):
        html = (ROOT / "docs" / "macro" / "index.html").read_text(encoding="utf-8")
        self.assertIn("원/달러와 실질금리차의 상관", html)
        self.assertIn(macro.REAL_RATE_EXPERT_NOTES[0], html)


if __name__ == "__main__":
    unittest.main()
