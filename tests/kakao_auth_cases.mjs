import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
const worker=await import('data:text/javascript;base64,'+fs.readFileSync('counter/worker.js').toString('base64'));
const RealDate=Date;
let clock=Date.parse('2026-10-07T01:00:00Z');
globalThis.Date=class extends RealDate {constructor(...args){super(...(args.length?args:[clock]));}static now(){return clock;}};
function environment(legacy=false){
  const db=new DatabaseSync(':memory:');
  db.exec(fs.readFileSync('counter/schema.sql','utf8'));
  if(legacy)for(const name of ['kakao_connection','kakao_oauth'])db.exec('DROP TABLE IF EXISTS '+name);
  function statement(sql,args=[]){return {bind:(...v)=>statement(sql,v),first:async()=>db.prepare(sql).get(...args)||null,all:async()=>({results:db.prepare(sql).all(...args)}),run:async()=>({meta:{changes:db.prepare(sql).run(...args).changes}})};}
  const DB={prepare:sql=>statement(sql),batch:async list=>{db.exec('BEGIN');try{const result=[];for(const s of list)result.push(await s.run());db.exec('COMMIT');return result;}catch(e){db.exec('ROLLBACK');throw e;}}};
  return {db,DB,STATS_TOKEN:'admin-secret',VISITOR_SALT:'salt',KAKAO_KEY:'rest-key',KAKAO_APP_ID:'123',KAKAO_TOKEN_KEY:Buffer.alloc(32,7).toString('base64'),KAKAO_PUBLIC_URL:'https://counter.example',KAKAO_CLIENT_SECRET:'client-secret'};
}
const request=async(env,path,{method='GET',body,admin=true,cookie,origin='https://namyikim.github.io'}={})=>worker.default.fetch(new Request('https://counter.example'+path,{method,headers:{Origin:origin,...(admin?{Authorization:'Bearer admin-secret'}:{}),...(cookie?{Cookie:cookie}:{}),...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})}),env,{});
const json=async(...args)=>{const r=await request(...args);return {status:r.status,body:await r.json()};};
const post=(env,path,body={},options={})=>json(env,path,{...options,method:'POST',body});
let calls=[],mode='ok',refreshToken='refresh-new',barrier=null,unlinkBarrier=null,unlinkEntered=null;
const diagnosticLogs=[];
console.error=(...args)=>diagnosticLogs.push(args);
globalThis.fetch=async(url,init={})=>{
  calls.push({url:String(url),init});
  if(mode==='timeout')throw new Error('secret-code client-secret access-old');
  if(mode==='unsafe-error-name'){const e=new Error('access-old');e.name='client-secret';throw e;}
  if(String(url)==='https://kauth.kakao.com/oauth/token'){
    const form=new URLSearchParams(init.body);
    assert.equal(form.get('client_id'),'rest-key');assert.equal(form.get('client_secret'),'client-secret');
    if(mode==='token-html')return new Response('secret-code client-secret access-old refresh-old',{status:502,headers:{'Content-Type':'text/html;secret=access-old'}});
    if(form.get('grant_type')==='refresh_token'){
      assert.ok(['refresh-old','refresh-new'].includes(form.get('refresh_token')));
      if(barrier)await barrier;
      if(mode==='invalid-refresh')return Response.json({error:'invalid_grant',error_code:'KOE322',error_description:'refresh-old'},{status:400});
      if(mode==='rate-limit')return Response.json({error_description:'refresh-old'},{status:429});
      return Response.json({access_token:'access-new',token_type:'bearer',expires_in:3600,...(refreshToken?{refresh_token:refreshToken,refresh_token_expires_in:5184000}:{})});
    }
    assert.equal(form.get('redirect_uri'),'https://counter.example/kakao/callback');assert.equal(form.get('code'),'secret-code');
    return Response.json({access_token:'access-old',token_type:'bearer',refresh_token:'refresh-old',expires_in:3600,refresh_token_expires_in:5184000,scope:'talk_message'});
  }
  assert.equal(init.headers.Authorization,'Bearer '+(init.headers.Authorization.endsWith('new')?'access-new':'access-old'));
  if(String(url)==='https://kapi.kakao.com/v1/user/access_token_info' && mode==='identity-timeout')throw new DOMException('access-old secret-code','TimeoutError');
  if(String(url)==='https://kapi.kakao.com/v2/user/scopes' && mode==='scope-html')return new Response('access-old client-secret',{status:403,headers:{'Content-Type':'text/html'}});
  if(String(url)==='https://kapi.kakao.com/v1/user/access_token_info')return Response.json({id:mode==='wrong-owner'?456:321,app_id:mode==='wrong-app'?999:123,expires_in:3600});
  if(String(url)==='https://kapi.kakao.com/v2/user/scopes' && mode==='scope-outage')return Response.json({error_description:'access-new'},{status:503});
  if(String(url)==='https://kapi.kakao.com/v2/user/scopes')return Response.json({id:mode==='wrong-owner'?456:321,scopes:[{id:'talk_message',using:true,agreed:mode!=='no-scope',revocable:true}]});
  if(String(url)==='https://kapi.kakao.com/v1/user/unlink'){if(unlinkEntered)unlinkEntered();if(unlinkBarrier)await unlinkBarrier;return Response.json({id:321});}
  throw new Error('예상하지 않은 외부 요청 '+url);
};
const env=environment();
assert.equal((await json(env,'/kakao/status',{admin:false})).status,401,'인증 없이 연결 정보 조회 금지');
assert.equal((await post(env,'/kakao/connect',{}, {admin:false})).status,401,'인증 없이 연결 시작 금지');
assert.equal((await post(env,'/kakao/connect',{}, {origin:'https://evil.example'})).status,403);
assert.equal((await json(env,'/kakao/status')).body.status,'disconnected');
const noKey=environment();delete noKey.KAKAO_TOKEN_KEY;
assert.equal((await post(noKey,'/kakao/connect')).status,503);
assert.equal((await json(environment(true),'/kakao/status')).body.available,false,'스키마 미적용과 미연결 구분');
const unsafe=environment();unsafe.KAKAO_PUBLIC_URL='https://counter.example/?token=secret';
assert.equal((await post(unsafe,'/kakao/connect')).status,503,'콜백 설정은 쿼리 없는 HTTPS 원점');

