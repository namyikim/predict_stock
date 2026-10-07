"""실제 생성기의 카운터 조각을 실행해 메뉴별 연결을 확인한다."""
import ast
import json
from pathlib import Path
import re
import subprocess
import unittest
ROOT=Path(__file__).resolve().parents[1]


def counter_from_source(source, variable, context):
    # 가격 수집/모델을 실행하지 않고 생성기가 쓰는 실제 카운터 표현식만 평가한다.
    candidates=[]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==variable for t in node.targets):
            if 'view-count' in ast.get_source_segment(source,node):candidates.append(node.value)
    if len(candidates)!=1:raise AssertionError((variable,len(candidates)))
    return eval(compile(ast.Expression(candidates[0]),'<counter>','eval'),context)


class TrafficAttributionTests(unittest.TestCase):
    def test_generated_counter_paths_and_browser_fallback(self):
        fixtures=[]
        ctx={'COUNTER_ENDPOINT':'https://counter.example','topic':{'key':'ai_news'}}
        for name,page in [('metals','metals'),('china','china'),('interest','interest'),('trends','trends'),('ai_news','ai_news'),('ai_news','robot_news')]:
            context=dict(ctx,topic={'key':page})
            source=(ROOT/f'tools/build_{name}_report.py').read_text()
            fixtures.append(dict(name=page,page=page,html=counter_from_source(source,'counter',context)))
        notebook=json.loads((ROOT/'samsung_direction_model_colab.ipynb').read_text())
        source=next(''.join(c['source']) for c in notebook['cells'] if '_counter_html = ' in ''.join(c['source']))
        for page in ['samsung','sk_hynix']:
            fixtures.append(dict(name=page,page=page,html=counter_from_source(source,'_counter_html',dict(ctx,TARGET=page))))
        macro=(ROOT/'tools/build_macro_report.py').read_text()
        fixtures.append(dict(name='macro',page='macro',html=counter_from_source(macro,'counter',ctx)))
        main=(ROOT/'docs/index.html').read_text()
        start=main.index('<script src="/predict_stock/traffic_attribution.js"></script>')
        fixtures.append(dict(name='main',page='main',html=main[start:]))
        worker=(ROOT/'counter/worker.js').read_text()
        allowed=json.loads(re.search(r'const ALLOWED_PAGES = (\[[\s\S]*?\]);',worker).group(1))
        for fixture in fixtures:
            self.assertIn(fixture['page'],allowed)
        run=subprocess.run(['node','tests/traffic_attribution_cases.cjs'],cwd=ROOT,input=json.dumps(fixtures),text=True,capture_output=True)
        self.assertEqual(run.returncode,0,run.stderr[-5000:])

    def test_browser_export(self):
        code="const fs=require('fs'),vm=require('vm'),assert=require('assert');const c={URL,URLSearchParams,Date};vm.createContext(c);vm.runInContext(fs.readFileSync('docs/traffic_attribution.js','utf8'),c);assert.equal(typeof c.TrafficAttribution.hitUrl,'function');"
        result=subprocess.run(['node','-e',code],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr[-1500:])
