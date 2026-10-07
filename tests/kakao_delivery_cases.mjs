import assert from 'node:assert/strict';
import fs from 'node:fs';
import {test} from 'node:test';
import {DatabaseSync} from 'node:sqlite';
const worker=await import('data:text/javascript;base64,'+fs.readFileSync('counter/worker.js').toString('base64'));
const RealDate=Date;
let clock=RealDate.parse('2026-10-06T22:00:00Z');
globalThis.Date=class extends RealDate{constructor(...args){super(...(args.length?args:[clock]));}static now(){return clock;}};
let sent=[],mode='ok',barrier=null,onToken=null;
globalThis.fetch=async(url,init={})=>{
  if(url==='https://kauth.kakao.com/oauth/token'){
    if(onToken)await onToken();
    return Response.json({access_token:'renewed-secret',token_type:'bearer',expires_in:3600});
  }
  if(url.endsWith('/access_token_info'))return Response.json({id:321,app_id:123,expires_in:3600});
  if(url.endsWith('/scopes'))return Response.json({id:321,scopes:[{id:'talk_message',using:true,agreed:true}]});
  assert.equal(url,'https://kapi.kakao.com/v2/api/talk/memo/default/send');
  assert.equal(init.redirect,'manual');
  assert.match(init.headers.Authorization,/^Bearer (access-secret|renewed-secret)$/);
  assert.match(init.headers['Content-Type'],/^application\/x-www-form-urlencoded/);
  const payload=JSON.parse(new URLSearchParams(init.body).get('template_object'));
  sent.push(payload);
  if(barrier)await barrier;
  if(mode==='timeout')throw new TypeError('access-secret private-response');
  if(mode==='html')return new Response('access-secret',{status:200});
  if(mode==='redirect')return new Response('',{status:302,headers:{Location:'https://evil.example'}});
  if(mode==='500')return Response.json({msg:'access-secret',code:-1},{status:500});
  if(mode==='429')return Response.json({msg:'access-secret',code:-10},{status:429});
  if(mode.startsWith('daily_'))return Response.json({msg:'access-secret',code:-Number(mode.slice(6))},{status:400});
  if(mode==='quota')return Response.json({msg:'access-secret',code:-10},{status:400});
  if(mode==='401')return Response.json({msg:'access-secret',code:-401},{status:401});
  if(mode==='scope')return Response.json({msg:'access-secret',code:-402},{status:403});
  if(mode==='rejected')return Response.json({msg:'access-secret',code:-2},{status:400});
  if(mode==='nonzero')return Response.json({result_code:5});
  return Response.json({result_code:0});
};
async function environment({legacy=false}={}){
  mode='ok';sent=[];barrier=null;onToken=null;
  const db=new DatabaseSync(':memory:');db.exec(fs.readFileSync('counter/schema.sql','utf8'));
  if(legacy)for(const t of ['kakao_delivery_settings','kakao_deliveries'])db.exec('DROP TABLE IF EXISTS '+t);
  const statement=(sql,args=[])=>({bind:(...v)=>statement(sql,v),first:async()=>db.prepare(sql).get(...args)||null,all:async()=>({results:db.prepare(sql).all(...args)}),run:async()=>({meta:{changes:db.prepare(sql).run(...args).changes}})});
  const DB={prepare:sql=>statement(sql),batch:async list=>{db.exec('BEGIN');try{const results=[];for(const s of list)results.push(await s.run());db.exec('COMMIT');return results;}catch(e){db.exec('ROLLBACK');throw e;}}};
  const env={db,DB,STATS_TOKEN:'admin-secret',KAKAO_KEY:'key',KAKAO_APP_ID:'123',KAKAO_PUBLIC_URL:'https://counter.example',KAKAO_TOKEN_KEY:Buffer.alloc(32,7).toString('base64')};
  const key=await crypto.subtle.importKey('raw',Buffer.alloc(32,7),'AES-GCM',false,['encrypt']);
  const iv=new Uint8Array(12),cipher=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:new TextEncoder().encode('123|321')},key,new TextEncoder().encode(JSON.stringify({access_token:'access-secret',refresh_token:'refresh-secret'})));
  db.prepare("INSERT INTO kakao_connection(id,owner_id,app_id,status,version,token_cipher,access_expires,refresh_expires) VALUES(1,'321','123','connected',1,?,?,?)").run('v1.'+Buffer.from(iv).toString('base64')+'.'+Buffer.from(cipher).toString('base64'),clock+3600000,clock+86400000);
  return env;
}
async function request(env,path,body,extra={}){
  const r=await worker.default.fetch(new Request('https://counter.example/kakao/'+path,{method:body===undefined?'GET':'POST',headers:{Origin:'https://namyikim.github.io',Authorization:'Bearer admin-secret','Content-Type':'application/json',...extra},...(body===undefined?{}:{body:JSON.stringify(body)})}),env);
  return {status:r.status,body:await r.json()};
}
const day=()=>new Date(clock+9*3600000).toISOString().slice(0,10);
function report(target,phase='pre_open') {return {target,session_date:day(),phase,lines:[target==='samsung'?'종가예측 100,000원 · 상승':'종가예측 200,000원 · 하락'],url:'https://namyikim.github.io/predict_stock/'+target+'/'};}
function publish(env,targets=['samsung','sk_hynix'],phase='pre_open'){
  const id='digest/'+day()+'/'+phase,stamp=new Date().toISOString();
  env.db.prepare('INSERT OR IGNORE INTO mail_editions(id,day,phase,created_at) VALUES(?,?,?,?)').run(id,day(),phase,stamp);
  for(const t of targets)env.db.prepare('INSERT OR IGNORE INTO mail_events(id,target,phase,session_date,content,source_revision,created_at) VALUES(?,?,?,?,?,?,?)').run(id+'/'+t,t,phase,day(),JSON.stringify(report(t,phase)),'a'.repeat(40),stamp);
}
async function ready(){const env=await environment();assert.equal((await request(env,'control',{enabled:true})).status,200);clock+=1000;publish(env);return env;}
const row=env=>env.db.prepare('SELECT * FROM kakao_deliveries ORDER BY created_at DESC').get();
const testBody=()=>({confirmed:true,request_id:crypto.randomUUID()});

