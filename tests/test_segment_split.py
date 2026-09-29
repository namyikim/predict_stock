"""삼성전자 부문 분리 영업이익 추정(segment_split_v1, 2026-09-29 사용자 결정)."""
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import build_earnings_forecast as b  # noqa: E402
import forecast_utils as fu  # noqa: E402

T = 1e12


def inputs(folder, rows="2026Q2,89.2,0.3,5,6,src,note"):
    path = Path(folder) / "seg.csv"
    path.write_text("quarter,ds_krw_tn,other_krw_tn,one_off_addback_low_krw_tn,one_off_addback_high_krw_tn,source,note\n"
                    + rows + "\n", encoding="utf-8")
    frame = pd.DataFrame({"exports_krw_k": [33.13 * T, 51.32 * T, 63.52 * T]},
                         index=pd.PeriodIndex(["2026Q1", "2026Q2", "2026Q3"], freq="Q"))
    profit = pd.Series([57.2 * T, 89.5 * T], index=pd.PeriodIndex(["2026Q1", "2026Q2"], freq="Q"))
    return frame, profit, path


class SegmentSplitTests(unittest.TestCase):
    def test_reproduces_the_recorded_q3_range(self):
        with tempfile.TemporaryDirectory() as folder:
            frame, profit, path = inputs(folder)
            seg = b.segment_split_estimate(frame, profit, "2026Q3", path)
        # 하한 = (89.2+5)×1.238 + 0.3, 상한 = (89.2+6)×1.238^탄력성 + 0.3 (탄력성은 1→2분기 실질 이익/수출)
        self.assertAlmostEqual(seg["exports_ratio"], 63.52 / 51.32, places=6)
        self.assertAlmostEqual(seg["low"] / T, 94.2 * 63.52 / 51.32 + 0.3, places=3)
        self.assertAlmostEqual(seg["elasticity"], 1.158, places=2)
        self.assertTrue(116.5 < seg["low"] / T < 117.5 and 121.5 < seg["high"] / T < 122.5)

    def test_waits_for_previous_quarter_segments(self):
        with tempfile.TemporaryDirectory() as folder:
            frame, profit, path = inputs(folder, rows="2026Q1,50,7,0,0,src,note")
            seg = b.segment_split_estimate(frame, profit, "2026Q3", path)
        self.assertIn("2026Q2 부문 실적이 아직 입력되지 않았습니다", seg["reason"])
        self.assertIn("검증 전 시나리오", b.render_segment_split({"segment_split": seg}))

    def test_block_shows_range_model_and_recorded_value(self):
        with tempfile.TemporaryDirectory() as folder:
            frame, profit, path = inputs(folder)
            seg = b.segment_split_estimate(frame, profit, "2026Q3", path)
        seg["recorded"] = {"recorded_at": "2026-09-29", "low": 116.4 * T, "high": 122.0 * T}
        html = b.render_segment_split({"segment_split": seg, "point": 120.7 * T, "last_actual": 89.5 * T})
        self.assertIn("116.9 ~ 122.2조 원", html)
        self.assertIn("기존 모델 120.7", html)
        self.assertIn("발표 전에 기록한 값(2026-09-29, 116.4~122.0조)", html)

    def test_recorded_estimate_is_read_from_the_log(self):
        rec = b.recorded_segment_estimate("samsung", "2026Q3")
        self.assertEqual((rec["low"] / T, rec["high"] / T), (116.4, 122.0))

    def test_summary_card_appears_only_with_a_range(self):
        base = {"quarter": "2026년 3분기", "point": 120.7 * T, "evaluation": {"beats_baselines": True}}
        close = pd.Series(range(100, 400), dtype=float)
        with_seg = fu.longterm_easy_summary_html(name="삼성전자", price_date="2026-09-28", close=close,
                                                 earnings={**base, "segment_split": {"low": 116.9 * T, "high": 122.2 * T}})
        without = fu.longterm_easy_summary_html(name="삼성전자", price_date="2026-09-28", close=close,
                                                earnings={**base, "segment_split": {"reason": "입력 대기"}})
        self.assertIn("부문 분리 추정", with_seg)
        self.assertIn("116.9~122.2조 원", with_seg)
        self.assertNotIn("부문 분리 추정", without)


if __name__ == "__main__":
    unittest.main()
