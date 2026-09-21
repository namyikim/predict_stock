# S02 고정 스냅샷 로더·공통 날짜 기준선·재개 가능 실험 CLI 실행 계획

**Goal:** S01 API에 실제 OHLCV를 넣을 수 있게, 해시로 고정된 스냅샷 로더와 공통 날짜에서 기준선(현재가 유지, 운영 Ridge, 동일 OHLCV 입력 Ridge)을 비교하는 재개 가능한 실험 CLI를 만든다.
**Architecture:** 기존 실험 기반(tools/run_model_improvement.py의 RunState·start_run·data_hash·replace_with_retry, tools/run_medium_horizon.py의 고정 입력 로더·폴드 계약)을 그대로 재사용한다. 새 러너는 tools/run_weekly_sequence.py 하나, 순수 함수는 weekly_sequence_utils.py에 추가한다. 운영 노트북·원장·보고서·워크플로는 건드리지 않는다.
**Tech Stack:** Python, NumPy, pandas, scikit-learn(Ridge), unittest. 시세는 기존 `runs/medium_horizon/<target>/data_cache`의 고정 스냅샷만 읽는다.
**Spec:** [주간 모델 설계](weekly-sequence-model-plan.md), [S01](weekly-sequence-s01-implementation.md)

## 승인·진행 상태

- [x] S01 완료(main d6c8ed69).
- [x] S02 상세 실행 계획 작성(이 문서, 2026-09-22).
- [ ] 상세 실행 계획 검토.
- [ ] S02 코드 구현·검증.
- [ ] S02 완료 기록 및 main 반영 확인.

이 문서는 실행 계획이다. 아래 러너·CLI·테스트는 아직 존재하지 않는다.

## Global Constraints

- S01의 build_sequences 계약(60거래일 입력, 세션 기준 d+4 만기, 원본 종가 타깃)을 바꾸지 않는다.
- 시세는 고정 스냅샷만 읽는다. 스냅샷이 없으면 실행을 거부한다 — 새로 내려받으면 다른 스냅샷이 되므로 러너가 직접 다운로드하지 않는다. 스냅샷을 만드는 방법(Colab 또는 기존 노트북 캐시 실행)은 문서에 적되, 실행 가능하다고 안내하지 않는다.
- 모든 산출물은 data_hash·config_hash·code_commit을 담은 manifest.json과 함께 저장한다. 해시가 다르면 다른 run이다.
- 기준선 비교는 세 모델이 모두 예측을 낼 수 있는 **공통 예측일**에서만 한다. 표본 수가 다른 비교를 같은 표에 놓지 않는다.
- 스케일러는 각 학습 구간만으로 적합한다. 미래 자료를 바꿔도 과거 폴드의 예측이 바뀌지 않아야 한다(테스트).
- M07 잠금 구간(마지막 12개월)은 이미 확인한 데이터다. S02는 **개발 구간만** 쓰고 잠금 구간은 S04까지 열지 않는다. 결과 표에 "탐색 평가"라고 표시한다.
- 합성 데이터 테스트 통과를 실제 성능으로 표현하지 않는다. S02는 인프라 단계이며, 실제 스냅샷으로 돌린 수치가 있어도 그것은 기준선의 기준값이지 개선 증거가 아니다.
- TCN·확률·구간은 S02에 넣지 않는다.

## Review Focus

1. 스냅샷 계약: 어떤 파일(원본 OHLCV, 조정 여부, 기업행동, available_at)을 읽고 해시에 무엇이 들어가는가.
2. available_at·prediction_at 산정: 봉 날짜만으로 확정하지 않는다는 S01 제약을 로더가 어떻게 지키는가.
3. 폴드 계약이 run_medium_horizon의 M01(개발 6개월 외부 폴드, purge=h-1)과 같은가.
4. 공통 날짜: 운영 Ridge(특징 행렬 기반)와 OHLCV Ridge의 예측일이 정확히 교집합인가.
5. 재개: 중단 후 재실행이 완료 단위를 건너뛰고 산출물 해시가 맞지 않으면 다시 계산하는가.

## Task 1: 스냅샷 로더 (weekly_sequence_utils.py)

Files:
- Modify: weekly_sequence_utils.py
- Modify: tests/test_weekly_sequence.py

