# -*- coding: utf-8 -*-
"""전날 저녁에 낸 시초가 예측을 따로 남기고 실제 시가로 채점한다(2026-09-16).

아침 06:30 시초가 예측은 밤사이 미국 시장을 보고 내는 값이라 맞히기 쉽다. 저녁(17~23시, 미국 장 시작 전)
예측을 'Candidate evening open' 으로 남겨 나란히 채점하면 그 차이가 밤사이 정보의 값이다.
"""
import json
import re
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forecast_utils as fu  # noqa: E402


def plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def daily(n=12):
    """같은 날을 아침(Ridge)·저녁(Candidate evening open)이 각각 예측한 시초가 채점 결과."""
    rows = []
    for i in range(n):
        day = pd.Timestamp("2026-08-01") + pd.offsets.BDay(i)
        for model, hit, err in (("Ridge", 1 if i % 5 else 0, .003), ("Candidate evening open", 1 if i % 2 else 0, .009)):
            rows.append(dict(target_date=day, kind="open", horizon_days=1, model=model, status="scored",
                             is_prospective=True, interval_hit=float(hit), return_error=err, predicted_return=.002,
                             predicted_open=100.2, center_open=100.2, actual_open=100.5,
                             actual_return=.002 + err, current_close=100.))
        for model, correct, probs in (("No macro ensemble", 1., (.2, .3, .5)), ("Candidate evening forecast", 0., (.5, .3, .2))):
            rows.append(dict(target_date=day, kind="direction", horizon_days=1, model=model, status="scored",
                             is_prospective=True, direction_correct=correct, prediction="상승" if correct else "하락",
                             p_down=probs[0], p_flat=probs[1], p_up=probs[2], actual_class=2, log_loss=.7, band=.01,
                             actual_return=.01, current_close=100.))
    return pd.DataFrame(rows)


class OvernightValueTests(unittest.TestCase):
    def test_open_forecasts_are_compared_morning_vs_evening(self):
        html = fu.overnight_value_html(daily(), "No macro ensemble")
        text = plain(html)
        self.assertIn("시초가 구간 적중률", text)
        self.assertIn("아침 (갭 정보 있음)", text)
        self.assertIn("저녁 (갭 정보 없음)", text)
        # 아침 75%(12일 중 i=0·5·10 이탈) vs 저녁 50%. 갭 오차 MAE 0.30% vs 0.90%.
        self.assertIn("아침 (갭 정보 있음) 75% · MAE 0.30% n=12", text)
        self.assertIn("저녁 (갭 정보 없음) 50% · MAE 0.90% n=12", text)
        self.assertIn("아침 (갭 정보 있음) 100% n=12", text)                # 방향 표는 그대로
        self.assertIn("전날 저녁에 낸 시초가 예측이 공정한 성적입니다", text)

    def test_without_evening_open_rows_only_the_direction_table_appears(self):
        frame = daily()
        frame = frame[frame["model"] != "Candidate evening open"]
        text = plain(fu.overnight_value_html(frame, "No macro ensemble"))
        self.assertIn("방향 적중률", text)
        self.assertNotIn("시초가 구간 적중률", text)

    def test_small_samples_show_a_dash(self):
        text = plain(fu.overnight_value_html(daily(n=4), "No macro ensemble"))
        self.assertIn("표본 10일 미만은 — 로 둡니다", text)


class HeadlineIsolationTests(unittest.TestCase):
    """저녁 후보 행이 대표 시초가 집계·표에 섞이면 안 된다."""

    def review(self):
        frame = daily()
        bars = pd.DataFrame({"open": 100., "close": 101., "adj_close": 101.},
                            index=pd.DatetimeIndex(sorted(frame["target_date"].unique())))
        return fu.review_ledger(frame, bars, ensemble_model="No macro ensemble")

    def test_rolling_open_stats_use_the_morning_row_only(self):
        roll = self.review()["rolling"]
        row = roll[(roll["window"] == 60) & (roll["kind"] == "open")].iloc[0]
        self.assertEqual(int(row["n"]), 12)
        self.assertAlmostEqual(float(row["interval_coverage"]), 9 / 12)      # 저녁 후보(50%)가 섞이면 값이 달라진다

    def test_scorecard_and_ledger_section_pick_the_morning_row(self):
        review = self.review()
        card = plain(fu.scorecard_html(review, "No macro ensemble"))
        self.assertIn("맞음 시초가", card)                          # 마지막 날(i=11) 아침 행은 적중
        section = plain(fu.ledger_section_html(review, "No macro ensemble"))
        self.assertEqual(section.count("시초가(갭)"), 1 + section.count("시초가(갭) 구간 적중"))


class NotebookWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        cls.cells = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]

    def test_evening_run_records_the_open_forecast_as_a_candidate(self):
        setup = next(c for c in self.cells if 'EVENING_MODEL = "Candidate evening forecast"' in c)
        self.assertIn('EVENING_OPEN_MODEL = "Candidate evening open"', setup)
        ledger = next(c for c in self.cells if '"kind": "open", "horizon_days": 1' in c)
        evening = ledger[ledger.index("if RECORD_EVENING_ONLY:"):ledger.index("else:", ledger.index("if RECORD_EVENING_ONLY:"))]
        self.assertIn('"model": EVENING_OPEN_MODEL, "kind": "open"', evening)
        self.assertIn("**open_forecast_row", evening)


if __name__ == "__main__":
    unittest.main()
