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
                             text=True, capture_output=True, check=True)
        result = json.loads(run.stdout)
        self.assertEqual(result["status"], 500)
        self.assertEqual(result["body"]["error"], "VISITOR_SALT가 설정되지 않았습니다")


if __name__ == "__main__":
    unittest.main()
