# R02c 누적 야간/장중 특징 그룹 D 를 다음 날 방향 대표 모델에 더했을 때

**결론: 채택하지 않는다.** 대표 모델(`No macro ensemble`, 시세만 5년 창)의 입력에 그룹 D 18열(종목·KOSPI 의 최근
5/20/60 세션 누적 야간 수익률·누적 장중 수익률·둘의 차)을 더해 **같은 12폴드·같은 날짜·같은 정답**에서 다시 학습해도
log_loss·balanced_accuracy·갭 AUC·세션 AUC **열 칸 전부 동률**이다(두 종목 × 다섯 지표, 95% 월 블록 CI 가 모두 0 을 품는다).
채택 규칙(두 종목 log_loss CI 상한 < 0, 또는 한 종목 명확 우위 + 다른 종목 동률)에 해당하지 않는다.

**세션 쪽에도 더하는 것이 없다.** 질문은 "누적 야간/장중 정보가 장중(시가→종가) 방향에 무엇이든 더하는가"였다.
세션 AUC 차이는 samsung +0.0031 [−0.0044, +0.0100], sk_hynix −0.0028 [−0.0097, +0.0037] — 0.5 근처에서 움직이지 않는다.
갭 AUC(≈0.81/0.83)도 그대로다. 예측력은 여전히 전일 미국 시장이 설명하는 갭에 있고, 과거 5~60일의 야간·장중 누적은
다음 날 세션에 대한 정보가 아니다.

계획: [guides/research-candidates-plan.md](../../../../guides/research-candidates-plan.md) R02 의 방향 모델 변형(계획 밖 등록 2026-09-16).
중기(5·20일) 가격 모델의 같은 특징·타깃 분해는 [experiments/medium_horizon/R02/decision.md](../../../medium_horizon/R02/decision.md).

## 설정

- 기준선: `experiments/model_improvement/P00/20260910T0100Z_baseline_full` 과 같은 고정 스냅샷(samsung d8a92744 / sk_hynix
  8798bdb0)·같은 12개 외부 폴드(2021-01 ~ 2026-09, 6개월, 5년 창). 노트북은 이 실행에서 캐시로 다시 돌렸고(P10b 와 같이
  코드가 P00 이후 바뀌어 평가일 1,367 / 1,362, 시세만 입력 61 / 58열), 비교는 같은 실행 안에서 했다.
- 후보는 **하나**: `current + D` (시세만 입력 + 그룹 D 18열 = 79 / 76열). 기준 `current (market only)` 는 같은 코드로 다시
  학습했고 노트북이 같은 폴드로 낸 대표 모델 OOF 와 **argmax 일치율 1.000, log_loss 차이 0** — 재현이 정확하다.
- 모델·후보·온도·선택 규칙은 대표 모델과 같다(`_ensemble_probabilities` = Logistic + LightGBM 확률 평균, 내부 3구간 log loss 선택).
- 그룹 D 정의는 중기 러너와 **같은 함수**(`run_medium_horizon.group_d_features`): 원본 시가·종가, 자산 달력에서 누적한 뒤
  노트북 달력(봉 ∪ 예측일)으로 정렬하고 한 칸 민다 → 행 d 는 d−1 세션까지. 그룹 D 결측 날짜(첫 세션 1일)는 두 후보 모두에서
  제외(공통 행, 평가일 손실 0).
- 갭·세션 수익률은 **평가에만** 쓰고 특징에 넣지 않는다(P09 규칙). AUC 차이의 CI 는 같은 날짜에서 월 블록 부트스트랩(b=2000).
- 학습 시간: current 47 / 45초, current + D 53 / 53초(12폴드 합).

## 성능 (같은 날짜·같은 정답, samsung 1,367일 / sk_hynix 1,362일)

| 종목 | 모델 | accuracy | balanced_accuracy | log_loss | brier | 갭 AUC | 세션 AUC |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| samsung | Always flat(사전확률) | 0.2824 | 0.3333 | 1.0966 | 0.6656 | 0.494 | 0.462 |
| samsung | **current (market only)** | 0.4543 | 0.4329 | 1.0285 | 0.6183 | 0.810 | 0.495 |
| samsung | current + D | 0.4601 | 0.4381 | 1.0282 | 0.6173 | 0.810 | 0.498 |
| sk_hynix | Always flat(사전확률) | 0.2739 | 0.3333 | 1.0924 | 0.6628 | 0.496 | 0.476 |
| sk_hynix | **current (market only)** | 0.4993 | 0.4652 | 1.0146 | 0.6086 | 0.829 | 0.489 |
| sk_hynix | current + D | 0.4919 | 0.4579 | 1.0176 | 0.6104 | 0.823 | 0.487 |

노트북 OOF(`No macro ensemble`) 행은 current 와 소수점 넷째 자리까지 같다(metrics.csv).

## 쌍체 비교 — current + D − current (95% 월 블록 CI; log_loss 는 음수가, 나머지는 양수가 개선)

