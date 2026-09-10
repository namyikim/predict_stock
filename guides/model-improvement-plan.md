# 삼성전자·SK하이닉스 모델 성능 개선 실행 계획

> **For agentic workers:** 이 문서의 작업을 한 번에 하나씩 실행한다. 실행 시 superpowers:executing-plans를 사용하고, 사용자가 지정한 작업 ID까지만 진행한다. 체크박스는 구현·검증 증거를 남긴 뒤 갱신한다.

**Goal:** 기존 일일 보고서를 유지하면서, 삼성전자·SK하이닉스의 방향 예측과 확률 품질을 동일한 시간순 평가에서 개선한다.

**Architecture:** 기존 Logistic·LightGBM 및 데이터·검증·원장 함수를 재사용한다. 실험 설정과 결과를 별도로 저장하고, 데이터 기여도 → 학습 기간·가중치 → 모델 결합 → 선택적 논문 모델 순서로 비교한다. 성능이 확인된 후보만 공식 모델에 반영한다.

**Tech Stack:** Python, pandas, NumPy, scikit-learn, LightGBM, Google Colab. PyTorch 및 시계열 사전학습 모델은 선택 실험에서만 사용한다. 기존 requirements.txt의 버전을 기준으로 실행 환경을 기록한다.

**Spec:** 2026-09-09 사용자 요청: “계획을 md파일로 Git에 올리고, 토큰 사용제한 때문에 하나씩 적용한 뒤 체크리스트로 관리.” 직전 논문 검토와 저장소의 guides/validation.md·guides/development.md가 상세 기준이다. 이 파일 자체가 실행 범위와 인수 조건을 포함하는 작업 명세다.

**작성일:** 2026-09-09
**검토 저장소:** namyikim/predict_stock
**검토 커밋:** 9cec346da480c8ab60f4016fcf2619d34896ed97
**현재 상태:** P00 진행 중. 다종목 로컬 실행 결함 수정·회귀 테스트 완료, 고정 스냅샷 quick/full 평가 진행 중.
**실행 지시 갱신(2026-09-10):** 사용자가 작업별 구현·검증·push 후 순차 진행을 요청했다. 한 작업씩 완료 증거를 남기며 진행하고, 미완료 작업의 재개 지점을 Git에 보존한다.

## 1. 운영 규칙과 전역 제약

- 한 번에 작업 ID 하나를 수행하고 단계별 커밋·push 후 다음 항목으로 진행한다(2026-09-10 사용자 지시). 중단 시 완료한 하위 항목과 정확한 재개 지점을 기록한다.
- 새 세션은 이 문서의 현황, 해당 작업, 직접 선행 작업의 결과 파일과 관련 코드만 먼저 읽는다. 2MB 이상인 노트북은 필요한 셀을 찾아 읽고 전체 출력·전체 논문 재검색을 반복하지 않는다.
- 이번 계획 작성은 모델 구현이나 전체 백테스트 실행 완료를 뜻하지 않는다.
- API 과금 토큰 없는 코드 실행을 유지한다. 외부 유료 LLM API를 추가하지 않는다.
- 단일 노트북의 Colab 실행을 유지한다. 공통 헬퍼 변경 시 tools/sync_notebook_helpers.py로 동기화한다.
- 실험은 PREDICT_STOCK_PUBLISH=false로 실행한다. docs/ 발행 결과와 기존 forecast_history/ 공식 예측을 실험으로 덮어쓰지 않는다.
- 문서는 guides/에 둔다. docs/는 이 저장소에서 GitHub Pages 생성 결과 디렉터리다.
- 방향은 기존 하락·보합·상승 3클래스와 과거 변동성 기반 보합 밴드를 유지한다. 이진 분류 정확도와 혼합 비교하지 않는다.
- 종목별로 동일 날짜·동일 타깃·동일 정보 마감 시각에서 비교한다. 현재 실행 마감 시각은 노트북과 자동 실행 설정에서 확인해 고정한다. 오전 7시로 임의 변경하지 않는다.
- 정규화·모델 선택·확률 보정·특징 선택·앙상블 가중치 선택은 모두 외부 평가보다 과거인 자료에서만 한다.
- 5·20일 중첩 타깃은 기존 purge 규칙을 유지한다. 공동 학습 시 날짜 단위로 모든 종목을 함께 분리한다.
- 과거 시점에 실제 공개된 값과 최신 수정치를 지연 정렬한 값을 구분한다. 후자는 엄밀한 과거 당시 데이터라고 주장하지 않는다.
- 정확도 목표 수치를 미리 보장하지 않는다. 비용·시간·표본 부족으로 중단한 실험도 그대로 기록한다.
- 새 모델은 기본 비활성화한다. 후보별 실험 횟수와 실패 결과를 보존하고 최종 평가를 보고 재조정하지 않는다.
- 검토 기준 이후 코드가 바뀌었다면 시작 시 관련 변경만 확인하고 파일 위치·선행조건을 이 문서에 갱신한다.

## 2. 최신 저장소에서 이미 확인한 기능

다음은 새로 만들 항목이 아니라 재사용·재현할 기반이다. “소스에서 확인”과 “이번 실행에서 성능 검증”을 구분한다.

| 기존 기능 | 확인 위치 | 계획에 반영 |
| --- | --- | --- |
| Logistic·LightGBM, 내부 시간순 설정 선택 | forecast_utils.py의 fit_direction_model | P00 기준선으로 고정 |
| 기본 5년 창 선택 함수 | rolling_train_indices(..., years=5) | P04는 최초 도입이 아니라 기간 비교 |
| 온도 확률 보정 | temperature_probabilities, predict_direction_model | P08은 추가 효과·신뢰도 평가 |
| SOX·Micron·Nvidia·TSMC·환율 등 해외 입력 | guides/data-sources.md | P03에서 기존 변수 기여도를 먼저 검증 |
| 월별 지표·뉴스심리·수급 제외 모델 | guides/validation.md | 중복 구현 없이 동일 날짜 비교 |
| 갭·세션 평가와 거래비용 | guides/validation.md | P09는 별도 학습 효과 확인 |
| 월 블록 부트스트랩·누수 검사 | guides/validation.md, tests/ | 공통 평가에 재사용 |
| 최초 사전 예측 보존·채점 | append_forecasts, evaluate_forecasts, review_ledger | 공식 기록 보존 |
| Transformer 비교 실험 | experiments/transformer/summary.csv | 무조건 편입하지 않음 |

