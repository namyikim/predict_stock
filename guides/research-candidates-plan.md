# 논문 기반 개선 후보와 실험 계획

작성일: 2026-09-10
근거 커밋: `583b18e` (중기 계획 M00~M08 결과), `02ebb17` (다음 날 방향 계획 P00~P10 결과)
상태: 계획 작성. R01~R06 미실행.

## 1. 왜 이 논문들인가 — 측정된 실패에 맞춘 선정

두 계획의 결과가 가리키는 실패는 넷이다. 논문은 그 실패를 직접 겨냥하는 것만 골랐다.

| 측정된 실패 | 근거 | 겨냥하는 후보 |
| --- | --- | --- |
| **구간 포함률이 국면 전환에서 무너진다.** 잠금(2025-08~2026-09) 20일 포함률 0.51(하이닉스)·0.58(삼성), 5일 0.73~0.77(목표 0.80). 현행·HAR·잔차 252개 보정 모두 실패 | M05·M07 decision.md | R01 온라인 적응 구간 |
| **가격 중심값의 신호가 약하다.** raw 예측이 현재가 유지를 안정적으로 못 이김. 특징(M02)·규제(M03)·패널(M06)로 개선 없음 | M00~M06 | R02 야간/장중 분해, R03 약한 신호용 결합 |
| **예측력은 갭(야간)에 있고 세션(장중)에는 없다.** 갭 AUC ≈ 0.8, 세션 AUC ≈ 0.5, 세션 순수익 ≈ 0 | P09, 노트북 8절 | R02 |
| **발행 판정이 국면에 민감하다.** 삼성 20일은 발행하면 해롭고, 하이닉스 20일 판정은 실행마다 뒤집힘 | M00·M03·M05 | R04 국면 변화 탐지, R01 |

### 운영 규칙(기존 계획과 같다)
- 실험은 `PREDICT_STOCK_PUBLISH=false`, 고정 스냅샷(`runs/medium_horizon/<target>/data_cache`, 입력 pkl)에서. `docs/`·`forecast_history/`를 덮지 않는다.
- 평가 계약은 M01 그대로: 개발 6개월 폴드 9개(2021-01~2025-06/08) + 잠금 12개월 1회, 날짜 기준 purge, 내부 3구간 선택. 판정은 중기 계획 4절(쌍체 95% CI, 월 블록 + 연속 블록; CI 가 0 포함이면 우위 미확인).
- 러너는 `tools/run_medium_horizon.py` 에 `--task R0x` 로 붙인다(재개·해시·산출물 규칙 동일). 결과는 `experiments/medium_horizon/R0x/<run_id>/`.
- 채택 없이 끝날 수 있다. 개발 폴드 통과 → 잠금 1회 → 원장 사전 예측 관찰(M07 방식)이 순서다.
- 새 유료 자료·GPU 없이 되는 것을 우선했다. 자료가 필요한 후보는 필요 자료를 명시하고 확보 전에는 보류한다.

## 2. 후보 논문과 기대 효과

