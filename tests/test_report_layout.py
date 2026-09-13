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
        '<h3 style="x">6. 모델 성적과 검증</h3><div>성능 본문<table><tr><td>표</td></tr></table></div>'
        '<h3 style="x">3. 장기 전망 (월간)</h3><div>장기 본문</div>'
        '<h3 style="x">7. 이 보고서의 데이터</h3><div>데이터 본문</div>'
        '</div>')


def balance(text):
    return {tag: (len(re.findall(rf"<{tag}\b", text)), len(re.findall(rf"</{tag}>", text)))
            for tag in ("div", "table", "details", "h3")}


class NavTests(unittest.TestCase):
    def test_ids_and_toc_are_added(self):
        out, sections = rh.add_report_nav(PAGE)
        self.assertEqual(len(sections), 6)
        self.assertEqual(len(re.findall(r'<h3[^>]*id="sec\d+"', out)), 6)
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
        self.assertEqual(by["2026-09-11 (금) 예측 vs 실제"], "요약")
        self.assertEqual(by["3. 장기 전망 (월간)"], "예측")
        self.assertEqual(by["7. 이 보고서의 데이터"], "해설")
        self.assertEqual([name for name, _ in rh.NAV_GROUPS], ["요약", "예측", "해설"])

    def test_tag_balance_is_preserved(self):
        out, _ = rh.add_report_nav(PAGE)
        for tag, (opens, closes) in balance(out).items():
            self.assertEqual(opens, closes, tag)

    def test_short_pages_are_left_alone(self):
        tiny = '<h3>하나</h3><div>본문</div>'
        out, sections = rh.add_report_nav(tiny)
        self.assertEqual(out, tiny)
        self.assertEqual(len(sections), 1)


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


def tab_balance(text):
    counts = balance(text)
    for tag in ("section", "nav"):
        counts[tag] = (len(re.findall(rf"<{tag}\b", text)), len(re.findall(rf"</{tag}>", text)))
    return counts


class TabTests(unittest.TestCase):
    """접던 절을 상단 탭으로 바꾼다(2026-09-13). '펼쳐 보기'를 찾아 누르는 것이 불편했다."""

    def test_conclusions_stay_in_the_first_tab_and_evidence_gets_its_own_tabs(self):
        panels = panel_titles(rh.tabify_sections(PAGE))
        self.assertEqual(panels["rtab-0"], ["한눈에 보는 쉬운 요약", "1. 다음 거래일 방향",
                                            "2026-09-11 (금) 예측 vs 실제"])
        # 탭은 문서 순서다. 이 조각에서는 6절이 3절보다 앞에 있다.
        self.assertEqual(panels["rtab-1"], ["6. 모델 성적과 검증"])
        self.assertEqual(panels["rtab-2"], ["3. 장기 전망 (월간)"])
        self.assertEqual(panels["rtab-3"], ["7. 이 보고서의 데이터"])

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
        labels = re.findall(r'<a href="#rtab-\d+" aria-selected="(?:true|false)">(.*?)</a>', out)
        self.assertEqual(labels, ["오늘의 예측", "성적과 검증", "장기 전망", "사용한 데이터"])
        self.assertTrue(all(len(label) <= 12 for label in labels))
        # 탭 이름에는 절 번호를 붙이지 않는다(2026-09-13). 번호는 절 제목에만 남는다.
        self.assertFalse(any(re.match(r"\d", label) for label in labels), labels)
        self.assertIn(">6. 모델 성적과 검증</h3>", out)

    def test_toc_links_into_another_tab_open_that_tab(self):
        """목차의 #sec10 같은 링크는 그 절이 든 탭을 연 뒤 그 절로 가야 한다."""
        out = rh.tabify_sections(PAGE)
        self.assertIn('addEventListener("hashchange",route)', out)
        self.assertIn('closest(".rtab-panel")', out)
        # hashchange 만으로는 부족하다 — 주소에 #조각이 안 붙는 환경에서 목차가 먹통이었다.
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

    def test_the_toc_lands_below_the_tab_bar_and_sees_every_section(self):
        """탭은 상단에 있어야 한다. 목차(13줄)가 탭 위로 오면 탭이 화면 아래로 밀려난다."""
        out, sections = rh.add_report_nav(rh.tabify_sections(PAGE))
        self.assertEqual(len(sections), 6)
        self.assertLess(out.index('class="rtabs"'), out.index("이 보고서의 구성"))
        self.assertIn("이 보고서의 구성", panel_titles_raw(out, "rtab-0"))
        for tag, (opens, closes) in tab_balance(out).items():
            self.assertEqual(opens, closes, tag)


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
        "3. 장기 전망 (월간)", "4. 이번 분기 영업이익 추정", "5. 이 예측을 어떻게 읽어야 하는가",
        "6. 모델 성적과 검증", "7. 이 보고서의 데이터", "참고 정보: 최근 공시와 예정 발표"))


