# -*- coding: utf-8 -*-
"""기타 법인·자사주 매입 기간(2026-10-01).

  1. KRX가 기타 법인을 따로 주면 추정하지 않고 그 값을 쓴다('(추정)'이 붙지 않는다).
  2. DART 자기주식 취득·신탁계약 공시에서 매입 기간을 읽는다(날짜 형식 여러 가지).
  3. 자사주 매입 기간에 기타 법인이 순매수면 '회사 매입과 맞는 모양'으로 적는다. 기간이 아니면 적지 않는다.
  4. 네이버로 받았어도 KRX가 빠진 이유를 남기고, 오류 문구에서 계정 값을 지운다.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forecast_utils as F  # noqa: E402
from data_sources import dart, flows  # noqa: E402

TODAY = {"foreign_net": -758236, "inst_net": 46127, "indiv_net": -1292880}
BUYBACK = [{"kind": "직접 취득", "start": "2026-09-01", "end": "2026-11-30", "amount": "3,000,000,000,000", "purpose": "주주가치 제고"}]


class OtherNetTests(unittest.TestCase):
    def test_krx_other_net_is_used_as_is(self):
        story = F.flow_story(dict(TODAY, other_net=1_900_000), None, {"c2c": .0279}, 274500)
        other = [a for a in story["actors"] if a["key"] == "other_net"]
        self.assertEqual(len(other), 1)
        self.assertEqual(other[0]["name"], "기타 법인")
        self.assertNotIn("합계의 반대편으로 추정", F.flow_story_html(story))

    def test_buyback_line_only_inside_period(self):
        inside = F.flow_story(dict(TODAY, other_net=1_900_000), None, {"c2c": .0279, "buyback": BUYBACK}, 274500)
        self.assertTrue(any("회사 매입과 맞는 모양" in x for x in inside["observations"]))
        self.assertTrue(any("자사주 매입 기간 중(직접 취득 2026-09-01~2026-11-30" in x for x in inside["observations"]))
        outside = F.flow_story(dict(TODAY, other_net=1_900_000), None, {"c2c": .0279}, 274500)
        self.assertFalse(any("자사주" in x for x in outside["observations"]))

    def test_no_buyback_claim_when_other_corporates_sold(self):
        story = F.flow_story(dict(TODAY, other_net=-500_000), None, {"c2c": -.01, "buyback": BUYBACK}, 274500)
        self.assertFalse(any("회사 매입과 맞는 모양" in x for x in story["observations"]))


class DartBuybackTests(unittest.TestCase):
    def test_dates_in_several_formats(self):
        for text in ("2026년 09월 01일", "2026-09-01", "20260901", "2026.9.1"):
            self.assertEqual(dart._dart_date(text), pd.Timestamp("2026-09-01"), text)
        self.assertIsNone(dart._dart_date("-"))

    def test_fetch_reads_both_endpoints(self):
        replies = {
            "tsstkAqDecsn": {"status": "000", "list": [{"aq_expd_bgd": "2026년 09월 01일", "aq_expd_edd": "2026년 11월 30일",
                                                         "aqpln_prc_ostk": "3,000,000,000,000", "aq_pp": "주주가치 제고",
                                                         "rcept_no": "1"}]},
            "tsstkAqTrctrCnsDecsn": {"status": "013", "message": "조회된 데이타가 없습니다."},
        }

        class Resp:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(self.body).encode()

        def fake(url, timeout=60, accept=None):
            name = url.split("/api/")[1].split(".json")[0]
            return Resp(replies[name])

        with mock.patch.object(dart, "open_url", fake):
            got = dart.fetch_buyback_periods("00126380", "k", "2025-09-01", "2026-10-01")
        self.assertEqual(got, [{"kind": "직접 취득", "start": "2026-09-01", "end": "2026-11-30",
                                "amount": "3,000,000,000,000", "purpose": "주주가치 제고", "rcept_no": "1"}])


class KrxErrorTests(unittest.TestCase):
    def test_krx_failure_is_kept_and_secret_scrubbed(self):
        naver = pd.DataFrame({"date": pd.to_datetime(["2026-10-01"]), "foreign_net": [-1.0], "inst_net": [1.0],
                              "indiv_net": [0.0], "volume": [1.0], "foreign_ratio": [50.0]})
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"KRX_ID": "someone", "KRX_PW": "pw-secret-123"}), \
                mock.patch.object(flows, "_flows_from_pykrx", side_effect=RuntimeError("login failed for someone pw-secret-123")), \
                mock.patch.object(flows, "_flows_from_naver", return_value=naver):
            frame, info = flows.load_investor_flows(tmp, "005930.KS", "2026-09-01", "2026-10-01")
        self.assertEqual(info["source"], "naver")
        self.assertIn("RuntimeError", info["krx_error"])
        self.assertNotIn("pw-secret-123", info["krx_error"])
        self.assertNotIn("someone", info["krx_error"])
        self.assertIsNone(info["fetch_error"])


if __name__ == "__main__":
    unittest.main()
