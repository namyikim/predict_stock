# 가격·거래량 시계열 기반 5거래일 예측 — 설계 및 인수인계

상태: 설계 승인. S01(시퀀스·날짜 계약)·S02(스냅샷 로더·기준선·재개 CLI) 구현 완료. 모델 구현(S03~)·실제 데이터 성능 실험(S04)·운영 채택은 미완료.
대상: 삼성전자(samsung), SK하이닉스(sk_hynix).
사용자 요청: 과거 주가 흐름으로 주간 예측을 개선하고, 작업을 하나씩 완료할 때마다 검증 결과와 함께 GitHub main에 반영한다. 중단 시 Claude가 이 문서로 재개한다.

## 목표와 비목표

- 매일 쌓인 실전 기록이 아니라 과거 여러 해 OHLCV로 학습한다.
- 최근 60거래일을 기본 입력으로 5거래일 종가 수익률을 직접 예측한다. 120일 창은 개발 구간에서만 비교할 제한된 후보다.
- 차트 이미지를 쓰지 않는다. 날짜와 수치가 보존된 가격·거래량 시계열을 사용한다.
- 기존 운영 모델, 공식 원장, 일일 자동 실행은 변경하지 않는다. 개선이 확인된 경우에만 별도 승인 후 연결한다.
- 예측 오차 감소를 보장하지 않는다. 미래 실전 기록은 학습 시작의 필수 조건이 아니라 최종 운영 검증이다.

## 기존 구현과 중복 방지

먼저 guides/medium-horizon-improvement-plan.md, tools/run_medium_horizon.py, forecast_utils.py, tests/test_medium_horizon.py를 읽는다.
이미 1·5·20거래일 Ridge 가격 모델, 날짜 기준 누수 방지, 시간순 검증, 후보 원장과 M00~M08 실험 기록이 있다.
M02 특징군 비교나 M03 Ridge 탐색을 새 성과로 재포장하지 않는다. 이번 차이는 OHLCV의 연속적인 흐름을 직접 학습하는 작은 시계열 모델이다.
기존 M07 잠금 구간은 이미 확인한 데이터다. 동일 기간을 새 독립 검증이라고 부르지 않는다.

## 추천 설계와 대안

1. 추천: 같은 시계열 입력의 Ridge 기준선 + 작은 causal TCN 회귀 후보.
2. 대안: Transformer. TCN 비교 완료 전에는 구현하지 않는다. 후보를 늘리면 검증 비용과 과적합 위험이 증가한다.
3. 이미지 차트 학습: 가격 축·해상도 변환의 복잡성이 있어 이번 범위에서 제외한다.

TCN은 별도 실험 모듈에 두고 운영 노트북에 즉시 삽입하지 않는다.
CPU quick 검증을 제공하고, 실제 전체 학습은 Colab에서 재개 가능하도록 한다.
새 외부 서비스·유료 API·GPU 구매는 요구하지 않는다.

## 데이터 및 날짜 계약

- S01에서 기존 가격 모델과 타깃 정의를 정확히 맞추고 테스트로 고정한다.
- 장 전 예측은 직전 완료 거래일 종가까지만 입력한다.
- 기존 모델 기준: 예측 거래일 d의 입력 마지막 날은 d-1, 5일 만기는 d+4 거래일이다.
- 타깃 수익률 = 만기 종가 / 기준 종가 - 1. 표시 가격 = 기준 종가 × (1 + 예측 수익률).
- available_at, origin_date, prediction_date, target_date, label_available_at을 보존한다.
- KRX 거래일 달력을 사용한다. 휴장·누락 봉을 임의 가격으로 보간하지 않는다.
- 원본 OHLCV, 출처, 수집 시각, 수정주가 정책, 파일 해시를 기록한다.
- 가격 입력은 수익률·상대 OHLC 범위로, 거래량은 과거 기준 상대값으로 변환한다.
- 조정 종가와 비조정 OHLC 혼용을 금지한다. 분할·배당 처리와 가격 환산 기준을 문서화한다.
- 스케일러는 각 학습 구간만으로 적합한다. 미래 데이터를 바꿔도 과거 입력이 바뀌지 않아야 한다.

