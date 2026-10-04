"""장기 전망은 메뉴 하나다(2026-10-02).

길다는 이유로 둘(요약·영업이익 / 장기 전망)·셋(+ 지난 전망 성적)으로 나눠 봤다가 같은 날 되돌렸다 — 메뉴가 늘자
무엇이 어디 있는지, 서로 무엇이 다른지 알기 어려웠다. 나뉘어 있던 때 발행된 페이지를 매일 오후 갱신 도구가
만나도 내용이 빠지거나 두 번 들어가면 안 된다.
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

TWO = (("장기 요약 · 영업이익", ("한눈에 보는 장기 전망 요약", "이번 분기 영업이익")),
       ("장기 전망", ("장기 전망", "지난 전망은 맞았나")), ("예측 성적", ("이 모델의 예측 성적",)))
THREE = (("장기 요약 · 영업이익", ("한눈에 보는 장기 전망 요약", "이번 분기 영업이익")),
         ("장기 전망", ("장기 전망",)), ("지난 전망 성적", ("지난 전망은 맞았나",)),
         ("예측 성적", ("이 모델의 예측 성적",)))


def h3(title):
    return f'<h3 style="x">{title}</h3>'


def content(tag):
    return ('<section id="longterm-summary">' + h3("한눈에 보는 장기 전망 요약") + f'<p>요약 {tag}</p></section>'
            + h3("1. 이번 분기 영업이익 추정") + f'<p>영업이익 {tag}</p>'
            + h3("2. 장기 전망 (월간)") + f'<p>장기 {tag}</p><h4>지금 위치</h4><p>위치 {tag}</p>'
            + h3("3. 지난 전망은 맞았나") + f'<p>성적 {tag}</p>')


def page(tag="OLD", groups=rh.TAB_GROUPS):
    body = ('<div class="wrap">' + h3("한눈에 보는 쉬운 요약") + '<p>오늘 예측</p>' + content(tag)
            + h3("이 모델의 예측 성적") + '<p>성능</p></div>')
    return rh.tabify_sections(body, groups=groups)


def labels(text):
    return re.findall(r'<a href="#rtab-\d+" aria-selected="\w+">([^<]+)</a>', text)


def panel_text(text):
    return {p["attrs"]["id"]: re.sub(r"<[^>]+>", " ", p["inner"]) for p in rh.panels(text)}


class OneMenuTests(unittest.TestCase):
    def test_everything_long_term_is_in_one_menu_in_this_order(self):
        self.assertEqual(labels(page()), ["오늘의 장 예측", "장기 전망", "예측 성적"])
        titles = [re.findall(r"<h3[^>]*>([^<]+)</h3>", p["inner"]) for p in rh.panels(page())]
        self.assertEqual(titles[1], ["1. 한눈에 보는 장기 전망 요약", "2. 이번 분기 영업이익 추정",
                                     "3. 장기 전망 (월간)", "4. 지난 전망은 맞았나"])
        self.assertEqual(rh.tab_structure_problems(page()), [])

    def test_menu_names_that_confused_are_gone(self):
        names = [label for label, _ in rh.TAB_GROUPS]
        self.assertNotIn("장기 요약 · 영업이익", names)
        self.assertNotIn("지난 전망 성적", names)        # '예측 성적'과 헷갈렸다

    def test_sections_by_tab_keeps_a_flat_fragment_whole(self):
        groups = rh.sections_by_tab(content("X"))
        self.assertEqual([label for label, _ in groups], ["장기 전망"])
        self.assertEqual(groups[0][1], content("X"))               # 빠지거나 겹치는 글자가 없다
        self.assertEqual(rh.sections_by_tab("NEW"), [(None, "NEW")])


class RefreshTests(unittest.TestCase):
    def check_merged(self, out, anchor="rtab-1"):
        text = panel_text(out)
        for word in ("요약 NEW", "영업이익 NEW", "장기 NEW", "위치 NEW", "성적 NEW"):
            self.assertIn(word, text[anchor], word)
            self.assertEqual(out.count(word), 1, word)             # 한 번씩만 들어간다
        self.assertNotIn("OLD", out)
        self.assertEqual(rh.tab_structure_problems(out), [])
        return text

    def test_single_menu_page_is_replaced_in_place(self):
        out, touched = rt.replace_tab_sections(page("OLD"), content("NEW"))
        text = self.check_merged(out)
        self.assertEqual(touched, ["rtab-1"])
        self.assertIn("오늘 예측", text["rtab-0"])                # 다른 탭은 그대로
        self.assertIn("성능", text["rtab-2"])

    def test_refreshing_twice_changes_nothing(self):
        once, _ = rt.replace_tab_sections(page("OLD"), content("NEW"))
        self.assertEqual(rt.replace_tab_sections(once, content("NEW"))[0], once)

    def test_page_split_in_two_gets_everything_in_the_first_and_a_pointer_in_the_second(self):
        """둘로 나뉘어 있던 때(2026-10-02) 발행된 페이지."""
        out, _ = rt.replace_tab_sections(page("OLD", TWO), content("NEW"))
        text = self.check_merged(out)
        self.assertIn("메뉴로 합쳤습니다", text["rtab-2"])
        self.assertEqual(rt.replace_tab_sections(out, content("NEW"))[0], out)

    def test_page_split_in_three_gets_pointers_in_both_old_menus(self):
        out, _ = rt.replace_tab_sections(page("OLD", THREE), content("NEW"))
        text = self.check_merged(out)
        self.assertIn("메뉴로 합쳤습니다", text["rtab-2"])
        self.assertIn("메뉴로 합쳤습니다", text["rtab-3"])
        self.assertIn("성능", text["rtab-4"])

    def test_page_without_the_summary_is_left_alone(self):
        with self.assertRaises(ValueError):
            rt.replace_tab_sections("<html>no longterm panel</html>", content("NEW"))


if __name__ == "__main__":
    unittest.main()
