# 5·20거래일 예측 개선 구현·검증 계획

작성일: 2026-09-10
검토 커밋: `545d3df813a719653951e31c621ce042080b9519`
상태: M00~M06 완료, M07 후보 등록·잠금 평가 완료(관찰 중), M08 미실행(2026-09-10).

## 1. 목표와 실행 범위

삼성전자·SK하이닉스의 5거래일(1주)·20거래일(약 1개월) 예측을 각각 개선한다. 가격 오차, 방향 확률, 예측 구간 품질을 구분한다. 개선을 보장하지 않으며, 기준선보다 낫지 않으면 현행을 유지한다.

이 문서는 구현 요청을 받았을 때 한 작업씩 실행하기 위한 명세다. 이번 문서 등록은 구현이나 검증 완료를 뜻하지 않는다. 기존 [다음 날 방향 모델 계획](model-improvement-plan.md)의 P00~P15와 별도로 관리한다. 그 계획의 expanding 결과를 중기 가격 예측에 그대로 적용하지 않는다.

### 세션 운영 규칙

- 사용자가 지정한 작업 ID까지만 실행한다. 범위를 지정하지 않고 이 계획의 실행을 요청하면 미완료 첫 작업 하나를 수행한다.
- 한 작업의 코드·검증·결과·문서 기록을 함께 커밋하고 push한다. 실패한 검증이나 push는 성공으로 기록하지 않는다.
- 토큰 또는 실행 시간 부족 시 완료한 하위 항목, 실패 원인, 정확한 재개 명령, 다음 실행 단위를 8절에 남긴다. 결과 없는 상위 체크는 금지한다.
- 새 세션은 이 문서와 해당 작업의 직접 선행 결과, 관련 코드만 먼저 읽는다. 노트북 전체 출력이나 과거 논문을 반복해서 읽지 않는다.
- 실험은 `PREDICT_STOCK_PUBLISH=false`. 기존 `docs/`와 `forecast_history/`를 실험 결과로 덮어쓰지 않는다.
- 공통 코드를 바꾸면 노트북 헬퍼 동기화와 단독 Colab 실행 가능성을 검증한다. 유료 LLM API나 GPU는 필수 조건이 아니다.
- 데이터 접근 실패는 합성 데이터의 성능으로 대체하지 않는다. 합성 자료는 동작·누수 테스트에만 사용한다.

## 2. 확인된 현행과 재사용할 부분

| 항목 | 현재 구현 |
| --- | --- |
| 가격 모델 | 노트북 `make_price_model`: StandardScaler + Ridge(alpha=10000) |
| 타깃 | `price_forecast_variants`: 행 d의 d-1 종가 대비 d+h-1 종가 수익률 |
| 지평 | `FORECAST_HORIZONS`: 1·5·20거래일, 각 지평 직접 학습 |
| 학습 | 변동성으로 나눈 수익률 타깃, 누적 학습, 시간순 OOF |
| 경계 제거 | TimeSeriesSplit의 gap=h-1; 실제 라벨 만기 기준도 추가 확인 필요 |
| 보정·보류 | `forecast_utils.calibrate_price_forecast`: OOF 50% 보정 / 25% 신호 선택 / 25% 평가 |
| 변동성 | simple 기본, HAR 비교 구현 존재. HAR 재도입 자체는 새 개선이 아님 |
| 거시 비교 | `price_macro_ablation`: 20일 macro 포함/제외 비교 존재 |
| 발행 | 기준선 우위 미확인 시 점 예측은 결측, 중심값은 현재가 |
| 원장 | append/evaluate/review 함수 및 최초 사전 예측 보존 테스트 재사용 |

1일 방향 모델의 시세만 설정과 가격 모델의 `feature_cols`는 같은 것으로 가정하지 않는다. M00에서 실제 열을 저장한다. LightGBM 회귀는 노트북에 과거 미채택 사유가 있으므로 첫 실험에 무조건 재추가하지 않는다.

## 3. 전체 체크리스트

상위 완료는 구현·검증·결과 기록이 끝났음을 뜻한다. 채택과는 별개다.