기존 Transformer 기록(2026-09-06, 저장소 파일에서 확인, 이번에 재실행한 결과 아님):

| 종목 | Transformer 추가 후 log loss 차이 | 95% CI | 저장된 판정 |
| --- | ---: | --- | --- |
| 삼성전자 | +0.009129 | [0.004410, 0.014125] | 편입 반대 |
| SK하이닉스 | +0.001705 | [-0.002672, 0.006348] | 동률 |

차이는 후보−기준이며 log loss는 작을수록 좋다. 기존 실험은 MASTER·DoubleAdapt·Chronos-2의 검증 결과가 아니다.

## 3. 전체 체크리스트

상위 체크는 “작업 구현/실험 및 검증 기록이 완료됨”을 뜻한다. 실제 채택 여부는 별도 열로 기록한다. 효과 없는 실험도 검증과 기록까지 끝나면 완료 처리할 수 있다.

| ID | 완료 | 작업 | 선행 | 실행 부담 | 채택/결론 |
| --- | --- | --- | --- | --- | --- |
| P00 | [ ] | 현행 기준선·평가 계약 고정 | 없음 | CPU, 전체 평가는 Colab | 진행 중: 로컬 러너 수정, 고정 데이터 평가 |
| P01 | [x] | 한 작업씩 실행·재개하는 실험 러너 | P00 | CPU | 완료: `tools/run_model_improvement.py`, 15개 테스트 통과 |
| P02 | [x] | 정보 공개 시각·데이터 품질 점검 | P01 | CPU | 완료: 미래 날짜 봉 결함 1건 수정, `tests/test_release_timing.py` 15개 |
| P03 | [ ] | 기존 특징군의 추가 가치 비교 | P02 | CPU/Colab | 미실행 |
| P04 | [ ] | 학습 기간 비교 | P03 | CPU/Colab | 미실행 |
| P05 | [ ] | 최근 표본 가중 학습 | P04 | CPU/Colab | 미실행 |
| P06 | [ ] | 재학습 주기 비교 | P05 | CPU/Colab | 미실행 |
| P07 | [ ] | 소수 모델 앙상블 비교 | P06 | CPU/Colab | 미실행 |
| P08 | [ ] | 확률 신뢰도·예측 보류 평가 | P07 | CPU | 미실행 |
| P09 | [ ] | 갭·장중 별도 학습 비교 | P08 | CPU/Colab | 미실행 |
| P10 | [ ] | 국내 관련 종목 공동 학습 기반 | P09 | CPU/Colab, 선택 | 미실행 |
| P11 | [ ] | MASTER 비교 실험 | P10 | GPU, 선택 | 미실행 |
| P12 | [ ] | DoubleAdapt 비교 실험 | P06·P10 | GPU, 선택 | 미실행 |
| P13 | [ ] | TRA 방식 모델 선택 실험 | P07·P10 | GPU, 선택 | 미실행 |
| P14 | [ ] | Chronos-2 비교 실험 | P09 | GPU, 선택 | 미실행 |
| P15 | [ ] | 후보 사전 예측·공식 반영·복구 | P09, 선택 실험은 완료된 것만 | Colab/Actions | 미실행 |

P10~P14는 전부 수행할 의무가 없다. P09까지 완료한 뒤 가장 유망한 후보 하나만 선택할 수 있다.
선택하지 않은 작업은 미체크 상태로 결론을 “보류(이유)”로 바꾼다. P15는 고급 모델 실험을 모두 기다릴 필요가 없다.

GitHub 편집기로 체크할 때 위 표의 [ ]를 [x]로 바꾸고, 해당 상세 작업과 실행 이력도 함께 갱신한다.

## 4. 공통 완료 조건과 결과 형식

각 작업은 다음 네 가지가 있어야 완료다.

1. 관련 동작 검증 통과 또는 실험 중단의 명확한 근거.
2. 실험 설정·코드 커밋·데이터 해시·실행 환경·실험 횟수 보존.
3. 결과 경로와 채택/미채택/동률/보류 결론 기록.
4. 이 문서의 체크 상태·다음 작업·재개 메모 갱신 후 Git 커밋.

코드만 끝나고 Colab 검증을 하지 못했다면 상위 완료는 체크하지 않는다. 하위 구현 항목만 체크하고 “Colab 검증 대기”라고 기록한다. QUICK_MODE 결과는 동작 확인이며 성능 채택 근거로 사용하지 않는다.

### 결과 저장 계약

기존 산출물은 그대로 사용한다. P01에서 다음 실험용 경로를 추가한다.

- experiments/model_improvement/<task_id>/<run_id>/manifest.json
- experiments/model_improvement/<task_id>/<run_id>/metrics.csv
- experiments/model_improvement/<task_id>/<run_id>/decision.md
- experiments/model_improvement/<task_id>/<run_id>/checkpoint.json
- 원시 예측·큰 모델·원본 데이터는 실행 저장소에 보관하고 manifest에 경로와 해시를 기록한다. 대용량 파일을 Git에 넣지 않는다.

manifest 필수 키:

~~~json
{
  "task_id": "P00",
  "run_id": "20260909T000000Z_samsung_baseline",
  "target": "samsung",
  "code_commit": "실행 시 Git에서 읽은 커밋 SHA",
  "data_hash": "실행 시 스냅샷에서 계산한 해시",
  "config_hash": "정렬된 실험 설정의 해시",
  "seed": 42,
  "mode": "full",
  "target_mode": "close_to_close",
  "classes": ["down", "flat", "up"],
  "status": "planned",
  "completed_units": [],
  "failed_units": [],
  "artifact_paths": {}
}
~~~

위 값은 형식 예시다. 실행 결과 파일에는 실제 값만 저장하며 status는 running/completed/failed로 갱신한다.
P00에서 실제 평가 기간, 정보 마감 시각, 폴드 경계, 밴드 설정, 라이브러리 버전도 manifest에 추가한다.

