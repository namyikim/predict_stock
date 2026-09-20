# -*- coding: utf-8 -*-
"""P16 운영 반영 — 시가 반영(09:37) 종가 방향 갱신의 배선.

정직 규칙을 기계로 고정한다.
  1. 격자 보간: 안쪽은 선형, 격자점은 그대로, 밖은 끝값 고정 + clamped 표시, 합은 1.
  2. 격자의 target_date 가 오늘 세션이 아니거나 오늘 봉에 시가가 없으면 행을 만들지 않는다.
  3. 원장 행은 information_cutoff=post_open, created_at_utc 는 실제 실행 시각, 기존 행은 바이트 하나 안 바뀐다.
  4. 채점: post_open 행은 15:30 KST 전에 만든 것만 사전 예측, pre_open 행은 09:00 규칙 그대로.
  5. Post-open 은 대표 집계(is_headline_model)에서 빠진다.
  6. 보고서: 07:00 결과와 시가 반영 결과가 따로 보이고, 정보 마감 표시가 붙는다.
  7. 노트북: post_open_live_models 학습·격자 저장·자리 표시.
"""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import forecast_utils as fu  # noqa: E402
import build_afternoon_update as af  # noqa: E402


def _grid(target="2026-09-21", band=.01):
    gaps = np.asarray(fu.POST_OPEN_GRID_GAPS)
    # 갭이 클수록 상승 확률이 커지는 단조 격자(보간 검사가 쉬운 형태)
    p_up = np.clip(.5 + 3. * gaps, .05, .9)
    p_down = np.clip(.5 - 3. * gaps, .05, .9)
    p_flat = np.clip(1 - p_up - p_down, .05, .9)
    rule = fu.gap_rule_labels(gaps, np.full(len(gaps), band))
    return pd.DataFrame({"target_date": target, "gap": gaps, "p_down": p_down, "p_flat": p_flat, "p_up": p_up,
                         "band": band, "gap_rule_label": [{0: "하락", 1: "보합", 2: "상승"}[int(r)] for r in rule]})


class GridInterpolationTests(unittest.TestCase):
    """계약 1."""

    def test_exact_grid_point_is_returned_as_is_after_renormalisation(self):
        grid = _grid()
        row = grid.iloc[120]
        out = fu.interpolate_post_open_grid(grid, row["gap"])
        total = row["p_down"] + row["p_flat"] + row["p_up"]
        for key in ("p_down", "p_flat", "p_up"):
            self.assertAlmostEqual(out[key], row[key] / total, places=9)
        self.assertFalse(out["clamped"])

    def test_interior_point_is_linear_between_neighbours(self):
        grid = _grid()
        a, b = grid.iloc[100], grid.iloc[101]              # 갭 0.000 과 0.001
        out = fu.interpolate_post_open_grid(grid, .00025)
        expect = np.array([(a[c] + (b[c] - a[c]) * .25) for c in ("p_down", "p_flat", "p_up")])
        expect = expect / expect.sum()
        np.testing.assert_allclose([out["p_down"], out["p_flat"], out["p_up"]], expect, rtol=1e-9)

    def test_outside_the_grid_is_clamped_and_flagged(self):
        grid = _grid()
        out = fu.interpolate_post_open_grid(grid, .25)
        edge = fu.interpolate_post_open_grid(grid, .10)
        self.assertTrue(out["clamped"])
        self.assertFalse(edge["clamped"])
        for key in ("p_down", "p_flat", "p_up"):
            self.assertAlmostEqual(out[key], edge[key])

    def test_probabilities_sum_to_one_and_rule_label_follows_the_band(self):
        grid = _grid(band=.01)
        for gap in (-.05, -.004, 0., .004, .05):
            out = fu.interpolate_post_open_grid(grid, gap)
            self.assertAlmostEqual(out["p_down"] + out["p_flat"] + out["p_up"], 1.)
            self.assertEqual(out["gap_rule_label"], "상승" if gap > .01 else "하락" if gap < -.01 else "보합")

    def test_build_grid_uses_the_shared_group_g_definition(self):
        """격자의 그룹 G 는 과거 행과 같은 함수(_gap_group_g)로 만든다 — 열 순서·정의가 같다."""
        rng = np.random.default_rng(0)
        n = 500
        X = rng.normal(0, 1, (n, 3)).astype(np.float32)
        g = rng.normal(0, .01, n)
        G = fu._gap_group_g(pd.Series(g), pd.Series(np.full(n, .01)), pd.Series(np.zeros(n)), pd.Series(np.full(n, .01)))
        y = np.where(g > .01, 2, np.where(g < -.01, 0, 1))
        fitted = fu.fit_direction_model(np.hstack([X, G.to_numpy(dtype=np.float32)]), y, np.arange(n), "Logistic")
        grid = fu.build_post_open_grid({"Logistic": fitted}, X[-1], .01, 0., .01, "2026-09-21")
        self.assertEqual(list(grid.columns), list(fu.POST_OPEN_GRID_COLUMNS))
        self.assertEqual(len(grid), 201)
        self.assertEqual(set(grid["target_date"]), {"2026-09-21"})
        np.testing.assert_allclose(grid[["p_down", "p_flat", "p_up"]].sum(axis=1), 1.)
        # 갭이 크게 양수면 상승, 크게 음수면 하락 — 격자가 갭을 실제로 읽는다.
        self.assertGreater(grid["p_up"].iloc[-1], grid["p_up"].iloc[0])
        self.assertEqual(grid["gap_rule_label"].iloc[0], "하락")
        self.assertEqual(grid["gap_rule_label"].iloc[100], "보합")


