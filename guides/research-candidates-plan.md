# 논문 기반 개선 후보와 실험 계획

작성일: 2026-09-10 (R07·R08 추가: 2026-09-10)
근거 커밋: `583b18e` (중기 계획 M00~M08 결과), `02ebb17` (다음 날 방향 계획 P00~P10 결과)
상태: R07 완료·R08 1단계 완료(2026-09-12). R01~R06 미실행, R08 2·3단계 미실행.

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
| R07 | Campbell (1987) *Stock returns and the term structure*, J. Financial Economics; Rapach, Strauss & Zhou (2010) *Out-of-Sample Equity Premium Prediction: Combination Forecasts*, RFS | 단기 금리·커브 기울기가 월 단위 이상 지평에서 주식 수익률을 예측한다(결합 예측에서 안정적) | 현재 10년물만 쓰는 금리 입력을 2년물·30년물·기울기(10Y−2Y)로 넓혀 **5·20일 가격 모델에만** 시험. 1일 방향 모델에는 넣지 않는다(P03: 거시류 특징 유의 열위) | 없음(Yahoo ^IRX/^FVX/^TYX 또는 FRED DGS2/DGS30) | 3 |
| R08 | Savor & Wilson (2013) *How Much Do Investors Care About Macroeconomic Risk?*, JFQA; Lucca & Moench (2015) *The Pre-FOMC Announcement Drift*, JF | 거시 발표일(고용·물가·FOMC)에 수익률 분포·변동성이 평일과 체계적으로 다르다 | 이벤트 일정을 NFP·PCE·ISM 제조업·FOMC 연간 전체로 넓혀 원장 표시를 확장하고, 이벤트일 구간 폭·보류 정책을 검증한다. 모델 입력이 아니라 **구간·보류 정책 재료** | 없음(BLS·BEA·ISM·연준 공표 일정) | 2(R01 과 함께) |

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

### R07 — 금리 커브 특징군 (중기 지평 한정)

**가설.** 2년물·커브 기울기는 정책 기대를 담아 5·20일 수익률에 10년물 하나보다 정보가 많다.

- 그룹 E(기존 `us10y_*` 와 중복 없음): `us2y_ret_5`, `us2y_level_z60`, `curve_slope_10y2y`(수준), `curve_slope_chg_20`, `us30y_ret_5`. 미국 마감 자산 규칙(d−1 미국 마감까지, P02 의 미완성 봉 제거)을 그대로 쓴다.
- 자료: Yahoo `^IRX`(13주)·`^FVX`(5년)·`^TYX`(30년) 또는 FRED `DGS2`·`DGS30`(API 키는 비밀로만). 2년물은 Yahoo 에 직접 티커가 없어 FRED 를 우선하고, 없으면 `^FVX` 로 대체하고 그 사실을 기록한다.
- 실험: M02 방식(`full_plus_E`, 공통 행, 폴드별 내부 선택 경로) vs current_full. 5·20일만. 판정: 보정 후 MAE 후보−현행 CI 상한 < 0.
- 예상: 동률로 끝날 가능성이 높다(M02 에서 시세·거시 특징 모두 우위 없음). 그래도 커브는 아직 시험하지 않은 유일한 금리 정보라 한 번은 측정한다.
- 비용: 20분. 누수 검사: 미국 마감 시각 정렬(기존 테스트 재사용), 미래 변경 불변.

### R08 — 미국 이벤트 일정 확장과 이벤트일 구간·보류 정책

**가설.** 고용·물가·FOMC 발표 다음 한국 거래일은 갭 변동폭이 커서 같은 폭의 구간은 덜 맞고, 발행을 쉬는 편이 낫다.

