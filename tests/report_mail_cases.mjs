import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
const worker=await import('data:text/javascript;base64,'+fs.readFileSync('counter/worker.js').toString('base64'));
const db=new DatabaseSync(':memory:');
db.exec(fs.readFileSync('counter/schema.sql','utf8'));
function statement(sql,args=[]) {
  return {bind:(...v)=>statement(sql,v),first:async()=>db.prepare(sql).get(...args)||null,
    all:async()=>({results:db.prepare(sql).all(...args)}),run:async()=>({meta:{changes:db.prepare(sql).run(...args).changes}})};
}
const DB={prepare:sql=>statement(sql),batch:async list=>{db.exec('BEGIN');try{const r=[];for(const s of list)r.push(await s.run());db.exec('COMMIT');return r;}catch(e){db.exec('ROLLBACK');throw e;}}};

const RealDate=Date;
let clock=Date.parse('2026-10-05T23:00:00Z');
globalThis.Date=class extends RealDate {constructor(...args){super(...(args.length?args:[clock]));} static now(){return clock;}};
const env={DB,MAIL_ENABLED:'1',RESEND_API_KEY:'fake-key',MAIL_FROM:'legacy@example.com',MAIL_PUBLIC_URL:'https://counter.example',MAIL_PUBLISH_TOKEN:'publish-secret',STATS_TOKEN:'admin-secret'};
const day='2026-10-06';
const event={target:'samsung',phase:'pre_open',session_date:day,lines:['상승 60% · 보합 20% · 하락 20%','<script>원문</script>'],source_revision:'a'.repeat(40),url:'https://namyikim.github.io/predict_stock/samsung/',automation:{event:'schedule',attempt:1}};
async function call(path,body,token='publish-secret',method='POST') {
 return worker.default.fetch(new Request('https://counter.example'+path,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})}),env);
}
function add(email,page){db.prepare('INSERT INTO subscribers VALUES (?,?,?)').run(email,page,new Date(clock-1000).toISOString());}
add('one@example.com','main');add('one@example.com','samsung');add('two@example.com','samsung');
assert.equal((await call('/mail/settings',{from_email:'owner@example.com'},'wrong')).status,401);
assert.equal((await call('/mail/settings',{from_email:'not-an-email'},'admin-secret')).status,400);
assert.equal((await call('/mail/settings',{from_email:'OWNER@example.com'},'admin-secret')).status,200);
let settings=await (await call('/mail/settings',undefined,'admin-secret','GET')).json();
assert.equal(settings.from_email,'owner@example.com');
assert.ok(!JSON.stringify(settings).includes('fake-key'));
assert.equal((await call('/mail/events',event,'wrong')).status,401);
assert.equal((await call('/mail/events',{...event,automation:{event:'workflow_dispatch',attempt:1,caller:'cloudflare-cron'}})).status,403);
assert.equal((await call('/mail/events',{...event,automation:{event:'schedule',attempt:2}})).status,403);
let a=await call('/mail/events',event);assert.equal(a.status,202);
const first=await a.json();
const second=await (await call('/mail/events',{...event,lines:['보완된 내용']})).json();
assert.equal(first.event_id,second.event_id,'내용이 바뀌어도 같은 날 같은 회차는 한 번');
const sent=[];
globalThis.fetch=async(url,init)=>{sent.push({url,...init,body:JSON.parse(init.body)});return new Response(JSON.stringify({id:'fake-provider-id'}),{status:200});};
await worker.processMail({...env,MAIL_ENABLED:'0'},new Date(),async()=>{});assert.equal(sent.length,0);
await worker.processMail(env,new Date(),async()=>{});
assert.equal(sent.length,1);assert.deepEqual(sent[0].body.to,['two@example.com'],'한 종목 구독자는 그 보고서가 준비되면 받는다');
const peer={...event,target:'sk_hynix',url:'https://namyikim.github.io/predict_stock/sk_hynix/',lines:['하이닉스 요약']};
await call('/mail/events',peer);
await Promise.all([worker.processMail(env,new Date(),async()=>{}),worker.processMail(env,new Date(),async()=>{})]);
assert.equal(sent.length,2);
let morning=sent.find(x=>x.body.to[0]==='one@example.com');
assert.equal(morning.body.from,'owner@example.com');
assert.ok(morning.body.html.includes('삼성전자')&&morning.body.html.includes('SK하이닉스'));
assert.ok(morning.body.html.includes('&lt;script&gt;'));assert.ok(!morning.body.html.includes('<script>'));
await call('/mail/events',{...peer,lines:['나중 보완']});
await worker.processMail(env,new Date(),async()=>{});assert.equal(sent.length,2);
// 오후에는 변경한 발신 주소로 두 종목을 한 통에 담는다.
clock=Date.parse('2026-10-06T07:00:00Z');
await call('/mail/settings',{from_email:'new-owner@example.com'},'admin-secret');
await call('/mail/events',{...event,phase:'post_close'});await call('/mail/events',{...peer,phase:'post_close'});
const retried=[];
globalThis.fetch=async(url,init)=>{
 const body=JSON.parse(init.body);retried.push({body,key:init.headers['Idempotency-Key']});
 if(body.to[0]==='two@example.com' && retried.filter(x=>x.body.to[0]==='two@example.com').length===1) throw new Error('응답 유실');
 return new Response(JSON.stringify({id:'accepted'}),{status:200});
};
await worker.processMail(env,new Date(),async()=>{});
clock+=6*60000;
await call('/mail/settings',{from_email:'later-owner@example.com'},'admin-secret');
await worker.processMail(env,new Date(),async()=>{});
const tries=retried.filter(x=>x.body.to[0]==='two@example.com');
assert.equal(tries.length,2);assert.deepEqual(tries[0],tries[1],'재시도는 발신 주소 변경 후에도 같은 요청을 유지');
assert.equal(tries[0].body.from,'new-owner@example.com');
assert.equal(retried.filter(x=>x.body.to[0]==='one@example.com').length,1);
await call('/mail/events',{...event,phase:'post_close',lines:['저녁 수정']});
await worker.processMail(env,new Date(),async()=>{});assert.equal(retried.length,3,'세 번째 메일은 없다');
const status=await (await call('/mail/status',undefined,'admin-secret','GET')).json();
assert.ok(!JSON.stringify(status).includes('one@example.com'));
assert.equal(status.max_per_day,2);
const token=db.prepare('SELECT token FROM mail_unsubscribe WHERE email=?').get('one@example.com').token;
assert.equal((await call('/mail/unsubscribe?token='+token,undefined,'','GET')).status,200);
assert.equal(db.prepare('SELECT count(*) n FROM subscribers WHERE email=?').get('one@example.com').n,2);
const stop=await worker.default.fetch(new Request('https://counter.example/mail/unsubscribe?token='+token,{method:'POST',body:'confirm=1'}),env);
assert.equal(stop.status,200);assert.equal(db.prepare('SELECT count(*) n FROM subscribers WHERE email=?').get('one@example.com').n,0);
assert.equal(db.prepare('SELECT count(*) n FROM mail_deliveries WHERE email=?').get('one@example.com').n,0);
// 지난 회차가 다음 한국 날짜나 장 시작 뒤로 넘어가 발송되지 않는다.
clock=Date.parse('2026-10-07T06:40:00Z');
let proof=await worker.mailAutomationProof(env,'afternoon-report.yml');
const auto={event:'workflow_dispatch',attempt:1,caller:'cloudflare-cron',proof};
assert.equal((await call('/mail/events',{...event,session_date:'2026-10-07',phase:'post_close',automation:{...auto,proof:proof+'0'}})).status,403);
assert.equal((await call('/mail/events',{...event,session_date:'2026-10-07',phase:'post_close',automation:auto})).status,202);
assert.equal((await call('/mail/events',{...event,session_date:'2026-10-06',phase:'post_close'})).status,200);
// 배치 시작 후 자정을 넘기면 아직 보내지 않은 수신자에게 전날 대기를 이월하지 않는다.
add('a-boundary@example.com','samsung');add('b-boundary@example.com','samsung');
clock=Date.parse('2026-10-07T14:59:59Z');
await call('/mail/events',{...event,session_date:'2026-10-07',phase:'post_close'});
// 이 회차의 구독은 최초 접수 전부터 있었다.
db.prepare("UPDATE subscribers SET ts='2026-10-07T06:00:00Z' WHERE email LIKE '%boundary%'").run();
let boundary=[];
globalThis.fetch=async(url,init)=>{boundary.push(JSON.parse(init.body).to[0]);clock+=2000;return new Response(JSON.stringify({id:'boundary'}),{status:200});};
await worker.processMail(env,new Date(),async()=>{});
assert.equal(boundary.length,1,'자정이 지나면 남은 전날 회고는 보내지 않는다');
await worker.processMail(env,new Date(),async()=>{});assert.equal(boundary.length,1);
// 다음날 오전도 09시를 넘기면 남은 대기가 만료된다.
clock=Date.parse('2026-10-07T23:59:59Z');
await call('/mail/events',{...event,session_date:'2026-10-08'});
boundary=[];await worker.processMail(env,new Date(),async()=>{});assert.equal(boundary.length,1);
await worker.processMail(env,new Date(),async()=>{});assert.equal(boundary.length,1);
// 옛 버전에서 이미 시도한 같은 날 회차는 새 묶음으로 다시 보내지 않는다.
clock=Date.parse('2026-10-08T07:00:00Z');
await call('/mail/events',{...event,session_date:'2026-10-08',phase:'post_close'});
db.prepare("INSERT INTO mail_events VALUES ('legacy','samsung','post_close','2026-10-08','{}','old',?)").run(new Date().toISOString());
for(const email of ['a-boundary@example.com','b-boundary@example.com','two@example.com'])
 db.prepare("INSERT INTO mail_deliveries(event_id,email,payload,status,attempts,created_at) VALUES ('legacy',?,'{}','sent',1,?)").run(email,new Date().toISOString());