| 종목 | 지표 | delta | 판정 |
| --- | --- | --- | --- |
| samsung | log_loss | −0.00026 [−0.00464, +0.00389] | 동률 |
| samsung | balanced_accuracy | +0.00529 [−0.01108, +0.02188] | 동률 |
| samsung | accuracy | +0.00585 [−0.01022, +0.02256] | 동률 |
| samsung | 갭 AUC | +0.00013 [−0.00473, +0.00472] | 동률 |
| samsung | 세션 AUC | +0.00309 [−0.00439, +0.01002] | 동률 |
| sk_hynix | log_loss | +0.00305 [−0.00170, +0.00824] | 동률 |
| sk_hynix | balanced_accuracy | −0.00725 [−0.02156, +0.00713] | 동률 |
| sk_hynix | accuracy | −0.00734 [−0.02216, +0.00680] | 동률 |
| sk_hynix | 갭 AUC | −0.00507 [−0.01385, +0.00392] | 동률 |
| sk_hynix | 세션 AUC | −0.00278 [−0.00970, +0.00370] | 동률 |

참고(사전확률 대비): current + D − Always flat log_loss samsung −0.0684 [−0.0853, −0.0501], sk_hynix −0.0748 [−0.0908, −0.0586] —
대표 모델과 같은 크기다. 즉 그룹 D 를 넣어도 잃지 않았지만 얻은 것도 없다.

## 보류 진단 — 최대 확률 ≥ 0.50 (P08 방식, 진단만. 현재 운영 기준 `DIRECTION_ISSUE_MIN_PROB` 는 0.0)

| 종목 | 모델 | 발행일 | coverage | 발행일 accuracy | 전체 accuracy |
| --- | --- | ---: | ---: | ---: | ---: |
| samsung | current (market only) | 334 / 1,367 | 24.4% | 0.680 | 0.454 |
| samsung | current + D | 319 / 1,367 | 23.3% | 0.661 | 0.460 |
| sk_hynix | current (market only) | 386 / 1,362 | 28.3% | 0.640 | 0.499 |
| sk_hynix | current + D | 389 / 1,362 | 28.6% | 0.643 | 0.492 |

임계치 0.6·0.7 행은 `selective.csv`. 발행 집합이 달라 쌍체 비교가 아니며 채택 근거로 쓰지 않는다(P08·P10b 와 같은 주의).

## 읽는 법

- **동률 열 칸은 "정보가 없다"는 뜻이지 "해롭다"는 뜻이 아니다.** P03 의 월별 지표·수급처럼 유의하게 나빠지지도 않았다.
  18열이 들어가도 규제된 로지스틱·얕은 LightGBM 이 그 열을 거의 쓰지 않았다는 해석이 가장 단순하다.
- **세션 AUC 는 두 종목 모두 0.49~0.50 에 머문다.** 야간/장중을 분리한 논문의 결과(야간 지속·장중 반전)는 월 단위 포트폴리오
  수익률의 이야기이고, 이 프로젝트의 "내일 시가→종가 부호"에는 5~60일 누적 성분이 닿지 않는다. 중기 R02 의 성분 표(여덟 칸 모두
  0 포함)와 같은 방향의 결과다.
- 삼성 balanced_accuracy +0.005, 하이닉스 −0.007 은 부호가 갈리고 둘 다 CI 폭(±0.015)의 절반 이하다. 종목별 판단 규칙대로
  각각 동률이며, 한쪽의 부호를 근거로 삼지 않는다.

## 채택

없음. 대표 모델·특징 목록을 유지한다. 그룹 D 빌더·러너 `R02c`·테스트(`tests/test_overnight_intraday.py`)는 기록으로 남긴다.

## 다음

다음 날 종가 방향의 남은 예측력은 갭이고 세션은 무작위와 구분되지 않는다는 결론이 P09·P10b·R02c 세 실험에서 같은 방향으로 나왔다.
07:00 특징을 더 늘리는 것(월별 지표·수급·패널·커브·야간/장중 누적 모두 동률 또는 열위)보다, **갭이 반영된 뒤(09:00 시가 이후)
남은 세션 방향을 다시 묻는 것**이 정보 시점상 유일하게 남은 방향이다. 09:37 재예측(시가·개장 직후 체결 정보를 특징으로 한
세션 타깃)은 별도 데이터 수집(시가·장초 가격)이 필요하며, 이 실험처럼 세션 AUC 가 0.5 를 유의하게 넘는지를 먼저 재야 한다.

## 재현

```powershell
$env:PREDICT_STOCK_PUBLISH = 'false'
python tools/run_model_improvement.py --task R02c --target samsung --mode full --storage runs/model_improvement --resume
python tools/run_model_improvement.py --task R02c --target sk_hynix --mode full --storage runs/model_improvement --resume
```

원본 실행: `runs/model_improvement/R02c/20260916T115212Z_samsung_r02c`, `…T115214Z_sk_hynix_r02c` (manifest `source_runs`).
