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
//
// 바인딩(대시보드 Settings에서 설정)
//   DB            D1 데이터베이스
//   VISITOR_SALT  방문자 해시용 비밀값(시크릿)
//   STATS_TOKEN   /stats 접근 토큰(시크릿)

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
  const visitor = await visitorHash(request, env.VISITOR_SALT || "no-salt", day);
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

  const [totals, daily, countries, regions, regionsDaily, cities, referrers] = await env.DB.batch([
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

  // Cron Trigger가 부른다. 누적 조회수(counters)는 그대로 두고, 일별 통계에 더는
  // 쓰이지 않는 오래된 조회 기록만 지운다.
  async scheduled(event, env) {
    const cutoff = new Date(Date.now() - RETENTION_DAYS * 86400000).toISOString().slice(0, 10);
    await env.DB.prepare("DELETE FROM hits WHERE day < ?").bind(cutoff).run();
  },
};
