# -*- coding: utf-8 -*-
"""보고서 레이아웃: 절 id 와 상단 탭.

보고서가 14만 자에 절 13개로 불어나 한 줄로 이어 두면 어디에 무엇이 있는지 알 수 없었다(2026-09-11).
이 함수들은 완성된 HTML 을 후처리하므로, 태그 균형을 깨뜨리지 않는 것이 가장 중요하다.
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
        '<h3 style="x">이 모델의 예측 성적</h3><div>성능 본문<table><tr><td>표</td></tr></table></div>'
        '<h3 style="x">1. 장기 전망 (월간)</h3><div>장기 본문</div>'
        '<h3 style="x">이 보고서의 데이터</h3><div>데이터 본문</div>'
        '</div>')


def balance(text):
    return {tag: (len(re.findall(rf"<{tag}\b", text)), len(re.findall(rf"</{tag}>", text)))
            for tag in ("div", "table", "details", "h3")}


class SectionIdTests(unittest.TestCase):
    def test_every_heading_gets_an_id(self):
        out, sections = rh.add_section_ids(PAGE)
        self.assertEqual(len(sections), 6)
        self.assertEqual(len(re.findall(r'<h3[^>]*id="sec\d+"', out)), 6)
        self.assertEqual([s["id"] for s in sections], [f"sec{i}" for i in range(1, 7)])

    def test_no_table_of_contents_is_inserted(self):
        """탭이 목차 노릇을 한다. 목차는 첫 탭 맨 위에서 쉬운 요약을 밀어낼 뿐이었다(2026-09-13)."""
        out, _ = rh.add_section_ids(rh.tabify_sections(PAGE))
        self.assertNotIn("이 보고서의 구성", out)
        self.assertNotIn('href="#sec', out)
        self.assertFalse(hasattr(rh, "add_report_nav"))
        self.assertFalse(hasattr(rh, "NAV_GROUPS"))

    def test_subtitle_is_excluded_from_the_title(self):
        _, sections = rh.add_section_ids(PAGE)
        titles = [s["title"] for s in sections]
        self.assertIn("1. 다음 거래일 방향", titles)
        self.assertNotIn("부제입니다", " ".join(titles))

    def test_existing_ids_are_kept(self):
        out, sections = rh.add_section_ids('<h3 id="keep">가</h3><h3>나</h3>')
        self.assertIn('<h3 id="keep">가</h3>', out)
        self.assertIn('<h3 id="sec2">나</h3>', out)
        self.assertEqual(len(sections), 2)

    def test_tag_balance_is_preserved(self):
        out, _ = rh.add_section_ids(PAGE)
        for tag, (opens, closes) in balance(out).items():
            self.assertEqual(opens, closes, tag)


def panel_titles(text):
    """탭 패널마다 들어 있는 절 제목. {패널 id: [제목, ...]}"""
    out = {}
    for panel_id in re.findall(r'<section class="rtab-panel" id="(rtab-\d+)">', text):
        titles = []
        for head in re.finditer(r"<h3\b[^>]*>(.*?)</h3>", panel_titles_raw(text, panel_id), re.S):
            title = re.sub(r"<span\b.*?</span>", "", head.group(1), flags=re.S)
            title = re.sub(r"<[^>]+>", "", title).replace("&nbsp;", " ")
            titles.append(re.sub(r"\s+", " ", title).strip(" ·"))
        out[panel_id] = titles
    return out


def tab_labels(text):
    return re.findall(r'<a href="#rtab-\d+" aria-selected="(?:true|false)">(.*?)</a>', text)


def tab_balance(text):
    counts = balance(text)
    for tag in ("section", "nav"):
        counts[tag] = (len(re.findall(rf"<{tag}\b", text)), len(re.findall(rf"</{tag}>", text)))
    return counts


class TabTests(unittest.TestCase):
    """접던 절을 상단 탭으로 바꾼다(2026-09-13). '펼쳐 보기'를 찾아 누르는 것이 불편했다."""

    def test_conclusions_stay_in_the_first_tab_and_evidence_gets_its_own_tabs(self):
        panels = panel_titles(rh.tabify_sections(PAGE))
        self.assertEqual(panels["rtab-0"], ["1. 한눈에 보는 쉬운 요약", "2. 다음 거래일 방향"])
        # 탭은 문서 순서다. 이 조각에서는 성적 절이 장기 전망보다 앞에 있다.
        self.assertEqual(panels["rtab-1"], ["1. 장기 전망 (월간)"])
        self.assertEqual(panels["rtab-2"], ["1. 2026-09-11 (금) 예측 vs 실제", "2. 이 모델의 예측 성적"])
        self.assertEqual(panels["rtab-3"], ["1. 이 보고서의 데이터"])

    def test_the_first_tab_is_the_one_selected_by_default(self):
        out = rh.tabify_sections(PAGE)
        selected = re.findall(r'<a href="#(rtab-\d+)" aria-selected="true"', out)
        self.assertEqual(selected, ["rtab-0"])

    def test_nothing_is_hidden_without_the_script(self):
        """스크립트가 안 돌면 예전처럼 모든 절이 보여야 한다. 숨김은 스크립트가 붙이는 클래스로만."""
        out = rh.tabify_sections(PAGE)
        for panel in re.findall(r'<section class="rtab-panel"[^>]*>', out):
            self.assertNotIn("hidden", panel)
            self.assertNotIn("display:none", panel)
        self.assertIn("#rtabs-root.rtabs-on .rtab-panel{display:none}", out)
        self.assertIn('r.className+=" rtabs-on"', out)

    def test_no_page_level_fold_remains(self):
        self.assertNotIn("펼쳐 보기", rh.tabify_sections(PAGE))

    def test_last_section_does_not_swallow_the_wrapper(self):
        # 마지막 절 끝에는 바깥 래퍼의 </div> 가 붙어 있다. 탭 묶음 안에 갇히면 안 된다.
        out = rh.tabify_sections(PAGE)
        for tag, (opens, closes) in tab_balance(out).items():
            self.assertEqual(opens, closes, f"{tag} 균형이 깨졌습니다")
        self.assertTrue(out.rstrip().endswith("</div>"))
        self.assertLess(out.index('<div id="rtabs-root">'), out.index("한눈에 보는 쉬운 요약"))

    def test_every_heading_survives(self):
        self.assertEqual(len(re.findall(r"<h3\b", rh.tabify_sections(PAGE))), 6)

    def test_tab_labels_are_short(self):
        """절 제목을 그대로 쓰면 휴대폰에서 탭 두 개도 한 줄에 안 들어간다."""
        out = rh.tabify_sections(PAGE)
        labels = tab_labels(out)
        self.assertEqual(labels, ["오늘의 예측", "장기 전망", "예측 성적", "사용한 데이터"])
        self.assertTrue(all(len(label) <= 12 for label in labels))
        # 탭 이름에는 절 번호를 붙이지 않는다(2026-09-13).
        self.assertFalse(any(re.match(r"\d", label) for label in labels), labels)
        self.assertIn("2. 이 모델의 예측 성적</h3>", out)

    def test_links_into_another_tab_open_that_tab(self):
        """#sec10 같은 링크는 그 절이 든 탭을 연 뒤 그 절로 가야 한다."""
        out = rh.tabify_sections(PAGE)
        self.assertIn('addEventListener("hashchange",route)', out)
        self.assertIn('closest(".rtab-panel")', out)
        # hashchange 만으로는 부족하다 — 주소에 #조각이 안 붙는 환경에서 링크가 먹통이었다.
        # 링크 클릭을 직접 받아 탭을 연다. 뒤로 가기는 popstate 로 따라간다.
        self.assertIn('document.addEventListener("click"', out)
        self.assertIn('addEventListener("popstate",route)', out)
        # 붙어 있는 탭 막대가 제목을 가리지 않도록 띄워 멈춘다.
        self.assertIn("scroll-margin-top", out)

    def test_the_tab_bar_sticks_and_print_shows_everything(self):
        out = rh.tabify_sections(PAGE)
        self.assertIn("position:sticky", out)
        self.assertIn("@media print", out)

    def test_pages_without_evidence_sections_are_left_alone(self):
        page = '<div><h3>한눈에 보는 쉬운 요약</h3><p>a</p><h3>1. 다음 거래일 방향</h3><p>b</p></div>'
        self.assertEqual(rh.tabify_sections(page), page)


