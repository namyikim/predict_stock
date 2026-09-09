-- 조회수 카운터 D1 스키마
-- Cloudflare 대시보드 → Storage & Databases → D1 → 해당 DB → Console 에 그대로 붙여넣고 실행한다.

-- 조회 1건마다 한 행. 통계(일별·국가별·유입경로별)는 전부 이 표에서 나온다.
-- visitor는 IP가 아니라 "소금 + 날짜 + IP + UA"의 해시다. 날짜가 섞여 있으므로
-- 날짜를 넘어 같은 사람을 이어붙일 수 없고, 하루 단위 순방문자만 셀 수 있다.
CREATE TABLE IF NOT EXISTS hits (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  page     TEXT NOT NULL,          -- main / samsung / sk_hynix / china / metals / trends / interest
  ts       TEXT NOT NULL,          -- ISO8601 UTC
  day      TEXT NOT NULL,          -- YYYY-MM-DD (UTC) — 집계 편의용
  country  TEXT NOT NULL DEFAULT '',
  region   TEXT NOT NULL DEFAULT '',   -- 시/도. IP 추정이라 대체로만 맞다
  city     TEXT NOT NULL DEFAULT '',   -- 시/군/구. NAT·회사 회선 때문에 편차가 크다
  referrer TEXT NOT NULL DEFAULT '',
  ua       TEXT NOT NULL DEFAULT '',
  visitor  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_hits_day_page ON hits (day, page);
-- 같은 방문자의 최근 조회를 찾는 데 쓴다(중복 억제).
CREATE INDEX IF NOT EXISTS idx_hits_visitor ON hits (visitor, page, day);
-- 날짜별 지역·도시 집계용.
CREATE INDEX IF NOT EXISTS idx_hits_day_geo ON hits (day, region, city);

-- 누적 조회수. hits를 매번 COUNT(*) 하면 표가 커질수록 느려지므로 따로 둔다.
CREATE TABLE IF NOT EXISTS counters (
  page  TEXT PRIMARY KEY,
  total INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO counters (page, total) VALUES ('main', 0), ('samsung', 0), ('sk_hynix', 0), ('china', 0), ('metals', 0), ('trends', 0), ('interest', 0);

-- 이미 표를 만든 뒤에 위치 열을 추가하는 경우에만 아래 두 줄을 실행한다.
-- (새로 만든 DB라면 위 CREATE TABLE에 이미 들어 있으므로 실행하면 오류가 난다.)
-- ALTER TABLE hits ADD COLUMN region TEXT NOT NULL DEFAULT '';
-- ALTER TABLE hits ADD COLUMN city TEXT NOT NULL DEFAULT '';
