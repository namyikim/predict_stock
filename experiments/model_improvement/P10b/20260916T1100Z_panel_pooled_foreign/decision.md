# P10b 해외 자산(종목 공통) 특징을 넣은 pooled 패널 vs 대표 모델

**결론: 채택하지 않는다.** P10 패널(9종목)에 대표 모델의 종목 공통 입력(해외 자산·KOSPI·달력 43개)을
붙여 pooled로 학습해도 대표 모델(No macro ensemble, P00)을 **같은 날짜·같은 정답**에서 이기지 못한다.
samsung은 동률(log_loss CI가 0을 포함, 상한 +0.0196), sk_hynix는 **유의하게 열위**(+0.0097 [+0.0019, +0.0178]).
채택 규칙(두 종목 log_loss CI 상한 < 0, 또는 한 종목 명확 우위 + 다른 종목 동률)에 어느 쪽도 해당하지 않는다.

P10의 결론("패널 입력만으로는 사전확률과 동률, 값이 나오려면 해외 자산 특징을 넣은 pooled를 봐야 한다")의
후속이다. 해외 자산을 넣자 pooled가 사전확률은 크게 이기지만(log_loss −0.058/−0.068, 대표 모델과 같은 크기),
그 이득은 **해외 자산 특징 자체**에서 오고 **공동 학습(9종목 pooling)은 더하는 것이 없다** —
pooled − 단독(같은 입력)은 samsung 동률, sk_hynix 유의 열위.

## 설정

- 기준선: `experiments/model_improvement/P00/20260910T0100Z_baseline_full`과 같은 스냅샷·같은 12개 외부 폴드.
  대표 모델은 재학습하지 않고 이 실행에서 노트북이 같은 폴드로 낸 OOF 확률을 그대로 썼다(코드 커밋이 P00
  이후 바뀌어 특징 80개, samsung 지표가 P00 기록과 소수점 셋째 자리에서 다르다. 비교는 같은 실행 안에서 했다).
- 패널: P10과 같은 9종목(제외 3: 2015년 이후 상장). 행 samsung 24,775 / sk_hynix 24,789(결측 제거 후).
  보간 위반 0. 생존 편향은 P10과 같다(종목 수 증가를 독립 표본 증가로 보지 않는다).
- 입력 62개 = 패널 10(수익률·변동성·거래대금 비율) + 종목 one-hot 9 + **종목 공통 43**(대표 모델의 시세만
  입력에서 `sam_`·`peer_`·GDR 열을 뺀 것: kospi_·sox_·nasdaq_·sp500_·micron_·nvidia_·tsmc_adr_·korea_etf_·
  usdkrw_·dxy_·vix_·us10y_·wti_·usdjpy_·vxn_·krwjpy_·cal_). 공통 특징은 노트북 특징 프레임에서 한 번 만들어
  날짜로 붙였다(`experiments/model_improvement/panel_foreign.py`).
- 정렬·누수 규칙(테스트 `tests/test_panel_foreign.py` 11개로 고정): 패널 행 d(종가 d까지, 라벨 d→다음 봉)에는
  노트북 행 e = "d 뒤 첫 예측일"의 공통 특징만 붙인다(같은 날짜 금지). 라벨을 완성하는 봉이 e보다 앞서는 행은
  버린다(samsung 33행 / sk_hynix 19행). 폴드 학습 행은 라벨이 시험 시작 전에 완성된 행뿐이고 폴드는 날짜
  단위다. 대상 종목 행의 패널 라벨과 노트북 공식 라벨 일치율 1.000(두 종목).
- 모델: 대표 모델과 같은 결합(Logistic + LightGBM 확률 평균), 같은 후보(C∈{.003,.01,.03}×class_weight,
  n_estimators∈{60,120}×class_weight)·같은 온도 후보·같은 선택 기준(내부 log loss). 내부 분할만 126거래일
  **날짜 블록**으로 바꿔 같은 날짜의 종목 행이 갈라지지 않게 했다(단독 학습에서는 노트북과 같은 분할).
