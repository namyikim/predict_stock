# S04 실제 스냅샷 full 비교 실행 계획

**Goal:** 실제 OHLCV 스냅샷으로 네 후보(현재가 유지, 운영 Ridge, OHLCV Ridge, TCN seed 3개)를 공통 예측일에서 full 비교하고, 잠금 12개월을 **한 번만** 열어 설계 문서의 채택 기준으로 판정 근거를 남긴다.
**Architecture:** 코드는 `tools/run_weekly_sequence.py --task S04`로 S03까지의 단위를 재사용하고, 운영 Ridge는 `runs/medium_horizon/<target>/inputs_full_<data_hash>.pkl`(M01 고정 입력)을 읽어 붙인다. 실행은 **실제 스냅샷이 있는 곳**(Colab 또는 스냅샷을 복사한 로컬)에서 한다. 이 환경(Claude 작업 컨테이너)에는 스냅샷이 없어 코드·테스트까지만 하고 실행은 하지 않는다.
**Tech Stack:** Python, NumPy, pandas, scikit-learn, PyTorch(CPU 또는 Colab GPU). 발행·운영 코드 무변경.
**Spec:** [주간 모델 설계](weekly-sequence-model-plan.md), [S02](weekly-sequence-s02-implementation.md), [S03](weekly-sequence-s03-implementation.md)

## 승인·진행 상태

- [x] S03 완료(main ea4cd345).
- [x] S04 상세 실행 계획 작성(이 문서, 2026-09-22).
- [ ] 상세 실행 계획 검토 — **잠금 구간을 여는 단계라 아래 "열기 전 확정 사항"에 사용자 승인이 필요하다.**
- [ ] S04 코드 구현·검증(합성 데이터).
- [ ] 실제 스냅샷 반입 및 full 실행(스냅샷이 있는 환경에서).
- [ ] 결과 기록·판정 근거·main 반영 확인.

이 문서는 실행 계획이다. `--task S04`와 아래 테스트는 아직 존재하지 않는다.

## 열기 전 확정 사항 (승인 필요)

잠금 12개월은 **한 번** 열면 다시 닫을 수 없다. 열기 전에 다음을 고정하고, 결과를 본 뒤 바꾸지 않는다.

1. **후보와 설정**: OHLCV Ridge(alpha 1e4), TCN full 설정(블록 3, 필터 32, epoch ≤ 60, patience 8, seed 42·43·44), 운영 Ridge(M01 고정 입력 그대로). 개발 폴드에서 다른 alpha·구조를 시험해도 잠금에는 **이 설정만** 낸다.
2. **채택 기준**(설계 문서 그대로): 개발 폴드와 잠금 구간 **모두**에서 TCN(seed 3개 평균)의 MAE가 현재가 유지·운영 Ridge·OHLCV Ridge 각각보다 낮고, 월 블록 부트스트랩 95% CI 상한이 0 미만. seed 3개 중 하나라도 운영 Ridge보다 나쁘면 채택 후보로 올리지 않는다.
3. **공통 표본**: 네 후보가 모두 예측을 낸 예측일만. 운영 Ridge의 예측일은 특징 행렬 기준(종목 거래일)이고 OHLCV 시퀀스는 KRX 세션 기준이라 다를 수 있다 — 교집합 크기와 빠진 날짜 수를 기록한다.
4. **판정 표기**: "채택 후보" / "관찰 후보" / "미채택". 어느 경우든 **운영 채택은 별도 결정**(설계 문서 8절)이며 S04가 하지 않는다.
5. **운영 영향 없음**: 원장·보고서·노트북·워크플로 무변경. 결과는 `experiments/weekly_sequence/S04/<run_id>/`에만 남긴다.

## Global Constraints

- 스냅샷은 `runs/weekly_sequence/<target>/data_cache/target.parquet`. 러너가 내려받지 않는다. 반입 절차는 Task 3.
- 개발 폴드 결과를 먼저 저장하고(`dev` 단위), 잠금은 별도 단위(`lock`)로 **개발 결과가 완료된 뒤에만** 실행한다. 잠금 단위는 manifest에 `lock_opened_at`을 남기고, 같은 target에 대해 두 번째로 열려고 하면 러너가 거부한다(`experiments/weekly_sequence/S04/LOCK_OPENED_<target>.json` 표식).
- 운영 Ridge 입력 pkl이 없으면 S04는 **진행하지 않는다**(skipped가 아니라 종료 코드 2). 세 후보만으로는 설계 문서 기준을 적용할 수 없다.
- pkl의 data_hash와 OHLCV 스냅샷의 마지막 봉 날짜가 다르면 거부한다(같은 시점의 자료여야 공통 날짜 비교가 성립한다).
- 잠금 구간의 TCN 학습도 **개발 구간 자료로만** 한다(잠금 시작 전 만기 행). 잠금 구간을 학습에 쓰지 않는다.
- 합성 데이터 테스트는 코드 동작만 증명한다. 실제 수치는 스냅샷이 있는 환경에서 나온다.

## Review Focus

1. 잠금 단위가 개발 단위 완료 뒤에만 열리고, 두 번째 열기가 거부되는가.
2. 운영 Ridge 예측이 pkl의 특징·분할과 같은 방식(StandardScaler+Ridge 1e4, vol-scaled 타깃 되돌림)으로 재현되는가 — run_medium_horizon의 M00 재현 코드를 호출하고 새로 쓰지 않는다.
3. 공통 날짜 교집합이 네 후보 모두에 적용되는가.
4. TCN 잠금 학습이 잠금 시작 전 자료만 쓰는가(purge).
5. 판정 문장이 설계 기준을 기계적으로 적용한 결과인가(사람 판단 없음).

