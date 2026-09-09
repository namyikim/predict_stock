# 기타 자동 보고서

## 보고서 목록

| 보고서 | 생성 도구 | 발행 위치 |
| --- | --- | --- |
| 인기 급상승 검색어 | `tools/build_trends_report.py` | `docs/trends/` |
| 금·은 예측 | `tools/build_metals_report.py` | `docs/metals/` |
| 중국 5개년 계획 | `tools/build_china_report.py` | `docs/china/` |
| 장기 관심도 | `tools/build_interest_report.py` | `docs/interest/` |

## 인기 급상승 검색어

Google Trends RSS가 제공하는 조회 시점의 최근 급상승 항목을 보여 줍니다. 하루 전체 순위가 아니며 검색량도 `100+`, `1000+`처럼 반올림된 근사치입니다.

외부 제목은 HTML 이스케이프하고 뉴스 주소는 HTTP(S)만 허용합니다. 항목이 하나도 없으면 기존 정상 보고서를 빈 페이지로 덮어쓰지 않습니다.

```bash
python tools/build_trends_report.py --out runs/trends
python tools/build_trends_report.py --out runs/trends --publish
```

## 금·은 예측

COMEX 금·은 선물(`GC=F`, `SI=F`)의 다음 거래일 방향, 1주일·1개월 범위, 사전 예측 원장과 사후 채점을 제공합니다.

한국 반도체주 노트북에 원자재를 억지로 넣지 않고, 워크포워드 모델 선택·현재가 유지 기준선·변동성 구간·원장 채점 같은 검증 장치만 공유합니다. 금·은은 거의 24시간 거래되어 한국 주식의 야간 갭 구조와 다릅니다. 방향 확률보다 변동성 구간을 중심으로 읽어야 합니다.

```bash
python tools/build_metals_report.py --out runs/metals --dump
python tools/build_metals_report.py --out runs/metals --publish
```

미국 선물 전자세션이 끝나기 전이면 당일 봉을 미완성으로 보고 제외합니다.

## 중국 5개년 계획과 주식 수익

8차부터 14차까지 중국 5개년 계획의 정책 업종 대표 종목 수익률을 상해종합과 비교하고, 15차 계획 후보 산업을 정리합니다.

종목은 과거 계획 업종에서 사후에 대표 상장사를 고른 것이므로 생존 편향이 있습니다. 15차 후보 역시 추천이 아니라 정책과 겹치는 상장사 목록입니다. A주는 위안, 홍콩주는 홍콩달러 기준이라 환율 효과를 포함하지 않습니다.

```bash
python tools/build_china_report.py --out runs/china --dump
python tools/build_china_report.py --out runs/china --publish
```

## 장기 관심도

위키미디어 Pageviews API의 한국어·영어 문서 월별 절대 조회수와 Google Trends 검색 지수를 나란히 보여 줍니다.

위키 조회수는 공식 API의 절대값이지만 검색량이 아니라 문서 조회수입니다. Google Trends 장기 시계열은 공식 API가 아니고 조회 묶음 안의 상대값 0~100이므로, 공통 앵커로 묶음 척도를 보정합니다. Trends가 막히면 해당 열만 비우고 위키 보고서는 계속 발행합니다.

증가율은 양 끝값이 아니라 전체 구간 로그-선형 추세로 계산합니다. 전체 시작 월을 덮지 못하는 문서는 증가율 순위에서 제외해 신규 문서의 낮은 초기값이 과장된 증가율을 만들지 않게 합니다.

```bash
python tools/build_interest_report.py --out runs/interest --publish
```

추적 대상과 정규 문서 제목은 `tools/build_interest_report.py`의 `TOPICS`를 수정합니다.

