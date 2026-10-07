// 조회수 카운터 Worker
//
// GitHub Pages는 정적 호스팅이라 서버 쪽에서 접속을 셀 수 없다. 이 Worker가 그
// 역할을 맡아 조회를 D1에 기록하고 누적 조회수를 돌려준다. 외부 카운터 서비스에
// 의존하지 않으므로 서비스가 사라질 걱정이 없고 원본 데이터도 저장소 소유자에게 남는다.
//
// 엔드포인트
//   GET /hit?page=<키>   조회 1건을 기록하고 그 페이지의 누적 조회수를 돌려준다.
//   GET /stats?days=30   최근 방문 통계를 JSON으로 돌려준다(비공개).
//                        헤더 `Authorization: Bearer <STATS_TOKEN>` 필요. URL에 토큰을
//                        싣지 않는다 — 쿼리스트링은 로그와 브라우저 기록에 남는다.
//   GET /geo             호출자 자신의 대략적인 위치만 돌려준다(점검용).
//   POST /subscribe      종목 보고서 상단의 구독 신청. 본문 {email, page}. 허용 출처에서만 받는다.
//   POST /unsubscribe    구독 해지. 본문 {email, page}. 신청과 같은 응답을 돌려준다(가입 여부를 알려 주지 않는다).
//   GET /subscribers     구독자 목록(비공개, /stats 와 같은 STATS_TOKEN).
//   POST /subscribers/delete  관리자가 한 건을 지운다(STATS_TOKEN). 본문 {email, page}.
//   POST /mail/events    보고서 게시 완료 요약 등록(MAIL_PUBLISH_TOKEN).
//   GET/POST /mail/settings  발신 주소 조회·변경(STATS_TOKEN).
//   GET /mail/status     발송 설정·집계(STATS_TOKEN). 수신 주소는 포함하지 않는다.
//   GET /mail/deliveries  관리자 수신자별 이력(50명씩). 본문·토큰 제외.
//   POST /mail/control, /mail/cancel  자동 발송 일시 중지·회차 대기 취소(STATS_TOKEN).
//   GET/POST /mail/unsubscribe  메일 내 토큰 링크. GET 확인 후 POST로 전체 해지.
//   POST /dispatch/test  관리자가 GitHub 채점 워크플로 호출을 한 번 시험한다(STATS_TOKEN). 결과 JSON 을 바로 돌려줘
//                        Cloudflare 로그를 열지 않아도 토큰·권한 문제를 알 수 있다(2026-10-01).
//
// Cron Trigger(대시보드 Settings → Triggers → Cron Triggers, 예: `0 3 * * *`)를 걸면
// 아래 scheduled()가 매일 오래된 조회 기록을 지운다(보관기간 관리).
// `37 0`·`10 7`·`45 7`·`30 8 * * MON-FRI`(UTC · 요일은 이름으로 — 숫자 1-5 는 Cloudflare 에서 일~목) 트리거를 더 걸고 GH_DISPATCH_TOKEN(또는 GITHUB_DISPATCH_TOKEN)을 넣으면 같은
// scheduled()가 그 시각에 GitHub의 채점 워크플로를 정시에 깨운다(README "채점 워크플로 정시 호출").
//
// 바인딩(대시보드 Settings에서 설정)
//   DB                    D1 데이터베이스
//   VISITOR_SALT          방문자 해시용 비밀값(시크릿)
//   STATS_TOKEN           /stats 접근 토큰(시크릿)
//   GH_DISPATCH_TOKEN (선택) 채점 워크플로를 깨우는 fine-grained PAT(시크릿). 이름은
//                     GITHUB_DISPATCH_TOKEN 이어도 된다 — 둘 중 있는 쪽을 읽는다.
//                         이 저장소 하나, Actions: Read and write 권한만. 없으면 깨우지 않는다.

// 이 오리진에서 온 요청만 집계한다. 열어두면 아무 사이트나(혹은 curl 반복문이) 우리
// 카운터를 올릴 수 있다. CORS 헤더는 브라우저의 *읽기*만 막을 뿐 요청 자체는 막지
// 못하므로, 집계 전에 Origin/Referer를 직접 확인한다(countable 참고).
const ALLOWED_ORIGINS = ["https://namyikim.github.io"];

// 같은 방문자가 같은 페이지를 이 시간 안에 다시 열면 세지 않는다. 새로고침 연타나
// 스크립트 반복 호출이 숫자를 부풀리는 것을 막는다.
const DEDUPE_MINUTES = 10;

// 조회 기록 보관 일수. 일별 통계용이라 이보다 오래된 행은 필요 없다.
const RETENTION_DAYS = 400;

// 페이지 키도 화이트리스트로 고정한다. 임의 키를 허용하면 남이 테이블을 부풀릴 수 있다.
const ALLOWED_PAGES = ["main", "samsung", "sk_hynix", "china", "metals", "ai_news",
                       "trends", "interest", "robot_news", "macro"];   // 이메일 도착 페이지 전체 집계(2026-10-07)

// 구독을 받는 페이지. 종목 보고서 두 곳과 메인 페이지에 버튼이 있다(2026-09-28 요청).
const SUBSCRIBE_PAGES = ["main", "samsung", "sk_hynix"];
// 같은 방문자(하루 단위 해시)가 하루에 보낼 수 있는 신청·해지 수. 스크립트로 목록을 채우는 것을 막는다.
const SUBSCRIBE_DAILY_LIMIT = 10;
// 형식만 본다. 실제로 받을 수 있는 주소인지는 메일을 보내 보기 전에는 알 수 없다.
const EMAIL_PATTERN = /^[^\s@<>()\[\],;:"]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$/;

// 크롤러는 사람의 조회가 아니므로 세지 않는다. 완벽한 판별은 불가능하고, 목적은
// 검색엔진·모니터링 봇이 만드는 명백한 과다 집계를 걷어내는 것이다.
const BOT_PATTERN = /bot|crawler|spider|crawling|slurp|facebookexternalhit|preview|monitor|curl|wget|python-requests|headless/i;

// 채점 워크플로 정시 호출. GitHub의 cron은 이 저장소에서 예정보다 4~5시간 늦게 실행을 만들고
// (09:37 회차가 14:10, 16:10 회차가 21:10 — 2026-09-08~10 사흘 모두) 하루 23개 중 절반은 아예
// 만들어지지 않았다. Cloudflare의 Cron Trigger는 분 단위로 정확하므로, 아래 시각(UTC)의 트리거가
// 오면 GitHub workflow_dispatch API로 채점 워크플로를 시작한다. 워크플로 쪽은 도착 시각으로 할 일을
// 정하고 이미 한 일은 건너뛰므로(tools/should_score_now.py) GitHub cron과 겹쳐도 해가 없다.
const DISPATCH_REPO = "namyikim/predict_stock";
const DISPATCH_WORKFLOW = "afternoon-report.yml";
// 09:37 KST 시가 채점 · 16:10 KST 마감 채점+회고 · 16:45·17:30 KST 재시도(네이버 투자자별 매매가 16:10 에는
// 아직 없어 회고의 '누가 팔고 샀나'가 비었다 — 2026-10-01). 이미 끝난 회차는 워크플로가 몇 초 만에 건너뛴다.
const DISPATCH_TIMES_UTC = [[0, 37], [7, 10], [7, 45], [8, 30]];
const DISPATCH_TOLERANCE_MINUTES = 3;            // 트리거가 몇 분 밀려 와도 같은 회차로 본다

// 이 트리거 시각이 채점 회차인가. 워크플로의 cron과 같이 월~금(UTC)만.
export function dispatchDue(scheduledTime, times = DISPATCH_TIMES_UTC) {
  const t = new Date(scheduledTime);
  const weekday = t.getUTCDay();                 // 0=일 … 6=토
  if (weekday === 0 || weekday === 6) return false;
  const minuteOfDay = t.getUTCHours() * 60 + t.getUTCMinutes();
  return times.some(([h, m]) => Math.abs(minuteOfDay - (h * 60 + m)) <= DISPATCH_TOLERANCE_MINUTES);
}

// 아침 종목 보고서 정시 호출(2026-10-02). GitHub 의 cron 이 아침 회차(06:22~07:52 KST, 11개)를 하나도 만들지 않은
// 날이 있었다 — 그날 보고서는 08:23 에 손으로 돌려서야 나왔다(9/30 은 08:17, 10/1 은 아침 기록 없음).
// 장 시작 전 예측은 09:00 을 넘기면 사전 예측으로 인정되지 않으므로, 채점처럼 이 Worker 가 정시에 깨운다.
// 06:20 KST 본 호출 · 07:00·07:40 KST 재시도. 워크플로는 오늘 예측이 이미 기록됐으면 몇 초 만에 건너뛰고,
// 재시도 회차에는 보조 보고서(뉴스·금은·중국 등)를 다시 만들지 않는다. GitHub cron 은 백업으로 그대로 둔다.
const MORNING_WORKFLOW = "daily-report.yml";
const MORNING_TIMES_UTC = [[21, 20], [22, 0], [22, 40]];   // 06:20 · 07:00 · 07:40 KST. 첫 시각이 본 호출이다.

// 이 트리거 시각이 아침 보고서 회차인가. 아니면 null, 맞으면 { retry }. KST 월~금 아침은 UTC 로 일~목 저녁이다.
export function morningDue(scheduledTime, times = MORNING_TIMES_UTC) {
  const t = new Date(scheduledTime);
  const weekday = t.getUTCDay();                 // 0=일 … 6=토
  if (weekday > 4) return null;                  // UTC 금·토 저녁 = KST 토·일 아침
  const minuteOfDay = t.getUTCHours() * 60 + t.getUTCMinutes();
  const slot = times.findIndex(([h, m]) => Math.abs(minuteOfDay - (h * 60 + m)) <= DISPATCH_TOLERANCE_MINUTES);
  return slot < 0 ? null : { retry: slot > 0 };
}

// GitHub에 workflow_dispatch 를 보낸다. 토큰이 없으면 아무것도 하지 않는다 — 조회수 카운터만 쓰는
// 배포도 그대로 동작해야 한다. 응답 본문은 기록하지 않는다(오류 문구에 토큰 정보가 섞일 수 있다).
async function dispatchScoring(env, automatic = false) {
  return dispatchWorkflow(env, DISPATCH_WORKFLOW, { caller: "cloudflare-cron" }, automatic);
}

async function dispatchWorkflow(env, workflow, inputs, automatic = false) {
  // 시크릿 이름은 두 가지를 다 받는다. 대시보드가 이름을 받아 주는 규칙이 화면마다 달라 한쪽만 고정하면
  // 이름 불일치로 조용히 skipped 가 된다(2026-09-10 실제로 그랬다).
  const token = env.GH_DISPATCH_TOKEN || env.GITHUB_DISPATCH_TOKEN;
  if (!token) return { status: "skipped", reason: "GH_DISPATCH_TOKEN/GITHUB_DISPATCH_TOKEN 없음" };
  if (automatic && env.MAIL_PUBLISH_TOKEN) inputs = { ...inputs, mail_proof: await mailAutomationProof(env, workflow) };
  const url = `https://api.github.com/repos/${DISPATCH_REPO}/actions/workflows/${workflow}/dispatches`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
      "User-Agent": "predict-stock-counter",   // GitHub API는 User-Agent 없는 요청을 거절한다
    },
    body: JSON.stringify({ ref: "main", inputs }),
  });
  return { status: response.status === 204 ? "dispatched" : "failed", code: response.status };
}

