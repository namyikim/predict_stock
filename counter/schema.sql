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
  visitor  TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',          -- email / 빈 값은 출처 미분류
  edition_day TEXT NOT NULL DEFAULT '',     -- 메일 본문의 발행 회차일
  edition_phase TEXT NOT NULL DEFAULT ''    -- pre_open / post_close
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

-- 종목 보고서 상단 구독(2026-09-28). 주소는 소문자로 맞춰 (주소, 페이지)마다 한 행.
-- 수집 항목은 이메일·신청한 페이지·신청 시각뿐이다. IP·위치는 남기지 않는다.
CREATE TABLE IF NOT EXISTS subscribers (
  email TEXT NOT NULL,
  page  TEXT NOT NULL,           -- samsung / sk_hynix
  ts    TEXT NOT NULL,           -- 신청 시각 ISO8601 UTC
  PRIMARY KEY (email, page)
);

-- 신청·해지 횟수 제한용. 하루 단위 방문자 해시만 두고, scheduled()가 지난 날짜 행을 지운다.
CREATE TABLE IF NOT EXISTS subscribe_log (
  visitor TEXT NOT NULL,
  day     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subscribe_log ON subscribe_log (visitor, day);

-- 보고서 갱신 알림(2026-10-04). 이벤트에는 공개 요약만, 수신 정보는 비공개 발송 표에 둔다.
CREATE TABLE IF NOT EXISTS mail_events (
  id TEXT PRIMARY KEY, target TEXT NOT NULL, phase TEXT NOT NULL,
  session_date TEXT NOT NULL, content TEXT NOT NULL, source_revision TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mail_deliveries (
  event_id TEXT NOT NULL, email TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, lease_until TEXT NOT NULL DEFAULT '',
  next_attempt TEXT NOT NULL DEFAULT '', sent_at TEXT, provider_id TEXT, error_code TEXT,
  PRIMARY KEY (event_id, email)
);
CREATE TABLE IF NOT EXISTS mail_unsubscribe (
  email TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_mail_events_created ON mail_events(created_at);
CREATE INDEX IF NOT EXISTS idx_mail_deliveries_email ON mail_deliveries(email);

-- 하루 두 회차 묶음과 관리자 발신 주소(2026-10-04). 기존 표는 그대로 두고 추가한다.
CREATE TABLE IF NOT EXISTS mail_editions (
  id TEXT PRIMARY KEY, day TEXT NOT NULL, phase TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(day, phase)
);
CREATE TABLE IF NOT EXISTS mail_settings (
  id INTEGER PRIMARY KEY CHECK (id=1), from_email TEXT NOT NULL, updated_at TEXT NOT NULL
);

-- 자동 발송 일시 중지와 회차 취소. 취소 표식을 남겨 Cron의 재생성을 막는다.
CREATE TABLE IF NOT EXISTS mail_controls (
  id TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);

-- 카카오 본인 연결. 토큰은 AES-GCM 암호문만 저장하고 연결 계정은 관리자 확인 후 고정한다.
CREATE TABLE IF NOT EXISTS kakao_connection (
  id INTEGER PRIMARY KEY CHECK(id=1), owner_id TEXT NOT NULL DEFAULT '',
  app_id TEXT NOT NULL DEFAULT '', token_cipher TEXT NOT NULL DEFAULT '',
  access_expires INTEGER NOT NULL DEFAULT 0, refresh_expires INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'disconnected',
  enabled INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
  updated_at INTEGER NOT NULL DEFAULT 0, lease_owner TEXT NOT NULL DEFAULT '',
  lease_until INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kakao_oauth (
  state_hash TEXT PRIMARY KEY, session_hash TEXT NOT NULL DEFAULT '',
  config_hash TEXT NOT NULL, expires_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'issued',
  base_version INTEGER NOT NULL, token_cipher TEXT NOT NULL DEFAULT '',
  owner_id TEXT NOT NULL DEFAULT '', app_id TEXT NOT NULL DEFAULT '',
  access_expires INTEGER NOT NULL DEFAULT 0, refresh_expires INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_kakao_oauth_expiry ON kakao_oauth(expires_at);
