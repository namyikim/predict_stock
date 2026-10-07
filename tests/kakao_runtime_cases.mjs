// 실제 workerd Request로 운영 TypeError와 리다이렉트 정책을 검증한다. 외부 통신은 하지 않는다.
export default {
  async test(ctrl, env) {
    const check = (value, message) => { if (!value) throw new Error(message); };
    let calls = 0;
    globalThis.fetch = async (url, init) => {
      const request = new Request(url, init);
      check(request.redirect === 'manual', '인증 요청은 리다이렉트를 따라가지 않아야 한다');
      calls++;
      return env.PROVIDER.fetch(request);
    };
    const init = {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:'client_id=dummy&code=dummy'};
    const response = await kakaoCall('https://kauth.kakao.com/oauth/token', init);
    check(response.ok && response.data.ok, '실제 Cloudflare 요청 옵션이 토큰 호출을 차단하지 않아야 한다');
    for (const status of [301,302,303,307,308]) {
      const before = calls;
      const result = await kakaoCall('https://kauth.kakao.com/oauth/token', {...init, headers:{...init.headers,'X-Test-Status':String(status)}});
      check(!result.ok && result.status === 'provider_error', '리다이렉트를 JSON 오류가 아닌 외부 응답 오류로 거절한다');
      check(calls === before + 1, '리다이렉트 목적지에는 인증 값을 보내지 않는다');
    }
    console.log('실제 Cloudflare 런타임 토큰 요청·리다이렉트 거절 검증 통과');
  }
};