function corsHeaders(origin) {
  const allowed = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Vary": "Origin",
    // 카운터 응답이 CDN이나 브라우저에 캐시되면 숫자가 멈춘 것처럼 보인다.
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
  };
}

function json(body, origin, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: corsHeaders(origin) });
}

// 원본 IP는 저장하지 않는다. IP는 개인정보라 보관하면 처리방침·보관기간 관리가
// 따라붙는데, 재방문 구분에는 날짜별로 바뀌는 해시만 있으면 충분하다.
// 소금에 날짜를 섞으므로 같은 방문자라도 날짜가 바뀌면 다른 값이 되어
// 날짜를 넘는 추적이 불가능하다(= 일별 순방문자만 셀 수 있다).
async function visitorHash(request, salt, day) {
  const ip = request.headers.get("CF-Connecting-IP") || "";
  const ua = request.headers.get("User-Agent") || "";
  const material = `${salt}|${day}|${ip}|${ua}`;
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(material));
  return [...new Uint8Array(digest)]
    .slice(0, 8)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// Cloudflare가 IP로 추정해 붙여 주는 대략적인 위치. 정확도의 한계가 분명하다:
// 시/도까지는 대체로 맞지만, 도시는 통신사 NAT나 회사 회선 때문에 실제와 다를 수 있고
// 동 단위는 애초에 얻을 수 없다. 좌표도 도시 중심점이라 저장하지 않는다.
function geoOf(request) {
  const cf = request.cf || {};
  return {
    country: cf.country || "",
    region: cf.region || "",
    city: cf.city || "",
  };
}

// 유입경로는 도메인까지만 남긴다. 전체 URL에는 검색어 같은 게 붙어 오는 일이 있다.
function referrerHost(raw) {
  if (!raw) return "";
  try {
    const host = new URL(raw).hostname;
    return ALLOWED_ORIGINS.some((o) => o.endsWith(host)) ? "" : host;
  } catch {
    return "";
  }
}

// 브라우저의 교차 출처 fetch에는 Origin이 실린다. 없으면(대개 curl·스크립트)
// Referer로 한 번 더 본다. 둘 다 허용 목록 밖이면 세지 않는다.
function countable(request) {
  const origin = request.headers.get("Origin") || "";
  if (origin) return ALLOWED_ORIGINS.includes(origin);
  try {
    const ref = new URL(request.headers.get("Referer") || "");
    return ALLOWED_ORIGINS.includes(ref.origin);
  } catch {
    return false;
  }
}

async function currentTotal(env, page) {
  const row = await env.DB.prepare("SELECT total FROM counters WHERE page = ?").bind(page).first();
  return row ? row.total : 0;
}

// 이메일 회차만 저장한다. 임의 쿼리·개인 식별값은 저장하지 않는다(2026-10-07).
export function parseEmailAttribution(url) {
  const p=url.searchParams;
  const keys=['utm_source','utm_medium','edition_day','edition_phase'];
  if(keys.some(k=>p.getAll(k).length!==1))return null;
  const day=p.get('edition_day'),phase=p.get('edition_phase');
  if(p.get('utm_source')!=='email'||p.get('utm_medium')!=='report'||
     !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(day)||day.startsWith('0000')||
     !['pre_open','post_close'].includes(phase))return null;
  const stamp=Date.parse(day+'T00:00:00Z');
  if(!Number.isFinite(stamp)||new Date(stamp).toISOString().slice(0,10)!==day)return null;
  return {source:'email',edition_day:day,edition_phase:phase};
}

function missingAttributionColumns(error) {
  // D1 이행 전의 세 컬럼 부재만 호환 처리한다. DB 장애는 숨기지 않는다.
  const messages=[error?.message,error?.cause?.message].filter(Boolean).join(' ');
  return /no such column: (?:hits\.)?(?:source|edition_day|edition_phase)\b/i.test(messages)||
    /table hits has no column named (?:source|edition_day|edition_phase)\b/i.test(messages);
}

async function attachEmailAttribution(env, id, attribution) {
  if(!attribution)return;
  try {
    await env.DB.prepare("UPDATE hits SET source=?, edition_day=?, edition_phase=? WHERE id=? AND source='' ")
      .bind(attribution.source,attribution.edition_day,attribution.edition_phase,id).run();
  } catch(error) {if(!missingAttributionColumns(error))throw error;}
}

async function emailTrafficStats(env, since) {
  try {
    const [daily,editions,countries,devices]=await env.DB.batch([
      env.DB.prepare("SELECT day,page,COUNT(*) AS views FROM hits WHERE day >= ? AND source='email' GROUP BY day,page ORDER BY day DESC,page").bind(since),
      env.DB.prepare("SELECT edition_day,edition_phase,page,COUNT(*) AS views FROM hits WHERE day >= ? AND source='email' GROUP BY edition_day,edition_phase,page ORDER BY edition_day DESC,edition_phase,page").bind(since),
      // 이미 저장한 국가·UA로 분포만 계산한다. 개인 정보나 UA 원문은 응답하지 않는다.
      env.DB.prepare("SELECT CASE WHEN country GLOB '[A-Z][A-Z]' THEN country ELSE 'unknown' END AS country,COUNT(*) AS views FROM hits WHERE day >= ? AND source='email' GROUP BY 1 ORDER BY views DESC,country").bind(since),
      env.DB.prepare("SELECT CASE WHEN lower(ua) LIKE '%ipad%' OR lower(ua) LIKE '%tablet%' OR (lower(ua) LIKE '%android%' AND lower(ua) NOT LIKE '%mobile%') THEN 'tablet' WHEN lower(ua) LIKE '%mobile%' OR lower(ua) LIKE '%iphone%' THEN 'mobile' WHEN lower(ua) LIKE '%windows nt%' OR lower(ua) LIKE '%macintosh%' OR lower(ua) LIKE '%x11%' THEN 'desktop' ELSE 'unknown' END AS device,COUNT(*) AS views FROM hits WHERE day >= ? AND source='email' GROUP BY 1 ORDER BY views DESC,device").bind(since),
    ]);
    return {available:true,timezone:'UTC',daily:daily.results,editions:editions.results,countries:countries.results,devices:devices.results};
  } catch(error) {
    if(!missingAttributionColumns(error))throw error;
    return {available:false,timezone:'UTC',daily:[],editions:[],countries:[],devices:[]};
  }
}

