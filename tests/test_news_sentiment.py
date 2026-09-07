"""뉴스심리지수(NSI) 정렬: 주 1회 공개를 반영해 지수 날짜+8일 이후에만 보여야 한다."""
import unittest

import numpy as np
import pandas as pd

import macro_utils as mu
from test_pipeline_behavior import make_synthetic_raw, run_feature_cell


class NsiFeatureTests(unittest.TestCase):
    def series(self):
        dates = pd.date_range("2026-01-01", "2026-09-06")
        return pd.DataFrame({"date": dates, "value": 100 + np.sin(np.arange(len(dates)) / 10) * 5})

    def test_value_is_hidden_until_release_lag(self):
        frame = self.series()
        level = frame.set_index("date")["value"]
        f = mu.nsi_features(frame, pd.bdate_range("2026-03-01", "2026-09-08"))
        # 9/8 예측에 보이는 값은 (9/8 - lag) 지수까지다.
        lag = pd.Timedelta(days=mu.NSI_RELEASE_LAG_DAYS)
        self.assertAlmostEqual(f.loc["2026-09-08", "nsi_level"], level.loc[pd.Timestamp("2026-09-08") - lag] - 100)
        self.assertNotAlmostEqual(f.loc["2026-09-08", "nsi_level"], level.loc["2026-09-06"] - 100)

    def test_a_future_revision_cannot_change_the_past_row(self):
        frame = self.series()
        base = mu.nsi_features(frame, pd.bdate_range("2026-06-01", "2026-07-01"))
        frame.loc[frame["date"] >= "2026-06-25", "value"] += 30      # 6/25 이후를 바꿔도
        changed = mu.nsi_features(frame, pd.bdate_range("2026-06-01", "2026-07-01"))
        cutoff = pd.Timestamp("2026-06-24") + pd.Timedelta(days=mu.NSI_RELEASE_LAG_DAYS)
        pd.testing.assert_frame_equal(base.loc[:cutoff], changed.loc[:cutoff])  # 공개 전 구간의 행은 그대로

    def test_stale_series_expires(self):
        frame = self.series()
        f = mu.nsi_features(frame, pd.bdate_range("2026-09-01", "2026-10-30"))
        fresh = pd.Timestamp("2026-09-06") + pd.Timedelta(days=mu.NSI_RELEASE_LAG_DAYS)
        fresh = pd.bdate_range(fresh, periods=1)[0]
        self.assertTrue(f.loc[fresh].notna().all())                                 # 공개 직후 신선
        self.assertTrue(f.loc[fresh + pd.Timedelta(days=mu.NSI_MAX_AGE_DAYS + 3)].isna().all())  # 만료

    def test_parse_and_normalize_reject_bad_input(self):
        got = mu.parse_ecos_daily({"StatisticSearch": {"row": [{"TIME": "20260901", "DATA_VALUE": "101.2"},
                                                               {"TIME": "20260902", "DATA_VALUE": "-"}]}})
        self.assertEqual(len(got), 1)
        with self.assertRaises(ValueError):
            mu.parse_ecos_daily({"StatisticSearch": {"row": []}})
        with self.assertRaises(ValueError):
            mu.normalize_daily(pd.DataFrame({"date": ["20260901", "20260901"], "value": [1, 2]}))


class NsiInNotebookTests(unittest.TestCase):
    def test_feature_cell_joins_nsi_with_release_lag(self):
        raw, dates, _, _ = make_synthetic_raw()
        frame = pd.DataFrame({"date": pd.date_range(dates[0] - pd.Timedelta(days=120), dates[-1] + pd.Timedelta(days=1)),
                              "value": 100.0})
        frame["value"] += np.arange(len(frame)) * 0.01
        ns = run_feature_cell(raw, NSI_ACTIVE=True, nsi_frame=frame, nsi_info={"enabled": True, "last": "x"})
        self.assertTrue(set(ns["nsi_feature_cols"]) >= {"nsi_level", "nsi_change_5d", "nsi_change_20d", "nsi_z60"})
        self.assertTrue(all(c in ns["feature_cols"] for c in ns["nsi_feature_cols"]))
        # 라이브 행의 nsi_level은 예측일-lag 지수여야 한다.
        pred = ns["prediction_date"]
        expected = float(frame.set_index("date").loc[pred - pd.Timedelta(days=mu.NSI_RELEASE_LAG_DAYS), "value"]) - 100
        self.assertAlmostEqual(float(ns["live_row"]["nsi_level"].iloc[0]), expected, places=6)

    def test_feature_cell_drops_nsi_when_live_row_is_stale(self):
        raw, dates, _, _ = make_synthetic_raw()
        frame = pd.DataFrame({"date": pd.date_range(dates[0] - pd.Timedelta(days=120), dates[-1] - pd.Timedelta(days=40)),
                              "value": 100.0})
        ns = run_feature_cell(raw, NSI_ACTIVE=True, nsi_frame=frame, nsi_info={"enabled": True, "last": "x"})
        self.assertFalse(ns["NSI_ACTIVE"])
        self.assertEqual(ns["nsi_feature_cols"], [])


if __name__ == "__main__":
    unittest.main()
