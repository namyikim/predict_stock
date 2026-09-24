# -*- coding: utf-8 -*-
"""노트북의 원장 저장(2026-09-23): 처음 읽은 sha 로만 저장하고, 겹치는 기록은 원격 쪽을 남겨 다시 채점한다.

전에는 (1) github_put 이 올리기 직전에 sha 를 새로 읽어, 방금 읽은 뒤 다른 실행이 더한 행을 오류 없이 지울 수
있었고, (2) 합칠 때 이 실행 쪽(keep="last")을 남기고 다시 채점하지 않아, 시작 뒤에 끝난 09:37 시초가 채점을
채점 전 값으로 덮을 수 있었다.
"""
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forecast_utils as fu  # noqa: E402

NB = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
SYNC_CELL = next("".join(c["source"]) for c in NB["cells"]
                 if "def github_put_expected(" in "".join(c["source"]))


class MergeLedgerLogsTests(unittest.TestCase):
    def frame(self, rows):
        return pd.DataFrame(rows, columns=["record_id", "status", "actual_open"])

    def test_remote_wins_for_shared_records_and_our_new_records_are_added(self):
        remote = self.frame([["a", "scored", 101.0], ["b", "scored", 99.0], ["c", "pending", None]])
        ours = self.frame([["a", "pending", None], ["b", "scored", 99.0], ["new", "pending", None]])
        merged = fu.merge_ledger_logs(remote, ours, bars=None, evaluate=lambda log, bars: log)
        self.assertEqual(list(merged["record_id"]), ["a", "b", "c", "new"])
        self.assertEqual(merged.set_index("record_id").loc["a", "status"], "scored")   # 다른 실행의 채점이 남는다
        self.assertEqual(merged.set_index("record_id").loc["a", "actual_open"], 101.0)

    def test_rows_without_record_id_only_come_from_remote(self):
        remote = self.frame([["a", "scored", 1.0]])
        ours = self.frame([["a", "scored", 1.0], [None, "pending", None]])
        merged = fu.merge_ledger_logs(remote, ours, bars=None, evaluate=lambda log, bars: log)
        self.assertEqual(len(merged), 1)

    def test_missing_remote_just_evaluates_ours(self):
        ours = self.frame([["x", "pending", None]])
        seen = []
        fu.merge_ledger_logs(None, ours, bars="B", evaluate=lambda log, bars: seen.append((list(log.record_id), bars)) or log)
        self.assertEqual(seen, [(["x"], "B")])

    def test_the_result_is_rescored(self):
        calls = []
        fu.merge_ledger_logs(self.frame([["a", "pending", None]]), self.frame([["b", "pending", None]]), bars="B",
                             evaluate=lambda log, bars: calls.append(bars) or log)
        self.assertEqual(calls, ["B"])


class EmbeddedPublisherTests(unittest.TestCase):
    """노트북은 tools/github_pages.py 를 헬퍼 셀에 그대로 넣어 모듈로 쓴다(발행 묶기 ④, 2026-09-24).

    예전에는 노트북에 따로 만든 github_put·github_put_expected 가 있었고 재시도 규칙이 도구와 달랐다.
    """

    def test_the_cell_is_exactly_the_tool_source(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import sync_notebook_helpers as sync
        cell = next("".join(c["source"]) for c in NB["cells"] if "github_pages" in c.get("metadata", {}).get("tags", []))
        self.assertEqual(cell, sync.helper_source("github_pages"), "tools/sync_notebook_helpers.py 를 다시 돌리세요")

    def load(self):
        """헬퍼 셀과 원장 셀의 github_put·github_put_expected 를 노트북처럼 한 이름공간에서 실행한다."""
        cell = next("".join(c["source"]) for c in NB["cells"] if "github_pages" in c.get("metadata", {}).get("tags", []))
        namespace = {}
        exec(cell, namespace)
        defs = SYNC_CELL[SYNC_CELL.index("def github_put("):SYNC_CELL.index("# ---- 참고자료 보관본 갱신")]
        exec(defs, namespace)
        return namespace

    def test_ledger_and_derived_files_become_one_commit_and_other_rows_survive(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_publish_batch import FakeRepo
        ns = self.load()
        gp = ns["github_pages"]
        repo = FakeRepo({"forecast_history/samsung/forecast_log.csv": "id\na\n"})
        read_sha = gp.blob_sha("id\na\n")
        repo.push_other({"forecast_history/samsung/forecast_log.csv": "id\na\nother\n"})   # 읽은 뒤 다른 실행
        derived = {"text": "before"}

        def merge(latest):
            derived["text"] = "after-merge"
            return latest + "ours\n"
        with patch.object(gp, "_repo_api", repo.api), patch("time.sleep"):
            with gp.batch("data: samsung 원장 (r)", "t"):
                ns["github_put_expected"]("forecast_history/samsung/forecast_log.csv", "id\na\nours\n", "t", "m",
                                          read_sha, merge)
                ns["github_put"]("forecast_history/samsung/daily_forecast_comparison.csv",
                                 lambda: derived["text"], "t", "m")
        files = repo.files()
        self.assertEqual(files["forecast_history/samsung/forecast_log.csv"], "id\na\nother\nours\n")
        self.assertEqual(files["forecast_history/samsung/daily_forecast_comparison.csv"], "after-merge")
        self.assertEqual(repo.count("POST", "git/commits"), 1)


class SyncWiringTests(unittest.TestCase):
    def test_ledger_is_written_with_the_sha_read_just_before_and_merged_by_the_shared_rule(self):
        self.assertIn('remote_text, remote_sha = github_get(f"{GITHUB_LEDGER_DIR}/forecast_log.csv", token)', SYNC_CELL)
        self.assertIn("merge_ledger_logs(", SYNC_CELL)
        self.assertIn("github_put_expected(", SYNC_CELL)
        self.assertIn("remote_sha, _merge_ledger_with)", SYNC_CELL)
        self.assertNotIn('drop_duplicates("record_id", keep="last")', SYNC_CELL)

    def test_ledger_report_and_caches_are_each_one_commit(self):
        ledger = SYNC_CELL[SYNC_CELL.index('with github_pages.batch(f"data: {TARGET} 원장 ({RUN_ID})", token):'):]
        self.assertLess(ledger.index("for name in LEDGER_FILES:"), ledger.index("post_open_grid.csv"))
        self.assertIn("lambda name=name: (STORAGE_ROOT / name).read_text", ledger, "파생 파일은 합친 원장 뒤에 읽는다")
        self.assertIn('with github_pages.batch(f"macro: 보관본 ({RUNTIME} {RUN_ID})", _cache_token):', SYNC_CELL)
        report = next("".join(c["source"]) for c in NB["cells"] if "GitHub Pages 발행: {_path}" in "".join(c["source"]))
        self.assertIn('with github_pages.batch(f"report: {prediction_date.date()} ({RUN_ID})", _token):', report)
        # 노트북에 자기 PUT 사본이 남지 않는다.
        for cell in NB["cells"]:
            self.assertNotIn('method="PUT"', "".join(cell["source"]))

    def test_the_afternoon_tool_uses_the_same_merge_rule(self):
        source = (ROOT / "tools" / "build_afternoon_update.py").read_text(encoding="utf-8")
        self.assertIn("merge_ledger_logs(latest, ours, bars)", source)


if __name__ == "__main__":
    unittest.main()