Interfaces:
- `load_ohlcv_snapshot(cache_dir, target) -> OhlcvSnapshot`
  - 읽는 파일: `<cache_dir>/target.parquet`(없으면 `target.csv`). 노트북 `load_raw` 가 ASSETS 의 키 이름으로 저장하고 종목의 키가 `target` 이다(2026-09-22 노트북 코드에서 확인).
  - 열: `open, high, low, close, adj_close, volume` (float, 유효숫자 6자리 반올림). `yf.download(auto_adjust=False)` 라 OHLC·close 는 원본이고 adj_close 만 조정값이다. 인덱스는 tz 없는 날짜(normalize).
  - `OhlcvSnapshot`: bars(DataFrame open/high/low/close/volume, 원본), adjusted(bool, 조정주가 여부), source(str), fetched_at, sha256, ticker.
  - 조정 종가와 비조정 OHLC 혼용 금지: 스냅샷에 Adj Close가 있고 Close와 다르면 **원본 Close**를 쓰고 adjusted=False로 기록한다. OHLC가 조정된 것으로 표시돼 있으면 거부한다(현재 스냅샷은 auto_adjust=False).
- `session_calendar(start, end) -> DatetimeIndex` — exchange_calendars XKRX. S01 테스트와 같은 방식.
- `availability_policy(bars, publish_hour=16, predict_hour=7, tz="Asia/Seoul") -> (available_at, prediction_at)`
  - 기본 정책: 봉은 그 세션 16:00 KST에 공개, 예측은 세션 07:00 KST. **이것은 정책이지 사실이 아니다** — 스냅샷에 실제 수집 시각 열이 있으면 그것을 우선 쓰고, 없으면 정책값을 쓰되 manifest에 `availability_policy: assumed` 로 남긴다.
- `corporate_action_flags(bars, ratio_threshold=0.5) -> Series[bool]`
  - 기업행동 자료원이 없으므로 **가격 불연속 휴리스틱**으로만 표시한다: 종가 대비 익일 시가·종가 비율이 threshold 밖이면 True. 이것이 기업행동을 다 잡는다고 주장하지 않는다 — manifest에 `corporate_actions: heuristic` 로 남기고, 결과 문서에 한계로 적는다.

- [ ] 실패 테스트: `load_ohlcv_snapshot` 미존재 → ImportError; 스냅샷 없는 디렉터리 → FileNotFoundError; 조정 OHLC 표시 → ValueError.
- [ ] 합성 스냅샷 fixture(csv, 2년치 KRX 세션)로 로더가 S01 `build_sequences`에 그대로 들어가는 것을 확인한다.
- [ ] `availability_policy` 산출이 시간대 있는 시각이고 available_at < 다음 세션 prediction_at 인지 확인한다.
- [ ] `corporate_action_flags` 가 반감 분할을 잡고, 정상 변동(±10%)은 잡지 않는지 확인한다.
- [ ] 스냅샷 sha256 이 파일 내용 변경에 반응하는지 확인한다.

## Task 2: 공통 날짜 기준선 (weekly_sequence_utils.py)

Interfaces:
- `flatten_windows(X) -> ndarray (N, lookback*5)` — Ridge 입력.
- `ridge_baseline(X_train, y_train, X_test, alpha) -> ndarray` — StandardScaler(학습 구간만)+Ridge. 운영 5일 모델과 같은 alpha(1e4)를 기본으로 하되 alpha는 설정에 둔다.
- `walk_forward_folds(prediction_dates, first_test, test_months, horizon) -> list[(train_idx, test_idx)]`
  - run_medium_horizon.evaluation_folds 와 같은 계약: 개발 구간 6개월 외부 폴드, 학습 라벨 만기(target_date)가 시험 정보 마감(prediction_date)보다 앞인 행만 학습에 쓴다(purge = horizon-1 세션).
  - **잠금 12개월은 first_test..locked_start 로 잘라 S02에서 제외한다.** locked_start 는 run_medium_horizon 의 M01 설정에서 읽어 같은 값을 쓴다.
- `common_dates(a_dates, b_dates, c_dates) -> DatetimeIndex` — 교집합.
- `persistence_baseline(y) -> zeros` — 현재가 유지(수익률 0).
- `score(y, pred) -> dict(mae, rmse, direction_hit, n)` — 주 지표 MAE, 보조 RMSE·방향 적중률.
- `block_bootstrap_ci(y, pred_a, pred_b, dates, b=2000, seed=42)` — run_medium_horizon.month_block_ci 를 그대로 호출해 두 모델 MAE 차이의 95% CI.

