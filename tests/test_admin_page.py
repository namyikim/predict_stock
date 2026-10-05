"""관리 페이지(docs/admin) 첫 화면."""
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "docs" / "admin" / "index.html"


class AdminLandingTabTests(unittest.TestCase):
    """처음 들어올 때 항상 방문 통계를 연다(2026-09-30: #subscribers 가 주소에 남아 구독자 탭이 먼저 떴다)."""

    def test_first_load_ignores_the_hash_and_clears_it(self):
        page = PAGE.read_text(encoding="utf-8")
        tail = page[page.index('window.addEventListener("hashchange", route);'):]
        tail = tail[:tail.index("})();")]
        self.assertIn('show("visits");', tail)
        self.assertNotIn("\n  route();", tail)
        self.assertIn('history.replaceState(null, "", location.pathname + location.search)', tail)

    def test_tab_clicks_still_route_by_hash(self):
        page = PAGE.read_text(encoding="utf-8")
        self.assertIn('window.addEventListener("hashchange", route);', page)
        self.assertIn('<a href="#subscribers" data-tab="subscribers">구독자</a>', page)


if __name__ == "__main__":
    unittest.main()


class DispatchTestTests(unittest.TestCase):
    """정시 호출 시험(2026-10-01): 관리자가 버튼 하나로 Worker→GitHub 호출 결과를 본다."""

    def test_worker_has_an_admin_only_test_route(self):
        text = (PAGE.parents[2] / "counter" / "worker.js").read_text(encoding="utf-8")
        self.assertIn('if (url.pathname === "/dispatch/test") return await handleDispatchTest(request, env, origin);', text)
        self.assertIn("if (!isAdmin(request, env)) return json({ error: \"unauthorized\" }, origin, 401);\n  const result = await dispatchScoring(env);", text)

    def test_admin_page_has_the_button(self):
        page = PAGE.read_text(encoding="utf-8")
        self.assertIn('id="dispatch-test"', page)
        self.assertIn('WORKER + "/dispatch/test"', page)
        self.assertIn("토큰에 Actions 쓰기 권한 없음", page)

class AdminMailButtonTests(unittest.TestCase):
    def test_real_script_sends_authenticated_request_and_displays_acceptance_not_delivery(self):
        import subprocess
        run=subprocess.run(['node','tests/admin_mail_cases.mjs'],cwd=PAGE.parents[2],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr[-3000:])
