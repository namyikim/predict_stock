"""파동 화면 모듈과 실험실 연결 계약."""
import json
from pathlib import Path
import re
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]


class WaveViewTests(unittest.TestCase):
    def test_node_state_graph_and_accessibility_cases(self):
        result=subprocess.run(['node','tests/wave_view_cases.mjs'],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_page_loads_module_controls_and_target_data(self):
        page=(ROOT/'docs/lab/index.html').read_text()
        for marker in ('src="wave_view.js"','id="wave-result"','id="wave-strategy"','id="wave-cost"','WaveView.renderWaveComparison','wave_validation_','wave_paper_status.json'):
            self.assertIn(marker,page)
        self.assertIn('min-width:0',page)
        self.assertIn('overflow-wrap:anywhere',page)

    def test_page_handles_unavailable_paper_and_target_switch(self):
        page=(ROOT/'docs/lab/index.html').read_text()
        script=re.search(r'<script>(.*?)</script>',page,re.S).group(1)
        self.assertIn('function loadWave(',script)
        code=f'''const elements={{}};global.document={{getElementById:(id)=>elements[id]||(elements[id]={{value:'samsung',addEventListener:()=>{{}}}}),querySelectorAll:()=>[]}};
global.WaveView={{renderWaveComparison:(v,p,o)=>JSON.stringify({{target:o.target,received:v?.target,paper:p}})}};
global.fetch=(url)=>String(url).includes('wave_validation_samsung')?Promise.resolve({{ok:true,json:()=>Promise.resolve({{target:'samsung'}})}}):String(url).includes('wave_paper_status')?Promise.resolve({{ok:false}}):new Promise(()=>{{}});
eval({json.dumps(script)});
setImmediate(()=>{{const assert=require('node:assert/strict');const rendered=JSON.parse(elements['wave-result'].innerHTML);assert.equal(rendered.received,'samsung');assert.equal(rendered.paper,null);}});
'''
        result=subprocess.run(['node'],input=code,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_late_previous_request_cannot_overwrite_latest_result(self):
        page=(ROOT/'docs/lab/index.html').read_text()
        script=re.search(r'<script>(.*?)</script>',page,re.S).group(1).replace('  loadPaper();','  global.loadWaveForTest=loadWave;')
        code=f'''const elements={{}},pending=[];global.document={{getElementById:(id)=>elements[id]||(elements[id]={{value:'samsung',addEventListener:()=>{{}}}}),querySelectorAll:()=>[]}};
global.WaveView={{renderWaveComparison:(v)=>v.marker}};
global.fetch=url=>String(url).includes('wave_')?new Promise(resolve=>pending.push({{url,resolve}})):new Promise(()=>{{}});
eval({json.dumps(script)});
loadWaveForTest('samsung');
(async()=>{{
 const tick=()=>new Promise(resolve=>setImmediate(resolve));
 pending[2].resolve({{ok:true,json:()=>Promise.resolve({{marker:'latest'}})}});pending[3].resolve({{ok:false}});await tick();
 pending[0].resolve({{ok:true,json:()=>Promise.resolve({{marker:'old'}})}});pending[1].resolve({{ok:false}});await tick();
 require('node:assert/strict').equal(elements['wave-result'].innerHTML,'latest');
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
'''
        result=subprocess.run(['node'],input=code,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__':unittest.main()