def _bars(with_open=True):
    idx = pd.to_datetime(["2026-09-17", "2026-09-18", "2026-09-21"])
    return pd.DataFrame({"open": [100., 101., 104. if with_open else np.nan], "high": [101., 102., np.nan],
                         "low": [99., 100., np.nan], "close": [100., 102., np.nan],
                         "adj_close": [100., 102., np.nan], "volume": [1e6, 1e6, 1e5]}, index=idx)


def _ledger_csv():
    common = dict(run_id="r0921", target_date="2026-09-21", prediction_date="2026-09-21", as_of_date="2026-09-18",
                  created_at_utc="2026-09-20T21:30:00+00:00", current_close=102., horizon_days=1,
                  target_mode="close_to_close", config_hash="h")
    rows = [dict(common, record_id="o", kind="open", model="Ridge", predicted_open=103., center_open=103.,
                 low_open=101., high_open=106., predicted_return=.01),
            dict(common, record_id="d", kind="direction", band=.01, model="No macro ensemble",
                 p_down=.5, p_flat=.3, p_up=.2, prediction="하락")]
    return pd.DataFrame(rows).to_csv(index=False)


class LedgerRowTests(unittest.TestCase):
    """계약 2·3."""

    NOW = pd.Timestamp("2026-09-21 09:40", tz="Asia/Seoul")

    def test_stale_grid_is_refused(self):
        row, why = fu.post_open_ledger_row(_grid(target="2026-09-18"), "2026-09-21", 104., 102., "2026-09-18", self.NOW)
        self.assertIsNone(row)
        self.assertIn("2026-09-18", why)

    def test_missing_open_is_refused(self):
        row, why = fu.post_open_ledger_row(_grid(), "2026-09-21", np.nan, 102., "2026-09-18", self.NOW)
        self.assertIsNone(row)
        self.assertIn("시가", why)

    def test_row_carries_the_cutoff_the_real_time_and_the_gap(self):
        row, _ = fu.post_open_ledger_row(_grid(), "2026-09-21", 104., 102., "2026-09-18",
                                         pd.Timestamp("2026-09-21 14:10", tz="Asia/Seoul"))
        self.assertEqual(row["model"], "Post-open")
        self.assertEqual(row["information_cutoff"], "post_open")
        self.assertEqual((row["kind"], row["horizon_days"], row["target_date"], row["as_of_date"]),
                         ("direction", 1, "2026-09-21", "2026-09-18"))
        self.assertEqual(row["created_at_utc"], "2026-09-21T05:10:00+00:00")     # 14:10 KST 그대로
        self.assertAlmostEqual(row["gap"], 104. / 102. - 1)
        self.assertEqual(row["gap_rule_label"], "상승")
        self.assertEqual(row["prediction"], "상승")
        self.assertAlmostEqual(row["p_down"] + row["p_flat"] + row["p_up"], 1.)
        self.assertTrue(row["record_id"].endswith(":direction:Post-open"))

    def test_append_leaves_existing_rows_byte_identical_and_adds_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "forecast_log.csv"
            path.write_text(_ledger_csv(), encoding="utf-8")
            before = pd.read_csv(path)
            row, why = af.append_post_open(path, _grid(), _bars(), self.NOW)
            self.assertIsNotNone(row, why)
            after = pd.read_csv(path)
            self.assertEqual(len(after), len(before) + 1)
            pd.testing.assert_frame_equal(after.iloc[:len(before)][before.columns], before)
            self.assertEqual(after["information_cutoff"].iloc[-1], "post_open")
            # 두 번째 호출은 중복을 만들지 않는다.
            again, why = af.append_post_open(path, _grid(), _bars(), self.NOW)
            self.assertIsNone(again)
            self.assertIn("이미", why)
            self.assertEqual(len(pd.read_csv(path)), len(before) + 1)

    def test_tool_skips_without_todays_bar_or_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "forecast_log.csv"
            path.write_text(_ledger_csv(), encoding="utf-8")
            row, why = af.append_post_open(path, _grid(), _bars().iloc[:-1], self.NOW)
            self.assertIsNone(row)
            self.assertIn("봉이 아직 없습니다", why)
            row, why = af.append_post_open(path, None, _bars(), self.NOW)
            self.assertIsNone(row)
            self.assertIn("post_open_grid.csv", why)
            row, why = af.append_post_open(path, _grid(), _bars(with_open=False), self.NOW)
            self.assertIsNone(row)
            self.assertEqual(pd.read_csv(path).shape[0], 2)