def panel_titles_raw(text, panel_id):
    """패널 안쪽 HTML. 짝이 되는 </section> 까지 — 패널 안에 쉬운 요약 같은 <section> 이 들어 있다.

    (.*?)</section> 로 자르면 안쪽 section 의 닫는 태그에서 멈춘다.
    """
    opener = re.search(rf'<section class="rtab-panel" id="{panel_id}">', text)
    if not opener:
        return ""
    depth = 1
    for tag in re.finditer(r"<(/?)section\b[^>]*>", text[opener.end():]):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return text[opener.end():opener.end() + tag.start()]
    return text[opener.end():]


# 실제 보고서의 절 순서. 탭 구성은 이 순서에서 판단해야 한다(PAGE 는 순서를 섞어 둔 조각이다).
REPORT_ORDER = "".join(
    f'<h3 style="x">{title}</h3><div>본문</div>' for title in (
        "한눈에 보는 쉬운 요약", "그 밖에 지금 알 수 있는 것", "2026-09-11 (금) 예측 vs 실제",
        "1. 다음 거래일 방향", "1-1. 외국인·기관 수급", "2. 시초가예측과 종가예측",
        "3. 이 예측을 어떻게 읽어야 하는가", "한눈에 보는 장기 전망 요약", "1. 장기 전망 (월간)",
        "2. 이번 분기 영업이익 추정",
        # 주간 뉴스는 자주 보는 거리라 장기 전망 뒤(세 번째 탭)에 둔다(2026-09-15).
        "주간 반도체 뉴스",
        "이 모델의 예측 성적", "이 보고서의 데이터", "참고 정보: 최근 공시와 예정 발표"))


