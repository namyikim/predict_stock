# -*- coding: utf-8 -*-
"""직전 거래일 장 회고를 새로 만든 보고서에 다시 붙인다(2026-09-23).

16:10 회차가 끼운 회고(흐름이 바뀐 시각과 그 전후 뉴스)는 저녁·코드 반영 실행이 페이지를 새로 만들면 사라졌다.
추석 연휴 앞 9/23 저녁 실행이 9/28 보고서를 만들며 그날 뉴스 목록이 첫 화면에서 빠진 것이 계기다.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import build_session_review as R  # noqa: E402
import forecast_utils as fu  # noqa: E402


def stored_review(events=True):
    """reviews/<날짜>.json 에 저장된 모양 — 시각은 ISO 문자열, 결측은 None."""
    news = [{"time": "2026-09-23T08:30:34+09:00", "title": "키움증권 \"3분기 실적 하회할듯\"", "source": "연합뉴스",
             "link": "https://example.com/a"}]
    return {"target": "samsung", "name": "삼성전자", "peer_name": "SK하이닉스", "session_date": "2026-09-23",
            "generated_at": "2026-09-23 17:28 KST",
            "summary": {"gap": 0.029, "session": -0.012, "c2c": 0.016, "high_vs_open": 0.0018, "low_vs_open": -0.0105,
                        "volume_ratio": None, "kospi_c2c": 0.004, "peer_c2c": None, "usdkrw_chg": -0.001,
                        "sox_ret": 0.015, "nasdaq_ret": 0.008},
            "classification": {"labels": ["갭 주도"], "reasons": ["갭이 종가→종가의 대부분"]},
            "events": ([{"time": "2026-09-23T09:00:00+09:00", "ret": 0.0289, "z": 22.13, "volume_ratio": 7.85,
                         "cum_before": 0.0, "cum_after": 0.0, "news": news}] if events else []),
            "turning_point": {"high_time": "2026-09-23T09:40:00+09:00", "high": 0.0018,
                              "low_time": "2026-09-23T10:50:00+09:00", "low": -0.0105, "pattern": "고점 후 반락"},
            "overnight_news": news, "top_news": news, "disclosures": [], "disclosure_note": None, "flows": None,
            "forecasts": [{"model": "No macro ensemble", "hit": True, "verdict": "맞음", "legs": "갭 맞음",
                           "band": 0.0095, "where": "갭"}],
            "price_check": None, "disclaimer": fu.REVIEW_DISCLAIMER}


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


class RenderFromStoredRecordTests(unittest.TestCase):
    def test_iso_times_and_missing_values_render(self):
        html = fu.review_section_html(stored_review())
        t = text(html)
        self.assertIn("흐름이 바뀐 시각과 그 전후의 뉴스", t)
        self.assertIn("09:00", t)                       # 전환점 시각
        self.assertIn("08:30", t)                       # 뉴스 시각
        self.assertIn("고점 +0.18%(09:40)", t)
        self.assertIn("키움증권", t)
        self.assertIn("거래량 (20일 평균 대비) —", t)   # None 은 '—'
        self.assertNotIn("nan", t.lower())

    def test_carried_heading_names_the_session(self):
        carried = text(fu.review_section_html(stored_review(), carried=True))
        self.assertIn("직전 거래일 장 회고 — 2026-09-23", carried)
        self.assertIn("다음 거래일 보고서를 새로 만들며 이 회고를 다시 붙였습니다", carried)
        today = text(fu.review_section_html(stored_review()))
        self.assertIn("오늘 장 회고 — 2026-09-23", today)
        self.assertNotIn("직전 거래일", today)

    def test_the_real_stored_records_render(self):
        for target in ("samsung", "sk_hynix"):
            path = ROOT / "forecast_history" / target / "reviews" / "2026-09-23.json"
            if not path.exists():
                self.skipTest("저장된 회고 기록이 없습니다")
            with self.subTest(target=target):
                html = fu.review_section_html(json.loads(path.read_text(encoding="utf-8")), carried=True)
                self.assertIn("흐름이 바뀐 시각과 그 전후의 뉴스", html)
                self.assertEqual(html.count(fu.REVIEW_START), 1)


class ToolDelegatesTests(unittest.TestCase):
    def test_tool_uses_the_shared_renderer_and_markers(self):
        self.assertEqual(R.MARK_START, fu.REVIEW_START)
        self.assertEqual(R.MARK_END, fu.REVIEW_END)
        self.assertEqual(R.DISCLAIMER, fu.REVIEW_DISCLAIMER)
        self.assertEqual(R.render_section(stored_review()), fu.review_section_html(stored_review()))

    def test_afternoon_review_replaces_the_carried_one(self):
        page = "<html><body><p>x</p><!--LEDGER_SECTION_END--><p>y</p></body></html>"
        carried = fu.insert_review_section(page, fu.review_section_html(stored_review(), carried=True))
        fresh = dict(stored_review(), session_date="2026-09-28", generated_at="2026-09-28 17:28 KST")
        replaced = R.insert_section(carried, R.render_section(fresh))
        self.assertEqual(replaced.count(fu.REVIEW_START), 1)
        self.assertIn("오늘 장 회고 — 2026-09-28", replaced)
        self.assertNotIn("직전 거래일 장 회고", replaced)
        self.assertLess(replaced.find("<!--LEDGER_SECTION_END-->"), replaced.find(fu.REVIEW_START))


class NotebookWiringTests(unittest.TestCase):
    def test_report_cell_reattaches_the_latest_review_after_tabs(self):
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        cell = next("".join(c["source"]) for c in nb["cells"] if "html = tabify_sections(html)" in "".join(c["source"]))
        self.assertIn("_review = _latest_review(last_samsung_date)", cell)
        self.assertIn("review_section_html(_review, carried=True)", cell)
        self.assertLess(cell.index("html = tabify_sections(html)"), cell.index("insert_review_section(html"))
        self.assertLess(cell.index("insert_review_section(html"), cell.index("_page = ("))
        self.assertIn('Path(GITHUB_LEDGER_DIR) / "reviews"', cell)   # 체크아웃에 있으면 그것부터


if __name__ == "__main__":
    unittest.main()
