# -*- coding: utf-8 -*-
"""'최신 뉴스 및 트렌드' 한 페이지(2026-09-13). AI 뉴스·검색어·장기 관심도를 탭으로 묶는다."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_news_hub as hub  # noqa: E402

TOOLS = ("build_ai_news_report.py", "build_trends_report.py", "build_interest_report.py")


class HubPageTests(unittest.TestCase):
    def setUp(self):
        self.page = hub.build_hub()

    def test_three_tabs_in_this_order(self):
        labels = re.findall(r'<a href="#\w+" data-tab="\w+" aria-selected="(?:true|false)">(.*?)</a>', self.page)
        self.assertEqual(labels, ["최신 AI 뉴스", "인기 급상승 검색어", "장기 관심도"])

    def test_the_first_tab_is_selected_by_default(self):
        self.assertEqual(re.findall(r'data-tab="(\w+)" aria-selected="true"', self.page), ["ai_news"])

    def test_each_tab_shows_the_existing_page(self):
        """틀만 둔다. 세 페이지를 다시 조립하면 갱신 주기가 다른 잡들이 같은 파일을 쓰게 된다."""
        self.assertEqual(re.findall(r'data-src="([^"]+)"', self.page), ["../ai_news/", "../trends/", "../interest/"])

    def test_tabs_load_their_page_only_when_first_opened(self):
        self.assertNotIn('<iframe title="최신 AI 뉴스" src=', self.page)
        self.assertIn('frame.setAttribute("src",frame.getAttribute("data-src"))', self.page)

    def test_nothing_is_hidden_without_the_script(self):
        for panel in re.findall(r'<section class="hub-panel"[^>]*>', self.page):
            self.assertNotIn("hidden", panel)
        self.assertIn(".hub-on .hub-panel{display:none}", self.page)
        self.assertEqual(self.page.count("<noscript>"), 3, "스크립트가 없으면 각 페이지로 가는 링크를 보여야 한다")

    def test_deep_links_and_clicks_select_a_tab(self):
        self.assertIn('addEventListener("hashchange",route)', self.page)
        self.assertIn('history.replaceState(null,"","#"+key)', self.page)

    def test_frames_fit_their_content_and_drop_the_duplicate_title(self):
        """틀 안에 스크롤바가 생기면 안 되고, 탭 이름과 같은 큰 제목이 두 번 보여도 안 된다."""
        self.assertIn("scrollHeight", self.page)
        self.assertIn("ResizeObserver", self.page)
        self.assertIn(".page-title{display:none!important}", self.page)

    def test_links_leaving_a_tab_open_in_the_whole_window(self):
        """탭 안에서 다른 페이지가 열리면 틀에 갇힌다."""
        self.assertIn('links[i].setAttribute("target","_top")', self.page)

    def test_the_tab_bar_sticks_and_the_page_does_not_scroll_sideways(self):
        self.assertIn("position:sticky", self.page)
        self.assertIn("overflow-x:clip", self.page)

    def test_the_hub_does_not_count_views_itself(self):
        """탭 안 페이지가 스스로 센다. 틀까지 세면 같은 방문이 두 번 잡힌다."""
        self.assertNotIn("/hit?page=", self.page)

    def test_the_committed_page_matches_the_generator(self):
        self.assertEqual(hub.HUB_PATH.read_text(encoding="utf-8"), self.page,
                         "docs/news/index.html 을 python tools/build_news_hub.py --write 로 다시 만드세요")


class MenuTests(unittest.TestCase):
    def test_the_main_menu_has_one_card_for_all_three(self):
        index = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="./news/"', index)
        self.assertIn("최신 뉴스 및 트렌드", index)
        for old in ('href="./ai_news/"', 'href="./trends/"', 'href="./interest/"'):
            self.assertNotIn(old, index)

    def test_the_card_sits_where_the_three_used_to_be(self):
        index = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertLess(index.index('href="./metals/"'), index.index('href="./news/"'))


class TitleHookTests(unittest.TestCase):
    """틀이 숨길 수 있도록 세 페이지의 제목에 class 를 단다. 따로 열면 그대로 보인다."""

    def test_each_page_marks_its_title(self):
        for name in TOOLS:
            with self.subTest(tool=name):
                source = (ROOT / "tools" / name).read_text(encoding="utf-8")
                self.assertIn('<div class="page-title" style="font-size:11px;letter-spacing:2px', source)
                self.assertIn('<h2 class="page-title"', source)

    def test_the_ai_news_page_still_shows_its_title_when_opened_alone(self):
        from datetime import datetime, timedelta, timezone
        import build_ai_news_report as ai
        page = ai.build_html([], datetime(2026, 9, 13, 14, 0, tzinfo=timezone(timedelta(hours=9))), 0, [])
        self.assertEqual(page.count('class="page-title"'), 2)
        self.assertNotIn(".page-title{display:none", page)


if __name__ == "__main__":
    unittest.main()
