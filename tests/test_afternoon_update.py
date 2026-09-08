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


class ScopeTests(unittest.TestCase):
    """개장 직후(09:37) 회차는 시가만 채점한다. 장중 close는 종가가 아니다."""

    def bars(self, scope, now="2026-09-08 09:37"):
        idx = pd.to_datetime(["2026-09-07", "2026-09-08"])
        frame = pd.DataFrame({"Open": [100., 104.], "High": [101., 105.], "Low": [99., 103.],
                              "Close": [100., 104.5], "Adj Close": [100., 104.5],
                              "Volume": [1e6, 5e5]}, index=idx)

        class FakeTicker:
            def __init__(self, *a, **k): pass
            def history(self, **k): return frame.copy()

        import yfinance
        saved_ticker, saved_now = yfinance.Ticker, pd.Timestamp.now
        yfinance.Ticker = FakeTicker
        pd.Timestamp.now = classmethod(
            lambda cls, tz=None: saved_now(tz=tz) if tz is None else pd.Timestamp(now, tz="Asia/Seoul"))
        try:
            return af.load_bars("005930.KS", scope=scope)
        finally:
            yfinance.Ticker, pd.Timestamp.now = saved_ticker, saved_now

    def test_open_scope_keeps_the_open_and_drops_the_intraday_close(self):
        bars = self.bars("open")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars["open"].iloc[-1], 104.)
        self.assertTrue(np.isnan(bars["close"].iloc[-1]))

    def test_all_scope_drops_the_unfinished_bar(self):
        bars = self.bars("all")
        self.assertEqual(len(bars), 1)

    def test_after_close_all_scope_keeps_today(self):
        bars = self.bars("all", now="2026-09-08 16:10")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars["close"].iloc[-1], 104.5)


class OpenScoringTimeTests(unittest.TestCase):
    """시가는 09:00에 확정되므로 그날 오전에 채점할 수 있다. 종가는 마감 뒤라야 한다."""

    def log(self):
        common = dict(run_id="r", target_date="2026-09-08", prediction_date="2026-09-08",
                      created_at_utc="2026-09-07T22:30:00Z", current_close=100., horizon_days=1)
        return pd.DataFrame([
            dict(common, record_id="o", kind="open", model="Ridge", predicted_open=103.,
                 center_open=103., low_open=101., high_open=106., predicted_return=.03),
            dict(common, record_id="d", kind="direction", model="Mean ensemble", band=.01,
                 p_down=.2, p_flat=.3, p_up=.5, prediction="상승"),
        ])

    def bars(self):
        return pd.DataFrame({"open": [100., 104.], "high": [101., np.nan], "low": [99., np.nan],
                             "close": [100., np.nan], "adj_close": [100., np.nan],
                             "volume": [1e6, 1e6]},
                            index=pd.to_datetime(["2026-09-07", "2026-09-08"]))

    def status_at(self, when):
        got = fu.evaluate_forecasts(self.log(), self.bars(), now=when)
        return dict(zip(got["kind"], got["status"]))

    def test_open_is_scored_after_the_bell_but_the_close_is_not(self):
        self.assertEqual(self.status_at("2026-09-08T00:30:00Z"),   # 09:30 KST
                         {"open": "scored", "direction": "pending"})

    def test_nothing_is_scored_before_the_bell(self):
        self.assertEqual(self.status_at("2026-09-07T23:50:00Z"),   # 08:50 KST
                         {"open": "pending", "direction": "pending"})


class WorkflowStepTests(unittest.TestCase):
    """`if: steps.X.outputs...` 가 있으면 그 X 스텝이 실제로 있어야 한다.

    2026-09-08에 경량화하면서 gate 스텝을 지웠는데 조건은 남겨, 워크플로가 초록불로 끝나면서
    채점을 한 번도 하지 않았다(원장에 score 커밋이 전무했다). 조용한 실패라 눈치채기 어렵다.
    """

    def test_every_step_condition_refers_to_an_existing_step(self):
        import re
        import yaml
        root = Path(__file__).resolve().parents[1]
        for path in sorted((root / ".github" / "workflows").glob("*.yml")):
            workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
            for job_name, job in (workflow.get("jobs") or {}).items():
                ids = {s.get("id") for s in job.get("steps", []) if s.get("id")}
                for step in job.get("steps", []):
                    for ref in re.findall(r"steps\.([A-Za-z0-9_-]+)\.outputs", str(step.get("if", ""))):
                        self.assertIn(ref, ids, f"{path.name}:{job_name} — 없는 스텝 '{ref}'를 참조합니다")
