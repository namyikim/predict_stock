# -*- coding: utf-8 -*-
"""D램 현물가: DRAMeXchange 첫 페이지 스냅샷을 매일 누적한다."""
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import macro_utils as mu  # noqa: E402
from data_sources import dram_spot as ds  # noqa: E402

HTML = '''<tbody id="tb_NationalDramSpotPrice">
<tr><td class="tab_title">Item</td><td>Daily High</td><td>Daily Low</td><td>Session High</td>
<td>Session Low</td><td>Session Average</td><td>Session Change</td><td>History</td></tr>
<tr><td class="tab_tr_gray2"><a href="/Price/Dram_Spot"><img src="x"> DDR5 16Gb (2Gx8) 4800/5600 </a></td>
<td>66.00</td><td>38.50</td><td>66.00</td><td>39.00</td><td>54.333</td><td><img src="d"><br>-0.31 %</td><td></td></tr>
<tr><td class="tab_tr_gray2"><a href="#"> DDR4 16Gb (2Gx8) 3200 </a></td><td>115</td><td>41</td><td>115</td>
<td>41</td><td>87.354</td><td>0.56 %</td><td></td></tr>
<tr><td class="tab_tr_gray2"><a href="#"> DDR4 8Gb (1Gx8) 3200 </a></td><td>74.5</td><td>21</td><td>74.5</td>
<td>21</td><td>42.610</td><td>0.25 %</td><td></td></tr>
<tr><td class="tab_tr_gray2"><a href="#"> DDR3 4Gb 512Mx8 1600/1866 </a></td><td>20.5</td><td>9.6</td><td>20.5</td>
<td>9.6</td><td>13.982</td><td>-0.30 %</td><td></td></tr>
</tbody>'''


class ParserTests(unittest.TestCase):
    def test_reads_session_average_for_tracked_items(self):
        got = ds.parse_dramexchange_spot(HTML)
        self.assertEqual(got, {"ddr5_16gb": 54.333, "ddr4_16gb": 87.354, "ddr4_8gb": 42.61})

    def test_untracked_items_are_ignored(self):
        self.assertNotIn("ddr3", str(ds.parse_dramexchange_spot(HTML)))

    def test_missing_table_is_an_error(self):
        with self.assertRaises(ValueError):
            ds.parse_dramexchange_spot("<html>nothing</html>")

    def test_renamed_items_are_an_error_not_silent_zeros(self):
        with self.assertRaises(ValueError):
            ds.parse_dramexchange_spot(HTML.replace("DDR5 16Gb", "DDR9 99Gb")
                                        .replace("DDR4 16Gb", "X").replace("DDR4 8Gb", "Y"))


class AccumulationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.fb = self.root / "fb"
        self.fb.mkdir()
        pd.DataFrame({"date": pd.bdate_range("2026-09-01", periods=5).strftime("%Y-%m-%d"),
                      "ddr5_16gb": [50, 51, 52, 53, 54], "ddr4_16gb": [80, 82, 84, 86, 88],
                      "ddr4_8gb": [40, 41, 42, 43, 44]}).to_csv(self.fb / "dram_spot.csv", index=False)
        self.saved = ds.fetch_dram_spot
        self.addCleanup(lambda: setattr(ds, "fetch_dram_spot", self.saved))

    def test_today_is_appended_to_the_stored_history(self):
        ds.fetch_dram_spot = lambda *a, **k: pd.DataFrame(
            [{"date": pd.Timestamp("2026-09-11"), "ddr5_16gb": 54.333, "ddr4_16gb": 87.354, "ddr4_8gb": 42.61}])
        frame, info = mu.load_dram_spot(self.root, fallback_dir=self.fb)
        self.assertEqual(info["source"], "DRAMEXCHANGE+cache")
        self.assertEqual((info["rows"], info["last"]), (6, "2026-09-11"))
        self.assertTrue(info["fresh"])

    def test_same_day_refetch_replaces_not_duplicates(self):
        ds.fetch_dram_spot = lambda *a, **k: pd.DataFrame(
            [{"date": pd.Timestamp("2026-09-07"), "ddr5_16gb": 99.0, "ddr4_16gb": 1.0, "ddr4_8gb": 1.0}])
        frame, _ = mu.load_dram_spot(self.root, fallback_dir=self.fb)
        self.assertEqual(len(frame), 5)
        self.assertEqual(float(frame.set_index("date").loc["2026-09-07", "ddr5_16gb"]), 99.0)

    def test_fetch_failure_falls_back_to_history(self):
        def boom(*a, **k):
            raise RuntimeError("차단")
        ds.fetch_dram_spot = boom
        _, info = mu.load_dram_spot(self.root, fallback_dir=self.fb)
        self.assertEqual(info["source"], "last_successful_fetch")
        self.assertFalse(info["fresh"])

    def test_nothing_available_raises(self):
        def boom(*a, **k):
            raise RuntimeError("차단")
        ds.fetch_dram_spot = boom
        with self.assertRaises(RuntimeError):
            mu.load_dram_spot(Path(tempfile.mkdtemp()))


class SummaryTests(unittest.TestCase):
    def test_short_history_reports_none_for_unavailable_changes(self):
        frame = pd.DataFrame({"date": pd.to_datetime(["2026-09-01", "2026-09-11"]),
                              "ddr5_16gb": [50.0, 55.0]})
        got = mu.dram_spot_summary(frame)
        self.assertAlmostEqual(got["change_7d"], 0.10)   # 9/1 값 대비
        self.assertIsNone(got["change_30d"])              # 한 달 전 값이 없다
        self.assertEqual(got["days"], 2)

    def test_missing_item_returns_none(self):
        self.assertIsNone(mu.dram_spot_summary(pd.DataFrame({"date": [], "ddr5_16gb": []})))


class ReportGuardTests(unittest.TestCase):
    def test_dram_is_displayed_but_not_yet_a_feature(self):
        source = (ROOT / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        self.assertIn("D램 현물 가격", source)
        self.assertIn("특징으로 넣지 않습니다", source)
        # FEATURES 목록에 들어가면 안 된다(이력이 검증에 충분해지기 전까지).
        self.assertNotIn("dram", source[source.index("FEATURES = ["):source.index("FEATURES = [") + 200].lower())
