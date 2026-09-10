# predict_stock

삼성전자와 SK하이닉스의 다음 거래일 방향·시초가·종가를 예측하고, 실제 결과와 계속 비교하는 연구용 프로젝트입니다. 주식 외에도 금·은, 중국 주식, 인기 검색어와 장기 관심도 보고서를 자동으로 발행합니다.

## 최신 보고서

전체 보고서: **[predict_stock GitHub Pages](https://namyikim.github.io/predict_stock/)**

| 보고서 | 바로가기 |
| --- | --- |
| 삼성전자 | [최신 보고서](https://namyikim.github.io/predict_stock/samsung/) |
| SK하이닉스 | [최신 보고서](https://namyikim.github.io/predict_stock/sk_hynix/) |
| 금·은 | [최신 보고서](https://namyikim.github.io/predict_stock/metals/) |
| 중국 주식 | [최신 보고서](https://namyikim.github.io/predict_stock/china/) |
| 인기 급상승 검색어 | [최신 보고서](https://namyikim.github.io/predict_stock/trends/) |
| 장기 관심도 | [최신 보고서](https://namyikim.github.io/predict_stock/interest/) |

## 무엇을 보여 주나요?

- 삼성전자·SK하이닉스의 **다음 거래일 방향**과 대상 날짜
- 장이 시작할 때의 **시초가예측**
- 1·5·20거래일 뒤 **종가예측**과 예상 범위
- 외국인·기관 수급, 주요 일정과 최근 공시
- 3·6·12개월 장기 전망과 분기 영업이익 추정
- 과거 백테스트가 아니라 실제로 미리 기록한 **예측 vs 실제** 성적
- 일반 사용자가 먼저 읽을 수 있는 **한눈에 보는 쉬운 요약**
- 흩어진 값을 한곳에 모은 **판단 재료 요약**(각 행에 출처 절 표시)

별도 AI API나 유료 토큰 없이 정해진 코드와 공개 데이터만으로 보고서를 만듭니다. 검증 근거가 부족하면 숫자를 억지로 내지 않고 `예측하기 어렵습니다` 또는 `신호 없음`으로 표시합니다.

## 먼저 알아야 할 한계

종가→종가 방향에서 관측된 예측력의 대부분은 **전일 종가에서 다음 날 시가까지의 야간 갭**에서 나왔습니다. 시가→종가 구간의 성능은 대체로 무작위에 가까웠습니다. 야간 갭은 장이 열릴 때 이미 가격에 반영되므로, 방향 적중률이 높아 보여도 그대로 거래 수익을 뜻하지 않습니다.

보고서는 갭과 장중 구간을 분리하고 거래비용을 반영해 보여 줍니다. 자세한 해석 방법은 [주식 보고서 안내](guides/stock-reports.md)와 [검증 방법](guides/validation.md)을 참고하세요.

**매수·매도 의견은 내지 않습니다.** 예측력이 있는 구간은 09:00 시가까지인데 의견은 그 이후에 실행되므로, 시가에 이미 반영된 정보로 시가 이후를 거래하라고 말하는 셈이 됩니다. 대신 **판단 재료 요약** 표에 사이클 국면·수급·분기 이익 추정·다가오는 일정 같은 값을 모아 두고, 그 아래에 **"이 보고서가 답하지 못하는 것"**(오늘 사야 하는지, 며칠 뒤 주가, 장중 뉴스의 영향)을 함께 적습니다. 판단은 사람이 합니다.

> 이 프로젝트는 연구·교육용입니다. 투자 자문이 아니며 수익을 보장하지 않습니다.

## 빠르게 실행하기

프로젝트의 중심은 Google Colab에서 위에서부터 실행할 수 있는 [`samsung_direction_model_colab.ipynb`](samsung_direction_model_colab.ipynb)입니다.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/namyikim/predict_stock/blob/main/samsung_direction_model_colab.ipynb)

로컬에서는 다음과 같이 실행합니다.

```bash
python -m venv .venv
# 가상환경을 활성화한 뒤
python -m pip install -r requirements.txt
python tools/run_notebook.py --storage ./outputs
```

Colab·로컬 실행 결과는 기본적으로 해당 실행 환경에만 저장되며 GitHub 보고서를 덮어쓰지 않습니다. 자세한 설정과 산출물은 [실행 안내](guides/running.md)를 참고하세요.

## 자동 발행

- 주식 보고서: 평일 06:22 KST 본 실행, 07:25 백업 및 3시간 간격 복구 실행
- 시가 채점: 평일 09:37 KST
- 종가 채점: 평일 16:10 KST
- 장기 전망: 매달 7일 07:00 KST
- 감시 작업: 평일 09:20 KST

정확한 cron, 중복 방지, 실패 감지와 필요한 Secrets는 [자동 실행과 발행](guides/automation.md)에 정리되어 있습니다.

## 상세 문서

| 문서 | 내용 |
| --- | --- |
| [주식 보고서 안내](guides/stock-reports.md) | 각 예측과 보고서 절을 읽는 방법 |
| [데이터 출처](guides/data-sources.md) | 시세·수출·경기·수급·공시 자료와 공개 시점 |
| [실행 안내](guides/running.md) | Colab·로컬 실행, 설정과 생성 파일 |
| [자동 실행과 발행](guides/automation.md) | Actions 일정, 재시도, 감시와 Secrets |
| [검증 방법](guides/validation.md) | 워크포워드, 누수 방지, 기준선과 성능 판정 |
| [모델 성능 개선 계획](guides/model-improvement-plan.md) | 단계별 실험, 완료 체크리스트와 재개 방법 |
| [5·20거래일 예측 개선 계획](guides/medium-horizon-improvement-plan.md) | 주간·월간 가격·방향·구간 예측의 구현, 검증과 재개 체크리스트 |
| [논문 기반 개선 후보와 실험 계획](guides/research-candidates-plan.md) | 측정된 실패(구간 붕괴·약한 신호·갭 편중·판정 불안정)에 맞춘 논문 10편과 R01~R08 실험 계약(금리 커브·이벤트 일정 포함) |
| [기타 보고서](guides/other-reports.md) | 금·은, 중국, 검색어와 관심도 보고서 |
| [개발자 안내](guides/development.md) | 코드 구조, 테스트와 노트북 동기화 |
| [조회수 카운터](counter/README.md) | Cloudflare Worker와 통계 대시보드 운영 |

## 핵심 파일

```text
predict_stock/
├── samsung_direction_model_colab.ipynb  # 주식 분석·학습·예측 노트북
├── forecast_utils.py                    # 학습·검증·원장 공통 함수
├── report_html.py                       # 주식 보고서 HTML 조각
├── data_sources/                        # 공식·보조 자료원 모듈
├── tools/                               # 실행기와 보고서 생성 도구
├── tests/                               # 단위·구조·전체 실행 테스트
├── guides/                              # 기능별 설명 문서
├── docs/                                # GitHub Pages 발행 결과
└── forecast_history/                    # 실제 사전 예측과 채점 원장
```
