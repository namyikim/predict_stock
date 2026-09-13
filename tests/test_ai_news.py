# -*- coding: utf-8 -*-
"""최신 AI 트렌드 및 뉴스 — 모으고 묶기만 한다. 요약·해석은 하지 않는다.

네트워크를 타지 않는다. RSS 원문을 직접 넣어 파싱·분류·표시 규칙만 본다.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_ai_news_report as ai  # noqa: E402

KST = timezone(timedelta(hours=9))


def rss(*items):
    body = "".join(
        f"<item><title>{t}</title><pubDate>{d}</pubDate>"
        f"<link>{u}</link><source>{s}</source></item>"
        for t, d, u, s in items)
    return f"<rss><channel>{body}</channel></rss>".encode("utf-8")


class ParseTests(unittest.TestCase):
    def test_source_suffix_is_removed_from_the_title(self):
        raw = rss(("엔비디아 실적 발표 - 한국경제", "Fri, 12 Sep 2026 09:00:00 GMT",
                   "https://example.com/a", "한국경제"))
        item = ai.parse_rss(raw)[0]
        self.assertEqual(item["title"], "엔비디아 실적 발표")
        self.assertEqual(item["source"], "한국경제")
        self.assertEqual(item["time"].tzinfo.utcoffset(None), timedelta(hours=9))

    def test_items_without_a_readable_time_are_dropped(self):
        raw = rss(("제목", "언제인지 모름", "https://example.com/a", "매체"),
                  ("좋은 제목", "Fri, 12 Sep 2026 09:00:00 GMT", "https://example.com/b", "매체"))
        self.assertEqual([i["title"] for i in ai.parse_rss(raw)], ["좋은 제목"])

    def test_empty_titles_are_dropped(self):
        self.assertEqual(ai.parse_rss(rss(("", "Fri, 12 Sep 2026 09:00:00 GMT", "u", "s"))), [])


class CollectTests(unittest.TestCase):
    def now(self):
        return datetime(2026, 9, 12, 20, 0, tzinfo=KST)

    def test_duplicates_across_queries_are_merged_and_sorted_newest_first(self):
        feeds = {
            "a": rss(("같은 기사 제목", "Sat, 12 Sep 2026 09:00:00 GMT", "https://e.com/1", "A")),
            "b": rss(("같은  기사   제목!", "Sat, 12 Sep 2026 09:10:00 GMT", "https://e.com/2", "B"),
                     ("다른 기사", "Sat, 12 Sep 2026 10:00:00 GMT", "https://e.com/3", "B")),
        }
        items, failed = ai.collect(("a", "b"), now=self.now(), fetch=lambda q: feeds[q])
        self.assertEqual([i["title"] for i in items], ["다른 기사", "같은 기사 제목"])
        self.assertEqual(failed, [])

    def test_old_and_future_items_are_excluded(self):
        feeds = {"a": rss(("오래된 기사", "Wed, 09 Sep 2026 09:00:00 GMT", "https://e.com/1", "A"),
                          ("미래 기사", "Mon, 14 Sep 2026 09:00:00 GMT", "https://e.com/2", "A"),
                          ("오늘 기사", "Sat, 12 Sep 2026 09:00:00 GMT", "https://e.com/3", "A"))}
        items, _ = ai.collect(("a",), now=self.now(), fetch=lambda q: feeds[q])
        self.assertEqual([i["title"] for i in items], ["오늘 기사"])

    def test_one_failing_query_does_not_lose_the_rest(self):
        def fetch(query):
            if query == "bad":
                raise TimeoutError("timed out")
            return rss(("살아남은 기사", "Sat, 12 Sep 2026 09:00:00 GMT", "https://e.com/1", "A"))
        items, failed = ai.collect(("bad", "good"), now=self.now(), fetch=fetch)
        self.assertEqual([i["title"] for i in items], ["살아남은 기사"])
        self.assertEqual(len(failed), 1)
        self.assertIn("bad", failed[0])


NOW = datetime(2026, 9, 12, 20, 0, tzinfo=KST)


def item(title, source="매체", minutes=0, link="https://e.com/a"):
    return {"title": title, "source": source, "link": link, "time": NOW - timedelta(minutes=minutes)}


class RankTests(unittest.TestCase):
    """인기 급상승 검색어처럼 순위로 보인다(2026-09-13, 주제별 묶음에서 바꿈).

    순위는 다룬 매체 수 → 기사 수 → 최근 시각. 검색량이 아니라 헤드라인 언급이다.
    """

    def terms(self, items, **kwargs):
        return [row["term"] for row in ai.rank_terms(items, **kwargs)]

    def test_terms_covered_by_more_outlets_rank_higher(self):
        items = [item("엔비디아 실적", "A"), item("엔비디아 주가", "B"), item("엔비디아 신제품", "C"),
                 item("오픈AI 신제품", "A"), item("오픈AI 소송", "A"), item("오픈AI 인수", "B")]
        ranked = ai.rank_terms(items)
        self.assertEqual([r["term"] for r in ranked[:2]], ["엔비디아", "오픈AI"])
        self.assertEqual((ranked[0]["sources"], ranked[0]["articles"]), (3, 3))
        self.assertEqual((ranked[1]["sources"], ranked[1]["articles"]), (2, 3))

    def test_one_outlet_repeating_a_story_does_not_make_a_trend(self):
        items = [item("앤트로픽 인터뷰", "A"), item("앤트로픽 소송", "A"), item("앤트로픽 인수", "A")]
        self.assertEqual(ai.rank_terms(items), [])

    def test_korean_english_compounds_stay_whole(self):
        """쪼개면 '오픈'이 1위가 된다(2026-09-13 실제 헤드라인에서 확인)."""
        terms = self.terms([item("오픈AI 상장 연기", "A"), item("오픈AI 올트먼 발언", "B")])
        self.assertIn("오픈AI", terms)
        self.assertNotIn("오픈", terms)

    def test_particles_are_removed_only_when_the_bare_word_also_appears(self):
        terms = self.terms([item("엔비디아가 반등", "A"), item("엔비디아 반등", "B"),
                            item("마이크로 반등", "A"), item("마이크로 신고가", "B")])
        self.assertIn("엔비디아", terms)
        self.assertNotIn("엔비디아가", terms)
        self.assertIn("마이크로", terms, "조사처럼 끝나는 이름을 자르면 안 된다")

    def test_generic_words_and_verb_forms_do_not_rank(self):
        terms = self.terms([item("AI 개발 속도 늦춰야", "A"), item("AI 개발 속도 늦춰야", "B")])
        for word in ("AI", "개발", "속도", "늦춰야"):
            self.assertNotIn(word, terms)

    def test_each_term_shows_its_latest_headlines_first_and_is_capped(self):
        row = ai.rank_terms([item(f"엔비디아 {i}", f"매체{i}", minutes=i) for i in range(6)], per_term=3)[0]
        self.assertEqual(row["term"], "엔비디아")
        self.assertEqual(len(row["news"]), 3)
        self.assertEqual(row["news"][0]["title"], "엔비디아 0")

    def test_the_list_is_capped_like_trending_searches(self):
        words = ["엔비디아", "오픈AI", "반도체", "데이터센터", "앤트로픽", "올트먼",
                 "자율주행", "울산", "최태원", "현대차", "삼성전자", "하이닉스"]
        items = [item(word, source) for word in words for source in ("A", "B")]
        self.assertEqual(len(ai.rank_terms(items)), ai.RANK_LIMIT)

    def test_categories_are_gone(self):
        for name in ("group_by_topic", "classify", "hot_terms", "TOPICS"):
            self.assertFalse(hasattr(ai, name), f"{name} 이 남아 있다")


class SafetyTests(unittest.TestCase):
    """헤드라인은 남이 쓴 글이다. 그대로 페이지에 넣으면 안 된다."""

    def page(self, items, failed=()):
        return ai.build_html(ai.rank_terms(items), NOW, len(items), list(failed))

    def test_non_http_links_are_not_linked(self):
        self.assertEqual(ai._safe_url("javascript:alert(1)"), "")
        self.assertEqual(ai._safe_url("data:text/html,<script>"), "")
        self.assertEqual(ai._safe_url("https://example.com/a"), "https://example.com/a")

    def test_headline_markup_is_escaped(self):
        page = self.page([item('<script>alert("x")</script> 엔비디아', "<b>매체</b>",
                               link="javascript:alert(1)"),
                          item("엔비디아 반등", "B")])
        self.assertIn("엔비디아", page)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("javascript:alert", page)
        self.assertNotIn("<b>매체</b>", page, "매체명도 그대로 들어가면 안 된다")
        self.assertIn("&lt;b&gt;매체&lt;/b&gt;", page)

    def test_page_states_what_it_is_and_is_not(self):
        page = self.page([item("엔비디아 반등", "A"), item("엔비디아 신고가", "B")])
        self.assertIn("요약하거나 해석하지 않았", page)
        self.assertIn("투자 자문이 아닙니다", page)
        self.assertIn("검색량 순위가 아니라", page)
        self.assertIn("rel=\"noopener noreferrer nofollow\"", page)

    def test_ranks_are_numbered_in_order(self):
        page = self.page([item("엔비디아 반등", "A"), item("엔비디아 신고가", "B"), item("엔비디아 급등", "C"),
                          item("오픈AI 상장", "A"), item("오픈AI 연기", "B")])
        self.assertLess(page.index("엔비디아</div>"), page.index("오픈AI</div>"))
        self.assertIn("매체 3곳 · 기사 3건", page)

    def test_an_empty_ranking_says_why(self):
        page = ai.build_html([], NOW, 5, [])
        self.assertIn("순위를 매길 만큼", page)
        self.assertIn("받은 기사가 없습니다", ai.build_html([], NOW, 0, []))

    def test_failed_queries_are_disclosed_on_the_page(self):
        page = self.page([item("엔비디아 반등", "A"), item("엔비디아 신고가", "B")], ["AI 규제(TimeoutError)"])
        self.assertIn("받지 못했습니다", page)
        self.assertIn("AI 규제", page)


class PublishTests(unittest.TestCase):
    def test_empty_result_never_overwrites_a_good_page(self):
        source = (ROOT / "tools" / "build_ai_news_report.py").read_text(encoding="utf-8")
        self.assertIn("if not items:", source)
        self.assertIn("기존 보고서를 유지합니다", source)

    def test_page_is_wired_into_menu_counter_and_admin(self):
        index = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        # 2026-09-13 부터 메인 메뉴에는 '최신 뉴스 및 트렌드' 한 장만 있고, AI 뉴스는 그 안의 첫 탭이다.
        self.assertIn('href="./news/"', index)
        hub = (ROOT / "docs" / "news" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-src="../ai_news/"', hub)
        worker = (ROOT / "counter" / "worker.js").read_text(encoding="utf-8")
        self.assertIn('"ai_news"', worker)      # 이 키가 없으면 카운터가 조회를 거부한다
        admin = (ROOT / "docs" / "admin" / "index.html").read_text(encoding="utf-8")
        self.assertIn('key: "ai_news"', admin)

    def test_workflow_runs_it(self):
        import yaml
        workflow = yaml.safe_load((ROOT / ".github/workflows/daily-report.yml").read_text(encoding="utf-8"))
        job = workflow["jobs"]["ai_news"]
        run = "\n".join(str(s.get("run", "")) for s in job["steps"])
        self.assertIn("build_ai_news_report.py", run)
        self.assertIn("--publish", run)


if __name__ == "__main__":
    unittest.main()