async function handleHit(request, env, url, origin) {
  const page = url.searchParams.get("page") || "";
  if (!ALLOWED_PAGES.includes(page)) {
    return json({ error: "unknown page" }, origin, 400);
  }

  const ua = request.headers.get("User-Agent") || "";
  if (BOT_PATTERN.test(ua) || !countable(request)) {
    // 봇과 외부 호출에도 현재 숫자는 돌려주되 집계에는 넣지 않는다.
    return json({ page, total: await currentTotal(env, page), counted: false }, origin);
  }

  const now = new Date();
  const day = now.toISOString().slice(0, 10);
  // 비밀값이 없으면 방문자 해시가 공개된 고정 문자열로 계산되어 보호가 사라진다.
  // 그럴 바에는 집계하지 않는다. 설정 누락을 조용히 넘기지 않기 위해 500으로 알린다.
  if (!env.VISITOR_SALT) {
    return json({ error: "VISITOR_SALT가 설정되지 않았습니다" }, origin, 500);
  }
  const visitor = await visitorHash(request, env.VISITOR_SALT, day);
  const geo = geoOf(request);

  const attribution=parseEmailAttribution(url);
  const recentQuery="SELECT id,ts FROM hits WHERE visitor = ? AND page = ? AND day = ? ORDER BY id DESC LIMIT 1";
  const recent = await env.DB.prepare(recentQuery).bind(visitor,page,day).first();
  if (recent && now.getTime() - Date.parse(recent.ts) < DEDUPE_MINUTES * 60000) {
    await attachEmailAttribution(env,recent.id,attribution);
    return json({ page, total: await currentTotal(env, page), counted: false }, origin);
  }

  const cutoff=new Date(now.getTime()-DEDUPE_MINUTES*60000).toISOString();
  const values=[page,now.toISOString(),day,geo.country,geo.region,geo.city,
                referrerHost(request.headers.get("Referer")),ua.slice(0,300),visitor];
  async function insert(includeAttribution) {
    const columns=includeAttribution?",source,edition_day,edition_phase":"";
    const slots=includeAttribution?",?,?,?":"";
    const extra=includeAttribution?[attribution?.source||'',attribution?.edition_day||'',attribution?.edition_phase||'']:[];
    // D1 batch는 트랜잭션이다. 같은 시각의 두 요청도 INSERT 시점에 다시 중복 확인한다.
    return await env.DB.batch([
      env.DB.prepare("INSERT INTO hits (page,ts,day,country,region,city,referrer,ua,visitor"+columns+") SELECT ?,?,?,?,?,?,?,?,?"+slots+
        " WHERE NOT EXISTS (SELECT 1 FROM hits WHERE visitor=? AND page=? AND day=? AND ts>?)")
        .bind(...values,...extra,visitor,page,day,cutoff),
      env.DB.prepare("INSERT INTO counters (page,total) SELECT ?,1 WHERE changes()>0 ON CONFLICT(page) DO UPDATE SET total=total+1").bind(page),
      env.DB.prepare("SELECT total FROM counters WHERE page=?").bind(page),
    ]);
  }
  let results;
  try {results=await insert(true);}
  catch(error) {if(!missingAttributionColumns(error))throw error;results=await insert(false);}
  const counted=results[0].meta.changes>0;
  // 조회와 INSERT 사이에 일반 방문이 먼저 저장된 경우에도 그 행만 보강한다.
  if(!counted&&attribution) {
    const latest=await env.DB.prepare(recentQuery).bind(visitor,page,day).first();
    if(latest)await attachEmailAttribution(env,latest.id,attribution);
  }
  const row = results[2].results[0];
  return json({ page, total: row ? row.total : 0, counted }, origin);
}

async function handleStats(request, env, url, origin) {
  // 통계는 공개 대상이 아니다. 토큰이 설정되지 않았으면 아예 막는다.
  // 토큰은 헤더로만 받는다. 쿼리스트링에 실으면 로그와 브라우저 기록에 남는다.
  const auth = request.headers.get("Authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  if (!env.STATS_TOKEN || !token || token !== env.STATS_TOKEN) {
    return json({ error: "unauthorized" }, origin, 401);
  }
  const days = Math.min(Math.max(parseInt(url.searchParams.get("days") || "30", 10), 1), 365);
  const since = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);

  const [totals, daily, countries, regions, regionsDaily,
         cities, citiesDaily, referrers] = await env.DB.batch([
    env.DB.prepare("SELECT page, total FROM counters ORDER BY total DESC"),
    env.DB.prepare(
      "SELECT day, page, COUNT(*) AS views, COUNT(DISTINCT visitor) AS visitors " +
        "FROM hits WHERE day >= ? GROUP BY day, page ORDER BY day DESC"
    ).bind(since),
    env.DB.prepare(
      "SELECT country, COUNT(*) AS views FROM hits WHERE day >= ? AND country <> '' " +
        "GROUP BY country ORDER BY views DESC LIMIT 30"
    ).bind(since),
    env.DB.prepare(
      "SELECT region, COUNT(*) AS views FROM hits WHERE day >= ? AND region <> '' " +
        "GROUP BY region ORDER BY views DESC LIMIT 30"
    ).bind(since),
    // 날짜별 시/도. 기간 전체 합계만으로는 "어느 날 어디서 들어왔나"를 볼 수 없다.
    // 행 수는 (날짜 × 시/도)라 상한을 둔다.
    env.DB.prepare(
      "SELECT day, region, COUNT(*) AS views, COUNT(DISTINCT visitor) AS visitors " +
        "FROM hits WHERE day >= ? AND region <> '' " +
        "GROUP BY day, region ORDER BY day DESC, views DESC LIMIT 5000"
    ).bind(since),
    env.DB.prepare(
      "SELECT city, COUNT(*) AS views FROM hits WHERE day >= ? AND city <> '' " +
        "GROUP BY city ORDER BY views DESC LIMIT 30"
    ).bind(since),
    // 날짜별 시/군/구. 시/도와 같은 이유로 둔다 — 유입이 튄 날 어디서 왔는지 보려면
    // 기간 합계가 아니라 그날의 분포가 필요하다. 도시는 시/도보다 종류가 많으므로
    // 상한을 넉넉히 둔다.
    env.DB.prepare(
      "SELECT day, city, COUNT(*) AS views, COUNT(DISTINCT visitor) AS visitors " +
        "FROM hits WHERE day >= ? AND city <> '' " +
        "GROUP BY day, city ORDER BY day DESC, views DESC LIMIT 8000"
    ).bind(since),
    env.DB.prepare(
      "SELECT referrer, COUNT(*) AS views FROM hits WHERE day >= ? AND referrer <> '' " +
        "GROUP BY referrer ORDER BY views DESC LIMIT 30"
    ).bind(since),
  ]);

  return json(
    {
      since,
      days,
      totals: totals.results,
      daily: daily.results,
      countries: countries.results,
      regions: regions.results,
      regionsDaily: regionsDaily.results,
      cities: cities.results,
      citiesDaily: citiesDaily.results,
      referrers: referrers.results,
      emailTraffic: await emailTrafficStats(env,since),
    },
    origin
  );
}

// 주소는 앞뒤 공백을 빼고 소문자로 둔다 — 같은 사람이 대소문자만 달리 두 번 신청해도 한 건이다.
export function normalizeEmail(raw) {
  const email = String(raw || "").trim().toLowerCase();
  return email.length <= 254 && EMAIL_PATTERN.test(email) ? email : "";
}

function isAdmin(request, env) {
  const auth = request.headers.get("Authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  return Boolean(env.STATS_TOKEN && token && token === env.STATS_TOKEN);
}

async function readJson(request, limit = 2000) {
  // 본문이 크면 읽지 않는다. 신청 한 건은 수백 바이트면 충분하다.
  const text = await request.text();
  if (text.length > limit) return null;
  try { return JSON.parse(text); } catch { return null; }
}

// 신청과 해지. 두 경우 모두 결과와 상관없이 같은 응답을 준다 — 응답으로 어떤 주소가 목록에 있는지
// 알아낼 수 없어야 한다. 원본 IP는 저장하지 않고, 하루 단위 방문자 해시로 신청 횟수만 제한한다.
async function handleSubscribe(request, env, origin, action) {
  if (!countable(request)) return json({ error: "forbidden" }, origin, 403);
  if (!env.VISITOR_SALT) return json({ error: "VISITOR_SALT가 설정되지 않았습니다" }, origin, 500);
  const body = await readJson(request);
  if (!body) return json({ error: "bad request" }, origin, 400);
  // 사람에게 보이지 않는 칸. 봇이 폼을 통째로 채우면 여기에 값이 들어온다. 조용히 받은 척한다.
  if (body.website) return json({ ok: true }, origin);
  const page = String(body.page || "");
  if (!SUBSCRIBE_PAGES.includes(page)) return json({ error: "unknown page" }, origin, 400);
  const email = normalizeEmail(body.email);
  if (!email) return json({ error: "invalid email" }, origin, 400);

  const now = new Date();
  const day = now.toISOString().slice(0, 10);
  const visitor = await visitorHash(request, env.VISITOR_SALT, day);
  const used = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM subscribe_log WHERE visitor = ? AND day = ?"
  ).bind(visitor, day).first();
  if (used && used.n >= SUBSCRIBE_DAILY_LIMIT) return json({ error: "too many requests" }, origin, 429);

  const write = action === "subscribe"
    ? env.DB.prepare(
        "INSERT INTO subscribers (email, page, ts) VALUES (?, ?, ?) ON CONFLICT(email, page) DO NOTHING"
      ).bind(email, page, now.toISOString())
    : env.DB.prepare("DELETE FROM subscribers WHERE email = ? AND page = ?").bind(email, page);
  await env.DB.batch([
    write,
    env.DB.prepare("INSERT INTO subscribe_log (visitor, day) VALUES (?, ?)").bind(visitor, day),
    ...(action === "unsubscribe" ? subscriberMailCleanup(env, email) : []),
  ]);
  return json({ ok: true }, origin);
}

// 정시 호출 시험(관리자). GitHub 를 실제로 한 번 부르고 결과를 그대로 돌려준다. 토큰 값은 돌려주지 않는다.
async function handleDispatchTest(request, env, origin) {
  if (!isAdmin(request, env)) return json({ error: "unauthorized" }, origin, 401);
  const result = await dispatchScoring(env);
  return json({ ...result, token_present: Boolean(env.GH_DISPATCH_TOKEN || env.GITHUB_DISPATCH_TOKEN),
                force: String(env.DISPATCH_FORCE || "") === "1", at: new Date().toISOString() }, origin);
}