## Task 1: 운영 Ridge 연결 (tools/run_weekly_sequence.py)

- `load_operational_inputs(storage, target, mode)`: `runs/medium_horizon/<target>/inputs_<mode>_*.pkl`을 찾아 run_medium_horizon.load_inputs와 같은 구조로 읽는다. 없으면 None.
- `operational_ridge_predictions(inputs, folds, horizon=5)`: run_medium_horizon.analyse_horizon이 쓰는 M00 재현 경로(price_design → StandardScaler+Ridge)를 **함수로 재사용**해 폴드별 시험 예측을 얻는다. 필요하면 run_medium_horizon에 작은 공개 함수를 추출하되 기존 결과(M00 summary)와 동등한지 회귀 테스트로 확인한다.
- 예측일을 OHLCV 시퀀스의 prediction_date와 맞추는 매핑(특징 행 날짜 = 예측일 d 의 원본 봉 d-1 → prediction_date = 다음 세션)을 명시하고 테스트한다.

- [ ] 실패 테스트: pkl 없이 `--task S04` → 종료 코드 2, "운영 입력 없음" 메시지.
- [ ] 합성 pkl fixture(run_medium_horizon.extract_inputs 형식)로 운영 Ridge 예측이 만들어지고 날짜가 세션 기준으로 정렬되는지.
- [ ] pkl 마지막 봉과 스냅샷 마지막 봉이 다르면 거부.
- [ ] 회귀: 기존 M00 quick summary의 raw MAE와 새 함수 결과가 같은 폴드에서 일치(허용 1e-9). (스냅샷 없는 환경에서는 skip.)

## Task 2: S04 단위 (dev·lock·verdict)

- `dev`: S02·S03 단위를 full 설정으로 실행(개발 폴드 전체). 네 후보의 metrics/comparisons를 `metrics_dev.csv`, `comparisons_dev.csv`로.
- `lock`: `LOCK_OPENED_<target>.json`이 있으면 거부. 없으면 잠금 폴드 하나(잠금 시작~마지막 만기)를 만들고, 각 후보를 잠금 시작 전 자료로 학습해 예측. `metrics_lock.csv`, `comparisons_lock.csv`. 표식 파일에 run_id·시각·data_hash 기록.
- `verdict`: 확정 사항 2의 기준을 기계적으로 적용해 `decision.md`에 "채택 후보 / 관찰 후보 / 미채택"과 근거 표(개발·잠금 각각의 MAE·CI, seed별 값, 공통 표본 수, 빠진 날짜 수)를 쓴다. 운영 채택은 별도 결정임을 명시.

- [ ] 실패 테스트: `lock` 단위가 `dev` 완료 전이면 거부.
- [ ] 두 번째 `lock` 실행이 표식 파일로 거부되고 첫 결과가 보존되는지.
- [ ] 잠금 폴드 학습 행의 만기가 모두 잠금 시작 전인지.
- [ ] verdict 문장이 기준 조합별로 맞게 나오는지(개발만 통과 → 관찰 후보, 둘 다 통과 → 채택 후보, seed 하나라도 열위 → 미채택).
- [ ] decision.md에 "운영 채택은 별도 결정" 문구.

## Task 3: 실제 스냅샷 반입·실행 (사용자 환경)

이 컨테이너에는 야후 접근이 없어 스냅샷을 만들 수 없다. 두 경로 중 하나:

- **A. Colab 실행**: 저장소를 Colab에서 열고 `runs/medium_horizon/<target>/data_cache`를 만든 M00 절차(노트북 `USE_DATA_CACHE=True` 캐시 모드 1회)로 스냅샷과 `inputs_full_<hash>.pkl`을 만든 뒤, 같은 세션에서 `python tools/run_weekly_sequence.py --task S04 --target samsung --mode full --storage runs/weekly_sequence --resume`. 산출물(`experiments/weekly_sequence/S04/`)만 커밋한다. `runs/`는 커밋하지 않는다.
- **B. 로컬 복사**: 사용자 PC에 `runs/model_improvement/P00/<target>/` 스냅샷이 있으면 `runs/weekly_sequence/<target>/data_cache/`로 복사하고 위 명령을 로컬에서 실행.

- [ ] 스냅샷 마지막 봉 날짜와 pkl data_hash를 실행 전에 기록한다.
- [ ] samsung → sk_hynix 순서로 각각 실행. 종목별로 잠금은 한 번씩.
- [ ] 산출물 커밋: `experiments/weekly_sequence/S04/<run_id>/` (csv·md·json만).

## Task 4: 기록·인수인계

- [ ] guides/weekly-sequence-model-plan.md 의 S04 상태·판정·진행 기록. 판정이 "미채택"이면 그 근거를 남기고 S05(운영 채택 결정)를 진행하지 않는다.
- [ ] 두 종목 판정을 표로: 개발 MAE 차이·CI, 잠금 MAE 차이·CI, seed별, 공통 표본, 판정.
- [ ] 커밋: feat: add S04 real-snapshot comparison with one-shot locked evaluation (코드), exp: S04 results for <target> (산출물).
- [ ] main 반영 뒤 원격 재확인.

## 다음 단계

판정이 "채택 후보"인 종목만 S05(운영 채택 결정 — 설계 문서 8절: 운영 Ridge와 병행 발행, 원장 관찰 60거래일)로 간다. "관찰 후보"는 원장 후보 등록 없이 월 1회 재평가. "미채택"은 근거를 남기고 종료한다.
