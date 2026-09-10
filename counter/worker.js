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
//
// Cron Trigger(대시보드 Settings → Triggers → Cron Triggers, 예: `0 3 * * *`)를 걸면
// 아래 scheduled()가 매일 오래된 조회 기록을 지운다(보관기간 관리).
// `37 0 * * 1-5`·`10 7 * * 1-5`(UTC) 트리거를 더 걸고 GH_DISPATCH_TOKEN 을 넣으면 같은
// scheduled()가 그 시각에 GitHub의 채점 워크플로를 정시에 깨운다(README "채점 워크플로 정시 호출").
//
// 바인딩(대시보드 Settings에서 설정)
//   DB                    D1 데이터베이스
//   VISITOR_SALT          방문자 해시용 비밀값(시크릿)
//   STATS_TOKEN           /stats 접근 토큰(시크릿)
//   GH_DISPATCH_TOKEN (선택) 채점 워크플로를 깨우는 fine-grained PAT(시크릿).
//                     (Cloudflare 대시보드가 GITHUB_ 로 시작하는 이름을 받지 않아 GH_ 를 쓴다.)
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
const ALLOWED_PAGES = ["main", "samsung", "sk_hynix", "china", "metals", "trends", "interest"];

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
const DISPATCH_TIMES_UTC = [[0, 37], [7, 10]];   // 09:37 KST 시가 채점 · 16:10 KST 마감 채점+회고
const DISPATCH_TOLERANCE_MINUTES = 3;            // 트리거가 몇 분 밀려 와도 같은 회차로 본다

// 이 트리거 시각이 채점 회차인가. 워크플로의 cron과 같이 월~금(UTC)만.
export function dispatchDue(scheduledTime, times = DISPATCH_TIMES_UTC) {
  const t = new Date(scheduledTime);
  const weekday = t.getUTCDay();                 // 0=일 … 6=토
  if (weekday === 0 || weekday === 6) return false;
  const minuteOfDay = t.getUTCHours() * 60 + t.getUTCMinutes();
  return times.some(([h, m]) => Math.abs(minuteOfDay - (h * 60 + m)) <= DISPATCH_TOLERANCE_MINUTES);
}

// GitHub에 workflow_dispatch 를 보낸다. 토큰이 없으면 아무것도 하지 않는다 — 조회수 카운터만 쓰는
// 배포도 그대로 동작해야 한다. 응답 본문은 기록하지 않는다(오류 문구에 토큰 정보가 섞일 수 있다).
async function dispatchScoring(env) {
  if (!env.GH_DISPATCH_TOKEN) return { status: "skipped", reason: "GH_DISPATCH_TOKEN 없음" };
  const url = `https://api.github.com/repos/${DISPATCH_REPO}/actions/workflows/${DISPATCH_WORKFLOW}/dispatches`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
      "User-Agent": "predict-stock-counter",   // GitHub API는 User-Agent 없는 요청을 거절한다
    },
    body: JSON.stringify({ ref: "main", inputs: { caller: "cloudflare-cron" } }),
  });
  return { status: response.status === 204 ? "dispatched" : "failed", code: response.status };
}

function corsHeaders(origin) {
  const allowed = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Headers": "Authorization",
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

  const recent = await env.DB.prepare(
    "SELECT ts FROM hits WHERE visitor = ? AND page = ? AND day = ? ORDER BY id DESC LIMIT 1"
  ).bind(visitor, page, day).first();
  if (recent && now.getTime() - Date.parse(recent.ts) < DEDUPE_MINUTES * 60000) {
    return json({ page, total: await currentTotal(env, page), counted: false }, origin);
  }

  const results = await env.DB.batch([
    env.DB.prepare(
      "INSERT INTO hits (page, ts, day, country, region, city, referrer, ua, visitor) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    ).bind(
      page,
      now.toISOString(),
      day,
      geo.country,
      geo.region,
      geo.city,
      referrerHost(request.headers.get("Referer")),
      ua.slice(0, 300),
      visitor
    ),
    env.DB.prepare(
      "INSERT INTO counters (page, total) VALUES (?, 1) " +
        "ON CONFLICT(page) DO UPDATE SET total = total + 1"
    ).bind(page),
    env.DB.prepare("SELECT total FROM counters WHERE page = ?").bind(page),
  ]);

  const row = results[2].results[0];
  return json({ page, total: row ? row.total : 0, counted: true }, origin);
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
    },
    origin
  );
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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get("Origin") || "";

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }
    if (request.method !== "GET") {
      return json({ error: "method not allowed" }, origin, 405);
    }

    try {
      if (url.pathname === "/hit") return await handleHit(request, env, url, origin);
      if (url.pathname === "/stats") return await handleStats(request, env, url, origin);
      if (url.pathname === "/geo") return handleGeo(request, origin);
      return json({ error: "not found" }, origin, 404);
    } catch (err) {
      // 카운터가 실패해도 보고서 페이지는 그대로 보여야 하므로, 오류를 조용히 JSON으로 돌려준다.
      return json({ error: String(err && err.message ? err.message : err) }, origin, 500);
    }
  },

  // Cron Trigger가 부른다. 채점 회차 시각이면 GitHub 워크플로를 깨우고, 그 밖의 트리거(보관기간 정리)는
  // 누적 조회수(counters)는 그대로 두고 일별 통계에 더는 쓰이지 않는 오래된 조회 기록만 지운다.
  async scheduled(event, env) {
    if (dispatchDue(event.scheduledTime)) {
      const result = await dispatchScoring(env);
      console.log(`workflow_dispatch ${JSON.stringify(result)}`);
      return;
    }
    const cutoff = new Date(Date.now() - RETENTION_DAYS * 86400000).toISOString().slice(0, 10);
    await env.DB.prepare("DELETE FROM hits WHERE day < ?").bind(cutoff).run();
  },
};
