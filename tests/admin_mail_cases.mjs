// 실제 관리자 스크립트를 DOM 경계만 대체해 실행한다. 네트워크·실제 이메일은 사용하지 않는다.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const html=fs.readFileSync('docs/admin/index.html','utf8');
const script=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]).find(s=>s.includes('// 구독자 목록.'));
const elements=new Map();
for(const m of html.matchAll(/id="([^"]+)"/g)) elements.set(m[1],{value:'',textContent:'',innerHTML:'',disabled:false,events:{},addEventListener(name,fn){this.events[name]=fn;}});
const get=id=>{assert.ok(elements.has(id),'존재하는 DOM만 사용: '+id);return elements.get(id);};
let confirmed=false, status='dispatched', paused=false, error=false;
const requests=[];
const context={document:{getElementById:get},location:{hash:''},window:{addEventListener(){}},localStorage:{getItem(){return null;}},
 confirm:()=>confirmed,fetch:async(url,init)=>{
  requests.push({url,init});
  if(url.endsWith('/mail/status')) return {ok:true,status:200,json:async()=>({enabled:true,configured:true,paused,events:[],missing:[],provider:'gmail',notice:''})};
  assert.ok(url.endsWith('/mail/send-today'));
  return {ok:!error,status:error?409:202,json:async()=>error?{error:'발송 시간 밖'}:{status}};
 }};
vm.runInNewContext(script,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
get('mail-send-today').events.click();await flush();assert.equal(requests.length,0,'인증 없이 요청하지 않음');
get('sub-token').value='private-token';
get('mail-refresh').events.click();await flush();assert.equal(get('mail-send-today').disabled,false);
get('mail-send-today').events.click();await flush();assert.equal(requests.filter(r=>r.url.endsWith('/mail/send-today')).length,0,'확인 취소 시 발송 요청 없음');
confirmed=true;get('mail-send-today').events.click();get('mail-send-today').events.click();await flush();
const posts=requests.filter(r=>r.url.endsWith('/mail/send-today'));
assert.equal(posts.length,1,'연속 클릭은 한 번만 전송');assert.equal(posts[0].init.method,'POST');
assert.equal(posts[0].init.headers.Authorization,'Bearer private-token');assert.ok(!posts[0].url.includes('private-token'));
assert.ok(get('mail-action-msg').textContent.includes('아직 발송 완료는 아닙니다'));
status='already_registered';get('mail-send-today').events.click();await flush();
assert.ok(get('mail-action-msg').textContent.includes('이미 등록'));
error=true;get('mail-send-today').events.click();await flush();assert.ok(get('mail-action-msg').textContent.includes('발송 시간 밖'));
paused=true;get('mail-refresh').events.click();await flush();assert.equal(get('mail-send-today').disabled,true);
console.log('관리자 버튼 인증·확인·연속 클릭·접수/발송 구분·오류 표시 통과');