class TabArrangementTests(unittest.TestCase):
    """2026-09-13 재구성: 영업이익 추정을 장기 전망 탭으로 옮기고, 절 번호를 탭마다 새로 매긴다."""

    def out(self):
        return rh.tabify_sections('<div class="wrap">' + REPORT_ORDER + '</div>')

    def test_tabs_are_in_this_order(self):
        # 2026-10-02: 장기 전망 탭 하나가 너무 길어 메뉴 둘(요약·영업이익 / 월간 장기 전망)로 나눴다.
        # '지난 전망 성적'은 그 절이 있을 때 세 번째로 붙는다(이 견본에는 없다).
        self.assertEqual(tab_labels(self.out()), ["오늘의 예측", "장기 요약 · 영업이익", "장기 전망", "주간 뉴스",
                                                  "예측 성적", "사용한 데이터", "공시·발표 일정"])

    def test_weekly_news_is_the_third_tab(self):
        """탭 순서는 그 탭의 첫 절이 문서에 나오는 순서다. TAB_GROUPS 만 고치면 안 바뀐다."""
        labels = tab_labels(self.out())
        self.assertEqual(labels[3], "주간 뉴스")
        self.assertEqual(panel_titles(self.out())["rtab-3"], ["1. 주간 반도체 뉴스"])

    def test_first_tab_holds_today_and_ends_with_the_reading_guide(self):
        first = panel_titles(self.out())["rtab-0"]
        self.assertTrue(first[-1].endswith("시초가예측과 종가예측"))
        self.assertFalse(any("이 예측을 어떻게 읽어야" in t for t in first))
        self.assertNotIn("1. 장기 전망 (월간)", first)
        self.assertNotIn("2. 이번 분기 영업이익 추정", first)

    def test_long_term_tab_holds_the_outlook_and_the_quarterly_profit(self):
        # 장기 전망 탭도 오늘의 예측처럼 쉬운 요약이 맨 위에 온다(2026-09-13).
        self.assertEqual(panel_titles(self.out())["rtab-1"],
                         ["1. 한눈에 보는 장기 전망 요약", "2. 이번 분기 영업이익 추정"])
        self.assertEqual(panel_titles(self.out())["rtab-2"], ["1. 장기 전망 (월간)"])

    def test_numbers_restart_in_every_tab(self):
        """번호는 탭 안에서 1부터 이어진다. 절이 하나뿐인 탭은 번호가 없다."""
        for panel, titles in panel_titles(self.out()).items():
            numbers = [int(m.group(1)) for m in (re.match(r"(\d+)\. ", t) for t in titles) if m]
            if len(titles) == 1:
                self.assertEqual(numbers, [1], panel)
            else:
                self.assertEqual(numbers, list(range(1, len(numbers) + 1)), panel)

    def test_sections_of_one_tab_gather_even_when_apart(self):
        """같은 탭의 절이 문서에서 떨어져 있어도 한 탭에 문서 순서대로 모인다."""
        page = ('<div><h3>한눈에 보는 쉬운 요약</h3><p>a</p><h3>1. 장기 전망 (월간)</h3><p>b</p>'
                '<h3>1. 다음 거래일 방향</h3><p>c</p><h3>2. 이번 분기 영업이익 추정</h3><p>d</p></div>')
        panels = panel_titles(rh.tabify_sections(page))
        self.assertEqual(panels["rtab-0"], ["1. 한눈에 보는 쉬운 요약", "2. 다음 거래일 방향"])
        # 메뉴 순서는 TAB_GROUPS 순서다 — 원문에서 장기 전망이 먼저 나와도 요약·영업이익 메뉴가 앞이다.
        self.assertEqual(panels["rtab-1"], ["1. 이번 분기 영업이익 추정"])
        self.assertEqual(panels["rtab-2"], ["1. 장기 전망 (월간)"])

    def test_order_inside_a_tab_follows_the_group_not_the_source(self):
        """탭 안 순서는 원문 순서가 아니라 TAB_GROUPS 열쇠 순서(2026-09-29부터 영업이익 → 장기 전망)를 따른다."""
        page = ('<div><h3>한눈에 보는 쉬운 요약</h3><p>a</p><h3>2. 이번 분기 영업이익 추정</h3><p>d</p>'
                '<h3>1. 장기 전망 (월간)</h3><p>b</p></div>')
        out = rh.tabify_sections(page)
        self.assertEqual(panel_titles(out)["rtab-1"], ["1. 이번 분기 영업이익 추정"])
        self.assertEqual(panel_titles(out)["rtab-2"], ["1. 장기 전망 (월간)"])
        self.assertEqual(rh.tab_structure_problems(out), [])

    def test_the_structure_stays_sound(self):
        self.assertEqual(rh.tab_structure_problems(self.out()), [])