| ID | 완료 | 작업 | 선행 | 채택/결론 |
| --- | --- | --- | --- | --- |
| M00 | [x] | 4개 대상 조합의 기준선·실패 유형 진단 | 없음 | 현행 유지. 4조합 발행 0, raw가 유지를 이기는 조합은 sk_hynix/5뿐(평가 구간, 가설). 결함 없음 |
| M01 | [x] | 재개 가능한 중기 실험 러너와 평가 계약 | M00 | 완료. 고정 입력 로더(pkl), 개발 9폴드 + 잠금 12개월 + 내부 3블록, purge 0, 개편 전후 수치 동등 |
| M02 | [x] | 지평별 특징군 비교 | M01 | current_full 유지(4조합). inner_selected 경로는 우위 없음(samsung/20 열위). full_plus_A 는 samsung/20 보정 후만 소폭 우위 → 관찰 대상 |
| M03 | [x] | Ridge 규제·학습 기간 내부 선택 | M02 | 현행(alpha=1e4, expanding) 유지. inner_selected 발행 중심값은 4조합 동률. 삼성 20일은 현행이 유지보다 유의하게 나쁨 → M05 보류 기준 근거 |
| M04 | [x] | 5·20일 직접 방향 확률 모델 | M03 | 채택 없음. log loss 는 4조합 모두 사전확률과 동률(delta > 0). samsung/5 만 balanced accuracy 우위이나 확률 품질 개선 아님 |
| M05 | [x] | 예측 구간·보류 정책 검증 | M03 | 잔차 252 보정 채택 없음(samsung/20 열위). HAR 은 sk_hynix 5·20 구간 점수 우위이나 포함률 명목 미만 → M07 관찰 후보. 3/3 판정은 sk_hynix/5 우위 → M07 후보. 발행률 병기 |
| M06 | [x] | 해외 입력 포함 공동 학습 | M03, 방향 비교 시 M04 | 축소판 실행. pooled ≈ 단독(동률), 현행 대비 열위(samsung/20·sk_hynix/5 유의). 채택 없음 |
| M07 | [ ] | 고정 후보 사전 예측 관찰 | M04·M05, M06은 선택 | 후보 3개 등록(sk_hynix HAR 구간 5·20, sk_hynix/5 엄격 판정), 잠금 평가 1회 완료(HAR 미통과, 엄격 판정은 2/3 판정과 동일 결정). 2026-09-11부터 원장 관찰. 삼성·방향 목적은 미채택 종료 |
| M08 | [ ] | 종목·지평별 채택 또는 유지·복구 | M07 | 미실행 |

M06은 생략할 수 있으며 이유를 기록한다. M07은 과거 실험에서 기준을 통과한 후보만 등록하고, 없으면 미채택 근거를 남긴다. 관찰 기간을 기다리는 작업은 구현 완료와 상위 완료를 구분한다.

## 4. 공통 데이터·평가 계약

### 타깃과 시간 경계

- 조합은 samsung/5, samsung/20, sk_hynix/5, sk_hynix/20 네 개다. 같은 조합 안에서 동일 날짜·기준 종가·만기 종가·정보 마감 시각을 비교한다.
- 각 행에 prediction_date, origin_date, target_date, available_at, label_available_at을 보존한다. KRX 거래일·휴장일·월말을 기준으로 만기를 계산한다.
- 과거 수정주가와 원본 종가의 용도를 구분한다. 분할·배당·결측·거래정지 처리와 원장 채점 기준은 M00에서 고정한다. 단순 영업일 offset으로 휴장일을 대체하지 않는다.
- 학습 라벨은 예측 시점에 만기 가격이 이미 관측된 것만 사용한다. 내부 선택, 정규화, 보정, 보류 기준에도 같은 조건을 적용한다.
- 단순 행 gap=h-1은 보조 방어다. 결측 제거 후에도 라벨 만기와 다음 구간 정보 마감이 겹치지 않는지 날짜로 검사한다.
- 최신 수정 월별 자료는 엄밀한 과거 발표 자료로 주장하지 않는다. 발표 시각을 확인하지 못한 새 외부 자료는 후보에서 제외하거나 한계를 별도 기록한다.

### 개발 평가와 최종 평가

- M00에서 현행 재현용 기간과 개발용 시간순 폴드, 마지막 잠금 평가 기간을 별도로 저장한다. 기본 잠금 후보는 마지막 12개월이며 자료 부족 시 점수를 보기 전에 기간과 이유를 고정한다.
- 개발 구간에는 외부 6개월 폴드를 기본으로 두고, 각 외부 폴드의 과거에서만 내부 선택을 반복한다. 최소 학습 500행, 내부 검증 3개 구간을 기본으로 한다. 충족하지 못하면 제외 사유를 남기며 임의로 기준을 낮추지 않는다.
- 잠금 구간의 기존 점수를 이미 보았다면 새로운 독립 검증이라고 주장하지 않는다. 최종 확인은 M07 미래 사전 예측이다.
- 보정 → 보류 판단 → 평가 순서를 유지한다. 현행 보정 함수를 중첩 평가에 재사용할 때 입력 예측이 모두 해당 행의 과거로만 학습됐는지 검증한다.
- 기준선 비교는 현재가 유지와 현행 Ridge를 모두 포함한다. 방향은 학습 구간 클래스 사전확률을 포함한다.
- 후보별 결측으로 쉬운 날짜만 남지 않도록 공통 날짜 성능과 전체 가용 날짜 성능·제외 수를 함께 저장한다.

### 지표와 판정

