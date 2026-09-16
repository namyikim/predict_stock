# -*- coding: utf-8 -*-
"""거시 경제 보고서 — 환율·금리·채권 등을 모아 보는 페이지(2026-09-15 시작).

지금은 뼈대만 있고 지표는 하나씩 붙인다. 이 테스트는 뼈대가 무너지지 않게 지킨다.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_macro_report as macro  # noqa: E402


class PageTests(unittest.TestCase):
    def page(self):
        return macro.build_page(datetime(2026, 9, 15, 9, 0, tzinfo=timezone(timedelta(hours=9))))

    def test_has_the_standard_back_button(self):
        html = self.page()
        self.assertIn("← 보고서 목록", html)
        self.assertIn("border:1px solid #cedff0;border-radius:5px", html)
        self.assertLess(html.index("← 보고서 목록"), html.index("거시 경제</h2>"))

    def test_only_one_back_link(self):
        self.assertEqual(self.page().count('href="../"'), 1)

    def test_every_planned_section_is_rendered(self):
        html = self.page()
        for title, _, items in macro.SECTIONS:
            self.assertIn(f">{title}</h3>", html)
            for item in items:
                self.assertIn(item, html)

    def test_states_that_it_is_not_a_forecast(self):
        # 이 저장소의 다른 보고서와 같은 태도를 유지한다.
        html = self.page()
        self.assertIn("예측이 아니라 자료 정리입니다", html)
        self.assertIn("투자 자문이 아닙니다", html)

    def test_empty_sections_say_so_instead_of_guessing(self):
        html = self.page()
        self.assertIn("준비 중입니다", html)
        self.assertIn("추측해 채우지 않습니다", html)

    def test_tag_balance(self):
        import re
        html = self.page()
        for tag in ("div", "ul", "h3", "body", "html"):
            self.assertEqual(len(re.findall(rf"<{tag}\b", html)),
                             len(re.findall(rf"</{tag}>", html)), tag)

    def test_committed_page_matches_the_generator(self):
        import re
        committed = (ROOT / "docs" / "macro" / "index.html").read_text(encoding="utf-8")
        strip = lambda text: re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", "", text)
        self.assertEqual(strip(committed), strip(self.page()),
                         "docs/macro/index.html 을 python tools/build_macro_report.py --write 로 다시 만드세요")


class LandingTests(unittest.TestCase):
    def test_landing_links_to_the_macro_page_after_metals(self):
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="./macro/"', html)
        self.assertLess(html.index('href="./metals/"'), html.index('href="./macro/"'),
                        "금·은 아래에 두기로 했다")
        self.assertLess(html.index('href="./macro/"'), html.index('href="./news/"'))
