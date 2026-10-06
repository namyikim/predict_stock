-- 기존 DB 전용: PRAGMA table_info(hits)로 확인하고 없는 컬럼의 문장만 한 번씩 실행한다.
-- 신규 DB는 schema.sql에 이미 포함되어 있으므로 이 파일을 적용하지 않는다.
ALTER TABLE hits ADD COLUMN source TEXT NOT NULL DEFAULT '';
ALTER TABLE hits ADD COLUMN edition_day TEXT NOT NULL DEFAULT '';
ALTER TABLE hits ADD COLUMN edition_phase TEXT NOT NULL DEFAULT '';