| ID | 논문 | 핵심 아이디어 | 이 프로젝트에서 기대하는 것 | 필요 자료 | 우선순위 |
| --- | --- | --- | --- | --- | --- |
| R01 | Gibbs & Candès (2021) *Adaptive Conformal Inference Under Distribution Shift*, NeurIPS; Angelopoulos, Candès & Tibshirani (2023) *Conformal PID Control for Time Series Prediction*, NeurIPS; Xu & Xie (2021) *Conformal prediction interval for dynamic time-series* (EnbPI), ICML | 분포가 바뀌어도 장기 포함률을 목표에 맞추는 온라인 구간 갱신. 빗나감이 잦으면 폭을 키우고, 과잉 포함이면 줄이는 제어 규칙(P·I 항) | 국면 전환에서 0.5 로 떨어진 20일 포함률을 0.8 근처로 되돌리는 것. 중심값 모델은 그대로 두고 폭만 바꾼다 | 없음(원장 잔차) | **1** |
| R02 | Lou, Polk & Skouras (2019) *A tug of war: Overnight versus intraday expected returns*, JFE; Rapach, Strauss & Zhou (2013) *International Stock Return Predictability: What Is the Role of the United States?*, JF | 야간 수익률과 장중 수익률은 지속성·반전이 다르고, 미국 수익률이 다른 나라 수익률을 지연 반영한다 | 5·20일 수익률을 야간 합 + 장중 합으로 나눠 예측하고, 누적 야간/장중 성분을 특징으로 쓴다. 갭에만 있는 예측력을 중기 지평으로 옮기는 시도 | 없음(시가·종가) | **2** |
| R03 | Elliott, Gargano & Timmermann (2013) *Complete subset regressions*, J. Econometrics; Kelly, Malamud & Zhou (2024) *The Virtue of Complexity in Return Prediction*, JF | 약한 신호·많은 예측변수에서 (a) 소수 변수 회귀들의 평균, (b) 매우 많은 무작위 특징 + 릿지가 단일 축소 회귀보다 낫다 | 78열 Ridge(1e4) 하나에 걸려 있는 현행을 결합·고차원 쪽으로 넓혀 본다 | 없음 | 3 |
| R04 | Wood, Roberts & Zohren (2022) *Slow Momentum with Fast Reversion*, J. Financial Data Science (온라인 변화점 탐지 모듈) | 추세 전환 직후 잘못된 베팅을 줄이기 위해 변화점 점수를 모델 입력·포지션 조절에 넣는다 | 변화점 점수를 발행 보류 정책과 R01 의 갱신 속도에 넣어 국면 전환 직후의 발행·과소 구간을 줄인다 | 없음 | 3 |
| R05 | Bollerslev, Patton & Quaedvlieg (2016) *Exploiting the errors* (HARQ), J. Econometrics | 실현변동성의 측정오차(실현 사분위)에 따라 HAR 계수를 바꿔 반응성을 높인다 | HAR 구간(M05 후보)의 국면 반응을 개선. 다만 일중 자료가 없어 **범위(고가·저가) 기반 대용**으로만 가능 | 일중 자료 없음 → 일봉 고저 대용 | 4(조건부) |
| R06 | Ke, Kelly & Xiu (2020) *Predicting Returns with Text Data* (SESTM) | 수익률 예측에 맞춰 단어를 선별·가중하는 지도 감성 점수 | 현행 뉴스심리지수(외부 지수) 대신 종목 맞춤 감성. 다만 기사 원문·시각이 있는 한국어 코퍼스가 필요 | 기사 코퍼스(미확보) | 보류 |

**제외한 것과 이유.** Transformer·Kronos 등 시계열 기반 모델: 이미 측정해 열위(log loss 1.0849 vs 1.0321, 앙상블 편입 시 유의 악화). Deep Momentum Networks(Lim, Zohren & Roberts 2019): 샤프 최적화 포지션 학습이 목적이라 이 프로젝트의 예측 정확도·구간 목표와 다르다(변화점 아이디어만 R04 로). Gu, Kelly & Xiu(2020)의 횡단면 ML: 수천 종목 횡단면 전제라 2종목 시계열에는 맞지 않는다(M06 패널이 이미 그 방향의 축소판).

## 3. 실험 계획

### R01 — 온라인 적응 예측 구간 (ACI / Conformal PID / EnbPI)

**가설.** 구간 폭을 고정 분위수가 아니라 최근 빗나감 비율로 갱신하면, 국면 전환 뒤 포함률이 수십 일 안에 목표로 복귀한다.

