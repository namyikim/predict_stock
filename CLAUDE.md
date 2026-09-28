# CLAUDE.md

삼성전자·SK하이닉스의 주가를 여러 기간에 걸쳐 예측하고, 실제 결과로 검증해 성적을 분석하고, 그 분석으로
모델 성능을 개선해 **투자자의 판단을 돕는** 프로젝트.

- 예측 기간: **다음 거래일**(방향·시초가·종가), **1주일 뒤**(5거래일 종가), **20거래일 뒤**(종가)와 예상 범위.
- 모든 예측은 미리 원장에 기록하고, 대상 날짜가 지나면 실제값으로 채점한다(백테스트가 아니라 실제 사전 예측의 성적).
- 기간별 성적을 분석해 약한 곳을 찾고 모델을 고친다. 고친 모델도 같은 방식으로 다시 검증한다.

금·은, 중국 주식, 거시 경제, 뉴스·검색어 보고서도 GitHub Actions가 자동으로 만들어 GitHub Pages(`docs/`)에 발행한다.
투자자를 돕는 방식은 **판단 재료를 주는 것**이다. 연구·교육용이며 **투자 자문이 아니다** — 화면·문구에 매수·매도 의견을 넣지 않는다.

## 대화와 작업 방식

- 사용자와는 **한국어**로 대화한다. 코드 주석·커밋 메시지·문서도 한국어로 쓴다.
- 설명은 가능하면 글보다 **그림·그래프**로 한다(2026-09-29 요청). 숫자 비교·범위·추이는 표나 긴 글 대신 도표로 보인다.
- 커밋·push 전에는 물어본다. 변경을 요약하고 "커밋하고 push할까요?"로 확인받는다.
- 작업 시작 전 `git pull`. 봇이 수시로 커밋하므로(보고서·원장·뉴스) push 전에는 `git pull --rebase` 후 push한다.
- 끝나면 실제로 확인한 것과 확인하지 못한 것을 나눠 말한다(로컬에서 테스트를 못 돌렸으면 그렇다고 적는다).

## 토큰을 아끼는 작업 방식

