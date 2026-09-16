# -*- coding: utf-8 -*-
"""거시 경제 페이지: 총저축률·국내총투자율(꺾은선)과 경상수지(막대)를 1990년부터 겹친 그림(2026-09-16)."""
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from data_sources import ecos  # noqa: E402
import build_macro_report as macro  # noqa: E402

YEARS = list(range(1990, 2026))
SAVING = {y: 39.5 - 0.15 * (y - 1990) for y in YEARS}
INVEST = {y: 40.1 - 0.30 * (y - 1990) for y in YEARS}
CA_MILLION = {y: (SAVING[y] - INVEST[y]) * 20000.0 for y in YEARS}
CA_MILLION[1997] = -8200.0           # 적자인 해
CA_MILLION[1998] = 40000.0


def fake_fetch(stat, item, first, last, fail=()):
    source = {ecos.SAVING_ITEM: SAVING, ecos.INVESTMENT_ITEM: INVEST, ecos.CA_ITEM: CA_MILLION}[item]
    if item in fail:
        raise RuntimeError("조회 실패")
    return pd.Series({y: v for y, v in source.items() if first <= y <= last}, dtype=float)


class BuildTests(unittest.TestCase):
    def test_codes_are_the_ones_checked_on_ecos(self):
        self.assertEqual((ecos.SAVING_STAT, ecos.SAVING_ITEM, ecos.INVESTMENT_ITEM),
                         ("200Y101", "80101", "8010200"))
        self.assertEqual((ecos.CA_STAT, ecos.CA_ITEM), ("301Y013", "000000"))

    def test_current_account_is_converted_to_hundred_million_dollars(self):
        frame, info = ecos.build_saving_investment(fetch_fn=fake_fetch, end_year=2025)
        self.assertEqual(list(frame.columns), ["saving_rate", "investment_rate", "current_account"])
        self.assertEqual((frame.index.min(), frame.index.max()), (1990, 2025))
        self.assertAlmostEqual(frame.at[1998, "current_account"], 400.0)      # 40,000백만 달러 = 400억 달러
        self.assertAlmostEqual(frame.at[1990, "saving_rate"], 39.5)
        self.assertEqual(info["source"], "ECOS_API")
        self.assertEqual((info["first"], info["last"], info["rows"]), (1990, 2025, 36))

    def test_new_values_win_and_the_cache_fills_what_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "saving.csv"
            pd.DataFrame({"year": [1990, 2025], "saving_rate": [1.0, 2.0], "investment_rate": [3.0, 4.0],
                          "current_account": [5.0, 6.0]}).to_csv(cache, index=False)
            frame, info = ecos.build_saving_investment(
                cache_path=cache, end_year=2025,
                fetch_fn=lambda *a: fake_fetch(*a, fail=(ecos.CA_ITEM,)))
        self.assertAlmostEqual(frame.at[1990, "saving_rate"], 39.5)          # 새 값이 우선
        self.assertAlmostEqual(frame.at[2025, "current_account"], 6.0)       # 실패한 계열은 보관본
        self.assertTrue(pd.isna(frame.at[2000, "current_account"]))          # 보관본에도 없으면 비움
        self.assertIn("current_account", info["failed"])
        self.assertEqual(info["source"], "ECOS_API+cache")

    def test_without_a_key_the_cache_is_used_and_the_reason_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "saving.csv"
            pd.DataFrame({"year": [1989, 1990], "saving_rate": [1.0, 2.0], "investment_rate": [3.0, 4.0],
                          "current_account": [5.0, 6.0]}).to_csv(cache, index=False)
            with patch.object(ecos, "ecos_key", return_value=None):
                frame, info = ecos.build_saving_investment(cache_path=cache)
        self.assertEqual(list(frame.index), [1990])                           # 1990년부터
        self.assertEqual(info["source"], "cache")
        self.assertIn("ECOS_API_KEY", info["failed"]["ECOS"])

    def test_annual_fetch_parses_the_ecos_response(self):
        payload = {"StatisticSearch": {"row": [{"TIME": "2024", "DATA_VALUE": "34.7"},
                                               {"TIME": "2023", "DATA_VALUE": "32.9"},
                                               {"TIME": "2022", "DATA_VALUE": ""}]}}
        with patch.object(ecos, "_ecos_request", return_value=payload) as request:
            series = ecos.fetch_ecos_annual("200Y101", "80101", 1990, 2025, "KEY")
        self.assertEqual(series.to_dict(), {2023: 32.9, 2024: 34.7})
        self.assertIn("/200Y101/A/1990/2025/80101", request.call_args[0][1])
        self.assertNotIn("KEY", request.call_args[0][1])                       # 키는 URL 틀에만 들어간다


