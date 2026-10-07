"""카카오 인증을 실제 Worker·SQLite로 검증한다. 외부 카카오 API만 대체한다."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class KakaoAuthTests(unittest.TestCase):
    def test_worker_authentication_lifecycle(self):
        result = subprocess.run(['node', 'tests/kakao_auth_cases.mjs'], cwd=ROOT,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr[-7000:])

    def test_administrator_connection_actions(self):
        result = subprocess.run(['node', 'tests/admin_kakao_cases.mjs'], cwd=ROOT,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