- 1단계(자료) **완료 2026-09-12**: `data_sources/us_calendar.py` 의 `US_RELEASES` 에 2026년 CPI·PPI·NFP·PCE·FOMC 를 공표 일정대로 넣었다(24→48건, 규칙 계산 금지). `US_RELEASES` 는 같은 날 두 발표가 덮어써지지 않도록 사전에서 `(날짜, 이벤트)` 쌍의 튜플로 바꿨다. ISM 제조업 PMI 는 안정적 공표 목록을 확보하지 못해 넣지 않았다.
- 2단계(진단) **완료 2026-09-12**: `tools/run_event_diagnostics.py`. 결과는 [experiments/medium_horizon/R08/decision.md](../experiments/medium_horizon/R08/decision.md). **원장이 아니라 시세로 쟀다** — 전향 원장은 2026-09-07 부터라 채점된 날이 닷새뿐이다. 일정표가 덮는 2026-01-09~2026-09-04 의 161 거래일에서 밤 사이 |갭|·하루 전체·장중을 나눠 쟀다. 열두 조합 모두 동률(부트스트랩 95% 구간이 0을 품는다).
  - 이벤트일 정의가 **두 가지**라는 것이 이 단계에서 드러났다. 계획이 말하는 "발표 다음 첫 거래일"은 29일인데, 지금 코드가 원장에 남기는 `korea_event_flags`(달력일 4일 소급)는 48일을 표시한다. 금요일 발표가 월·화를 모두 표시하기 때문이다. 3단계는 어느 정의를 쓸지 먼저 정해야 한다.
  - 구간 포함률·방향 적중률은 원장이 닷새뿐이라 나누지 못했다. 원장이 쌓이면 같은 도구로 덧붙인다.
- 2b단계(표본 늘리기, 3단계보다 먼저): 일정표를 2024~2025년으로 넓힌다. 지금은 2026년만 덮어 이벤트일이 29~48일뿐이고, 시세는 2015년부터 있으므로 과거 공표 일정만 넣으면 표본이 즉시 세 배가 된다. 기다리는 것(월 3~4회, 60일까지 8개월)보다 싸다.
- 3단계(정책 후보, R01 과 같은 계약): `event_widen`(이벤트 다음 거래일 구간 반폭 × k, k 는 과거 이벤트일 잔차 분위로 학습 구간에서만 결정), `event_hold`(이벤트 다음 거래일은 점 예측 보류). 기준: 현행 구간·현행 판정. 판정: 포함률 편차와 interval score(R01 규칙), 발행 중심값 MAE(R04 규칙).
  - 2단계에서 **장중 폭은 오히려 작았다**(네 조합 모두 음수). 소화가 갭에서 끝난다는 뜻이므로, 정책을 만든다면 구간 전체가 아니라 갭 쪽만 넓히는 형태여야 한다.
- 컨센서스 서프라이즈(예상치 대비)는 안정적 무료 출처가 없어 넣지 않는다. 확보되면 별도 항목으로.
- 비용: 자료 30분, 진단 20분, 정책 30분. 누수 검사: 일정은 미리 아는 정보(발표 결과 아님)이므로 누수 없음 — 결과값을 쓰지 않는다는 테스트를 둔다.

### R09 — 선행지수 2~3개월 전망 (ARIMA·VAR, 분산비는 진단)

**가설.** 다음 분기 영업이익 모형은 선행지수의 **현재 값**만 쓴다. 분기가 끝나기 전에 지수가 어디로 갈지를 함께 넣으면 그 모형이 나아진다.

목표는 "선행지수를 잘 맞히기"가 아니다. 선행지수 전망이 **다음 분기 이익 추정을 개선하는가**이다. 지수 자체의 오차가 줄어도 이익 추정이 그대로면 채택하지 않는다.

**자료.** `macro_history/cli_g20.csv`(G20, 2000-01~, 320개월), `macro_history/leading_cycle.csv`(국내, 2013-01~, 163개월). 발표 지연은 한 달 남짓이라 마지막 값이 전월이다. 따라서 "2~3개월 전망"은 오늘 기준 3~4개월 앞이다.

