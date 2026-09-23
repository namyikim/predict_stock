# PER·PBR 기업가치 참고 범위

장기 전망의 기존 수익률 모델과 별도로 표시한다. 두 회사 모두 일일 장기 전망 실행 시 갱신된다.

## 자료와 계산

- Yahoo Finance/yfinance의 `trailingEps`(최근 12개월 EPS), `bookValue`(BPS), `lastFiscalQuarter`를 사용한다. DART 원문 검증 자료나 애널리스트 컨센서스가 아닌 외부 집계자료다.
- 현재 PER = 원종가 / EPS, PBR = 원종가 / BPS. 가격은 당일 장중 자료를 제외한 최근 일봉의 원종가다.
- 가격 시나리오 = EPS × 가정 PER 또는 BPS × 가정 PBR. EPS 대신 영업이익을 넣거나, 현재 PER × EPS로 현재가를 목표가처럼 재출력하지 않는다.
- `valuation_assumptions.json`의 PER 8/12/16배, PBR 1/1.5/2배는 민감도 비교용 예시다. 과거 평균·적정 배수·증권사 컨센서스가 아니다. 회사별 적정성은 검증되지 않았으며 두 회사에 같은 가정을 적용한다고 같은 가치 평가가 정당화되지 않는다.
- 표에서 배수, 자료 기준 분기말, 조회일, 시세일, 원자료 출처 및 주가 대비 차이를 표시한다. 상세 EPS 산정 기간과 주식수 기준은 원공시 추가 확인이 필요하다.

## 자료가 부적절할 때

- 원화 단위를 확인하지 못하거나 재무 분기말이 200일 이상 오래된 경우, 미래 기준일 또는 시세가 10일 이상 오래된 경우 계산 보류한다(정확한 허용 범위는 코드의 200일/10일 이하).
- EPS 또는 BPS가 0 이하·누락·비정상 값이면 해당 방법만 보류한다.
- 조회 실패 시 해당 영역에 사유만 표시하고 기존 장기 전망은 계속 생성한다. `--no-fetch`에서는 10일 이내의 로컬 보관본만 허용한다.

## 성능 비교와 기록

`longterm.json`의 `valuation`에 계산 결과를 저장하고 발행 시 `docs/<target>/valuation_history/<KST 일시>.json`에 당시 배수·자료·기존 모델 전망을 함께 누적한다. 재실행은 별도 기록으로 보존한다. 현재 재무자료를 과거 학습이나 평가 데이터로 소급 적용하지 않는다.

이 기능은 **현재 가치의 민감도 표**이고 도달 날짜를 정한 주가 예측이 아니다. 따라서 기존 3·6·12개월 예측의 MAE/적중률을 이 표의 성적으로 재사용하지 않는다. 자동 미래 채점은 아직 구현하지 않았다. 후속 평가를 도입하려면 관측 시점·평가 지평·배수 선택 규칙을 먼저 고정하고, 주식분할 등 자본변동 보정 및 당시 정보만 사용하는 평가 계약을 추가해야 한다.

## 검증

`python -m unittest discover -s tests -p 'test_valuation_reference.py' -v`

- 이익·순자산별 계산, 적자/누락, 단위·기준일 검증, 당일 장중 자료 제외, 조회 실패 격리, HTML 이스케이프 검증.

참고: [KRX 투자지표 산출 안내](https://kind.krx.co.kr/external/dst/guidebook/Investment_Indicators_KOR.pdf), [yfinance get_info](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_info.html).
