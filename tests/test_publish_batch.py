# -*- coding: utf-8 -*-
"""발행 묶기 ①(2026-09-23): 도구 한 번의 발행을 한 커밋으로. guides/publish-batching-plan.md 의 안전 조건을 고정한다.

가짜 GitHub 저장소(ref·커밋·트리·blob)를 메모리에 두고 github_pages._repo_api 를 바꿔 끼워, 실제 API 와 같은
순서(ref → 커밋 → blob → 트리 → 커밋 → ref 갱신)로 돌린다.
"""
import base64
import hashlib
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import github_pages as gp  # noqa: E402


class HttpError(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


def sha_of(data):
    return hashlib.sha1(data).hexdigest()


class FakeRepo:
    """아주 작은 git: 트리는 {경로: blob sha} 평면 사전."""

    def __init__(self, files):
        self.blobs, self.trees, self.commits = {}, {}, {}
        self.calls = []
        self.before_ref_update = None       # ref 갱신 직전에 끼어들 동작(다른 실행 흉내)
        self.drop_ref_response = False      # ref 는 바꾸고 응답만 잃는다
        tree = self._tree({path: self._blob(text.encode()) for path, text in files.items()})
        self.head = self._commit(tree, [], "init")

    def _blob(self, data):
        sha = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        self.blobs[sha] = data
        return sha

    def _tree(self, entries):
        sha = sha_of(repr(sorted(entries.items())).encode())
        self.trees[sha] = dict(entries)
        return sha

    def _commit(self, tree, parents, message):
        sha = sha_of(f"{tree}{parents}{message}{len(self.commits)}".encode())
        self.commits[sha] = {"tree": tree, "parents": parents, "message": message}
        return sha

    def files(self, commit=None):
        tree = self.trees[self.commits[commit or self.head]["tree"]]
        return {path: self.blobs[sha].decode() for path, sha in tree.items()}

    def push_other(self, changes, message="other run"):
        """다른 실행이 main 에 커밋한 것처럼."""
        tree = dict(self.trees[self.commits[self.head]["tree"]])
        tree.update({path: self._blob(text.encode()) for path, text in changes.items()})
        self.head = self._commit(self._tree(tree), [self.head], message)

    def api(self, suffix, tok, method="GET", body=None):
        self.calls.append((method, suffix.split("?")[0]))
        if suffix == "git/ref/heads/main":
            return {"object": {"sha": self.head}}
        if suffix.startswith("git/commits/") and method == "GET":
            return {"tree": {"sha": self.commits[suffix.rsplit("/", 1)[1]]["tree"]}}
        if suffix.startswith("contents/"):
            path, ref = suffix[len("contents/"):].split("?ref=")
            tree = self.trees[self.commits[ref]["tree"]]
            path = urllib.parse.unquote(path)
            if path not in tree:
                raise HttpError(404)
            return {"sha": tree[path]}
        if suffix.startswith("git/blobs/"):
            return {"content": base64.b64encode(self.blobs[suffix.rsplit("/", 1)[1]]).decode()}
        if suffix == "git/blobs":
            return {"sha": self._blob(base64.b64decode(body["content"]))}
        if suffix == "git/trees":
            tree = dict(self.trees[body["base_tree"]])
            tree.update({e["path"]: e["sha"] for e in body["tree"]})
            return {"sha": self._tree(tree)}
        if suffix == "git/commits":
            return {"sha": self._commit(body["tree"], body["parents"], body["message"])}
        if suffix == "git/refs/heads/main" and method == "PATCH":
            if self.before_ref_update:
                action, self.before_ref_update = self.before_ref_update, None
                action()
            if self.commits[body["sha"]]["parents"] != [self.head]:
                raise HttpError(422)                    # fast-forward 가 아니다
            self.head = body["sha"]
            if self.drop_ref_response:
                self.drop_ref_response = False
                raise OSError("timeout")               # 반영은 됐는데 응답만 잃었다(code 없음)
            return {"object": {"sha": self.head}}
        if suffix.startswith("compare/"):
            base = suffix[len("compare/"):].split("...")[0]
            seen, cursor = set(), self.head
            while cursor:
                seen.add(cursor)
                parents = self.commits[cursor]["parents"]
                cursor = parents[0] if parents else None
            return {"status": "identical" if base == self.head else ("ahead" if base in seen else "diverged")}
        raise AssertionError(f"예상하지 못한 호출 {method} {suffix}")

    def count(self, method, suffix):
        return sum(1 for m, s in self.calls if m == method and s == suffix)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.repo = FakeRepo({"docs/a.html": "A0", "docs/b.json": "B0", "macro_history/x.csv": "X0"})
        self.patches = [patch.object(gp, "_repo_api", self.repo.api), patch("time.sleep")]
        for p in self.patches:
            p.start()
        self.legacy = patch.object(gp, "_api", side_effect=AssertionError("묶음 안에서 파일별 커밋을 만들면 안 된다"))
        self.legacy.start()

    def tearDown(self):
        self.legacy.stop()
        for p in self.patches:
            p.stop()

    def test_many_publishes_become_one_commit(self):
        start = self.repo.head
        with gp.batch("earnings: samsung 2026Q3"):
            self.assertEqual(gp.publish("docs/a.html", "A1", "tok", "a"), gp.PENDING)
            gp.publish("docs/b.json", "B1", "tok", "b")
            gp.publish("docs/new.html", "N1", "tok", "new")
        self.assertEqual(self.repo.commits[self.repo.head]["parents"], [start], "커밋은 하나")
        self.assertEqual(self.repo.files(), {"docs/a.html": "A1", "docs/b.json": "B1", "docs/new.html": "N1",
                                             "macro_history/x.csv": "X0"})
        message = self.repo.commits[self.repo.head]["message"]
        self.assertTrue(message.startswith("earnings: samsung 2026Q3\n\n"))
        self.assertIn("- docs/new.html — new", message)

    def test_same_path_twice_keeps_the_last(self):
        with gp.batch("m"):
            gp.publish("docs/a.html", "first", "tok", "a")
            gp.publish("docs/a.html", "second", "tok", "a")
        self.assertEqual(self.repo.files()["docs/a.html"], "second")

    def test_unchanged_files_are_skipped_and_an_empty_batch_makes_no_commit(self):
        start = self.repo.head
        with tempfile.TemporaryDirectory() as d:
            flag = os.path.join(d, "changed.flag")
            with patch.dict(os.environ, {"PAGES_CHANGED_FLAG": flag}):
                with gp.batch("m") as b:
                    gp.publish("docs/a.html", "A0", "tok", "same")
                self.assertIsNone(b.commit_sha)
                self.assertFalse(os.path.exists(flag), "커밋이 없으면 Pages 재빌드 표시도 없다")
                with gp.batch("m"):
                    gp.publish("docs/a.html", "A0", "tok", "same")
                    gp.publish("docs/b.json", "B9", "tok", "changed")
                self.assertTrue(os.path.exists(flag))
        self.assertEqual(self.repo.count("POST", "git/blobs"), 1, "같은 파일은 blob 도 만들지 않는다")
        self.assertEqual(self.repo.commits[self.repo.head]["parents"], [start])
        self.assertNotIn("docs/a.html —", self.repo.commits[self.repo.head]["message"])

    def test_exception_inside_the_block_commits_nothing(self):
        start = self.repo.head
        with self.assertRaises(ValueError):
            with gp.batch("m"):
                gp.publish("docs/a.html", "half", "tok", "a")
                raise ValueError("보고서 생성 실패")
        self.assertEqual(self.repo.head, start)
        self.assertEqual(self.repo.count("POST", "git/blobs"), 0)

    def test_main_moved_by_another_run_is_retried_and_their_change_is_kept(self):
        self.repo.before_ref_update = lambda: self.repo.push_other({"macro_history/x.csv": "X-other"})
        with gp.batch("m"):
            gp.publish("docs/a.html", "A1", "tok", "a")
        files = self.repo.files()
        self.assertEqual(files["docs/a.html"], "A1")
        self.assertEqual(files["macro_history/x.csv"], "X-other", "다른 실행이 바꾼 파일을 되돌리지 않는다")
        self.assertEqual(self.repo.count("PATCH", "git/refs/heads/main"), 2)

    def test_read_modify_write_file_is_merged_when_it_changed(self):
        read_sha = gp.blob_sha("L0\n")
        self.repo.push_other({"ledger.csv": "L0\n"})
        self.repo.push_other({"ledger.csv": "L0\nother\n"})     # 우리가 읽은 뒤 다른 실행이 한 줄 더했다
        with gp.batch("m"):
            gp.publish("ledger.csv", "L0\nours\n", "tok", "ledger", expected_sha=read_sha,
                       merge=lambda latest: latest + "ours\n")
        self.assertEqual(self.repo.files()["ledger.csv"], "L0\nother\nours\n")

    def test_read_modify_write_file_without_merge_stops_the_whole_batch(self):
        self.repo.push_other({"ledger.csv": "L0\nother\n"})
        start = self.repo.head
        with self.assertRaises(RuntimeError) as caught:
            with gp.batch("m"):
                gp.publish("docs/a.html", "A1", "tok", "a")
                gp.publish("ledger.csv", "L0\nours\n", "tok", "ledger", expected_sha=gp.blob_sha("L0\n"))
        self.assertIn("동시 변경 감지", str(caught.exception))
        self.assertEqual(self.repo.head, start, "덮어쓰지 않고 아무것도 올리지 않는다")

    def test_expected_new_file_that_appeared_is_not_overwritten(self):
        self.repo.push_other({"ledger.csv": "someone else"})
        with self.assertRaises(RuntimeError):
            with gp.batch("m"):
                gp.publish("ledger.csv", "ours", "tok", "ledger", expected_sha=None)

    def test_lost_ref_response_is_not_committed_twice(self):
        self.repo.drop_ref_response = True
        start = self.repo.head
        with gp.batch("m") as b:
            gp.publish("docs/a.html", "A1", "tok", "a")
        self.assertEqual(self.repo.commits[self.repo.head]["parents"], [start])
        self.assertEqual(b.commit_sha, self.repo.head)
        self.assertEqual(self.repo.count("POST", "git/commits"), 1, "응답을 잃었다고 커밋을 또 만들지 않는다")

    def test_rejection_without_contention_is_an_error_not_a_retry(self):
        def refuse(suffix, tok, method="GET", body=None):
            if suffix == "git/refs/heads/main" and method == "PATCH":
                raise HttpError(422)                    # main 은 그대로인데 거절 — 경합이 아니다
            return self.repo.api(suffix, tok, method, body)
        with patch.object(gp, "_repo_api", refuse):
            with self.assertRaises(RuntimeError) as caught:
                with gp.batch("m"):
                    gp.publish("docs/a.html", "A1", "secret-token", "a")
        self.assertIn("422", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))

    def test_blob_sha_matches_git(self):
        self.assertEqual(gp.blob_sha("hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")   # git hash-object

    def test_publish_outside_a_batch_is_unchanged(self):
        self.legacy.stop()
        calls = []

        def fake(path, tok, method="GET", body=None):
            calls.append(method)
            return {"sha": "s1"} if method == "GET" else {"content": {"sha": "abcdef1234"}}
        with patch.object(gp, "_api", fake):
            self.assertEqual(gp.publish("docs/a.html", "A1", "tok", "a"), "abcdef1")
        self.assertEqual(calls, ["GET", "PUT"])
        self.legacy.start()


class ToolWiringTests(unittest.TestCase):
    """①단계 대상 세 도구가 발행을 한 묶음으로 감싸는지."""

    def source(self, name):
        return (ROOT / "tools" / name).read_text(encoding="utf-8")

    def test_earnings_publishes_in_one_batch_and_protects_its_ledger(self):
        src = self.source("build_earnings_forecast.py")
        block = src[src.index("with github_pages.batch(f\"earnings:"):]
        # 공용 이력 보관본은 publish_history(날짜로 합쳐 올림, 2026-09-24)도 같은 묶음에 들어간다.
        self.assertGreaterEqual(block.count("github_pages.publish(") + block.count("github_pages.publish_history("), 9)
        self.assertIn("remote, ledger_sha = github_pages.fetch_with_sha(ledger_name, token)", src)
        self.assertIn("expected_sha=ledger_sha", src)
        self.assertIn("expected_sha=vintage_sha", src)

    def test_longterm_publishes_in_one_batch(self):
        src = self.source("build_longterm_report.py")
        block = src[src.index("with github_pages.batch(f\"longterm:"):]
        self.assertGreaterEqual(block.count("github_pages.publish(") + block.count("github_pages.publish_history("), 3)

    def test_tab_refresh_uses_the_shared_publisher_inside_a_batch(self):
        src = self.source("refresh_longterm_tab.py")
        self.assertIn("with github_pages.batch(", src)
        self.assertIn("expected_sha=sha, merge=apply", src)
        self.assertNotIn("github_pages._api(path, token, 'PUT'", src)

    def test_workflow_rebuilds_pages_only_after_a_commit(self):
        import yaml
        wf = yaml.safe_load((ROOT / ".github" / "workflows" / "monthly-longterm.yml").read_text(encoding="utf-8"))
        job = wf["jobs"]["longterm"]
        self.assertEqual(job["env"]["PAGES_CHANGED_FLAG"], "runs/pages_changed.flag")
        pages = next(s for s in job["steps"] if "Pages" in (s.get("name") or ""))
        self.assertIn('if [ ! -f "$PAGES_CHANGED_FLAG" ]', pages["run"])


if __name__ == "__main__":
    unittest.main()