**계열 성질(2026-09-12 측정).** G20 월 변화의 표준편차 0.4727, 자기상관은 1개월 +0.49 이고 2개월 이후 사실상 0(+0.07, −0.08, −0.12, −0.06, −0.02)이다. 차분 AR(1), 즉 ARIMA(1,1,0)이 자연스럽고 2~3개월 전망의 대부분이 그 관성에서 나온다. 최근 12개월 변화는 ±0.06 으로 매우 평탄하다.

**모형.**

| 모형 | 구현 | 비고 |
| --- | --- | --- |
| 기준선 1 | 마지막 값 그대로 | 임의보행 |
| 기준선 2 | 마지막 변화 그대로 | 관성 |
| ARIMA(p,1,0) | 넘파이 최소제곱 | 새 의존성 없음 |
| VAR(p) | 넘파이 최소제곱 | 반도체 수출·국내 선행지수·장단기 금리차와 함께 |
| ARIMA(p,1,q) | statsmodels 필요 | 이동평균 항이 있어야 할 때만 추가 |

분산비(VR)는 **전망 모형이 아니라 진단**이다. 1에 가까우면 임의보행이고 그때는 무엇을 얹어도 기준선을 못 이긴다. 결과표가 아니라 모형 선택 앞단에 둔다. (벡터오차수정모형을 뜻한다면 VAR 자리에 함께 둔다.)

**두 가지 함정.**

1. **개정.** OECD 선행지수는 나중에 값이 바뀌고 특히 최근 몇 달이 크게 바뀐다. 우리는 최신본 한 벌만 덮어쓰며 보관하므로 과거 시점에 보이던 값이 없다. 최신본으로 백테스트하면 그때 알 수 없던 개정을 미리 아는 셈이라 성적이 부풀려진다. **0단계로 오늘부터 판본을 쌓는다**(`macro_history/cli_g20_vintages/<받은 날>.csv`, 받을 때마다 한 벌). 그 전까지의 결과에는 "개정 무시, 성적 과대"를 반드시 적는다.
2. **이미 평활된 지수.** 선행지수는 필터를 거친 값이라 ARIMA 가 아주 잘 맞는 것처럼 보인다. 그 정확도는 예측력이 아니라 평활의 산물이다. 그래서 위 기준선 둘을 반드시 함께 재고, 둘 다 못 이기면 채택하지 않는다.

**단계.**

- 0단계: 판본 보관을 켠다(비용 5분). 이것만으로도 나중에 정직한 평가가 가능해진다.
- 1단계: 분산비와 자기상관으로 임의보행인지 먼저 잰다. 임의보행이면 여기서 멈추고 그 사실을 기록한다.
- 2단계: 1·2·3개월 앞 전망을 확장 창 워크포워드로 재고 기준선 둘과 견준다. 판정은 기존 계약대로 CI 상한 < 0.
- 3단계: 2단계를 통과한 전망만 `FEATURES_NEXT` 에 얹어 **다음 분기 이익 추정**이 나아지는지 쌍체로 잰다. 이것이 진짜 판정이다.

**예상.** 2단계는 통과할 수 있지만 3단계는 어려울 것이다. P03 에서 거시 특징군은 두 종목 모두 유의하게 열위였고 R07 커브도 네 조합 모두 동률이었다. 그래도 선행지수는 이익 모형에서 이미 쓰이는 유일한 거시 변수라 한 번은 측정할 값이 있다.

**비용.** 0단계 5분, 1단계 20분, 2단계 40분, 3단계 30분. 누수 검사: 전망은 그 시점까지의 값만 쓰고, 판본이 쌓이기 전 결과에는 개정 경고를 붙인다.

## 4. 체크리스트