metrics.csv는 target, model, target_mode, fold, n, accuracy, balanced_accuracy, log_loss, brier, auc_gap, auc_session을 기본으로 한다.
기존 출력과 필드가 다르면 P01 어댑터에서 명시적으로 변환한다. 계산할 수 없는 값은 빈 값과 사유를 남기고 0으로 채우지 않는다.
쌍체 비교는 comparison, metric, delta, ci_low, ci_high, common_n으로 별도 행/파일에 기록한다.

### 채택 판정

- 방향 정확도 개선: 기존 3클래스 balanced_accuracy의 후보−기준 95% 월 블록 CI 하한이 0 초과일 때만 주장.
- 확률 품질 개선: log_loss의 후보−기준 CI 상한이 0 미만일 때만 주장. 확률 개선을 방향 정확도 개선으로 표현하지 않는다.
- 두 지표가 상충하면 자동 채택하지 않고 “상충”으로 기록한다. 운영 대표 모델은 기존 log_loss 선택 원칙을 유지한다.
- 신뢰구간이 0을 포함하면 동률, 표본·클래스 부족이면 판단 보류.
- 특정 신호 날짜만 고른 성능은 전체 날짜 성능 및 coverage와 함께 기록.
- 세션 수익 주장은 auc_session과 기존 COST_BP 차감 후 성과를 별도로 통과해야 한다.
- 반복 비교의 낙관 편향을 줄이기 위해 후보 수를 제한·기록하고, 최종 후보는 조정하지 않은 미래 사전 예측에서 다시 확인한다.
- 삼성전자와 하이닉스는 각각 판단한다. 한 종목 개선으로 두 종목에 일괄 적용하지 않는다.

## 5. 작업별 실행 내용

### P00 — 현행 기준선과 평가 계약 고정

**파일:** 읽기 guides/validation.md, guides/running.md, forecast_utils.py, 노트북 설정·학습·저장 셀, experiments/transformer/summary.csv. 생성 experiments/model_improvement/P00/<run_id>/의 결과 파일.
**입출력:** 현행 모델과 고정 데이터 스냅샷 → 종목별 기준선 manifest, metrics, decision.

- [x] 현행 HEAD, 대표 모델 HEADLINE_MODEL, 실제 사용 특징, 3클래스 밴드, 학습 창·폴드·발행 시각·비용을 기록한다. → 아래 “기준선 계약” 표
- [x] 기존 산출물에서 재사용 가능한 기준선을 찾고, 새 실행이 필요한 범위만 정한다. 항상 보합·학습 구간 클래스 사전확률·Previous ensemble·현행 대표 모델을 같은 날짜에서 비교한다. → 기존 캐시 재사용 불가(자산 키 불일치)로 스냅샷 신규 고정. 비교 모델 6종이 같은 날짜에서 출력됨
- [x] 같은 고정 스냅샷으로 quick 동작 확인 후 full 평가를 실행한다. → quick 완료(`experiments/model_improvement/P00/20260910T0000Z_baseline_quick/`), full 실행 중
- [ ] 노트북 가이드에 기재된 과거 결과와 달라진 이유를 데이터·버전·설정 차이로 구분한다. 재현 불가면 실패 이유를 남기고 P01에 넘길 기준을 명시한다.
- [ ] 기준선 파일 경로·실제 커밋·다음 작업 P01을 실행 이력에 기록하고 완료 체크한다.

실행 예시(기존 명령, 저장 경로는 종목별 실제 산출 위치를 manifest에 기록):

~~~bash
PREDICT_STOCK_PUBLISH=false PREDICT_STOCK_TARGETS=samsung,sk_hynix python tools/run_notebook.py --storage ./outputs/model_improvement/P00 --quick --use-cache
PREDICT_STOCK_PUBLISH=false PREDICT_STOCK_TARGETS=samsung,sk_hynix python tools/run_notebook.py --storage ./outputs/model_improvement/P00 --use-cache
~~~

캐시가 없다면 먼저 발행 비활성 상태에서 한 번 수집해 스냅샷을 고정한다. 데이터 다운로드 실패를 다른 날짜 자료로 숨기지 않는다.
#### 기준선 계약 (2026-09-10 노트북에서 읽은 실제 값)

| 항목 | 값 | 위치 |
| --- | --- | --- |
| HEADLINE_MODEL | `No macro ensemble` (시세만; `macro_`/`nsi_`/`flow_` 제외) | C9:97 |
| ENSEMBLE_MODELS | `["Logistic", "LightGBM"]` 단순 평균 | C9:58 |
| SELECTION_METRIC | `log_loss` | guides/validation.md |
| START_DATE | 2015-01-01 | C9:1 |
| TARGET_MODE | `close_to_close` | C9:35 |
| 밴드 | `vol_scaled`, VOL_BAND_MULT 0.3 (과거 변동성만 사용) | C9:41-42 |
| 외부 평가 시작 | FIRST_TEST_DATE 2021-01-01 | C9:47 |
| 폴드 | TEST_MONTHS 6, 전체 12폴드 (quick 3) | C9:48 |
| 학습 창 | ROLLING_TRAIN_YEARS 5 | C9:49 |
| 거래비용 | COST_BP 20.0 | C9:73 |
| 부트스트랩 | BOOTSTRAP_B 2000 (quick 400), 월 블록 | C9:74 |
| SEED | 42 | C4:51 |
| 비교 기준선 | Always flat / Previous ensemble / No macro / No NSI / No flow / No macro price | 실행 출력 |
| 발행 시각 | Actions 06:22 KST(백업 07:25), 원장 마감 09:00 — P02에서 엄밀 검증 | 워크플로 |

스냅샷: samsung `1b535c11f8be69aaea37`, sk_hynix `c12bc46b6e199a08b37a`, 통합 `93656a4827bf38daa78a`.
학습 행 samsung 2,753 / sk_hynix 2,752, 백테스트 범위 2015-04-01~2026-09-09, 예측일 2026-09-10.

**완료 증거:** 두 종목의 동일 조건 기준선과 실제 평가 날짜 수. quick 완료, full 실행 중.

### P01 — 최소 실험 러너와 재개 기능

**파일:** 생성 tools/run_model_improvement.py, tests/test_model_improvement_runner.py. 필요 시 tools/run_notebook.py의 실행 인터페이스 재사용. 결과는 4절 계약 준수.
**인터페이스:** CLI --task, --target, --mode quick|full, --storage, --resume. 첫 구현은 P00 기준선 작업만 지원하고, 각 후속 작업에서 해당 task를 등록한다. 미지원 ID는 명확한 오류로 종료한다.

