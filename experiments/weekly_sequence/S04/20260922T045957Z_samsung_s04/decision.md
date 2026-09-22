# S04 판정 — samsung (full)

판정: **미채택**

**운영 채택은 별도 결정이다**(설계 문서 8절). 이 문서는 설계 기준을 기계적으로 적용한 결과이고, 사람의 판단을 담지 않았다.

## 기준

- 채택 후보: 개발 폴드와 잠금 구간 **모두**에서 tcn_mean 의 MAE 가 현재가 유지·운영 Ridge·OHLCV Ridge 각각보다 낮고 95% CI 상한 < 0. seed 셋 모두 운영 Ridge 보다 낮아야 한다.
- 관찰 후보: 개발에서만 통과. 미채택: 개발 실패 또는 seed 하나라도 운영 Ridge 이상.

## 근거

- seed [42, 43, 44] 의 MAE 가 운영 Ridge(0.02707) 이상 — 후보 탈락
- 개발 vs persistence: MAE 차이 +0.00272, CI 상한 +0.00686 → 미통과
- 개발 vs operational_ridge: MAE 차이 +0.00177, CI 상한 +0.00551 → 미통과
- 개발 vs ohlcv_ridge: MAE 차이 -0.00110, CI 상한 +0.00277 → 미통과
- 잠금 vs persistence: MAE 차이 +0.00161, CI 상한 +0.00615 → 미통과
- 잠금 vs operational_ridge: MAE 차이 -0.00467, CI 상한 -0.00047 → 통과
- 잠금 vs ohlcv_ridge: MAE 차이 +0.00263, CI 상한 +0.00662 → 미통과

## 개발 폴드 (공통 예측일 824개)

### 지표

| 모델 | seed | MAE | RMSE | 방향 적중률 | n |
| --- | --- | --- | --- | --- | --- |
| persistence | — | 0.02612 | 0.03424 | — | 824 |
| operational_ridge | — | 0.02707 | 0.03528 | 0.495 | 824 |
| ohlcv_ridge | — | 0.02994 | 0.04061 | 0.484 | 824 |
| tcn_seed42 | 42 | 0.02981 | 0.04862 | 0.483 | 824 |
| tcn_seed43 | 43 | 0.02941 | 0.04496 | 0.487 | 824 |
| tcn_seed44 | 44 | 0.02971 | 0.06321 | 0.483 | 824 |
| tcn_mean | — | 0.02884 | 0.04345 | 0.483 | 824 |

### 비교

- tcn_mean vs persistence: MAE 차이 +0.00272, 95% CI [+0.00023, +0.00686], n=824
- tcn_mean vs operational_ridge: MAE 차이 +0.00177, 95% CI [-0.00050, +0.00551], n=824
- tcn_mean vs ohlcv_ridge: MAE 차이 -0.00110, 95% CI [-0.00529, +0.00277], n=824

## 잠금 구간 (잠금 시작 2025-05-28)

### 지표

| 모델 | seed | MAE | RMSE | 방향 적중률 | n |
| --- | --- | --- | --- | --- | --- |
| persistence | — | 0.05911 | 0.07996 | — | 158 |
| operational_ridge | — | 0.06539 | 0.08883 | 0.430 | 158 |
| ohlcv_ridge | — | 0.05809 | 0.07901 | 0.633 | 158 |
| tcn_seed42 | 42 | 0.06422 | 0.08929 | 0.494 | 158 |
| tcn_seed43 | 43 | 0.06380 | 0.08598 | 0.468 | 158 |
| tcn_seed44 | 44 | 0.05765 | 0.07975 | 0.563 | 158 |
| tcn_mean | — | 0.06072 | 0.08346 | 0.494 | 158 |

### 비교

- tcn_mean vs persistence: MAE 차이 +0.00161, 95% CI [-0.00262, +0.00615], n=158
- tcn_mean vs operational_ridge: MAE 차이 -0.00467, 95% CI [-0.00956, -0.00047], n=158
- tcn_mean vs ohlcv_ridge: MAE 차이 +0.00263, 95% CI [-0.00118, +0.00662], n=158

## 한계

- 봉 공개 시각 assumed · 기업행동 heuristic — S02 와 같은 가정.
- 잠금 구간은 이 실행에서 한 번 열렸다. 같은 종목에 대해 다시 열 수 없다(LOCK_OPENED 표식).
- 합성 자료로 돌렸다면 판정은 코드 동작 확인일 뿐이다. manifest.snapshot 으로 실제 자료 여부를 확인한다.

code_commit 3f30e7d43055e5612b58a65f2fb31152fe4482f9 · data_hash 2ad9c2d9be16a0ea7cf5 · config_hash f4eaae126db772a51566
