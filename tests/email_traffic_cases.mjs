// 실제 SQLite로 Worker의 SQL·트랜잭션·이전 스키마 호환을 확인한다.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
const worker=await import('data:text/javascript;base64,'+fs.readFileSync('counter/worker.js').toString('base64'));
assert.equal(typeof worker.parseEmailAttribution,'function','유입 파서가 필요합니다');
const OriginalDate=Date;
let now=OriginalDate.parse('2026-10-07T00:00:00Z');
globalThis.Date=class extends OriginalDate {constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}};
const query='utm_source=email&utm_medium=report&edition_day=2026-10-07&edition_phase=pre_open';
const parse=q=>worker.parseEmailAttribution(new URL('https://counter.example/hit?'+q));
assert.deepEqual(parse(query),{source:'email',edition_day:'2026-10-07',edition_phase:'pre_open'});
for(const q of ['',query+'&utm_source=email',query+'&edition_phase=post_close',query.replace('2026-10-07','2026-02-30'),query.replace('2026-10-07','0000-01-01'),query.replace('pre_open','<script>'),query.replace('email','kakao'),query.replace('report','x'.repeat(5000))])assert.equal(parse(q),null,q);
assert.ok(parse(query.replace('2026-10-07','2024-02-29')));
function database({legacy=false,broken=false}={}) {
  const db=new DatabaseSync(':memory:');
  let schema=fs.readFileSync('counter/schema.sql','utf8');
  if(legacy)schema=schema.replace(/^\s*(source|edition_day|edition_phase)\s+TEXT[^\n]*\n/gm,'').replace(/,\s*\)/g,')');
  db.exec(schema);
  if(broken)db.exec('DROP TABLE counters');
  const api={db,prepare(sql){let values=[];return {sql,bind(...args){values=args;return this;},async first(){return db.prepare(sql).get(...values)||null;},async run(){return {meta:db.prepare(sql).run(...values)};},execute(){const statement=db.prepare(sql);if(statement.columns().length)return {results:statement.all(...values),meta:{changes:0}};return {results:[],meta:statement.run(...values)};}};},async batch(queries){db.exec('BEGIN');try{const out=queries.map(q=>q.execute());db.exec('COMMIT');return out;}catch(e){db.exec('ROLLBACK');throw e;}}};
  return api;
}
const environment=options=>({DB:database(options),VISITOR_SALT:'test-salt',STATS_TOKEN:'stats'});
const call=async(env,path,headers={})=>{
  const response=await worker.default.fetch(new Request('https://counter.example'+path,{headers:{Origin:'https://namyikim.github.io','User-Agent':'Mozilla/5.0','CF-Connecting-IP':'192.0.2.1',...headers}}),env,{});
  return {status:response.status,body:await response.json()};
};
const hit=(env,q=query,headers={})=>call(env,'/hit?page=samsung&'+q,headers);
const stats=(env,days='30')=>call(env,'/stats?days='+days,{Authorization:'Bearer stats'});
const rows=env=>env.DB.db.prepare('SELECT * FROM hits ORDER BY id').all();
const env=environment();
assert.equal((await hit(env,'')).body.total,1);
assert.equal((await hit(env)).body.counted,false);
assert.equal(rows(env).length,1);
assert.equal(rows(env)[0].source,'email');
await hit(env,query.replace('pre_open','post_close'));
assert.equal(rows(env)[0].edition_phase,'pre_open','첫 이메일 회차를 유지');
assert.equal((await stats(env)).body.emailTraffic.daily[0].views,1);
now+=10*60000;
assert.equal((await hit(env)).body.total,2);
let result=(await stats(env)).body;
assert.deepEqual(result.emailTraffic,{available:true,timezone:'UTC',daily:[{day:'2026-10-07',page:'samsung',views:2}],editions:[{edition_day:'2026-10-07',edition_phase:'pre_open',page:'samsung',views:2}],countries:[{country:'unknown',views:2}],devices:[{device:'unknown',views:2}]});
assert.equal((await call(env,'/stats')).status,401);
const bot=environment();await hit(bot,'');await hit(bot,query,{'User-Agent':'Googlebot'});
assert.equal(rows(bot)[0].source,'');
assert.equal((await hit(bot,query,{'Origin':'https://evil.example'})).body.counted,false);
assert.equal((await call(bot,'/hit?page=unknown&'+query)).status,400);
const malformed=environment();assert.equal((await hit(malformed,query.replace('pre_open','bad'))).body.total,1);assert.equal(rows(malformed)[0].source,'');
const concurrent=environment();
const pair=await Promise.all([hit(concurrent),hit(concurrent)]);
assert.equal(rows(concurrent).length,1,'동시 요청도 조회수는 하나');
assert.equal((await stats(concurrent)).body.totals[0].total,1);
assert.equal(pair.filter(r=>r.body.counted).length,1);
const late=environment();await hit(late,'');
await Promise.all([hit(late),hit(late,query.replace('pre_open','post_close'))]);
assert.equal(rows(late).length,1);assert.equal(rows(late)[0].edition_phase,'pre_open');
const old=environment({legacy:true});
assert.equal((await hit(old)).body.total,1);assert.equal((await hit(old)).body.counted,false);
assert.deepEqual((await stats(old)).body.emailTraffic,{available:false,timezone:'UTC',daily:[],editions:[],countries:[],devices:[]});
old.DB.db.exec(fs.readFileSync('counter/migrations/20261007_email_attribution.sql','utf8'));
assert.equal(rows(old)[0].source,'','과거 방문을 소급 분류하지 않음');
await hit(old);assert.equal(rows(old)[0].source,'email');
assert.equal((await stats(old)).body.emailTraffic.available,true);
const broken=environment({broken:true});assert.equal((await hit(broken)).status,500);assert.equal(rows(broken).length,0,'실패 batch는 전체 롤백');
now+=32*86400000;
assert.deepEqual((await stats(env)).body.emailTraffic.daily,[],'조회 기간 밖 제외');
assert.equal((await stats(env)).body.totals[0].total,2,'누적 합계 유지');
console.log('이메일 유입 파서·중복·동시 요청·통계·구 스키마 검증 통과');
// 이행 도중 컬럼 일부만 있는 DB도 기존 방문수를 유지한다.
const partial=environment({legacy:true});partial.DB.db.exec("ALTER TABLE hits ADD COLUMN source TEXT NOT NULL DEFAULT ''");
assert.equal((await hit(partial)).body.total,1);
assert.equal((await hit(partial)).body.counted,false);
assert.equal((await stats(partial)).body.emailTraffic.available,false);
// 통계의 DB 장애를 '자료 없음'으로 감추지 않는다.
const statsBroken=environment();statsBroken.DB.db.exec('DROP TABLE hits');
assert.equal((await stats(statsBroken)).status,500);
// 하루 경계에서는 기존 일별 해시/조회 정책을 그대로 따른다.
const midnight=environment();now=OriginalDate.parse('2026-10-07T23:59:59Z');await hit(midnight);
now+=2000;await hit(midnight);
assert.deepEqual((await stats(midnight)).body.emailTraffic.daily.map(r=>r.day),['2026-10-08','2026-10-07']);
assert.equal((await stats(midnight)).body.emailTraffic.editions[0].edition_day,'2026-10-07');

// 거시 보고서도 실제 허용 목록·저장 경로를 통과해야 한다.
const macroVisit=environment();
const macroHit=await call(macroVisit,'/hit?page=macro&'+query);
assert.equal(macroHit.status,200);
assert.equal(macroHit.body.total,1);
assert.equal(rows(macroVisit)[0].page,'macro');
assert.equal((await stats(macroVisit)).body.emailTraffic.daily[0].page,'macro');