- [x] 동일 task·target·데이터·설정 해시의 완료 실행을 재개하면 재학습하지 않는 테스트를 작성한다. → `test_resume_with_same_inputs_does_not_retrain`
- [x] 데이터/설정 해시 변경 시 이전 체크포인트를 재사용하지 않는 테스트와 미지원 ID 거부 테스트를 작성한다. → `test_changed_data_starts_a_new_run`, `test_changed_config_starts_a_new_run`, `test_unregistered_task_is_rejected`, `test_unsupported_target_is_rejected`
- [x] 실행 단위를 (target, candidate, fold, seed)로 저장한다. 처음에는 종목 단위 재개를 허용하고 폴드 지원 여부를 manifest에 명시한다. → 현재 단위는 `<target>:baseline` 하나. 폴드 단위 재개는 미지원이며 manifest의 `completed_units`로 확인한다
- [x] 중단 시 완료 단위만 저장하고, 원자적 쓰기로 손상된 완료 상태를 만들지 않는다. 공식 발행은 항상 비활성화한다. → `test_failure_is_recorded_and_unit_stays_incomplete`, `test_corrupt_checkpoint_is_treated_as_missing`, `test_atomic_write_leaves_no_partial_file`, `test_publishing_is_always_disabled`
- [x] 같은 실행을 두 번 재개해 완료 단위가 중복 실행되지 않는지 확인하고 사용 예시를 guides/running.md에 추가한다. → `test_resuming_twice_does_not_duplicate_units`, guides/running.md “모델 개선 실험 러너”

구현 후 사용할 고정 CLI:

~~~bash
python tools/run_model_improvement.py --task P00 --target samsung --mode quick --storage ./outputs/model_improvement
python tools/run_model_improvement.py --task P00 --target samsung --mode full --storage ./outputs/model_improvement --resume
python -m unittest discover -s tests -p 'test_model_improvement_runner.py' -v
~~~

**완료 증거:** 재개 전후 완료 단위 동일, 설정 변경 시 별도 run, 발행 파일 무변경.

### P02 — 공개 시각·데이터 품질 점검

**파일:** 수정이 필요한 경우에만 data_sources/ 해당 모듈과 노트북 정렬 셀. 확장 tests/test_pipeline_behavior.py, tests/test_macro_release.py. 생성 결과 P02.
**입출력:** P00 스냅샷·시각 계약 → 특징별 source/as_of/available_at/수정치 여부/결측 비율 표.

- [x] 기존 국내 shift, 미국 마감 시각·서머타임·양국 휴일, 보조 자산 결측 처리 테스트를 읽고 빠진 경계 사례만 추가한다. → 기존 20+12개를 읽고 겹치지 않는 경계만 `tests/test_release_timing.py`에 넣었다(마감 정각/1분 전, 서머타임 UTC 동일 시각 여름·겨울, 미래 날짜 봉, tz 없는 시각, 허용치 초과 노후값)
- [x] 예측 마감 뒤 국내 가격·미국 미완성 봉을 변경해도 해당 시각 특징이 같다는 테스트를 실행한다. → 기존 `test_todays_data_cannot_change_todays_features_or_band`, `test_global_features_come_from_an_earlier_bar` 통과 확인
- [x] released_at 없는 월별 최신 수정치를 “지연 가정 자료”로 표시한다. → `macro_history_mode="lagged_latest_vintage"`가 원장·config에 남는 것을 `LaggedVintageLabelTests`로 고정
- [x] 노후 자료 제외·결측 여부 때문에 모델별 평가 날짜가 달라지는지 확인하고 공통 평가 집합을 저장한다. → 두 종목 모두 9개 모델이 동일한 276일(2025-07-01~2026-09-09). `experiments/model_improvement/P00/<run_id>/common_evaluation.json`
- [x] 발견한 결함만 수정하고, 수정으로 결과가 바뀌면 P00 기준선 버전을 새로 만든다. → 아래 결함 1건 수정. 백테스트 지표 영향 여부는 같은 스냅샷 quick 재실행으로 대조한다

#### 발견·수정한 결함 (2026-09-10)

`drop_unclosed_last_bar`가 **그 시장 기준 미래 날짜인 봉을 남겼다.**

옛 판정은 `last_date >= now.date() and 지금 < 마감시각` 이었다. 한국 07:00 예측은 뉴욕 기준
전날 18:00이고 이는 미국 마감(16:05)·연속시장 마감(17:05) 이후라 시간 조건이 거짓이 되어,
아직 열리지도 않은 날짜의 봉이 "마감된 봉"으로 통과했다.

실제 자료에서 확인했다. 2026-09-10 고정 스냅샷에서 `usdjpy`, `usdkrw`가 뉴욕 날짜
2026-09-09 시점에 2026-09-10 봉을 갖고 있었다(FX는 17:00 ET에 날짜가 넘어간다).

수정: 미래 날짜 봉을 먼저 모두 버리고, 남은 마지막 봉이 오늘 날짜일 때만 마감 시각과 비교한다.
검증을 위해 `now`를 주입할 수 있게 했다(그전에는 `pd.Timestamp.now()`를 직접 불러 시각
경계를 고정할 수 없었다).

**영향 범위:** 과거 폴드의 봉은 미래 날짜가 될 수 없으므로 백테스트 지표는 바뀌지 않을 것으로
보지만, 라이브 예측 행은 바뀔 수 있다. 같은 스냅샷 quick 재실행으로 대조한 뒤 기록한다.

**완료 증거:** 관련 누수·공개 시각 테스트 통과, 날짜별 입력 가용성 표, 기준선 영향 기록.

### P03 — 기존 특징군 추가 가치 비교

**파일:** tools/run_model_improvement.py에 P03 등록, 노트북 기존 ablation 설정·결과 출력, 필요 시 tests/test_macro_features.py·tests/test_price_macro.py 확장.
**입출력:** P02에서 허용된 특징·공통 날짜 → 기존 특징군 포함/제외 비교.