## 평가 계약

- 현재가 유지, 기존 운영 Ridge, 동일 OHLCV 입력 Ridge, TCN을 공통 날짜에서 비교한다.
- 주 지표: 5일 수익률 MAE. 보조: 원화 MAE, RMSE, 방향 적중률.
- 상승·하락 확률은 회귀값 부호를 확률로 포장하지 않는다. 별도 확률 모델·보정 검증 전에는 제공하지 않는다.
- 80% 가격 범위는 과거에 만기가 확정된 검증 잔차로 보정하고 포함률·폭·interval score를 함께 평가한다.
- 시간순 walk-forward 검증, 학습 라벨 만기와 검증 정보 마감의 겹침 제거, 내부 검증으로만 설정·early stopping 선택.
- 평가일이 겹치는 5일 타깃을 독립 표본으로 취급하지 않는다. 블록 부트스트랩 95% CI와 모든 시작 offset의 비중첩 진단을 기록한다.
- TCN은 고정한 소수 seed(예: 42, 43, 44)를 모두 보고하고 최고 seed만 선택하지 않는다.
- 최종 비교 기간·후보 설정·데이터 해시를 점수 계산 전에 고정한다. 이미 본 기간은 탐색 평가라고 표시한다.
- 채택 검토: 현재가 유지와 운영 Ridge 각각 대비 오차 차이 CI 상한이 0보다 작아야 한다. 비용·구간 품질·발행률도 함께 확인한다.
- 합성 데이터는 코드 동작 테스트만 증명한다. 실제 주가 성능 결과로 사용하지 않는다.

## 단계별 체크리스트

각 단계는 테스트 → 결과·인수인계 문서 갱신 → 커밋 → main 반영 확인 순서로 끝낸다.

| 단계 | 상태 | 산출물 / 종료 조건 |
| --- | --- | --- |
| S00 | 완료 | 설계 승인(사용자), S01 상세 실행 계획 작성 |
| S01 | 완료(main d6c8ed69) | 기존 타깃과 동등한 OHLCV 시퀀스·날짜 계약 및 누수/휴장/분할 테스트 |
| S02 | 완료(코드·테스트) | 해시 고정 데이터 로더, 공통 날짜 기준선, 재개 가능한 실험 CLI와 중단 복구 테스트 |
| S03 | 미시작 | 작은 TCN 회귀 후보, seed 고정, CPU quick 학습·저장·재개 테스트 |
| S04 | 미시작 | 실제 데이터 시간순 full 비교와 오차·불확실성·계산비용 결과; 실패/미개선도 기록 |
| S05 | 미시작 | 과거 잔차 기반 가격 범위 및 입력 그룹 제거 비교; 기여도와 인과 구분 |
| S06 | 미시작 | 통과 시에만 별도 승인 후 후보 원장·보고서 연결, 미통과 시 현행 유지 |

구현됨(S01·S02): weekly_sequence_utils.py, tools/run_weekly_sequence.py, tests/test_weekly_sequence.py,
tests/test_weekly_sequence_runner.py. 산출물 경로: experiments/weekly_sequence/<task>/<run_id>/,
시세 스냅샷: runs/weekly_sequence/<target>/data_cache/target.parquet(러너가 내려받지 않는다).
실행 예: `python tools/run_weekly_sequence.py --task S02 --target samsung --mode quick --resume`
— 이 환경에는 실제 스냅샷이 없어 합성 fixture 로만 검증했다.

## 실험 산출물과 체크포인트

manifest.json: 코드 커밋, dirty 여부, 입력·설정 해시, 라이브러리 버전, seed, 날짜 구간, 실행 상태.
metrics.csv / comparisons.csv: 종목·후보·기간·표본 수·가격 및 구간 오차·CI.
decision.md: 채택/미채택/미확정, 근거, 한계.
checkpoint.json: 완료 단위, 산출물 해시, 다음 단위. 설정/데이터가 달라지면 다른 run으로 분리.
모델 가중치와 대용량 원시 입력은 Git에 올리지 않고 재현·보관 위치를 기록한다.
개별 예측에는 기준일·만기일·학습 종료일·모델/입력 해시를 남긴다.