class ChartTests(unittest.TestCase):
    def frame(self):
        return ecos.build_saving_investment(fetch_fn=fake_fetch, end_year=2025)[0]

    def test_lines_bars_and_axes(self):
        html = macro.saving_investment_chart(self.frame())
        self.assertIn("총저축률·국내총투자율과 경상수지", html)
        self.assertIn("1990년~2025년 · 연간", html)
        self.assertEqual(html.count("<polyline"), 2)                           # 저축률·투자율
        self.assertEqual(len(re.findall(r"<rect [^>]*><title>\d{4}년 경상수지", html)), 36)
        self.assertIn(f'fill="{macro.SAVING_COLORS["deficit"]}"><title>1997년 경상수지 -82억 달러', html)
        self.assertIn(f'fill="{macro.SAVING_COLORS["surplus"]}"><title>1998년 경상수지 400억 달러', html)
        self.assertIn("국민총처분가능소득 대비 %(왼쪽 축)", html)
        self.assertIn("억 달러(오른쪽 축)", html)
        self.assertIn('stroke-dasharray="3,3"', html)                          # 경상수지 0 선
        for year in ("1990", "2000", "2010", "2020", "2025"):
            self.assertIn(f'fill="#8a9199">{year}</text>', html)

    def test_investment_rate_is_blue(self):
        """2026-09-16 요청: 국내총투자율을 파랑으로."""
        self.assertEqual(macro.SAVING_COLORS["investment_rate"], "#1a5490")
        self.assertNotEqual(macro.SAVING_COLORS["saving_rate"], "#1a5490")
        html = macro.saving_investment_chart(self.frame())
        self.assertIn('stroke="#1a5490" stroke-width="2.5"/><text x="186" y="18" fill="#1a1a1a" '
                      'font-weight="600">국내총투자율', html)

    def test_lines_stay_above_the_zero_line_of_the_bars(self):
        """2026-09-16 지적: 꺾은선이 적자 막대 영역(0 선 아래)까지 내려왔다. 왼쪽 축을 맞춰 위로 올린다."""
        for frame in (self.frame(), self.frame().assign(current_account=lambda f: f["current_account"] - 600)):
            with self.subTest(deficits=int((frame["current_account"] < 0).sum())):
                html = macro.saving_investment_chart(frame)
                zero = float(re.search(r'y1="([\d.]+)" y2="[\d.]+" stroke="#9aa3ab"', html).group(1))
                ys = [float(y) for points in re.findall(r'<polyline [^>]*points="([^"]+)"', html)
                      for y in re.findall(r",([\d.]+)", points)]
                self.assertTrue(ys)
                self.assertLess(max(ys), zero, "선의 가장 낮은 점(가장 큰 y)이 0 선보다 위여야 한다")

    def test_axis_ticks_are_round_numbers(self):
        html = macro.saving_investment_chart(self.frame())
        left = re.findall(r'text-anchor="end" fill="#48525c">([\d.]+)%</text>', html)
        right = re.findall(r'fill="#4f7a5c">(-?[\d,]+)</text>', html)
        self.assertTrue(left and right)
        self.assertTrue(all(float(v) % 1 == 0 for v in left), left)
        self.assertIn("0", right)
        self.assertNotIn("-0", right)
        self.assertEqual(macro.nice_ticks(-170, 1320, (100, 200, 250, 500)), [0.0, 250.0, 500.0, 750.0, 1000.0, 1250.0])

    def test_latest_values_and_the_identity_are_explained(self):
        html = macro.saving_investment_chart(self.frame())
        text = re.sub(r"<[^>]+>", "", html)
        saving, invest = SAVING[2025], INVEST[2025]
        self.assertIn(f"2025년 총저축률 {saving:.1f}% · 국내총투자율 {invest:.1f}% (차이 {saving - invest:+.1f}%p)",
                      text)
        self.assertIn(f"경상수지 {CA_MILLION[2025] / 100:,.0f}억 달러", text)
        self.assertRegex(text, r"저축률−투자율 차이와 경상수지의 상관 [+-]\d\.\d\d \(36년\)")
        self.assertIn("회계상 항등식", text)
        self.assertIn("인과가 아니라", text)
        self.assertIn("한국은행 ECOS", text)

    def test_missing_or_short_data_draws_nothing(self):
        self.assertEqual(macro.saving_investment_chart(None), "")
        self.assertEqual(macro.saving_investment_chart(self.frame().iloc[-3:]), "")
        self.assertEqual(macro.saving_investment_chart(self.frame().drop(columns="current_account")), "")


class PageTests(unittest.TestCase):
    NOW = datetime(2026, 9, 16, 9, 0, tzinfo=timezone(timedelta(hours=9)))

    def test_page_shows_the_chart_or_why_it_could_not(self):
        frame = ecos.build_saving_investment(fetch_fn=fake_fetch, end_year=2025)[0]
        page = macro.build_page(self.NOW, saving_frame=frame, saving_info={})
        self.assertIn("총저축률·국내총투자율과 경상수지", page)
        failed = macro.build_page(self.NOW, saving_frame=None,
                                  saving_info={"failed": {"ECOS": "ECOS_API_KEY가 없습니다"}})
        self.assertIn("총저축률·투자율·경상수지 자료를 받지 못했습니다 — ECOS: ECOS_API_KEY가 없습니다", failed)

    def test_committed_cache_covers_1990_onwards(self):
        cache = ROOT / "macro_history" / "korea_saving_investment.csv"
        frame = pd.read_csv(cache)
        self.assertEqual(int(frame["year"].min()), 1990)
        self.assertGreaterEqual(int(frame["year"].max()), 2025)
        self.assertEqual(int(frame[["saving_rate", "investment_rate", "current_account"]].isna().sum().sum()), 0)


if __name__ == "__main__":
    unittest.main()
