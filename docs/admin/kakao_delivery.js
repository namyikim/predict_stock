/* 카카오 본인 발송 제어. 시험 내용을 확인한 클릭만 전송 요청으로 보낸다. */
(function () {
  'use strict';
  var $=function(id){return document.getElementById(id);};
  if(!$('kakao-delivery-load'))return;
  var base='https://predict-stock-counter.kimname1.workers.dev/kakao/',busy=false,connection=null,delivery=null,requestId=null;
  var buttons=['kakao-delivery-load','kakao-delivery-test','kakao-delivery-enable','kakao-delivery-disable'];
  var states={pending:'재시도 대기',sending:'전송 중',sent:'전송 접수 성공',failed:'실패',uncertain:'확인 필요',cancelled:'취소됨',blocked:'인증 확인 필요',not_claimed:'다른 요청 처리 중'};
  var reasons={rate_limited:'카카오 요청 한도',daily_limit:'카카오 하루 메시지 한도 초과',response_unknown:'전송 결과 불명확 · 자동 재전송 안 함',reconnect_required:'다시 로그인 필요',permission_required:'메시지 동의 확인 필요',provider_error:'카카오 앱·제품 링크 도메인 설정 확인',unexpected_redirect:'예상하지 않은 카카오 응답',control_changed:'자동 알림 설정 변경',edition_expired:'발송 시간 종료',configuration_required:'Worker 설정 확인',connection_changed:'연결 상태 변경',busy:'인증 갱신 중'};
  function token(){return $('kakao-token').value.trim() || $('sub-token').value.trim() || $('token').value.trim();}
  async function api(path,body){
    if(!token())throw new Error('관리자 토큰을 먼저 입력하세요.');
    var init={headers:{Authorization:'Bearer '+token()},cache:'no-store'};
    if(body!==undefined){init.method='POST';init.headers['Content-Type']='application/json';init.body=JSON.stringify(body);}
    var response=await fetch(base+path,init);
    if(response.status===404)throw new Error('Worker에 발송 기능이 없습니다. 최신 Worker 전체 코드를 배포하세요.');
    if(response.status===401)throw new Error('관리자 토큰을 확인하세요.');
    var data=await response.json();if(!response.ok)throw new Error(data.error || '발송 상태를 확인하지 못했습니다.');
    return data;
  }
  function render(){
    var available=delivery && delivery.available,connected=connection && connection.configured && ['connected','verification_required'].includes(connection.status);
    buttons.forEach(function(id){$(id).disabled=busy;});
    $('kakao-delivery-test').disabled=busy || !available || !connected;
    $('kakao-delivery-enable').disabled=busy || !available || !connected || connection.status!=='connected' || delivery.enabled;
    $('kakao-delivery-disable').disabled=busy || !available || !delivery.enabled;
    if(!delivery)return;
    $('kakao-delivery-status').textContent=!available?'카카오 발송 D1 설정이 필요합니다.':
      (delivery.enabled?'자동 보고서 알림 켜짐':'자동 보고서 알림 꺼짐')+' · 켠 이후 게시된 개장 전·마감 후 회차를 각 한 건으로 보냅니다.';
    $('kakao-delivery-preview').textContent=delivery.preview?delivery.preview.text+'\n'+delivery.preview.link.web_url:'';
    $('kakao-delivery-history').textContent=(delivery.deliveries || []).map(function(r){
      return r.day+' · '+(r.kind==='test'?'시험 메시지':r.phase==='pre_open'?'개장 전':'마감 후')+' · '+(states[r.status] || '상태 확인 필요')+
        (r.error_code?' · '+(reasons[r.error_code] || '인증 상태와 설정을 확인하세요.'):'');
    }).join('\n') || '아직 발송 기록이 없습니다.';
  }
  async function load(){connection=await api('status');delivery=await api('delivery-status');render();}
  async function action(fn){
    if(busy)return;busy=true;render();$('kakao-delivery-action').textContent='처리 중…';
    try{var message=await fn();$('kakao-delivery-action').textContent=message || '';}
    catch(error){connection=null;delivery=null;$('kakao-delivery-action').textContent=error.message || '요청 결과를 확인하지 못했습니다.';}
    finally{busy=false;render();}
  }
  $('kakao-delivery-load').addEventListener('click',function(){return action(load);});
  $('kakao-delivery-test').addEventListener('click',function(){
    if(busy || !connection || !delivery || !delivery.available || !delivery.preview)return;
    if(!window.confirm('확인한 회원번호 '+connection.owner_id+'의 나와의 채팅에 아래 시험 메시지 한 건을 보냅니다.\n\n'+delivery.preview.text+'\n\n보내시겠습니까?'))return;
    // 응답 유실 뒤 같은 버튼을 다시 눌러도 동일 요청 키를 사용한다.
    requestId=requestId || crypto.randomUUID();
    return action(async function(){
      var data=await api('test',{confirmed:true,request_id:requestId});
      if(['sent','failed','uncertain','cancelled','outside_session'].includes(data.status))requestId=null;
      await load();
      return data.status==='sent'?'카카오 전송 접수가 성공했습니다. 나와의 채팅에서 메시지를 확인하세요.':
        (states[data.status] || '상태 확인 필요')+' · '+(reasons[data.error_code || data.reason] || '최근 발송 기록을 확인하세요.')+
        (['blocked','pending','sending','not_claimed'].includes(data.status)?' · 잠시 뒤 시험 메시지 보내기를 다시 눌러 확인하세요.':'');
    });
  });
  function control(enabled){
    if(enabled && !window.confirm('지금 이후 게시되는 삼성전자·SK하이닉스 보고서를 본인의 나와의 채팅으로 자동 발송합니다. 개장 전·마감 후 각 한 건입니다. 켜시겠습니까?'))return;
    return action(async function(){await api('control',{enabled:enabled});await load();return enabled?'자동 보고서 알림을 켰습니다.':'자동 보고서 알림을 껐습니다. 이미 전송을 시작한 메시지는 회수할 수 없습니다.';});
  }
  $('kakao-delivery-enable').addEventListener('click',function(){return control(true);});
  $('kakao-delivery-disable').addEventListener('click',function(){return control(false);});
  render();
})();
