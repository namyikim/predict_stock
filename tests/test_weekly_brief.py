# -*- coding: utf-8 -*-
"""주간 반도체 뉴스 탭.

reports/ 에 월요일 아침마다 올라오는 브리핑을 보고서에 그대로 보여 준다(2026-09-14 요청).
예측에 쓰지 않는 참고 자료이므로, 없으면 절 자체가 나오지 않아야 한다.
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import report_html as rh  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
import should_run_today as srt  # noqa: E402

SAMPLE = """# 메모리 반도체 주간 브리핑 — 2026년 9월 14일

**대상 기간:** 2026년 9월 8일~14일

## 한눈에 보는 요약

메모리 업황은 **강하지만** 원화 강세가 겹쳤습니다. [관세청](https://example.com/a) 자료 기준입니다.

- **한국 수출:** 9월 1~10일 반도체 수출 약 165억 달러
- **가격:** DDR5 는 올랐고 DDR4 는 내렸습니다

### 1. 한국 반도체 수출

| 발표일 | 항목 | 결과 |
|---|---|---:|
| 2026-09-11 | 반도체 수출 | 약 165억 달러 |
"""


class MarkdownTests(unittest.TestCase):
    def test_tables_links_lists_and_emphasis(self):
        html = rh.markdown_to_html(SAMPLE)
        self.assertIn("<table", html)
        self.assertIn('href="https://example.com/a"', html)
        self.assertIn("<ul", html)
        self.assertIn("<b>강하지만</b>", html)

    def test_tag_balance(self):
        html = rh.markdown_to_html(SAMPLE)
        for tag in ("table", "ul", "div", "h4"):
            self.assertEqual(len(re.findall(rf"<{tag}\b", html)),
                             len(re.findall(rf"</{tag}>", html)), tag)

    def test_separator_row_is_dropped(self):
        html = rh.markdown_to_html(SAMPLE)
        self.assertNotIn("---", re.sub(r"<[^>]+>", "", html))

    def test_h1_is_dropped_because_the_section_title_replaces_it(self):
        self.assertNotIn("메모리 반도체 주간 브리핑", rh.markdown_to_html(SAMPLE))

    def test_html_in_source_is_escaped(self):
        html = rh.markdown_to_html("본문 <script>alert(1)</script> 끝")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


class SectionTests(unittest.TestCase):
    def write(self, name):
        root = Path(tempfile.mkdtemp())
        (root / "reports").mkdir()
        (root / "reports" / name).write_text(SAMPLE, encoding="utf-8")
        return root

    def test_section_uses_the_newest_local_file(self):
        import os
        root = self.write("2026-09-07-memory-semiconductor-brief.md")
        (root / "reports" / "2026-09-14-memory-semiconductor-brief.md").write_text(
            SAMPLE, encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(root)
        try:
            text, name = rh.latest_weekly_brief("x/y", "main")
        finally:
            os.chdir(cwd)
        self.assertEqual(name, "2026-09-14-memory-semiconductor-brief.md")

    def test_section_has_title_date_and_source_link(self):
        import os
        root = self.write("2026-09-14-memory-semiconductor-brief.md")
        cwd = os.getcwd()
        os.chdir(root)
        try:
            html = rh.weekly_brief_html("namyikim/predict_stock", "main")
        finally:
            os.chdir(cwd)
        self.assertIn("주간 반도체 뉴스", html)
        self.assertIn("2026-09-14", html)
        self.assertIn("예측에 쓰지 않는 참고 자료", html)
        self.assertIn("blob/main/reports/", html)

    def test_missing_brief_renders_nothing(self):
        import os
        root = Path(tempfile.mkdtemp())
        (root / "reports").mkdir()
        cwd = os.getcwd()
        os.chdir(root)
        try:
            # 네트워크가 막힌 환경에서도 빈 문자열이어야 한다(절이 통째로 빠진다).
            html = rh.weekly_brief_html("no/such-repo", "main")
        finally:
            os.chdir(cwd)
        self.assertEqual(html, "")


class TabAndGateTests(unittest.TestCase):
    def test_tab_is_registered(self):
        labels = [name for name, _ in rh.TAB_GROUPS]
        self.assertIn("주간 뉴스", labels)
        keys = dict(rh.TAB_GROUPS)["주간 뉴스"]
        self.assertIn("주간 반도체 뉴스", keys)

    def test_gate_checks_the_weekly_brief(self):
        """브리핑은 docs/ 밖이라 조각 검사에 안 걸린다. 별도 검사가 게이트에 연결돼 있어야 한다."""
        source = (ROOT / "tools" / "should_run_today.py").read_text(encoding="utf-8")
        self.assertIn("def weekly_brief_newer_than_report(", source)
        self.assertIn("weekly_brief_newer_than_report(target, ref)", source)

    def test_notebook_renders_the_section(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        source = "\n".join("".join(c["source"]) for c in nb["cells"])
        self.assertIn("weekly_html = weekly_brief_html(GITHUB_REPO, GITHUB_BRANCH)", source)
        self.assertIn("f'{weekly_html}'", source)
        # 실패해도 보고서는 만들어져야 한다.
        self.assertIn('weekly_html = ""', source)
