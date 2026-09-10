# M07 — 고정 후보 사전 예측 관찰 (2026-09-10 등록)

실행: 잠금 평가 `M07/20260910T*_sk_hynix_m07`, `M07/20260910T*_samsung_m07` (full, 고정 입력 캐시). 후보 등록은 노트북 36·38셀
(`PRICE_CANDIDATES`, `price_candidate_rows`), 헤드라인 집계 보호는 `forecast_utils.review_ledger`(모델명 "Candidate …" 제외).

## 1. 고정 후보 (잠금 점수를 보기 전에 고정, 이후 조정하지 않음)

| 종목 / 지평 / 목적 | 후보 | 원장 모델명 | 정의 | 개발 폴드 근거(M05) |
| --- | --- | --- | --- | --- |
| sk_hynix / 5 / 구간 | har_interval | `Candidate HAR interval` | 헤드라인 중심값 + 노트북 HAR 변형의 반폭 | 구간 점수 −0.0098 [−0.0162, −0.0024] |
| sk_hynix / 20 / 구간 | har_interval | `Candidate HAR interval` | 같음 | 구간 점수 −0.0223 [−0.0414, −0.0037] |
| sk_hynix / 5 / 가격 | strict_gate | `Candidate strict gate` | 선택 구간 CI 상한 < 0 **이고** 평가 구간 CI 상한 < 0 일 때만 점 예측(노트북 판정의 엄격판) | 3/3 판정 발행 중심값 −0.00027 [−0.00054, −0.00005] |

samsung 두 지평과 방향 목적(M04)에는 기준을 통과한 후보가 없다 → **미채택으로 종료**. M02 의 full_plus_A(samsung/20) 는 사전 선언한 선택 장치가
고르지 않아 후보로 올리지 않는다(관찰 대상 메모만).

## 2. 잠금 평가 (1회, 마지막 12개월: 5일 2025-09-04~2026-09-03, 20일 2025-08-13~2026-08-12, 각 226행)

**주의**: 잠금 구간은 M00 의 평가 구간(2024-08~)과 겹쳐 이미 본 점수다. 새로운 독립 검증이 아니며 최종 확인은 아래 3절의 사전 예측이다.

| 검정 | delta [월 블록 95% CI] | 부트스트랩 p | Holm(주 지표 3개) |
| --- | --- | --- | --- |
| sk_hynix/5 HAR − simple, 구간 점수 | −0.0279 [−0.0589, +0.0011] | 0.066 | 0.066 |
| sk_hynix/5 HAR − simple, 포함률 | −0.044 [−0.092, −0.004] (0.73 vs 0.77) | 0.039 | — |
| sk_hynix/20 HAR − simple, 구간 점수 | +0.0293 [−0.1159, +0.2302] | 0.833 | 0.833 |
| sk_hynix/20 HAR − simple, 포함률 | −0.035 [−0.089, +0.022] (0.51 vs 0.54) | 0.260 | — |
| sk_hynix/5 strict_gate − 유지, 발행 중심값 MAE | −0.0045 [−0.0072, −0.0019] | <0.001 | <0.001 |
| sk_hynix/5 strict_gate − 2/3 판정 | 0 (잠금 폴드에서 두 판정 모두 발행, 내부 3/3) | 1.0 | — |

- HAR 구간은 잠금에서 통과하지 못했다(점수 개선 미확인, 5일 포함률은 유의하게 더 낮음). 같은 잠금 기간에 재조정하지 않는다. 관찰은 계속한다.
- strict_gate 는 잠금에서 유지보다 유의하게 낫지만, 이 폴드에서는 2/3 판정과 같은 결정(발행)이라 "엄격함"의 이득이 아니라 모델 자체가
  잠금 기간에 유지를 이긴 것(raw 0.0988 vs 유지 0.1034, 내부 기울기 1.0)이다. M00 평가 구간의 하이닉스 5일 raw 우위와 같은 자료다.
- 잠금 구간의 참고치(모든 조합): 20일 구간 포함률이 0.54(하이닉스)·0.58(삼성)으로 무너졌고 5일도 0.76~0.77 이다. 유지 MAE 가 20일 29%(하이닉스)·
  20%(삼성)에 이르는 급등 국면에서 보정 구간의 q 가 너무 작았다. 이것이 M00 에서 본 '구간 오차'의 실체이며, 후보 HAR 도 해결하지 못한다.
  삼성 20일은 잠금에서 내부 판정 0/3, 기울기 0(모델이 해롭다는 M03 결론 그대로).

## 3. 사전 예측 관찰 (2026-09-11 첫 기록부터)

- 기록: 매일 아침 실행이 sk_hynix 원장에 `Candidate HAR interval`(5·20일)과 `Candidate strict gate`(5일) 행을 헤드라인 `Ridge` 행과 같은
  origin/target 날짜·config_hash 로 추가한다(record_id `…:price_candidate:<후보>:<h>`). 보고서·앙상블·헤드라인 집계에는 넣지 않는다.
  원장 보존(최초 사전 예측만), 재실행 중복(prediction_date 당 첫 행), 미도래 채점(pending), 결측 fallback(HAR 없으면 행 생략)은 기존
  원장 규칙과 테스트를 그대로 따른다.
- 최초 점검 조건: 5일 후보 = 공통 만기 도래 60개 및 비중첩 12개 이상(약 2026-12), 20일 후보 = 120개 및 비중첩 6개 이상(약 2027-03).
  숫자 충족은 채택 조건이 아니라 점검 조건이다. 매일 점수로 재조정하지 않고, 첫 점검 뒤 추가 60개 공통 채점마다 다시 본다(이력 보존).
  CI 가 불명확하면 계속 관찰한다(특히 20일). 과거 소급 예측은 표본에 넣지 않는다.
- 점검 명령: `python tools/run_medium_horizon.py --task M07 --target sk_hynix --mode full --resume` 는 잠금 재평가가 아니라
  `candidate_ledger_status`(관찰일·발행일·만기 도래·비중첩 수)를 summary 에 남긴다. 판정 규칙은 계획 4절(구간 점수 CI 상한 < 0 이면서 포함률이
  폭 축소만으로 얻은 것이 아닐 것; 가격은 발행 중심값 MAE CI 상한 < 0).
- 현재 현황(2026-09-10): 관찰 0 · 발행 0 · 만기 0 · 비중첩 0. 상위 체크는 관찰 판정 뒤에만 채운다.

## 재현

```powershell
$env:PREDICT_STOCK_PUBLISH = 'false'
python tools/run_medium_horizon.py --task M07 --target sk_hynix --mode full --resume
python tools/run_medium_horizon.py --task M07 --target samsung --mode full --resume
python tools/run_notebook.py --storage runs/medium_horizon/sk_hynix --targets sk_hynix --quick --use-cache   # 후보 행 기록 확인(로컬)
```