- [ ] 현재 No macro/No flow/No sentiment/No price macro 등 실제 모델 이름과 정확한 특징 목록을 출력한다.
- [ ] 먼저 현행 대표 모델과 기존 ablation들을 비교한다. 이미 있는 해외 자산·수급을 새로 수집하는 작업으로 바꾸지 않는다.
- [ ] 각 비교에서 동일한 평가 날짜, 학습 창, 튜닝 예산, 밴드를 사용한다.
- [ ] log_loss·balanced_accuracy의 쌍체 차이와 CI, 결측으로 제외된 날짜 수를 종목별 저장한다.
- [ ] 새 변수군 추가는 기존 입력에 명확한 공백이 있을 때 후속 작업으로 기록한다. 효과가 불명확하면 기존 대표 특징군을 유지한다.

**완료 증거:** 특징군별 동일 날짜 비교표와 P04에서 사용할 고정 특징 목록.

### P04 — 학습 기간 비교

**파일:** forecast_utils.py의 rolling_train_indices, 노트북 외부 폴드·라이브 학습 창 설정, tests/test_forecast_improvements.py, 러너 P04 등록.
**입출력:** P03 특징 목록 → 최근 2·3·5년 및 가용 전체 과거 expanding 후보.

- [ ] 모든 후보의 시작·종료 경계 테스트를 추가한다. 예측일 당일은 학습에 포함하지 않는다.
- [ ] 5년 기본 동작을 보존하면서 기간 설정을 외부 폴드와 라이브 학습 모두에 전달한다.
- [ ] 기간 선택도 외부 평가 이전의 내부 검증에서 수행한다. 외부 폴드 점수를 보고 해당 폴드의 창을 다시 고르지 않는다.
- [ ] 짧은 창에서 클래스·표본이 부족하면 기존 fallback 또는 후보 제외 사유를 기록한다.
- [ ] 종목별 창의 성능·학습 시간·표본 수를 비교하고 P05의 고정 비교 기준을 남긴다.

**완료 증거:** 경계 테스트와 전체 평가. 함수 존재만으로 신규 작업을 완료 처리하지 않는다.

### P05 — 최근 데이터 가중 학습

**파일:** forecast_utils.py의 fit_direction_model, tests/test_forecast_improvements.py, 노트북 설정 및 헬퍼 동기화, 러너 P05 등록.
**인터페이스 제안:** 기존 위치 인자는 보존하고 선택적 키워드 sample_weight=None을 추가한다. 배열은 X와 같은 길이이며 train_indices·내부 tr로 각각 자른다.

- [ ] None과 모든 값 1인 가중치의 결과가 허용 오차 내 같고, 음수·비유한·길이 불일치 가중치는 거부하는 테스트를 추가한다.
- [ ] 가중치 공식 w=2^(-age_trading_days/half_life)를 구현한다. 각 학습 폴드 마지막 관측을 기준으로 age를 계산하고 평균 1로 정규화한다.
- [ ] Logistic 파이프라인과 LightGBM의 fit에 학습 행 가중치를 전달한다. 기존 class_weight와 곱해지는 효과를 설정에 기록한다.
- [ ] 반감기 후보는 무가중·126·252·504 거래일로 제한한다. 내부 검증에서만 선택하고 평가 지표는 원래 날짜별 동일 가중으로 계산한다.
- [ ] 외부 평가 라벨을 바꿔도 선택 설정·학습 가중치가 달라지지 않는지 검증한다.

회귀 검증 핵심 예시(새 인터페이스 구현 후 SelectionTests에 추가):

~~~python
a = fu.fit_direction_model(X, y, train_indices, "Logistic")
b = fu.fit_direction_model(
    X, y, train_indices, "Logistic", sample_weight=np.ones(len(y))
)
np.testing.assert_allclose(
    fu.predict_direction_model(a, X[test_indices]),
    fu.predict_direction_model(b, X[test_indices]),
    rtol=1e-6, atol=1e-7,
)
~~~

**완료 증거:** 가중치·누수 테스트, 무가중 대비 성능과 시간. 단순 가중 학습을 DoubleAdapt 재현이라고 부르지 않는다.

### P06 — 재학습 주기 비교

**파일:** 러너 P06 등록, 노트북/러너의 학습 호출 경계, 생성 tests/test_retraining_schedule.py.
**입출력:** P05 후보 → 1·5·21 거래일 재학습 주기 비교.

- [ ] 달력일 대신 실제 입력 거래일 인덱스로 재학습 시점을 정하는 테스트를 작성한다.
- [ ] 재학습하지 않는 날에는 기존 모델·스케일러·온도 값을 고정하고 새로 가용한 특징으로만 예측한다.
- [ ] 모델 버전·trained_until·실제 학습 횟수를 날짜별 기록한다.
- [ ] 같은 날짜를 평가하면서 주기별 성능과 총 실행 시간을 비교한다. 주기는 내부 검증에서 선택한다.
- [ ] 하루 오답을 이유로 즉시 자동 튜닝하지 않는 동작과 재개 후 주기 유지 여부를 검증한다.

**완료 증거:** 휴일·재개 일정 테스트, 주기별 성능/시간 표.

### P07 — 소수 모델 앙상블

**파일:** 러너 P07 등록, 필요한 순수 함수는 forecast_utils.py, 생성 tests/test_model_ensemble.py.
**입출력:** 동일 날짜의 후보별 p_down/p_flat/p_up → 3클래스 결합 확률.

- [ ] 현재 대표 모델·최근 창 모델·최근 가중 모델 중 최대 3개를 고정한다. 같은 모델의 이름만 바꾼 중복 후보는 제외한다.
- [ ] 단순 평균을 먼저 평가하고, 두 모델 결합 가중치는 0·0.25·0.5·0.75·1 후보만 내부 검증에서 선택한다.
- [ ] 확률 클래스 순서·합 1·유한값 및 후보 누락 시 fallback을 테스트한다.
- [ ] 외부 라벨을 바꿔도 해당 외부 구간의 앙상블 가중치가 변하지 않는지 확인한다.
- [ ] 최선 단일 후보와 현행 대표 모델 양쪽에 대해 추가 가치를 기록한다.

**완료 증거:** 확률·시간 분리 테스트, 앙상블 채택/동률/미채택 판정.

### P08 — 확률 보정과 예측 보류 평가