async function start(env){
  const r=await post(env,'/kakao/connect');assert.equal(r.status,200);
  assert.equal(JSON.stringify(r.body).includes('admin-secret'),false);
  const url=new URL(r.body.login_url);assert.equal(url.origin,'https://counter.example');
  const redirect=await request(env,url.pathname+url.search,{admin:false});assert.equal(redirect.status,302);
  const auth=new URL(redirect.headers.get('Location'));assert.equal(auth.origin,'https://kauth.kakao.com');assert.equal(auth.searchParams.get('scope'),'talk_message');
  const cookie=redirect.headers.get('Set-Cookie');assert.match(cookie,/HttpOnly/);assert.match(cookie,/Secure/);assert.match(cookie,/SameSite=Lax/);
  assert.equal((await request(env,url.pathname+url.search,{admin:false})).status,400,'연결 진입 코드는 한 번만 사용');
  return {state:auth.searchParams.get('state'),cookie:cookie.split(';')[0]};
}
const callback=(env,flow,extra='&code=secret-code')=>request(env,'/kakao/callback?state='+flow.state+extra,{admin:false,cookie:flow.cookie});
async function link(env){
  const flow=await start(env),r=await callback(env,flow);assert.equal(r.status,303);
  assert.equal(r.headers.get('Referrer-Policy'),'no-referrer');assert.equal(r.headers.get('Location').includes('secret-code'),false);
  const pending=(await json(env,'/kakao/status')).body.pending;assert.equal(pending.owner_id,'321');
  assert.equal((await post(env,'/kakao/confirm',{pending_id:pending.id,owner_id:'999'})).status,400);
  assert.equal((await post(env,'/kakao/confirm',{pending_id:pending.id,owner_id:'321'})).status,200);
  const status=(await json(env,'/kakao/status')).body;assert.equal(status.status,'connected');assert.equal(status.enabled,false);
  assert.equal((await post(env,'/kakao/confirm',{pending_id:pending.id,owner_id:'321'})).status,409);
  return flow;
}
let flow=await start(env),before=calls.length;
assert.equal((await callback(env,{...flow,cookie:'__Host-kakao_auth=wrong'})).status,400);assert.equal(calls.length,before);
assert.equal((await callback(env,{...flow,state:'tampered'})).status,400);assert.equal(calls.length,before);
assert.equal((await callback(env,flow,'&state='+flow.state+'&code=secret-code')).status,400);
assert.equal((await callback(env,flow)).status,303);
assert.equal((await callback(env,flow)).status,400,'동일 state로 토큰 재발급 금지');
flow=await start(environment());clock+=601000;assert.equal((await callback(environment(),flow)).status,400);clock-=601000;
const expiry=environment();flow=await start(expiry);clock+=601000;assert.equal((await callback(expiry,flow)).status,400);clock-=601000;
for(const scenario of ['wrong-app','no-scope','timeout']){
  const e=environment();flow=await start(e);mode=scenario;const r=await callback(e,flow);assert.equal(r.status,303);
  const s=(await json(e,'/kakao/status')).body;assert.equal(s.status,'disconnected');assert.equal(s.pending,null);
  assert.equal(JSON.stringify(s).includes('secret-code'),false);assert.equal(JSON.stringify(s).includes('access-old'),false);mode='ok';
}
// 운영에서 반복된 일반 오류의 위치만 기록하고 인가 코드·토큰·외부 응답은 로그에도 남기지 않는다.
for(const [scenario,api,stage,status,kind] of [
  ['timeout','token','request',0,'Error'],
  ['unsafe-error-name','token','request',0,'other'],
  ['token-html','token','response_json',502,'SyntaxError'],
  ['identity-timeout','access_token_info','request',0,'TimeoutError'],
  ['scope-html','scopes','response_json',403,'SyntaxError']
]){
  const e=environment(),flow=await start(e),before=diagnosticLogs.length;mode=scenario;
  assert.equal((await callback(e,flow)).status,303);
  assert.equal((await json(e,'/kakao/status')).body.last_error,'temporarily_unavailable');
  assert.equal(diagnosticLogs.length,before+1);
  const log=diagnosticLogs.at(-1);assert.equal(log[0],'[kakao-auth]');
  assert.deepEqual(log[1],{api,stage,http_status:status,error_name:kind,content_type:stage==='response_json'?'non_json':'unknown',timeout_supported:true});
  for(const secret of ['secret-code','client-secret','access-old','refresh-old','admin-secret'])assert.equal(JSON.stringify(log).includes(secret),false);
  mode='ok';
}
{
  const e=environment(),flow=await start(e),originalTimeout=AbortSignal.timeout;
  try {AbortSignal.timeout=undefined;assert.equal((await callback(e,flow)).status,303);}
  finally {AbortSignal.timeout=originalTimeout;}
  assert.equal(diagnosticLogs.at(-1)[1].stage,'prepare');
  assert.equal(diagnosticLogs.at(-1)[1].timeout_supported,false);
}
{
  const before=diagnosticLogs.length;await link(environment());
  assert.equal(diagnosticLogs.length,before,'정상 인증의 응답과 토큰은 로그에 기록하지 않는다');
}
const changed=environment();flow=await start(changed);changed.KAKAO_APP_ID='999';before=calls.length;
assert.equal((await callback(changed,flow)).status,400);assert.equal(calls.length,before,'중간에 변경된 앱 설정으로 인증하지 않음');
const connected=environment();await link(connected);
const stored=connected.db.prepare('SELECT token_cipher FROM kakao_connection').get().token_cipher;
assert.equal(stored.includes('access-old'),false);assert.equal(stored.includes('refresh-old'),false);
before=calls.length;assert.equal((await worker.getKakaoAccessToken(connected)).accessToken,'access-old');assert.equal(calls.length,before);
clock+=3600000;refreshToken=null;assert.equal((await worker.getKakaoAccessToken(connected)).accessToken,'access-new');
// 기존 갱신 토큰이 유지돼 다음 갱신 요청도 성공해야 한다.
clock+=3600000;refreshToken='refresh-new';assert.equal((await worker.getKakaoAccessToken(connected)).accessToken,'access-new');
let release;barrier=new Promise(resolve=>release=resolve);clock+=3600000;before=calls.length;
const one=worker.getKakaoAccessToken(connected);await new Promise(resolve=>setImmediate(resolve));
assert.equal((await worker.getKakaoAccessToken(connected)).status,'busy');release();await one;barrier=null;
assert.equal(calls.filter(c=>c.url==='https://kauth.kakao.com/oauth/token').length>0,true);
const rotated=environment();await link(rotated);clock+=3600000;mode='scope-outage';
assert.equal((await worker.getKakaoAccessToken(rotated)).status,'provider_error');mode='ok';
assert.equal((await worker.getKakaoAccessToken(rotated)).status,'ready','권한 API 일시 장애가 교체된 갱신 토큰을 버리지 않음');
clock+=3600000;before=calls.length;assert.equal((await worker.getKakaoAccessToken(rotated)).status,'ready');
assert.equal(new URLSearchParams(calls.slice(before).find(c=>c.url==='https://kauth.kakao.com/oauth/token').init.body).get('refresh_token'),'refresh-new');
const raced=environment();await link(raced);clock+=3600000;barrier=new Promise(resolve=>release=resolve);
const refresh=worker.getKakaoAccessToken(raced);await new Promise(resolve=>setImmediate(resolve));
assert.equal((await post(raced,'/kakao/disconnect')).status,200);release();assert.notEqual((await refresh).status,'ready');barrier=null;
assert.equal(raced.db.prepare('SELECT token_cipher FROM kakao_connection').get().token_cipher,'','해제 중 갱신이 토큰을 복구하지 않음');
const revoked=environment();await link(revoked);clock+=3600000;mode='invalid-refresh';
assert.equal((await worker.getKakaoAccessToken(revoked)).status,'reconnect_required');mode='ok';
assert.equal((await json(revoked,'/kakao/status')).body.status,'reconnect_required');
const retry=environment();await link(retry);clock+=3600000;mode='rate-limit';assert.equal((await worker.getKakaoAccessToken(retry)).status,'rate_limited');mode='ok';
assert.equal((await worker.getKakaoAccessToken(retry)).status,'ready');
flow=await start(connected);mode='wrong-owner';await callback(connected,flow);mode='ok';
assert.equal((await json(connected,'/kakao/status')).body.pending,null,'다른 계정으로 교체 금지');
assert.equal((await post(connected,'/kakao/disconnect',{revoke:true})).status,200);
assert.equal((await json(connected,'/kakao/status')).body.status,'disconnected');
assert.equal((await worker.getKakaoAccessToken(connected)).status,'disconnected');
const unlinkRace=environment();await link(unlinkRace);const priorFlow=await start(unlinkRace);
let unlinkRelease;unlinkBarrier=new Promise(resolve=>unlinkRelease=resolve);
const entered=new Promise(resolve=>unlinkEntered=resolve);const unlinking=post(unlinkRace,'/kakao/disconnect',{revoke:true});await entered;unlinkEntered=null;
assert.equal((await post(unlinkRace,'/kakao/connect')).status,409,'앱 동의 해제 중 다시 연결 금지');
assert.equal((await post(unlinkRace,'/kakao/disconnect')).status,409,'앱 동의 해제 중 중복 해제 금지');
assert.equal((await json(unlinkRace,'/kakao/status')).body.status,'revoking');
unlinkRelease();await unlinking;unlinkBarrier=null;
assert.equal((await json(unlinkRace,'/kakao/status')).body.status,'disconnected');await link(unlinkRace);
const uncertain=environment();await link(uncertain);mode='timeout';await post(uncertain,'/kakao/disconnect',{revoke:true});mode='ok';
assert.equal((await json(uncertain,'/kakao/status')).body.status,'revoke_uncertain');
assert.equal((await post(uncertain,'/kakao/connect')).status,409,'불명확한 원격 해제는 확인하기 전 재연결 금지');
assert.equal((await post(uncertain,'/kakao/disconnect')).status,409,'일반 해제로 불명확 상태를 우회하지 않음');
assert.equal((await post(uncertain,'/kakao/disconnect',{acknowledge_revoke:true})).status,200);
const consumeRace=environment();flow=await start(consumeRace);const prepare=consumeRace.DB.prepare;
consumeRace.DB.prepare=sql=>{const stmt=prepare(sql);if(sql.startsWith("UPDATE kakao_oauth SET status='consumed'")){
  return {bind(...values){const bound=stmt.bind(...values);return {...bound,async run(){const r=await bound.run();await post(consumeRace,'/kakao/disconnect');return r;}};}};}return stmt;};
