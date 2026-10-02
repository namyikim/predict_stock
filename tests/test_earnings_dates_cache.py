"""실적 발표일 보관본(2026-10-02, 검토 후속 B).

발표일 캐시가 runs/ 아래에만 있어 Actions 의 새 실행마다 비었고, 매번 2015년 이후 모든 분기를 DART 에 다시 물었다.
저장소 보관본(macro_history/)에 있는 분기는 다시 묻지 않고, 회고를 발행할 때 보관본을 함께 올린다.
"""
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_session_review as R  # noqa: E402

SOURCE = (ROOT / "tools" / "build_session_review.py").read_text(encoding="utf-8")


class Fetcher:
    """분기 말 → 발표일 표로 답하는 가짜 DART. 물은 창을 기록한다."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def __call__(self, corp_code, start, stop):
        self.asked.append((start.date().isoformat(), stop.date().isoformat()))
        quarter = (start - pd.Timedelta(days=1)).date().isoformat()
        date = self.answers.get(quarter)
        rows = [{"rcept_dt": "20990101", "report_nm": "주요사항보고서"}]
        if date and pd.Timestamp(date) <= stop:
            rows.append({"rcept_dt": date.replace("-", ""), "report_nm": "연결재무제표기준영업(잠정)실적(공정공시)"})
        return rows


ANSWERS = {"2025-03-31": "2025-04-08", "2025-06-30": "2025-07-08", "2025-09-30": "2025-10-14",
           "2025-12-31": "2026-01-08", "2026-03-31": "2026-04-07", "2026-06-30": "2026-07-07",
           "2026-09-30": "2026-10-08"}


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache, self.archive = Path(self.tmp.name) / "runs", Path(self.tmp.name) / "macro_history"
        self.archive.mkdir()

    def collect(self, fetch, today, **kwargs):
        return R.collect_earnings_dates("samsung", "00126380", self.cache, fetch, today, since=2025,
                                        archive_dir=self.archive, **kwargs)

    def test_first_run_asks_every_quarter_and_writes_the_cache(self):
        fetch = Fetcher(ANSWERS)
        dates, asked = self.collect(fetch, "2026-10-02")
        self.assertEqual(asked, 7)                               # 2025Q1 ~ 2026Q3(창이 막 열린 분기 포함)
        self.assertEqual(dates, sorted(v for k, v in ANSWERS.items() if k != "2026-09-30"))
        saved = R.read_earnings_dates(self.cache / "earnings_dates_samsung.csv")
        self.assertEqual(saved["2026-06-30"], "2026-07-07")
        self.assertNotIn("2026-09-30", saved)                    # 아직 발표 전이고 창이 열려 있다 — 남기지 않는다

    def test_a_fresh_runner_with_the_archive_only_asks_the_open_quarter(self):
        """Actions 의 새 실행: runs/ 는 비었고 저장소 보관본만 있다."""
        first = Fetcher(ANSWERS)
        self.collect(first, "2026-10-02")
        (self.archive / "earnings_dates_samsung.csv").write_text(
            (self.cache / "earnings_dates_samsung.csv").read_text(encoding="utf-8"), encoding="utf-8")
        (self.cache / "earnings_dates_samsung.csv").unlink()
        fetch = Fetcher(ANSWERS)
        dates, asked = self.collect(fetch, "2026-10-09")
        self.assertEqual(fetch.asked, [("2026-10-01", "2026-10-09")])
        self.assertEqual(asked, 1)
        self.assertEqual(dates[-1], "2026-10-08")
        again = Fetcher(ANSWERS)
        self.assertEqual(self.collect(again, "2026-10-12")[1], 0)   # 찾은 뒤에는 묻지 않는다

    def test_a_closed_window_without_a_filing_is_not_asked_again(self):
        answers = dict(ANSWERS, **{"2025-06-30": None})
        dates, asked = self.collect(Fetcher(answers), "2026-10-02")
        self.assertNotIn("2025-07-08", dates)
        saved = R.read_earnings_dates(self.cache / "earnings_dates_samsung.csv")
        self.assertEqual(saved["2025-06-30"], "")
        fetch = Fetcher(answers)
        self.collect(fetch, "2026-10-02")
        self.assertEqual(fetch.asked, [("2026-10-01", "2026-10-02")])

    def test_old_date_only_cache_is_still_read(self):
        self.cache.mkdir()
        (self.cache / "earnings_dates_samsung.csv").write_text("date\n2026-07-07\n", encoding="utf-8")
        self.assertEqual(R.read_earnings_dates(self.cache / "earnings_dates_samsung.csv"), {"2026-06-30": "2026-07-07"})

    def test_archive_text_merges_by_quarter(self):
        from forecast_utils import merge_history_csv
        old = R.earnings_dates_csv({"2026-03-31": "2026-04-07"})
        new = R.earnings_dates_csv({"2026-03-31": "2026-04-07", "2026-06-30": "2026-07-07"})
        merged = merge_history_csv(old, new)
        self.assertEqual(merged, new)
        self.assertEqual(merge_history_csv(merged, new), merged)     # 바뀐 것이 없으면 그대로 — 커밋이 생기지 않는다


class WiringTests(unittest.TestCase):
    def test_review_publishes_the_archive_with_the_review(self):
        self.assertIn('github_pages.publish_history(f"macro_history/{earnings_dates_name(args.target)}"', SOURCE)
        batch = SOURCE.index("with github_pages.batch(")
        self.assertGreater(SOURCE.index("github_pages.publish_history("), batch)

    def test_reactions_read_the_repo_archive(self):
        self.assertIn('EARNINGS_DATES_ARCHIVE = Path("macro_history")', SOURCE)
        body = SOURCE[SOURCE.index("def earnings_reactions("):SOURCE.index("def buyback_on(")]
        self.assertIn("collect_earnings_dates(", body)


if __name__ == "__main__":
    unittest.main()
