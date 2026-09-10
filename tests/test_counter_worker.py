"""Cloudflare 조회수 Worker의 실제 응답 동작."""
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CounterWorkerTests(unittest.TestCase):
    def test_missing_visitor_salt_returns_clear_server_error(self):
        script = r"""
import fs from 'node:fs';
const source = fs.readFileSync('counter/worker.js');
const worker = await import('data:text/javascript;base64,' + source.toString('base64'));
const request = new Request('https://counter.example/hit?page=samsung', {
  headers: {Origin: 'https://namyikim.github.io', 'User-Agent': 'Mozilla/5.0'}
});
const response = await worker.default.fetch(request, {}, {});
console.log(JSON.stringify({status: response.status, body: await response.json()}));
"""
        run = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                             text=True, encoding="utf-8", capture_output=True, check=True)
        result = json.loads(run.stdout)
        self.assertEqual(result["status"], 500)
        self.assertEqual(result["body"]["error"], "VISITOR_SALT가 설정되지 않았습니다")


class ScoringDispatchTests(unittest.TestCase):
    """채점 워크플로 정시 호출: 회차 시각에만, 토큰이 있을 때만 GitHub를 부른다."""

    def run_node(self, script):
        run = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                             text=True, encoding="utf-8", capture_output=True, check=True)
        return json.loads(run.stdout)

    def test_dispatch_is_due_only_at_the_scoring_times_on_weekdays(self):
        result = self.run_node(r"""
import fs from 'node:fs';
const source = fs.readFileSync('counter/worker.js');
const worker = await import('data:text/javascript;base64,' + source.toString('base64'));
const t = (s) => Date.parse(s);
console.log(JSON.stringify({
  open: worker.dispatchDue(t('2026-09-10T00:37:00Z')),        // 09:37 KST 목요일
  close: worker.dispatchDue(t('2026-09-10T07:10:30Z')),       // 16:10 KST
  slightlyLate: worker.dispatchDue(t('2026-09-10T07:12:00Z')),
  tooLate: worker.dispatchDue(t('2026-09-10T07:14:00Z')),
  retention: worker.dispatchDue(t('2026-09-10T03:00:00Z')),   // 보관기간 정리 cron
  saturday: worker.dispatchDue(t('2026-09-12T07:10:00Z')),
}));
""")
        self.assertEqual(result, {"open": True, "close": True, "slightlyLate": True,
                                  "tooLate": False, "retention": False, "saturday": False})

    def test_scheduled_calls_github_only_with_a_token_and_never_logs_it(self):
        result = self.run_node(r"""
import fs from 'node:fs';
const source = fs.readFileSync('counter/worker.js');
const worker = await import('data:text/javascript;base64,' + source.toString('base64'));
const fetches = [];
globalThis.fetch = async (url, init) => {
  fetches.push({url, method: init.method, auth: init.headers.Authorization, ua: init.headers['User-Agent'],
                body: JSON.parse(init.body)});
  return { status: 204 };
};
const deletes = [];
const DB = { prepare: (sql) => ({ bind: () => ({ run: async () => { deletes.push(sql); } }) }) };
const logs = [];
console.log = (line) => logs.push(String(line));
const closeTime = Date.parse('2026-09-10T07:10:00Z');
await worker.default.scheduled({ scheduledTime: closeTime, cron: '10 7 * * 1-5' }, { DB });
const withoutToken = fetches.length;
await worker.default.scheduled({ scheduledTime: closeTime, cron: '10 7 * * 1-5' }, { DB, GH_DISPATCH_TOKEN: 'tok-secret' });
await worker.default.scheduled({ scheduledTime: Date.parse('2026-09-10T03:00:00Z'), cron: '0 3 * * *' }, { DB, GH_DISPATCH_TOKEN: 'tok-secret' });
process.stdout.write(JSON.stringify({ withoutToken, fetches, deletes, logs }));
""")
        self.assertEqual(result["withoutToken"], 0, "토큰이 없으면 GitHub를 부르지 않는다")
        self.assertEqual(len(result["fetches"]), 1)
        call = result["fetches"][0]
        self.assertTrue(call["url"].endswith("/repos/namyikim/predict_stock/actions/workflows/afternoon-report.yml/dispatches"))
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["auth"], "Bearer tok-secret")
        self.assertTrue(call["ua"])
        self.assertEqual(call["body"], {"ref": "main", "inputs": {"caller": "cloudflare-cron"}})
        self.assertEqual(len(result["deletes"]), 1, "회차 시각이 아닌 트리거만 보관기간 정리를 한다")
        self.assertIn("DELETE FROM hits", result["deletes"][0])
        self.assertFalse(any("tok-secret" in line for line in result["logs"]), "로그에 토큰이 남으면 안 된다")


if __name__ == "__main__":
    unittest.main()