class FragmentNumberTests(unittest.TestCase):
    """월 1회 만든 조각에는 만들 때의 번호가 박혀 있다. 끼울 때 장기 전망 탭의 번호(1·2)로 맞춘다."""

    def test_any_old_number_becomes_the_tab_number(self):
        for old in ("7", "3"):
            with self.subTest(old=old):
                out = rh.renumber_fragment(f'<h3 style="font-size:15px">{old}. 장기 전망 (월간) '
                                           '<span>3·6·12개월</span></h3><p>3. 장기 전망 본문</p>')
                self.assertIn(">2. 장기 전망 (월간)", out)   # 2026-09-29: 영업이익을 1절로 올렸다
                self.assertIn("<p>3. 장기 전망 본문</p>", out)      # 제목 밖은 건드리지 않는다
        for old in ("8", "4"):
            with self.subTest(old=old):
                out = rh.renumber_fragment(f'<h3 style="x">{old}. 이번 분기 영업이익 추정</h3>')
                self.assertIn(">1. 이번 분기 영업이익 추정", out)

    def test_empty_fragment_is_returned_as_is(self):
        self.assertEqual(rh.renumber_fragment(""), "")
        self.assertIsNone(rh.renumber_fragment(None))


# 실제 보고서 모양을 줄인 것. 쉬운 요약은 감싸개(<section>)가 제목보다 먼저 열리고, 채점 절 앞뒤에는
# 오후 갱신이 찾는 표시 주석이 있다. PAGE 만으로는 2026-09-13 의 깨짐을 재현할 수 없었다.
REAL_PAGE = (
    '<div class="wrap"><h2>종합 보고서</h2>'
    '<section id="easy-summary" style="x"><h3 style="x">한눈에 보는 쉬운 요약</h3>'
    '<!--SCORECARD_START--><div>지난 예측</div><!--SCORECARD_END-->'
    '<ul><li>요약 한 줄</li></ul></section>'
    '<h3 style="x">그 밖에 지금 알 수 있는 것</h3><div>카드</div>'
    '<!--LEDGER_SECTION_START--><h3 style="x">2026-09-11 (금) 예측 vs 실제</h3>'
    '<div>채점 본문</div><!--LEDGER_SECTION_END-->'
    '<h3 style="x">1. 다음 거래일 방향</h3><div>방향 본문</div>'
    '<h3 style="x">이 모델의 예측 성적</h3><div>성능 본문</div>'
    '<h3 style="x">참고 정보: 최근 공시와 예정 발표</h3><div>공시 본문</div>'
    '</div>')


class RealShapeTabTests(unittest.TestCase):
    """개수는 맞는데 브라우저에서 깨지는 모양을 막는다."""

    def test_a_wrapper_that_opens_before_its_heading_moves_with_it(self):
        out = rh.tabify_sections(REAL_PAGE)
        self.assertIn('id="rtabs-root"', out, "탭이 적용되지 않았다")
        self.assertEqual(rh.tab_structure_problems(out), [])
        first = panel_titles_raw(out, "rtab-0")
        self.assertIn('id="easy-summary"', first, "감싸개가 제목과 함께 첫 탭에 들어가야 한다")
        self.assertNotIn("<!--SCORECARD_START-->", first)
        self.assertEqual(panel_titles(out)["rtab-0"], ["1. 한눈에 보는 쉬운 요약", "2. 그 밖에 지금 알 수 있는 것", "3. 다음 거래일 방향"])

    def test_the_outer_wrapper_stays_outside_the_tabs(self):
        out = rh.tabify_sections(REAL_PAGE)
        self.assertTrue(out.startswith('<div class="wrap"><h2>종합 보고서</h2><div id="rtabs-root">'))
        self.assertTrue(out.endswith("</div></div>"))

    def test_the_checker_catches_the_breakage_seen_on_2026_09_13(self):
        """제목에서 잘랐을 때의 실제 모양. 개수는 맞지만 둘째 제목이 탭 밖으로 떨어진다."""
        broken = ('<section id="easy-summary"><div id="rtabs-root">'
                  '<section class="rtab-panel" id="rtab-0"><h3>한눈에 보는 쉬운 요약</h3></section>'
                  '<h3>그 밖에 지금 알 수 있는 것</h3></section></div>')
        problems = rh.tab_structure_problems(broken)
        self.assertTrue(any(p.startswith("탭 밖 제목") for p in problems), problems)

    def test_ledger_markers_stay_in_one_tab_and_the_afternoon_update_still_splices(self):
        out = rh.tabify_sections(REAL_PAGE)
        performance = next(k for k,v in panel_titles(out).items() if any("예측 vs 실제" in t for t in v))
        first = panel_titles_raw(out, performance)
        self.assertIn("<!--LEDGER_SECTION_START-->", first)
        self.assertIn("<!--LEDGER_SECTION_END-->", first)
        try:
            sys.path.insert(0, str(ROOT / "tools"))
            import build_afternoon_update as ba
        except Exception as exc:          # 무거운 의존성이 없는 환경
            self.skipTest(f"build_afternoon_update 를 불러오지 못함: {exc}")
        spliced = ba.replace_section(out, '<h3 style="x">2026-09-11 (금) 예측 vs 실제</h3><div>새 채점</div>')
        self.assertIsNotNone(spliced)
        spliced = ba.replace_section(spliced, "<div>오후 성적</div>", ba.SCORECARD_START, ba.SCORECARD_END)
        self.assertIsNotNone(spliced)
        self.assertEqual(rh.tab_structure_problems(spliced), [])
        self.assertIn("새 채점", panel_titles_raw(spliced, performance))
        self.assertIn("오후 성적", panel_titles_raw(spliced, performance))

    def test_the_session_review_lands_in_the_first_tab(self):
        out = rh.tabify_sections(REAL_PAGE)
        try:
            sys.path.insert(0, str(ROOT / "tools"))
            import build_session_review as sr
        except Exception as exc:
            self.skipTest(f"build_session_review 를 불러오지 못함: {exc}")
        section = sr.MARK_START + '<h3 style="x">오늘 장 회고 — 2026-09-11</h3><div>회고</div>' + sr.MARK_END
        page = sr.insert_section(out, section)
        self.assertEqual(rh.tab_structure_problems(page), [])
        # 2026-09-30 요청: '오늘의 예측' 바로 다음의 '오늘의 장 회고' 탭에 들어간다. 다시 넣어도 하나만 남고 자리가 같다.
        self.assertNotIn("오늘 장 회고", panel_titles_raw(page, "rtab-0"))
        self.assertIn("오늘 장 회고", panel_titles_raw(page, "rtab-review"))
        nav = page[page.find('<nav class="rtabs"'):page.find("</nav>")]
        self.assertLess(nav.find("#rtab-0"), nav.find("#rtab-review"))
        self.assertLess(nav.find("#rtab-review"), nav.find("#rtab-1"))
        self.assertEqual(sr.insert_section(page, section), page)
        self.assertEqual(page.count(sr.MARK_START), 1)

    def test_a_layout_that_cannot_be_split_safely_is_left_as_it_was(self):
        """감싸개 안에서 제목 앞에 다른 내용이 있으면 끌어올 수 없다. 깨진 탭 대신 원래 페이지를 둔다."""
        page = ('<div><h3>한눈에 보는 쉬운 요약</h3><p>a</p>'
                '<section><p>머리말</p><h3>이 모델의 예측 성적</h3><p>e</p></section>'
                '<h3>이 보고서의 데이터</h3><p>f</p></div>')
        self.assertEqual(rh.tabify_sections(page), page)


