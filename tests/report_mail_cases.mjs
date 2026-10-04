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
const env={DB,MAIL_ENABLED:'1',RESEND_API_KEY:'fake-key',MAIL_FROM:'reports@example.com',MAIL_PUBLIC_URL:'https://counter.example',MAIL_PUBLISH_TOKEN:'publish-secret',STATS_TOKEN:'admin-secret'};
const now=new Date();
const day=new Date(now.getTime()+9*3600000).toISOString().slice(0,10);
const event={target:'samsung',phase:'pre_open',session_date:day,lines:['상승 60% · 보합 20% · 하락 20%','<script>원문</script>'],source_revision:'a'.repeat(40),url:'https://namyikim.github.io/predict_stock/samsung/'};
async function call(path,body,token='publish-secret',method='POST') {
 return worker.default.fetch(new Request('https://counter.example'+path,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})}),env);
}
db.prepare('INSERT INTO subscribers VALUES (?,?,?)').run('one@example.com','main',new Date(now-1000).toISOString());
db.prepare('INSERT INTO subscribers VALUES (?,?,?)').run('one@example.com','samsung',new Date(now-1000).toISOString());
db.prepare('INSERT INTO subscribers VALUES (?,?,?)').run('two@example.com','sk_hynix',new Date(now-1000).toISOString());
assert.equal((await call('/mail/events',event,'wrong')).status,401);
let a=await call('/mail/events',event); assert.equal(a.status,202);
let first=await a.json();
let second=await (await call('/mail/events',{...event,source_revision:'b'.repeat(40)})).json();
assert.equal(first.event_id,second.event_id,'게시 리비전만 바뀌면 중복 알림을 만들지 않는다');
const sent=[];
globalThis.fetch=async(url,init)=>{sent.push({url,...init,body:JSON.parse(init.body)});return new Response(JSON.stringify({id:'fake-provider-id'}),{status:200});};
await worker.processMail({...env,MAIL_ENABLED:'0'},new Date(),async()=>{});
assert.equal(sent.length,0,'비활성화 시 발송하지 않는다');
await Promise.all([worker.processMail(env,new Date(),async()=>{}),worker.processMail(env,new Date(),async()=>{})]);
assert.equal(sent.length,1,'중복 구독과 동시 실행에도 한 통만 보낸다');
assert.deepEqual(sent[0].body.to,['one@example.com']);
assert.ok(sent[0].body.html.includes('&lt;script&gt;'));
assert.ok(!sent[0].body.html.includes('<script>'));
await worker.processMail(env,new Date(),async()=>{});assert.equal(sent.length,1);
let status=await (await call('/mail/status',undefined,'admin-secret','GET')).json();
assert.ok(!JSON.stringify(status).includes('one@example.com'));
const token=db.prepare('SELECT token FROM mail_unsubscribe WHERE email=?').get('one@example.com').token;
const get=await call('/mail/unsubscribe?token='+token,undefined,'','GET');
assert.equal(get.status,200);assert.equal(db.prepare('SELECT count(*) n FROM subscribers WHERE email=?').get('one@example.com').n,2,'GET은 해지하지 않는다');
const stop=await worker.default.fetch(new Request('https://counter.example/mail/unsubscribe?token='+token,{method:'POST',body:'confirm=1'}),env);
assert.equal(stop.status,200);assert.equal(db.prepare('SELECT count(*) n FROM subscribers WHERE email=?').get('one@example.com').n,0);
assert.equal(db.prepare('SELECT count(*) n FROM mail_deliveries WHERE email=?').get('one@example.com').n,0,'해지 후 발송 자료의 이메일도 지운다');
assert.equal((await call('/mail/events',{...event,url:'https://evil.example'})).status,400);
console.log('메일 이벤트·수신 대상·중복·동시 실행·비활성화·해지 계약 통과');
// 접수 응답이 유실되어도 같은 요청 키·본문으로 재시도하며, 성공한 다른 수신자는 건드리지 않는다.
const old=new Date().toISOString();
db.prepare('INSERT INTO subscribers VALUES (?,?,?)').run('retry@example.com','samsung',old);
db.prepare('INSERT INTO subscribers VALUES (?,?,?)').run('success@example.com','samsung',old);
const changed=await (await call('/mail/events',{...event,lines:['새로운 회고 내용']})).json();
assert.notEqual(changed.event_id,first.event_id);
const retried=[];
globalThis.fetch=async(url,init)=>{
 const payload=JSON.parse(init.body);retried.push({key:init.headers['Idempotency-Key'],body:init.body,email:payload.to[0]});
 if(payload.to[0]==='retry@example.com' && retried.filter(r=>r.email===payload.to[0]).length===1) throw new Error('응답 유실');
 return new Response(JSON.stringify({id:'accepted'}),{status:200});
};
await worker.processMail(env,new Date(),async()=>{});
const retryRow=db.prepare('SELECT * FROM mail_deliveries WHERE email=? AND event_id=?').get('retry@example.com',changed.event_id);
assert.equal(retryRow.status,'pending');
await worker.processMail(env,new Date(Date.now()+6*60000),async()=>{});
const attempts=retried.filter(x=>x.email==='retry@example.com');
assert.equal(attempts.length,2);assert.equal(attempts[0].key,attempts[1].key);assert.equal(attempts[0].body,attempts[1].body);
assert.equal(retried.filter(x=>x.email==='success@example.com').length,1);
// 이전 이벤트는 새 가입자에게 소급 발송하지 않는다.
db.prepare('INSERT INTO subscribers VALUES (?,?,?)').run('new@example.com','samsung',new Date(Date.now()+1000).toISOString());
await worker.processMail(env,new Date(Date.now()+7*60000),async()=>{});
assert.ok(!retried.some(x=>x.email==='new@example.com'));
// 재시도 가능 기간을 넘긴 미접수 메일을 뒤늦게 발송하지 않는다.
db.prepare("UPDATE mail_deliveries SET status='pending',next_attempt='' WHERE email=?").run('retry@example.com');
const before=retried.length;
await worker.processMail(env,new Date(Date.now()+24*3600000),async()=>{});
assert.equal(retried.length,before);
console.log('부분 실패·같은 요청 재시도·소급 발송 차단·만료 계약 통과');
