# -*- coding: utf-8 -*-
"""보고서 레이아웃: 목차와 접기.

보고서가 14만 자에 절 13개로 불어나 목차 없이는 어디에 무엇이 있는지 알 수 없었다(2026-09-11).
이 두 함수는 완성된 HTML 을 후처리하므로, 태그 균형을 깨뜨리지 않는 것이 가장 중요하다.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import report_html as rh  # noqa: E402

PAGE = ('<div class="wrap">'
        '<h3 style="x">한눈에 보는 쉬운 요약</h3><div>요약 본문</div>'
        '<h3 style="x">1. 다음 거래일 방향 <span style="y">&nbsp;부제입니다</span></h3><div>방향 본문</div>'
        '<h3 style="x">2026-09-11 (금) 예측 vs 실제</h3><div>채점 본문</div>'
        '<h3 style="x">4. 모델 성능</h3><div>성능 본문<table><tr><td>표</td></tr></table></div>'
        '<h3 style="x">7. 장기 전망 (월간)</h3><div>장기 본문</div>'
        '</div>')


def balance(text):
    return {tag: (len(re.findall(rf"<{tag}\b", text)), len(re.findall(rf"</{tag}>", text)))
            for tag in ("div", "table", "details", "h3")}


class NavTests(unittest.TestCase):
    def test_ids_and_toc_are_added(self):
        out, sections = rh.add_report_nav(PAGE)
        self.assertEqual(len(sections), 5)
        self.assertEqual(len(re.findall(r'<h3[^>]*id="sec\d+"', out)), 5)
        self.assertIn("이 보고서의 구성", out)
        self.assertIn('href="#sec1"', out)

    def test_subtitle_is_excluded_from_the_toc(self):
        _, sections = rh.add_report_nav(PAGE)
        titles = [s["title"] for s in sections]
        self.assertIn("1. 다음 거래일 방향", titles)
        self.assertNotIn("부제입니다", " ".join(titles))

    def test_sections_are_grouped_in_reading_order(self):
        _, sections = rh.add_report_nav(PAGE)
        by = {s["title"]: s["group"] for s in sections}
        self.assertEqual(by["한눈에 보는 쉬운 요약"], "요약")
        self.assertEqual(by["1. 다음 거래일 방향"], "예측")
        self.assertEqual(by["2026-09-11 (금) 예측 vs 실제"], "성적")
        self.assertEqual(by["7. 장기 전망 (월간)"], "배경")
        self.assertEqual([name for name, _ in rh.NAV_GROUPS], ["요약", "예측", "성적", "배경"])

    def test_tag_balance_is_preserved(self):
        out, _ = rh.add_report_nav(PAGE)
        for tag, (opens, closes) in balance(out).items():
            self.assertEqual(opens, closes, tag)

    def test_short_pages_are_left_alone(self):
        tiny = '<h3>하나</h3><div>본문</div>'
        out, sections = rh.add_report_nav(tiny)
        self.assertEqual(out, tiny)
        self.assertEqual(len(sections), 1)


class CollapseTests(unittest.TestCase):
    def collapsed(self, text):
        """접힌 절의 제목 목록. h3 마다 잘라 그 직후가 <details> 인지 본다.

        (.*?)</h3>\s*<details 로 찾으면 details 가 나올 때까지 여러 절을 삼킨다.
        """
        titles = []
        for match in re.finditer(r"<h3\b[^>]*>(.*?)</h3>", text, re.S):
            if not text[match.end():].lstrip().startswith("<details"):
                continue
            title = re.sub(r"<span\b.*?</span>", "", match.group(1), flags=re.S)
            title = re.sub(r"<[^>]+>", "", title).replace("&nbsp;", " ")
            titles.append(re.sub(r"\s+", " ", title).strip(" ·"))
        return titles

    def test_evidence_sections_collapse_and_summaries_do_not(self):
        out = rh.collapse_sections(PAGE)
        titles = self.collapsed(out)
        self.assertIn("4. 모델 성능", titles)
        self.assertIn("7. 장기 전망 (월간)", titles)
        self.assertNotIn("한눈에 보는 쉬운 요약", titles)
        self.assertNotIn("1. 다음 거래일 방향", titles)

    def test_last_section_collapses_without_breaking_the_wrapper(self):
        # 마지막 절 본문 끝에는 바깥 래퍼의 </div> 가 붙어 있다. details 안에 갇히면 안 된다.
        out = rh.collapse_sections(PAGE)
        self.assertIn("7. 장기 전망 (월간)", self.collapsed(out))
        for tag, (opens, closes) in balance(out).items():
            self.assertEqual(opens, closes, f"{tag} 균형이 깨졌습니다")
        self.assertTrue(out.rstrip().endswith("</div>"))

    def test_headings_stay_visible(self):
        out = rh.collapse_sections(PAGE)
        self.assertEqual(len(re.findall(r"<h3\b", out)), 5)
        self.assertIn("펼쳐 보기", out)

    def test_nav_after_collapse_still_sees_every_section(self):
        out, sections = rh.add_report_nav(rh.collapse_sections(PAGE))
        self.assertEqual(len(sections), 5)
        for tag, (opens, closes) in balance(out).items():
            self.assertEqual(opens, closes, tag)


class NotebookWiringTests(unittest.TestCase):
    def test_notebook_collapses_then_adds_nav(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        report = next("".join(c["source"]) for c in nb["cells"]
                      if "def build_summary():" in "".join(c.get("source", [])))
        self.assertIn("html = collapse_sections(html)", report)
        self.assertIn("html, _report_sections = add_report_nav(html)", report)
        # 순서가 중요하다: 접은 뒤 목차를 만들어야 id 가 최종 HTML 에 남는다.
        self.assertLess(report.index("collapse_sections(html)"), report.index("add_report_nav(html)"))
