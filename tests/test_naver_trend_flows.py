# -*- coding: utf-8 -*-
"""네이버 증권 투자자별 매매동향 JSON(2026-10-01).

옛 finance.naver.com/item/frgn 표가 새 사이트로 넘어가 사라진 뒤 수급이 보관본(전날까지)에 머물렀다.
  1. JSON 한 행을 FLOW_COLUMNS 로 옮긴다(부호·쉼표·% 처리, 개인 포함).
  2. bizdate 커서로 과거 쪽으로 넘기며 start 까지 모은다.
  3. JSON 이 실패하면 옛 표로 내려간다.
"""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data_sources import flows  # noqa: E402

ROW = {"itemCode": "005930", "bizdate": "20261001", "foreignerPureBuyQuant": "-758,236", "foreignerHoldRatio": "46.39%",
       "organPureBuyQuant": "+46,127", "individualPureBuyQuant": "-1,292,880", "closePrice": "274,500",
       "accumulatedTradingVolume": "13,686,117"}


def _row(day):
    return dict(ROW, bizdate=day.strftime("%Y%m%d"))


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class NaverTrendTests(unittest.TestCase):
    def test_parse_one_row(self):
        got = flows.parse_naver_trend_json([ROW]).iloc[0]
        self.assertEqual(got["date"], pd.Timestamp("2026-10-01"))
        self.assertEqual((got["foreign_net"], got["inst_net"], got["indiv_net"]), (-758236, 46127, -1292880))
        self.assertEqual(got["volume"], 13686117)
        self.assertAlmostEqual(got["foreign_ratio"], 46.39)
        self.assertEqual(list(flows.parse_naver_trend_json([]).columns), flows.FLOW_COLUMNS)

    def test_pages_backwards_with_bizdate_cursor(self):
        days = pd.bdate_range("2026-07-01", "2026-10-01")[::-1]
        calls = []

        def fake(url, timeout=60, accept=None):
            calls.append(url)
            cursor = pd.Timestamp(url.split("bizdate=")[1][:8]) if "bizdate=" in url else None
            older = [d for d in days if cursor is None or d < cursor][:60]   # bizdate 는 그 날짜보다 앞선 것부터
            return _Response(json.dumps([_row(d) for d in older]).encode())

        with mock.patch.object(flows, "open_url", fake), mock.patch.object(flows.time, "sleep"):
            got = flows._flows_from_naver_json("005930.KS", "2026-07-02")
        self.assertEqual(got["date"].min(), pd.Timestamp("2026-07-02"))
        self.assertEqual(got["date"].max(), pd.Timestamp("2026-10-01"))
        self.assertTrue(got["date"].is_unique)
        self.assertEqual(len(calls), 2)
        self.assertIn("bizdate=", calls[1])

    def test_falls_back_to_old_table(self):
        sentinel = pd.DataFrame(columns=flows.FLOW_COLUMNS)
        with mock.patch.object(flows, "_flows_from_naver_json", side_effect=RuntimeError("x")), \
                mock.patch.object(flows, "_flows_from_naver_html", return_value=sentinel) as html:
            self.assertIs(flows._flows_from_naver("005930.KS", "2026-09-01"), sentinel)
        html.assert_called_once()


if __name__ == "__main__":
    unittest.main()
