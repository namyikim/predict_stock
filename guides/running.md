# 실행 안내

## Google Colab

[`samsung_direction_model_colab.ipynb`](../samsung_direction_model_colab.ipynb)을 Colab에서 열고 위에서부터 전체 실행합니다.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/namyikim/predict_stock/blob/main/samsung_direction_model_colab.ipynb)

기본 대상은 삼성전자와 SK하이닉스입니다. 첫 번째 설정 셀의 `RUN_TARGETS` 또는 환경변수 `PREDICT_STOCK_TARGETS`로 선택합니다.

```text
samsung,sk_hynix
samsung
sk_hynix
```

Colab·로컬 실행은 기본적으로 GitHub에 발행하지 않습니다. 결과는 현재 런타임에만 남으므로 종료 전에 `latest_outputs.zip`을 내려받으세요. 기본 폴더는 종목별로 `/content/samsung_outputs/`와 `/content/sk_hynix_outputs/`입니다.

## API 키와 CSV

키는 노트북 코드나 채팅에 직접 쓰지 말고 Colab의 **보안 비밀** 또는 로컬 환경변수로 등록합니다.

| 이름 | 용도 | 없을 때 |
| --- | --- | --- |
| `KOSIS_API_KEY` | 경기선행지수·반도체 수출 | 로컬·Colab은 설정에 따라 시세만 사용하거나 중단 |
| `ECOS_API_KEY` | 뉴스심리·금리차 | 해당 선택 지표 제외 |
| `DART_API_KEY` | 최근 공시·실적 이력 | 해당 공시 기능 제외 |
| `DATA_GO_KR_KEY` | 관세청 품목별 수출 | KOSIS·보관본 사용 |
| `KRX_ID`, `KRX_PW` | 투자자별 수급 | 네이버·보관본으로 대체 |

저장 폴더의 `macro_inputs/`에 CSV를 두면 API 대신 사용할 수 있습니다. 지원 파일과 시점 규칙은 [데이터 출처](data-sources.md)를 참고하세요.

## 로컬 실행

```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python tools/run_notebook.py --storage ./outputs
```

유용한 옵션:

```bash
python tools/run_notebook.py --storage ./outputs --quick
python tools/run_notebook.py --storage ./outputs --use-cache
python tools/run_notebook.py --storage ./outputs --no-macro
```

- `--quick`: 최근 3개 폴드와 작은 부트스트랩으로 빠르게 확인
- `--use-cache`: 고정 시세 스냅샷 재현용; 최신 일일 예측에는 사용하지 않음
- `--no-macro`: 월별 지표 없이 시세 모델만 확인

## 주요 설정

노트북의 실험 설정 셀에서 조정합니다.

| 설정 | 기본값 | 설명 |
| --- | --- | --- |
| `START_DATE` | `2015-01-01` | 시세 수집 시작일 |
| `TARGET_MODE` | `close_to_close` | 종가→종가 또는 시가→종가 방향 |
| `BAND_MODE` | `vol_scaled` | 변동성 비례 또는 고정 보합 범위 |
| `VOL_BAND_MULT` | `0.3` | 변동성 비례 밴드 배수 |
| `NEUTRAL_BAND` | `0.005` | 고정 모드의 보합 범위 |
| `ENSEMBLE_MODELS` | Logistic·LightGBM | 단순 평균 구성 모델 |
| `RUN_TRANSFORMER` | `False` | Transformer 비교 실험 실행 |
| `COST_BP` | `20.0` | 왕복 거래비용(bp) |
| `BOOTSTRAP_B` | `2000` | 월 블록 부트스트랩 횟수 |
| `USE_DATA_CACHE` | `False` | 고정 시세 스냅샷 사용 |
| `QUICK_MODE` | `False` | 빠른 검증 모드 |
| `PREDICTION_DATE_OVERRIDE` | `None` | 거래일 자동 계산을 수동 보정 |

환경변수로 바꿀 수 있는 값은 첫 설정 셀의 설명을 기준으로 합니다.

## 생성 파일

각 실행은 `runs/<run_id>/`에 산출물을 보관하고 저장 폴더에 누적 원장을 둡니다.

| 파일 | 내용 |
| --- | --- |
| `report.html` | 한 화면 종합 보고서 |
| `latest_outputs.zip` | 최신 결과와 원장 사본 |
| `backtest_predictions.csv` | 날짜·모델별 과거 방향 확률과 실제값 |
| `metrics_native_window.csv` | 성능·신뢰구간·갭/세션 분해 |
| `latest_forecast.csv` | 다음 거래일 방향과 확률 |
| `forecast_log.csv` | 변경하지 않는 사전 예측 원장 |
| `daily_forecast_comparison.csv` | 일별 예측과 실제값 비교 |
| `multi_horizon_price_forecast.csv` | 1·5·20거래일 종가예측과 구간 |
| `feature_list.csv` | 사용한 특징 목록 |
| `config.json` | 설정, 버전, 데이터·코드 식별 정보 |

## 발행 스위치

| `PREDICT_STOCK_PUBLISH` | 동작 |
| --- | --- |
| 미설정 | GitHub Actions에서만 발행 |
| `true` | 현재 환경에서도 발행 |
| `false` | 어디서든 발행하지 않음 |

공식 원장의 계보가 섞이지 않도록 평소에는 Actions만 발행 주체로 사용합니다. Colab에서 일시적으로 발행해야 하더라도 GitHub 토큰을 파일에 저장하지 마세요.