- 중심값: 현행(M05 와 같은 폴드별 내부 기울기 보정 예측; 발행 없으면 현재가). **중심값은 바꾸지 않는다.**
- 비적합 점수 s_t = |y_t − c_t| / σ_t (σ = 20일 변동성 × √h). 구간 = c_t ± q_t σ_t.
- 후보(계수는 점수를 보기 전에 고정):
  - `aci`: α_{t+1} = α_t + γ(0.2 − err_t), err_t = 1{s_t > q_t}, q_t = 만기 도래 점수 최근 252개의 (1−α_t) 분위. γ = 0.01(주), {0.005, 0.02} 민감도.
  - `pid`: 분위 추적(P) + 누적 오차 항(I). Angelopoulos 등의 기본 설정(학습률 = 최근 점수 척도 × 0.1, I 항 창 252)을 그대로 쓴다. D 항(scorecaster)은 쓰지 않는다.
  - `enbpi`: Ridge 부트스트랩 20개(학습 행 복원추출)의 leave-out 잔차로 분위를 만들고 슬라이딩 창으로 갱신.
- **지연 피드백.** h일 뒤에야 만기가 오므로 갱신은 target_date < 예측일인 점수만 쓴다(Hyndman 등 *Online conformal inference for multi-step time series*, 2024; 지연 피드백 ACI 논의 참고). 지연은 h 로 고정하고 h 마다 별도 상태를 둔다.
- 기준: `simple_inner_q`(현행), `har_inner_q`.
- 지표: (주) 포함률의 목표 편차 |cov − 0.80| 와 80% interval score, (보조) 폭, 국면별(σ 3분위) 포함률, 전환 뒤 복귀 시간(포함률 60일 이동평균이 0.75 를 회복하는 데 걸린 일수).
- 판정: 개발 폴드와 잠금 모두에서 |cov − 0.80| ≤ 0.05 이고, interval score 가 현행 대비 CI 상한 < 0 이거나 동률이면서 포함률 편차가 유의하게 작을 것. 폭만 키워 포함률을 맞춘 결과는 score 로 걸러진다.
- 누수 검사: 미래 잔차를 바꿔도 과거 q_t 불변(테스트), 갱신에 쓰인 점수의 만기 날짜 < 예측일(테스트).
- 운영 반영(채택 시): 상태 α_t 는 원장의 만기 도래 잔차로 매일 처음부터 재계산할 수 있어 별도 상태 저장이 필요 없다(노트북 36셀 `band_q` 대체). 복구값: 현행 `band_q`.
- 비용: 러너 30분, CPU. 완료 증거: `experiments/medium_horizon/R01/*/decision.md`, 4조합 표.

### R02 — 야간/장중 분해와 미국 선행

**가설.** 5·20일 수익률의 야간 성분은 지속성이, 장중 성분은 반전이 있어 따로 예측해 합치면 통째로 예측하는 것보다 낫다. 갭 예측력(P09)이 이 성분에 들어 있다.

- 특징 그룹 D(기존 열과 중복 없음): 종목·KOSPI 의 최근 5/20/60일 누적 야간 수익률 Π(open_t/close_{t−1})−1, 누적 장중 수익률 Π(close_t/open_t)−1, 둘의 차. 행 d 에는 d−1 종가까지(장중 성분은 d−1 세션까지). 미국 누적은 그룹 A 와 중복이라 만들지 않는다.
- R02a: `full_plus_D` 를 M02 방식(공통 행, 내부 선택 경로)으로 현행과 비교.
- R02b: 타깃 분해. y = y_overnight + y_intraday (같은 h 구간의 야간 합·장중 합). 각각 Ridge(1e4) 로 예측해 합친 `decomposed` 를 `current` 와 비교. 두 성분의 raw 예측력(각각 유지 대비)을 따로 표로 남긴다 — 어느 성분에 신호가 있는지가 결과다.
- 판정: 보정 후 수익률 MAE 후보−현행 CI 상한 < 0. 성분별 표는 진단이며 채택 근거로 쓰지 않는다.
- 누수 검사: 성분 합이 원래 라벨과 같음(항등식, 1e-12), 미래 변경 불변.
- 비용: 20분. 자료 있음.

### R03 — 약한 신호용 결합: 완전 부분집합 회귀와 무작위 특징 릿지

**가설.** 78열 하나의 강축소 회귀보다 (a) 변수 1~3개 회귀들의 평균(CSR), (b) 무작위 푸리에 특징 P≫n 의 릿지(VoC)가 약한 신호를 더 안정적으로 뽑는다.