**파일:** 기존 temperature_probabilities 재사용, 러너 P08 등록, 필요 시 report_html.py, 생성 tests/test_probability_diagnostics.py.
**입출력:** 과거 보정 구간과 외부 확률 → 신뢰도 구간 표·전체 및 선택 예측 성능.

- [ ] 기존 온도 보정 전후 log_loss·Brier·클래스별 신뢰도 구간을 비교한다. 같은 기능을 새로 구현하지 않는다.
- [ ] 온도 보정이 argmax를 유지하는지 테스트한다. 보정으로 정확도가 향상됐다고 잘못 기록하지 않는다.
- [ ] 보류 임계치는 없음·최대 확률 0.50·0.60·0.70만 비교한다. 필요 선택은 내부 검증에서 하고 외부 평가로 재선택하지 않는다.
- [ ] 전체 날짜 정확도, 선택 날짜 정확도, coverage, 선택 건수를 같이 출력한다. 신호 0건의 정확도는 미정 값으로 처리한다.
- [ ] 기본 보고서 반영은 P15까지 보류하고 진단 결과만 저장한다.

**완료 증거:** 확률 보정 및 빈 선택 집합 테스트, coverage 포함 비교표.

### P09 — 갭과 장중의 별도 학습

**파일:** 노트북 TARGET_MODE·가격/시가 모델·평가 셀, forecast_utils.py 채점 재사용, tests/test_forecast_improvements.py, 러너 P09 등록.
**입출력:** P02 정보 시점과 같은 데이터 → 종가→종가, 갭, 시가→종가 별도 학습 결과.

- [ ] 현재 갭·세션 “평가”와 각각을 타깃으로 하는 “학습” 경로를 구분해 적는다. 존재하는 학습 경로는 재사용한다.
- [ ] 오전 예측의 장중 타깃은 실제 시가를 정답 계산에만 쓰고 특징에는 넣지 않는다.
- [ ] 갭·세션을 독립 확률로 임의 합쳐 전체 상승확률을 만들지 않는다. 처음에는 세 타깃을 나란히 비교한다.
- [ ] 가격 기준의 기업행사 조정 일관성을 확인한다. 종가→종가 수익률은 (1+gap)*(1+session)-1과 일치하는 fixture로 검증한다.
- [ ] 장중 방향과 COST_BP 차감 성과를 따로 기록한다. 기존 갭 성능을 장중 매매 성능으로 표현하지 않는다.

**완료 증거:** 타깃 분리·실제 시가 누수 테스트, 구간별 성능. 다음은 P15 또는 선택 실험 하나.

### P10 — 공동 학습용 국내 종목 데이터

**파일:** 생성 experiments/model_improvement/panel_data.py, tests/test_panel_data.py, 러너 P10 등록.
**입출력:** 기준 시점에 정의한 국내 반도체 관련 종목 목록·시점 정렬 시세 → date/instrument 기준 패널.

- [ ] 종목 선정 규칙과 선정 기준일을 먼저 파일에 고정한다. 현재 생존 종목만 과거로 적용했다면 생존편향을 명시한다.
- [ ] 한국 종목은 공동 학습 패널로, 미국 종목은 기존 한국 마감 시각에 정렬한 외부 변수로 사용한다.
- [ ] 동일 날짜 모든 종목이 같은 폴드에 속하는지, 상장 전·거래정지·결측 자료를 임의 보간하지 않는지 테스트한다.
- [ ] 수익률·거래대금 비율 등 비교 가능한 입력과 종목 식별값으로 단순 pooled LightGBM을 먼저 평가한다.
- [ ] 삼성전자·하이닉스 각각의 단독 학습 대비 추가 가치·메모리·시간을 기록한다.

**완료 증거:** 패널 누수/결측 테스트와 pooled 기준선. 종목 수 증가를 독립 날짜 표본 증가로 주장하지 않는다.

### P11 — MASTER 선택 실험

**파일:** 생성 experiments/model_improvement/master_adapter.py, tests/test_master_adapter.py. 선택 의존성은 experiments/model_improvement/requirements-master.txt. 러너 P11 등록.
**입출력:** P10 패널 → 동일 평가 날짜의 종목별 예측.

- [ ] 논문·공식 저장소 버전과 라이선스를 고정하고 공개 검증 데이터/전처리 수정 공지를 검토 기록에 포함한다.
- [ ] 먼저 작은 합성 패널로 입력 축, 종목 순서, 결측 종목, 미래 데이터 차단을 테스트한다.
- [ ] 원 논문의 순위/수익률 학습과 본 프로젝트의 3클래스 출력 차이를 명시한다. 분류 헤드를 바꾸면 “MASTER 기반 변형”으로 기록한다.
- [ ] 기존 P10 pooled 기준선 및 현행 운영 모델과 동일 폴드에서 비교한다. seed는 42·43·44, 초기 설정은 1개로 제한한다.
- [ ] GPU 최대 메모리·시간·종목별 성능을 기록하고 채택/동률/중단 결론을 남긴다.

**완료 증거:** 데이터 어댑터 테스트·재현 가능한 모델 버전·삼성전자/하이닉스 자체 평가. 중국시장 IC를 한국 방향 정확도로 환산하지 않는다.

### P12 — DoubleAdapt 선택 실험

**파일:** 생성 experiments/model_improvement/doubleadapt_adapter.py, tests/test_doubleadapt_adapter.py, 선택 requirements-doubleadapt.txt, 러너 P12 등록.
**입출력:** P10 데이터·P06 순차 일정 → 시점별 적응 모델 예측.

- [ ] 공식 구현 버전과 메타학습/적응/외부 평가 구간을 고정한다.
- [ ] 미확정 타깃은 업데이트에 사용하지 않는다. 예측→정답 공개→갱신 순서를 합성 시퀀스로 테스트한다.
- [ ] 동일 기반 신경망의 무적응·일반 순차 갱신·DoubleAdapt를 비교해 모델 변경과 적응 효과를 분리한다.
- [ ] 내부 구간에서만 적응 설정을 정하고 같은 seed 42·43·44로 외부 평가한다.
- [ ] P05의 단순 가중 학습과 비용 대비 추가 가치를 비교하고, 개선이 없으면 공식 모델에 넣지 않는다.

**완료 증거:** 업데이트 시각 테스트·기반 모델 통제 비교·메타학습 비용 기록.

