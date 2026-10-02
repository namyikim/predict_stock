"""장기 전망을 메뉴 셋으로 나눈다(2026-10-02): 요약·영업이익 / 장기 전망 / 지난 전망 성적.

탭 하나가 화면 열 장 분량이었다. 일일 보고서가 탭을 나눌 때와, 매일 오후 그 내용만 바꿔 끼울 때가 같은 표
(report_html.TAB_GROUPS)를 써야 한다 — 한쪽만 나누면 오후 갱신 때 내용이 한 탭에 몰리거나 두 번 들어간다.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import report_html as rh  # noqa: E402
import refresh_longterm_tab as rt  # noqa: E402


def h3(title):
    return f'<h3 style="x">{title}</h3>'


def content(tag):
    return ('<section id="longterm-summary">' + h3("한눈에 보는 장기 전망 요약") + f'<p>요약 {tag}</p></section>'
            + h3("1. 이번 분기 영업이익 추정") + f'<p>영업이익 {tag}</p>'
            + h3("2. 장기 전망 (월간)") + f'<p>장기 {tag}</p><h4>지금 위치</h4><p>위치 {tag}</p>'
            + h3("3. 지난 전망은 맞았나") + f'<p>성적 {tag}</p>')


def page(tag="OLD"):
    body = ('<div class="wrap">' + h3("한눈에 보는 쉬운 요약") + '<p>오늘 예측</p>' + content(tag)
            + h3("이 모델의 예측 성적") + '<p>성능</p></div>')
    return rh.tabify_sections(body)


def panel_text(text):
    return {p["attrs"]["id"]: re.sub(r"<[^>]+>", " ", p["inner"]) for p in rh.panels(text)}


class SplitTests(unittest.TestCase):
    def test_three_menus_in_order(self):
        labels = re.findall(r'<a href="#rtab-\d+" aria-selected="\w+">([^<]+)</a>', page())
        self.assertEqual(labels, ["오늘의 예측", "장기 요약 · 영업이익", "장기 전망", "지난 전망 성적", "예측 성적"])
        self.assertEqual(rh.tab_structure_problems(page()), [])

    def test_sections_by_tab_sorts_a_flat_fragment(self):
        groups = rh.sections_by_tab(content("X"))
        self.assertEqual([label for label, _ in groups], ["장기 요약 · 영업이익", "장기 전망", "지난 전망 성적"])
        self.assertIn('<section id="longterm-summary">', groups[0][1])      # 요약의 감싸개가 제 절과 함께 간다
        self.assertEqual(groups[0][1].count("<section"), groups[0][1].count("</section>"))
        self.assertIn("위치 X", groups[1][1])
        self.assertEqual("".join(html for _, html in groups), content("X"))   # 빠지거나 겹치는 글자가 없다
        self.assertEqual(rh.sections_by_tab("NEW"), [(None, "NEW")])


class RefreshTests(unittest.TestCase):
    def test_each_section_lands_in_its_own_menu(self):
        out, touched = rt.replace_tab_sections(page("OLD"), content("NEW"))
        text = panel_text(out)
        self.assertEqual(touched and sorted(touched), ["rtab-1", "rtab-2", "rtab-3"])
        for panel, new in (("rtab-1", ("요약 NEW", "영업이익 NEW")), ("rtab-2", ("장기 NEW", "위치 NEW")),
                           ("rtab-3", ("성적 NEW",))):
            for word in new:
                self.assertIn(word, text[panel])
        self.assertNotIn("OLD", out)
        self.assertEqual(out.count("NEW"), 5)                    # 한 번씩만 들어간다
        self.assertIn("오늘 예측", text["rtab-0"])                # 다른 탭은 그대로
        self.assertIn("성능", text["rtab-4"])
        self.assertEqual(rh.tab_structure_problems(out), [])

    def test_separate_menus_are_numbered_from_one(self):
        out, _ = rt.replace_tab_sections(page("OLD"), content("NEW"))
        titles = {p["attrs"]["id"]: re.findall(r"<h3[^>]*>([^<]+)</h3>", p["inner"]) for p in rh.panels(out)}
        self.assertEqual(titles["rtab-2"], ["1. 장기 전망 (월간)"])
        self.assertEqual(titles["rtab-3"], ["1. 지난 전망은 맞았나"])

    def test_refreshing_twice_changes_nothing(self):
        once, _ = rt.replace_tab_sections(page("OLD"), content("NEW"))
        twice, _ = rt.replace_tab_sections(once, content("NEW"))
        self.assertEqual(twice, once)

    def test_old_single_tab_page_keeps_everything_in_that_tab(self):
        """일일 보고서가 새 구조로 다시 만들기 전의 페이지: 탭 하나에 장기 내용이 모두 들어 있다."""
        old_groups = (("장기 전망", ("한눈에 보는 장기 전망 요약", "이번 분기 영업이익", "장기 전망", "지난 전망은 맞았나")),
                      ("예측 성적", ("이 모델의 예측 성적",)))
        body = ('<div class="wrap">' + h3("한눈에 보는 쉬운 요약") + '<p>오늘 예측</p>' + content("OLD")
                + h3("이 모델의 예측 성적") + '<p>성능</p></div>')
        old_page = rh.tabify_sections(body, groups=old_groups)
        out, touched = rt.replace_tab_sections(old_page, content("NEW"))
        text = panel_text(out)
        self.assertEqual(touched, ["rtab-1"])
        for word in ("요약 NEW", "영업이익 NEW", "장기 NEW", "성적 NEW"):
            self.assertIn(word, text["rtab-1"])
        self.assertNotIn("OLD", out)
        self.assertEqual(out.count("NEW"), 5)
        self.assertEqual(rh.tab_structure_problems(out), [])

    def test_a_missing_menu_falls_back_to_the_summary_tab(self):
        """지난 전망 절이 없던 페이지에 성적 절이 새로 생기면 요약 탭에 붙는다 — 빠뜨리지 않는다."""
        body = ('<div class="wrap">' + h3("한눈에 보는 쉬운 요약") + '<p>오늘 예측</p>'
                + content("OLD").split(h3("3. 지난 전망은 맞았나"))[0] + h3("이 모델의 예측 성적") + '<p>성능</p></div>')
        out, touched = rt.replace_tab_sections(rh.tabify_sections(body), content("NEW"))
        text = panel_text(out)
        self.assertIn("성적 NEW", text["rtab-1"])
        self.assertIn("장기 NEW", text["rtab-2"])
        self.assertEqual(out.count("성적 NEW"), 1)

    def test_page_without_the_summary_is_left_alone(self):
        with self.assertRaises(ValueError):
            rt.replace_tab_sections("<html>no longterm panel</html>", content("NEW"))


if __name__ == "__main__":
    unittest.main()