- `csr_k` (k=1,2,3): 모든 k-부분집합(k=3 은 76,076개라 2,000개 무작위 표본, 시드 고정)의 OLS 예측 평균. 타깃은 현행과 같은 변동성 스케일.
- `rff_ridge` (P ∈ {1000, 5000}, 릿지 z ∈ {1e-3, 1e-2, 1e-1, 1, 10}): 표준화 입력 → cos(Wx+b) 무작위 특징 → 릿지. 내부 3구간으로 (P, z) 선택(M03 규칙).
- 기준: 현행. 판정: 보정 후 MAE·발행 중심값 MAE 후보−현행 CI 상한 < 0, 발행률 병기. raw 가 커진 것은 근거가 아니다.
- 주의: VoC 는 월별 시장 지수 예측이 무대이고 후속 논쟁이 있다. 여기서는 "복잡한 쪽이 낫다"를 가정하지 않고 같은 계약에서 측정만 한다.
- 비용: CSR 1시간, RFF 30분(CPU).

### R04 — 변화점 점수를 넣은 보류·갱신 정책

**가설.** 변화점 직후에는 예측을 내지 않는 것이 유지보다 낫고, R01 의 갱신 속도를 변화점 뒤에 높이면 복귀가 빨라진다.

- 변화점 점수: 일별 수익률에 대한 베이지안 온라인 변화점 탐지(Adams & MacKay 2007, run-length 분포)의 "최근 20일 안에 변화점 확률". Wood 등의 GP 기반 모듈은 무겁고 GPU 가 필요하지 않지만 느려서 대용으로 BOCPD 를 쓴다(대용임을 기록).
- 정책 후보: `hold_if_cp` (점수 > 0.5 이면 현재가), `gate_2of3 & !cp`, R01 의 γ 를 점수에 비례해 2배까지 키우는 `aci_cp`.
- 기준: M05 의 gate_2of3·gate_3of3·never_issue. 판정: 발행 중심값 MAE 후보−유지 CI 상한 < 0, 발행률·발행일 MAE 병기. 구간은 R01 판정 규칙.
- 누수 검사: 점수는 d−1 까지의 수익률만(테스트).
- 비용: 30분.

### R05 — 범위 기반 HARQ (조건부)

- 일중 자료가 없어 실현 사분위를 구할 수 없다. 대용: 일봉 고저 범위(Parkinson)로 일별 분산, 그 제곱의 이동평균으로 측정오차 대용. HARQ 형태(계수 = β₀ + β₁·√RQ_proxy)로 σ 를 예측해 R01 의 σ_t 로 쓴다.
- 판정: R01 과 같은 구간 규칙. HAR 대비 우위가 없으면 종료.
- 조건: R01 결과가 σ 척도 개선을 요구할 때만 실행한다.

### R06 — 지도 감성(SESTM) (보류)

- 필요 자료: 종목 관련 한국어 기사 원문 + 발행 시각(수년치). 현재 저장소에는 없다(회고 도구는 RSS 헤드라인만, 과거분 없음).
- 확보되면: 학습 구간에서만 단어 선별·가중(누수 주의), 감성 점수를 특징으로 M02 방식 비교. 그 전까지 보류.

## 4. 체크리스트

| ID | 완료 | 작업 | 선행 | 결론 |
| --- | --- | --- | --- | --- |
| R01 | [ ] | 온라인 적응 구간(aci·pid·enbpi) vs 현행·HAR | 없음(M05 코드 재사용) | 미실행 |
| R02 | [ ] | 야간/장중 분해 특징·타깃 분해 | 없음(M02 코드 재사용) | 미실행 |
| R03 | [ ] | CSR·무작위 특징 릿지 | 없음(M03 코드 재사용) | 미실행 |
| R04 | [ ] | 변화점 점수 보류·갱신 정책 | R01 | 미실행 |
| R05 | [ ] | 범위 기반 HARQ σ | R01 결과에 따라 | 조건부 |
| R06 | [ ] | SESTM 감성 | 기사 코퍼스 확보 | 보류 |