class NotebookWiringTests(unittest.TestCase):
    def report_cell(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        return next("".join(c["source"]) for c in nb["cells"]
                    if "def build_summary():" in "".join(c.get("source", [])))

    def test_notebook_makes_tabs_then_adds_ids_without_a_toc(self):
        report = self.report_cell()
        self.assertIn("html = tabify_sections(html)", report)
        self.assertIn("html, _report_sections = add_section_ids(html)", report)
        self.assertNotIn("add_report_nav", report)
        self.assertNotIn("collapse_sections(html)", report)
        self.assertLess(report.index("tabify_sections(html)"), report.index("add_section_ids(html)"))

    def test_page_css_lets_the_tab_bar_stick(self):
        """overflow-x:hidden 은 body 를 스크롤 상자로 만들어 sticky 가 붙지 않는다. clip 은 괜찮다."""
        report = self.report_cell()
        self.assertIn("overflow-x:clip", report)


class EasySummaryHierarchyTests(unittest.TestCase):
    """쉬운 요약이 같은 크기 글자만 나열되면 무엇이 결론인지 알 수 없다(2026-09-12 지적)."""

    def summary(self):
        import pandas as pd
        import forecast_utils as fu
        return fu.easy_summary_html(
            name="삼성전자", prediction_date=pd.Timestamp("2026-09-14"),
            data_date=pd.Timestamp("2026-09-11"),
            summary={"live": {"prediction": "큰 변화 없음", "p_up": .33, "p_flat": .366, "p_down": .30},
                     "metrics": {"auc_gap": .81, "auc_session": .49},
                     "ensemble": "No macro ensemble", "band": .01},
            open_forecast={"signal": "없음"}, price_forecasts=[], review={"n_scored_days": 5})

    def test_first_item_is_a_lead_box(self):
        html = self.summary()
        # 전체 결론은 흰 박스에 16px 굵은 글씨로 뽑는다.
        self.assertIn("background:#fff;border:1px solid #cedff0", html)
        self.assertIn("font-size:16px;line-height:1.6;font-weight:600", html)

    def test_explanations_are_small_and_muted(self):
        """믿음 정도·주의할 점은 투자 판단에 덜 급한 설명이라 작은 회색 글로 내린다(2026-09-28 요청)."""
        html = self.summary()
        fine = html[html.index("얼마나 믿을 수 있나요?") - 200:]
        self.assertIn("font-size:12px;line-height:1.6;color:#8a9199", fine)
        # 옛 글 목록(같은 크기 제목+본문 여섯 줄)은 남아 있지 않다.
        self.assertNotIn('<li style="margin:11px 0">', html)

    def test_prices_are_a_range_chart_and_outlook_is_tiles(self):
        import pandas as pd
        import forecast_utils as fu
        html = fu.easy_summary_html(
            name="삼성전자", prediction_date=pd.Timestamp("2026-09-14"), data_date=pd.Timestamp("2026-09-11"),
            summary={"live": {"p_up": .5, "p_flat": .3, "p_down": .2}},
            open_forecast={"signal": "있음", "predicted_open": 70100, "predicted_return": .004,
                           "current_close": 69820, "low_open": 69500, "high_open": 70700},
            price_forecasts=[{"signal": "없음", "predicted_close": 71000, "center_close": 69820, "trading_days": 5,
                              "current_close": 69820, "low_close": 66000, "high_close": 73000,
                              "target_date": pd.Timestamp("2026-09-18")}],
            review={}, longterm={"as_of": "2026-08-31", "forecast": {"3": {"point": .05}},
                                 "evaluation": {"3": {"beats_zero": True}}})
        self.assertEqual(html.count('class="pr-row"'), 2)
        self.assertIn("69,500~70,700원", html)            # 구간은 보인다
        self.assertIn("66,000~73,000원", html)
        self.assertNotIn("71,000", html)                    # 검증을 못 통과한 예상가는 숫자·점 모두 없다
        self.assertEqual(html.count("box-shadow:0 0 0 1px #1a5490"), 1)
        self.assertIn("기준 가격(전일 종가) 69,820원", html)
        self.assertIn(">+5.1%<", html)                       # 3개월 타일(로그수익률 0.05 → +5.1%)
        self.assertEqual(html.count("<h3"), 1)


class CaveatSpacingTests(unittest.TestCase):
    """표 아래 설명에 아래 여백이 없어 다음 블록과 글자가 겹쳐 보였다(2026-09-12 지적)."""

    def test_caveat_has_bottom_margin(self):
        import forecast_utils as fu
        html = fu.decision_inputs_html(name="t", unknowns=["x"], caveat="설명",
                                       cards=[{"label": "a", "value": "b", "detail": "c", "source": "d"}])
        self.assertIn("margin:6px 0 20px", html)
        self.assertNotIn('color:#8a9199;margin-top:6px">설명', html)


if __name__ == "__main__":
    unittest.main()


class BackLinkTests(unittest.TestCase):
    """종목 보고서 맨 위에 보고서 목록으로 가는 버튼(2026-09-14 요청).

    금·은·중국 보고서는 하단에만 링크가 있어 긴 페이지를 끝까지 내려야 했다. 탭이 생기면서
    페이지가 더 길어져 상단에 둔다.
    """

    @classmethod
    def setUpClass(cls):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        # 헬퍼 셀(report_html 등)을 빼고 본문 셀만 본다 — 버튼 HTML 은 헬퍼 함수에 있고, 본문은 그 함수를 부른다.
        cls.source = "\n".join("".join(c["source"]) for c in nb["cells"] if "tags" not in c.get("metadata", {}))
        cls.call = "report_top_bar_html(COUNTER_ENDPOINT, TARGET, TARGET_NAME)"

    def test_button_exists_and_points_to_the_index(self):
        import report_html
        bar = report_html.report_top_bar_html("", "samsung", "삼성전자")
        self.assertIn('<a href="../"', bar)
        self.assertIn("← 보고서 목록", bar)
        self.assertIn(self.call, self.source)

    def test_button_comes_before_the_title(self):
        # 주석에도 '종합 보고서'가 나오므로 제목 태그로 찾는다.
        button = self.source.index(self.call)
        # 제목에 종목 이름이 들어간다(2026-09-17): '삼성전자 종합 보고서'
        title = self.source.index("{TARGET_NAME} 종합 보고서</h2>")
        self.assertLess(button, title, "버튼이 제목보다 뒤에 있으면 상단 버튼이 아니다")

    def test_button_is_inside_the_page_wrapper(self):
        # 바깥 래퍼 div 안에 있어야 가운데 정렬·폭 제한이 적용된다.
        wrapper = self.source.index("max-width:980px;margin:0 auto;")
        self.assertLess(wrapper, self.source.index(self.call))


class SubscribeBarTests(unittest.TestCase):
    """종목 보고서 상단 구독 버튼(2026-09-28): 동의 없이 보내지 않고, 카운터 Worker 로 보낸다."""

    def test_subscribe_form_asks_consent_and_posts_to_the_worker(self):
        import report_html
        bar = report_html.report_top_bar_html("https://counter.example", "sk_hynix", "SK하이닉스")
        self.assertIn("← 보고서 목록", bar)
        self.assertLess(bar.index("← 보고서 목록"), bar.index("✉ 구독"))
        self.assertIn('id="sub-box" hidden', bar, "처음에는 닫혀 있다")
        self.assertIn('type="email"', bar)
        self.assertIn("개인정보 수집·이용에 동의", bar)
        for item in ("수집 항목: 이메일 주소", "목적: SK하이닉스 보고서 소식 안내", "보관 기간: 구독 해지 시까지"):
            self.assertIn(item, bar)
        self.assertIn('if(!$("sub-agree").checked)', bar)
        self.assertIn('E="https://counter.example",P="sk_hynix"', bar)
        self.assertIn('send("/subscribe"', bar)
        self.assertIn('send("/unsubscribe"', bar)
        self.assertIn('name="website"', bar, "봇 걸러내는 숨은 칸")

    def test_page_key_is_one_the_worker_accepts(self):
        worker = (ROOT / "counter" / "worker.js").read_text(encoding="utf-8")
        self.assertIn('const SUBSCRIBE_PAGES = ["main", "samsung", "sk_hynix"];', worker)

    def test_main_page_has_the_same_bar_without_back_link(self):
        # 메인 페이지는 정적 파일이라 함수 출력을 붙여 둔다. 함수를 고치고 붙이는 것을 잊으면 여기서 걸린다.
        import report_html
        bar = report_html.report_top_bar_html("https://predict-stock-counter.kimname1.workers.dev", "main",
                                              "전체 예측", back=False)
        index = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertIn(bar, index)
        self.assertNotIn("← 보고서 목록", bar)
        self.assertIn("전체 예측 보고서 구독", bar)
        self.assertNotIn("보고서 보고서", bar)
        self.assertLess(index.index('id="sub-open"'), index.index("<h1>예측 보고서</h1>"))

    def test_admin_page_lists_subscribers(self):
        admin = (ROOT / "docs" / "admin" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-tab="subscribers"', admin)
        self.assertIn('"/subscribers"', admin)


class AllReportsBackLinkTests(unittest.TestCase):
    """모든 보고서 맨 위에 목록으로 가는 버튼(2026-09-14 요청).

    금·은·중국은 하단에만, 검색어·관심도는 상단 링크가 없었다. 페이지가 길어 끝까지 내려야
    돌아갈 수 있었다.
    """

    # 페이지를 만드는 모든 도구. 새 페이지를 추가하면 여기에도 넣어야 테스트가 지켜 준다.
    # 2026-09-15: news·ai_news·lab 을 빠뜨려 /news/ 에만 버튼이 없었다.
    TOOLS = ("build_metals_report.py", "build_china_report.py",
             "build_trends_report.py", "build_interest_report.py",
             "build_news_hub.py", "build_ai_news_report.py", "build_macro_report.py")

    def source(self, name):
        return (ROOT / "tools" / name).read_text(encoding="utf-8")

    def test_every_report_has_the_button(self):
        for name in self.TOOLS:
            source = self.source(name)
            self.assertIn("← 보고서 목록", source, name)
            self.assertIn('<a href="../"', source, name)

    def test_button_precedes_the_page_title(self):
        # 상단 버튼이어야 한다. 제목보다 뒤면 의미가 없다.
        for name in self.TOOLS:
            source = self.source(name)
            button = source.index("← 보고서 목록")
            # 보고서마다 제목 태그가 다르다(h1/h2, class 유무).
            title = min((source.index(tag) for tag in ('<h1 style="font-size:24px',
                                                       '<h2 class="page-title"',
                                                       '<h2 style="margin:6px 0 4px;font-size:27px',
                                                       '<h2 style="margin:6px 0 5px;font-size:27px')
                         if tag in source), default=None)
            self.assertIsNotNone(title, name)
            self.assertLess(button, title, f"{name}: 버튼이 제목보다 뒤에 있습니다")

    def test_same_style_everywhere(self):
        # 보고서마다 모양이 다르면 같은 버튼으로 읽히지 않는다.
        for name in self.TOOLS:
            self.assertIn("border:1px solid #cedff0;border-radius:5px", self.source(name), name)


    def test_static_lab_page_uses_the_same_button(self):
        html = (ROOT / "docs" / "lab" / "index.html").read_text(encoding="utf-8")
        self.assertIn("← 보고서 목록", html)
        self.assertIn("border:1px solid #cedff0;border-radius:5px", html)
        self.assertLess(html.index("← 보고서 목록"), html.index("<h1>실험실"))

    def test_every_generator_is_covered(self):
        """페이지를 만드는 도구를 빠뜨리면 그 페이지에만 버튼이 없어진다.

        docs/ 에 발행되는 페이지 수와 검사 대상이 어긋나지 않는지 본다(admin 은 운영용이라 제외).
        """
        pages = {p.name for p in (ROOT / "docs").iterdir()
                 if p.is_dir() and p.name != "admin" and (p / "index.html").exists()}
        # 종목 둘은 노트북이, lab 은 정적 파일이 만든다. 나머지는 TOOLS 가 만든다.
        by_tool = len(self.TOOLS)
        self.assertGreaterEqual(by_tool + 3, len(pages),
                                f"검사하지 않는 페이지가 있습니다: {sorted(pages)}")

    def test_only_one_back_link_per_generator(self):
        """목록 링크는 맨 위 버튼 하나만. 하단에도 있으면 같은 버튼이 두 번 보인다.

        2026-09-15: 상단 버튼을 넣으면서 기존 하단 링크와 중복됐다(뉴스 허브·금은·중국·검색어·
        관심도 다섯 곳).
        """
        for name in self.TOOLS:
            source = self.source(name)
            count = source.count('href="../"')
            self.assertEqual(count, 1, f"{name}: 목록 링크가 {count}개입니다(맨 위 버튼 하나만)")

    def test_repository_committed_pages_have_no_duplicate(self):
        """저장소에 커밋된 페이지도 중복이 없어야 한다.

        각 페이지는 자기 워크플로가 다음에 돌 때 다시 만들어지므로, 도구만 고친 직후에는
        옛 발행본이 남아 있을 수 있다. 그래서 '도구가 고쳐졌는지'는 위 테스트가 지키고,
        여기서는 다시 만들어진 페이지만 본다(상단 버튼 + 하단 링크가 함께 있으면 중복).
        """
        for page in sorted((ROOT / "docs").iterdir()):
            if not page.is_dir() or page.name == "admin":
                continue
            index = page / "index.html"
            if not index.exists() or "← 보고서 목록" not in index.read_text(encoding="utf-8"):
                continue
            html = index.read_text(encoding="utf-8")
            if html.count('href="../"') <= 1:
                continue                       # 이미 정리된 페이지
            # 아직 옛 발행본이면 도구 쪽이 고쳐져 있는지만 확인한다.
            self.assertTrue(any('href="../"' in self.source(name) and
                                self.source(name).count('href="../"') == 1
                                for name in self.TOOLS),
                            f"{page.name}: 발행본이 중복인데 도구도 고쳐지지 않았습니다")

class TypographyScaleTests(unittest.TestCase):
    """보고서 전체 글자 크기 척도(2026-09-27 지적: 예측 성적 탭의 절마다 크기가 달랐다).

    크기를 적지 않은 글이 브라우저 기본(본문 16px, h3 18.7px)으로 나왔다 — 읽는 법 본문 16px, 끼워 넣은
    절 제목(누적 성적·학습 설정) 18.7px. 페이지 CSS 가 척도를 정한다: 본문 13 · 절 제목 15 · 소제목 14 · 표 13.
    """

    @classmethod
    def setUpClass(cls):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        cls.source = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")

    def test_page_css_sets_the_scale(self):
        for rule in ("body{font-size:13px;zoom:1.15}", "h3{font-size:15px;", "h4{font-size:14px;", "table{font-size:13px}"):
            self.assertIn(rule, self.source, rule)

    def test_table_body_text_is_one_size(self):
        """표 본문은 13px 하나(2026-09-27 통일 요청). 머리글 11px·고정폭 티커 12px·칸 안 주석 span 은 예외."""
        import re
        for name in ("forecast_utils.py", "report_html.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"border-collapse:collapse;font-size:12(\.5)?px", text), name)
            # 확률 막대(prob_bar)의 칸은 색 막대 위 흰 글씨라 표 본문이 아니다 — 제외한다.
            for m in re.finditer(r"<td style=\"([^\"]*font-size:1[12]px[^\"]*)", text):
                style = m.group(1)
                if "monospace" in style or "background:{c}" in text[m.start() - 80:m.start() + 120]:
                    continue
                self.fail(f"{name}: 표 본문 칸이 13px 가 아닙니다 — {style[:80]}")
        self.assertIsNone(re.search(r"<td style=\"[^\"]*color:#8a9199;font-size:12px\">'\s*\n\s*f'\{detail\}", self.source))

    def test_inserted_section_titles_rely_on_the_default_h3(self):
        # report_html 이 끼워 넣는 절 제목은 스타일 없는 <h3> 다. 기본 규칙이 있어야 다른 절과 같아 보인다.
        text = (ROOT / "report_html.py").read_text(encoding="utf-8")
        self.assertIn('<h3>실제 발행 후 누적 성적</h3>', text)
        self.assertIn('<h3>학습·검증 설정</h3>', text)

    def test_weekly_news_is_inserted_once(self):
        # 두 번 끼우면 주간 뉴스 탭에 같은 절이 두 번 나온다(2026-09-27 발행본에서 확인).
        import re
        self.assertEqual(len(re.findall(r"f'\{weekly_html\}'", self.source)), 1)


class PageZoomTests(unittest.TestCase):
    """모든 페이지를 1.15배로 본다(2026-09-27: 종목 보고서에 먼저 적용 후 '적당하다' — 나머지도 같게).

    뉴스 허브는 AI 뉴스·검색어·관심도를 iframe 으로 품는다. 그 세 페이지까지 늘 확대하면 허브 안에서
    1.15×1.15 로 두 번 커진다. 세 페이지는 iframe 밖에서만 확대한다(head 의 embedded 판정을 쓴다).
    """

    PAGES = ("tools/build_metals_report.py", "tools/build_china_report.py", "tools/build_macro_report.py",
             "tools/build_news_hub.py", "docs/lab/index.html", "docs/index.html")
    EMBEDDED = ("tools/build_ai_news_report.py", "tools/build_trends_report.py", "tools/build_interest_report.py")

    def text(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_every_page_zooms(self):
        for path in self.PAGES:
            self.assertIn("body{zoom:1.15}", self.text(path), path)

    def test_embedded_pages_zoom_only_outside_the_hub(self):
        for path in self.EMBEDDED:
            text = self.text(path)
            self.assertIn("html:not(.embedded) body{zoom:1.15}", text, path)
            self.assertNotIn("}body{zoom:1.15}", text.replace("html:not(.embedded) body{zoom:1.15}", ""), path)
            self.assertIn("window.top!==window.self", text, path)

    def test_stock_report_zooms(self):
        import json
        nb = json.loads(self.text("samsung_direction_model_colab.ipynb"))
        source = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
        self.assertIn("zoom:1.15", source)
