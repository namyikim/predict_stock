# S04 판정 — sk_hynix (full)

판정: **미채택**

**운영 채택은 별도 결정이다**(설계 문서 8절). 이 문서는 설계 기준을 기계적으로 적용한 결과이고, 사람의 판단을 담지 않았다.

## 기준

- 채택 후보: 개발 폴드와 잠금 구간 **모두**에서 tcn_mean 의 MAE 가 현재가 유지·운영 Ridge·OHLCV Ridge 각각보다 낮고 95% CI 상한 < 0. seed 셋 모두 운영 Ridge 보다 낮아야 한다.
- 관찰 후보: 개발에서만 통과. 미채택: 개발 실패 또는 seed 하나라도 운영 Ridge 이상.

## 근거

- seed [42, 43, 44] 의 MAE 가 운영 Ridge(0.04348) 이상 — 후보 탈락
- 개발 vs persistence: MAE 차이 +0.00090, CI 상한 +0.00203 → 미통과
- 개발 vs operational_ridge: MAE 차이 +0.00075, CI 상한 +0.00157 → 미통과
- 개발 vs ohlcv_ridge: MAE 차이 +0.00040, CI 상한 +0.00111 → 미통과
- 잠금 vs persistence: MAE 차이 -0.00323, CI 상한 -0.00064 → 통과
- 잠금 vs operational_ridge: MAE 차이 -0.00244, CI 상한 +0.00032 → 미통과
- 잠금 vs ohlcv_ridge: MAE 차이 -0.00290, CI 상한 -0.00095 → 통과

## 개발 폴드 (공통 예측일 701개)

### 지표

| 모델 | seed | MAE | RMSE | 방향 적중률 | n |
| --- | --- | --- | --- | --- | --- |
| persistence | — | 0.04333 | 0.05528 | — | 701 |
| operational_ridge | — | 0.04348 | 0.05558 | 0.505 | 701 |
| ohlcv_ridge | — | 0.04383 | 0.05591 | 0.506 | 701 |
| tcn_seed42 | 42 | 0.04440 | 0.05604 | 0.498 | 701 |
| tcn_seed43 | 43 | 0.04429 | 0.05622 | 0.475 | 701 |
| tcn_seed44 | 44 | 0.04507 | 0.05659 | 0.461 | 701 |
| tcn_mean | — | 0.04423 | 0.05586 | 0.498 | 701 |

### 비교

- tcn_mean vs persistence: MAE 차이 +0.00090, 95% CI [-0.00013, +0.00203], n=701
- tcn_mean vs operational_ridge: MAE 차이 +0.00075, 95% CI [-0.00005, +0.00157], n=701
- tcn_mean vs ohlcv_ridge: MAE 차이 +0.00040, 95% CI [-0.00025, +0.00111], n=701

## 잠금 구간 (잠금 시작 2025-05-28)

### 지표

| 모델 | seed | MAE | RMSE | 방향 적중률 | n |
| --- | --- | --- | --- | --- | --- |
| persistence | — | 0.09567 | 0.12158 | — | 158 |
| operational_ridge | — | 0.09488 | 0.12015 | 0.570 | 158 |
| ohlcv_ridge | — | 0.09534 | 0.12022 | 0.538 | 158 |
| tcn_seed42 | 42 | 0.09310 | 0.11843 | 0.576 | 158 |
| tcn_seed43 | 43 | 0.09216 | 0.11803 | 0.576 | 158 |
| tcn_seed44 | 44 | 0.09274 | 0.11829 | 0.595 | 158 |
| tcn_mean | — | 0.09244 | 0.11791 | 0.589 | 158 |

### 비교

- tcn_mean vs persistence: MAE 차이 -0.00323, 95% CI [-0.00592, -0.00064], n=158
- tcn_mean vs operational_ridge: MAE 차이 -0.00244, 95% CI [-0.00598, +0.00032], n=158
- tcn_mean vs ohlcv_ridge: MAE 차이 -0.00290, 95% CI [-0.00534, -0.00095], n=158

## 한계

- 봉 공개 시각 assumed · 기업행동 heuristic — S02 와 같은 가정.
- 잠금 구간은 이 실행에서 한 번 열렸다. 같은 종목에 대해 다시 열 수 없다(LOCK_OPENED 표식).
- 합성 자료로 돌렸다면 판정은 코드 동작 확인일 뿐이다. manifest.snapshot 으로 실제 자료 여부를 확인한다.

code_commit c3d9d64c347c6226aadc3f0a75b73d83c1bfceac · data_hash fb7b58e33ca9340b5c3b · config_hash f4eaae126db772a51566
