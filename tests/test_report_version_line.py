"""보고서 상단의 '생성 시각 · 코드 커밋' 표시."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import github_pages as gp  # noqa: E402


class VersionLineTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("GITHUB_SHA")
        os.environ["GITHUB_SHA"] = "0123456789abcdef0123456789abcdef01234567"
        # 네트워크에 나가지 않도록 커밋 조회를 막는다(메시지·시각은 없이 sha만 남는 경로).
        self._urlopen = gp.__dict__.get("urlopen")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("GITHUB_SHA", None)
        else:
            os.environ["GITHUB_SHA"] = self._saved

    def test_uses_the_actions_sha_and_links_to_the_commit(self):
        info = gp.code_version(tok=None)
        self.assertEqual(info["short"], "0123456")
        self.assertIn("/commit/", info["url"])

    def test_line_contains_generation_time_and_commit(self):
        line = gp.version_line(generated_at="2026-09-08 07:03 KST")
        self.assertIn("2026-09-08 07:03 KST", line)
        self.assertIn("0123456", line)
        self.assertIn("코드 커밋", line)

    def test_line_survives_without_any_commit_information(self):
        os.environ.pop("GITHUB_SHA", None)
        saved = gp._git_head
        gp._git_head = lambda: None
        try:
            line = gp.version_line(generated_at="2026-09-08 07:03 KST")
        finally:
            gp._git_head = saved
        self.assertIn("2026-09-08 07:03 KST", line)


class ReportsUseItTests(unittest.TestCase):
    def test_stock_report_builds_the_line(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        source = "\n".join("".join(c["source"]) for c in nb["cells"])
        self.assertIn("def _code_version():", source)
        self.assertIn("version_html", source)
        self.assertIn('f\'{version_html}\'', source)

    def test_metals_and_china_use_the_shared_helper(self):
        for name in ("build_metals_report.py", "build_china_report.py"):
            text = (ROOT / "tools" / name).read_text(encoding="utf-8")
            self.assertIn("github_pages.version_line(", text, name)


if __name__ == "__main__":
    unittest.main()