| 목적 | 주 지표 | 보조 지표와 판정 |
| --- | --- | --- |
| 가격 중심 | 수익률 단위 MAE | 후보−현행, 후보−현재가 유지의 쌍체 CI 상한 < 0. RMSE·원화 MAE 별도 |
| 방향 확률 | log loss | 사전확률 대비 CI 상한 < 0. Brier·balanced accuracy 별도 |
| 방향 정확도 주장 | balanced accuracy | 후보−기준 CI 하한 > 0일 때만 개선 주장 |
| 구간 | 80% interval score | 실제 포함률·평균 폭 병기. 폭만 넓혀 포함률 개선한 결과는 채택 근거 부족 |
| 보류 | 전체 날짜 중심값 MAE | 발행일 MAE·발행률·비발행 수를 함께 공개 |

- 가격 비교는 보정 전 raw, 보정 후, 보류 후 실제 중심값을 각각 저장한다. 보류=0% 수익률 중심값을 모델이 맞힌 방향으로 세지 않는다.
- CI는 후보−기준, 95% 쌍체 블록 부트스트랩, seed=42, full 반복 2000을 기본으로 한다. 월 블록 결과와 연속 거래일 블록 길이 max(20, 2h)의 민감도 결과를 같이 기록한다.
- h거래일마다 선택한 비중첩 부분집합도 모든 시작 offset에 대해 진단한다. 그 부분집합 역시 완전히 독립이라고 단정하지 않는다. 가장 좋은 offset만 고르지 않는다.
- CI가 0을 포함하면 우위 미확인이다. 네 조합을 일괄 채택하지 않는다. 잠금 비교의 동일 목적 4개 검정은 Holm 보정도 기록하고, 개발 후보 비교는 탐색 결과로 명시한다.
- 이미 본 평가 결과에 맞춰 후보를 추가하면 실험 횟수에 기록하고 새 후보로 취급한다. 평가 후 조정된 후보는 미래 관찰을 새로 시작한다.

### 결과·재개 파일

경로: `experiments/medium_horizon/<task_id>/<run_id>/`

- `manifest.json`: task/run, code_commit, dirty 여부, 종목·지평, 실제 데이터 SHA256, config SHA256, seed, 버전, 모드, 시작·종료 시각, 폴드 날짜, 후보 수, status, 원본 경로·해시.
- `metrics.csv`: target, horizon, candidate, fold, evaluation_stage, n, MAE, RMSE, log_loss, Brier, balanced_accuracy, interval_score, interval_coverage, mean_width, issuance_rate. 해당 없는 지표는 결측과 사유.
- `comparisons.csv`: 비교명, 지표, delta, CI, common_n, 블록 설정, 보정 여부.
- `decision.md`: 결과·한계·채택/미채택/관찰·다음 단계.
- `checkpoint.json`: 완료/실패 단위 `(target,horizon,candidate,fold,seed)`와 산출물 해시.
- 원시 예측·데이터는 `runs/medium_horizon/`에 두고 Git에는 작은 요약과 경로·해시만 넣는다. data_hash에 설명 문구를 대신 넣지 않는다.

## 5. 단계별 구현과 인수 조건

### M00 — 기준선과 실패 유형 진단

읽기: 노트북 가격 예측 셀, forecast_utils.py, guides/validation.md, tests/test_price_macro.py, 기존 중기 원장·가격 결과.

- [x] 네 조합의 실제 특징·날짜·학습 행·버전·고정 스냅샷과 정보 마감을 저장한다. → `experiments/medium_horizon/M00/<run>/summary_<t>_h<h>.json`, manifest `snapshot_sha256`
- [x] 현행 5·20일 가격 모델을 quick 동작 확인 후 full 재현한다. 기존 실행을 재사용하면 코드·입력 동일성을 확인한다. → 노트북 통계 9개 값과 동등(4조합)
- [x] 현재가 유지 대비 raw/보정/발행 중심값 MAE, 보정 기울기, 발행률, 구간 포함률·폭을 저장한다. → metrics.csv·comparisons.csv(월 블록 + 연속 블록 max(20,2h))
- [x] 원장의 만기 도래 예측만 채점하고 보류·미도래·결측을 구분한다. 주가 급등락 시기와 평상시 오차는 사전 고정한 변동성 구간으로 진단한다. → 만기 도래 0건(첫 만기 09-11/10-07). 보정 구간 sigma 3분위 기준 평가 구간의 ~94%가 high
- [x] 타깃 날짜, 미래 라벨, 분할·배당 경계 이상을 검사한다. 오류 발견 시 결함 수정과 기준선 재생성을 먼저 기록한다. → purge 위반 0, 경계 이상 0(2026-07-31 급등은 두 종목 동일 날짜의 실제 사건)
- [x] 실패 원인을 신호 부족 / 과도한 축소 의심 / 구간 오차 / 데이터·채점 결함으로 나누되 원인 미확인은 가설로 표시한다. → samsung 5·20 신호 부족, sk_hynix/5 과도한 축소 의심(가설), sk_hynix/20 신호 부족+구간 오차(포함률 0.67)