let env=await environment();
assert.equal((await request(env,'delivery-status')).status,200,'발송 상태 API가 필요하다');
assert.equal((await request(env,'delivery-status')).body.enabled,false);
assert.equal((await request(env,'control',{enabled:true},{Authorization:''})).status,401);
assert.equal((await request(env,'test',testBody(),{Origin:'https://evil.example'})).status,403);
assert.equal((await request(env,'test',{request_id:'x'})).status,400);
assert.equal(sent.length,0);
assert.equal((await request(await environment({legacy:true}),'delivery-status')).body.available,false);
env=await environment();publish(env);assert.equal((await worker.processKakao(env)).status,'disabled');assert.equal(sent.length,0);
await request(env,'control',{enabled:true});clock+=1000;await worker.processKakao(env);assert.equal(sent.length,0,'켜기 이전 회차를 소급 발송하지 않음');

env=await ready();
await Promise.all([worker.processKakao(env),worker.processKakao(env)]);
assert.equal(sent.length,1,'동시 Cron의 단일 전송');assert.equal(row(env).status,'sent');
assert.match(sent[0].text,/2026-10-07.*개장 전/);assert.match(sent[0].text,/100,000원/);assert.match(sent[0].text,/200,000원/);
assert.equal(sent[0].buttons.length,2);assert.equal(new URL(sent[0].buttons[0].link.web_url).searchParams.get('utm_source'),'kakao');
env.db.prepare('UPDATE kakao_connection SET version=version+1').run();await worker.processKakao(env);assert.equal(sent.length,1,'토큰 버전 변경에도 같은 회차 중복 금지');
await request(env,'control',{enabled:false});await request(env,'control',{enabled:true});await worker.processKakao(env);assert.equal(sent.length,1,'재개해도 이미 보낸 회차 재전송 금지');
let status=(await request(env,'delivery-status')).body;assert.equal(status.deliveries[0].status,'sent');assert.equal(JSON.stringify(status).includes('access-secret'),false);assert.equal(JSON.stringify(status).includes('payload'),false);

