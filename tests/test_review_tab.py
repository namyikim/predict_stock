"""'오늘의 장 회고' 탭(2026-09-30 요청).

오늘의 장 예측과 장기 전망 사이에 탭을 두고, 최근 거래일 회고 3~4개를 날짜 단추로 고른다. 장이 끝나기 전에는
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


class ReviewOrderTests(unittest.TestCase):
    """회고 본문 순서(2026-09-30 요청): 누가 팔고 샀나 → 그날 함께 관찰된 것 → 흐름이 바뀐 시각과 뉴스 → 오늘 장 → 아침 예측과 비교."""

    def titles(self, r):
        import re
        return re.findall(r'<b>(\d+\. [^<]+)</b>', fu.review_section_html(r))

    def test_order_and_numbers_with_flows(self):
        self.assertEqual(self.titles(review("2026-09-29")),
                         ["1. 누가 팔고 샀나", "2. 그날 함께 관찰된 것", "3. 흐름이 바뀐 시각과 그 전후의 뉴스",
                          "4. 오늘 장", "5. 아침 예측과 비교", "6. 다가오는 주요 일정"])

    def test_numbers_close_up_when_flows_are_missing(self):
        # 수급이 없어도 시장·업종 등 '그날 함께 관찰된 것'은 남는다(2026-10-01). '누가 팔고 샀나'만 빠진다.
        self.assertEqual(self.titles(review("2026-09-29", flow_story=None)),
                         ["1. 그날 함께 관찰된 것", "2. 흐름이 바뀐 시각과 그 전후의 뉴스", "3. 오늘 장",
                          "4. 아침 예측과 비교", "5. 다가오는 주요 일정"])

    def test_source_codes_are_shown_in_words(self):
        r = review("2026-09-29")
        r["flow_story"] = dict(r["flow_story"], source_note="출처 last_successful_fetch · 잠정치")
        html = fu.review_section_html(r)
        self.assertNotIn("last_successful_fetch", html)
        self.assertIn("저장소 보관본(마지막 성공분)", html)


class ReviewDateLineTests(unittest.TestCase):
    """날짜별 회고는 제목('장 회고 — 날짜') 없이 날짜만 보인다(2026-09-30: '1.1 장 회고 — …'로 번호까지 붙었다)."""

    def test_date_only_and_no_heading_tag(self):
        import re
        html = fu.review_tab_html([review("2026-09-29"), review("2026-09-28")])
        self.assertEqual(re.findall(r"<h4\b", html), [])
        self.assertNotIn("장 회고 —", html)
        self.assertIn(">2026-09-29 (화)</div>", html)
        self.assertIn(">2026-09-28 (월)</div>", html)


class ReviewContextTests(unittest.TestCase):
    """'그날의 맥락'과 '다가오는 주요 일정'(2026-09-30: 장 마감 회고 영상 검토에서 나온 항목)."""

    def setUp(self):
        import os
        self._cwd = os.getcwd()
        os.chdir(ROOT)     # macro_inputs/corporate_actions.csv 를 저장소 기준으로 읽는다

    def tearDown(self):
        import os
        os.chdir(self._cwd)

    def test_ex_dividend_day_adds_the_dividend_back(self):
        ctx = fu.review_context(review("2026-09-29"))
        d = ctx["dividend"]
        self.assertEqual(d["per_share"], 4600.0)
        self.assertAlmostEqual(d["pct"], 4600 / 270000, places=6)
        self.assertAlmostEqual(d["gap_ex"], (266000 + 4600) / 270000 - 1, places=6)
        self.assertIn("배당락일", fu.review_context_html(ctx))

    def test_no_dividend_on_other_days(self):
        self.assertIsNone(fu.review_context(review("2026-09-28"))["dividend"])

    def test_calendar_flags(self):
        flags = lambda day: " ".join(fu.review_context(review(day))["calendar"])
        self.assertIn("3분기 마지막 거래일", flags("2026-09-30"))
        self.assertIn("옵션 만기일", flags("2026-10-08"))
        self.assertIn("선물·옵션 동시 만기일", flags("2026-12-10"))
        self.assertIn("월 마지막 거래일", flags("2026-10-30"))
        self.assertEqual(flags("2026-09-29"), "")

    def test_upcoming_events_close_the_review(self):
        import re
        titles = re.findall(r'<b>(\d+\. [^<]+)</b>', fu.review_section_html(review("2026-09-29")))
        self.assertEqual(titles[-1], "6. 다가오는 주요 일정")
        self.assertIn("마이크론 실적", fu.review_section_html(review("2026-09-29")))

    def test_review_run_stores_yahoo_dividends(self):
        text = (ROOT / "tools" / "build_session_review.py").read_text(encoding="utf-8")
        self.assertIn('"context": session_context(spec["ticker"], session_date)', text)


class MondayVideoAdditionsTests(unittest.TestCase):
    """9/28 회고 영상 검토(2026-09-30)로 더한 것: 배당락 전일, 연휴 전, 연속 일수, 휴장 기간 미국 누적."""

    def setUp(self):
        import os
        self._cwd = os.getcwd(); os.chdir(ROOT)

    def tearDown(self):
        import os
        os.chdir(self._cwd)

    def test_day_before_ex_dividend_and_pre_holiday_flags(self):
        flags = lambda day: " ".join(fu.review_context(review(day))["calendar"])
        self.assertIn("내일(09/29)이 배당락일 — 주당 4,600원", flags("2026-09-28"))
        self.assertIn("연휴 전 마지막 거래일 — 다음 거래일이 10/06(4일 뒤)", flags("2026-10-02"))
        self.assertNotIn("연휴 전", flags("2026-09-25") if False else flags("2026-09-29"))

    def test_next_session_skips_weekends_and_listed_holidays(self):
        import pandas as pd
        self.assertEqual(fu.next_krx_session(pd.Timestamp("2026-10-02")), pd.Timestamp("2026-10-06"))
        self.assertEqual(fu.next_krx_session(pd.Timestamp("2026-10-08")), pd.Timestamp("2026-10-12"))

    def test_streak_and_holiday_window_wording(self):
        story = fu.flow_story({"foreign_net": -1000, "inst_net": 500, "indiv_net": 500}, None,
                              {"c2c": -.05, "sox_ret": .04, "us_nights": 3, "prior_streak": 4}, 1000)
        text = " ".join(story["observations"])
        self.assertIn("휴장 기간(3거래일 누적) SOX는 +4.00%로 반대 방향", text)
        self.assertIn("직전 4거래일 연속 상승 뒤의 하락", text)
        r = review("2026-09-28"); r["summary"].update(us_nights=3)
        self.assertIn("휴장 기간 미국 누적(3거래일) SOX / 나스닥 / 마이크론", fu.review_section_html(r))

    def test_review_run_uses_the_previous_korean_session_as_the_window_start(self):
        text = (ROOT / "tools" / "build_session_review.py").read_text(encoding="utf-8")
        self.assertIn('summary["us_nights"] = int(len(window))', text)
        self.assertIn('summary["prior_streak"] = int(streak * sign)', text)


class WednesdayVideoAdditionsTests(unittest.TestCase):
    """9/30 회고 영상 검토(2026-10-01)로 고친 것: 모두 순매도인 날, 분기말 기관 매도=리밸런싱, 이동평균·고점, 국내 잠정실적."""

    def setUp(self):
        import os
        self._cwd = os.getcwd(); os.chdir(ROOT)

    def tearDown(self):
        import os
        os.chdir(self._cwd)

    def test_all_three_selling_on_an_up_day_is_not_called_buying(self):
        story = fu.flow_story({"foreign_net": -3.4e5, "inst_net": -1.35e5, "indiv_net": -1.06e5}, None,
                              {"c2c": .0062}, 1781000)
        self.assertTrue(story["all_one_side"])
        self.assertIsNone(story["counter"])
        html = fu.flow_story_html(story)
        self.assertIn("모두 순매도", html)
        self.assertNotIn("가장 많이 산 쪽", html)

    def test_quarter_end_institution_selling_is_tied_to_rebalancing(self):
        import json
        real = json.loads((ROOT / "forecast_history" / "samsung" / "reviews" / "2026-09-30.json").read_text(encoding="utf-8"))
        html = fu.review_section_html(real)   # 9/30 실제 기록: 기관 -3,679억 순매도
        self.assertIn("분기 마지막 거래일에 기관이", html)
        self.assertIn("리밸런싱(주식·현금 비중 맞추기)과 맞는 모양", html)
        self.assertNotIn("마지막 거래일에 기관이", fu.review_section_html(review("2026-09-29")))

    def test_moving_average_and_high_rows(self):
        r = review("2026-09-29"); r["summary"].update(close=272500.0, ma5=275000.0, ma20=268000.0, high20=283000.0, high252=283000.0)
        html = fu.review_section_html(r)
        self.assertIn("종가의 5일선 / 20일선 대비", html)
        self.assertIn("아래 (-0.91%) / 위 (+1.68%)", html)
        self.assertIn("20일 고점 / 52주 고점 대비", html)

    def test_korean_expected_earnings_show_as_unconfirmed(self):
        ctx = fu.review_context(review("2026-10-01"))
        labels = [ev["label"] for ev in ctx["next_events"]]
        self.assertTrue(any("삼성전자 3분기 잠정실적(예상) · 미확정" in x for x in labels))

    def test_review_run_computes_ma_and_highs(self):
        text = (ROOT / "tools" / "build_session_review.py").read_text(encoding="utf-8")
        self.assertIn('summary["ma5"], summary["ma20"]', text)
        self.assertIn('summary["high252"]', text)
