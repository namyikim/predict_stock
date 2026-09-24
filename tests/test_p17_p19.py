# -*- coding: utf-8 -*-
"""P17(해외 수익률 as-of 누적)·P19(대표 모델 학습 행) 실험의 입력 정의(2026-09-24)."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_model_improvement as rmi  # noqa: E402


def us_bars(dates, closes):
    return pd.DataFrame({"adj_close": closes, "close": closes}, index=pd.to_datetime(dates))


class AsofCumulativeTests(unittest.TestCase):
    # 한국 행: 9/7(월)·9/8(화)·9/9(수)·9/14(월, 한국 연휴 9/10~9/11 뒤)
    korea = pd.to_datetime(["2026-09-07", "2026-09-08", "2026-09-09", "2026-09-14"])
    # 미국: 9/4(금), 9/7(월) 휴장(노동절), 9/8(화), 9/9(수), 9/10(목), 9/11(금)
    us = us_bars(["2026-09-03", "2026-09-04", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"],
                 [100., 103., 104., 105., 110., 99.])

    def ns(self):
        feat = pd.DataFrame({"sam_ret_1": [0.] * 4}, index=self.korea)
        return {"feat": feat, "raw": {"sp500": self.us}, "GLOBAL_ASSETS": ["sp500"]}

    def test_us_holiday_gives_zero_not_the_repeated_return(self):
        frame = rmi.p17_candidate_columns(self.ns())
        # 9/8(화) 한국 행이 보는 미국 세션은 9/4(금) 그대로다(9/7 휴장) → 새 정보 없음 = 0
        self.assertEqual(frame.loc["2026-09-08", "sp500_ret_1"], 0.0)
        self.assertEqual(frame.loc["2026-09-08", "us_new_session"], 0.0)
        # 지금 규칙은 같은 자리에 9/4 수익률(+3%)을 반복한다.
        current = rmi.asof_with_source(self.korea, self.us["adj_close"].pct_change())
        self.assertAlmostEqual(current.loc["2026-09-07", "value"], current.loc["2026-09-08", "value"])

    def test_korean_holiday_accumulates_all_us_sessions(self):
        frame = rmi.p17_candidate_columns(self.ns())
        # 9/14 한국 행: 9/9 행이 본 9/8 종가(104) 이후 9/9·9/10·9/11 세션이 모두 반영 → 99/104 − 1
        self.assertAlmostEqual(frame.loc["2026-09-14", "sp500_ret_1"], 99. / 104. - 1)
        self.assertEqual(frame.loc["2026-09-14", "us_new_session"], 1.0)
        self.assertEqual(frame.loc["2026-09-14", "us_obs_age_days"], 3.0)      # 9/11 세션 → 9/14

    def test_same_availability_as_the_notebook(self):
        # 미국 세션 d 는 한국 d+1 부터 — 9/9 한국 행은 9/8 세션까지만 본다(9/9 세션 값을 보면 누수).
        frame = rmi.asof_with_source(self.korea, self.us["adj_close"])
        self.assertEqual(frame.loc["2026-09-09", "source_date"], pd.Timestamp("2026-09-08"))

    def test_missing_flag_asset_is_an_error_not_a_silent_zero(self):
        ns = self.ns()
        ns["GLOBAL_ASSETS"], ns["raw"] = ["nasdaq"], {"nasdaq": self.us}
        with self.assertRaises(RuntimeError):
            rmi.p17_candidate_columns(ns)


class MarketRowsTests(unittest.TestCase):
    def test_rows_missing_only_auxiliary_data_are_kept(self):
        dates = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
        feat = pd.DataFrame({"sam_ret_1": [.01, .02, .03], "nsi_level": [np.nan, 1., 1.],
                             "target": [2., 1., 0.], "target_return": [.01, 0., -.01], "band": [.005] * 3}, index=dates)
        ns = {"feat": feat, "sam": pd.DataFrame(index=dates), "feature_cols": ["sam_ret_1", "nsi_level"],
              "market_feature_idx": [0], "NON_FEATURE_COLS": ["target", "target_return", "band"]}
        X, y, got = rmi.p19_market_rows(ns)
        self.assertEqual(list(got), list(dates), "뉴스심리 결측 행(1/2)도 시세만 모델의 학습 행이다")
        self.assertEqual(X.shape, (3, 1))
        self.assertEqual(list(y), [2, 1, 0])


class NotebookAdoptionTests(unittest.TestCase):
    """P17 운영 반영(2026-09-24): 노트북 특징 셀이 러너와 같은 forecast_utils 함수로 해외 1일 수익률을 만든다."""

    def cell(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        return next("".join(c["source"]) for c in nb["cells"] if "for name in GLOBAL_ASSETS:" in "".join(c["source"])
                    and "def merge_latest_available(" in "".join(c["source"]))

    def test_one_day_returns_use_the_asof_cumulative_definition(self):
        cell = self.cell()
        self.assertIn('feat[f"{name}_ret_1"] = asof_cumulative_return(all_dates, close.astype(float)).to_numpy()', cell)
        self.assertIn('feat["krwjpy_ret_1"] = asof_cumulative_return(all_dates, _krwjpy).to_numpy()', cell)
        self.assertNotIn("merge_latest_available(all_dates, safe_pct_change(close, 1), 1)", cell)
        # 5일 수익률은 그대로('지금 상태'라 반복이 옳다)
        self.assertIn('feat[f"{name}_ret_5"] = merge_latest_available(all_dates, safe_pct_change(close, 5), 1)', cell)

    def test_session_columns_come_right_after_the_calendar_columns(self):
        # 러너 후보는 시세 열 끝에 두 열을 붙였다. 노트북도 시세 열의 마지막이어야 학습 결과가 같다.
        cell = self.cell()
        self.assertLess(cell.index('feat["cal_month_end"]'), cell.index("us_session_features(all_dates"))
        self.assertLess(cell.index("us_session_features(all_dates"), cell.index("feat = feat.replace([np.inf, -np.inf], np.nan).ffill(limit=5)"))


class RegistrationTests(unittest.TestCase):
    def test_tasks_are_registered(self):
        for task in ("P17", "P19"):
            self.assertIn(task, rmi.TASKS)
            self.assertIn(task, rmi.TASK_RUNNERS)


if __name__ == "__main__":
    unittest.main()