class TabArrangementTests(unittest.TestCase):
    """2026-09-13 제안: 5절(읽는 법)은 첫 탭 맨 아래로, 3절(장기 전망)은 둘째 탭으로."""

    def out(self):
        return rh.tabify_sections('<div class="wrap">' + REPORT_ORDER + '</div>')

    def test_tabs_are_in_this_order(self):
        labels = re.findall(r'<a href="#rtab-\d+" aria-selected="(?:true|false)">(.*?)</a>', self.out())
        self.assertEqual(labels, ["오늘의 예측", "장기 전망", "성적과 검증",
                                  "사용한 데이터", "공시·발표 일정"])

    def test_reading_guide_is_the_last_thing_in_the_first_tab(self):
        first = panel_titles(self.out())["rtab-0"]
        self.assertEqual(first[-1], "5. 이 예측을 어떻게 읽어야 하는가")
        self.assertEqual(first[-2], "4. 이번 분기 영업이익 추정")
        self.assertNotIn("3. 장기 전망 (월간)", first)

    def test_long_term_outlook_has_its_own_tab(self):
        self.assertEqual(panel_titles(self.out())["rtab-1"], ["3. 장기 전망 (월간)"])

    def test_the_structure_stays_sound(self):
        self.assertEqual(rh.tab_structure_problems(self.out()), [])