완료 증거: 네 조합 기준선 파일, 검증 기간 계약, 다음 단계에서 우선 비교할 특징과 이유. 성능 개선 주장은 아직 하지 않는다.

### M01 — 중기 러너와 평가 계약

생성 후보: `tools/run_medium_horizon.py`, `medium_horizon_utils.py`, `tests/test_medium_horizon.py`. 기존 러너의 체크포인트·원자적 저장 기능을 재사용하며 범용 프레임워크로 확대하지 않는다. 실제 경로가 달라지면 이 문서도 갱신한다.

- [x] 노트북의 가격 모델/타깃 구성에서 필요한 함수를 추출하고 노트북에 동기화한다. 고정 입력에서 기존 예측과 수치 동등성을 확인한다. → forecast_utils `make_price_model`·`price_design_frame`·`price_oof_predictions`, 노트북 36셀 호출. 개편 전 M00 통계와 1e-9 이내 동등(4조합)
- [x] CLI `--task --target --horizon --mode --storage --resume`를 제공한다. 미구현 작업을 거부한다. → `--results` 추가. 두 지평은 항상 함께 계산
- [x] 동일 입력 완료 단위는 건너뛰고 데이터·설정·코드 변경은 격리한다. 완료 표시가 있어도 산출물 유실·해시 불일치 시 재계산한다. → `artifacts_intact`(요약·행·OOF 해시), 단위별 행 파일로 재개 metrics.csv = 단일 실행
- [x] 누수 테스트: 미래 가격 변경에도 이전 특징·내부 선택·보정값 불변. 5·20일 각각 라벨 만기 경계와 휴장일 검사. → tests/test_medium_horizon.py (Design/Purge/EvaluationContract)
- [x] 중단·재개 결과와 단일 실행 결과가 같고 실패 실행이 공식 원장을 수정하지 않음을 검증한다. → RunnerTests(중단 후 재개 = 단일 실행, 실패 시 원장 해시 불변, status=failed)

완료 증거: quick 네 조합 무오류, 기존 기준선 동등성, 의미 있는 회귀 테스트 통과.

### M02 — 지평별 특징군 비교

고정 모델은 현행 Ridge와 simple 변동성이다. 새 특징의 추가 효과부터 분리한다.

- [x] 실제 기존 열과 중복되는 후보는 재구현하지 않고 목록에 표시한다. → 그룹 A 13열·그룹 B 전부·그룹 C 전부 중복(decision.md, summary `duplicates_not_reimplemented`)
- [x] 현행 전체 특징과 시세만을 비교한다. 추가 그룹은 최대 3개로 제한한다. → market_only·no_macro·full_plus_A 세 후보
- [x] 그룹 A: 종목·KOSPI·반도체 해외 자산의 5/20/60일 누적 수익률, 상대 수익률·변동성. → 26열(5일은 기존 열 사용)
- [x] 그룹 B: 외국인·기관 5/20일 누적 순매수의 거래대금 대비 비율. 가격 수준에 종속된 절대금액은 피한다. → 기존 `flow_frgn_5/20`·`flow_inst_5`(순매수/20일 평균 거래량)와 같은 정의라 새 후보 없음
- [x] 그룹 C: 기존 월별 수출·거시 지표의 공개 이후 변화율. 새로운 유료 자료 없이 출처·공개 시각을 검증 가능한 항목만 사용한다. → 기존 `macro_*_change_1m/3m`·`mom` 과 중복이라 새 후보 없음
- [x] 그룹별 단독 추가를 비교하고 내부 검증에서 최대 한 그룹을 선택한다. 모든 조합의 조합 탐색은 하지 않는다. → 폴드별 내부 3구간 선택(2/3 승 규칙), inner_selected 경로 평가
- [x] 미래 데이터 변화 불변성, 롤링 창 시점, 수급 공개 지연, 동일 날짜 비교를 테스트한다. → FeatureGroupTests(그룹 A 시점·휴장일·미래 불변·공통 행). 수급은 기존 열(merge_asof 지연) 재사용

완료 증거: 네 조합의 특징 목록·제외 날짜·쌍체 결과. 다음 날 모델에서의 특징 판정을 중기 결과로 대신하지 않는다.

### M03 — 규제 강도·학습 창 선택

