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