class EvaluateCutoffTests(unittest.TestCase):
    """계약 4."""

    def log(self, created_post, created_pre="2026-09-21T02:00:00+00:00"):
        common = dict(run_id="r", target_date="2026-09-21", prediction_date="2026-09-21", as_of_date="2026-09-18",
                      current_close=102., horizon_days=1, target_mode="close_to_close", band=.01, kind="direction",
                      p_down=.2, p_flat=.3, p_up=.5, prediction="상승")
        return pd.DataFrame([
            dict(common, record_id="pre", model="No macro ensemble", created_at_utc=created_pre),
            dict(common, record_id="post", model="Post-open", created_at_utc=created_post,
                 information_cutoff="post_open"),
        ])

    def bars(self):
        return pd.DataFrame({"open": [100., 101., 104.], "close": [100., 102., 105.], "adj_close": [100., 102., 105.]},
                            index=pd.to_datetime(["2026-09-17", "2026-09-18", "2026-09-21"]))

    def test_post_open_row_made_at_0940_is_prospective_and_one_made_at_1545_is_not(self):
        for created, expected in (("2026-09-21T00:40:00+00:00", True), ("2026-09-21T06:45:00+00:00", False)):
            got = fu.evaluate_forecasts(self.log(created), self.bars(), now="2026-09-21T07:10:00+00:00")
            by = got.set_index("record_id")
            self.assertEqual(bool(by.loc["post", "is_prospective"]), expected, created)
            self.assertEqual(by.loc["post", "status"], "scored")
            self.assertEqual(by.loc["post", "actual_class"], 2)

    def test_pre_open_rows_keep_the_0900_rule(self):
        got = fu.evaluate_forecasts(self.log("2026-09-21T00:40:00+00:00"), self.bars(), now="2026-09-21T07:10:00+00:00")
        by = got.set_index("record_id")
        self.assertFalse(bool(by.loc["pre", "is_prospective"]))      # 11:00 KST 에 만든 07:00 행은 사전 예측이 아니다
        got = fu.evaluate_forecasts(self.log("2026-09-21T00:40:00+00:00", created_pre="2026-09-20T22:00:00+00:00"),
                                    self.bars(), now="2026-09-21T07:10:00+00:00")
        self.assertTrue(bool(got.set_index("record_id").loc["pre", "is_prospective"]))

    def test_daily_comparison_keeps_both_rows_separately(self):
        got = fu.evaluate_forecasts(self.log("2026-09-21T00:40:00+00:00", created_pre="2026-09-20T22:00:00+00:00"),
                                    self.bars(), now="2026-09-21T07:10:00+00:00")
        daily = fu.daily_comparison(got)
        self.assertEqual(sorted(daily["model"]), ["No macro ensemble", "Post-open"])