- [x] 후보를 Ridge alpha={100,1000,10000}, 학습 창={5년,expanding}의 최대 6개로 고정한다. M02에서 고정한 특징을 쓴다. → `ALPHA_CANDIDATES`·`WINDOW_CANDIDATES`, current_full
- [x] 각 외부 폴드마다 과거 내부 3개 검증 구간 평균 수익률 MAE로 선택한다. 동률이면 alpha가 큰 후보, 이후 현행 expanding을 우선한다. → `select_setting`(테스트)
- [x] expanding 내부 학습을 먼저 5년 자료로 잘라 버리지 않도록 전체 과거에서 창을 구성한다. 서로 다른 창이 실제 다른 행을 쓰는 테스트를 둔다. → `window_rows`(purge 된 전체 과거에서 창 적용), 5년 창 평균 1,153행 vs expanding 1,403행
- [x] 정규화·모델 선택·보정·보류를 외부 평가 이전에서 끝낸다. 외부 점수로 최종 alpha를 고르지 않는다. → 선택·기울기·발행 판정 모두 내부 3구간에서(`fold_gate`)
- [x] raw 예측이 커졌다는 이유로 채택하지 않는다. 실제 발행 중심값의 오차와 발행률을 함께 비교한다. → metrics `mean_abs_raw`·`mae_issued`·`issuance_rate`, 비교표 `mae_return_issued`

완료 증거: 폴드별 선택값·학습 행 수·네 조합 성능·소요 시간. 개선이 없으면 현행 설정 유지.

### M04 — 직접 방향 확률 모델

- [x] 5·20일 누적 수익률을 하락·보합·상승으로 분류한다. 과거 변동성×sqrt(h)×0.3 밴드를 초기 계약으로 고정하고 평가 결과로 조정하지 않는다. → `direction_labels`(DIRECTION_BAND_MULT=0.3)
- [x] 첫 모델은 규제 Logistic, C={0.003,0.01,0.03}, class_weight=None이다. 내부 시간순 log loss로 선택하고 학습 구간 클래스 사전확률과 비교한다. → 36폴드 모두 C=0.003·온도 2.0 선택
- [x] 기존 `fit_direction_model`은 1일 계약을 가정하므로 그대로 호출하지 않는다. horizon/label_end를 지원하도록 확장하거나 중기 전용 경로를 만들고 내부 purge를 검증한다. → 중기 전용 `fit_direction_probabilities` + M01 폴드(날짜 purge 0)
- [x] 결측 클래스·확률 합=1·유한값·미래 라벨 불변성·기간별 라벨 경계를 테스트한다. → DirectionModelTests + 실행 중 검사
- [x] 가격 회귀 부호의 2클래스 적중률과 3클래스 정확도를 혼합하지 않는다. 확률 모델 결과는 별도 표로 저장한다. → M04 metrics.csv 는 확률 지표만

완료 증거: 네 조합 log loss·Brier·balanced accuracy와 사전확률 쌍체 비교. 목표가 정확도 개선으로 표현하지 않는다.

### M05 — 구간과 예측 보류

- [x] 현행 simple 변동성 구간을 기준으로 한다. HAR의 기존 결과를 재검토하되 자동 채택하지 않는다. → `simple_inner_q` 기준, `har_inner_q`(구간만 HAR) 비교. 자동 채택 없음
- [x] 추가 후보는 최근 확정 잔차 252개로 보정한 구간 하나만 둔다. 표본 100개 미만이면 현행 방식으로 fallback한다. → `simple_resid252`(dev_01 fallback)
- [x] 잔차는 해당 날짜에 사전 생성됐고 만기가 도래한 예측만 사용한다. 미래 잔차 변경이 과거 구간에 영향을 주지 않는 테스트를 둔다. → `matured_residuals`(테스트)
- [x] 80% interval score, 포함률, 폭을 비교한다. 포함률 차이의 불확실성과 변동성 구간별 품질도 공개한다. → comparisons.csv(점수·포함률·폭 CI), metrics 의 coverage_low/mid/high
- [x] 가격 보류와 방향 확률을 구분한다. 보고서는 현재가 중심값을 가격 유지 예측이라고 오해시키지 않으며, 발행률을 병기한다. → 노트북 40셀에 최근 60예측일 발행률(`price_issuance_summary`) 병기

완료 증거: 보정 기간·잔차 시점·구간 점수·보류 결과. 구간 품질 개선을 방향 개선으로 표현하지 않는다.

### M06 — 공동 학습, 선택 작업

- [x] 기존 `experiments/model_improvement/panel_data.py`를 재사용하고 당시 종목 선정·생존 편향을 기록한다. → 9종목(3종목 상장일 미달 제외), 생존 편향 summary 에 기록
- [x] M02의 해외 자산·상대강도 특징을 패널에 포함한다. 같은 입력의 단독 모델과 pooled 모델, 현행 모델을 구분해 비교한다. → KOSPI·SOX 20/60일 누적 + 상대 모멘텀, current/single/pooled 세 모델 같은 행
- [x] 첫 모델은 Ridge 하나, 방향은 필요 시 Logistic 하나다. 날짜 단위로 모든 종목을 함께 분리하고 라벨 만기 purge를 적용한다. → Ridge 만(방향은 M04 결과가 동률이라 생략), 종목별 만기 purge(테스트)
- [x] 과거 데이터 없는 종목의 날짜를 생성·보간하지 않는다. 종목 ID 처리·스케일링도 학습 구간에서만 결정한다. → check_no_interpolation 0, 종목 ID 미사용, StandardScaler 는 학습 행에서만
- [x] 추가 종목 자료 확보 실패나 앞 단계 대비 기대 가치 부족 시 이유를 기록하고 보류한다. → 자료는 P10 스냅샷으로 충분해 실행함