assert.equal((await callback(consumeRace,flow)).status,303,'state 소비 직후 해제돼도 코드 URL을 벗어나 관리자 복귀');
const tampered=environment();await link(tampered);tampered.db.prepare("UPDATE kakao_connection SET token_cipher=token_cipher||'x'").run();assert.equal((await worker.getKakaoAccessToken(tampered)).status,'configuration_required');
const oldDB=environment(true);assert.equal((await json(oldDB,'/stats')).status,200,'카카오 스키마 미적용이어도 방문 통계 유지');
const cleanup=environment();await start(cleanup);clock+=601000;await worker.default.scheduled({cron:'*/5 * * * *',scheduledTime:clock},cleanup);assert.equal(cleanup.db.prepare('SELECT COUNT(*) AS n FROM kakao_oauth').get().n,0,'주기 실행에서 만료된 연결 자료 폐기');clock-=601000;
oldDB.db.exec(fs.readFileSync('counter/migrations/20261007_kakao_auth.sql','utf8'));oldDB.db.exec(fs.readFileSync('counter/migrations/20261007_kakao_auth.sql','utf8'));assert.equal((await json(oldDB,'/kakao/status')).body.available,true);
console.log('카카오 인증·state·암호화·계정 확인·갱신·동시 실행·해제·기존 경로 검증 통과');
