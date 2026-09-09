# 개발자 안내

## 코드 구조

| 경로 | 역할 |
| --- | --- |
| `samsung_direction_model_colab.ipynb` | 주식 데이터 수집·학습·예측·보고서 조립 |
| `forecast_utils.py` | 워크포워드, 예측 기록·채점, 가격 모델 공통 함수 |
| `report_html.py` | 숫자·확률·구간·수급·공시·원장 HTML 조각 |
| `macro_utils.py` | `data_sources/` 공개 함수의 호환 facade |
| `data_sources/` | 자료원별 수집·정규화·시점 처리 |
| `tools/run_notebook.py` | Jupyter 없이 노트북 셀 순차 실행 |
| `tools/build_*.py` | 장기·영업이익·기타 보고서 생성 |
| `tests/` | 단위·구조·누수·통합·전체 실행 테스트 |
| `.github/workflows/` | 자동 실행·검증·감시 |

## 자료원 패키지

| 모듈 | 역할 |
| --- | --- |
| `data_sources/_common.py` | HTTP 헤더, 정규화, 보관본 노후 판정 |
| `data_sources/kosis.py` | 경기선행지수·반도체 수출 |
| `data_sources/ecos.py` | 뉴스심리·장단기 금리차 |
| `data_sources/oecd.py` | OECD G20 CLI |
| `data_sources/exports.py` | 관세청 품목별 수출과 조업일수 |
| `data_sources/flows.py` | 외국인·기관 수급 |
| `data_sources/dart.py` | 공시·실적 이력 |
| `data_sources/us_calendar.py` | 미국 주요 발표 일정·TSMC 월매출 |

각 자료원 모듈은 `_common` 외의 다른 자료원 모듈에 의존하지 않습니다. 테스트에서 함수를 교체할 때는 facade가 아니라 함수가 정의된 모듈을 대상으로 합니다.

## 노트북 헬퍼 동기화

노트북은 파일 하나만 Colab으로 받아도 실행돼야 하므로 공통 Python 모듈의 소스를 태그가 붙은 셀에 포함합니다. 헬퍼를 수정한 뒤 반드시 동기화합니다.

```bash
python tools/sync_notebook_helpers.py
```

동기화 대상은 `forecast_utils.py`, `macro_utils.py`가 내보내는 자료원 모듈, `report_html.py`입니다. 테스트가 노트북 셀과 원본 소스가 같은지 확인합니다.

## 테스트

전체 테스트:

```bash
python -m unittest discover -s tests -v
```

노트북 전체 실행이 오래 걸릴 때의 빠른 검사:

```bash
PREDICT_STOCK_SKIP_SMOKE=1 python -m unittest discover -s tests -v
python -m unittest tests.test_notebook_smoke -v
```

주요 테스트 층:

- 구조 검사: 모든 코드 셀 문법, 설정, 헬퍼 동기화, 워크플로 조건
- 합성 데이터 동작 검사: 시점 정렬, 누수, 폴드 경계, 결측 처리
- 원장 검사: 최초 사전 예측 보존, 실제값 채점, 설정별 집계
- 보고서 검사: HTML 이스케이프, 절 순서, 날짜·요일과 쉬운 요약
- 전체 실행 검사: 외부 API를 가짜 데이터로 바꾸고 모든 노트북 셀 실행

`git diff --check`도 함께 실행해 공백 오류를 확인합니다.

## 보고서 코드

자유변수 없이 분리할 수 있는 렌더링은 `report_html.py`에 둡니다. 노트북에는 계산된 전역값을 명시적 인자로 넘기는 얇은 어댑터만 남깁니다. 보고서 전체 조립은 아직 여러 계산 결과와 연결되어 있어 노트북 안에 있습니다.

각 보고서 제목 아래에는 생성 시각과 코드 커밋을 표시합니다. Actions는 `GITHUB_SHA`, 로컬·Colab은 Git 커밋 조회를 사용하며 커밋 정보를 찾지 못해도 보고서 생성은 계속합니다.

## 결과와 소스 변경 구분

- 소스: 노트북, Python 모듈, `tools/`, 테스트, 워크플로, 문서
- 생성 결과: `docs/`, `forecast_history/`, `macro_history/`, 일부 `experiments/`

생성 결과 커밋이 다시 보고서 워크플로를 호출하지 않도록 Actions의 push 경로를 제한합니다. 소스 변경은 테스트를 통과한 뒤 보고서만 다시 만들며 기존 공식 예측을 바꾸지 않습니다.

## 기여 시 확인사항

1. 미래 정보가 특징에 들어가지 않는가?
2. 새 자료의 실제 공개 시각을 반영했는가?
3. 단순 기준선과 같은 날짜에서 비교했는가?
4. 노트북 헬퍼를 동기화했는가?
5. 관련 단위 테스트와 전체 실행 테스트가 통과하는가?
6. 공개 문서와 워크플로 설명이 실제 설정과 일치하는가?