- 후보는 둘뿐: `panel pooled + foreign`(9종목), `panel single + foreign`(대상 종목만, 같은 입력).
  학습 시간 pooled 95~97초 / 단독 34~35초(12폴드 합, 대표 모델 재사용 0초).

## 성능 (세 모델 모두 같은 날짜·같은 공식 라벨, samsung 1,362일 / sk_hynix 1,357일)

| 종목 | 모델 | accuracy | balanced_accuracy | log_loss | brier |
| --- | --- | ---: | ---: | ---: | ---: |
| samsung | Always flat(사전확률) | 0.2819 | 0.3333 | 1.0965 | 0.6655 |
| samsung | **No macro ensemble(대표)** | 0.4596 | 0.4376 | 1.0287 | 0.6186 |
| samsung | panel pooled + foreign | 0.4545 | 0.4277 | 1.0384 | 0.6244 |
| samsung | panel single + foreign | 0.4552 | 0.4334 | 1.0362 | 0.6238 |
| sk_hynix | Always flat(사전확률) | 0.2734 | 0.3333 | 1.0923 | 0.6627 |
| sk_hynix | **No macro ensemble(대표)** | 0.5011 | 0.4671 | 1.0149 | 0.6087 |
| sk_hynix | panel pooled + foreign | 0.4923 | 0.4621 | 1.0246 | 0.6155 |
| sk_hynix | panel single + foreign | 0.4923 | 0.4589 | 1.0154 | 0.6094 |

## 쌍체 비교 (95% 월 블록 CI, log_loss는 음수가 개선)

| 종목 | 비교 | 지표 | delta | 판정 |
| --- | --- | --- | --- | --- |
| samsung | pooled+해외 − 대표 | log_loss | +0.00963 [−0.00011, +0.01956] | 동률(CI가 0 포함) |
| samsung | pooled+해외 − 대표 | balanced_accuracy | −0.00994 [−0.03318, +0.01526] | 동률(CI가 0 포함) |
| samsung | 단독+해외 − 대표 | log_loss | +0.00741 [+0.00167, +0.01368] | 후보가 유의하게 열위 |
| samsung | 단독+해외 − 대표 | balanced_accuracy | −0.00421 [−0.02315, +0.01508] | 동률(CI가 0 포함) |
| samsung | pooled+해외 − 단독+해외(같은 입력) | log_loss | +0.00222 [−0.00717, +0.01117] | 동률(CI가 0 포함) |
| samsung | pooled+해외 − 단독+해외(같은 입력) | balanced_accuracy | −0.00573 [−0.02870, +0.01825] | 동률(CI가 0 포함) |
| samsung | pooled+해외 − 사전확률 | log_loss | −0.05809 [−0.07344, −0.04361] | 후보가 유의하게 우위 |
| samsung | pooled+해외 − 사전확률 | balanced_accuracy | +0.09437 [+0.06877, +0.11849] | 후보가 유의하게 우위 |
| sk_hynix | pooled+해외 − 대표 | log_loss | +0.00966 [+0.00187, +0.01783] | 후보가 유의하게 열위 |
| sk_hynix | pooled+해외 − 대표 | balanced_accuracy | −0.00503 [−0.02744, +0.01798] | 동률(CI가 0 포함) |
| sk_hynix | 단독+해외 − 대표 | log_loss | +0.00052 [−0.00514, +0.00618] | 동률(CI가 0 포함) |
| sk_hynix | 단독+해외 − 대표 | balanced_accuracy | −0.00819 [−0.02131, +0.00594] | 동률(CI가 0 포함) |
| sk_hynix | pooled+해외 − 단독+해외(같은 입력) | log_loss | +0.00914 [+0.00014, +0.01810] | 후보가 유의하게 열위 |
| sk_hynix | pooled+해외 − 단독+해외(같은 입력) | balanced_accuracy | +0.00317 [−0.01861, +0.02489] | 동률(CI가 0 포함) |
| sk_hynix | pooled+해외 − 사전확률 | log_loss | −0.06770 [−0.07945, −0.05611] | 후보가 유의하게 우위 |
| sk_hynix | pooled+해외 − 사전확률 | balanced_accuracy | +0.12875 [+0.10753, +0.15056] | 후보가 유의하게 우위 |

