"""아침 종목 보고서 정시 호출(2026-10-02).

GitHub 의 cron 이 아침 회차(06:22~07:52 KST, 11개)를 하나도 만들지 않은 날이 있었다. 보고서는 08:23 에 손으로
돌려서야 나왔다. Cloudflare Worker 가 06:20 KST 에 workflow_dispatch 로 부르고 07:00·07:40 에 다시 부른다.
워크플로는 그 호출을 예약 실행처럼 다뤄야 한다 — 수동 실행처럼 다루면 부를 때마다 예측이 원장에 한 번 더 남는다.
"""
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/daily-report.yml").read_text(encoding="utf-8"))
WORKER = (ROOT / "counter" / "worker.js").read_text(encoding="utf-8")
TIMED = "(github.event_name == 'schedule' || inputs.caller == 'cloudflare-cron')"


class WorkflowTests(unittest.TestCase):
    def test_dispatch_accepts_the_inputs_the_worker_sends(self):
        inputs = WORKFLOW[True]["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["caller"]["default"], "수동")
        self.assertEqual(inputs["retry"]["default"], "false")
        sent = re.search(r'dispatchWorkflow\(env, MORNING_WORKFLOW,\s*\{([^}]*)\}', WORKER).group(1)
        for key in re.findall(r"(\w+):", sent):
            self.assertIn(key, inputs, f"Worker 가 보내는 입력 {key} 를 워크플로가 받지 않으면 호출이 422 로 거절된다")

    def test_timed_call_checks_the_ledger_like_a_scheduled_run(self):
        steps = {s.get("name"): s for s in WORKFLOW["jobs"]["report"]["steps"]}
        for name in ("거래일 달력 설치", "오늘 예측이 이미 기록됐는지 확인"):
            self.assertIn(TIMED, steps[name]["if"], name)
        # 사람이 누른 수동 실행은 검사하지 않는다(의도한 실행이다) — caller 기본값이 '수동'이라 조건에 걸리지 않는다.
        self.assertNotIn("workflow_dispatch", steps["오늘 예측이 이미 기록됐는지 확인"]["if"])

    def test_timed_call_records_the_forecast(self):
        steps = WORKFLOW["jobs"]["report"]["steps"]
        env = next(s["env"] for s in steps if s.get("name") == "보고서 생성·발행")
        self.assertEqual(env["PREDICT_STOCK_RECORD_FORECAST"],
                         "${{ github.event_name == 'push' && 'false' || 'true' }}")

    def test_retry_calls_skip_the_side_reports(self):
        for name in ("ai_news", "trends", "metals", "china", "interest"):
            self.assertTrue(WORKFLOW["jobs"][name]["if"].startswith("inputs.retry != 'true' &&"), name)
        self.assertNotIn("inputs.retry", str(WORKFLOW["jobs"]["report"].get("if")))     # 종목 보고서는 재시도에도 돈다

    def test_run_name_tells_timed_calls_from_manual_ones(self):
        name = WORKFLOW["run-name"]
        self.assertLess(name.index("일일 보고서 · 정시 호출"), name.index("일일 보고서 · 수동"))


class WorkerTests(unittest.TestCase):
    def test_times_are_before_the_market_opens_and_after_the_us_close(self):
        times = re.search(r"MORNING_TIMES_UTC = \[(.*?)\];", WORKER).group(1)
        slots = [(int(h), int(m)) for h, m in re.findall(r"\[(\d+), (\d+)\]", times)]
        kst = [((h + 9) % 24) * 60 + m for h, m in slots]
        self.assertEqual(kst[0], 6 * 60 + 20)                       # 본 호출 06:20 KST
        self.assertTrue(all(6 * 60 <= minute < 8 * 60 for minute in kst), kst)   # 모두 06~08시, 09:00 개장 전
        self.assertEqual(kst, sorted(kst))

    def test_github_cron_stays_as_the_backup(self):
        crons = [item["cron"] for item in WORKFLOW[True]["schedule"]]
        self.assertIn("22 21 * * 0-4", crons)

    def test_readme_lists_the_trigger(self):
        readme = (ROOT / "counter" / "README.md").read_text(encoding="utf-8")
        self.assertIn("*/20 21-22 * * SUN-THU", readme)
        self.assertIn("아침 보고서 정시 호출", readme)


if __name__ == "__main__":
    unittest.main()
