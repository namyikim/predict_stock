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


class TopicTests(unittest.TestCase):
    def test_classification_uses_words_actually_in_the_headline(self):
        self.assertEqual(ai.classify("SK하이닉스 HBM 공급 확대"), "반도체·인프라")
        self.assertEqual(ai.classify("오픈AI, 새 LLM 공개"), "모델·연구")
        self.assertEqual(ai.classify("EU, AI 규제 법안 합의"), "정책·규제")
        self.assertEqual(ai.classify("어느 주제에도 안 걸리는 말"), ai.OTHER)

    def test_semiconductor_comes_first_when_several_match(self):
        # 이 저장소의 관심사라 반도체를 앞에 둔다. 순서가 바뀌면 분류가 달라진다.
        self.assertEqual(ai.classify("HBM 투자 확대"), "반도체·인프라")

    def test_empty_topics_are_dropped_and_other_goes_last(self):
        items = [{"title": "HBM 공급", "time": 1, "source": "", "link": ""},
                 {"title": "분류 안 되는 제목", "time": 2, "source": "", "link": ""}]
        groups = ai.group_by_topic(items)
        self.assertEqual([g[0] for g in groups], ["반도체·인프라", ai.OTHER])

    def test_each_topic_is_capped(self):
        items = [{"title": f"HBM 기사 {i}", "time": i, "source": "", "link": ""} for i in range(20)]
        groups = ai.group_by_topic(items, per_topic=3)
        self.assertEqual(len(groups[0][2]), 3)


class HotTermTests(unittest.TestCase):
    def test_common_words_are_excluded_and_singletons_dropped(self):
        items = [{"title": "AI 인공지능 엔비디아 실적"}, {"title": "엔비디아 주가 상승"},
                 {"title": "한 번만 나온 낱말"}]
        terms = dict(ai.hot_terms(items))
        self.assertEqual(terms.get("엔비디아"), 2)
        self.assertNotIn("AI", terms)          # 불용어
        self.assertNotIn("인공지능", terms)
        self.assertNotIn("주가", terms)        # 한 번만 나왔다


class SafetyTests(unittest.TestCase):
    """헤드라인은 남이 쓴 글이다. 그대로 페이지에 넣으면 안 된다."""

    def test_non_http_links_are_not_linked(self):
        self.assertEqual(ai._safe_url("javascript:alert(1)"), "")
        self.assertEqual(ai._safe_url("data:text/html,<script>"), "")
        self.assertEqual(ai._safe_url("https://example.com/a"), "https://example.com/a")

    def test_headline_markup_is_escaped(self):
        now = datetime(2026, 9, 12, 20, 0, tzinfo=KST)
        items = [{"title": '<script>alert("x")</script> HBM', "time": now,
                  "source": "<b>매체</b>", "link": "javascript:alert(1)"}]
        page = ai.build_html(ai.group_by_topic(items), ai.hot_terms(items), now, 1, [])
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("javascript:alert", page)
        self.assertNotIn("<b>매체</b>", page, "매체명도 그대로 들어가면 안 된다")
        self.assertIn("&lt;b&gt;매체&lt;/b&gt;", page)

    def test_page_states_what_it_does_not_do(self):
        now = datetime(2026, 9, 12, 20, 0, tzinfo=KST)
        items = [{"title": "HBM 공급 확대", "time": now, "source": "매체", "link": "https://e.com/1"}]
        page = ai.build_html(ai.group_by_topic(items), [], now, 1, [])
        self.assertIn("요약하거나 해석하지 않았", page)
        self.assertIn("투자 자문이 아닙니다", page)
        self.assertIn("rel=\"noopener noreferrer nofollow\"", page)

    def test_failed_queries_are_disclosed_on_the_page(self):
        now = datetime(2026, 9, 12, 20, 0, tzinfo=KST)
        items = [{"title": "HBM", "time": now, "source": "", "link": ""}]
        page = ai.build_html(ai.group_by_topic(items), [], now, 1, ["AI 규제(TimeoutError)"])
        self.assertIn("받지 못했습니다", page)
        self.assertIn("AI 규제", page)


class PublishTests(unittest.TestCase):
    def test_empty_result_never_overwrites_a_good_page(self):
        source = (ROOT / "tools" / "build_ai_news_report.py").read_text(encoding="utf-8")
        self.assertIn("if not items:", source)
        self.assertIn("기존 보고서를 유지합니다", source)

    def test_page_is_wired_into_menu_counter_and_admin(self):
        index = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="./ai_news/"', index)
        # 인기 급상승 검색어 바로 위에 있어야 한다.
        self.assertLess(index.index('href="./ai_news/"'), index.index('href="./trends/"'))
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
