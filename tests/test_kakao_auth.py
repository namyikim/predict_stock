"""카카오 인증을 실제 Worker·SQLite로 검증한다. 외부 카카오 API만 대체한다."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKERD = os.environ.get('WORKERD_BIN') or shutil.which('workerd')


class KakaoAuthTests(unittest.TestCase):
    @unittest.skipUnless(WORKERD, 'workerd 실행 파일이 있을 때 Cloudflare 런타임도 확인한다')
    def test_cloudflare_request_compatibility(self):
        source = (ROOT / 'counter/worker.js').read_text()
        helper = 'async function kakaoCall(' + source.split('async function kakaoCall(', 1)[1].split('\nasync function kakaoIdentity', 1)[0]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'case.js').write_text(helper + '\n' + (ROOT / 'tests/kakao_runtime_cases.mjs').read_text())
            (path / 'provider.js').write_text("export default {async fetch(request){const status=Number(request.headers.get('X-Test-Status')||200);return status===200?Response.json({ok:true}):new Response('응답 본문을 읽지 않는다',{status,headers:{Location:'https://unexpected.example'}});}};")
            (path / 'test.capnp').write_text('''using Workerd = import "/workerd/workerd.capnp";
const config :Workerd.Config = (services = [
  (name = "test", worker = (modules = [(name = "case.js", esModule = embed "case.js")], compatibilityDate = "2026-10-07", bindings = [(name = "PROVIDER", service = "provider")])),
  (name = "provider", worker = (modules = [(name = "provider.js", esModule = embed "provider.js")], compatibilityDate = "2026-10-07"))
]);''')
            result = subprocess.run([WORKERD, 'test', str(path / 'test.capnp')], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr[-7000:])

    def test_worker_authentication_lifecycle(self):
        result = subprocess.run(['node', 'tests/kakao_auth_cases.mjs'], cwd=ROOT,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr[-7000:])

    def test_administrator_connection_actions(self):
        result = subprocess.run(['node', 'tests/admin_kakao_cases.mjs'], cwd=ROOT,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
