// 조회수 카운터 Worker
//
// GitHub Pages는 정적 호스팅이라 서버 쪽에서 접속을 셀 수 없다. 이 Worker가 그
// 역할을 맡아 조회를 D1에 기록하고 누적 조회수를 돌려준다. 외부 카운터 서비스에
// 의존하지 않으므로 서비스가 사라질 걱정이 없고 원본 데이터도 저장소 소유자에게 남는다.
//
// 엔드포인트
//   GET /hit?page=<키>     조회 1건을 기록하고 그 페이지의 누적 조회수를 돌려준다.
//   GET /stats?token=<토큰> 최근 방문 통계를 JSON으로 돌려준다(비공개).
//
// 바인딩(대시보드 Settings에서 설정)
//   DB            D1 데이터베이스
//   VISITOR_SALT  방문자 해시용 비밀값(시크릿)
//   STATS_TOKEN   /stats 접근 토큰(시크릿)

// 이 오리진에서 온 요청만 집계한다. 열어두면 아무 사이트나 우리 카운터를 올릴 수 있다.
const ALLOWED_ORIGINS = ["https://namyikim.github.io"];

// 페이지 키도 화이트리스트로 고정한다. 임의 키를 허용하면 남이 테이블을 부풀릴 수 있다.
const ALLOWED_PAGES = ["main", "samsung", "sk_hynix"];

// 크롤러는 사람의 조회가 아니므로 세지 않는다. 완벽한 판별은 불가능하고, 목적은
// 검색엔진·모니터링 봇이 만드는 명백한 과다 집계를 걷어내는 것이다.
const BOT_PATTERN = /bot|crawler|spider|crawling|slurp|facebookexternalhit|preview|monitor|curl|wget|python-requests|headless/i;

function corsHeaders(origin) {
  const allowed = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    "Access-Control-Allow-Origin": allowed,
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

async function handleHit(request, env, url, origin) {
  const page = url.searchParams.get("page") || "";
  if (!ALLOWED_PAGES.includes(page)) {
    return json({ error: "unknown page" }, origin, 400);
  }

  const ua = request.headers.get("User-Agent") || "";
  if (BOT_PATTERN.test(ua)) {
    // 봇에게도 현재 숫자는 돌려주되 집계에는 넣지 않는다.
    const row = await env.DB.prepare("SELECT total FROM counters WHERE page = ?")
      .bind(page)
      .first();
    return json({ page, total: row ? row.total : 0, counted: false }, origin);
  }

  const now = new Date();
  const day = now.toISOString().slice(0, 10);
  const visitor = await visitorHash(request, env.VISITOR_SALT || "no-salt", day);

  const results = await env.DB.batch([
    env.DB.prepare(
      "INSERT INTO hits (page, ts, day, country, referrer, ua, visitor) VALUES (?, ?, ?, ?, ?, ?, ?)"
    ).bind(
      page,
      now.toISOString(),
      day,
      request.cf && request.cf.country ? request.cf.country : "",
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

async function handleStats(env, url, origin) {
  // 통계는 공개 대상이 아니다. 토큰이 설정되지 않았으면 아예 막는다.
  const token = url.searchParams.get("token") || "";
  if (!env.STATS_TOKEN || token !== env.STATS_TOKEN) {
    return json({ error: "unauthorized" }, origin, 401);
  }
  const days = Math.min(Math.max(parseInt(url.searchParams.get("days") || "30", 10), 1), 365);
  const since = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);

  const [totals, daily, countries, referrers] = await env.DB.batch([
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
      referrers: referrers.results,
    },
    origin
  );
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
      if (url.pathname === "/stats") return await handleStats(env, url, origin);
      return json({ error: "not found" }, origin, 404);
    } catch (err) {
      // 카운터가 실패해도 보고서 페이지는 그대로 보여야 하므로, 오류를 조용히 JSON으로 돌려준다.
      return json({ error: String(err && err.message ? err.message : err) }, origin, 500);
    }
  },
};
