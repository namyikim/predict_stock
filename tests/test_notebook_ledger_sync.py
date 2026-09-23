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
                 if "def github_put(path, text, token, message, attempt=0):" in "".join(c["source"]))


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


class GithubPutExpectedTests(unittest.TestCase):
    """노트북의 github_put_expected 를 셀 소스에서 꺼내 가짜 API 로 돌린다."""

    def load(self, github_get):
        body = SYNC_CELL[SYNC_CELL.index("def github_put_expected("):SYNC_CELL.index("# ---- 참고자료 보관본 갱신")]
        namespace = {"json": json, "GITHUB_BRANCH": "main", "GITHUB_REPO": "o/r", "github_get": github_get,
                     "time": type("T", (), {"sleep": staticmethod(lambda s: None)})}
        exec(body, namespace)
        return namespace["github_put_expected"]

    def fake_urlopen(self, responses, bodies):
        class Http(Exception):
            def __init__(self, code):
                super().__init__(code)
                self.code = code

        class Response:
            def __init__(self, sha): self.sha = sha
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps({"content": {"sha": self.sha}}).encode()

        def urlopen(request, timeout=60):
            bodies.append(json.loads(request.data.decode()))
            outcome = responses.pop(0)
            if isinstance(outcome, int):
                raise Http(outcome)
            return Response(outcome)
        return urlopen

    def test_first_attempt_uses_the_sha_we_read_and_does_not_reread(self):
        reads, bodies = [], []
        put = self.load(lambda path, tok: reads.append(path) or ("x", "s9"))
        with patch("urllib.request.urlopen", self.fake_urlopen(["abcdef123"], bodies)):
            self.assertEqual(put("l.csv", "ours", "t", "m", "s1", merge=None), "abcdef1")
        self.assertEqual([b.get("sha") for b in bodies], ["s1"])
        self.assertEqual(reads, [], "성공하면 sha 를 다시 읽지 않는다")

    def test_changed_file_is_merged_and_written_with_the_new_sha(self):
        bodies, merged_from = [], []
        put = self.load(lambda path, tok: ("theirs", "s2"))

        def merge(latest):
            merged_from.append(latest)
            return "theirs+ours"
        with patch("urllib.request.urlopen", self.fake_urlopen([409, "merged1234"], bodies)):
            put("l.csv", "ours", "t", "m", "s1", merge)
        self.assertEqual(merged_from, ["theirs"])
        self.assertEqual([b.get("sha") for b in bodies], ["s1", "s2"])
        import base64
        self.assertEqual(base64.b64decode(bodies[-1]["content"]).decode(), "theirs+ours")

    def test_unchanged_sha_after_422_is_not_contention(self):
        bodies = []
        put = self.load(lambda path, tok: ("same", "s1"))
        with patch("urllib.request.urlopen", self.fake_urlopen([422], bodies)):
            with self.assertRaises(RuntimeError) as caught:
                put("l.csv", "ours", "t", "m", "s1", merge=lambda t: self.fail("합치면 안 된다"))
        self.assertIn("경합이 아님", str(caught.exception))
        self.assertEqual(len(bodies), 1)


class SyncWiringTests(unittest.TestCase):
    def test_ledger_is_written_with_the_sha_read_just_before_and_merged_by_the_shared_rule(self):
        self.assertIn('remote_text, remote_sha = github_get(f"{GITHUB_LEDGER_DIR}/forecast_log.csv", token)', SYNC_CELL)
        self.assertIn("merge_ledger_logs(", SYNC_CELL)
        self.assertIn("github_put_expected(", SYNC_CELL)
        self.assertIn("remote_sha, _merge_ledger_with)", SYNC_CELL)
        self.assertNotIn('drop_duplicates("record_id", keep="last")', SYNC_CELL)

    def test_the_afternoon_tool_uses_the_same_merge_rule(self):
        source = (ROOT / "tools" / "build_afternoon_update.py").read_text(encoding="utf-8")
        self.assertIn("merge_ledger_logs(latest, ours, bars)", source)


if __name__ == "__main__":
    unittest.main()