각 작업은 M-계획과 같은 완료 기록 규칙(코드·검증·결과·문서를 한 커밋, decision.md, 체크리스트·재개 지점 갱신)을 따른다. 통과한 후보는 M07 방식으로 원장에 `Candidate …` 행을 추가해 관찰한다. 잠금 평가는 후보당 1회다.

## 5. 재개 지점

- 현재 작업: 없음. 다음 실행은 R01(가장 뚜렷한 실패를 겨냥하고 새 자료가 필요 없다).
- 먼저 읽을 파일: 본 문서, `experiments/medium_horizon/M05/…/decision.md`, `M07/decision.md`, `tools/run_medium_horizon.py`(run_m05·band_quantile·matured_residuals).
- 재개 요청 예시: `guides/research-candidates-plan.md를 읽고 R01만 진행해주세요. 실제 완료한 항목만 체크하고 결과·검증·다음 재개 지점을 기록한 뒤 커밋·push해주세요.`

## 6. 출처

- Gibbs, I., & Candès, E. (2021). Adaptive Conformal Inference Under Distribution Shift. NeurIPS 34. https://proceedings.neurips.cc/paper/2021/hash/0d441de75945e5acbc865406fc9a2559-Abstract.html
- Angelopoulos, A. N., Candès, E. J., & Tibshirani, R. J. (2023). Conformal PID Control for Time Series Prediction. NeurIPS 36. https://proceedings.neurips.cc/paper_files/paper/2023/hash/47f2fad8c1111d07f83c91be7870f8db-Abstract-Conference.html
- Xu, C., & Xie, Y. (2021). Conformal prediction interval for dynamic time-series. ICML. https://proceedings.mlr.press/v139/xu21h.html (코드 https://github.com/hamrel-cxu/EnbPI)
- Wang, X., & Hyndman, R. J. 등 (2024). Online conformal inference for multi-step time series forecasting. https://arxiv.org/pdf/2410.13115
- Lou, D., Polk, C., & Skouras, S. (2019). A tug of war: Overnight versus intraday expected returns. Journal of Financial Economics 134(1), 192–213. https://ideas.repec.org/a/eee/jfinec/v134y2019i1p192-213.html
- Rapach, D. E., Strauss, J. K., & Zhou, G. (2013). International Stock Return Predictability: What Is the Role of the United States? Journal of Finance 68(4), 1633–1662. https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12041
- Elliott, G., Gargano, A., & Timmermann, A. (2013). Complete subset regressions. Journal of Econometrics 177(2), 357–373. https://econpapers.repec.org/RePEc:eee:econom:v:177:y:2013:i:2:p:357-373
- Kelly, B. T., Malamud, S., & Zhou, K. (2024). The Virtue of Complexity in Return Prediction. Journal of Finance. https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13298
- Wood, K., Roberts, S., & Zohren, S. (2022). Slow Momentum with Fast Reversion: A Trading Strategy Using Deep Learning and Changepoint Detection. Journal of Financial Data Science 4(1), 111–129. https://arxiv.org/abs/2105.13727
- Lim, B., Zohren, S., & Roberts, S. (2019). Enhancing Time Series Momentum Strategies Using Deep Neural Networks. Journal of Financial Data Science. https://arxiv.org/pdf/1904.04912
- Bollerslev, T., Patton, A. J., & Quaedvlieg, R. (2016). Exploiting the errors: A simple approach for improved volatility forecasting. Journal of Econometrics 192(1), 1–18. https://ideas.repec.org/a/eee/econom/v192y2016i1p1-18.html
- Ke, Z. T., Kelly, B. T., & Xiu, D. (2020). Predicting Returns with Text Data. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3389884
- Gu, S., Kelly, B., & Xiu, D. (2020). Empirical Asset Pricing via Machine Learning. Review of Financial Studies 33(5), 2223–2273. https://academic.oup.com/rfs/article/33/5/2223/5758276
