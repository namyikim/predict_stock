"""뉴스심리지수(NSI) 정렬: 주 1회 공개를 반영해 지수 날짜+8일 이후에만 보여야 한다."""
import unittest

import numpy as np
import pandas as pd

from pathlib import Path

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


class CliFeatureTests(unittest.TestCase):
    """G20 CLI: 참조월+1개월 20일 이후에만 보인다."""

    def frame(self):
        months = pd.date_range("2024-01-01", "2026-07-01", freq="MS")
        return pd.DataFrame({"month": months, "value": 100 + np.sin(np.arange(len(months)) / 4)})

    def test_value_appears_on_release_day(self):
        f = self.frame()
        level = f.set_index("month")["value"]
        feat = mu.cli_features(f, pd.bdate_range("2026-08-01", "2026-09-08"))
        self.assertAlmostEqual(feat.loc["2026-08-19", "cli_level"], level["2026-06-01"] - 100)
        self.assertAlmostEqual(feat.loc["2026-08-20", "cli_level"], level["2026-07-01"] - 100)

    def test_oecd_csv_is_parsed_and_filtered(self):
        csv = ("STRUCTURE,REF_AREA,Reference area,TIME_PERIOD,Time period,OBS_VALUE,Observation value\n"
               "DATAFLOW,G20,G20,2026-05,2026-05,100.41,100.41\n"
               "DATAFLOW,G7,G7,2026-05,2026-05,99.9,99.9\n"
               "DATAFLOW,G20,G20,2026-06,2026-06,100.52,100.52\n"
               "DATAFLOW,G20,G20,2026-07,2026-07,,\n")
        got = mu.parse_oecd_csv(csv, "G20")
        self.assertEqual(list(got["value"]), [100.41, 100.52])      # G7 행과 결측은 빠진다
        with self.assertRaises(ValueError):
            mu.parse_oecd_csv("a,b\n1,2\n")

    def test_fred_missing_values_are_dropped(self):
        got = mu.parse_fred_observations({"observations": [{"date": "2026-06-01", "value": "100.8"},
                                                            {"date": "2026-07-01", "value": "."}]})
        self.assertEqual(len(got), 1)
        with self.assertRaises(ValueError):
            mu.parse_fred_observations({"observations": [{"date": "2026-07-01", "value": "."}]})


class InvestorFlowTests(unittest.TestCase):
    """외국인·기관 수급: 네이버 표 파싱, 전일까지만 쓰는 특징."""

    HTML = """<table class="type2"><tr><th>날짜</th><th>종가</th><th>전일비</th><th>등락률</th><th>거래량</th>
      <th colspan="2">순매매량</th><th colspan="2">외국인</th></tr>
      <tr><th></th><th></th><th></th><th></th><th></th><th>기관</th><th>외국인</th><th>보유주수</th><th>보유율</th></tr>
      <tr><td>2026.09.08</td><td>270,000</td><td>+12,500</td><td>+4.86%</td><td>30,123,456</td><td>-1,234,567</td><td>+5,678,901</td><td>3,000,000,000</td><td>50.25%</td></tr>
      <tr><td>2026.09.07</td><td>257,500</td><td>+2,000</td><td>+0.78%</td><td>20,000,000</td><td>+300,000</td><td>-1,000,000</td><td>2,994,321,099</td><td>50.15%</td></tr></table>"""

    def test_naver_two_level_header_maps_to_the_right_columns(self):
        got = mu.parse_naver_frgn_html(self.HTML).set_index("date")
        self.assertEqual(got.loc["2026-09-08", "foreign_net"], 5678901)
        self.assertEqual(got.loc["2026-09-08", "inst_net"], -1234567)
        self.assertEqual(got.loc["2026-09-08", "volume"], 30123456)
        self.assertAlmostEqual(got.loc["2026-09-08", "foreign_ratio"], 50.25)
        self.assertEqual(list(got.index), list(pd.to_datetime(["2026-09-07", "2026-09-08"])))

    def test_features_use_only_the_previous_day(self):
        days = pd.bdate_range("2026-06-01", "2026-09-08")
        rng = np.random.default_rng(0)
        flows = pd.DataFrame({"date": days, "foreign_net": rng.normal(0, 2e6, len(days)),
                              "inst_net": rng.normal(0, 1e6, len(days)), "indiv_net": np.nan,
                              "volume": rng.integers(1e7, 3e7, len(days)).astype(float),
                              "foreign_ratio": 50 + np.cumsum(rng.normal(0, .02, len(days)))})
        feat = mu.flow_features(flows, pd.bdate_range("2026-08-01", "2026-09-09"))
        f = flows.set_index("date")
        vol20 = f["volume"].rolling(20, min_periods=10).mean()
        self.assertAlmostEqual(feat.loc["2026-09-08", "flow_frgn_1"], (f["foreign_net"] / vol20).loc["2026-09-07"])
        # 9/9 예측일(자료 없음)은 9/8까지의 값으로 채워진다
        self.assertAlmostEqual(feat.loc["2026-09-09", "flow_frgn_1"], (f["foreign_net"] / vol20).loc["2026-09-08"])

    def test_streak_counts_consecutive_signed_days(self):
        days = pd.bdate_range("2026-08-01", "2026-09-08")
        net = np.ones(len(days)) * 1e6
        net[-4:] = -1e6                     # 마지막 4일 순매도
        flows = pd.DataFrame({"date": days, "foreign_net": net, "inst_net": 0., "indiv_net": np.nan,
                              "volume": 2e7, "foreign_ratio": 50.})
        feat = mu.flow_features(flows, pd.bdate_range("2026-09-01", "2026-09-09"))
        self.assertEqual(feat.loc["2026-09-09", "flow_frgn_streak"], -4)   # 9/8까지 4일 연속 순매도


class UserAgentTests(unittest.TestCase):
    """파이썬 기본 User-Agent는 CDN이 막는다(OECD가 Actions에서 403). 모든 외부 요청에 붙인다."""

    def test_requests_carry_a_browser_user_agent(self):
        request = mu.urllib_request_with_agent("https://example.com", accept="text/csv")
        self.assertIn("Mozilla/5.0", request.get_header("User-agent"))
        self.assertEqual(request.get_header("Accept"), "text/csv")

    def test_no_raw_urlopen_left_in_fetchers(self):
        source = Path(mu.__file__).read_text(encoding="utf-8")
        # 외부 조회는 모두 open_url을 거쳐야 한다. urlopen 직접 호출은 open_url 정의 한 줄뿐이다.
        direct = [l.strip() for l in source.splitlines()
                  if "urlopen(" in l and "open_url(" not in l and not l.strip().startswith(("#", "from", "import"))]
        self.assertEqual(direct, ["return urlopen(urllib_request_with_agent(url, accept), timeout=timeout)"])