async function handleSubscribers(request, env, origin) {
  if (!isAdmin(request, env)) return json({ error: "unauthorized" }, origin, 401);
  const rows = await env.DB.prepare(
    "SELECT email, page, ts FROM subscribers ORDER BY ts DESC LIMIT 10000"
  ).all();
  return json({ subscribers: rows.results }, origin);
}

async function handleSubscriberDelete(request, env, origin) {
  if (!isAdmin(request, env)) return json({ error: "unauthorized" }, origin, 401);
  const body = await readJson(request);
  const email = normalizeEmail(body && body.email);
  const page = String((body && body.page) || "");
  if (!email || !SUBSCRIBE_PAGES.includes(page)) return json({ error: "bad request" }, origin, 400);
  await env.DB.batch([env.DB.prepare("DELETE FROM subscribers WHERE email = ? AND page = ?").bind(email, page), ...subscriberMailCleanup(env, email)]);
  return json({ ok: true }, origin);
}

// 호출자 자신의 위치만 돌려준다. 다른 방문자 정보는 나오지 않으며, Cloudflare가
// 이 계정에서 시/도·도시를 실제로 채워 주는지 확인하기 위한 점검용이다.
function handleGeo(request, origin) {
  const cf = request.cf || {};
  return json({
    country: cf.country || "",
    region: cf.region || "",
    city: cf.city || "",
    timezone: cf.timezone || "",
    colo: cf.colo || "",
  }, origin);
}

// 보고서 발행 완료 알림(2026-10-04). 공개 요약과 비공개 수신 정보를 분리하며 기본은 발송 중지다.
const MAIL_NAMES = { samsung: '삼성전자', sk_hynix: 'SK하이닉스' };
const MAIL_SITE_NAMES = {metals:'금·은 예측',china:'중국 주식·5개년 계획',macro:'거시 경제',ai_news:'AI 뉴스',robot_news:'로봇 뉴스',trends:'인기 급상승 검색어',interest:'장기 관심도'};
const MAIL_BATCH_SIZE = 5;
// Gmail은 제공자 중복 요청 키가 없어 회차당 한 번만 시도한다(2026-10-04 Gmail 발신 요청).
function mailBase64(value) {
  return btoa(Array.from(new TextEncoder().encode(value),b=>String.fromCharCode(b)).join(''));
}
function gmailMime(payload,id) {
  const clean=value=>{if (typeof value!=='string' || /[\r\n]/.test(value)) throw new Error('mail_header');return value;};
  const subject=[];let word='';
  for (const c of clean(payload.subject)) {
    if (new TextEncoder().encode(word+c).length>42) {subject.push('=?UTF-8?B?'+mailBase64(word)+'?=');word='';}
    word+=c;
  }
  if (word) subject.push('=?UTF-8?B?'+mailBase64(word)+'?=');
  const boundary='report_'+crypto.randomUUID().replaceAll('-','');
  const headers=[`From: ${clean(payload.from)}`,`To: ${clean(payload.to[0])}`,'Subject: '+subject.join('\r\n '),
    'Date: '+new Date().toUTCString(),`Message-ID: <${clean(id)}@gmail.com>`,'MIME-Version: 1.0',`Content-Type: multipart/alternative; boundary="${boundary}"`];
  for (const [key,value] of Object.entries(payload.headers || {})) {
    if (!/^[A-Za-z0-9-]+$/.test(key)) throw new Error('mail_header');
    headers.push(key+': '+clean(value));
  }
  const part=(type,body)=>`--${boundary}\r\nContent-Type: ${type}; charset=UTF-8\r\nContent-Transfer-Encoding: base64\r\n\r\n`+
    (mailBase64(body || '').match(/.{1,76}/g) || ['']).join('\r\n')+'\r\n';
  return headers.join('\r\n')+'\r\n\r\n'+part('text/plain',payload.text)+part('text/html',payload.html)+`--${boundary}--\r\n.\r\n`;
}
export async function sendGmailSmtp(env,payload,id,connectSocket) {
  const user=normalizeEmail(env.GMAIL_USER),password=String(env.GMAIL_APP_PASSWORD || '').replace(/\s/g,'');
  if (!user.endsWith('@gmail.com') || payload.from!==user || payload.to.length!==1 || !normalizeEmail(payload.to[0])) throw new Error('gmail_sender');
  if (!password) throw new Error('gmail_password');
  const mime=gmailMime(payload,id);
  const connect=connectSocket || (await import('cloudflare:sockets')).connect;
  const socket=connect({hostname:'smtp.gmail.com',port:465},{secureTransport:'on'});
  socket.closed.catch(()=>{});
  const reader=socket.readable.getReader(),writer=socket.writable.getWriter();
  let buffer='',timer;
  const response=async expected=>{
    let code;
    for(let lines=0;lines<100;lines++) {
      while(!buffer.includes('\r\n')) {
        const r=await reader.read();if(r.done)throw new Error('smtp_closed');
        buffer+=new TextDecoder().decode(r.value);
        if(buffer.length>65536)throw new Error('smtp_response');
      }
      const end=buffer.indexOf('\r\n'),line=buffer.slice(0,end);buffer=buffer.slice(end+2);
      const match=/^(\d{3})([ -])/.exec(line);
      if(!match || (code && code!==match[1]))throw new Error('smtp_response');
      code=match[1];
      if(match[2]===' ') {
        if(!expected.includes(Number(code)))throw new Error('smtp_'+code);
        return;
      }
    }
    throw new Error('smtp_response');
  };
  const write=text=>writer.write(new TextEncoder().encode(text));
  const command=async(text,codes)=>{await write(text+'\r\n');await response(codes);};
  const work=async()=>{
    await socket.opened;await response([220]);
    await command('EHLO predict-stock.invalid',[250]);
    await command('AUTH LOGIN',[334]);await command(mailBase64(user),[334]);await command(mailBase64(password),[235]);
    await command('MAIL FROM:<'+user+'>',[250]);await command('RCPT TO:<'+payload.to[0]+'>',[250,251]);
    await command('DATA',[354]);await write(mime);await response([250]);
    // DATA 접수가 끝난 뒤 QUIT 연결 종료 오류는 성공을 실패로 바꾸지 않는다.
    return id;
  };
  try {
    return await Promise.race([work(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('smtp_timeout')),30000);})]);
  } finally {
    clearTimeout(timer);
    try {await socket.close();} catch {}
  }
}

