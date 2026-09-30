"""탭·페이지 제목 아래 생성 시각(2026-09-30 요청: 제목 아래, 실제 내용이 시작하기 전)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import forecast_utils as fu  # noqa: E402
import report_html as rh  # noqa: E402


def page():
    h3 = lambda t: f'<h3 style="x">{t}</h3><p>본문 {t}</p>'
    return rh.tabify_sections('<div>' + h3("1. 다음 거래일 방향") + h3("한눈에 보는 장기 전망 요약")
                              + h3("주간 반도체 뉴스") + '</div>')


class StampTests(unittest.TestCase):
    def test_kst_text(self):
        self.assertEqual(fu.kst_stamp("2026-09-30 08:22"), "생성 2026-09-30 08:22 KST")
        self.assertEqual(fu.kst_stamp("2026-09-29T23:10:00+00:00", "갱신"), "갱신 2026-09-30 08:10 KST")

    def test_every_tab_gets_one_line_right_after_its_title(self):
        import re
        out = fu.stamp_all_panels(page(), "생성 2026-09-30 08:22 KST")
        for panel in re.findall(r'<section class="rtab-panel" id="([\w-]+)">', out):
            inner = out[out.find(f'id="{panel}"'):]
            self.assertRegex(inner, r'^[^<]*>\s*(<[^h][^>]*>)*<h3[^>]*>.*?</h3><div class="gen-stamp"')
        self.assertEqual(rh.tab_structure_problems(out), [])

    def test_restamping_replaces_not_adds(self):
        once = fu.stamp_all_panels(page(), "생성 2026-09-30 08:22 KST")
        twice = fu.stamp_all_panels(once, "생성 2026-09-30 18:00 KST")
        self.assertEqual(twice.count('class="gen-stamp"'), once.count('class="gen-stamp"'))
        self.assertNotIn("08:22", twice)

    def test_partial_update_stamps_only_changed_tabs(self):
        before = fu.stamp_all_panels(page(), "생성 2026-09-30 08:22 KST")
        after = before.replace("본문 주간 반도체 뉴스", "본문 바뀜")
        out = fu.stamp_changed_panels(before, after, "갱신 2026-09-30 16:12 KST")
        self.assertEqual(out.count("갱신 2026-09-30 16:12 KST"), 1)
        self.assertEqual(out.count("생성 2026-09-30 08:22 KST"), 2)

    def test_review_tab_stamps_itself_with_the_review_time(self):
        import json
        review = json.loads((ROOT / "forecast_history" / "samsung" / "reviews" / "2026-09-29.json").read_text(encoding="utf-8"))
        html = fu.review_tab_html([review])
        self.assertIn(f'생성 {review["generated_at"]}</div>', html)
        self.assertLess(html.find("</h3>"), html.find('class="gen-stamp"'))
        self.assertLess(html.find('class="gen-stamp"'), html.find('id="review-status"'))


class WriterTests(unittest.TestCase):
    def test_notebook_stamps_all_tabs_but_the_review(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        source = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
        self.assertIn("html = stamp_all_panels(html, kst_stamp(), skip=(REVIEW_TAB_ID,))", source)

    def test_long_term_refresh_ignores_stamp_only_differences(self):
        import refresh_longterm_tab as R
        stamped = fu.stamp_all_panels(page(), "생성 2026-09-30 08:22 KST")
        self.assertEqual(R.without_stamps(stamped), R.without_stamps(fu.stamp_all_panels(page(), "생성 2026-09-30 18:00 KST")))
        self.assertEqual(R.longterm_panel_id('<section class="rtab-panel" id="rtab-1"><section id="longterm-summary">'), "rtab-1")

    def test_scoring_refresh_marks_changed_tabs(self):
        text = (ROOT / "tools" / "build_afternoon_update.py").read_text(encoding="utf-8")
        self.assertIn('return stamp_changed_panels(page, updated, kst_stamp(now, "갱신"))', text)

    def test_interest_and_lab_pages_show_time_under_the_title(self):
        interest = (ROOT / "tools" / "build_interest_report.py").read_text(encoding="utf-8")
        self.assertIn("연평균 증가율 순 · 생성 {now:%Y-%m-%d %H:%M} KST", interest)
        lab = (ROOT / "docs" / "lab" / "index.html").read_text(encoding="utf-8")
        self.assertLess(lab.find("<h1>실험실"), lab.find('id="lab-stamp"'))
        self.assertIn("showLabStamp(all);", lab)


if __name__ == "__main__":
    unittest.main()