## Claude 인수인계

1. git status로 사용자 변경을 확인한다. 깨끗할 때 git pull --ff-only origin main으로 최신화한다.
2. 이 문서와 기존 중기 계획을 읽고 이미 완료된 실험·운영 설정을 확인한다.
3. 현재 다음 단계는 사용자 설계 검토와 상세 실행 계획 작성이다. 구현 완료로 오해하지 않는다.
4. 승인 후 S01 하나부터 수행한다. 첫 구현에서 실패 테스트를 먼저 확인한다.
5. 각 단계 후 완료 파일, 테스트 명령/결과, 남은 한계, 실제 재개 명령을 아래 기록에 추가한다.
6. git push 또는 승인된 GitHub 연결로 main 반영을 확인한다. 실패 시 미반영이라고 명시한다. 강제 푸시 금지.
7. 인증키를 문서·코드·로그에 기록하지 않는다. 기존 공식 예측과 평가 필드를 실험으로 덮어쓰지 않는다.

## 진행 기록

- S00: 기존 5·20일 러너와 계획, 테스트 실행 설정 확인. 사용자 요청에 따른 설계·단계·재개 규칙 작성.
- 검증: 문서 내 단계 상태와 미구현 명령 구분 점검. 코드/모델 변경 없음; 성능 실험 미실행.
- 다음: 설계 검토 승인 → 상세 실행 계획 → S01 구현.
- S01 (2026-09-22, Claude 재개): Codex 가 계획 작성 후 토큰 만료로 중단한 지점에서 이어 구현.
  - 완료 파일: weekly_sequence_utils.py(build_sequences, causal_features, SequenceBatch, CHANNELS),
    tests/test_weekly_sequence.py(28개).
  - 실패 테스트 먼저 확인: 모듈 미존재 ModuleNotFoundError → 구현 후 통과.
  - 검증: `python -m unittest tests.test_weekly_sequence -v` 28/28 OK(경고를 오류로 둬도 OK),
    `python -m unittest tests.test_medium_horizon.DesignTests` OK,
    `PREDICT_STOCK_SKIP_SMOKE=1 python -m unittest discover -s tests` 1346개 OK(skipped 5),
    `git diff --check` 문제 없음, 변경 파일은 새 파일 둘뿐(운영 노트북·원장·보고서·워크플로 무변경).
  - 운영 타깃과의 일치: 같은 종가 계열에서 forecast_utils.price_design_frame 의 future_return
    (close[d+h-1]/close[d-1]-1)과 전 표본 값이 같음을 테스트로 고정.
  - 계획 대비 정한 것: ① 자료 시작 전 표본은 insufficient_history 로 따로 기록. ② 봉 결측 검사는
    워밍업 시작부터 만기까지 전체 범위(만기 사이 중간 봉 결측도 제외 — 보수적). ③ 사유가 여럿이면
    insufficient_history → pending_target → missing_bar → corporate_action → unavailable_input →
    zero_volume_mean 순으로 하나만 기록. ④ metadata.available_at 은 워밍업+입력 봉 공개 시각의 최댓값.
  - 거부 테스트는 메시지까지 확인한다. 메시지 검사를 넣자 원래 '주말 봉' 테스트가 엉뚱한 이유
    (available_at 날짜 불일치)로 통과하고 있었음이 드러나 고쳤다.
  - 남은 한계: 실제 OHLCV 로 돌려 보지 않았다(합성 fixture 만 — 성능에 대해 아무것도 말하지 않는다).
    기업행동 자료원과 실제 공개 시각(available_at) 산정은 S02 로더의 책임이다. 운영 모델은 종목 자체
    거래일 행 기준으로 이동하므로, 종목 봉이 빠진 날(거래정지 등)에는 두 정의가 다를 수 있다 — S02
    비교는 계획대로 공통 날짜에서만 한다.
  - 재개 명령: `python -m unittest tests.test_weekly_sequence -v`
  - 다음: S02 — 해시 고정 데이터 로더(OHLCV·기업행동·available_at), 공통 날짜 기준선, 재개 가능한
    실험 CLI와 중단 복구 테스트. S02 상세 실행 계획부터 작성한다.
