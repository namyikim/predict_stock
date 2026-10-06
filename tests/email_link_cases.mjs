import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
const w=await import('data:text/javascript;base64,'+fs.readFileSync('counter/worker.js').toString('base64'));
assert.equal(typeof w.emailReportUrl,'function','메일 링크 표시 함수가 필요합니다');
const base='https://namyikim.github.io/predict_stock/';
const input=base+'samsung/?keep=one%20two&utm_source=old&utm_source=twice#forecast';
const link=w.emailReportUrl(input,'2026-10-07','pre_open');
const url=new URL(link);
assert.equal(url.searchParams.get('keep'),'one two');assert.equal(url.hash,'#forecast');
assert.equal(url.searchParams.getAll('utm_source').length,1);
assert.equal(url.searchParams.get('utm_source'),'email');assert.equal(url.searchParams.get('utm_medium'),'report');
assert.equal(url.searchParams.get('edition_day'),'2026-10-07');assert.equal(url.searchParams.get('edition_phase'),'pre_open');
assert.equal(w.emailReportUrl(link,'2026-10-07','pre_open'),link,'재적용해도 중복 없음');
assert.throws(()=>w.emailReportUrl(base+'samsung/','2026-02-30','pre_open'));
assert.throws(()=>w.emailReportUrl(base+'samsung/','2026-10-07','bad'));
assert.throws(()=>w.emailReportUrl('https://evil.example/','2026-10-07','pre_open'));
const db=new DatabaseSync(':memory:');db.exec(fs.readFileSync('counter/schema.sql','utf8'));
function stmt(sql,args=[]){return {bind:(...v)=>stmt(sql,v),run:async()=>db.prepare(sql).run(...args),first:async()=>db.prepare(sql).get(...args)};}
const env={DB:{prepare:sql=>stmt(sql)},MAIL_PUBLIC_URL:'https://counter.example',MAIL_FROM:'owner@example.com'};
const keys=['samsung','sk_hynix','metals','china','macro','ai_news','robot_news','trends','interest'];
for(const phase of ['pre_open','post_close']){
 const content={day:'2026-10-07',phase,reports:keys.slice(0,2).map(target=>({target,lines:['요약 <태그>'],url:base+target+'/'})),
   site_reports:keys.slice(2).map(key=>({key,title:key,lines:['다른 메뉴'],as_of:'2026-10-06',url:base+key+'/'}))};
 const event={content:JSON.stringify(content)},before=event.content;
 const payload=await w.mailPayload(env,event,'recipient@example.com');
 assert.equal(event.content,before,'저장 이벤트를 변경하지 않음');
 const htmlLinks=[...payload.html.matchAll(/href="([^"]+)"/g)].map(m=>m[1].replaceAll('&amp;','&'));
 const textLinks=payload.text.split('\n').filter(s=>s.startsWith('전체 보고서: ')).map(s=>s.slice('전체 보고서: '.length));
 assert.equal(textLinks.length,keys.length);
 for(const [i,key] of keys.entries()){
  const expected=w.emailReportUrl(base+key+'/',content.day,phase);
  assert.equal(textLinks[i],expected);assert.equal(htmlLinks[i],expected);
  assert.deepEqual(w.parseEmailAttribution(new URL(expected)),{source:'email',edition_day:content.day,edition_phase:phase});
  assert.ok(!expected.includes('recipient')&&!expected.includes('token'));
 }
 const token=db.prepare('SELECT token FROM mail_unsubscribe WHERE email=?').get('recipient@example.com').token;
 const cancel='https://counter.example/mail/unsubscribe?token='+encodeURIComponent(token);
 assert.equal(htmlLinks.at(-1),cancel);assert.ok(payload.text.includes('구독 취소: '+cancel));
 assert.equal(payload.headers['List-Unsubscribe'],'<'+cancel+'>');
 assert.equal(payload.headers['List-Unsubscribe-Post'],'List-Unsubscribe=One-Click');
 assert.ok(payload.html.includes('&lt;태그&gt;'));assert.ok(!payload.html.includes('recipient@example.com'));
}
console.log('HTML·텍스트 전체 보고서 링크·회차·취소 URL 보존 검증 통과');