완료 증거: 삼성·하이닉스 평가일에서 동일 입력 단독 대비 결과, 계산 비용과 편향. GPU 대형 모델은 이 계획의 필수 범위가 아니다.

### M07 — 고정 후보 사전 예측

- [x] 종목·지평·목적별 최대 한 후보를 잠금 평가 전에 고정한다. 후보가 없으면 미채택으로 종료할 수 있다. → `LOCKED_CANDIDATES`(2026-09-10). samsung·방향 목적은 미채택 종료
- [x] 잠금 평가를 한 번 실행하고 결과를 공개한다. 통과하지 못한 후보를 같은 잠금 기간에 재조정하지 않는다. → `experiments/medium_horizon/M07/decision.md`(Holm 포함). HAR 미통과, 재조정 없음
- [x] 기존 대표 모델을 유지하면서 후보 모델명·config_hash·origin/target 날짜로 별도 원장에 기록한다. 원장 보존·재실행 중복·미도래 채점·결측 fallback을 검증한다. → 노트북 `PRICE_CANDIDATES`·`price_candidate_rows`, record_id `…:price_candidate:<후보>:<h>`; review_ledger 는 "Candidate …" 제외(테스트). 로컬 quick 실행으로 행 생성 확인
- [ ] 최초 점검은 5일 후보: 공통 만기 도래 예측 60개 및 비중첩 12개 이상, 20일 후보: 120개 및 비중첩 6개 이상이다. 숫자 충족은 채택 조건이 아니라 관찰 점검 조건이다. → 관찰 중(2026-09-10 현황 0). `candidate_ledger_status` 가 summary 에 현황을 남긴다
- [ ] 매일 점수로 재조정하지 않는다. 첫 점검 이후 추가 60개 공통 채점마다 검토하되 반복 검토 이력을 보존한다. CI 불명확 시 계속 관찰하며 특히 20일은 장기간 검증이 필요하다. → 관찰 중
- [ ] 관찰일 수, 발행일 수, 만기 도래 수, 비중첩 수를 별도 보고한다. 과거 소급 예측은 미래 검증 표본으로 넣지 않는다. → 관찰 중(M07 summary `candidate_rows[].ledger`)

완료 증거: 고정 설정·실제 사전 예측·관찰 판정. 후보 등록만 완료된 시점에는 상위 체크를 비워 둔다.

### M08 — 채택·유지·복구

- [ ] 4절 기준으로 네 조합과 목적별 채택/유지/관찰 연장을 기록한다. 가격·방향·구간 채택을 분리한다.
- [ ] 채택할 때만 기본 설정을 변경하고 이전 모델·특징·보정·원장 설정을 복구값으로 저장한다.
- [ ] 노트북 단독 실행, 보고서 표현, 최초 예측 보존, 후보 장애 fallback, 전체 회귀 테스트를 수행한다.
- [ ] 모델별 실행 시간·메모리와 기존 자동 실행 환경 적합성을 기록한다.

완료 증거: 사전 예측 평가·종목/지평별 결정·복구 방법·회귀 결과. 관찰 연장 결정이면 운영 채택 완료로 표시하지 않는다.

## 6. 검증·실행 명령

