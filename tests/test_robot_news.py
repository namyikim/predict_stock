"""최신 로봇 뉴스(2026-10-02 요청): AI 뉴스와 같은 도구에 주제만 바꾼 페이지."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_ai_news_report as ai  # noqa: E402
import build_robot_news_report as robot  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 2, 11, 0, tzinfo=KST)


def item(title, source, minutes=10):
    return {"time": NOW - timedelta(minutes=minutes), "title": title, "source": source,
            "link": "https://news.example.com/" + source}


ITEMS = [item("현대차그룹 아틀라스 로봇 손 공개", "가", 5), item("아틀라스 로봇 손가락 넷으로 너트 조여", "나", 9),
         item("휴머노이드 로봇 양산 준비", "다", 12), item("휴머노이드 로봇 실증 확대", "라", 20)]


class TopicTests(unittest.TestCase):
    def test_robot_is_too_common_to_rank(self):
        """'로봇'은 모든 헤드라인에 나온다. 순위에 오르면 무엇이 화제인지 알려 주지 않는다."""
        plain = [row["term"] for row in ai.rank_terms(ITEMS)]
        self.assertEqual(plain[0], "로봇")
        ranked = [row["term"] for row in ai.rank_terms(ITEMS, stopwords=robot.STOPWORDS)]
        self.assertNotIn("로봇", ranked)
        self.assertEqual(set(ranked), {"아틀라스", "휴머노이드"})

    def test_page_names_its_topic_and_counts_its_own_views(self):
        page = robot.build_html(ai.rank_terms(ITEMS, stopwords=robot.STOPWORDS), NOW, len(ITEMS), [])
        self.assertIn("<title>최신 로봇 트렌드 및 뉴스 2026-10-02</title>", page)
        self.assertIn("ROBOT TRENDS &amp; NEWS", page)
        self.assertIn("구글 뉴스에서 로봇 관련 검색어로", page)
        self.assertIn('P="robot_news"', page)
        self.assertNotIn('P="ai_news"', page)
        self.assertNotIn("AI TRENDS", page)
        self.assertEqual(page.count('class="page-title"'), 2)          # 허브가 숨길 수 있게 제목에 표시가 있다
        self.assertIn("투자 자문이 아닙니다", page)

    def test_ai_page_is_unchanged_by_default(self):
        page = ai.build_html([], NOW, 0, [])
        self.assertIn("<title>최신 AI 트렌드 및 뉴스 2026-10-02</title>", page)
        self.assertIn('P="ai_news"', page)
        self.assertIn("구글 뉴스에서 AI 관련 검색어로", page)
        self.assertEqual(ai.AI_TOPIC["queries"], ai.QUERIES)

    def test_queries_are_about_robots(self):
        self.assertIn("휴머노이드 로봇", robot.QUERIES)
        self.assertEqual(len(set(robot.QUERIES)), len(robot.QUERIES))
        self.assertEqual(robot.ROBOT_TOPIC["key"], "robot_news")


class WiringTests(unittest.TestCase):
    def test_hub_counter_admin_and_first_page(self):
        hub = (ROOT / "docs" / "news" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-src="../robot_news/"', hub)
        self.assertLess(hub.index('data-src="../ai_news/"'), hub.index('data-src="../robot_news/"'))
        worker = (ROOT / "counter" / "worker.js").read_text(encoding="utf-8")
        self.assertIn('"robot_news"', worker)        # 이 키가 없으면 카운터가 조회를 거부한다(Worker 재배포 필요)
        admin = (ROOT / "docs" / "admin" / "index.html").read_text(encoding="utf-8")
        self.assertIn('key: "robot_news"', admin)
        # 탭이 가리키는 페이지가 저장소에 있어야 첫 자동 실행 전에도 404 가 아니다.
        self.assertTrue((ROOT / "docs" / "robot_news" / "index.html").exists())

    def test_workflow_runs_it_after_ai_news_even_if_that_failed(self):
        import yaml
        workflow = yaml.safe_load((ROOT / ".github/workflows/daily-report.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["ai_news"]["steps"]
        runs = [str(s.get("run", "")) for s in steps]
        ai_at = next(i for i, r in enumerate(runs) if "build_ai_news_report.py" in r)
        robot_at = next(i for i, r in enumerate(runs) if "build_robot_news_report.py" in r)
        self.assertLess(ai_at, robot_at)
        self.assertIn("--publish", runs[robot_at])
        self.assertIn("!cancelled()", str(steps[robot_at].get("if")))


if __name__ == "__main__":
    unittest.main()
