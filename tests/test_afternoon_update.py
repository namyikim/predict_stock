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
import github_pages  # noqa: E402,F401
import forecast_utils as fu  # noqa: E402
import report_html  # noqa: E402,F401


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
        self.assertIn("예측 vs 실제", html)
        self.assertIn("2026-09-08", html)      # 제목에 채점 대상일이 들어간다
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


class PublishRetryTests(unittest.TestCase):
    """여러 워크플로가 같은 파일을 건드린다. sha 충돌(409)은 재시도로 넘긴다."""

    def setUp(self):
        import github_pages as gp
        import time
        self.gp = gp
        self.saved_api, self.saved_sleep = gp._api, time.sleep
        time.sleep = lambda s: None
        self.addCleanup(self._restore)

    def _restore(self):
        import time
        self.gp._api, time.sleep = self.saved_api, self.saved_sleep

    def api(self, put_results):
        calls = {"put": 0}

        def fake(path, tok, method="GET", body=None):
            if method == "GET":
                return {"sha": "abc"}
            outcome = put_results[min(calls["put"], len(put_results) - 1)]
            calls["put"] += 1
            if isinstance(outcome, int):
                error = OSError("conflict")
                error.code = outcome
                raise error
            return {"content": {"sha": "deadbeef1234"}}
        self.gp._api = fake
        return calls

    def test_conflict_is_retried_with_a_fresh_sha(self):
        calls = self.api([409, 409, "ok"])
        self.assertEqual(self.gp.publish("p", "t", "k", "m"), "deadbee")
        self.assertEqual(calls["put"], 3)

    def test_gives_up_with_a_clear_message(self):
        self.api([409])
        with self.assertRaises(RuntimeError) as ctx:
            self.gp.publish("p", "t", "k", "m")
        self.assertIn("409", str(ctx.exception))

    def test_other_errors_are_not_retried(self):
        calls = self.api([403])
        with self.assertRaises(RuntimeError):
            self.gp.publish("p", "t", "k", "m")
        self.assertEqual(calls["put"], 1)


class RowOrderTests(unittest.TestCase):
    """표는 하루의 시간 순서대로 놓는다: 09:00 시초가 → 15:30 종가."""

    def test_open_row_comes_before_close_rows(self):
        import re
        bars = pd.DataFrame({"open": [100., 104.], "close": [100., 99.], "adj_close": [100., 99.]},
                            index=pd.to_datetime(["2026-09-07", "2026-09-08"]))
        common = dict(run_id="r", target_date="2026-09-08", status="scored",
                      is_prospective=True, horizon_days=1, current_close=100.)
        daily = pd.DataFrame([
            dict(common, record_id="d", kind="direction", model="Mean ensemble", prediction="상승",
                 p_down=.2, p_flat=.3, p_up=.5, band=.01, actual_class=0, direction_correct=0.,
                 log_loss=1.6, actual_return=-.01),
            dict(common, record_id="o", kind="open", model="Ridge", predicted_open=103.,
                 center_open=103., low_open=101., high_open=106., predicted_return=.03,
                 actual_open=104., interval_hit=1., return_error=-.01, actual_return=.04),
            dict(common, record_id="p", kind="price", model="Ridge", predicted_close=101.,
                 center_close=101., low_close=98., high_close=104., predicted_return=.01,
                 actual_close=99., interval_hit=1., actual_return=-.01),
        ])
        review = fu.review_ledger(daily, bars, ensemble_model="Mean ensemble")
        html = fu.ledger_section_html(review, "Mean ensemble")
        labels = re.findall(r'border-top:1px solid #eee">([^<]+)</td>', html)[:3]
        self.assertEqual(labels, ["시초가(갭)", "종가 방향", "1거래일 종가예측"])


class CounterSecurityTests(unittest.TestCase):
    """방문자 해시 비밀값이 없으면 집계하지 않는다(공개된 고정 문자열로 해시하지 않는다)."""

    def test_worker_refuses_without_a_salt(self):
        source = (Path(__file__).resolve().parents[1] / "counter" / "worker.js").read_text(encoding="utf-8")
        self.assertNotIn('"no-salt"', source)
        self.assertIn("if (!env.VISITOR_SALT)", source)
        self.assertIn("VISITOR_SALT가 설정되지 않았습니다", source)


class ScoredDateInTitleTests(unittest.TestCase):
    """제목에 채점 대상일이 없으면 09:37 회차인지 16:10 회차인지, 휴장일을 건너뛴 것인지 알 수 없다."""

    def review(self, target_date="2026-09-08"):
        bars = pd.DataFrame({"open": [100., 104.], "close": [100., 99.], "adj_close": [100., 99.]},
                            index=pd.to_datetime(["2026-09-07", "2026-09-08"]))
        common = dict(run_id="r", target_date=target_date, status="scored", is_prospective=True,
                      horizon_days=1, current_close=100.)
        daily = pd.DataFrame([
            dict(common, record_id="o", kind="open", model="Ridge", predicted_open=103.,
                 center_open=103., low_open=101., high_open=106., predicted_return=.03,
                 actual_open=104., interval_hit=1., return_error=-.01, actual_return=.04),
            dict(common, record_id="d", kind="direction", model="Mean ensemble", prediction="상승",
                 p_down=.2, p_flat=.3, p_up=.5, band=.01, actual_class=0, direction_correct=0.,
                 log_loss=1.6, actual_return=-.01),
        ])
        return fu.review_ledger(daily, bars, ensemble_model="Mean ensemble")

    def title(self, html):
        import re
        return re.search(r'border-bottom:1px solid #ddd">(.*?)<span', html).group(1).strip()

    def test_title_carries_the_scored_date_and_weekday(self):
        html = fu.ledger_section_html(self.review(), "Mean ensemble")
        self.assertEqual(self.title(html), "2026-09-08 (화) 예측 vs 실제")

    def test_title_says_pending_when_nothing_is_scored(self):
        empty = {"n_scored_days": 0, "latest": pd.DataFrame(), "rolling": pd.DataFrame(),
                 "alerts": [], "latest_date": None}
        self.assertIn("채점 대기", self.title(fu.ledger_section_html(empty, "Mean ensemble")))

    def test_metals_report_also_dates_its_section(self):
        source = (Path(__file__).resolve().parents[1] / "tools" / "build_metals_report.py").read_text(encoding="utf-8")
        self.assertIn('_label}예측 vs 실제', source)
        self.assertNotIn(">어제 예측 vs 실제", source)


