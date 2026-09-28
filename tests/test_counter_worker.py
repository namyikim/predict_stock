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
await worker.default.scheduled({ scheduledTime: closeTime, cron: '10 7 * * 1-5' }, { DB, GITHUB_DISPATCH_TOKEN: 'tok-secret' });
await worker.default.scheduled({ scheduledTime: Date.parse('2026-09-10T03:00:00Z'), cron: '0 3 * * *' }, { DB, GH_DISPATCH_TOKEN: 'tok-secret' });
process.stdout.write(JSON.stringify({ withoutToken, fetches, deletes, logs }));
""")
        self.assertEqual(result["withoutToken"], 0, "토큰이 없으면 GitHub를 부르지 않는다")
        self.assertEqual(len(result["fetches"]), 2, "GH_ / GITHUB_ 어느 이름이든 읽는다")
        call = result["fetches"][0]
        self.assertTrue(call["url"].endswith("/repos/namyikim/predict_stock/actions/workflows/afternoon-report.yml/dispatches"))
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["auth"], "Bearer tok-secret")
        self.assertTrue(call["ua"])
        self.assertEqual(call["body"], {"ref": "main", "inputs": {"caller": "cloudflare-cron"}})
        hits = [sql for sql in result["deletes"] if "DELETE FROM hits" in sql]
        self.assertEqual(len(hits), 1, "회차 시각이 아닌 트리거만 보관기간 정리를 한다")
        self.assertTrue(any("DELETE FROM subscribe_log" in sql for sql in result["deletes"]))
        self.assertFalse(any("FROM subscribers" in sql for sql in result["deletes"]), "구독자 목록은 정리하지 않는다")
        self.assertFalse(any("tok-secret" in line for line in result["logs"]), "로그에 토큰이 남으면 안 된다")


class SubscribeTests(unittest.TestCase):
    """종목 보고서 상단 구독(2026-09-28): 허용 출처·형식만 받고, 응답으로 가입 여부를 드러내지 않는다."""

    def run_node(self, body):
        script = r"""
import fs from 'node:fs';
const source = fs.readFileSync('counter/worker.js');
const worker = await import('data:text/javascript;base64,' + source.toString('base64'));
const rows = new Map();
const log = [];
const stmt = (sql, args) => ({
  sql, args,
  first: async () => sql.includes('subscribe_log') ? { n: log.length } : null,
  all: async () => ({ results: [...rows.values()] }),
  run: async () => apply(sql, args),
});
function apply(sql, args) {
  if (sql.startsWith('INSERT INTO subscribers')) { const k = args[0] + '|' + args[1]; if (!rows.has(k)) rows.set(k, {email: args[0], page: args[1], ts: args[2]}); }
  if (sql.startsWith('DELETE FROM subscribers')) rows.delete(args[0] + '|' + args[1]);
  if (sql.startsWith('INSERT INTO subscribe_log')) log.push(args);
}
const DB = {
  prepare: (sql) => ({ bind: (...args) => stmt(sql, args), ...stmt(sql, []) }),
  batch: async (list) => { list.forEach((s) => apply(s.sql, s.args)); return []; },
};
const env = { DB, VISITOR_SALT: 'salt', STATS_TOKEN: 'admin-tok' };
const origin = 'https://namyikim.github.io';
async function call(path, method, payload, headers = {}) {
  const init = { method, headers: { Origin: origin, 'User-Agent': 'Mozilla/5.0', ...headers } };
  if (payload !== undefined) init.body = JSON.stringify(payload);
  const r = await worker.default.fetch(new Request('https://counter.example' + path, init), env, {});
  return { status: r.status, body: await r.json() };
}
""" + body
        run = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                             text=True, encoding="utf-8", capture_output=True, check=True)
        return json.loads(run.stdout)

    def test_subscribe_list_and_unsubscribe(self):
        result = self.run_node(r"""
const out = {};
out.ok = await call('/subscribe', 'POST', { email: '  Me@Example.COM ', page: 'samsung' });
out.again = await call('/subscribe', 'POST', { email: 'me@example.com', page: 'samsung' });
out.other = await call('/subscribe', 'POST', { email: 'me@example.com', page: 'sk_hynix' });
out.bad = await call('/subscribe', 'POST', { email: 'not-an-email', page: 'samsung' });
out.page = await call('/subscribe', 'POST', { email: 'a@b.co', page: 'china' });
out.bot = await call('/subscribe', 'POST', { email: 'bot@spam.io', page: 'samsung', website: 'x' });
out.foreign = await call('/subscribe', 'POST', { email: 'a@b.co', page: 'samsung' }, { Origin: 'https://evil.example' });
out.noToken = await call('/subscribers', 'GET');
out.list = await call('/subscribers', 'GET', undefined, { Authorization: 'Bearer admin-tok' });
out.unsub = await call('/unsubscribe', 'POST', { email: 'me@example.com', page: 'sk_hynix' });
out.unknownUnsub = await call('/unsubscribe', 'POST', { email: 'nobody@example.com', page: 'samsung' });
out.after = await call('/subscribers', 'GET', undefined, { Authorization: 'Bearer admin-tok' });
process.stdout.write(JSON.stringify(out));
""")
        self.assertEqual(result["ok"], {"status": 200, "body": {"ok": True}})
        self.assertEqual(result["again"], result["ok"], "이미 있는 주소도 같은 응답 — 가입 여부를 드러내지 않는다")
        self.assertEqual(result["unknownUnsub"], result["ok"])
        self.assertEqual(result["bad"]["status"], 400)
        self.assertEqual(result["page"]["status"], 400)
        self.assertEqual(result["foreign"]["status"], 403)
        self.assertEqual(result["noToken"]["status"], 401)
        emails = sorted((r["email"], r["page"]) for r in result["list"]["body"]["subscribers"])
        self.assertEqual(emails, [("me@example.com", "samsung"), ("me@example.com", "sk_hynix")],
                         "소문자로 맞춰 한 건만, 봇 칸이 채워진 신청은 저장하지 않는다")
        after = [(r["email"], r["page"]) for r in result["after"]["body"]["subscribers"]]
        self.assertEqual(after, [("me@example.com", "samsung")])

    def test_daily_limit_per_visitor(self):
        result = self.run_node(r"""
const codes = [];
for (let i = 0; i < 12; i++) codes.push((await call('/subscribe', 'POST', { email: `u${i}@example.com`, page: 'samsung' })).status);
process.stdout.write(JSON.stringify(codes));
""")
        self.assertEqual(result[:10], [200] * 10)
        self.assertEqual(result[10:], [429, 429])


if __name__ == "__main__":
    unittest.main()
