# -*- coding: utf-8 -*-
"""보고서 도구들의 GitHub 발행 경로.

여러 워크플로가 같은 브랜치에 동시에 커밋한다. sha 를 읽고 쓰는 사이에 다른 잡이 끼어들면
Contents API 가 409 를 준다. 공용 발행기는 sha 를 다시 읽어 재시도하지만, 도구마다 자기
사본을 갖고 있으면 그 사본에는 재시도가 없어 그대로 죽는다 — 2026-09-12 금리 보고서가
그렇게 죽었다. 그래서 사본을 두지 않는다는 것까지 테스트로 고정한다.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import github_pages  # noqa: E402

TOOLS = ("build_interest_report.py", "build_trends_report.py", "build_ai_news_report.py",
         "build_china_report.py", "build_metals_report.py")


class HttpError(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


class DelegationTests(unittest.TestCase):
    def sources(self):
        for name in TOOLS:
            path = ROOT / "tools" / name
            if path.exists():
                yield name, path.read_text(encoding="utf-8")

    def test_no_tool_keeps_its_own_retryless_publisher(self):
        for name, text in self.sources():
            with self.subTest(tool=name):
                if "def publish(" not in text:
                    continue                      # 발행하지 않는 도구
                self.assertIn("github_pages", text, f"{name} 이 공용 발행기를 쓰지 않는다")
                self.assertNotIn('_api(path, token, "PUT", body)', text,
                                 f"{name} 에 재시도 없는 PUT 사본이 남아 있다")

    def test_the_shared_publisher_is_the_only_one_that_talks_to_the_api(self):
        for name, text in self.sources():
            with self.subTest(tool=name):
                self.assertNotIn("def _api(", text, f"{name} 의 API 사본은 공용으로 옮겨야 한다")


class RetryTests(unittest.TestCase):
    """실제 재시도 동작. 409 한 번은 넘기고, 끝까지 409 면 토큰 없는 문구로 알린다."""

    def test_conflict_is_retried_after_reading_the_sha_again(self):
        calls = []

        def fake(path, tok, method="GET", body=None):
            calls.append(method)
            if method == "GET":
                return {"sha": f"sha{calls.count('GET')}"}
            if calls.count("PUT") == 1:
                raise HttpError(409)
            return {"content": {"sha": "abcdef1234"}}

        with patch.object(github_pages, "_api", fake), patch("time.sleep"):
            self.assertEqual(github_pages.publish("docs/x.html", "hi", "tok", "msg"), "abcdef1")
        self.assertEqual(calls.count("PUT"), 2, "충돌 뒤 한 번 더 시도해야 한다")
        self.assertEqual(calls.count("GET"), 2, "재시도 전에 sha 를 다시 읽어야 한다")

    def test_repeated_conflicts_fail_without_leaking_the_token(self):
        def always_conflict(path, tok, method="GET", body=None):
            if method == "GET":
                return {"sha": "s"}
            raise HttpError(409)

        with patch.object(github_pages, "_api", always_conflict), patch("time.sleep"):
            with self.assertRaises(RuntimeError) as caught:
                github_pages.publish("docs/x.html", "hi", "secret-token", "msg")
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertIn("409", str(caught.exception))

    def test_a_missing_file_is_created_rather_than_treated_as_an_error(self):
        def missing_then_put(path, tok, method="GET", body=None):
            if method == "GET":
                raise HttpError(404)
            self.assertNotIn("sha", body, "새 파일에는 sha 를 보내면 안 된다")
            return {"content": {"sha": "1234567890"}}

        with patch.object(github_pages, "_api", missing_then_put):
            self.assertEqual(github_pages.publish("docs/new.html", "hi", "tok", "msg"), "1234567")


class ExpectedShaTests(unittest.TestCase):
    """원장처럼 행을 더하는 파일은 처음 읽은 sha 로만 저장한다(2026-09-23).

    예전 발행기는 저장 직전에 최신 sha 를 읽어 덮어써서, 이 실행이 읽은 뒤 다른 실행이 더한 행을 오류 없이 지웠다.
    """

    def remote(self, text, sha):
        import base64
        return {"content": base64.b64encode(text.encode()).decode(), "sha": sha}

    def test_unchanged_file_is_written_once_with_the_sha_we_read(self):
        calls = []

        def fake(path, tok, method="GET", body=None):
            calls.append((method, (body or {}).get("sha")))
            return {"content": {"sha": "newblob123"}}

        with patch.object(github_pages, "_api", fake):
            self.assertEqual(github_pages.publish("l.csv", "ours", "tok", "m", expected_sha="s1"), "newblob")
        self.assertEqual(calls, [("PUT", "s1")], "처음 읽은 sha 로 곧바로 쓰고, 그 전에 sha 를 새로 읽지 않는다")

    def test_changed_file_is_merged_not_overwritten(self):
        state = {"remote": ("a\nb\n", "s2"), "puts": []}      # 우리가 s1 을 읽은 뒤 누군가 b 를 더했다

        def fake(path, tok, method="GET", body=None):
            if method == "GET":
                return self.remote(*state["remote"])
            state["puts"].append(body)
            if body.get("sha") != state["remote"][1]:
                raise HttpError(409)
            return {"content": {"sha": "merged1234"}}

        merged_from = []

        def merge(latest):
            merged_from.append(latest)
            return latest + "ours\n"

        with patch.object(github_pages, "_api", fake), patch("time.sleep"):
            github_pages.publish("l.csv", "a\nours\n", "tok", "m", expected_sha="s1", merge=merge)
        self.assertEqual(merged_from, ["a\nb\n"], "최신 내용을 합치기 함수에 넘긴다")
        self.assertEqual([p.get("sha") for p in state["puts"]], ["s1", "s2"])
        import base64
        self.assertEqual(base64.b64decode(state["puts"][-1]["content"]).decode(), "a\nb\nours\n")

    def test_without_merge_a_changed_file_is_left_alone(self):
        puts = []

        def fake(path, tok, method="GET", body=None):
            if method == "GET":
                return self.remote("theirs", "s2")
            puts.append(body)
            raise HttpError(409)

        with patch.object(github_pages, "_api", fake), patch("time.sleep"):
            with self.assertRaises(RuntimeError) as caught:
                github_pages.publish("l.csv", "ours", "secret-token", "m", expected_sha="s1")
        self.assertEqual(len(puts), 1, "덮어쓰려고 다시 시도하지 않는다")
        self.assertIn("동시 변경", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))

    def test_a_422_with_the_same_sha_is_not_treated_as_contention(self):
        merged = []

        def fake(path, tok, method="GET", body=None):
            if method == "GET":
                return self.remote("same", "s1")
            raise HttpError(422)

        with patch.object(github_pages, "_api", fake), patch("time.sleep"):
            with self.assertRaises(RuntimeError) as caught:
                github_pages.publish("l.csv", "ours", "tok", "m", expected_sha="s1", merge=merged.append)
        self.assertEqual(merged, [], "파일이 그대로면 합치지 않는다")
        self.assertIn("경합이 아님", str(caught.exception))

    def test_a_file_that_did_not_exist_is_created_without_a_sha(self):
        def fake(path, tok, method="GET", body=None):
            self.assertNotIn("sha", body)
            return {"content": {"sha": "created123"}}

        with patch.object(github_pages, "_api", fake):
            self.assertEqual(github_pages.publish("new.csv", "x", "tok", "m", expected_sha=None), "created")

    def test_fetch_with_sha(self):
        with patch.object(github_pages, "_api", lambda path, tok, method="GET", body=None: self.remote("hi", "s9")):
            self.assertEqual(github_pages.fetch_with_sha("x", "tok"), ("hi", "s9"))

        def missing(path, tok, method="GET", body=None):
            raise HttpError(404)

        with patch.object(github_pages, "_api", missing):
            self.assertEqual(github_pages.fetch_with_sha("x", "tok"), (None, None))

    def test_docstring_no_longer_claims_a_commit_sha_or_that_overwriting_is_safe(self):
        doc = github_pages.publish.__doc__
        self.assertIn("blob sha", doc)
        self.assertNotIn("재시도가 안전하다.", doc.replace("재시도가 안전하다\"", ""))


if __name__ == "__main__":
    unittest.main()
