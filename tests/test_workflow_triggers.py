"""워크플로 정리(2026-09-30) 뒤 지켜야 할 것."""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FLOWS = ROOT / ".github" / "workflows"


def load(name):
    data = yaml.safe_load((FLOWS / name).read_text(encoding="utf-8"))
    return data.get(True) or data.get("on")


class WorkflowTriggerTests(unittest.TestCase):
    def test_tests_skip_pushes_the_daily_report_validates(self):
        """일일 보고서 validation 이 tests.yml 을 같은 동시성 그룹에서 부르므로, 그 경로만 바뀐 push 에서
        tests.yml 이 따로 돌면 매번 취소된다. 반대로 tests.yml 이 무시하는 경로는 일일 보고서가 반드시 돌아야 한다."""
        base = {"docs/**", "forecast_history/**", "macro_history/**", "runs/**", "**.md"}
        ignored = set(load("tests.yml")["push"]["paths-ignore"]) - base
        daily = set(load("daily-report.yml")["push"]["paths"])
        self.assertEqual(ignored, daily)

    def test_no_explicit_pages_rebuild_requests(self):
        # 커밋마다 Pages 빌드가 저절로 돈다. 따로 요청하면 겹쳐서 대부분 취소됐다(9/28~30: 하루 약 300회 중 78%).
        for path in FLOWS.glob("*.yml"):
            self.assertNotIn("pages/builds", path.read_text(encoding="utf-8"), path.name)

    def test_notebook_sync_workflow_is_kept(self):
        # 노트북을 동기화하지 않은 push 를 자동으로 고친다. 없으면 검증이 막혀 보고서가 멈춘다.
        self.assertTrue((FLOWS / "sync-notebook.yml").exists())


if __name__ == "__main__":
    unittest.main()