accuracy는 전 비교에서 동률(comparisons.csv).

## 보류 진단 — 최대 확률 ≥ 0.50 (보고서의 방향 발행 기준 `forecast_utils.DIRECTION_ISSUE_MIN_PROB`)

| 종목 | 모델 | 발행일 | coverage | 발행일 accuracy | 전체 accuracy |
| --- | --- | ---: | ---: | ---: | ---: |
| samsung | No macro ensemble(대표) | 309 / 1,362 | 22.7% | 0.683 | 0.460 |
| samsung | panel pooled + foreign | 239 / 1,362 | 17.5% | 0.736 | 0.454 |
| samsung | panel single + foreign | 293 / 1,362 | 21.5% | 0.645 | 0.455 |
| sk_hynix | No macro ensemble(대표) | 372 / 1,357 | 27.4% | 0.642 | 0.501 |
| sk_hynix | panel pooled + foreign | 210 / 1,357 | 15.5% | 0.724 | 0.492 |
| sk_hynix | panel single + foreign | 384 / 1,357 | 28.3% | 0.628 | 0.492 |

임계치 0.6·0.7 행은 `selective.csv`. pooled는 발행일이 대표 모델보다 적고(coverage −5~−12%p) 그 날의
적중률은 높다. 이는 pooled의 확률이 더 보수적(초기 폴드 로지스틱 온도 2.0)이라 문턱을 넘는 날이 줄어든
결과이며, **발행 집합이 달라 쌍체 비교가 성립하지 않는다.** 전체 확률 품질(log_loss)이 동률·열위인 모델의
발행일 적중률만 보고 채택하면 P08의 "보정으로 정확도가 올랐다"와 같은 오독이 된다. 채택 근거로 쓰지 않는다.

## 읽는 법

- **해외 자산 특징이 예측력의 전부다.** 같은 패널이 P10에서는 사전확률과 동률이었고(log_loss +0.0003/+0.0077),
  공통 특징을 넣자 −0.058/−0.068로 대표 모델과 같은 크기의 이득이 생겼다. P09(갭 AUC≈0.8)와 일관된다.
- **9종목 공동 학습은 그 위에 아무것도 더하지 않는다.** pooled − 단독(같은 입력): samsung 동률, sk_hynix
  유의 열위. 다른 8종목의 행은 해외 신호와 각 종목 반응의 관계를 흐리는 쪽으로 작용했다(로지스틱이 매 폴드
  가장 강한 규제 C=0.003을 골랐고 온도가 1.5~2.0으로 커졌다 — 표본이 9배인데 확신은 줄었다).
- **단독+해외가 대표 모델과 동률(sk_hynix)·열위(samsung)인 것은 입력 차이다.** 대표 모델에는 종목 고유
  입력(sam_ 기술 지표·peer_·GDR 야간 신호)이 있고 후보에는 그 자리에 패널 10개 특징만 있다. 그 차이가 samsung에서
  log_loss +0.007로 유의하다(GDR 야간 신호는 삼성전자만 있다).
- 대표 모델의 확률 품질(1.029/1.015)은 이 실행에서 다시 확인됐다.

## P11~P14에 미치는 영향

P10의 보류 사유가 그대로다. 해외 자산을 넣어도 pooling이 단독을 이기지 못하므로, 패널 위에서 더 무거운
공동 학습 모델(MASTER·DoubleAdapt·TRA)을 올릴 근거가 생기지 않았다. 보류 유지.

## 채택

없음. 패널 공통 특징 모듈(`panel_foreign.py`)과 러너 `P10b`, 테스트는 기록으로 남긴다. 후속으로 볼 값이 있다면
"공동 학습"이 아니라 **대표 모델의 단독 입력에 다른 종목의 전일 수익률을 특징으로 더하는 것**(횡단면 정보를
입력으로만 쓰는 방식)이나, 이미 등록된 P15 후보(expanding 창) 관찰이다.
