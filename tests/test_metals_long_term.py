# -*- coding: utf-8 -*-
"""금·은 보고서의 장기 가격 그림(2026-09-13 추가)."""
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_metals_report as mr  # noqa: E402
import forecast_utils as fu  # noqa: E402


def daily(first=400.0, last=4500.0, start="2004-01-02", end="2026-09-04", spike=None):
    """꾸준히 오르는 가격. spike=(위치, 배수)면 그 자리부터 한 달 반 동안 튀어 과거 고점을 만든다.

    하루만 튀게 하면 월말 종가로 줄일 때 사라지고, 배수가 작으면 끝값보다 낮아 고점이 되지 못한다.
    """
    index = pd.bdate_range(start, end)
    values = np.geomspace(first, last, len(index))
    if spike is not None:
        position, factor = spike
        values[position:position + 32] *= factor
    return pd.Series(values, index=index)


class SummaryTests(unittest.TestCase):
    def test_changes_are_measured_back_from_the_last_close(self):
        close = daily()
        summary = mr.long_term_summary(close)
        one_year_ago = close.loc[:close.index[-1] - pd.DateOffset(years=1)].iloc[-1]
        self.assertAlmostEqual(summary["changes"]["1년"], close.iloc[-1] / one_year_ago - 1)
        self.assertAlmostEqual(summary["changes"]["전체"], 4500 / 400 - 1)
        self.assertGreater(summary["cagr"], 0.10)

    def test_the_peak_and_the_distance_from_it_are_reported(self):
        close = daily(spike=(3000, 6.0))
        summary = mr.long_term_summary(close)
        self.assertEqual(summary["peak_date"], close.idxmax())
        self.assertLess(summary["peak_date"], close.index[-1])
        self.assertLess(summary["from_peak"], 0)

    def test_a_short_history_is_not_called_long_term(self):
        self.assertIsNone(mr.long_term_summary(daily(start="2026-06-01")))

    def test_a_period_longer_than_the_data_is_left_blank(self):
        summary = mr.long_term_summary(daily(start="2020-01-02"))
        self.assertIsNone(summary["changes"]["10년"])


class ChartTests(unittest.TestCase):
    def test_the_axis_is_logarithmic_with_round_dollar_ticks(self):
        svg = mr.long_term_chart(daily(), "금", "#b8860b")
        self.assertIn("로그 눈금", svg)
        for tick in (">$500<", ">$1,000<", ">$2,000<"):
            self.assertIn(tick, svg)

    def test_cheap_prices_keep_their_cents(self):
        svg = mr.long_term_chart(daily(first=4.0, last=45.0), "은", "#6b7a89")
        self.assertIn(">$5<", svg)
        self.assertIn(">$20<", svg)

    def test_a_past_peak_and_the_current_price_are_both_labelled(self):
        svg = mr.long_term_chart(daily(spike=(3000, 6.0)), "은", "#6b7a89")
        self.assertIn("고점 ", svg)
        self.assertIn("현재 ", svg)
        self.assertNotIn("사상 최고", svg)

    def test_one_label_when_today_is_the_peak(self):
        """지금이 곧 고점이면 이름표 둘이 같은 자리에서 겹친다. 하나로 합친다."""
        svg = mr.long_term_chart(daily(), "금", "#b8860b")
        self.assertIn("사상 최고", svg)
        self.assertNotIn("고점 ", svg)

    def test_the_chart_is_valid_svg(self):
        ET.fromstring(mr.long_term_chart(daily(spike=(3000, 3.0)), "금", "#b8860b"))

    def test_too_short_to_draw(self):
        self.assertEqual(mr.long_term_chart(daily(start="2026-01-02"), "금", "#b8860b"), "")


class SectionTests(unittest.TestCase):
    def res(self, history=None):
        wf = dict(n=3689, folds=30, first="2012-01-03", last="2026-09-04", balanced_accuracy=.357,
                  prior_balanced_accuracy=.337, log_loss=1.0956, prior_log_loss=1.0930, auc_up=.527,
                  auc_up_vs_down=.534, class_share=np.array([.33, .29, .38]),
                  log_loss_diff_lo=-.003, log_loss_diff_hi=.008)
        live = dict(p_down=.296, p_flat=.322, p_up=.382, band=.0045, as_of=pd.Timestamp("2026-09-04"))
        row = dict(horizon="1주일", trading_days=5, as_of_date="2026-09-04", target_date="2026-09-11",
                   current_close=4476.6, signal="없음", predicted_return=np.nan, predicted_close=np.nan,
                   center_close=4476.6, low_close=4278., high_close=4675., band_coverage=.79, vol_model="simple",
                   model_mae=.02, zero_baseline_mae=.02, mae_diff_lo=0., mae_diff_hi=0., oof_slope=0.)
        out = dict(live=live, wf=wf, price_rows=[row],
                   price_stats={"1주일": dict(selection_mae_diff_lo=0., selection_mae_diff_hi=0.)},
                   prediction_date=pd.Timestamp("2026-09-07"), scored=pd.DataFrame(),
                   review=fu.review_ledger(pd.DataFrame(), pd.DataFrame()))
        if history is not None:
            out["history"] = history
        return out

    def test_the_section_comes_before_the_direction_forecast(self):
        html = mr.render_asset("gold", self.res(daily()), 1355.)
        self.assertIn("장기 가격 흐름", html)
        self.assertIn("<svg", html)
        self.assertLess(html.index("장기 가격 흐름"), html.index("다음 거래일("))

    def test_the_summary_table_and_its_limits_are_shown(self):
        html = mr.render_asset("silver", self.res(daily(first=4.0, last=45.0, spike=(3000, 3.0))), 1355.)
        for text in ("1년 전 대비", "5년 전 대비", "10년 전 대비", "연평균", "사상 최고 대비",
                     "로그 눈금", "앞으로의 방향을 알려 주지 않습니다"):
            self.assertIn(text, html)

    def test_no_history_means_no_section_and_no_crash(self):
        html = mr.render_asset("gold", self.res(), 1355.)
        self.assertNotIn("장기 가격 흐름", html)
        self.assertIn("다음 거래일(", html)

    def test_main_passes_the_price_history(self):
        source = (ROOT / "tools" / "build_metals_report.py").read_text(encoding="utf-8")
        self.assertIn('history=bars["close"]', source)


if __name__ == "__main__":
    unittest.main()