### P13 — TRA 선택 실험

**파일:** 생성 experiments/model_improvement/tra_adapter.py, tests/test_tra_adapter.py, 선택 requirements-tra.txt, 러너 P13 등록.
**입출력:** P10 패널과 과거 전문가 오차 → 예측 당시 선택/결합된 전문가 출력.

- [ ] 공식 TRA 구현 버전을 고정하고 전문가 수를 우선 2개로 제한한다.
- [ ] 라우터가 볼 수 있는 오차 이력은 정답이 공개된 과거 예측으로 제한한다.
- [ ] 동일 기반 전문가의 단순 평균과 학습 라우터를 비교한다.
- [ ] 한 전문가로 쏠리는 비율·기간별 선택 비중·라우터 입력 누수를 테스트/기록한다.
- [ ] 원 논문 OT 학습을 생략한 단순 국면 가중 방식이라면 TRA 재현으로 부르지 않고 별도 변형으로 기록한다.

**완료 증거:** 과거 오차만 사용하는 테스트·단순 평균 대비 성능·전문가 사용량.

### P14 — Chronos-2 선택 실험

**파일:** 생성 experiments/model_improvement/chronos2_adapter.py, tests/test_chronos2_adapter.py, 선택 requirements-chronos2.txt, 러너 P14 등록.
**입출력:** 과거 타깃·가용 공변량 → 다음 거래일 수익률/가격 분포 또는 별도 명시한 출력.

- [ ] 공식 체크포인트 ID·revision·라이선스·공개된 사전학습 데이터 범위를 기록한다. 평가 기간과 겹침을 배제하지 못하면 “사전학습 오염 불확실”로 표시한다.
- [ ] zero-shot 설정 1개부터 시작한다. 미래에 알 수 없는 환율·종가·수급을 future covariate로 넣지 않는다.
- [ ] 모델이 분위수만 반환한다면 평균/중앙값을 상승확률로 간주하지 않는다. 3클래스 확률 변환을 쓰려면 과거 별도 보정과 방법을 명시한다.
- [ ] 우선 가격/수익률 오차와 구간 품질을 기준선과 비교한다. 방향 확률 지표는 유효한 확률 출력이 마련된 경우에만 비교한다.
- [ ] 기존 3클래스 모델과 타깃·평가 날짜를 맞추고 메모리·시간·표본·오염 한계를 기록한다.

**완료 증거:** 공변량 시점 테스트·출력 의미 검증·모델 revision과 성능 기록.

### P15 — 사전 예측, 공식 반영, 복구

**파일:** 노트북 후보 설정·라이브 예측 셀, forecast_utils.py 원장 함수 재사용, report_html.py 필요 시 수정, guides/validation.md·guides/running.md. 테스트 tests/test_forecast_improvements.py·tests/test_report_html.py·tests/test_notebook_smoke.py.
**입출력:** 완료된 후보 실험 → 별도 후보 사전 예측, 종목별 채택 결정과 복구 설정.

- [ ] 가장 유망한 후보 하나와 기존 대표 모델을 고정하고 별도 model/config_hash로 사전 예측을 기록한다. 과거 예측은 소급 생성하지 않는다.
- [ ] 운영 대표는 유지한 채 최소 60개의 공통 채점 거래일을 관찰한다. 60일은 최초 점검 시점이며 유의한 개선을 보장하는 표본 수가 아니다.
- [ ] 4절 판정 규칙으로 종목별 방향 정확도와 확률 품질을 구분해 평가한다. CI가 불명확하면 관찰을 연장한다.
- [ ] 채택한 경우에만 기본 설정을 바꾸고 이전 HEADLINE_MODEL/학습 설정/모델 버전을 복구값으로 문서화한다.
- [ ] 최초 예측 보존, 재실행 중복 방지, 후보 결측 fallback, 노트북 단독 실행과 보고서 출력 검증 후 커밋한다.

**완료 증거:** 사전 예측 비교 결과와 채택/미채택 결정, 회귀검증, 복구 절차. 후보 구현 후 관찰 중에는 상위 체크를 남겨 둔다.

## 6. 검증 명령과 커밋 원칙

새 동작은 해당 작업의 의미 있는 회귀 테스트부터 작성한다. 데이터/학습 변경은 미래 라벨 변경 불변성, 날짜 경계, 확률 계약을 중심으로 검증한다.
단순 문서 수정에는 모델 전체 재학습을 요구하지 않는다.

헬퍼를 바꾼 작업의 기본 검증:

~~~bash
python tools/sync_notebook_helpers.py
python -m unittest discover -s tests -p 'test_forecast_improvements.py' -v
python -m unittest discover -s tests -p 'test_notebook_structure.py' -v
git diff --check
~~~

해당 작업의 신규 테스트는 위 명령에 추가한다. 노트북 실행 경로에 영향을 준 작업은 smoke 테스트도 실행한다.

~~~bash
python -m unittest discover -s tests -p 'test_notebook_smoke.py' -v
~~~

운영 반영 P15에서는 전체 회귀 검증을 실행한다.

~~~bash
python -m unittest discover -s tests -v
~~~

테스트 미실행·실패·데이터 접근 실패를 통과로 기록하지 않는다. 환경 문제와 모델 문제를 구분하고 재개 명령을 남긴다.
커밋 메시지는 작업 ID를 포함한다. 예: feat(model): P05 add recency-weighted training.
작업 완료 문서 변경과 해당 소스 변경을 함께 커밋한다. 완료 체크의 근거는 실행 결과 경로와 커밋 이력으로 추적한다.

## 7. 재개 요청 예시

~~~text
guides/model-improvement-plan.md를 읽고 P00만 진행해주세요.
최신 코드에서 관련 부분만 확인하고, 기존 기능은 재사용해주세요.
완료 조건까지 검증한 항목만 체크하고 결과 경로와 다음 작업을 기록해주세요.
토큰이 부족하면 하위 체크와 재개 명령을 저장하고 다음 작업으로 넘어가지 마세요.
~~~

P00 이후에는 작업 ID만 바꿔 요청한다. Colab 실행이 필요한 경우 구현 완료와 full 검증 대기를 구분해 기록한다.

## 8. 실행 이력과 현재 재개 지점

