"""장 마감 회고가 '공시·발표 일정' 탭의 최근 공시 목록도 다시 채운다(2026-10-02).

공시 목록은 보고서를 만들 때만 받아, 장중 공시가 저녁이나 다음 날 아침에야 그 탭에 보였다(2026-09-28 지적).
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import report_html as rh  # noqa: E402
import build_session_review as R  # noqa: E402
from data_sources.dart import classify_disclosure  # noqa: E402

OLD = [{"rcept_dt": "20260925", "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서", "url": "https://dart.fss.or.kr/a"}]
NEW = OLD + [{"rcept_dt": "20261002", "report_nm": "연결재무제표기준영업(잠정)실적(공정공시) <b>", "url": "https://dart.fss.or.kr/b"}]
INFO = {"enabled": True}


def page(disclosures=OLD, info=INFO):
    body = ('<div class="wrap"><h3>한눈에 보는 쉬운 요약</h3><p>오늘 예측</p>'
            + rh.disclosure_section_html(disclosures, info, classify_disclosure)
            + '<div id="upcoming">예정 발표 표</div></div>')
    return rh.tabify_sections(body)


class ReplaceBlockTests(unittest.TestCase):
    def test_only_the_list_changes(self):
        before = page()
        after = rh.replace_disclosure_block(before, NEW, INFO, classify_disclosure)
        self.assertIn("20261002"[:4] + "-10-02", after)
        self.assertIn("영업(잠정)실적(공정공시) &lt;b&gt;", after)             # 제목은 이스케이프된다
        self.assertEqual(after.count("예정 발표 표"), 1)
        self.assertEqual(after.count("참고 정보: 최근 공시와 예정 발표"), 1)
        self.assertEqual(after, page(NEW))                                    # 보고서를 새로 만든 것과 같은 모양
        self.assertEqual(rh.tab_structure_problems(after), [])

    def test_same_list_changes_nothing(self):
        self.assertEqual(rh.replace_disclosure_block(page(), OLD, INFO, classify_disclosure), page())

    def test_empty_and_disabled_pages_are_replaced_too(self):
        for start in (page([], INFO), page([], {"enabled": False, "reason": "DART_API_KEY 없음"})):
            self.assertEqual(rh.replace_disclosure_block(start, NEW, INFO, classify_disclosure), page(NEW))

    def test_list_under_a_stamp_line_is_found(self):
        import forecast_utils as fu
        before = page()
        panel = [p["attrs"]["id"] for p in rh.panels(before) if "참고 정보" in p["inner"]][0]
        stamped = fu.stamp_panel(before, panel, "생성 2026-10-02 06:30 KST")
        after = rh.replace_disclosure_block(stamped, NEW, INFO, classify_disclosure)
        self.assertIn("2026-10-02</td>", after)
        self.assertIn("생성 2026-10-02 06:30 KST", after)

    def test_page_without_the_section_is_untouched(self):
        self.assertEqual(rh.replace_disclosure_block("<html><body>x</body></html>", NEW, INFO, classify_disclosure),
                         "<html><body>x</body></html>")


class RefreshTests(unittest.TestCase):
    NOW = pd.Timestamp("2026-10-02 16:12", tz="Asia/Seoul")

    def test_refresh_asks_for_the_last_ten_days_and_stamps_the_tab(self):
        asked = []

        def fetch(start, stop):
            asked.append((start.date().isoformat(), stop.date().isoformat()))
            return NEW
        out = R.refresh_recent_disclosures(page(), "00126380", self.NOW, fetch=fetch)
        self.assertEqual(asked, [("2026-09-22", "2026-10-02")])
        self.assertIn("2026-10-02</td>", out)
        self.assertEqual(out.count("갱신 2026-10-02 16:12 KST"), 1)             # 바뀐 탭에만 찍는다

    def test_unchanged_list_leaves_the_page_and_its_stamp_alone(self):
        self.assertEqual(R.refresh_recent_disclosures(page(), "00126380", self.NOW, fetch=lambda a, b: OLD), page())

    def test_failed_fetch_keeps_the_morning_list(self):
        def broken(start, stop):
            raise RuntimeError("DART 500")
        self.assertEqual(R.refresh_recent_disclosures(page(), "00126380", self.NOW, fetch=broken), page())

    def test_no_key_keeps_the_page(self):
        from unittest.mock import patch
        with patch("data_sources.dart.dart_key_optional", return_value=None):
            self.assertEqual(R.refresh_recent_disclosures(page(), "00126380", self.NOW), page())

    def test_review_publish_refreshes_the_list(self):
        source = (ROOT / "tools" / "build_session_review.py").read_text(encoding="utf-8")
        self.assertIn("refresh_recent_disclosures(html_text, spec_corp, now)", source)
        self.assertIn("finish(insert_section(latest, section) if latest else ours)", source)


if __name__ == "__main__":
    unittest.main()
