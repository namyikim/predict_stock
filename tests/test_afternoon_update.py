"""장 마감 후 갱신: 보고서의 표시된 구간만 바꾸고, 예측은 새로 만들지 않는다."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_afternoon_update as af  # noqa: E402
import forecast_utils as fu  # noqa: E402


class SectionReplacementTests(unittest.TestCase):
    def page(self, inner="옛 표"):
        return f"<html>앞{af.MARK_START}{inner}{af.MARK_END}뒤</html>"

    def test_only_the_marked_range_changes(self):
        got = af.replace_section(self.page(), "새 표")
        self.assertEqual(got, f"<html>앞{af.MARK_START}새 표{af.MARK_END}뒤</html>")

    def test_old_report_without_markers_is_left_alone(self):
        self.assertIsNone(af.replace_section("<html>표시 없음</html>", "새 표"))

    def test_repeated_updates_do_not_stack(self):
        page = af.replace_section(self.page(), "1차")
        page = af.replace_section(page, "2차")
        self.assertEqual(page.count(af.MARK_START), 1)
        self.assertIn("2차", page)
        self.assertNotIn("1차", page)


class LedgerSectionTests(unittest.TestCase):
    def review(self):
        bars = pd.DataFrame({"open": [100., 104.], "close": [100., 99.], "adj_close": [100., 99.]},
                            index=pd.to_datetime(["2026-09-07", "2026-09-08"]))
        daily = pd.DataFrame([dict(record_id="d", run_id="r", target_date="2026-09-08", kind="direction",
                                   horizon_days=1, model="Mean ensemble", status="scored", is_prospective=True,
                                   prediction="상승", p_down=.2, p_flat=.3, p_up=.5, band=.01, actual_class=0,
                                   direction_correct=0., log_loss=-np.log(.2), actual_return=-.01,
                                   current_close=100.)])
        return fu.review_ledger(daily, bars, ensemble_model="Mean ensemble")

    def test_shared_renderer_shows_the_scored_row(self):
        html = fu.ledger_section_html(self.review(), "Mean ensemble")
        self.assertIn("어제 예측 vs 실제", html)
        self.assertIn("미적중", html)

    def test_update_note_is_placed_before_the_tables(self):
        html = fu.ledger_section_html(self.review(), "Mean ensemble", updated_note="<div>갱신 표시</div>")
        self.assertLess(html.index("갱신 표시"), html.index("<table"))


class UnclosedBarTests(unittest.TestCase):
    def test_ghost_bars_are_dropped(self):
        idx = pd.to_datetime(["2026-09-07", "2026-09-08"])
        frame = pd.DataFrame({"Open": [100., 50.], "High": [101., 50.], "Low": [99., 50.],
                              "Close": [100., 50.], "Adj Close": [100., 50.], "Volume": [1000, 0]}, index=idx)

        class FakeTicker:
            def __init__(self, *a, **k): pass
            def history(self, **k): return frame.copy()

        import yfinance
        saved = yfinance.Ticker
        yfinance.Ticker = FakeTicker
        try:
            bars = af.load_bars("005930.KS")
        finally:
            yfinance.Ticker = saved
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars.index[0].date().isoformat(), "2026-09-07")


if __name__ == "__main__":
    unittest.main()
