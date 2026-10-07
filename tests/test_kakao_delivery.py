"""실제 Worker/SQLite 기반 카카오 보고서 발송 회귀 검사."""
from pathlib import Path
import subprocess
import unittest
ROOT = Path(__file__).resolve().parents[1]
class KakaoDeliveryTests(unittest.TestCase):
    def test_delivery_lifecycle(self):
        result = subprocess.run(['node', 'tests/kakao_delivery_cases.mjs'], cwd=ROOT, text=True, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-7000:])

    def test_administrator_delivery_actions(self):
        result = subprocess.run(['node', 'tests/admin_kakao_delivery_cases.mjs'], cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr[-5000:])