class HeadlineIsolationTests(unittest.TestCase):
    """계약 5."""

    def test_is_headline_model(self):
        s = pd.Series(["No macro ensemble", "Ridge", "Candidate expanding", "Post-open", "Mean ensemble"])
        self.assertEqual(list(fu.is_headline_model(s)), [True, True, False, False, True])
        self.assertTrue(fu.is_headline_model("Ridge"))
        self.assertFalse(fu.is_headline_model("Post-open"))
        self.assertFalse(fu.is_headline_model("Candidate evening open"))

    def test_no_startswith_candidate_left_in_forecast_utils(self):
        source = (ROOT / "forecast_utils.py").read_text(encoding="utf-8")
        body = source.split("def direction_call", 1)[1]          # is_headline_model 정의 뒤
        self.assertNotIn('str.startswith("Candidate")', body)

    def test_review_ledger_keeps_post_open_out_of_the_headline_rows(self):
        review = _scored_review()
        roll = review["rolling"]
        head = roll[(roll["kind"] == "direction") & (roll["window"] == 60)].iloc[0]
        post = roll[(roll["kind"] == "direction_post_open") & (roll["window"] == 60)].iloc[0]
        self.assertEqual((int(head["n"]), head["hit_rate"]), (1, 0.))      # 대표 행에는 07:00 만
        self.assertEqual((int(post["n"]), post["hit_rate"]), (1, 1.))      # 시가 반영은 따로
        self.assertEqual(post["information_cutoff"], "post_open")


def _scored_review():
    """같은 날 07:00 행(틀림)과 Post-open 행(맞음)이 채점된 review."""
    if True:
        bars = pd.DataFrame({"open": [100., 104.], "close": [100., 105.], "adj_close": [100., 105.]},
                            index=pd.to_datetime(["2026-09-18", "2026-09-21"]))
        common = dict(run_id="r", target_date="2026-09-21", status="scored", is_prospective=True, horizon_days=1,
                      current_close=100., kind="direction", band=.01, actual_class=2, actual_return=.05)
        daily = pd.DataFrame([
            dict(common, record_id="d", model="No macro ensemble", prediction="하락", p_down=.5, p_flat=.3, p_up=.2,
                 direction_correct=0., log_loss=-np.log(.2), created_at_utc="2026-09-20T22:00:00+00:00"),
            dict(common, record_id="p", model="Post-open", prediction="상승", p_down=.2, p_flat=.3, p_up=.5,
                 direction_correct=1., log_loss=-np.log(.5), created_at_utc="2026-09-21T00:40:00+00:00",
                 information_cutoff="post_open", gap=.04, gap_rule_label="상승", open_price=104.),
        ])
        return fu.review_ledger(daily, bars, ensemble_model="No macro ensemble")


