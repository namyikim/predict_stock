# -*- coding: utf-8 -*-
"""이력 보관본은 덮어쓰지 않고 날짜로 합친다(2026-09-24).

같은 보관본(macro_history/*.csv)을 여러 실행이 서로 다른 기간으로 받아 통째로 덮어써, 매 실행 오래된 이력이
지워졌다 되살아났다 — 선행지수 24행(영업이익 vs 장기 전망), 뉴스심리 3,468행(노트북 vs 장기 전망), 관세청 122행.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import forecast_utils as fu  # noqa: E402
import github_pages as gp  # noqa: E402

LONG = "month,value\n1998-01-01,99.1\n1999-01-01,99.5\n2000-01-01,100.0\n2026-08-01,102.8694\n"
SHORT = "month,value\n2000-01-01,100.0\n2026-08-01,102.8694\n"


class MergeHistoryCsvTests(unittest.TestCase):
    def test_a_shorter_series_no_longer_deletes_older_history(self):
        self.assertEqual(fu.merge_history_csv(LONG, SHORT), LONG)

    def test_ping_pong_settles_and_stops_changing(self):
        state = SHORT                                   # 지금처럼 짧은 쪽이 마지막에 쓴 상태에서 시작
        for writer in (LONG, SHORT, LONG, SHORT):
            state = fu.merge_history_csv(state, writer)
        self.assertEqual(state, LONG)
        self.assertEqual(fu.merge_history_csv(state, SHORT), state, "짧은 쪽이 다시 써도 바뀌지 않는다")

    def test_unchanged_input_is_byte_identical(self):
        text = "date,value\n2014-07-01,101.38\n2014-07-02,105.77\n2026-09-20,96.8\n"
        self.assertEqual(fu.merge_history_csv(text, text), text)
        self.assertEqual(fu.merge_history_csv(text, "date,value\n2026-09-20,96.8\n"), text)

    def test_new_values_win_for_the_same_date_and_new_rows_are_added(self):
        merged = fu.merge_history_csv(LONG, "month,value\n2026-08-01,102.9\n2026-09-01,103.0\n")
        self.assertIn("2026-08-01,102.9\n", merged)
        self.assertNotIn("102.8694", merged)
        self.assertTrue(merged.endswith("2026-09-01,103.0\n"))
        self.assertTrue(merged.startswith("month,value\n1998-01-01,99.1\n"))

    def test_strings_are_kept_as_they_are(self):
        old = "date,value\n2020-01-01,102.0\n2020-01-02,1.50\n"
        self.assertEqual(fu.merge_history_csv(old, "date,value\n2020-01-03,7\n"),
                         "date,value\n2020-01-01,102.0\n2020-01-02,1.50\n2020-01-03,7\n")

    def test_new_columns_are_appended(self):
        merged = fu.merge_history_csv("date,a\n2020-01-01,1\n", "date,a,b\n2020-01-02,2,3\n")
        self.assertEqual(merged, "date,a,b\n2020-01-01,1,\n2020-01-02,2,3\n")

    def test_crlf_input_is_read_and_lf_written(self):
        self.assertEqual(fu.merge_history_csv(LONG.replace("\n", "\r\n"), SHORT), LONG)

    def test_cases_that_cannot_be_merged_fall_back_safely(self):
        self.assertEqual(fu.merge_history_csv(None, SHORT), SHORT)
        self.assertEqual(fu.merge_history_csv("", SHORT), SHORT)
        self.assertEqual(fu.merge_history_csv(LONG, ""), LONG, "빈 새 자료로 이력을 지우지 않는다")
        self.assertEqual(fu.merge_history_csv(LONG, "month,value\n"), LONG)
        self.assertEqual(fu.merge_history_csv(LONG, "date,value\n2020-01-01,1\n"), "date,value\n2020-01-01,1\n")
        dup = "month,value\n2020-01-01,1\n2020-01-01,2\n"
        self.assertEqual(fu.merge_history_csv(LONG, dup), dup, "첫 열이 중복이면 합치지 않는다")

    def test_real_caches_are_unchanged_when_merged_with_themselves(self):
        for name in ("cli_g20.csv", "news_sentiment.csv", "term_spread.csv", "investor_flows_005930.csv"):
            path = ROOT / "macro_history" / name
            if not path.exists():
                continue
            with self.subTest(name=name):
                text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
                self.assertEqual(fu.merge_history_csv(text, text), text)


class PublishHistoryTests(unittest.TestCase):
    def test_nothing_is_published_when_the_merge_changes_nothing(self):
        with patch.object(gp, "fetch_with_sha", return_value=(LONG, "s1")), \
             patch.object(gp, "publish") as publish:
            self.assertEqual(gp.publish_history("macro_history/cli_g20.csv", SHORT, "tok", "m"), "unchanged")
        publish.assert_not_called()

    def test_changes_are_published_with_the_sha_read_and_a_merge_for_later_changes(self):
        with patch.object(gp, "fetch_with_sha", return_value=(SHORT, "s1")), \
             patch.object(gp, "publish", return_value="abc1234") as publish:
            gp.publish_history("macro_history/cli_g20.csv", LONG, "tok", "m")
        (path, text, tok, message), kwargs = publish.call_args
        self.assertEqual(text, LONG)
        self.assertEqual(kwargs["expected_sha"], "s1")
        later = "month,value\n2026-09-01,103.0\n"             # 그 사이 다른 실행이 더한 최신본
        self.assertEqual(kwargs["merge"](later), fu.merge_history_csv(later, LONG))

    def test_a_missing_file_is_created(self):
        with patch.object(gp, "fetch_with_sha", return_value=(None, None)), \
             patch.object(gp, "publish", return_value="abc1234") as publish:
            gp.publish_history("macro_history/new.csv", SHORT, "tok", "m")
        self.assertIsNone(publish.call_args.kwargs["expected_sha"])


class WiringTests(unittest.TestCase):
    def source(self, name):
        return (ROOT / "tools" / name).read_text(encoding="utf-8")

    def test_shared_caches_use_the_merging_publisher(self):
        earnings = self.source("build_earnings_forecast.py")
        self.assertIn('github_pages.publish_history("macro_history/cli_g20.csv"', earnings)
        self.assertIn('github_pages.publish_history("macro_history/customs_exports.csv"', earnings)
        longterm = self.source("build_longterm_report.py")
        self.assertIn('github_pages.publish_history(f"macro_history/{name}"', longterm)
        self.assertIn('github_pages.publish_history("macro_history/korea_exports.csv"', longterm)
        for text in (earnings, longterm):
            self.assertNotIn('github_pages.publish("macro_history/cli_g20.csv"', text)

    def test_notebook_merges_before_uploading_its_caches(self):
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        cell = next("".join(c["source"]) for c in nb["cells"] if "for _name, _text in _candidates:" in "".join(c["source"]))
        loop = cell[cell.index("for _name, _text in _candidates:"):]
        self.assertIn("_merged = merge_history_csv(_existing, _text)", loop)
        self.assertIn("github_put_expected(_path, _merged", loop)
        self.assertNotIn('github_put(f"{GITHUB_MACRO_DIR}/{_name}", _text', loop)


if __name__ == "__main__":
    unittest.main()