# 실제 보고서 모양을 줄인 것. 쉬운 요약은 감싸개(<section>)가 제목보다 먼저 열리고, 채점 절 앞뒤에는
# 오후 갱신이 찾는 표시 주석이 있다. PAGE 만으로는 2026-09-13 의 깨짐을 재현할 수 없었다.
REAL_PAGE = (
    '<div class="wrap"><h2>종합 보고서</h2>'
    '<section id="easy-summary" style="x"><h3 style="x">한눈에 보는 쉬운 요약</h3>'
    '<ul><li>요약 한 줄</li></ul></section>'
    '<h3 style="x">그 밖에 지금 알 수 있는 것</h3><div>카드</div>'
    '<!--LEDGER_SECTION_START--><h3 style="x">2026-09-11 (금) 예측 vs 실제</h3>'
    '<div>채점 본문</div><!--LEDGER_SECTION_END-->'
    '<h3 style="x">1. 다음 거래일 방향</h3><div>방향 본문</div>'
    '<h3 style="x">6. 모델 성능</h3><div>성능 본문</div>'
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
        self.assertEqual(panel_titles(out)["rtab-0"], ["한눈에 보는 쉬운 요약", "그 밖에 지금 알 수 있는 것",
                                                       "2026-09-11 (금) 예측 vs 실제", "1. 다음 거래일 방향"])

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
        first = panel_titles_raw(out, "rtab-0")
        self.assertIn("<!--LEDGER_SECTION_START-->", first)
        self.assertIn("<!--LEDGER_SECTION_END-->", first)
        try:
            sys.path.insert(0, str(ROOT / "tools"))
            import build_afternoon_update as ba
        except Exception as exc:          # 무거운 의존성이 없는 환경
            self.skipTest(f"build_afternoon_update 를 불러오지 못함: {exc}")
        spliced = ba.replace_section(out, '<h3 style="x">2026-09-11 (금) 예측 vs 실제</h3><div>새 채점</div>')
        self.assertIsNotNone(spliced)
        self.assertEqual(rh.tab_structure_problems(spliced), [])
        self.assertIn("새 채점", panel_titles_raw(spliced, "rtab-0"))

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
        self.assertIn("오늘 장 회고", panel_titles_raw(page, "rtab-0"))

    def test_a_layout_that_cannot_be_split_safely_is_left_as_it_was(self):
        """감싸개 안에서 제목 앞에 다른 내용이 있으면 끌어올 수 없다. 깨진 탭 대신 원래 페이지를 둔다."""
        page = ('<div><h3>한눈에 보는 쉬운 요약</h3><p>a</p>'
                '<section><p>머리말</p><h3>6. 모델 성능</h3><p>e</p></section>'
                '<h3>7. 자동 판정</h3><p>f</p></div>')
        self.assertEqual(rh.tabify_sections(page), page)


class NotebookWiringTests(unittest.TestCase):
    def report_cell(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        return next("".join(c["source"]) for c in nb["cells"]
                    if "def build_summary():" in "".join(c.get("source", [])))

    def test_notebook_makes_tabs_then_adds_nav(self):
        report = self.report_cell()
        self.assertIn("html = tabify_sections(html)", report)
        self.assertNotIn("collapse_sections(html)", report)
        self.assertIn("html, _report_sections = add_report_nav(html)", report)
        # 순서가 중요하다: 탭을 만든 뒤 목차를 넣어야 목차가 첫 탭 안, 탭 막대 아래에 온다.
        self.assertLess(report.index("tabify_sections(html)"), report.index("add_report_nav(html)"))

    def test_page_css_lets_the_tab_bar_stick(self):
        """overflow-x:hidden 은 body 를 스크롤 상자로 만들어 sticky 가 붙지 않는다. clip 은 괜찮다."""
        report = self.report_cell()
        self.assertIn("overflow-x:clip", report)


class MobileLayoutTests(unittest.TestCase):
    """모바일(약 380px)에서 목차 줄바꿈이 깨지지 않아야 한다.

    2026-09-12 지적: 그룹 이름과 링크가 같은 줄에서 시작해, 링크가 줄바꿈되면 다음 줄이
    이름 자리까지 밀려 들어와 정렬이 무너졌다. 제목도 중간('1-1. 외국인·')에서 끊겼다.
    """

    def nav(self):
        out, _ = rh.add_report_nav(PAGE)
        start = out.index("이 보고서의 구성")
        return out[start:out.index("<h3", start)]

    def test_group_label_is_on_its_own_line(self):
        nav = self.nav()
        # 이름과 링크가 다른 블록에 있어야 줄바꿈이 이름 자리를 침범하지 않는다.
        self.assertIn("margin-bottom:1px", nav)
        self.assertNotIn("display:inline-block;min-width:38px", nav)

    def test_one_link_per_line(self):
        """여러 개를 한 줄에 흘리면 '1-1.'과 '2.'가 붙어 번호 순서가 눈에 들어오지 않는다."""
        nav = self.nav()
        links = re.findall(r"<a\b[^>]*>", nav)
        self.assertTrue(links)
        for link in links:
            self.assertIn("display:block", link)

    def test_numbers_run_in_order_across_groups(self):
        """목차의 번호는 1부터 오름차순이어야 한다. 예전에는 그룹 탓에 5번이 6·7번 뒤에 나왔다."""
        _, sections = rh.add_report_nav(PAGE)
        # 목차는 그룹 순서로 그려진다. 문서 순서가 아니라 그 순서로 번호를 읽어야 한다.
        ordered = [s for name, _ in rh.NAV_GROUPS for s in sections if s["group"] == name]
        numbers = [int(m.group(1)) for m in
                   (re.match(r"(\d+)\.", s["title"]) for s in ordered) if m]
        self.assertEqual(numbers, sorted(numbers), f"목차 번호가 뒤섞였습니다: {numbers}")

    def test_no_middot_separator_that_can_start_a_line(self):
        # ' · ' 로 이으면 줄 맨 앞에 가운뎃점이 남을 수 있다. 여백으로 구분한다.
        text = re.sub(r"<[^>]+>", "", self.nav())
        self.assertNotIn(" · ", text)


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

    def test_remaining_items_have_bold_titles_and_muted_bodies(self):
        html = self.summary()
        self.assertIn("font-size:13px;font-weight:700;color:#1a1a1a", html)
        self.assertIn("color:#3a4652", html)
        # 옛 스타일(같은 크기, <b>제목</b><br>본문)이 남아 있으면 안 된다.
        self.assertNotIn('<li style="margin:9px 0"><b>', html)

    def test_bullets_are_removed_because_titles_separate_items(self):
        self.assertIn("list-style:none", self.summary())


class CaveatSpacingTests(unittest.TestCase):
    """표 아래 설명에 아래 여백이 없어 다음 블록과 글자가 겹쳐 보였다(2026-09-12 지적)."""

    def test_caveat_has_bottom_margin(self):
        import forecast_utils as fu
        html = fu.decision_inputs_html(name="t", unknowns=["x"], caveat="설명",
                                       cards=[{"label": "a", "value": "b", "detail": "c", "source": "d"}])
        self.assertIn("margin:6px 0 20px", html)
        self.assertNotIn('color:#8a9199;margin-top:6px">설명', html)