- S02 (2026-09-22, Claude): 상세 계획(guides/weekly-sequence-s02-implementation.md) 작성 후 구현.
  - 완료 파일: weekly_sequence_utils.py 에 로더(load_ohlcv_snapshot, session_calendar,
    availability_policy, corporate_action_flags)와 기준선(walk_forward_folds, flatten_windows,
    ridge_baseline, persistence_baseline, common_dates, score) 추가; tools/run_weekly_sequence.py(러너);
    tests/test_weekly_sequence.py +22개, tests/test_weekly_sequence_runner.py 6개.
  - 검증: 신규 28개 OK(경고를 오류로 둬도 OK), test_medium_horizon.DesignTests OK, 전체 1374개 OK
    (skipped 5), git diff --check 문제 없음, 운영 파일 무변경.
  - 스냅샷 계약: 노트북 load_raw 형식(<cache>/target.parquet, open/high/low/close/adj_close/volume,
    auto_adjust=False). 원본 close 를 쓰고 adj_close 는 무시(adjusted=False 기록), 조정 OHLC 표시는 거부.
    parquet(ns)·csv(us) 인덱스 정밀도 차이를 ns 로 통일했다.
  - 정한 것: ① 봉 공개 시각은 실제 수집 시각이 있으면 그것, 없으면 세션 16:00 KST 정책값 —
    manifest 에 availability_policy: assumed 로 남긴다. ② 기업행동은 KRX 일일 제한폭(±30%)을 시가·종가가
    함께 넘는 불연속 휴리스틱으로만 표시(corporate_actions: heuristic). 처음 0.5배 기준은 2:1 분할이
    경계값(0.5014)에 걸려 안 잡혀 제한폭 근거로 바꿨다. ③ 폴드는 run_medium_horizon 과 같은 계약
    (2021-01 부터 6개월, purge = 만기 < 시험 시작)이되 잠금 12개월은 만들지 않는다(S04 까지 닫음).
    ④ 방향 적중률은 예측이 방향을 부른 행에서만 센다 — 현재가 유지(예측 0)는 NaN. 0 으로 세면
    '항상 틀린 모델'로 보였다. ⑤ 운영 Ridge 기준선은 medium_horizon 고정 입력 pkl 이 있어야 하며 S02 는
    skipped 로 기록만 한다(연결은 S04).
  - 재개: WEEKLY_SEQ_FAIL_AFTER=<unit> 로 중단을 흉내 내 완료 단위 건너뜀·산출물 훼손 시 재계산·설정
    변경 시 별도 run·manifest 에 토큰 문자열 없음을 테스트로 고정했다.
  - 합성 무작위보행에서 ohlcv_ridge ≈ persistence(MAE 차이 +0.0004, CI 가 0 을 포함) — 신호가 없는
    자료에서 기대되는 결과이며 코드 동작 확인일 뿐이다.
  - 남은 한계: 실제 스냅샷으로 돌리지 않았다. 실제 실행은 Colab 또는 medium_horizon data_cache 를
    runs/weekly_sequence/<target>/data_cache 로 복사한 뒤 위 명령으로 한다.
  - 재개 명령: `python -m unittest tests.test_weekly_sequence tests.test_weekly_sequence_runner -v`
  - 다음: S03 — 작은 causal TCN 회귀 후보(seed 42·43·44 모두 보고), CPU quick 학습·저장·재개 테스트.
    S02 러너의 sequences·folds 단위를 입력으로 쓴다. S03 상세 실행 계획부터 작성한다.