boundary=[];await worker.processMail(env,new Date(),async()=>{});assert.equal(boundary.length,0);
// 새 가입은 다음 회차부터, 부분 해지는 재시도 전에 반영한다.
clock=Date.parse('2026-10-08T22:00:00Z');
add('partial@example.com','samsung');add('partial@example.com','sk_hynix');
await call('/mail/events',{...event,session_date:'2026-10-09'});
await call('/mail/events',{...peer,session_date:'2026-10-09'});
clock+=2000;add('late@example.com','samsung');
let partialAttempts=0,lateAttempts=0;
globalThis.fetch=async(url,init)=>{
 const to=JSON.parse(init.body).to[0];
 if(to==='late@example.com')lateAttempts++;
 if(to==='partial@example.com'){partialAttempts++;throw new Error('응답 유실');}
 return new Response(JSON.stringify({id:'ok'}),{status:200});
};
await worker.processMail(env,new Date(),async()=>{});
assert.equal(partialAttempts,1);assert.equal(lateAttempts,0);
assert.equal((await call('/subscribers/delete',{email:'partial@example.com',page:'samsung'},'admin-secret')).status,200);
clock+=6*60000;await worker.processMail(env,new Date(),async()=>{});
assert.equal(partialAttempts,1,'부분 해지 후 동결된 묶음 메일은 재시도하지 않는다');
assert.equal(db.prepare("SELECT status FROM mail_deliveries WHERE email='partial@example.com'").get().status,'cancelled');
// 수동 관리자 시험에는 메일용 서명을 붙이지 않고 실제 Cron에만 붙인다.
const dispatches=[];
globalThis.fetch=async(url,init)=>{dispatches.push(JSON.parse(init.body));return new Response(null,{status:204});};
const dispatchEnv={...env,MAIL_ENABLED:'0',GH_DISPATCH_TOKEN:'fake-github-token'};
await worker.default.fetch(new Request('https://counter.example/dispatch/test',{method:'POST',headers:{Authorization:'Bearer admin-secret'}}),dispatchEnv);
assert.ok(!dispatches.at(-1).inputs.mail_proof);
await worker.default.scheduled({scheduledTime:Date.parse('2026-10-08T21:20:00Z')},dispatchEnv);
assert.ok(dispatches.at(-1).inputs.mail_proof);
await worker.default.scheduled({scheduledTime:Date.parse('2026-10-08T21:20:00Z')},{...dispatchEnv,DISPATCH_FORCE:'1'});
assert.ok(!dispatches.at(-1).inputs.mail_proof);
// 발신 주소를 비우면 환경변수의 옛 주소로 돌아가지 않고 발송을 중단한다.
await call('/mail/settings',{from_email:''},'admin-secret');
settings=await (await call('/mail/status',undefined,'admin-secret','GET')).json();assert.equal(settings.configured,false);
console.log('관리자 발신 주소·하루 두 회차·종목 묶음·수동 차단·동시 실행·재시도·해지 통과');