현재 존재하는 검증 명령:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python -m unittest discover -s tests -p test_price_macro.py -v
python -m unittest discover -s tests -p test_forecast_improvements.py -v
python -m unittest discover -s tests -p test_notebook_structure.py -v
git diff --check
```

헬퍼 변경 시 `python tools/sync_notebook_helpers.py` 후 구조·동등성 테스트를 수행한다. 실행 경로 변경 시 `test_notebook_smoke.py`, M08에서는 `python -m unittest discover -s tests -v`를 추가한다. 문서만 바꾸는 단계에서는 전체 모델 재학습이 필요 없다.

M00·M01에서 검증한 실제 명령(두 지평을 항상 함께 계산한다; `--storage` 기본 `runs/medium_horizon`, `--results` 기본 `experiments/medium_horizon`):

```powershell
$env:PREDICT_STOCK_PUBLISH = 'false'
python -m unittest discover -s tests -p test_medium_horizon.py -v
python tools/run_medium_horizon.py --task M00 --target samsung --mode quick --resume
python tools/run_medium_horizon.py --task M00 --target samsung --mode full --resume
python tools/run_medium_horizon.py --task M00 --target sk_hynix --mode full --resume
python tools/run_medium_horizon.py --task M01 --target samsung --mode full --resume
python tools/run_medium_horizon.py --task M01 --target sk_hynix --mode full --resume
python tools/run_medium_horizon.py --task M02 --target samsung --mode full --resume
python tools/run_medium_horizon.py --task M02 --target sk_hynix --mode full --resume
python tools/run_medium_horizon.py --task M03 --target samsung --mode full --resume
python tools/run_medium_horizon.py --task M03 --target sk_hynix --mode full --resume
python tools/run_medium_horizon.py --task M04 --target samsung --mode full --resume
python tools/run_medium_horizon.py --task M04 --target sk_hynix --mode full --resume
python tools/run_medium_horizon.py --task M05 --target samsung --mode full --resume
python tools/run_medium_horizon.py --task M05 --target sk_hynix --mode full --resume
python tools/run_medium_horizon.py --task M06 --target samsung --mode full --resume
python tools/run_medium_horizon.py --task M06 --target sk_hynix --mode full --resume
python tools/run_medium_horizon.py --task M07 --target sk_hynix --mode full --resume
python tools/run_medium_horizon.py --task M07 --target samsung --mode full --resume
```

고정 스냅샷은 `runs/medium_horizon/<target>/data_cache`(+ `macro_cache`, `macro_fallback`, `macro_snapshots`)에 있어야 하며, 없으면 러너가 거부한다. 2026-09-10 실행은 `runs/model_improvement/P00/<target>/`의 스냅샷(마지막 봉 2026-09-09)을 복사해 썼다. 러너는 처음 한 번 노트북 전체를 캐시로 실행해(종목당 full 약 8분) 필요한 입력을 `runs/medium_horizon/<target>/inputs_<mode>_<data_hash>.pkl` 에 저장하고, 같은 data_hash 면 그 파일을 읽는다(M01 고정 입력 로더; 종목당 약 1분). pkl 은 Git 에 넣지 않으며 없으면 자동으로 다시 만든다.

## 7. 완료 기록 규칙

작업 완료에는 다음 네 항목이 모두 필요하다.

1. 동작·누수 검증 통과 또는 실험 중단의 구체적 근거.
2. 코드·데이터·설정 해시와 실험 횟수·환경 기록.
3. 결과 경로·채택 여부·한계 기록.
4. 이 문서의 체크리스트와 재개 지점 갱신, 커밋·push 결과 확인.

quick는 동작 검증이며 성능 채택 근거로 사용하지 않는다. 구현만 끝났다면 상세 하위 체크만 표시한다. 커밋 메시지 예: `feat(model): M03 compare medium-horizon ridge settings`.

## 8. 실행 이력·현재 재개 지점

| 날짜 | ID | 변경/결과 | 검증 | 다음 행동 |
| --- | --- | --- | --- | --- |
| 2026-09-10 | PLAN | 본 계획·README 링크 작성 | 문서 경로·체크리스트·diff 확인. 모델 실험 미실행 | M00 기준선 진단 |
| 2026-09-10 | M00 | `tools/run_medium_horizon.py`·`tests/test_medium_horizon.py`. 4조합 full 재현(노트북 통계 동등), purge·경계 검사 통과, 실패 유형 분류. 결과 `experiments/medium_horizon/M00/20260910T072442Z_samsung_m00`(decision.md), `…T073254Z_sk_hynix_m00` | 테스트 16개 통과. 발행 0/4, sk_hynix/5만 평가 구간 raw 우위(가설) | M01: 고정 입력 로더, 외부 6개월 폴드·잠금 구간(마지막 12개월), 선택 구간 CI 기록 |
| 2026-09-10 | M01 | 설계 행렬·OOF 함수를 forecast_utils 로 추출(노트북 동기화), 고정 입력 로더(pkl), 평가 계약 `evaluation_folds`(개발 6개월 폴드·잠금 12개월·내부 3블록·60행 미만 폴드 제외), 산출물 해시 재개, Windows 원자적 쓰기 재시도. 결과 `experiments/medium_horizon/M01/20260910T081026Z_samsung_m01`(decision.md), `…T081053Z_sk_hynix_m01` | 테스트 30개 통과(전체 521개 중 환경 오류 1). 개편 전후 수치 동등, purge 0, 9 개발 폴드 + 잠금 | M02: 특징군 비교(현행 전체 vs 시세만 vs 그룹 A/B/C) |
| 2026-09-10 | M02 | 후보 4(현행·시세만·월별 제외·그룹 A 26열) + 내부 선택 경로, 개발 9폴드 공통 행 비교. 결과 `experiments/medium_horizon/M02/20260910T082103Z_samsung_m02`(decision.md), `…T082115Z_sk_hynix_m02` | 테스트 36개 통과. 현행 유지(4조합), purge 0 | M03: 규제 alpha·학습 창 내부 선택(특징 = current_full) |
| 2026-09-10 | M03 | 후보 6(alpha 3 × 창 2) 내부 선택 + 폴드 발행 판정, 개발 9폴드. 결과 `experiments/medium_horizon/M03/20260910T082518Z_samsung_m03`(decision.md), `…T082535Z_sk_hynix_m03` | 테스트 40개 통과. 4조합 동률 → 현행 유지 | M04: 방향 확률 모델 |
| 2026-09-10 | M04 | 규제 Logistic 3클래스(밴드 0.3) vs 사전확률, 개발 9폴드. 결과 `experiments/medium_horizon/M04/20260910T082743Z_samsung_m04`(decision.md), `…T082806Z_sk_hynix_m04` | 테스트 45개 통과. log loss 4조합 동률 → 채택 없음 | M05: 구간·보류 정책 |
| 2026-09-10 | M05 | 구간 후보 3(simple/HAR/잔차252) + 보류 정책 4, 개발 9폴드. 보고서에 발행률 병기(`price_issuance_summary`, 노트북 동기화). 결과 `experiments/medium_horizon/M05/20260910T083330Z_samsung_m05`(decision.md), `…T083339Z_sk_hynix_m05` | 테스트 53개 통과. 잔차252 채택 없음, HAR·3/3 판정은 하이닉스 관찰 후보 | M06 보류 사유 기록 → M07 후보 고정·잠금 평가 |
| 2026-09-10 | M06 | 패널 9종목 pooled Ridge vs 단독 vs 현행(같은 행), 개발 9폴드. 결과 `experiments/medium_horizon/M06/20260910T103520Z_samsung_m06`(decision.md), `…T103540Z_sk_hynix_m06` | 테스트 56개 통과. pooled≈단독, 현행 대비 열위 → 채택 없음 | M07: 후보 고정(sk_hynix HAR 구간 5·20, sk_hynix/5 엄격 판정), 잠금 평가 1회, 원장 후보 기록 |
| 2026-09-10 | M07 | 후보 3개 고정, 잠금 평가 1회(HAR 미통과·재조정 없음, strict_gate 는 잠금에서 2/3 판정과 동일), 노트북 후보 행 기록 + review_ledger 후보 제외. 결과 `experiments/medium_horizon/M07/decision.md`, `M07/20260910T*_{sk_hynix,samsung}_m07` | 테스트 62개 통과, 로컬 quick 노트북 실행으로 후보 행 확인 | 관찰(5일 첫 점검 ≈ 2026-12, 20일 ≈ 2027-03). M08 은 현행 유지 결정·회귀 테스트·실행 환경 기록 |

- 현재 작업: M07 관찰(코드 변경 없음). 다음 실행은 M08(현행 유지 결정 기록).
- 완료: 7/9(M07 은 관찰 중이라 상위 미체크). 보류: 없음. 실제 후보 채택: 없음. 관찰 후보 3(하이닉스). M07 후보 예정: sk_hynix 5·20일 HAR 구간(구간 목적), sk_hynix 5일 3/3 발행 판정(가격 목적). 관찰 대상: full_plus_A(samsung/20, 보정 후 −0.045%p, 탐색 결과).
- 먼저 읽을 파일: 본 문서, `experiments/medium_horizon/M07/decision.md`, M06~M00 decision.md, `tools/run_medium_horizon.py`(load_inputs·evaluation_folds·analyse_horizon), forecast_utils.py의 `price_design_frame`·`calibrate_price_forecast`.
- 미해결: (1) 원장에 발행 판정에 쓰인 선택 구간 CI가 없어 하이닉스 20일 발행이 2026-09-08부터 뒤집힌 이유를 원장만으로 볼 수 없다 — M02 이후 결과 파일(comparisons.csv)에는 남기고, 원장 열 추가는 M07에서 정한다. (2) 평가 구간이 2024-08 이후 고변동성 한 국면이라 M05 구간 보정을 우선한다. (3) 입력 pkl 은 pandas 버전이 바뀌면 다시 만들어야 한다(data_hash 만 검사).
- 마지막 검증: `python -m unittest tests.test_medium_horizon`(62개 통과). M07 full 두 종목(exit 0, purge 0). 노트북 quick(sk_hynix, 캐시) 실행으로 후보 행 3개 생성 확인.

중단 시 아래 양식을 채워 이어서 실행한다.

```text
작업 ID / run_id:
기준 코드 커밋 / 데이터 해시 / 설정 해시:
완료한 하위 항목과 증거:
진행 중인 실행 단위 및 프로세스 상태:
실패·대기 사유:
마지막 검증 명령과 실제 결과:
결과·체크포인트 경로:
정확한 재개 명령:
다음 실행 단위:
커밋 / push 상태:
```

재개 요청 예시:

```text
guides/medium-horizon-improvement-plan.md를 읽고 M00만 진행해주세요.
실제 완료한 항목만 체크하고 결과·검증·다음 재개 지점을 기록한 뒤 커밋·push해주세요.
토큰이 부족하면 중간 기록을 남기고 다음 작업으로 넘어가지 마세요.
```
