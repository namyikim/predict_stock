"""'오늘의 장 회고' 탭(2026-09-30 요청).

오늘의 예측과 장기 전망 사이에 탭을 두고, 최근 거래일 회고 3~4개를 날짜 단추로 고른다. 장이 끝나기 전에는
직전 거래일 회고가 보이므로 '아직 오늘 장이 마감되지 않았습니다'를 알린다.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forecast_utils as fu  # noqa: E402
import report_html as rh  # noqa: E402


STORED = ROOT / "forecast_history" / "samsung" / "reviews" / "2026-09-29.json"


def review(day, **extra):
    """저장소의 실제 회고 기록을 날짜만 바꿔 쓴다(회고 한 개에 필요한 값이 많다)."""
    import json
    base = json.loads(STORED.read_text(encoding="utf-8"))
    base.update(session_date=day, generated_at=f"{day} 16:12 KST", **extra)
    return base


def page():
    h3 = lambda t: f'<h3 style="x">{t}</h3><p>본문</p>'
    raw = ('<div>' + h3("1. 다음 거래일 방향") + h3("한눈에 보는 장기 전망 요약") + h3("1. 장기 전망 (월간)")
           + h3("주간 반도체 뉴스") + '</div>')
    return rh.tabify_sections(raw)


class ReviewTabTests(unittest.TestCase):
    def test_keeps_the_latest_four_days_newest_first(self):
        days = ["2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23"]
        html = fu.review_tab_html([review(d) for d in days])
        import re
        self.assertEqual(re.findall(r'data-review-day="([^"]+)"', html), days[::-1][:4])
        self.assertIn('data-latest="2026-09-23"', html)
        self.assertIn('data-latest-label="9/23(수)"', html)
        self.assertEqual(html.count("<h3"), 1)   # 탭 나누기가 h3 로 절을 자르므로 날짜별 본문은 h4

    def test_only_the_latest_panel_is_open_at_first(self):
        html = fu.review_tab_html([review("2026-09-28"), review("2026-09-29")])
        self.assertIn('<div data-review-panel="2026-09-29">', html)
        self.assertIn('<div data-review-panel="2026-09-28" hidden>', html)

    def test_notice_covers_before_close_after_close_and_weekend(self):
        html = fu.review_tab_html([review("2026-09-29")])
        self.assertIn("아직 오늘 장이 마감되지 않았습니다", html)
        self.assertIn("회고를 만드는 중입니다", html)
        self.assertIn("오늘은 휴장일입니다", html)
        self.assertIn("mins<15*60+30", html)

    def test_no_reviews_means_nothing(self):
        self.assertEqual(fu.review_tab_html([]), "")

    def test_tab_goes_right_after_todays_forecast(self):
        out = fu.insert_review_section(page(), fu.review_tab_html([review("2026-09-29")]))
        nav = out[out.find('<nav class="rtabs"'):out.find("</nav>")]
        self.assertLess(nav.find("#rtab-0"), nav.find("#rtab-review"))
        self.assertLess(nav.find("#rtab-review"), nav.find("#rtab-1"))
        self.assertIn(">오늘의 장 회고</a>", nav)
        self.assertEqual(rh.tab_structure_problems(out), [])

    def test_reinserting_replaces_in_place(self):
        once = fu.insert_review_section(page(), fu.review_tab_html([review("2026-09-28")]))
        twice = fu.insert_review_section(once, fu.review_tab_html([review("2026-09-29"), review("2026-09-28")]))
        self.assertEqual(twice.count('id="rtab-review"'), 1)
        self.assertEqual(twice.count('href="#rtab-review"'), 1)
        self.assertEqual(twice.count(fu.REVIEW_START), 1)
        self.assertIn('data-latest="2026-09-29"', twice)

    def test_old_review_in_the_first_tab_moves_out(self):
        old = fu.insert_review_section(page(), fu.review_section_html(review("2026-09-28")))
        moved = fu.insert_review_section(old.replace('<section class="rtab-panel" id="rtab-review">', '<section class="rtab-panel" id="rtab-x">'),
                                         fu.review_tab_html([review("2026-09-29")]))
        self.assertEqual(moved.count(fu.REVIEW_START), 1)


class ReviewRunTests(unittest.TestCase):
    def test_review_run_gathers_recent_reviews_from_the_repository(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import build_session_review as sr
        today = review("2026-10-05")
        got = sr.recent_reviews("samsung", today)
        self.assertEqual(got[0]["session_date"], "2026-10-05")
        self.assertLessEqual(len(got), 4)
        dates = [r["session_date"] for r in got]
        self.assertEqual(dates, sorted(dates, reverse=True))


if __name__ == "__main__":
    unittest.main()