- [ ] 실패 테스트: purge 위반(학습 행의 target_date >= 시험 첫 prediction_date)이 0건인지 폴드마다 검사하는 테스트를 먼저 쓴다.
- [ ] 미래 자료 변경 불변: 시험 구간 이후 봉을 바꿔도 앞 폴드 예측이 같음.
- [ ] 스케일러가 학습 구간 통계만 쓰는지: 시험 구간 값을 극단으로 바꿔도 학습 구간 변환 결과 불변.
- [ ] 공통 날짜: 세 날짜 집합 중 하나에만 있는 날은 결과에서 빠지고, 표본 수가 세 모델에서 같음.
- [ ] 방향 적중률은 부호 일치 비율이며 확률로 포장하지 않는다(결과 dict 에 `p_up` 같은 키가 없음).

## Task 3: 재개 가능한 CLI (tools/run_weekly_sequence.py)

```
python tools/run_weekly_sequence.py --task S02 --target samsung --mode quick \
    --storage runs/weekly_sequence --results experiments/weekly_sequence --resume
```

- 산출물: `experiments/weekly_sequence/S02/<run_id>/{manifest.json, metrics.csv, comparisons.csv, folds.csv, decision.md, checkpoint.json}`.
- manifest: code_commit, dirty, data_hash(스냅샷 sha256들), config_hash, versions, seed, 날짜 구간, availability_policy, corporate_actions 출처, status.
- 완료 단위: `snapshot`, `sequences`, `folds`, `baseline:persistence`, `baseline:ohlcv_ridge`, `baseline:operational_ridge`(입력 pkl이 있을 때만), `comparison`.
- quick 모드: 폴드 2개·부트스트랩 200회. full: 전체 폴드·2000회.
- 운영 Ridge 기준선은 run_medium_horizon 의 고정 입력 pkl(`inputs_<mode>_<data_hash>.pkl`)이 있을 때만 계산하고, 없으면 `skipped: operational inputs missing` 으로 기록한다. 노트북을 이 러너가 실행하지 않는다.
- decision.md 는 "S02는 인프라 단계 — 채택 판단 없음"을 명시하고 세 기준선의 수치와 CI, 공통 표본 수, 한계(availability 정책 가정, 기업행동 휴리스틱, 탐색 평가)를 적는다.

- [ ] 실패 테스트: 스냅샷 없이 실행 → 종료 코드 2 와 명확한 메시지(다운로드하지 않음).
- [ ] 합성 스냅샷으로 quick 실행이 끝까지 돌고 6개 파일이 생기는지.
- [ ] 중단 복구: `baseline:ohlcv_ridge` 뒤에서 KeyboardInterrupt 를 흉내 내 재실행하면 앞 단위를 건너뛰고 이어서 완료하는지.
- [ ] 산출물 훼손: metrics.csv 를 지우면 완료 표시가 있어도 그 단위를 다시 계산하는지(run_medium_horizon.artifacts_intact 방식).
- [ ] 설정 변경 격리: alpha 를 바꾸면 다른 run_id 가 되고 이전 산출물을 덮지 않는지.
- [ ] manifest 에 인증키·토큰 문자열이 없는지(환경변수 값을 흘리지 않음).

## Task 4: 검증·인수인계·커밋

- [ ] python -m unittest tests.test_weekly_sequence -v
- [ ] python -m unittest tests.test_medium_horizon.DesignTests -v
- [ ] PREDICT_STOCK_SKIP_SMOKE=1 python -m unittest discover -s tests
- [ ] git diff --check
- [ ] 실제 스냅샷이 이 환경에 없으면 quick 실행은 합성 fixture 로만 검증했다고 적는다. 실제 수치를 만들지 않는다.
- [ ] guides/weekly-sequence-model-plan.md 의 S02 상태·진행 기록·다음 단계(S03) 갱신.
- [ ] 운영 파일 무변경 확인(git status).
- [ ] 커밋: feat: add weekly sequence snapshot loader, baselines and resumable runner
- [ ] main 반영 뒤 원격 파일·커밋 재확인. 실패 시 체크하지 않는다.

## 다음 단계

S03 은 이 러너의 `sequences`·`folds` 단위를 입력으로 쓰는 작은 causal TCN 후보(seed 42·43·44, CPU quick, 저장·재개)다.
S02 만 완료한 상태에서 TCN 이나 성능 개선을 구현했다고 말하지 않는다.
