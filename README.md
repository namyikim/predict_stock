# predict_stock

삼성전자(`005930.KS`)의 **다음 거래일 주가 방향**과 **1주일·1개월 뒤 종가**를 예측하고, 여러 머신러닝·딥러닝 모델을 동일한 시계열 검증 조건에서 비교하는 연구용 프로젝트입니다.

프로젝트의 중심은 Google Colab에서 위에서부터 순서대로 실행할 수 있는 [`samsung_direction_model_colab.ipynb`](samsung_direction_model_colab.ipynb) 노트북입니다. Yahoo Finance에서 데이터를 내려받고, 특징 생성부터 워크포워드 검증, 최신 예측, 결과 저장까지 한 번에 수행합니다.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/namyikim/predict_stock/blob/main/samsung_direction_model_colab.ipynb)

## 주요 기능

- 다음 거래일의 삼성전자 수익률을 `하락`, `보합`, `상승`으로 분류합니다.
  - 하락: 수익률 < -band, 보합: -band ≤ 수익률 ≤ +band, 상승: 수익률 > +band
  - `band`는 기본값으로 **최근 20일 일간 변동성 × 0.3**(변동성 스케일 밴드)이며, 고정 ±0.5%로 바꿀 수 있습니다. 변동성 국면과 무관하게 클래스 비율이 유지되어 모델이 변동성이 아니라 방향을 학습합니다.
  - 타깃은 종가→종가(07:00 예측) 또는 시가→종가(09:00 시가 확정 후 예측) 중 선택합니다.
- 5거래일(약 1주일), 20거래일(약 1개월) 뒤의 예상 수익률과 종가를 계산합니다.
- 과거 데이터로 학습하고 이후 구간을 예측하는 워크포워드 방식으로 모델을 평가합니다.
- 방향 예측 확률, 중기 가격 예측, 검증 지표, 학습 모델과 재현 설정을 파일로 저장합니다.
- 별도 API 키 없이 Colab에서 전체 실험을 재현할 수 있습니다.

## 비교 모델

### 다음 거래일 방향 예측