class RenderingTests(unittest.TestCase):
    """계약 6."""

    def review(self):
        return _scored_review()

    def test_scorecard_shows_both_results_on_separate_lines(self):
        card = fu.scorecard_html(self.review(), "No macro ensemble")
        self.assertIn("예측 하락 → 실제 상승", card)                    # 07:00 결과
        self.assertIn("시가 반영 갱신(실제 실행 09:40)", card)              # 시가 반영 결과, 실제 시각
        self.assertIn("정보 마감 15:30 이전", card)
        self.assertIn(">틀림</span>", card)
        self.assertIn(">맞음</span>", card)
        self.assertIn("시가 반영 갱신 적중률", card)
        self.assertIn("07:00 과 다른 질문", card)

    def test_ledger_section_has_a_separate_post_open_row_and_rolling_line(self):
        html = fu.ledger_section_html(self.review(), "No macro ensemble")
        self.assertIn("종가 방향 · 시가 반영 갱신", html)
        self.assertIn("실제 실행 09:40 · 정보 마감 15:30 이전", html)
        self.assertIn("시가 반영 갱신 방향 · 정보 마감 15:30 이전", html)
        self.assertIn("대표 성적과 합치지 않음", html)
        self.assertIn("갭 +4.00%", html)

    def test_easy_summary_emits_the_placeholder_between_cards_and_scorecard(self):
        html = fu.easy_summary_html(
            name="삼성전자", prediction_date=pd.Timestamp("2026-09-21"), data_date=pd.Timestamp("2026-09-18"),
            summary={"live": {"p_up": .2, "p_flat": .3, "p_down": .5}, "ensemble": "No macro ensemble"},
            open_forecast={"signal": "있음", "predicted_open": 103.}, price_forecasts=[], review={})
        self.assertEqual(html.count(fu.POSTOPEN_START), 1)
        self.assertEqual(html.count(fu.POSTOPEN_END), 1)
        self.assertIn("09:37 갱신", html)
        self.assertIn("07:00 예측은 그대로 남습니다", html)
        self.assertLess(html.index("종가 · 15:30"), html.index(fu.POSTOPEN_START))
        self.assertLess(html.index(fu.POSTOPEN_END), html.index(fu.SCORECARD_START))

    def test_easy_summary_renders_the_card_when_the_row_exists(self):
        row, _ = fu.post_open_ledger_row(_grid(), "2026-09-21", 250_100., 249_100., "2026-09-18",
                                         pd.Timestamp("2026-09-21 09:40", tz="Asia/Seoul"))
        html = fu.easy_summary_html(
            name="삼성전자", prediction_date=pd.Timestamp("2026-09-21"), data_date=pd.Timestamp("2026-09-18"),
            summary={"live": {"p_up": .2, "p_flat": .3, "p_down": .5}, "ensemble": "No macro ensemble"},
            open_forecast={"signal": "있음", "predicted_open": 103.}, price_forecasts=[], review={},
            post_open=row, target="samsung")
        self.assertNotIn("09:37 갱신 —", html)
        self.assertIn("시가 반영 갱신", html)
        self.assertIn("실제 실행 09:40 KST", html)
        self.assertIn("시가 250,100원(갭 +0.40%)", html)
        self.assertIn("갭 규칙만으로도 ‘보합’", html)      # 밴드 1% 안이라 규칙은 보합
        self.assertIn("07:00 예측(‘하락’ 50%)은 위 카드 그대로", html)
        self.assertIn("모델이 좋아진 것이 아니라 정보가 늘어난 것입니다", html)
        self.assertIn("07:00 45%·시가 반영 56%, 갭 부호만 읽어도 54%", html)

    def test_card_flags_a_late_run_and_a_clamped_gap(self):
        row, _ = fu.post_open_ledger_row(_grid(), "2026-09-21", 130., 100., "2026-09-18",
                                         pd.Timestamp("2026-09-21 14:10", tz="Asia/Seoul"))
        card = fu.post_open_card_html(row, target="sk_hynix")
        self.assertIn("실제 실행 14:10 KST · 예정 09:37 보다 늦게 실행됨", card)
        self.assertIn("격자(±10%) 밖", card)
        self.assertIn("07:00 50%·시가 반영 58%", card)

    def test_replace_section_swaps_only_the_marked_block(self):
        page = f"<p>앞</p>{fu.POSTOPEN_START}자리{fu.POSTOPEN_END}<p>뒤</p>"
        out = af.replace_section(page, "카드", fu.POSTOPEN_START, fu.POSTOPEN_END)
        self.assertEqual(out, f"<p>앞</p>{fu.POSTOPEN_START}카드{fu.POSTOPEN_END}<p>뒤</p>")


