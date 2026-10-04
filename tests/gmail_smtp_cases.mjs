import assert from 'node:assert/strict';
import fs from 'node:fs';
const worker=await import('data:text/javascript;base64,'+fs.readFileSync('counter/worker.js').toString('base64'));
assert.equal(typeof worker.sendGmailSmtp,'function','Gmail SMTP 전송 구현 필요');
const env={GMAIL_USER:'sender@gmail.com',GMAIL_APP_PASSWORD:'abcd efgh ijkl mnop'};
const mail={from:'sender@gmail.com',to:['receiver@example.com'],subject:'한글 보고서',text:'한글 본문',html:'<p>한글 본문</p>',headers:{'List-Unsubscribe':'<https://counter.example/unsubscribe>'}};
function fakeSocket(failAt=''){
 let controller,steps=0,closed=false;
 const writes=[];
 const readable=new ReadableStream({start(c){controller=c;c.enqueue(new TextEncoder().encode('220 smtp ready\r\n'));}});
 const reply=s=>controller.enqueue(new TextEncoder().encode(s));
 const writable=new WritableStream({write(chunk){
  const data=new TextDecoder().decode(chunk);writes.push(data);steps++;
  if(failAt==='auth'&&steps===4){reply('535 bad credentials\r\n');return;}
  if(failAt==='data'&&steps===8){controller.close();return;}
  const replies=['250-smtp\r\n250 AUTH LOGIN\r\n','334 user\r\n','334 password\r\n','235 authenticated\r\n','250 sender\r\n','250 recipient\r\n','354 data\r\n','250 queued\r\n','221 bye\r\n'];
  if(replies[steps-1])reply(replies[steps-1]);
 }});
 return {socket:{readable,writable,opened:Promise.resolve(),closed:Promise.resolve(),close:async()=>{closed=true;}},writes,isClosed:()=>closed};
}
let mock=fakeSocket();
const connect=(address,options)=>{assert.deepEqual(address,{hostname:'smtp.gmail.com',port:465});assert.equal(options.secureTransport,'on');return mock.socket;};
const id=await worker.sendGmailSmtp(env,mail,'report-abc',connect);
assert.ok(id.includes('report-abc'));
assert.equal(mock.writes[0],'EHLO predict-stock.invalid\r\n');
assert.equal(mock.writes[3],btoa('abcdefghijklmnop')+'\r\n');
const mime=mock.writes[7];assert.ok(mime.includes('multipart/alternative'));assert.ok(mime.includes('=?UTF-8?B?'));
assert.ok(mime.includes(Buffer.from('한글 본문').toString('base64')));assert.ok(mime.endsWith('\r\n.\r\n'));assert.ok(mock.isClosed());
mock=fakeSocket('auth');await assert.rejects(worker.sendGmailSmtp(env,mail,'report-abc',connect),/smtp_535/);
mock=fakeSocket('data');await assert.rejects(worker.sendGmailSmtp(env,mail,'report-abc',connect),/smtp_closed/);
await assert.rejects(worker.sendGmailSmtp(env,{...mail,from:'other@gmail.com'},'id',connect),/gmail_sender/);
await assert.rejects(worker.sendGmailSmtp(env,{...mail,headers:{bad:'x\r\nBcc: stolen@example.com'}},'id',connect),/mail_header/);
console.log('Gmail TLS·SMTP 응답·한글 MIME·인증실패·응답유실·헤더 주입 차단 통과');