이 저장소는 Claude API를 호출하지 않는다(보고서는 정해진 코드와 공개 자료로 만든다). 아래는 Claude Code로 작업할 때의 습관이다.
출처: [Reducing cost and improving performance with Claude Platform](https://claude.com/blog/reducing-cost-and-improving-performance-with-claude-platform)(2026-09-08).

- **큰 파일은 필요한 부분만 읽는다.** 노트북은 약 2만 2천 줄, `docs/samsung/index.html`·`docs/sk_hynix/index.html`은 각 290KB다.
  `grep -n`으로 위치를 찾고 그 줄 범위만 읽는다. 생성된 `docs/` 페이지와 `forecast_history/` CSV는 통째로 출력하지 않는다.
- **명령 출력은 짧게 받는다.** `| tail`, `| head`, `grep -c`로 필요한 줄만 본다(전체 테스트 로그, `git log`, API 응답 등).
- **일의 크기에 맞게 생각한다.** `git pull`·문구 수정·메뉴 순서 같은 작은 일은 바로 하고, 모델·검증·원장처럼 틀리면 비싼 일에서 깊게 따진다.
- **한 대화 안에서 모델·effort 설정을 자주 바꾸지 않는다.** 앞부분 캐시가 깨져 다시 읽는 비용이 든다. 바꿀 거라면 새 작업을 시작할 때 바꾼다.
- **오래 걸리는 확인은 기다리며 붙잡지 않는다.** CI처럼 몇 분 이상 걸리는 일은 백그라운드로 돌려 끝나면 알림을 받는다.
- **서브에이전트는 넓게 뒤져야 할 때만** 쓴다. 파일·함수 위치를 이미 알면 직접 찾는다.
- **이 파일(CLAUDE.md)은 매 대화에 실린다.** 짧게 유지하고, 날짜처럼 자주 바뀌는 값이나 한 번만 필요한 설명은 `guides/`에 둔다.
  "반드시 두 번 확인", "최대한 철저히", 고정된 단계 절차 같은 문구는 오히려 도구 호출과 토큰을 늘리므로 넣지 않는다.
  서로 부딪히는 규칙이 생기지 않게 고칠 때 전체를 한 번 훑는다. 점검은 Claude Code에서 `/claude-api prompt-audit`로 할 수 있다.

## 구조 (자세히는 `guides/development.md`)

| 경로 | 역할 |
| --- | --- |
| `samsung_direction_model_colab.ipynb` | 종목 보고서의 중심. 수집·학습·예측·보고서 조립 |
| `report_html.py`, `forecast_utils.py`, `macro_utils.py`, `data_sources/` | 노트북이 쓰는 헬퍼(노트북 셀에 소스가 그대로 복사됨) |
| `tools/build_*.py` | 종목 외 보고서 생성기 |
| `counter/worker.js`, `counter/schema.sql` | 조회수·구독 Cloudflare Worker와 D1 스키마 |
| `docs/` | 발행 결과(GitHub Pages) |
| `forecast_history/`, `macro_history/` | 예측 원장·자료 보관본(생성 결과) |
| `tests/` | `python -m unittest discover -s tests -v` |

### `docs/` 페이지는 누가 만드나

- **손으로 관리하는 정적 파일**: `docs/index.html`(메인), `docs/admin/index.html`(관리자), `docs/lab/index.html`(실험실). 직접 고친다.
- **노트북이 만든다**: `docs/samsung/`, `docs/sk_hynix/` — 직접 고치지 말고 노트북·`report_html.py`를 고친다.
- **도구가 만든다**: `metals`·`china`·`trends`·`interest`·`ai_news`·`macro`는 `tools/build_*.py`.
  `docs/news/index.html`은 `python tools/build_news_hub.py --write`로 다시 쓴다(테스트가 생성 결과와 비교한다).
- 메인 페이지의 구독 칸은 `report_html.report_top_bar_html(..., back=False)` 출력을 붙여 둔 것이다. 함수를 고치면 메인에도 다시 붙인다.

## 지킬 것

- **노트북 헬퍼 동기화**: `report_html.py`·`forecast_utils.py`·`data_sources/`를 고치면 `python tools/sync_notebook_helpers.py`를 실행해 노트북 셀을 맞춘다(테스트가 같은지 본다).
- **원장(`forecast_history/`)을 손으로 고치지 않는다.** 날짜별 **최초 사전 예측**만 집계한다. 나중 값으로 덮어쓰면 결과를 보고 예측을 고른 것과 구별할 수 없다.
- **수동 `Run workflow`(workflow_dispatch)는 예측을 원장에 한 번 더 기록한다.** 보고서만 다시 만들려면 "코드 반영"(push) 실행을 쓴다(`PREDICT_STOCK_RECORD_FORECAST=false`).
- 미래 정보가 특징에 섞이지 않게 한다. 새 자료는 실제 공개 시각 기준으로 쓴다.
- 개인정보: 방문자 IP·위치 원본을 저장하지 않는다(날짜가 섞인 해시만). 이메일 수집에는 수집 항목·목적·보관 기간과 동의를 둔다.

## GitHub Actions에서 알아둘 것

- `daily-report.yml`의 push 트리거는 **노트북·헬퍼·`tools/`·`requirements.txt`가 바뀔 때만** 돈다. `docs/`만 바꾼 push로는 종목 보고서가 다시 만들어지지 않는다(정적 페이지는 Pages 배포로 바로 반영).
- push로 돈 실행은 먼저 `validation / test`(= `tests.yml`)를 돌리고, **테스트가 실패하면 보고서 작업이 건너뛰어진다.** push 직후 따로 뜨는 "테스트" 실행은 이것과 겹쳐 취소되는 것이 보통이다.
- 정기 실행은 오늘 예측이 이미 기록됐으면 종목 보고서를 다시 만들지 않는다(`tools/should_run_today.py`).
- 실행 상태는 공개 API로 볼 수 있다: `curl -s "https://api.github.com/repos/namyikim/predict_stock/actions/runs?head_sha=<sha>"`.
  로그는 로그인이 필요해 사용자에게 `FAIL:`/`ERROR:` 부분을 붙여 달라고 한다.

## Cloudflare Worker (조회수·구독)

- `counter/worker.js`는 **자동 배포되지 않는다.** 사용자가 대시보드(Workers & Pages → predict-stock-counter → Edit code)에 붙여넣고 Deploy 해야 한다.
- 파일 전체를 붙여야 한다. `export default`가 빠지면 "Binding 'DB' of type 'd1' requires a Worker written in ES module format" 오류가 난다.
- 이 Mac에서는 `pbcopy`가 막혀 있다. 코드를 대화에 그대로 보여 주거나 `open -e counter/worker.js`로 열어 준다.
- D1 Console에서 SQL은 **한 문장씩** 실행한다(여러 줄을 한 번에 붙이면 주석 때문에 깨진다). 새 표는 `schema.sql`과 `counter/README.md`에 함께 적는다.
- 허용 목록(`ALLOWED_PAGES`, `SUBSCRIBE_PAGES`)을 바꾸면 재배포가 필요하다고 사용자에게 알린다.

## 로컬 환경의 한계

- 기본 `python3`는 3.9이고 numpy·pandas가 없으며 `node`도 없다. CI는 Python 3.13 + node 22다.
  → 로컬에서는 문법 검사(`ast.parse`)와 순수 파이썬 부분만 확인하고, 나머지는 CI 결과로 확인한다.
- `report_html.py`는 numpy를 import하므로, 함수 하나를 로컬에서 돌릴 때는 `ast`로 그 함수만 떼어 실행한다.
- 화면 확인: `cd docs && python3 -m http.server 8765 --bind 127.0.0.1`을 백그라운드로 띄우고 앱 브라우저로 연다
  (`preview_start`의 서버는 Documents 폴더 권한이 없어 실패한다). 휴대폰 폭(375px)에서 가로 스크롤이 없는지도 본다.
- 운영 중인 Worker에는 실제 이메일 등 저장되는 요청을 보내지 않는다. 형식이 틀린 값으로 경로만 확인한다.

## 코드 쓰는 습관

- 주석에는 **왜** 그렇게 했는지와 날짜·요청을 남긴다. 예: `# 탭 → 왼쪽 메뉴(2026-09-28 요청).`
- 페이지 HTML은 대부분 인라인 스타일이다. 색·모양은 기존 것을 따른다.
  - 강조색 `#1a5490`
  - 목록 버튼: `border:1px solid #cedff0;border-radius:5px`
  - 탭: 알약 버튼(연한 배경 `#f0f6fc`·테두리 `#b9d3ec`, 고른 탭은 `#1a5490`으로 채움)
- 페이지 스크립트 일부는 테스트가 **node에서** 실행한다(`tests/test_lab_page.py` 등). `location`·`window`처럼 브라우저에만 있는 것을 쓸 때는 `typeof location === "undefined"` 같은 대비를 둔다.
- 커밋 메시지: `feat:`/`fix:`/`ui:`/`admin:` 같은 짧은 접두어 + 한국어 한 줄 요약, 빈 줄 뒤에 이유.
