"""검증·방법 소제목 접기(2026-10-02). 장기 전망 탭의 글이 많아 읽기 어렵다는 요청의 2단계.

투자 판단에 먼저 쓰는 절은 그대로 두고, 검증·방법 설명 절만 접는다. 내용은 지우지 않는다.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import report_html as rh  # noqa: E402

H4 = '<h4 style="font-size:14px;margin:18px 0 6px">'
PAGE = ('<div class="wrap"><h3>한눈에 보는 쉬운 요약</h3><p>요약</p>'
        '<section id="longterm-summary"><h3>한눈에 보는 장기 전망 요약</h3><p>장기 요약</p></section>'
        '<h3>이번 분기 영업이익 추정</h3><p>머리</p>'
        f'{H4}추정</h4><p>추정 본문</p>'
        f'{H4}검증 — 기준선을 이기는가 (워크포워드)</h4><div><table><tr><td>검증 표</td></tr></table></div><p>검증 설명</p>'
        f'{H4}D램 현물 가격 <span style="color:#8a9199">세션 평균</span></h4><p>현물 본문</p><!--NEXT_START-->'
        '<h3>장기 전망 (월간)</h3>'
        f'{H4}지금 위치</h4><p>위치 본문</p>'
        f'{H4}재 보고 쓰지 않기로 한 것</h4><p>쓰지 않은 것</p></div>')


class FoldTests(unittest.TestCase):
    def test_only_listed_subsections_are_folded(self):
        out = rh.fold_detail_sections(PAGE)
        titles = re.findall(r'<details class="fold-sub"[^>]*><summary[^>]*>(.*?) <span style="font-weight:400', out)
        self.assertEqual(titles, ['검증 — 기준선을 이기는가 (워크포워드)',
                                  'D램 현물 가격 <span style="color:#8a9199">세션 평균</span>',
                                  '재 보고 쓰지 않기로 한 것'])
        self.assertIn(f'{H4}추정</h4><p>추정 본문</p>', out)          # 투자 판단에 쓰는 절은 그대로
        self.assertIn(f'{H4}지금 위치</h4><p>위치 본문</p>', out)

    def test_content_is_kept_inside_the_fold(self):
        out = rh.fold_detail_sections(PAGE)
        self.assertIn('</summary><div><table><tr><td>검증 표</td></tr></table></div><p>검증 설명</p></details>', out)
        for text in ('검증 표', '현물 본문', '쓰지 않은 것'):
            self.assertEqual(out.count(text), 1)

    def test_markers_and_outer_wrappers_stay_outside(self):
        out = rh.fold_detail_sections(PAGE)
        self.assertIn('<p>현물 본문</p></details><!--NEXT_START--><h3>장기 전망 (월간)</h3>', out)
        self.assertTrue(out.endswith('<p>쓰지 않은 것</p></details></div>'))     # 바깥 감싸개의 닫는 태그는 밖에
        self.assertEqual(out.count('<details'), out.count('</details>'))

    def test_folding_twice_changes_nothing(self):
        out = rh.fold_detail_sections(PAGE)
        self.assertEqual(rh.fold_detail_sections(out), out)

    def test_numbered_titles_are_matched_and_the_number_is_dropped(self):
        page = f'<div><h3>1. 가</h3>{H4}1.2 검증 — 기준선을 이기는가</h4><p>x</p></div>'
        out = rh.fold_detail_sections(page)
        self.assertIn('>검증 — 기준선을 이기는가 <span', out)
        self.assertNotIn('1.2 검증', out)

    def test_tabs_fold_and_keep_a_sound_structure(self):
        out = rh.tabify_sections(PAGE)
        self.assertIn('id="rtabs-root"', out)
        self.assertEqual(out.count('class="fold-sub"'), 3)
        self.assertEqual(rh.tab_structure_problems(out), [])

    def test_pages_without_listed_titles_are_untouched(self):
        page = f'<div><h3>가</h3>{H4}나</h4><p>다</p></div>'
        self.assertEqual(rh.fold_detail_sections(page), page)


class WiringTests(unittest.TestCase):
    def test_daily_tab_refresh_folds_too(self):
        source = (ROOT / "tools" / "refresh_longterm_tab.py").read_text(encoding="utf-8")
        self.assertIn("replace_panel(latest, fold_detail_sections(body))", source)

    def test_listed_titles_exist_in_the_generators(self):
        """접을 제목이 만드는 쪽에서 바뀌면 조용히 안 접힌다. 제목이 실제로 있는지 본다."""
        sources = "".join((ROOT / "tools" / name).read_text(encoding="utf-8")
                          for name in ("build_earnings_forecast.py", "build_longterm_report.py"))
        for key in rh.FOLD_SUBSECTIONS:
            self.assertIn(f'>{key}', sources, key)


if __name__ == "__main__":
    unittest.main()