| ID | 완료 | 작업 | 선행 | 결론 |
| --- | --- | --- | --- | --- |
| R01 | [ ] | 온라인 적응 구간(aci·pid·enbpi) vs 현행·HAR | 없음(M05 코드 재사용) | 미실행 |
| R02 | [ ] | 야간/장중 분해 특징·타깃 분해 | 없음(M02 코드 재사용) | 미실행 |
| R03 | [ ] | CSR·무작위 특징 릿지 | 없음(M03 코드 재사용) | 미실행 |
| R04 | [ ] | 변화점 점수 보류·갱신 정책 | R01 | 미실행 |
| R05 | [ ] | 범위 기반 HARQ σ | R01 결과에 따라 | 조건부 |
| R06 | [ ] | SESTM 감성 | 기사 코퍼스 확보 | 보류 |
| R07 | [x] | 금리 커브 특징군(2년물·기울기·30년물), 5·20일만 | 없음(M02 코드 재사용) | 채택 없음(2026-09-12). FRED CMT 4구간 10열, 4조합 모두 보정 후 동률. raw 로는 삼성 두 지평 열위 |
| R08 | [~] | 미국 이벤트 일정 확장 → 이벤트일 구간·보류 정책 | 1·2단계는 없음, 3단계는 R01 | 1·2단계 완료(2026-09-12). 2단계 열두 조합 모두 동률 — 갭은 이벤트일에 1.2배지만 이벤트일 29~48일이라 구간이 0을 품는다. 장중은 오히려 작다. 정책 없음. 2b(일정표를 과거로) → 3단계 순서 |
| R09 | [ ] | 선행지수 2~3개월 전망(ARIMA·VAR), 다음 분기 이익 모형에 반영 | 0단계(판본 보관)가 먼저 | 미실행. 판본이 없어 지금 백테스트하면 개정을 미리 아는 셈이다 |

각 작업은 M-계획과 같은 완료 기록 규칙(코드·검증·결과·문서를 한 커밋, decision.md, 체크리스트·재개 지점 갱신)을 따른다. 통과한 후보는 M07 방식으로 원장에 `Candidate …` 행을 추가해 관찰한다. 잠금 평가는 후보당 1회다.

## 5. 재개 지점

- 현재 작업: 없음. 다음 실행은 R01(가장 뚜렷한 실패를 겨냥하고 새 자료가 필요 없다).
- R09 는 0단계(선행지수 판본 보관)를 먼저 켜는 것이 순서다. 5분이면 되고, 켜 두지 않으면 나중에도
  정직한 평가를 할 수 없다. 그 뒤 1단계(분산비 진단)에서 임의보행으로 나오면 거기서 멈춘다.
- 2026-09-12 실행: R07 채택 없음(`experiments/medium_horizon/R07/decision.md`), R08 1·2단계 완료
  (`experiments/medium_horizon/R08/decision.md`). R08 2단계는 정책 없음 — 갭이 이벤트일에 1.2배지만
  이벤트일이 29~48일뿐이라 열두 조합 모두 구간이 0을 품는다. 다음은 2b(일정표를 2024~2025년으로
  넓혀 표본 세 배)이고 3단계(구간·보류 정책)는 그 뒤, R01 다음이다.
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
- Campbell, J. Y. (1987). Stock returns and the term structure. Journal of Financial Economics 18(2), 373–399.
- Rapach, D. E., Strauss, J. K., & Zhou, G. (2010). Out-of-Sample Equity Premium Prediction: Combination Forecasts and Links to the Real Economy. Review of Financial Studies 23(2), 821–862.
- Savor, P., & Wilson, M. (2013). How Much Do Investors Care About Macroeconomic Risk? Evidence from Scheduled Economic Announcements. Journal of Financial and Quantitative Analysis 48(2), 343–375.
- Lucca, D. O., & Moench, E. (2015). The Pre-FOMC Announcement Drift. Journal of Finance 70(1), 329–371.
- Gu, S., Kelly, B., & Xiu, D. (2020). Empirical Asset Pricing via Machine Learning. Review of Financial Studies 33(5), 2223–2273. https://academic.oup.com/rfs/article/33/5/2223/5758276