async function mailConfig(env) {
  const row = await env.DB.prepare('SELECT from_email,updated_at FROM mail_settings WHERE id=1').first();
  const control = await env.DB.prepare("SELECT value FROM mail_controls WHERE id='paused'").first();
  // 빈 값 저장은 발송 중지를 뜻한다. 과거 환경변수 주소로 되돌리지 않는다.
  const from = row ? row.from_email : String(env.MAIL_FROM || '');
  const provider=String(env.MAIL_PROVIDER || 'resend');
  const missing=(provider==='gmail'?['GMAIL_USER','GMAIL_APP_PASSWORD','MAIL_PUBLIC_URL','MAIL_PUBLISH_TOKEN']:['RESEND_API_KEY','MAIL_PUBLIC_URL','MAIL_PUBLISH_TOKEN']).filter(k=>!env[k]);
  if (!['resend','gmail'].includes(provider)) missing.push('MAIL_PROVIDER 형식');
  if (provider==='gmail' && (!normalizeEmail(env.GMAIL_USER).endsWith('@gmail.com') || from!==normalizeEmail(env.GMAIL_USER))) missing.push('발신 주소와 Gmail 인증 계정 일치');
  if (!from) missing.push('관리자 발신 이메일');
  let validUrl = false;
  try { const u = new URL(env.MAIL_PUBLIC_URL); validUrl = u.protocol === 'https:' && u.pathname === '/' && !u.search && !u.hash && !u.username && !u.password; } catch {}
  if (env.MAIL_PUBLIC_URL && !validUrl) missing.push('MAIL_PUBLIC_URL 형식');
  return { paused:control?.value==='1', enabled:String(env.MAIL_ENABLED || '')==='1', configured:missing.length===0, missing,
    from_email:from, provider, max_per_day:2, updated_at:row ? row.updated_at : null };
}
async function handleMailSettings(request,env,origin) {
  if (!isAdmin(request,env)) return json({error:'unauthorized'},origin,401);
  if (request.method==='POST') {
    const body=await readJson(request);
    if (!body || typeof body.from_email!=='string') return json({error:'발신 이메일을 입력하세요.'},origin,400);
    const raw=body.from_email.trim(), email=normalizeEmail(raw);
    if (raw && !email) return json({error:'올바른 이메일 주소를 입력하세요.'},origin,400);
    if (email && env.MAIL_PROVIDER==='gmail' && email!==normalizeEmail(env.GMAIL_USER)) return json({error:'Gmail 인증 계정과 같은 발신 주소를 입력하세요.'},origin,400);
    await env.DB.prepare('INSERT INTO mail_settings (id,from_email,updated_at) VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET from_email=excluded.from_email,updated_at=excluded.updated_at')
      .bind(email,new Date().toISOString()).run();
  }
  return json(await mailConfig(env),origin);
}
function mailDay(now) { return new Date(now.getTime()+9*3600000).toISOString().slice(0,10); }
function phaseOpen(phase,now) {
  const h=new Date(now.getTime()+9*3600000).getUTCHours();
  const minute=new Date(now.getTime()+9*3600000).getUTCMinutes();
  return phase==='pre_open' ? h<9 : h*60+minute>=15*60+30;
}
export async function mailAutomationProof(env,workflow,now=Date.now()) {
  if (!env.MAIL_PUBLISH_TOKEN) return '';
  const raw=Math.floor(now/1000)+':'+crypto.randomUUID();
  const key=await crypto.subtle.importKey('raw',new TextEncoder().encode(env.MAIL_PUBLISH_TOKEN),{name:'HMAC',hash:'SHA-256'},false,['sign']);
  const bytes=await crypto.subtle.sign('HMAC',key,new TextEncoder().encode(workflow+'|'+raw));
  return raw+':'+[...new Uint8Array(bytes)].map(x=>x.toString(16).padStart(2,'0')).join('');
}
// 보고서 생성과 메일 등록을 분리한다. 관리자는 명시적으로, Cron은 누락 회차만 요청한다.
// 서명은 용도·날짜·회차에 묶고 15분만 유효하다. 구독/발송 원장은 기존 것을 그대로 쓴다.
async function requestMailRecovery(env, caller, now=new Date()) {
  const config=await mailConfig(env);
  if (!config.enabled || !config.configured || config.paused)
    return {status:'unavailable',error:'메일 설정과 일시 중지 상태를 확인하세요.'};
  const phase=phaseOpen('pre_open',now)?'pre_open':phaseOpen('post_close',now)?'post_close':null;
  if (!phase) return {status:'outside_session',error:'발송 시간은 개장 전 09:00 이전 또는 마감 후 15:30 이후입니다.'};
  const day=mailDay(now), edition='digest/'+day+'/'+phase;
  const stopped=await env.DB.prepare('SELECT value FROM mail_controls WHERE id=?').bind(edition).first();
  if (stopped?.value==='1') return {status:'cancelled',error:'취소한 회차는 다시 발송하지 않습니다.'};
  const registered=await env.DB.prepare('SELECT COUNT(*) AS n FROM mail_events WHERE id IN (?,?)')
    .bind(edition+'/samsung',edition+'/sk_hynix').first();
  if (registered.n===2) return {status:'already_registered',session:day,phase};
  const subscribers=await env.DB.prepare('SELECT COUNT(*) AS n FROM subscribers').first();
  if (!subscribers.n) return {status:'no_subscribers',error:'등록된 구독자가 없습니다.'};
  if (!(env.GH_DISPATCH_TOKEN || env.GITHUB_DISPATCH_TOKEN))
    return {status:'unavailable',error:'GitHub 호출 토큰 설정이 없습니다.'};
  const stamp=now.toISOString(), seconds=Math.floor(now.getTime()/1000), id='request/'+edition;
  // 동시에 버튼을 누르거나 Cron과 겹쳐도 10분에 한 번만 작업을 만든다.
  const lock=await env.DB.prepare(`INSERT INTO mail_controls(id,value,updated_at) VALUES (?,?,?)
    ON CONFLICT(id) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
    WHERE CAST(mail_controls.value AS INTEGER)<=? RETURNING id`)
    .bind(id,String(seconds),stamp,seconds-600).first();
  if (!lock) return {status:'already_requested',session:day,phase};
  try {
    const proof=await mailAutomationProof(env,'report-email.yml|'+caller+'|'+phase+'|'+day);
    const result=await dispatchWorkflow(env,'report-email.yml',{caller,phase,session:day,mail_proof:proof});
    if (result.status==='dispatched') return {...result,session:day,phase};
    await env.DB.prepare('DELETE FROM mail_controls WHERE id=? AND updated_at=?').bind(id,stamp).run();
    return {...result,error:'GitHub 메일 등록 작업 호출 실패: '+(result.code || result.status)};
  } catch {
    await env.DB.prepare('DELETE FROM mail_controls WHERE id=? AND updated_at=?').bind(id,stamp).run();
    return {status:'failed',error:'GitHub 메일 등록 작업 호출 중 통신 오류가 발생했습니다.'};
  }
}
async function handleMailSendToday(request,env,origin) {
  if (!isAdmin(request,env)) return json({error:'unauthorized'},origin,401);
  const result=await requestMailRecovery(env,'admin-mail');
  return json(result,origin,result.status==='dispatched'?202:
    ['already_requested','already_registered'].includes(result.status)?200:result.status==='failed'?502:409);
}
async function automaticMail(env,a,phase,now) {
  if (!a || a.attempt!==1) return false;
  if (a.event==='schedule') return true;
  if (a.event!=='workflow_dispatch' || !['cloudflare-cron','admin-mail','cloudflare-mail'].includes(a.caller) || !env.MAIL_PUBLISH_TOKEN) return false;
  const parts=String(a.proof || '').split(':');
  if (parts.length!==3 || !/^\d+$/.test(parts[0]) || !/^[a-f0-9]{64}$/.test(parts[2])) return false;
  const age=now.getTime()/1000-Number(parts[0]);
  const recovery=a.caller==='admin-mail' || a.caller==='cloudflare-mail';
  if (age<0 || age>(recovery?900:4*3600)) return false;
  const key=await crypto.subtle.importKey('raw',new TextEncoder().encode(env.MAIL_PUBLISH_TOKEN),{name:'HMAC',hash:'SHA-256'},false,['verify']);
  const bytes=Uint8Array.from(parts[2].match(/../g),x=>parseInt(x,16));
  const workflow=recovery?'report-email.yml|'+a.caller+'|'+phase+'|'+mailDay(now):
    phase==='pre_open'?'daily-report.yml':'afternoon-report.yml';
  return crypto.subtle.verify('HMAC',key,bytes,new TextEncoder().encode(workflow+'|'+parts.slice(0,2).join(':')));
}
function mailEscape(text) {
  return String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
async function mailHash(value) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2,'0')).join('');
}
async function handleMailEvent(request, env, origin) {
  if (!env.MAIL_PUBLISH_TOKEN || request.headers.get('Authorization') !== 'Bearer ' + env.MAIL_PUBLISH_TOKEN)
    return json({error:'unauthorized'}, origin, 401);
  const body = await readJson(request, 100000);
  if (!body || !MAIL_NAMES[body.target] || !['pre_open','post_close'].includes(body.phase) ||
      !/^\d{4}-\d{2}-\d{2}$/.test(body.session_date || '') || !/^[a-f0-9]{40}$/.test(body.source_revision || '') ||
      body.url !== `https://namyikim.github.io/predict_stock/${body.target}/` ||
      !Array.isArray(body.lines) || !body.lines.length || body.lines.length > 12 ||
      body.lines.some(line => typeof line !== 'string' || line.length > 1500))
    return json({error:'invalid report event'}, origin, 400);
  const site=body.site_reports===undefined?[]:body.site_reports;
  if (!Array.isArray(site) || site.length>7 || new Set(site.map(r=>r && r.key)).size!==site.length ||
      site.some(r=>!r || !Object.hasOwn(MAIL_SITE_NAMES,r.key) || typeof r.as_of!=='string' || r.as_of.length>260 ||
        r.url!==`https://namyikim.github.io/predict_stock/${r.key}/` || !Array.isArray(r.lines) ||
        !r.lines.length || r.lines.length>5 || r.lines.some(x=>typeof x!=='string' || x.length>700)))
    return json({error:'invalid site reports'},origin,400);
  const siteReports=site.map(r=>({key:r.key,title:MAIL_SITE_NAMES[r.key],as_of:r.as_of,lines:r.lines,url:r.url}));
  const now=new Date(), day=mailDay(now);
  if (!await automaticMail(env,body.automation,body.phase,now)) return json({error:'자동 실행 또는 서명된 관리자 요청만 이메일을 등록할 수 있습니다.'},origin,403);
  if (body.session_date!==day || !phaseOpen(body.phase,now)) return json({status:'outside_session',enabled:false},origin,200);
  const edition='digest/'+day+'/'+body.phase, id=edition+'/'+body.target;
  const content = JSON.stringify({target:body.target,phase:body.phase,session_date:body.session_date,lines:body.lines,url:body.url,site_reports:siteReports});
  // 회차별 최초 자동 게시본을 고정한다. 수급 보완·재실행은 세 번째 메일을 만들지 않는다.
  await env.DB.batch([
    env.DB.prepare('INSERT OR IGNORE INTO mail_editions (id,day,phase,created_at) VALUES (?,?,?,?)').bind(edition,day,body.phase,now.toISOString()),
    env.DB.prepare('INSERT OR IGNORE INTO mail_events (id,target,phase,session_date,content,source_revision,created_at) VALUES (?,?,?,?,?,?,?)')
      .bind(id,body.target,body.phase,day,content,body.source_revision,now.toISOString()),
  ]);
  const config=await mailConfig(env);
  return json({status:'queued',event_id:id,enabled:config.enabled,configured:config.configured},origin,202);
}
// 알림 수신 이벤트의 URL 검증은 유지하고 메일 출력 링크만 표시한다(2026-10-07).
export function emailReportUrl(rawUrl, day, phase) {
  const url=new URL(rawUrl);
  if(url.origin!=='https://namyikim.github.io'||!url.pathname.startsWith('/predict_stock/')||url.username||url.password)
    throw new Error('보고서 사이트 URL이 필요합니다');
  for(const [key,value] of Object.entries({utm_source:'email',utm_medium:'report',edition_day:day,edition_phase:phase}))
    url.searchParams.set(key,value);
  if(!parseEmailAttribution(url))throw new Error('유효한 메일 발행 회차가 필요합니다');
  return url.toString();
}

