# -*- coding: utf-8 -*-
"""거시 경제: 원/달러·한·미 실질금리차 그림 아래의 해석(2026-09-16 요청).

코멘트의 판단('지금 실질금리는 한국이 더 높다')은 자료로 확인될 때만 맞다고 적는다. 한국 물가 자료가
끊겨 실질금리차가 멈춰 있으면 그 사실과 사유를 적는다.
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
    us10y = np.full(n, us)
    data = pd.DataFrame({"usdkrw": krw, "rate_gap": nominal_gap - nominal_gap[-1] + (kr - us),
                         "real_rate_gap": real_gap, "us10y": us10y}, index=index)
    data["kr10y"] = data["rate_gap"] + data["us10y"]
    if stale_after is not None:
        data.loc[data.index > stale_after, "real_rate_gap"] = np.nan
    return data


def plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))


class CommentaryTests(unittest.TestCase):
    def test_real_gap_is_compared_with_the_nominal_gap(self):
        text = plain(macro.real_rate_commentary(frame()))
        self.assertRegex(text, r"원/달러와 실질금리차의 상관은 -0\.\d\d, 같은 기간 명목금리차와는 [+-]0\.\d\d입니다")
        self.assertIn("실질금리차 쪽 관계가 더 뚜렷해, 코멘트의 '실질금리가 더 영향을 준다'는 관찰과 맞는 방향입니다", text)
        self.assertNotIn("둘 다 약한 관계이지만", text)       # 이 자료는 실질금리차 상관이 강하다
        self.assertIn("음(−)이면 한국 금리가 상대적으로 오를 때 원/달러가 내리는(원화 강세) 관계입니다", text)

    def test_weak_relations_are_called_weak(self):
        data = frame()
        rng = np.random.default_rng(11)
        data["usdkrw"] = 1200 * np.exp(np.cumsum(rng.normal(0, .02, len(data)))
                                       - .003 * (data["real_rate_gap"] - data["real_rate_gap"].iloc[0]))
        text = plain(macro.real_rate_commentary(data))
        corr = float(re.search(r"실질금리차의 상관은 ([+-]0\.\d\d)", text).group(1))
        if corr < 0 and abs(corr) < .3 and "실질금리차 쪽 관계가 더 뚜렷해" in text:
            self.assertIn("둘 다 약한 관계이지만 실질금리차 쪽 관계가 더 뚜렷해", text)

    def test_current_nominal_rates_are_checked_against_the_comment(self):
        text = plain(macro.real_rate_commentary(frame()))
        self.assertIn("2026-09 명목 10년물은 한국 4.44%, 미국 4.87% — 명목금리차 -0.43%p로 미국이 더 높습니다", text)
        self.assertIn("코멘트의 수치(미국 5%, 한국 4.5% 안팎)와 비슷합니다", text)
        far = plain(macro.real_rate_commentary(frame(kr=3.0, us=3.5)))
        self.assertIn("차이가 있습니다", far)

    def test_stale_real_gap_is_not_used_to_confirm_the_comment(self):
        html = macro.real_rate_commentary(frame(stale_after="2023-11-01", real_level=-0.82),
                                          info={"notes": {"korea_cpi": REASON}})
        text = plain(html)
        self.assertRegex(text, r"실질금리차 최신값은 [+-]\d\.\d\d%p\(2023-11\)로 (한국|미국)의 실질금리가 더 높았습니다")
        self.assertIn("그 뒤 34개월은 한국 물가 자료가 끊겨 계산하지 못했습니다", text)
        self.assertIn("HTTP 500", text)
        self.assertIn("이 페이지 자료로는 아직 확인되지 않습니다", text)
        self.assertNotIn("맞습니다.", text.split("실질금리차 최신값")[1].split("지금 명목금리차")[0])

    def test_current_real_gap_confirms_or_contradicts(self):
        agree = plain(macro.real_rate_commentary(frame(real_level=0.8)))
        self.assertIn("한국의 실질금리가 더 높았습니다. 코멘트의 '실질금리는 한국이 더 높다'와 맞습니다", agree)
        differ = plain(macro.real_rate_commentary(frame(real_level=-0.5)))
        self.assertIn("미국의 실질금리가 더 높았습니다. 코멘트의 '실질금리는 한국이 더 높다'와 다릅니다", differ)

    def test_inflation_threshold_for_a_positive_real_gap(self):
        text = plain(macro.real_rate_commentary(frame()))
        self.assertIn("한국 소비자물가 상승률이 미국보다 0.43%p 넘게 낮으면 실질금리차는 한국이 높은 쪽(+)이 됩니다", text)

    def test_expert_comment_is_quoted_and_labelled(self):
        text = plain(macro.real_rate_commentary(frame()))
        for note in macro.REAL_RATE_EXPERT_NOTES:
            self.assertIn(note, text)
        self.assertIn("외부 의견을 옮긴 것 · 이 페이지의 계산이나 예측이 아닙니다", text)
        self.assertIn("인과나 예측이 아닙니다", text)
        self.assertIn("2026-09 기준 · 그림을 만들 때마다 다시 계산", text)

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
        self.assertLess(comment, page.index("<h3", chart + 10))

    def test_ecos_cpi_failure_is_recorded_instead_of_swallowed(self):
        """ECOS 한국 물가가 실패해 FRED(2023-11까지)로 넘어갈 때 그 사유를 남긴다. 실질금리차가 멈춘 이유가
        보이지 않아 코드를 고칠 수 없었다(2026-09-16)."""
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