env=await environment();await request(env,'control',{enabled:true});clock+=1000;publish(env,['samsung']);await worker.processKakao(env);assert.equal(sent.length,0,'두 종목 게시 완료 대기');publish(env,['sk_hynix']);await worker.processKakao(env);assert.equal(sent.length,1);
const long=worker.kakaoReportTemplate([{...report('samsung'),lines:['😀'.repeat(500)]},report('sk_hynix')],day(),'pre_open');assert.ok(long.text.length<=200);assert.equal(long.text.includes('\ud83d\n'),false);assert.match(long.text,/SK하이닉스/);
assert.throws(()=>worker.kakaoReportTemplate([{...report('samsung'),url:'https://evil.example'},report('sk_hynix')],day(),'pre_open'));
assert.throws(()=>worker.kakaoReportTemplate([{...report('samsung'),session_date:'2020-01-01'},report('sk_hynix')],day(),'pre_open'));

for(const [failure,expected] of [['timeout','uncertain'],['html','uncertain'],['500','uncertain'],['nonzero','uncertain'],['redirect','failed'],['401','failed'],['scope','failed'],['rejected','failed']]){
  env=await ready();mode=failure;await worker.processKakao(env);assert.equal(row(env).status,expected,failure);assert.equal(sent.length,1);clock+=360000;await worker.processKakao(env);assert.equal(sent.length,1,failure+' 결과 자동 재전송 금지');assert.equal(JSON.stringify((await request(env,'delivery-status')).body).includes('access-secret'),false);
}
for(const failure of ['429','quota']){env=await ready();mode=failure;await worker.processKakao(env);assert.equal(row(env).status,'pending');assert.equal(row(env).error_code,'rate_limited');await worker.processKakao(env);assert.equal(sent.length,1);clock+=360000;mode='ok';await worker.processKakao(env);assert.equal(sent.length,2);assert.equal(row(env).status,'sent');}
env=await ready();mode='429';await worker.processKakao(env);await request(env,'control',{enabled:false});await request(env,'control',{enabled:true});clock+=360000;mode='ok';await worker.processKakao(env);assert.equal(sent.length,1,'중지한 대기 요청은 재개해도 부활하지 않음');

env=await ready();env.db.prepare('UPDATE kakao_connection SET access_expires=?').run(clock-1);await worker.processKakao(env);assert.equal(sent.length,1,'만료 임박 토큰 갱신 뒤 전송');assert.equal(row(env).status,'sent');
env=await ready();env.db.prepare('UPDATE kakao_connection SET access_expires=?').run(clock-1);onToken=async()=>{await request(env,'disconnect',{});};await worker.processKakao(env);assert.equal(sent.length,0,'갱신 중 연결 해제는 발송 차단');
env=await ready();env.db.prepare('UPDATE kakao_connection SET access_expires=?').run(clock-1);onToken=async()=>{await request(env,'control',{enabled:false});};await worker.processKakao(env);assert.equal(sent.length,0,'갱신 중 자동 중지는 발송 차단');

env=await environment();let body=testBody();assert.equal((await request(env,'test',body)).body.status,'sent');await request(env,'test',body);assert.equal(sent.length,1,'같은 시험 요청 중복 금지');assert.equal((await request(env,'delivery-status')).body.enabled,false,'시험은 자동 발송을 켜지 않음');
await request(env,'test',testBody());await request(env,'test',testBody());assert.equal((await request(env,'test',testBody())).status,429,'하루 시험 메시지는 최대 3회');assert.equal(sent.length,3);
env=await ready();let release;barrier=new Promise(r=>release=r);const sending=worker.processKakao(env);while(!sent.length)await new Promise(r=>setTimeout(r,1));clock+=120000;await worker.processKakao(env);assert.equal(row(env).status,'uncertain','버려진 전송 임대는 확인 필요');assert.equal(sent.length,1);release();await sending;assert.equal(sent.length,1);

