import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const nodes=new Map();
for(const id of ['kakao-token','kakao-status','kakao-action','kakao-load','kakao-connect','kakao-confirm','kakao-refresh','kakao-disconnect','kakao-revoke','kakao-acknowledge','token','sub-token'])nodes.set(id,{id,value:'',disabled:false,checked:false,textContent:'',handlers:{},addEventListener(type,fn){this.handlers[type]=fn;}});
const document={getElementById:id=>nodes.get(id)||null};
const requests=[],navigations=[];
let status={available:true,configured:true,status:'disconnected',enabled:false,pending:null},fail=0;
const window={document,location:{assign:url=>navigations.push(url)},localStorage:{getItem:()=>null},confirm:()=>true};
const context=vm.createContext({window,document,URL,fetch:async(url,init)=>{requests.push({url,init});if(fail)return {ok:false,status:fail,json:async()=>({error:'권한 오류'})};return {ok:true,status:200,json:async()=>url.endsWith('/status')?status:url.endsWith('/connect')?{login_url:'https://predict-stock-counter.kimname1.workers.dev/kakao/authorize?ticket='+ 'a'.repeat(64)}:{ok:true,status:'ready'}};}});
vm.runInContext(fs.readFileSync('docs/admin/kakao_auth.js','utf8'),context);
const click=async id=>nodes.get(id).handlers.click();
await click('kakao-load');assert.equal(requests.length,0,'토큰 없이 요청하지 않음');
nodes.get('kakao-token').value='admin-token';await click('kakao-load');
assert.match(nodes.get('kakao-status').textContent,/미연결/);assert.equal(requests.at(-1).init.headers.Authorization,'Bearer admin-token');
await click('kakao-connect');assert.equal(navigations.length,1);assert.equal(navigations[0].includes('admin-token'),false);
status={available:true,configured:true,status:'disconnected',enabled:false,pending:{id:'a'.repeat(64),owner_id:'321'}};
await click('kakao-load');assert.equal(nodes.get('kakao-confirm').disabled,false);await click('kakao-confirm');
const confirmed=requests.find(r=>r.url.endsWith('/confirm'));assert.deepEqual(JSON.parse(confirmed.init.body),{pending_id:'a'.repeat(64),owner_id:'321'});
status={available:true,configured:true,status:'connected',enabled:false,owner_id:'321',last_error:'<script>alert(1)</script>'};
await click('kakao-load');assert.match(nodes.get('kakao-status').textContent,/연결됨/);assert.equal(nodes.get('kakao-confirm').disabled,true);
nodes.get('kakao-revoke').checked=true;await click('kakao-disconnect');assert.deepEqual(JSON.parse(requests.find(r=>r.url.endsWith('/disconnect')).init.body),{revoke:true});
status={available:true,configured:true,status:'revoking',enabled:false,pending:null};await click('kakao-load');assert.equal(nodes.get('kakao-connect').disabled,true);assert.equal(nodes.get('kakao-disconnect').disabled,true);
status={available:true,configured:true,status:'revoke_uncertain',enabled:false,pending:null};await click('kakao-load');assert.match(nodes.get('kakao-status').textContent,/해제 결과 확인/);await click('kakao-acknowledge');assert.equal(JSON.parse(requests.filter(r=>r.url.endsWith('/disconnect')).at(-1).init.body).acknowledge_revoke,true);
status={available:false,status:'schema_required'};await click('kakao-load');assert.match(nodes.get('kakao-status').textContent,/D1/);assert.equal(nodes.get('kakao-connect').disabled,true);
fail=401;await click('kakao-load');assert.match(nodes.get('kakao-action').textContent,/토큰/);
console.log('관리자 카카오 연결·인증·계정 확인·해제 UI 검증 통과');

// 실제 관리자 메뉴 스크립트를 실행해 카카오 콜백 복귀와 일반 진입을 함께 검증한다.
const html=fs.readFileSync('docs/admin/index.html','utf8');
const routing='//'+html.split('<script>\n// 왼쪽 메뉴 전환.')[1].split('</script>')[0];
for(const [search,expected] of [['?kakao=return','kakao'],['','visits']]){
  const panels=['visits','kakao','subscribers'].map(name=>({dataset:{tab:name},hidden:true,querySelector:()=>null}));
  const tabs=panels.map(p=>({dataset:p.dataset,setAttribute(){},removeAttribute(){}}));
  const d={querySelectorAll:selector=>selector==='.admin-panel'?panels:tabs,getElementById:id=>panels.find(p=>'panel-'+p.dataset.tab===id)||null};
  vm.runInNewContext(routing,{document:d,window:{addEventListener(){}},location:{search,hash:'#kakao',pathname:'/predict_stock/admin/'},history:{replaceState(){}},URLSearchParams});
  assert.deepEqual(panels.filter(p=>!p.hidden).map(p=>p.dataset.tab),[expected]);
}
