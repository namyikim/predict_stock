"""이메일 유입의 집계 전용 국가·기기 분류."""
import subprocess
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class EmailDimensionsTests(unittest.TestCase):
    def test_worker_dimensions(self):
        run=subprocess.run(['node','tests/email_dimensions_cases.mjs'],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(run.returncode,0,run.stderr[-3000:])
    def test_admin_email_view(self):
        run=subprocess.run(['node','tests/admin_email_traffic_cases.cjs'],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(run.returncode,0,run.stderr[-3000:])