class PendingStatusTests(unittest.TestCase):
    """오후에 보면 시초가는 채점됐지만 종가는 아직이다. 판정 전 항목은 그렇게 적는다."""

    def rows(self, td, o, d, p):
        c = dict(run_id="r", target_date=td, is_prospective=True, horizon_days=1, current_close=100.)
        return [
            dict(c, record_id=f"o{td}", kind="open", model="Ridge", status=o, predicted_open=103.,
                 center_open=103., low_open=101., high_open=106., predicted_return=.03,
                 actual_open=104. if o == "scored" else np.nan,
                 interval_hit=1. if o == "scored" else np.nan, return_error=-.01, actual_return=.04),
            dict(c, record_id=f"d{td}", kind="direction", model="Mean ensemble", status=d,
                 prediction="상승", p_down=.2, p_flat=.3, p_up=.5, band=.01,
                 actual_class=0 if d == "scored" else np.nan,
                 direction_correct=0. if d == "scored" else np.nan, log_loss=1.6, actual_return=-.01),
            dict(c, record_id=f"p{td}", kind="price", model="Ridge", status=p, predicted_close=101.,
                 center_close=101., low_close=98., high_close=104., predicted_return=.01,
                 actual_close=99. if p == "scored" else np.nan,
                 interval_hit=1. if p == "scored" else np.nan, actual_return=-.01),
        ]

    def bars(self):
        return pd.DataFrame({"open": [100., 104., 105.], "close": [100., 99., np.nan],
                             "adj_close": [100., 99., np.nan]},
                            index=pd.to_datetime(["2026-09-07", "2026-09-08", "2026-09-09"]))

    def test_afternoon_shows_today_partially_and_yesterday_fully(self):
        daily = pd.DataFrame(self.rows("2026-09-08", "scored", "scored", "scored")
                             + self.rows("2026-09-09", "scored", "pending", "pending"))
        html = fu.ledger_section_html(fu.review_ledger(daily, self.bars(), ensemble_model="Mean ensemble"),
                                      "Mean ensemble")
        self.assertIn("2026-09-09 (수) 오늘 예측 — 채점 상태", html)
        self.assertIn("채점됨 — 실제 시가 104원 (+4.00%)", html)
        self.assertEqual(html.count("판정 전 — 16:10 장 마감 후 회차에 채점"), 2)
        # 아래 표는 종가까지 채점된 어제로 남는다(오늘로 바뀌면 어제 종가 결과가 사라진다).
        self.assertIn("2026-09-08 (화) 예측 vs 실제", html)
        self.assertIn("미적중", html)

    def test_morning_shows_all_pending(self):
        daily = pd.DataFrame(self.rows("2026-09-08", "scored", "scored", "scored")
                             + self.rows("2026-09-09", "pending", "pending", "pending"))
        html = fu.ledger_section_html(fu.review_ledger(daily, self.bars(), ensemble_model="Mean ensemble"),
                                      "Mean ensemble")
        self.assertIn("판정 전 — 09:37 시초가 확인 회차에 채점", html)
        self.assertEqual(html.count("판정 전"), 3)

    def test_no_block_when_everything_is_scored(self):
        daily = pd.DataFrame(self.rows("2026-09-08", "scored", "scored", "scored"))
        html = fu.ledger_section_html(fu.review_ledger(daily, self.bars(), ensemble_model="Mean ensemble"),
                                      "Mean ensemble")
        self.assertNotIn("채점 상태", html)


class FlowChartLayoutTests(unittest.TestCase):
    """일별 막대가 제목 글자를 덮지 않아야 한다."""

    def test_bars_stay_inside_the_plot_even_on_an_extreme_day(self):
        import re
        import report_html as rh
        days = pd.bdate_range("2026-06-01", "2026-09-08")
        net = np.random.default_rng(1).normal(0, 2e6, len(days))
        net[-3] = 3e7                                    # 하루 유난히 큰 날
        flows = pd.DataFrame({"date": days, "foreign_net": net, "inst_net": 0., "indiv_net": np.nan,
                              "volume": 2e7, "foreign_ratio": 50.})
        html = rh.flow_section_html(flows, {"source": "t"}, True, pd.Series(70000., index=days),
                                    days[-1], pd.DataFrame())
        svg = re.search(r"<svg.*?</svg>", html, re.S).group()
        tops = [float(m) for m in re.findall(
            r'<rect x="[\d.]+" y="([\d.]+)" width="[\d.]+" height="[\d.]+" fill="#(?:4c78a8|b5453c)"', svg)]
        self.assertGreaterEqual(min(tops), 40, "막대가 그림 영역(TOP=40) 위로 올라갔습니다")
        self.assertIn('clip-path="url(#flowplot)"', svg)