| 날짜 | ID | 실행/커밋 또는 결과 경로 | 검증 | 결론·다음 행동 |
| --- | --- | --- | --- | --- |
| 2026-09-09 | PLAN | 이 문서 및 README 링크 | 저장소 문서·핵심 함수·기존 Transformer 결과 확인 | 계획 생성. P00부터 시작 |
| 2026-09-10 | P00 사전 수정 | `tools/run_notebook.py`, `tests/test_run_notebook.py` | 8개 오프라인 회귀 테스트 통과 | 기존 CLI가 IPython 히스토리 부재로 둘째 종목을 건너뛰던 결함 수정. P00 전체 완료 아님 |
| 2026-09-10 | P02 | `tests/test_release_timing.py`, 노트북 `drop_unclosed_last_bar` | `discover -p test_release_timing.py` 15개 통과 | 완료. 미래 날짜 봉을 마감된 봉으로 취급하던 결함 수정(실제 스냅샷의 usdjpy·usdkrw에서 확인). 공통 평가 집합 276일 저장 |
| 2026-09-10 | P01 | `tools/run_model_improvement.py`, `tests/test_model_improvement_runner.py` | `discover -p test_model_improvement_runner.py` 15개 통과 | 완료. 재개·해시 격리·원자적 쓰기·발행 차단 검증. 구현 중 결함 2건(재개 시 반환형 불일치, 같은 초 run_id 충돌) 발견·수정 |
| 2026-09-10 | P00 quick | `experiments/model_improvement/P00/20260910T0000Z_baseline_quick/` | 두 종목 46셀 무오류 완료, 평가 276일 | 기준선 계약 기록·스냅샷 고정 완료. 기존 캐시는 자산 키 불일치로 폐기. full 평가 실행 중 |

- 현재 작업: P00 full 평가 실행 중(quick·계약 기록 완료). P01·P02 완료
- 완료한 신규 작업: 2/16 (P01, P02)
- 다음 작업: P00 full 결과 기록 → P03
- 보류 작업: 없음
- 마지막 검증 결과: `python -m unittest discover -s tests -p test_run_notebook.py -v` — 8개 통과. 전체 기존 테스트는 Windows 출력 인코딩 오류가 확인되어 `PYTHONIOENCODING=utf-8`로 재검증 중.
- 재개 시 먼저 읽을 파일: 이 문서의 P00, guides/validation.md, guides/running.md
- 향후 결과 기록: 각 작업 종료 시 위 표에 날짜, task/run_id, 결과 경로, 검증 명령·결과, 채택 여부, 다음 작업을 추가

## 9. 논문·공식 구현

논문의 성능 수치는 해당 데이터·타깃·평가 방법에 한정된다. 아래 연구들은 삼성전자·하이닉스에 대한 개선 보증이 아니다.

1. DoubleAdapt: A Meta-learning Approach to Incremental Learning for Stock Trend Forecasting, KDD 2023.
   - 논문: https://arxiv.org/abs/2306.09862
   - 공식 구현: https://github.com/SJTU-DMTai/DoubleAdapt
   - 연결 작업: P05·P06은 단순 적응 기준선, P12는 논문 기반 실험.
2. MASTER: Market-Guided Stock Transformer for Stock Price Forecasting, AAAI 2024.
   - 논문: https://ojs.aaai.org/index.php/AAAI/article/view/27767
   - 공식 구현: https://github.com/SJTU-DMTai/MASTER
   - 연결 작업: P10·P11. 공식 README의 데이터/검증 전처리 수정 공지 확인.
3. Learning Multiple Stock Trading Patterns with Temporal Routing Adaptor and Optimal Transport, KDD 2021.
   - 논문: https://arxiv.org/abs/2106.12950
   - 공식 구현: https://github.com/microsoft/qlib/tree/main/examples/benchmarks/TRA
   - 연결 작업: P07은 단순 결합 기준선, P13은 논문 기반 실험.
4. Chronos-2: From Univariate to Universal Forecasting, 2025 프리프린트.
   - 논문: https://arxiv.org/abs/2510.15821
   - 공식 구현: https://github.com/amazon-science/chronos-forecasting
   - 연결 작업: P14.
5. Are Transformers Effective for Time Series Forecasting?, AAAI 2023.
   - 논문: https://ojs.aaai.org/index.php/AAAI/article/view/26317
   - 활용: 복잡한 모델을 단순 기준선과 비교할 근거. 주식 단기 방향 성능 검증으로 해석하지 않는다.
6. On Calibration of Modern Neural Networks, ICML 2017.
   - 논문: https://proceedings.mlr.press/v70/guo17a.html
   - 연결 작업: P08. 확률 신뢰도와 방향 정확도는 구분.
7. The Probability of Backtest Overfitting, 2015.
   - 논문: https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
   - 활용: 후보 탐색 횟수 기록, 최종 평가 보호, 사전 예측 확인.

### P00 중간 인수인계 (2026-09-10)

- 기준 코드: `9d92667`(원격 최신 보고서·원장 포함). 계획 작성 기준 이후 모델 소스 변경 없음.
- 로컬 러너는 종목별 독립 namespace와 `storage/<target>`를 사용한다. 단일 종목은 지정한 storage를 그대로 사용한다. 실패 종목은 오류로 종료하고 환경변수를 복원한다.
- 실제 기존 시세 캐시: `D:/SourceCode/predict_stock/runs/real_validation/data_cache`, 가격 마지막 날짜 2026-09-04. 과거 full 삼성 결과는 54특징·12폴드·1,362일이며 현재 두 종목 기준선을 대체하지 않는다.
- `macro_integration`, `macro_release_validation`은 합성 거시 테스트 자료이므로 제외했다.
- 시각 차이: Actions 06:22 KST/백업07:25, 지표 정렬 설명07:00, 사전예측 원장 마감09:00. 과거 해외 자료는 날짜+1 정렬이므로 엄밀한06:22 정보 마감을 검증했다고 주장하지 않는다. P02에서 다룬다.
- 고정 입력 및 실행 중 원본 결과는 이 작업 worktree의 `runs/model_improvement/` 아래에 있다. 큰 시세 파일과 원시 예측은 Git에 올리지 않는다.
- P00 상위 체크는 두 종목 기준선 계약·실제 full 평가 및 결과 파일을 확인할 때까지 비워 둔다.