export async function mailPayload(env, event, email) {
  await env.DB.prepare('INSERT OR IGNORE INTO mail_unsubscribe (email,token) VALUES (?,?)')
    .bind(email,crypto.randomUUID()+crypto.randomUUID()).run();
  const token = (await env.DB.prepare('SELECT token FROM mail_unsubscribe WHERE email=?').bind(email).first()).token;
  const url = env.MAIL_PUBLIC_URL.replace(/\/$/,'') + '/mail/unsubscribe?token=' + encodeURIComponent(token);
  const c = JSON.parse(event.content);
  const subject = `[일일 보고서] ${c.day} · ${c.phase === 'pre_open' ? '개장 전 예측' : '마감 후 회고'}`;
  const note = '연구·교육용 판단 자료이며 투자 자문이 아닙니다. 예측은 실제 결과와 다를 수 있습니다.';
  const site=c.site_reports || [];
  const stocks=c.reports.map(r=>({title:MAIL_NAMES[r.target],lines:r.lines,url:r.url}));
  const sections=[...stocks,...site].map(r=>({...r,url:emailReportUrl(r.url,c.day,c.phase)}));
  const text=sections.map(r=>r.title+'\n'+(r.as_of?'게시 자료 기준: '+r.as_of+'\n':'')+r.lines.join('\n')+'\n전체 보고서: '+r.url).join('\n\n');
  const html=sections.map(r=>'<section style="margin:24px 0;padding:0 0 18px;border-bottom:1px solid #e3eaf1"><h3 style="margin:0 0 8px;color:#1a5490">'+mailEscape(r.title)+'</h3>'+
    (r.as_of?'<p style="font-size:12px;color:#667085">게시 자료 기준: '+mailEscape(r.as_of)+'</p>':'')+
    '<ul style="padding-left:20px">'+r.lines.map(x=>'<li style="margin:7px 0">'+mailEscape(x)+'</li>').join('')+'</ul><p><a href="'+mailEscape(r.url)+'">전체 보고서 보기</a></p></section>').join('');
  return {from:env.MAIL_FROM,to:[email],subject,
    text:subject+'\n\n'+(site.length?'전체 메뉴의 최신 게시본을 모았습니다. 메뉴마다 자료 기준일과 갱신 주기가 다릅니다.\n\n':'')+text+'\n\n'+note+
      '\n\n메일 수신을 원하지 않으시면 아래 링크에서 구독을 취소할 수 있습니다. 이 이메일 주소로 신청한 모든 보고서 구독이 취소됩니다.\n구독 취소: '+url,
    html:'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body style="margin:0;padding:20px 12px;font-family:Arial,sans-serif;line-height:1.7;color:#243447;overflow-wrap:anywhere"><div style="max-width:760px;margin:auto"><h2>'+mailEscape(subject)+
      '</h2>'+(site.length?'<p style="padding:12px;background:#f0f6fc">전체 메뉴의 최신 게시본입니다. 메뉴마다 자료 기준일과 갱신 주기가 다르므로 아래 기준 시각을 함께 확인하세요.</p>':'')+html+'<p>'+note+
      '</p><footer style="margin-top:28px;padding:20px;background:#f0f6fc;border:1px solid #cedff0;border-radius:8px;font-size:14px">'+
      '<p style="margin:0 0 10px">메일 수신을 원하지 않으시면 아래 링크에서 구독을 취소할 수 있습니다.</p>'+
      '<p style="margin:0 0 14px">이 이메일 주소로 신청한 모든 보고서 구독이 취소됩니다.</p>'+
      '<a href="'+mailEscape(url)+'" style="display:inline-block;padding:10px 18px;color:#1a5490;background:#fff;border:1px solid #b9d3ec;border-radius:5px;font-weight:bold">구독 취소</a>'+
      '</footer></div></body></html>',
    headers:{'List-Unsubscribe':'<'+url+'>','List-Unsubscribe-Post':'List-Unsubscribe=One-Click','X-Report-Topics':c.reports.map(r=>r.target).join(','),
      'X-Report-Provider':env.MAIL_PROVIDER || 'resend',...(site.length?{'X-Report-Scope':'main'}:{})}};
}
// 메일 하단에서 이어지는 취소 화면도 휴대폰에서 읽고 누를 수 있게 한다(2026-10-04 요청).
function mailUnsubscribePage(title, message, confirm = false) {
  return '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'+
    '<title>'+mailEscape(title)+' · 예측 보고서</title><style>body{margin:0;padding:32px 16px;background:#f0f6fc;color:#243447;font-family:Arial,sans-serif;line-height:1.8;overflow-wrap:anywhere}'+
    'main{max-width:540px;margin:32px auto;padding:24px;background:#fff;border:1px solid #cedff0;border-radius:12px}h1{font-size:24px;color:#1a5490;line-height:1.4}'+
    'button{max-width:100%;padding:12px 18px;border:0;border-radius:5px;background:#1a5490;color:#fff;font:inherit;font-weight:bold;cursor:pointer}a{color:#1a5490}</style></head><body><main>'+
    '<h1>'+mailEscape(title)+'</h1><p>'+mailEscape(message)+'</p>'+
    (confirm?'<form method="post"><button name="confirm" value="1">모든 보고서 구독 취소</button></form>':'')+
    '<p><a href="https://namyikim.github.io/predict_stock/">보고서 사이트로 돌아가기</a></p></main></body></html>';
}
// GET 링크 미리보기로 해지되지 않게 확인 화면과 실제 POST 처리를 나눈다.
async function handleMailUnsubscribe(request, env, url) {
  const token = url.searchParams.get('token') || '';
  const row = token.length <= 100 ? await env.DB.prepare('SELECT email FROM mail_unsubscribe WHERE token=?').bind(token).first() : null;
  const headers = {'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store','Referrer-Policy':'no-referrer',
    'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"};
  if (!row) return new Response(mailUnsubscribePage('구독 취소 링크 안내','이미 구독을 취소했거나 유효하지 않은 링크입니다. 구독 중이라면 가장 최근에 받은 보고서 메일의 링크를 이용해 주세요.'),{headers});
  if (request.method === 'POST') {
    const text = await request.text();
    if (!['confirm=1','List-Unsubscribe=One-Click'].includes(text)) return new Response(mailUnsubscribePage('구독 취소 확인이 필요합니다','메일의 구독 취소 링크에서 다시 진행해 주세요.'),{status:400,headers});
    await env.DB.batch([
      env.DB.prepare('DELETE FROM subscribers WHERE email=?').bind(row.email),
      env.DB.prepare('DELETE FROM mail_deliveries WHERE email=?').bind(row.email),
      env.DB.prepare('DELETE FROM mail_unsubscribe WHERE email=?').bind(row.email),
    ]);
    return new Response(mailUnsubscribePage('구독 취소가 완료되었습니다','이 이메일 주소로 신청한 모든 보고서 구독을 취소했습니다. 앞으로 보고서 이메일을 받지 않습니다.'),{headers});
  }
  return new Response(mailUnsubscribePage('보고서 메일 구독 취소','아래 버튼을 누르면 이 이메일 주소로 신청한 모든 보고서 구독이 취소됩니다.',true),{headers});
}
function subscriberMailCleanup(env, email) {
  return [env.DB.prepare(`DELETE FROM mail_deliveries WHERE email=? AND NOT EXISTS (
    SELECT 1 FROM subscribers s LEFT JOIN mail_editions ed ON ed.id=mail_deliveries.event_id
    LEFT JOIN mail_events e ON e.id=mail_deliveries.event_id WHERE s.email=mail_deliveries.email AND
    ((ed.id IS NOT NULL AND s.ts<=ed.created_at) OR ((s.page='main' OR s.page=e.target) AND s.ts<=e.created_at)))`).bind(email),
    env.DB.prepare('DELETE FROM mail_unsubscribe WHERE email=? AND NOT EXISTS (SELECT 1 FROM subscribers WHERE email=?)').bind(email,email)];
}
async function cleanupSubscriberMail(env, email) {
  await env.DB.batch(subscriberMailCleanup(env,email));
}
// 원자적 임대와 제공자의 같은 요청 키를 함께 쓴다. 접수 응답 유실 뒤 재시도해도 같은 본문이다.
export async function processMail(env, now = new Date(), pause = ms => new Promise(resolve => setTimeout(resolve,ms))) {
  const config = await mailConfig(env);
  if (!config.enabled || !config.configured || config.paused) return {status:'disabled',...config};
  const phase=phaseOpen('pre_open',now)?'pre_open':phaseOpen('post_close',now)?'post_close':'';
  if (!phase) return {status:'outside_session'};
  const stamp=now.toISOString(),day=mailDay(now);
  await env.DB.prepare('DELETE FROM mail_deliveries WHERE created_at<?').bind(new Date(now.getTime()-30*86400000).toISOString()).run();
  const due = await env.DB.prepare(`SELECT DISTINCT e.id,e.day,e.phase,e.created_at,s.email FROM mail_editions e
    JOIN subscribers s ON s.ts<=e.created_at
    LEFT JOIN mail_deliveries d ON d.event_id=e.id AND d.email=s.email
    WHERE e.day=? AND e.phase=?
    AND NOT EXISTS (SELECT 1 FROM mail_controls c WHERE c.id=e.id AND c.value='1')
    AND (d.event_id IS NULL OR
      (d.attempts<5 AND ((d.status='pending' AND d.next_attempt<=?) OR (d.status='sending' AND d.lease_until<=?))))
    AND NOT EXISTS (SELECT 1 FROM subscribers need WHERE need.email=s.email AND need.ts<=e.created_at AND
      ((need.page IN ('main','samsung') AND NOT EXISTS (SELECT 1 FROM mail_events m WHERE m.id=e.id||'/samsung')) OR
       (need.page IN ('main','sk_hynix') AND NOT EXISTS (SELECT 1 FROM mail_events m WHERE m.id=e.id||'/sk_hynix'))))
    AND NOT EXISTS (SELECT 1 FROM mail_deliveries old JOIN mail_events legacy ON legacy.id=old.event_id
      WHERE old.email=s.email AND old.attempts>0 AND legacy.phase=e.phase AND date(COALESCE(old.sent_at,old.created_at),'+9 hours')=e.day)
    ORDER BY e.created_at,s.email LIMIT ?`).bind(day,phase,stamp,stamp,MAIL_BATCH_SIZE).all();
  let accepted=0,failed=0;
  for (const event of due.results) {
    if (mailDay(new Date())!==day || !phaseOpen(phase,new Date())) break;
    let delivery=await env.DB.prepare('SELECT * FROM mail_deliveries WHERE event_id=? AND email=?').bind(event.id,event.email).first();
    if (!delivery) {
      const subscriptions=await env.DB.prepare('SELECT page FROM subscribers WHERE email=? AND ts<=?').bind(event.email,event.created_at).all();
      const pages=subscriptions.results.map(s=>s.page);
      const reports=await env.DB.prepare('SELECT content FROM mail_events WHERE id IN (?,?) ORDER BY target').bind(event.id+'/samsung',event.id+'/sk_hynix').all();
      const selected=reports.results.map(r=>JSON.parse(r.content)).filter(r=>pages.includes('main') || pages.includes(r.target));
      if (!selected.length) continue;
      // 메인 구독자에게만 전체 메뉴를 한 번 넣는다. 종목별 등록에 실린 같은 자료를 중복하지 않는다.
      const site_reports=pages.includes('main')?(selected.find(r=>r.site_reports && r.site_reports.length)?.site_reports || []):[];
      const payload=await mailPayload({...env,MAIL_FROM:config.from_email},{content:JSON.stringify({day,phase,reports:selected,site_reports})},event.email);
      await env.DB.prepare('INSERT OR IGNORE INTO mail_deliveries (event_id,email,payload,created_at) VALUES (?,?,?,?)')
        .bind(event.id,event.email,JSON.stringify(payload),stamp).run();
    }
    // Gmail은 접수 응답이 유실되어도 재시도하지 않는다. 오래된 임대도 중복 전송하지 않는다.
    delivery=await env.DB.prepare('SELECT * FROM mail_deliveries WHERE event_id=? AND email=?').bind(event.id,event.email).first();
    if (!delivery || ['sent','failed','cancelled'].includes(delivery.status)) continue;
    const transport=JSON.parse(delivery.payload).headers['X-Report-Provider'] || 'resend';
    if (transport!==config.provider || (transport==='gmail' && delivery.attempts>0)) {
      await env.DB.prepare("UPDATE mail_deliveries SET status='failed',error_code=? WHERE event_id=? AND email=? AND (status='pending' OR (status='sending' AND lease_until<=?))")
        .bind(transport!==config.provider?'provider_changed':'gmail_uncertain',event.id,event.email,stamp).run();
      continue;
    }
    delivery=await env.DB.prepare(`UPDATE mail_deliveries SET status='sending',attempts=attempts+1,lease_until=?
      WHERE event_id=? AND email=? AND attempts<?
      AND NOT EXISTS (SELECT 1 FROM mail_controls c WHERE c.id IN ('paused',mail_deliveries.event_id) AND c.value='1') AND
      ((status='pending' AND next_attempt<=?) OR (status='sending' AND lease_until<=?)) RETURNING *`)
      .bind(new Date(now.getTime()+5*60000).toISOString(),event.id,event.email,transport==='gmail'?1:5,stamp,stamp).first();
    if (!delivery) continue;
    const active=await env.DB.prepare('SELECT page FROM subscribers WHERE email=? AND ts<=?').bind(event.email,event.created_at).all();
    const pages=active.results.map(s=>s.page), frozenHeaders=JSON.parse(delivery.payload).headers, topics=frozenHeaders['X-Report-Topics'].split(',');
    if ((frozenHeaders['X-Report-Scope']==='main' && !pages.includes('main')) || !topics.every(t=>pages.includes('main') || pages.includes(t))) {
      await env.DB.prepare("UPDATE mail_deliveries SET status='cancelled',payload='' WHERE event_id=? AND email=?").bind(event.id,event.email).run();
      await cleanupSubscriberMail(env,event.email);
      continue;
    }
    const idempotency='report-'+await mailHash(event.id+'|'+event.email);
    // DB 처리·이전 수신자 발송 중 자정이나 개장 시각을 넘겼으면 여기서 멈춘다.
    const sendingAt=new Date();
    if (mailDay(sendingAt)!==day || !phaseOpen(phase,sendingAt)) break;
    if (transport==='gmail') {
      try {
        const providerId=await sendGmailSmtp(env,JSON.parse(delivery.payload),idempotency);
        await env.DB.prepare("UPDATE mail_deliveries SET status='sent',sent_at=?,provider_id=?,error_code=NULL WHERE event_id=? AND email=?")
          .bind(new Date().toISOString(),providerId,event.id,event.email).run();
        accepted++;
      } catch {
        await env.DB.prepare("UPDATE mail_deliveries SET status='failed',error_code='gmail_send_failed_or_uncertain' WHERE event_id=? AND email=?").bind(event.id,event.email).run();
        failed++;
      }
      await pause(600);continue;
    }
    let code='network',response;
    try {
      response=await fetch('https://api.resend.com/emails',{method:'POST',headers:{Authorization:'Bearer '+env.RESEND_API_KEY,
        'Content-Type':'application/json','Idempotency-Key':idempotency},body:delivery.payload,signal:AbortSignal.timeout(15000)});
      code='http_'+response.status;
    } catch { /* 주소·토큰·응답 본문은 로그에 남기지 않는다. */ }
    if (response && response.ok) {
      let result;
      try {result=await response.json();} catch {}
      if (result && typeof result.id==='string' && result.id.length<=200) {
        await env.DB.prepare("UPDATE mail_deliveries SET status='sent',sent_at=?,provider_id=?,error_code=NULL WHERE event_id=? AND email=?")
          .bind(new Date().toISOString(),result.id,event.id,event.email).run();
        accepted++;await pause(600);continue;
      }
      code='invalid_response';
    }
    const retry=!response || response.status===429 || response.status>=500 || response.status===409 || code==='invalid_response';
    await env.DB.prepare('UPDATE mail_deliveries SET status=?,next_attempt=?,error_code=? WHERE event_id=? AND email=?')
      .bind(retry && delivery.attempts<5?'pending':'failed',new Date(now.getTime()+Math.min(60,5*2**(delivery.attempts-1))*60000).toISOString(),code,event.id,event.email).run();
    failed++;await pause(600);
  }
  return {status:'processed',accepted,failed};
}
// 회차 취소는 아직 배송 행을 만들지 않은 주소에도 적용한다(2026-10-04 요청).
async function handleMailControl(request,env,origin) {
  if (!isAdmin(request,env)) return json({error:'unauthorized'},origin,401);
  const body=await readJson(request);
  if (!body || typeof body.paused!=='boolean') return json({error:'일시 중지 여부를 선택하세요.'},origin,400);
  await env.DB.prepare("INSERT INTO mail_controls (id,value,updated_at) VALUES ('paused',?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at")
    .bind(body.paused?'1':'0',new Date().toISOString()).run();
  return json(await mailConfig(env),origin);
}
async function handleMailCancel(request,env,origin) {
  if (!isAdmin(request,env)) return json({error:'unauthorized'},origin,401);
  const body=await readJson(request);
  if (!body || typeof body.event_id!=='string' || body.event_id.length>100) return json({error:'취소할 회차를 선택하세요.'},origin,400);
  const edition=await env.DB.prepare('SELECT id FROM mail_editions WHERE id=?').bind(body.event_id).first();
  if (!edition) return json({error:'회차를 찾을 수 없습니다.'},origin,404);
  await env.DB.batch([
    env.DB.prepare("INSERT OR IGNORE INTO mail_controls (id,value,updated_at) VALUES (?,'1',?)").bind(edition.id,new Date().toISOString()),
    env.DB.prepare("UPDATE mail_deliveries SET status='cancelled',payload='',error_code='admin_cancelled' WHERE event_id=? AND status='pending'").bind(edition.id)
  ]);
  return json({cancelled:true,notice:'이 회차의 대기를 취소했습니다. 이미 전송 중이거나 접수된 메일은 회수하지 못합니다.'},origin);
}
// 발송 이력은 구독이 유지되는 주소와 저장된 배송 기록을 합쳐 표시한다. 본문·인증 값은 반환하지 않는다.
function mailHistorySQL(filter) {
  return `WITH editions AS (SELECT * FROM mail_editions ${filter}),
    recipients AS (SELECT e.id,s.email FROM editions e JOIN subscribers s ON s.ts<=e.created_at
      UNION SELECT d.event_id,d.email FROM mail_deliveries d JOIN editions e ON e.id=d.event_id),
    history AS (SELECT e.id,e.phase,e.day AS session_date,e.created_at,r.email,d.sent_at,COALESCE(d.attempts,0) AS attempts,
      d.error_code,d.next_attempt,d.lease_until,
      CASE WHEN d.status IN ('sent','sending','failed','cancelled') THEN d.status
        WHEN c.value='1' THEN 'cancelled' ELSE 'pending' END AS status,
      CASE WHEN c.value='1' THEN 1 ELSE 0 END AS stopped
      FROM editions e LEFT JOIN recipients r ON r.id=e.id
      LEFT JOIN mail_deliveries d ON d.event_id=e.id AND d.email=r.email
      LEFT JOIN mail_controls c ON c.id=e.id) `;
}
async function handleMailDeliveries(request,env,origin) {
  if (!isAdmin(request,env)) return json({error:'unauthorized'},origin,401);
  const url=new URL(request.url), id=url.searchParams.get('event_id'), raw=url.searchParams.get('offset')||'0';
  if (!id || id.length>100 || !/^\d{1,7}$/.test(raw)) return json({error:'올바른 회차와 페이지를 선택하세요.'},origin,400);
  const offset=Number(raw), limit=50;
  const rows=await env.DB.prepare(mailHistorySQL('WHERE id=?')+`SELECT email,status,attempts,created_at,sent_at,error_code,session_date,phase
    FROM history WHERE email IS NOT NULL ORDER BY email LIMIT ? OFFSET ?`).bind(id,limit+1,offset).all();
  const now=new Date();
  return json({deliveries:rows.results.slice(0,limit).map(d=>({...d,status:d.status==='pending' && (d.session_date!==mailDay(now)||!phaseOpen(d.phase,now))?'expired':d.status})),
    offset,next_offset:rows.results.length>limit?offset+limit:null},origin);
}
async function handleMailStatus(request,env,origin) {
  if (!isAdmin(request,env)) return json({error:'unauthorized'},origin,401);
  const recent=await env.DB.prepare(mailHistorySQL('ORDER BY created_at DESC LIMIT 20')+`SELECT id,phase,session_date,created_at,stopped,
    COUNT(email) AS eligible,SUM(CASE WHEN attempts>0 THEN 1 ELSE 0 END) AS attempted,
    SUM(CASE WHEN email IS NOT NULL AND status='sent' THEN 1 ELSE 0 END) AS accepted,
    SUM(CASE WHEN email IS NOT NULL AND status='failed' THEN 1 ELSE 0 END) AS failed,
    SUM(CASE WHEN email IS NOT NULL AND status='cancelled' THEN 1 ELSE 0 END) AS cancelled,
    SUM(CASE WHEN email IS NOT NULL AND status='sending' THEN 1 ELSE 0 END) AS sending,
    SUM(CASE WHEN email IS NOT NULL AND status='pending' THEN 1 ELSE 0 END) AS remaining,MAX(sent_at) AS last_sent_at
    FROM history GROUP BY id ORDER BY created_at DESC`).all();
  const now=new Date();
  return json({...await mailConfig(env),events:recent.results.map(e=>({...e,target:'digest',expired:e.session_date!==mailDay(now) || !phaseOpen(e.phase,now)})),
    notice:'수신자별 하루 최대 두 통입니다. 발송 완료는 발송 서비스 접수 기준이며 수신함 도착을 보장하지 않습니다. 전송 중인 메일은 취소할 수 없습니다.'},origin);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get("Origin") || "";

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    try {
      if (url.pathname === '/mail/unsubscribe' && ['GET','POST'].includes(request.method)) return await handleMailUnsubscribe(request,env,url);
      if (request.method === 'POST' && url.pathname === '/mail/send-today') return await handleMailSendToday(request,env,origin);
      if (request.method === 'POST' && url.pathname === '/mail/events') return await handleMailEvent(request,env,origin);
      if (['GET','POST'].includes(request.method) && url.pathname === '/mail/settings') return await handleMailSettings(request,env,origin);
      if (request.method === 'POST' && url.pathname === '/mail/control') return await handleMailControl(request,env,origin);
      if (request.method === 'POST' && url.pathname === '/mail/cancel') return await handleMailCancel(request,env,origin);
      if (request.method === 'GET' && url.pathname === '/mail/deliveries') return await handleMailDeliveries(request,env,origin);
      if (request.method === 'GET' && url.pathname === '/mail/status') return await handleMailStatus(request,env,origin);
      if (request.method === "POST") {
        if (url.pathname === "/subscribe") return await handleSubscribe(request, env, origin, "subscribe");
        if (url.pathname === "/unsubscribe") return await handleSubscribe(request, env, origin, "unsubscribe");
        if (url.pathname === "/subscribers/delete") return await handleSubscriberDelete(request, env, origin);
        if (url.pathname === "/dispatch/test") return await handleDispatchTest(request, env, origin);
        return json({ error: "not found" }, origin, 404);
      }
      if (request.method !== "GET") {
        return json({ error: "method not allowed" }, origin, 405);
      }
      if (url.pathname === "/subscribers") return await handleSubscribers(request, env, origin);
      if (url.pathname === "/hit") return await handleHit(request, env, url, origin);
      if (url.pathname === "/stats") return await handleStats(request, env, url, origin);
      if (url.pathname === "/geo") return handleGeo(request, origin);
      return json({ error: "not found" }, origin, 404);
    } catch (err) {
      // 카운터가 실패해도 보고서 페이지는 그대로 보여야 하므로, 오류를 조용히 JSON으로 돌려준다.
      return json({ error: url.pathname.startsWith('/mail/') ? '메일 처리 실패: 설정과 D1 스키마를 확인하세요.' : String(err && err.message ? err.message : err) }, origin, 500);
    }
  },

  // Cron Trigger가 부른다. 채점 회차 시각이면 GitHub 워크플로를 깨우고, 그 밖의 트리거(보관기간 정리)는
  // 누적 조회수(counters)는 그대로 두고 일별 통계에 더는 쓰이지 않는 오래된 조회 기록만 지운다.
  async scheduled(event, env) {
    // 기존 보고서 정시 호출과 함께 실행한다. 별도 */5 * * * * 트리거는 발송 재시도용이다.
    if (String(env.MAIL_ENABLED || '') === '1') {
      try { const result = await processMail(env); console.log('report_mail ' + JSON.stringify({status:result.status,accepted:result.accepted})); }
      catch { console.log('report_mail 처리 실패: 관리자 발송 상태와 D1을 확인하세요.'); }
    }
    // 메일 전용 주기가 보고서 정시와 겹쳐도 GitHub 작업을 두 번 시작하지 않는다.
    if (event.cron === '*/5 * * * *') {
      const local=new Date(Date.now()+9*3600000), hour=local.getUTCHours(), minute=local.getUTCMinutes();
      const tick=new Date(event.scheduledTime).getUTCMinutes();
      // 아침 06:30~08:50, 오후 15:40~23:50: 모델을 돌리지 않고 누락된 등록만 10분 간격 복구.
      if (String(env.MAIL_ENABLED || '')==='1' && local.getUTCDay()>0 && local.getUTCDay()<6 && tick%10===0 &&
          ((hour*60+minute>=390 && hour<9) || hour*60+minute>=940)) {
        try { const result=await requestMailRecovery(env,'cloudflare-mail');
          console.log('mail_recovery '+JSON.stringify({status:result.status,code:result.code})); }
        catch { console.log('mail_recovery 실패: 메일 설정과 D1을 확인하세요.'); }
      }
      return;
    }
    // DISPATCH_FORCE=1 (Worker 변수)이면 어느 트리거든 올 때마다 GitHub 를 부른다 — 정시 호출이 되는지 시험할 때만
    // 잠깐 켠다(2026-09-30: 정시 호출이 한 번도 성공한 적이 없어 원인을 가르려고). 시험이 끝나면 변수를 지운다.
    const forced = String(env.DISPATCH_FORCE || "") === "1";
    // 아침 종목 보고서 회차(06:20·07:00·07:40 KST)인지 먼저 본다. 채점 회차와 시각이 겹치지 않는다.
    const morning = morningDue(event.scheduledTime);
    if (morning) {
      const result = await dispatchWorkflow(env, MORNING_WORKFLOW,
                                            { caller: "cloudflare-cron", retry: morning.retry ? "true" : "false" }, !forced);
      console.log(`workflow_dispatch ${JSON.stringify({ ...result, workflow: MORNING_WORKFLOW, retry: morning.retry,
                                                        cron: event.cron || null })}`);
      return;
    }
    if (forced || dispatchDue(event.scheduledTime)) {
      const result = await dispatchScoring(env, !forced);
      console.log(`workflow_dispatch ${JSON.stringify({ ...result, forced, cron: event.cron || null })}`);
      return;
    }
    const cutoff = new Date(Date.now() - RETENTION_DAYS * 86400000).toISOString().slice(0, 10);
    await env.DB.prepare("DELETE FROM hits WHERE day < ?").bind(cutoff).run();
    // 신청 횟수 제한용 기록은 그날만 쓰인다. 구독자 목록(subscribers)은 지우지 않는다.
    const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    await env.DB.prepare("DELETE FROM subscribe_log WHERE day < ?").bind(yesterday).run();
  },
};
