/* 카카오 본인 인증 화면(K2). 토큰 원문은 Worker에만 보관한다. */
(function () {
  'use strict';
  var ENDPOINT = 'https://predict-stock-counter.kimname1.workers.dev';
  var $ = function (id) { return document.getElementById(id); };
  if (!$('kakao-load')) return;
  var pending = null, busy = false, lastStatus = null;
  var buttons = ['kakao-load','kakao-connect','kakao-confirm','kakao-refresh','kakao-disconnect','kakao-acknowledge'];
  var labels = {disconnected:'미연결',connected:'연결됨',reconnect_required:'다시 로그인 필요',verification_required:'권한 재확인 필요',schema_required:'D1 설정 필요',revoking:'앱 동의 해제 진행 중',revoke_uncertain:'앱 동의 해제 결과 확인 필요'};
  var errors = {login_cancelled:'로그인을 취소했습니다.',app_mismatch:'앱 ID를 확인하세요.',owner_mismatch:'기존에 확인한 계정으로 로그인하세요.',permission_required:'카카오 메시지 전송 동의가 필요합니다.',temporarily_unavailable:'카카오 인증 응답을 받지 못했습니다. 다시 시도하세요.',rate_limited:'카카오 요청 제한입니다. 잠시 뒤 다시 확인하세요.',reconnect_required:'다시 로그인하세요.',provider_error:'카카오 앱 설정과 Client Secret을 확인하세요.',refresh_expired:'다시 로그인하세요.',revoke_uncertain:'카카오계정의 연결된 서비스에서 앱 동의 해제를 확인하세요.'};
  function token() { return $('kakao-token').value.trim() || $('sub-token').value.trim() || $('token').value.trim(); }
  function message(text) { $('kakao-action').textContent = text; }
  function setBusy(value) { busy = value; buttons.forEach(function (id) { $(id).disabled = value; }); if (!value) { if (lastStatus) render(lastStatus);else buttons.slice(1).forEach(function (id) { $(id).disabled = true; }); } }
  async function api(path, body) {
    if (!token()) throw new Error('관리자 토큰을 먼저 입력하세요.');
    var init = {headers:{Authorization:'Bearer ' + token()},cache:'no-store'};
    if (body !== undefined) { init.method = 'POST'; init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    var r = await fetch(ENDPOINT + '/kakao/' + path, init);
    if (r.status === 401) throw new Error('관리자 토큰이 맞는지 확인하세요.');
    if (r.status === 404) throw new Error('Worker에 카카오 인증 코드가 없습니다. 최신 전체 코드를 배포하세요.');
    var data = await r.json();
    if (!r.ok) throw new Error(data.error || '카카오 연결 요청을 처리하지 못했습니다.');
    return data;
  }
  function render(data) {
    lastStatus = data;
    pending = data.pending || null;
    var text = labels[data.status] || '상태 확인 필요';
    if (data.available === false) text += ' · 카카오 D1 이행 SQL을 적용하세요.';
    else if (!data.configured) text += ' · Worker 설정 필요: ' + (data.missing || []).join(', ');
    else {
      if (data.owner_id) text += ' · 확인한 회원번호 ' + data.owner_id;
      if (pending) text += ' · 로그인한 회원번호 ' + pending.owner_id + ': 본인 계정을 확인한 뒤 아래 버튼을 누르세요.';
      if (data.last_error) text += ' · ' + (errors[data.last_error] || '연결 요청을 완료하지 못했습니다. 다시 연결하세요.');
    }
    // 응답 문자열을 HTML로 해석하지 않는다.
    $('kakao-status').textContent = text;
    $('kakao-connect').disabled = data.available === false || !data.configured || ['revoking','revoke_uncertain'].includes(data.status);
    $('kakao-confirm').disabled = !pending;
    $('kakao-refresh').disabled = !['connected','verification_required'].includes(data.status);
    $('kakao-disconnect').disabled = data.available === false || ['revoking','revoke_uncertain'].includes(data.status);
    $('kakao-acknowledge').disabled = data.status !== 'revoke_uncertain';
  }
  async function load() { render(await api('status')); }
  async function action(fn) {
    if (busy) return;
    setBusy(true);message('처리 중…');
    try { await fn();message(''); }
    catch (error) { message(error.message || '연결 상태를 확인하지 못했습니다.'); }
    finally { setBusy(false); }
  }
  $('kakao-load').addEventListener('click', function () { return action(load); });
  $('kakao-connect').addEventListener('click', function () { return action(async function () {
    var data = await api('connect', {}), url = new URL(data.login_url);
    if (url.origin !== ENDPOINT || url.pathname !== '/kakao/authorize' || !/^[a-f0-9]{64}$/.test(url.searchParams.get('ticket') || '')) throw new Error('연결 주소를 확인하지 못했습니다.');
    // Worker로 직접 이동해 쿠키를 설정한다. Pages의 제3자 쿠키 허용 여부에 의존하지 않는다.
    window.location.assign(url.href);
  }); });
  $('kakao-confirm').addEventListener('click', function () {
    if (!pending || !window.confirm('방금 로그인한 회원번호 ' + pending.owner_id + '가 본인 계정입니까? 이 계정으로 연결을 고정합니다.')) return;
    var candidate = pending;
    return action(async function () { await api('confirm', {pending_id:candidate.id,owner_id:candidate.owner_id});await load(); });
  });
  $('kakao-refresh').addEventListener('click', function () { return action(async function () {
    var data = await api('refresh', {});await load();
    if (data.status !== 'ready') throw new Error(errors[data.status] || '갱신 상태: ' + data.status);
  }); });
  $('kakao-disconnect').addEventListener('click', function () {
    var revoke = $('kakao-revoke').checked;
    if (!window.confirm(revoke ? '서버 토큰을 지우고 이 앱에 대한 카카오 동의도 해제합니다. 다시 사용하려면 로그인과 동의가 필요합니다.' : '카카오 자동 연결을 중지하고 서버 토큰을 지웁니다. 카카오 앱 동의는 유지됩니다.')) return;
    return action(async function () {
      var data = await api('disconnect', {revoke:revoke});await load();
      if (data.revoke_status === 'manual_required') throw new Error('서버 연결은 해제됐습니다. 카카오계정의 연결된 서비스에서 앱 동의 해제를 확인하세요.');
    });
  });
  $('kakao-acknowledge').addEventListener('click', function () {
    if (!window.confirm('카카오계정의 연결된 서비스에서 이 앱의 동의가 해제된 것을 직접 확인했습니까? 확인한 경우에만 다시 연결할 수 있습니다.')) return;
    return action(async function () { await api('disconnect', {acknowledge_revoke:true});await load(); });
  });
  try { var saved = window.localStorage.getItem('predict-stock-stats-token');if (saved) $('kakao-token').value = saved; } catch (ignored) {}
})();
