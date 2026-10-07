# 카카오톡 K3 발송 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 게시된 삼성전자·SK하이닉스 보고서 두 개를 본인의 나와의 채팅에 하루 두 회차로 전달한다.
**Architecture:** 기존 검증된 mail_events를 읽고 카카오 전용 설정·발송 원장으로 처리한다. 원자적 발송 점유와 불확실 결과의 재전송 금지로 중복을 막는다. 관리자 화면에서 시험 발송과 자동 발송을 명시적으로 선택한다.
**Tech Stack:** 단일 ES module Worker, D1 SQLite, 관리자 JavaScript, Python unittest/Node SQLite.
**Spec:** [기존 승인된 연결·발송 설계](kakao-notification-setup.md#인증발송-설계)

## 공통 제약

- 본인 계정만 대상. 자동 발송 기본 꺼짐. 기존 이메일 상태·구독자 유무와 독립.
- 본문 최대 200자, 공식 기본 텍스트 API, 사이트 URL만 허용. 날짜·회차·두 종목 원문 요약과 링크 포함.
- API result_code=0은 전송 접수 성공이며 수신·열람으로 표시하지 않는다.
- 토큰·예외/응답 원문을 API·로그에 노출하지 않는다. 인증 상태/버전을 최종 발송 점유에서 재검사한다.
- 실제 메시지는 관리자가 대상과 시험 내용을 확인하고 버튼을 누른 경우에만 발송한다.
- 기존 수동 Worker 전체 파일 배포와 D1 문장별 적용 방식을 유지한다.

## 검토 초점

- 두 Cron 또는 버튼 중복 호출: 같은 전송 행은 한 요청만 외부로 보낸다.
- 토큰 갱신/재연결: 회차 중복 키는 동일 앱·본인 계정을 기준으로 유지한다.
- 전송 중 연결 해제/중지: 최종 점유 전이면 취소, 이미 시작한 전송은 회수 불가를 안내한다.
- 외부 5xx/타임아웃/잘못된 성공 본문: 확인 필요로 종료하고 자동 재전송하지 않는다.
- 구 D1 또는 카카오 API 장애: 기존 이메일·채점 Cron은 계속 동작한다.

## Task 1: 전용 원장·발송 API

**Files:** counter/worker.js, counter/schema.sql, counter/migrations/20261007_kakao_delivery.sql, tests/kakao_delivery_cases.mjs, tests/test_kakao_delivery.py.
**Interfaces:** kakaoReportTemplate(reports, day, phase), processKakao(env, now=Date), GET /kakao/delivery-status, POST /kakao/control, POST /kakao/test.
- [x] 실제 Worker·SQLite 테스트에서 미구현 엔드포인트 404를 확인한다.
- [x] 전용 settings/deliveries 표와 기본 꺼짐·신규 게시본만 대상인 설정을 구현한다.
- [x] 보고서 두 종목 대기, 200자/URL 검증, 원문 요약과 버튼을 구현한다.
- [x] 점유·권한/연결 재검사·타임아웃 확인 필요·429 제한적 재시도·실패 분류를 구현한다.
- [x] 인증 없는 발송, 중복/동시 호출, 중지/해제 경합, 토큰 갱신, 쿼터·잘못된 본문, 구 스키마를 검증한다.
Run: python -m unittest discover -s tests -p 'test_kakao_delivery.py' -v. Expected: 모든 검사 통과.

## Task 2: Cron·관리자 연결

**Files:** counter/worker.js, docs/admin/index.html, docs/admin/kakao_auth.js, docs/admin/kakao_delivery.js, tests/admin_kakao_delivery_cases.mjs, tests/test_kakao_delivery.py.
**Interfaces:** Task 1의 세 HTTP 경로. 자동 발송은 별도 */5 * * * * Cron에서 진행하며 이메일 분기와 독립한다.
- [x] 구 Worker/DB, 연결 상태별 버튼, 미리보기·확인 취소·중복 클릭, 관리자 헤더 전달 테스트를 먼저 작성한다.
- [x] 자동 발송 켜기/끄기, 시험 본문과 본인 수신 확인, 최근 발송 내역을 구현한다.
- [x] Cron에서 카카오 예외를 격리하고 기존 이메일·채점 검사를 실행한다.
Run: python -m unittest discover -s tests -p 'test_kakao*.py' -v. Expected: 모든 검사 통과(workerd가 없으면 해당 검사만 skip).

## Task 3: 배포 문서·검증·반영

**Files:** counter/README.md, guides/kakao-notification-setup.md, guides/email-traffic-tracking-plan.md.
- [x] D1 문장별 적용, Worker 전체 배포, 제품 링크 도메인, 시험 발송, 자동 발송 켜기 순서를 기록한다.
- [x] 관련 회귀 검사와 전체 검사 결과를 기록하고 독립 리뷰의 중요한 문제를 수정한다.
- [x] 승인된 push 범위에서 최신 main에 반영하고 사용자에게 D1 첫 문장부터 안내한다.

## 실행 판단 기록

- 기존 설계와 ‘진행해주세요’에 따라 같은 대화에서 직접 구현한다. 전용 scratch 체크아웃의 기능 브랜치를 사용한다.
- 연결 version은 토큰 갱신에도 증가한다. 같은 앱/계정·날짜·회차로 중복 키를 고정해 갱신/재연결에 따른 재발송을 막는다.
- 자동 켜기 이후 생성된 회차만 발송한다. 끄기/켜기마다 설정 세대를 바꾸어 기존 대기 행의 부활을 막는다.
- K4 유입 집계는 후속이다. 이 단계에서는 utm_source=kakao 링크와 필요한 발송 제어/이력만 제공한다.