1. 항상 보합을 예측하는 기준선
2. 다항 로지스틱 회귀
3. LightGBM 분류기
4. 30거래일 특징 시퀀스를 사용하는 소형 Transformer Encoder
5. 금융 OHLCV 시계열 파운데이션 모델 [Kronos-small](https://github.com/shiyu-coder/Kronos)의 zero-shot 예측
6. 전체 워크포워드 OOF log loss를 기준으로 가중치를 정한 성과 가중 앙상블
7. 워크포워드 OOF 확률(로그)을 다항 로지스틱 메타모델로 결합한 **스태킹 앙상블** — 메타모델도 이전 폴드로만 학습해 다음 폴드를 예측하며, 비교용 단순 평균 앙상블과 함께 평가합니다.

모델은 `accuracy`, `balanced_accuracy`, `macro_f1`, `log_loss`, `Brier score`로 비교합니다. Kronos는 실행 시간이 길어 일부 최근 날짜만 평가하므로, 모든 모델이 예측한 날짜의 교집합 성능과 각 모델의 전체 평가 구간 성능을 함께 제공합니다.

### 1주일·1개월 가격 예측

Ridge와 LightGBM 회귀 모델이 각 예측 기간의 미래 수익률을 직접 학습합니다. `TimeSeriesSplit` 검증 MAE가 낮은 모델에 더 큰 가중치를 주어 예상 수익률과 종가를 계산합니다. 검증 MAE는 항상 **"수익률 0% 예측" 기준선**과 함께 보고되며, 기준선보다 나쁘면 경고합니다.

## 사용 데이터

기본 데이터 수집 기간은 2015년 1월 1일부터 실행 시점까지입니다. Yahoo Finance를 통해 다음 자산을 사용합니다.

| 구분 | 데이터 |
| --- | --- |
| 한국 시장 | 삼성전자, KOSPI, KOSPI 200, SK하이닉스 |
| 해외 삼성 가격 | 런던 삼성전자 GDR(SMSN.IL) — 한국 장 마감 후 삼성전자 가격의 대리변수(야간 신호) |
| 미국 주식·지수 | 필라델피아 반도체 지수, Nasdaq, S&P 500, Micron, Nvidia, TSMC ADR, EWY |
| 거시·시장 변수 | 원/달러 환율, 달러 지수, VIX, 미국 10년물 금리, WTI |
| 달력 변수 | 요일, 월말 여부 |

삼성전자와 한국 시장의 과거 수익률·추세·변동성·거래량, 그리고 예측 시점에 확인할 수 있는 해외 시장·거시 변수로 특징을 구성합니다. 다운로드에 일시적으로 실패한 보조 자산은 제외하고 계속 진행하지만, 삼성전자 데이터가 없으면 실행을 중단합니다.

## 데이터 누수 방지와 검증 방식

- 예측 대상 날짜를 `d`라고 할 때 삼성전자와 한국 시장 특징은 `d-1` 거래일까지의 정보만 사용합니다.
- 미국 시장 종가는 한국 시간으로 다음 날 새벽에 확정된다고 보고, 가용 날짜를 하루 뒤로 이동해 결합합니다.
- 데이터를 무작위로 섞지 않고 최근 5년을 학습한 뒤 다음 6개월을 평가합니다.
- Transformer의 scaler와 모델은 각 폴드의 과거 데이터만 사용해 다시 학습합니다.
- 중기 가격 예측은 학습 라벨과 검증 구간이 겹치지 않도록 예측 기간에 맞춘 `gap`을 둡니다.

이 방식은 명백한 미래 정보 유입을 막기 위한 장치이지만, 실거래 환경의 거래비용, 체결 가능성, 데이터 발표 지연까지 완전히 모사하지는 않습니다.

## Colab에서 실행하기

1. 위의 **Open in Colab** 배지를 누르거나 노트북 파일을 Google Colab에서 엽니다.
2. `런타임 → 런타임 유형 변경`에서 **T4 GPU** 이상을 선택합니다.
3. `런타임 → 모두 실행`으로 셀을 위에서부터 실행합니다.
4. 마지막 결과 저장 셀까지 완료되면 `/content/samsung_direction_outputs.zip`이 생성됩니다.
5. zip 파일을 바로 내려받으려면 마지막 셀의 `files.download(zip_path)` 관련 두 줄의 주석을 해제합니다.

첫 번째 실행에서는 Python 패키지를 설치하고 Kronos 저장소와 사전학습 가중치를 내려받기 때문에 시간이 더 걸릴 수 있습니다. CPU에서도 실행할 수 있지만 Transformer와 Kronos 평가는 GPU 사용을 권장합니다.

### 주요 설정

노트북의 `1. 실험 설정` 셀에서 다음 값을 조정할 수 있습니다.

| 설정 | 기본값 | 설명 |
| --- | --- | --- |
| `START_DATE` | `2015-01-01` | 데이터 수집 시작일 |
| `TARGET_MODE` | `close_to_close` | `close_to_close`(종가→종가) 또는 `open_to_close`(시가→종가) |
| `BAND_MODE` | `vol_scaled` | `vol_scaled`(변동성 × `VOL_BAND_MULT`) 또는 `fixed`(±`NEUTRAL_BAND`) |
| `VOL_BAND_MULT` | `0.3` | 변동성 스케일 밴드 배수 |
| `NEUTRAL_BAND` | `0.005` | `fixed` 모드의 보합 범위(±0.5%) |
| `LIVE_OPEN_PRICE` | `None` | `open_to_close` 모드에서 예측일 09:00 시가 |
| `QUICK_MODE` | `False` | 전체 워크포워드 폴드와 확장된 학습·평가 횟수로 실행 |
| `RUN_KRONOS` | `True` | Kronos-small 평가와 최신 예측 실행 여부 |
| `PREDICTION_DATE_OVERRIDE` | `None` | 한국 휴일 때문에 자동 계산된 예측일을 수정할 때 사용 |

빠르게 동작을 확인하려면 `QUICK_MODE=True`로 변경합니다. 이 경우 최근 3개 워크포워드 폴드만 사용하고 Transformer 학습 epoch, Kronos 평가 날짜와 Monte Carlo 표본 수를 줄입니다.

## 생성 결과

결과는 `/content/samsung_direction_outputs/`에 저장된 뒤 하나의 zip 파일로 묶입니다.

| 파일 | 내용 |
| --- | --- |
| `backtest_predictions.csv` | 날짜·모델별 과거 방향 예측 확률과 실제값 |
| `metrics_native_window.csv` | 모델별 전체 평가 가능 구간 성능 |
| `metrics_common_dates.csv` | 모든 모델이 예측한 공통 날짜의 성능 |
| `latest_forecast.csv` | 다음 거래일 방향과 클래스별 확률 |
| `multi_horizon_price_forecast.csv` | 1주일·1개월 예상 수익률과 종가 |
| `feature_list.csv` | 최종 학습에 사용한 특징 목록 |
| `config.json` | 실행 설정, 예측 기간, 검증 MAE와 앙상블 가중치 |
| `stacking_meta_model.joblib` | 스태킹 메타모델 |
| `*.joblib`, `transformer_model.pt` | 학습한 회귀·분류 모델과 Transformer 가중치 |

## 저장소 구조

```text
predict_stock/
├── samsung_direction_model_colab.ipynb  # 전체 분석·학습·예측 노트북
├── tests/
│   └── test_multihorizon_notebook.py    # 노트북 구조·누수 방지 규칙·문법 검증
└── README.md
```

## 테스트

저장소 루트에서 다음 명령을 실행합니다.

```bash
python -m unittest discover -s tests -v
```

현재 테스트는 모든 코드 셀의 Python 문법, 타깃·밴드 설정, 변동성 밴드가 전일 정보만 쓰는지, 스태킹이 이전 폴드로만 학습하는지, 앙상블 가중치가 전체 OOF 구간을 쓰는지, 중기 예측의 시계열 분할과 0% 기준선, 결과 저장 메타데이터를 확인합니다. 실제 모델 성능은 최신 데이터와 실행 환경에 따라 달라지므로 노트북의 워크포워드 결과를 직접 확인해야 합니다.

## 주의사항

- 이 프로젝트는 연구 및 교육 목적이며 투자 조언이나 수익 보장을 제공하지 않습니다.
- Yahoo Finance는 재현 편의를 위한 데이터 소스입니다. 실제 운영 환경에서는 KRX, 한국은행 ECOS, CME 등 원천 데이터 사용을 고려하세요.
- 다음 거래일과 중기 예측의 `target_date`는 주말만 제외해 계산하므로 한국 공휴일이나 임시 휴장일과 다를 수 있습니다. 필요하면 `PREDICTION_DATE_OVERRIDE`를 지정하세요.
- 빠른 모드의 제한된 평가 결과만으로 모델의 우위를 판단하지 마세요. 여러 기간의 반복 성능과 확률 보정 지표를 함께 확인해야 합니다.