// 기존 게시 검증 경로로 등록된 이벤트를 이메일 꺼짐/구독자 없음 상태에서도 처리한다.
env=await environment();env.MAIL_PUBLISH_TOKEN='publish-secret';await request(env,'control',{enabled:true});clock+=1000;
for(const target of ['samsung','sk_hynix']){
  const result=await worker.default.fetch(new Request('https://counter.example/mail/events',{method:'POST',headers:{Authorization:'Bearer publish-secret','Content-Type':'application/json'},body:JSON.stringify({...report(target),source_revision:'a'.repeat(40),automation:{event:'schedule',attempt:1}})}),env);
  assert.equal(result.status,202);assert.equal((await result.json()).enabled,false);
}
await worker.default.scheduled({cron:'*/5 * * * *',scheduledTime:clock},env);assert.equal(sent.length,1,'게시 이벤트 → Cron → 본인 발송 통합');
// 확정 거절 후 재시도에서도 최초 원문을 고정한다.
env=await ready();mode='429';await worker.processKakao(env);const frozen=JSON.stringify(sent[0]);env.db.prepare("UPDATE mail_events SET content='{}'").run();clock+=300000;mode='ok';await worker.processKakao(env);assert.equal(JSON.stringify(sent[1]),frozen);
// 이행은 반복 적용해도 기존 인증과 발송 내역을 보존한다.
for(let i=0;i<2;i++)env.db.exec(fs.readFileSync('counter/migrations/20261007_kakao_delivery.sql','utf8'));
assert.equal(row(env).status,'sent');assert.equal(env.db.prepare('SELECT owner_id FROM kakao_connection').get().owner_id,'321');
// 마감 후 회차도 두 종목이 준비되면 한 건, 다음 날에는 지난 회차를 보내지 않는다.
clock=RealDate.parse('2026-10-07T06:45:00Z');env=await environment();await request(env,'control',{enabled:true});clock+=1000;publish(env,['samsung','sk_hynix'],'post_close');await worker.processKakao(env);assert.equal(sent.length,1);assert.match(sent[0].text,/마감 후 회고/);clock+=86400000;await worker.processKakao(env);assert.equal(sent.length,1);

// 신구 스키마에서도 카카오 장애가 이메일/Cron 흐름을 중단하지 않는다.
env=await environment({legacy:true});await worker.default.scheduled({cron:'*/5 * * * *',scheduledTime:clock},env);
console.log('카카오 발송: 권한·본문·회차·동시성·재시도·해제/중지·시험 상한·구 DB 검증 통과');

await test('응답 유실 뒤 자정을 넘긴 동일 시험 요청은 다시 보내지 않는다',async()=>{
  clock=RealDate.parse('2026-10-08T14:59:59Z');env=await environment();const body=testBody();
  assert.equal((await request(env,'test',body)).body.status,'sent');clock+=2000;
  assert.equal((await request(env,'test',body)).body.status,'sent');assert.equal(sent.length,1);
  assert.equal(env.db.prepare("SELECT COUNT(*) AS n FROM kakao_deliveries").get().n,1);
});
await test('인증 임대가 풀리면 같은 시험 요청을 다시 눌러 발송할 수 있다',async()=>{
  env=await environment();env.db.prepare('UPDATE kakao_connection SET lease_until=?').run(clock+60000);
  const body=testBody();assert.equal((await request(env,'test',body)).body.status,'blocked');assert.equal(sent.length,0);
  env.db.prepare('UPDATE kakao_connection SET lease_until=0').run();
  assert.equal((await request(env,'test',body)).body.status,'sent');assert.equal(sent.length,1);
});
await test('카카오 일간 발신·수신·쌍별 한도는 설정 오류와 구분하고 재시도하지 않는다',async()=>{
  for(const code of [532,533,536]){
    env=await environment();mode='daily_'+code;const body=testBody();
    const result=(await request(env,'test',body)).body;assert.equal(result.status,'failed');assert.equal(result.error_code,'daily_limit');
    await request(env,'test',body);assert.equal(sent.length,1);
  }
});
await test('전날 인증 대기 중이던 시험 요청은 다음 날 새로 보내지 않는다',async()=>{
  clock=RealDate.parse('2026-10-09T14:59:59Z');env=await environment();env.db.prepare('UPDATE kakao_connection SET lease_until=?').run(clock+60000);
  const body=testBody();assert.equal((await request(env,'test',body)).body.status,'blocked');clock+=2000;
  env.db.prepare('UPDATE kakao_connection SET lease_until=0').run();
  assert.equal((await request(env,'test',body)).body.status,'cancelled');assert.equal(sent.length,0);
  assert.equal((await request(env,'test',testBody())).body.status,'sent');assert.equal(sent.length,1);
});
