-- 카카오 발송 설정. 인증 표의 enabled와 함께 명시적으로 켠 이후 새 회차만 보낸다.
CREATE TABLE IF NOT EXISTS kakao_delivery_settings (
  id INTEGER PRIMARY KEY CHECK(id=1), enabled_since INTEGER NOT NULL DEFAULT 0,
  generation INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL DEFAULT 0
);
-- 메일 발송과 독립된 원장. 외부 응답 유실 시 uncertain으로 남겨 자동 재전송하지 않는다.
CREATE TABLE IF NOT EXISTS kakao_deliveries (
  id TEXT PRIMARY KEY, recipient_key TEXT NOT NULL, kind TEXT NOT NULL,
  day TEXT NOT NULL, phase TEXT NOT NULL, payload TEXT NOT NULL,
  settings_generation INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  lease_until INTEGER NOT NULL DEFAULT 0, next_attempt INTEGER NOT NULL DEFAULT 0,
  error_code TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_kakao_deliveries_created ON kakao_deliveries(created_at);