class ToolEndToEndTests(unittest.TestCase):
    """09:37 도구 종단: open 회차만 행을 더하고 카드를 끼운다. all 회차·격자 없음·격자 날짜 불일치는 건너뛴다."""

    def frame(self):
        return pd.DataFrame({"Open": [100., 101., 104.], "High": [101., 102., 105.], "Low": [99., 100., 103.],
                             "Close": [100., 102., 104.5], "Adj Close": [100., 102., 104.5],
                             "Volume": [1e6, 1e6, 5e5]}, index=pd.to_datetime(["2026-09-17", "2026-09-18", "2026-09-21"]))

    def run_main(self, when, scope, grid_text):
        import yfinance
        fixed = pd.Timestamp(when, tz="Asia/Seoul")
        real_now = pd.Timestamp.now
        saved = (yfinance.Ticker, pd.Timestamp.now, af.datetime)
        pd.Timestamp.now = classmethod(lambda cls, tz=None: real_now(tz=tz) if tz is None else fixed)

        class Frozen(af.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.to_pydatetime()
        af.datetime = Frozen
        frame = self.frame()

        class FakeTicker:
            def __init__(self, *a, **k): pass
            def history(self, **k): return frame.copy()
        yfinance.Ticker = FakeTicker
        page = (f"<html>{fu.SCORECARD_START}아침{fu.SCORECARD_END}{fu.POSTOPEN_START}자리{fu.POSTOPEN_END}"
                f"{af.MARK_START}옛 표{af.MARK_END}</html>")
        ledger = _ledger_csv()
        gp = af.github_pages
        saved_gp = (gp.token, gp.code_version, gp.fetch, gp.publish, sys.argv)
        published = []

        def fetch(path, tok):
            if path.endswith("forecast_log.csv"):
                return ledger
            if path.endswith("post_open_grid.csv"):
                return grid_text
            return page
        gp.token = lambda: "t"
        gp.code_version = lambda tok=None: {"short": "abc1234"}
        gp.fetch = fetch
        gp.publish = lambda path, text, tok, message: published.append((path, message, text)) or "deadbee"
        sys.argv = ["x", "--target", "samsung", "--out", tempfile.mkdtemp(), "--scope", scope, "--publish"]
        try:
            af.main()
        finally:
            yfinance.Ticker, pd.Timestamp.now, af.datetime = saved
            gp.token, gp.code_version, gp.fetch, gp.publish, sys.argv = saved_gp
        return {p: (m, t) for p, m, t in published}

    def test_open_scope_appends_one_post_open_row_and_swaps_the_card(self):
        out = self.run_main("2026-09-21 09:40", "open", _grid().to_csv(index=False))
        log = pd.read_csv(io.StringIO(out["forecast_history/samsung/forecast_log.csv"][1]))
        post = log[log["model"] == "Post-open"]
        self.assertEqual(len(post), 1)
        self.assertEqual(post["information_cutoff"].iloc[0], "post_open")
        self.assertEqual(post["created_at_utc"].iloc[0], "2026-09-21T00:40:00+00:00")
        self.assertEqual(str(post["is_prospective"].iloc[0]), "True")
        self.assertEqual(post["status"].iloc[0], "pending")                     # 종가는 16:10 에
        self.assertAlmostEqual(post["gap"].iloc[0], 104. / 102. - 1)
        # 07:00 행은 그대로(값 하나 안 바뀜).
        original = pd.read_csv(io.StringIO(_ledger_csv()))
        for column in ("record_id", "model", "prediction", "p_up", "created_at_utc"):
            self.assertEqual(list(log[column].iloc[:2].astype(str)), list(original[column].astype(str)))
        page = out["docs/samsung/index.html"][1]
        self.assertNotIn("자리", page)
        self.assertIn("시가 반영 갱신", page)
        self.assertIn("실제 실행 09:40 KST", page)
        self.assertIn("07:00 예측(‘하락’ 50%)은 위 카드 그대로", page)

    def test_all_scope_never_adds_a_post_open_row(self):
        out = self.run_main("2026-09-21 16:10", "all", _grid().to_csv(index=False))
        log = pd.read_csv(io.StringIO(out["forecast_history/samsung/forecast_log.csv"][1]))
        self.assertNotIn("Post-open", set(log["model"]))
        self.assertIn("자리", out["docs/samsung/index.html"][1])

    def test_stale_grid_or_missing_grid_is_skipped_without_failing_the_scoring(self):
        for grid_text in (_grid(target="2026-09-18").to_csv(index=False), None, "<html>404</html>"):
            with self.subTest(grid=str(grid_text)[:20]):
                out = self.run_main("2026-09-21 09:40", "open", grid_text)
                log = pd.read_csv(io.StringIO(out["forecast_history/samsung/forecast_log.csv"][1]))
                self.assertNotIn("Post-open", set(log["model"]))
                self.assertEqual(log.set_index("kind").loc["open", "status"], "scored")   # 시초가 채점은 그대로
                self.assertIn("자리", out["docs/samsung/index.html"][1])


class NotebookWiringTests(unittest.TestCase):
    """계약 7."""

    @classmethod
    def setUpClass(cls):
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        cls.cells = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
        cls.source = "\n".join(cls.cells)

    def test_post_open_models_are_trained_right_after_the_market_only_live_fit(self):
        live = next(s for s in self.cells if "market_live_models = {}" in s)
        self.assertLess(live.index('live_probs["No macro ensemble"]'), live.index("post_open_live_models[family] = fit_direction_model"))
        self.assertIn("fit_direction_model(_po_X, y, _po_train, family, seed=SEED, selection=SELECTION_METRIC)", live)
        self.assertIn("post_open_gap_features(_po_bars, feat[\"band\"], pd.DatetimeIndex(dates))", live)
        self.assertIn("build_post_open_grid(post_open_live_models, live_X[:, market_feature_idx][0], live_band", live)
        self.assertIn("더 나은 모델이 아니라 늦은 정보 시점이다", live)
        # 대표 모델의 확률·입력은 건드리지 않는다.
        self.assertNotIn('live_probs["Post-open"]', live)

    def test_grid_is_written_and_published_next_to_the_ledger_only_on_recording_runs(self):
        save = next(s for s in self.cells if 'live_table.to_csv(OUTPUT_DIR / "latest_forecast.csv")' in s)
        self.assertIn('post_open_grid.to_csv(OUTPUT_DIR / "post_open_grid.csv", index=False)', save)
        self.assertIn("if RECORD_FORECAST and not RECORD_EVENING_ONLY:", save)
        self.assertIn('github_put(f"{GITHUB_LEDGER_DIR}/post_open_grid.csv"', save)

    def test_easy_summary_gets_the_post_open_row(self):
        report = next(s for s in self.cells if "def build_summary():" in s)
        self.assertIn("POST_OPEN_ROW = post_open_row_for(daily, prediction_date)", report)
        self.assertIn('post_open=globals().get("POST_OPEN_ROW"), target=globals().get("TARGET")', report)

    def test_helper_cell_is_in_sync(self):
        helper = next(s for s in self.cells if s.startswith('"""시간순 예측 모델 선택과 누적 예측 원장'))
        self.assertEqual(helper, (ROOT / "forecast_utils.py").read_text(encoding="utf-8"))
        self.assertIn("def post_open_placeholder_html", helper)


class RunnerDelegationTests(unittest.TestCase):
    """러너 P16 의 그룹 G 는 forecast_utils 의 함수 하나에 위임한다."""

    def test_runner_reuses_the_shared_definition(self):
        import run_model_improvement as runner
        self.assertIs(runner.gap_rule_labels, fu.gap_rule_labels)
        self.assertEqual(runner.P16_GAP_COLUMNS, fu.POST_OPEN_GAP_COLUMNS)
        bars = pd.DataFrame({"open": [100., 101., 103.], "close": [100., 102., 104.]},
                            index=pd.to_datetime(["2026-09-17", "2026-09-18", "2026-09-21"]))
        band = pd.Series(.01, index=bars.index)
        pd.testing.assert_frame_equal(runner.p16_gap_features(bars, band, bars.index),
                                      fu.post_open_gap_features(bars, band, bars.index))


if __name__ == "__main__":
    unittest.main()
