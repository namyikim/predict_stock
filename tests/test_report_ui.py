"""공통 화면을 다시 적용해도 원문과 알림 검증 정보가 보존되는지 확인한다."""
import json
import re
import unittest
from pathlib import Path
from tools.report_ui import START, END, apply_simple_ui, simple_document
from tools.sync_notebook_helpers import helper_source

ROOT = Path(__file__).resolve().parents[1]


class SimpleUITests(unittest.TestCase):
    def test_idempotent_and_preserves_published_body(self):
        # 생성 시각·공식 실행 번호는 발송 검증에도 쓰이므로 바뀌면 안 된다.
        for name in ('samsung', 'sk_hynix', 'admin', 'lab', 'news'):
            with self.subTest(page=name):
                original = (ROOT / 'docs' / name / 'index.html').read_text()
                themed = apply_simple_ui(original)
                self.assertEqual(apply_simple_ui(themed), themed)
                self.assertEqual(original.split('<body', 1)[1], themed.split('<body', 1)[1])
                self.assertEqual(themed.count(START), 1)
                self.assertEqual(themed.count(END), 1)

    def test_fragment_is_untouched(self):
        fragment = '<section id="easy-summary">예측 보류 <script>keep()</script></section>'
        self.assertEqual(apply_simple_ui(fragment), fragment)

    def test_generated_document_gets_theme(self):
        @simple_document
        def build(value):
            return '<!doctype html><html><head></head><body>' + value + '</body></html>'
        result = build('2026-10-07 · 공식 예측')
        self.assertIn(START, result)
        self.assertIn('<body>2026-10-07 · 공식 예측</body>', result)

    def test_colab_has_same_standalone_module_and_uses_it(self):
        book = json.loads((ROOT / 'samsung_direction_model_colab.ipynb').read_text())
        cells = book['cells']
        source = next(''.join(c['source']) for c in cells if 'report_ui' in c.get('metadata', {}).get('tags', []))
        self.assertEqual(source, helper_source('report_ui'))
        self.assertIn('_page = report_ui.apply_simple_ui(_page)', ''.join(''.join(c['source']) for c in cells))
        # 모듈 소스의 __main__ 절이 Colab 로딩 때 실행되지 않는지도 확인한다.
        scope = {}
        exec(source, scope)
        self.assertEqual(scope['report_ui'].apply_simple_ui('<head></head>'), apply_simple_ui('<head></head>'))

    def test_refresh_preserves_every_existing_script(self):
        original = (ROOT / 'docs/admin/index.html').read_text()
        without_theme = re.sub(re.escape(START) + '.*?' + re.escape(END), '', original, flags=re.S)
        themed = apply_simple_ui(without_theme)
        restored = re.sub(re.escape(START) + '.*?' + re.escape(END), '', themed, flags=re.S)
        self.assertEqual(restored, without_theme)


if __name__ == '__main__':
    unittest.main()
