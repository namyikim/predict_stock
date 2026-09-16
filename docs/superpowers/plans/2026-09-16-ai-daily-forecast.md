# AI Daily Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 매 거래일 삼성전자·SK하이닉스에 대해 AI가 직접 남긴 시초가·종가 방향·종가 예측을 별도 원장에 고정하고, 장 마감 뒤 실제값과 비교한 누적 성과를 `admin → lab`에서 보여 준다.

**Architecture:** 표준 라이브러리만 사용하는 `tools/ai_daily_forecast.py`가 날짜별 JSON의 검증·불변 저장·채점·누적 집계를 담당한다. `docs/lab/index.html`은 GitHub raw의 별도 `index.json`을 읽는 세 번째 탭을 제공하며 기존 모델 원장에는 접근하지 않는다. ChatGPT 예약 작업 두 개가 주간 보고서와 같은 방식으로 오전 예측과 오후 채점을 실행하고 GitHub 앱으로 `main`에 반영한다.

**Tech Stack:** Python 3 표준 라이브러리, `unittest`, 정적 HTML/CSS/JavaScript, ChatGPT Automations, GitHub 앱

**Spec:** `docs/superpowers/specs/2026-09-16-ai-daily-forecast-design.md`

## Global Constraints

- 기존 주가예측 모델, `forecast_history`, 거시경제 판정의 입력·예측·점수를 사용하지 않는다.
- 종가 방향은 전일 종가 대비 상승·보합·하락이다.
- 오전 예약은 평일 08:00 KST, 오후 예약은 평일 16:10 KST에 실행한다.
- 09:00 KST 이후에는 예측 필드를 새로 만들거나 수정하지 않는다.
- 휴장·거래정지·미확정·수집 실패 값은 추측하지 않는다.
- 단일 100점 점수 대신 방향 적중률, 시초가 MAPE, 종가 MAPE, 표본 수를 분리한다.
- 날짜별 예측 생성 커밋과 사후 채점 커밋을 분리한다.
- 저장소에서 OpenAI API를 호출하거나 `OPENAI_API_KEY`를 요구하지 않는다.

---

## File Structure

- Create: `tools/ai_daily_forecast.py` — 스키마 검증, 예측 불변 저장, 채점, 누적 인덱스 생성, CLI
- Create: `tests/test_ai_daily_forecast.py` — 도메인·저장·CLI 단위 테스트
- Create: `ai_daily_forecast/index.json` — 실험실 탭이 처음부터 읽을 수 있는 빈 원장 인덱스
- Modify: `docs/lab/index.html` — 세 번째 AI 일일예측 탭과 렌더러
- Modify: `tests/test_lab_page.py` — 탭 분리, 안전한 렌더링, 빈 상태·오류 상태 회귀 테스트
- Create: `docs/ai-daily-forecast-operations.md` — 예약 작업의 입력 계약, 명령, 실패·재시도 절차

---

### Task 1: 불변 예측 원장과 채점기

**Files:**
- Create: `tools/ai_daily_forecast.py`
- Create: `tests/test_ai_daily_forecast.py`
- Create: `ai_daily_forecast/index.json`

**Interfaces:**
- Consumes: 예약 작업이 만든 UTF-8 JSON, KST ISO-8601 시각, 날짜별 실제 시초가·종가
- Produces: `record_prediction(document: dict, root: Path, now: datetime) -> Path`, `score_prediction(target_date: str, actuals: dict, root: Path, scored_at: datetime) -> Path`, `build_index(root: Path, updated_at: datetime) -> dict`

- [x] **Step 1: 스키마·점수 계산의 실패 테스트 작성**

```python
class MetricTests(unittest.TestCase):
    def test_direction_is_measured_against_previous_close(self):
        self.assertEqual(ai.actual_direction(71000, 72000), "상승")
        self.assertEqual(ai.actual_direction(71000, 71000), "보합")
        self.assertEqual(ai.actual_direction(71000, 70000), "하락")

    def test_absolute_percentage_error_is_percent(self):
        self.assertAlmostEqual(ai.absolute_percentage_error(102, 100), 2.0)

    def test_zero_actual_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "actual must be positive"):
            ai.absolute_percentage_error(100, 0)
```

- [x] **Step 2: 테스트가 예상대로 실패하는지 확인**

Run: `python -m unittest tests.test_ai_daily_forecast.MetricTests -v`

Expected: `ModuleNotFoundError` 또는 `AttributeError`로 실패한다.

- [x] **Step 3: 최소 점수 계산 함수 구현**

```python
DIRECTIONS = {"상승", "보합", "하락"}

def actual_direction(previous_close, actual_close):
    if actual_close > previous_close:
        return "상승"
    if actual_close < previous_close:
        return "하락"
    return "보합"

def absolute_percentage_error(predicted, actual):
    if actual <= 0:
        raise ValueError("actual must be positive")
    return abs(predicted - actual) / actual * 100.0
```

- [x] **Step 4: 예측 불변성·채점·집계의 실패 테스트 작성**

```python
def sample_document(day="2026-09-17"):
    return {
        "schema_version": 1,
        "target_date": day,
        "created_at_kst": f"{day}T08:00:00+09:00",
        "status": "predicted",
        "stocks": {
            "samsung": {
                "ticker": "005930", "name": "삼성전자", "previous_close": 70000,
                "predicted_open": 70500, "predicted_close_direction": "상승",
                "predicted_close": 71500, "rationale": ["전일 미국 반도체주 강세"],
                "sources": [{"title": "공개 자료", "url": "https://example.com/source"}],
            },
            "sk_hynix": {
                "ticker": "000660", "name": "SK하이닉스", "previous_close": 190000,
                "predicted_open": 192000, "predicted_close_direction": "하락",
                "predicted_close": 188000, "rationale": ["환율 변동성 확대"],
                "sources": [{"title": "공개 자료", "url": "https://example.com/source"}],
            },
        },
    }

class LedgerTests(unittest.TestCase):
    def test_prediction_must_be_created_before_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "before 09:00 KST"):
                ai.record_prediction(sample_document(), Path(tmp),
                    datetime(2026, 9, 17, 9, 0, tzinfo=ai.KST))

    def test_existing_prediction_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 9, 17, 8, 0, tzinfo=ai.KST)
            ai.record_prediction(sample_document(), root, now)
            with self.assertRaises(FileExistsError):
                ai.record_prediction(sample_document(), root, now)

    def test_scoring_preserves_prediction_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = sample_document()
            ai.record_prediction(before, root, datetime(2026, 9, 17, 8, 0, tzinfo=ai.KST))
            ai.score_prediction("2026-09-17", {
                "samsung": {"actual_open": 70400, "actual_close": 71600},
                "sk_hynix": {"actual_open": 191000, "actual_close": 187000},
            }, root, datetime(2026, 9, 17, 16, 10, tzinfo=ai.KST))
            after = json.loads((root / "2026-09-17.json").read_text())
            self.assertEqual(after["stocks"]["samsung"]["predicted_open"], 70500)
            self.assertTrue(after["stocks"]["samsung"]["direction_correct"])
            self.assertTrue(after["stocks"]["sk_hynix"]["direction_correct"])

    def test_index_reports_metrics_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai.record_prediction(sample_document(), root,
                datetime(2026, 9, 17, 8, 0, tzinfo=ai.KST))
            ai.score_prediction("2026-09-17", {
                "samsung": {"actual_open": 70400, "actual_close": 71600},
                "sk_hynix": {"actual_open": 191000, "actual_close": 187000},
            }, root, datetime(2026, 9, 17, 16, 10, tzinfo=ai.KST))
            index = ai.build_index(root, datetime(2026, 9, 17, 16, 11, tzinfo=ai.KST))
            self.assertEqual(index["summary"]["samsung"]["scored_days"], 1)
            self.assertEqual(index["summary"]["samsung"]["direction_accuracy_pct"], 100.0)
            self.assertIn("open_mape_pct", index["summary"]["samsung"])
            self.assertIn("close_mape_pct", index["summary"]["samsung"])
            self.assertNotIn("total_score", index["summary"]["samsung"])
```

- [x] **Step 5: 원장 검증·불변 저장·채점·집계 구현**

`validate_prediction()`은 정확히 `samsung`, `sk_hynix` 두 키, 양의 가격, 허용된 방향, 비어 있지 않은 판단 근거와 HTTPS 출처를 검사한다. `record_prediction()`은 `now.astimezone(KST).time() < 09:00`과 대상 날짜를 확인하고 기존 파일이 있으면 중단한다. `score_prediction()`은 `actual_*`, `actual_close_direction`, `direction_correct`, 두 MAPE와 `scored_at_kst`만 추가한다. 모든 JSON은 `ensure_ascii=False`, `indent=2`, 정렬된 키와 마지막 개행으로 저장한다.

- [x] **Step 6: 빈 인덱스와 CLI 테스트 작성**

```python
class CliTests(unittest.TestCase):
    def test_empty_index_contract(self):
        index = json.loads((ROOT / "ai_daily_forecast" / "index.json").read_text())
        self.assertEqual(index["schema_version"], 1)
        self.assertEqual(index["records"], [])
        self.assertEqual(index["summary"], {})

    def test_help_exposes_record_score_and_rebuild(self):
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "ai_daily_forecast.py"), "--help"],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        for command in ("record", "score", "rebuild-index"):
            self.assertIn(command, done.stdout)
```

- [x] **Step 7: CLI와 원자적 인덱스 갱신 구현**

CLI 계약은 다음과 같이 고정한다.

```bash
python tools/ai_daily_forecast.py record --input /tmp/ai-prediction.json
python tools/ai_daily_forecast.py score --date 2026-09-17 --input /tmp/ai-actuals.json
python tools/ai_daily_forecast.py rebuild-index
```

`record`와 `score`가 성공하면 자동으로 `ai_daily_forecast/index.json`을 다시 만든다. 임시 파일에 쓴 뒤 `Path.replace()`로 교체해 부분 JSON이 남지 않게 한다.

- [x] **Step 8: Task 1 테스트 실행**

Run: `python -m unittest tests.test_ai_daily_forecast -v`

Expected: 모든 테스트가 통과한다.

- [x] **Step 9: Task 1 커밋**

```bash
git add tools/ai_daily_forecast.py tests/test_ai_daily_forecast.py ai_daily_forecast/index.json
git commit -m "feat: add immutable AI daily forecast ledger"
```

---

### Task 2: 실험실 AI 일일예측 탭

**Files:**
- Modify: `docs/lab/index.html`
- Modify: `tests/test_lab_page.py`

**Interfaces:**
- Consumes: `https://raw.githubusercontent.com/namyikim/predict_stock/main/ai_daily_forecast/index.json`
- Produces: `tab-ai`, `panel-ai`, `loadAiForecasts()`, `renderAiForecasts(index)`

- [x] **Step 1: 세 번째 탭과 데이터 분리의 실패 테스트 작성**

```python
class AiDailyForecastTabTests(PageSource):
    def test_tab_is_separate_and_not_default(self):
        self.assertIn('id="tab-ai"', self.html)
        self.assertIn('id="panel-ai" hidden', self.html)
        self.assertIn("AI 일일예측", self.html)

    def test_reads_only_the_separate_ai_ledger(self):
        self.assertIn("ai_daily_forecast/index.json", self.script)
        ai_block = self.script[self.script.index("function loadAiForecasts") :]
        self.assertNotIn("forecast_log.csv", ai_block)
        self.assertNotIn("macro", ai_block.lower())

    def test_explains_baseline_and_separate_metrics(self):
        for text in ("전일 종가 대비", "방향 적중률", "시초가 평균 오차", "종가 평균 오차"):
            self.assertIn(text, self.html + self.script)

    def test_warns_on_small_samples_and_disclaims_advice(self):
        self.assertIn("scored_days < 20", self.script)
        self.assertIn("투자 자문이 아닙니다", self.html)
```

- [x] **Step 2: 새 테스트가 실패하는지 확인**

Run: `python -m unittest tests.test_lab_page.AiDailyForecastTabTests -v`

Expected: `tab-ai`와 `loadAiForecasts`가 없어 실패한다.

- [x] **Step 3: 탭 마크업과 상태 영역 구현**

기존 탭 목록에 `<button id="tab-ai" class="tab">AI 일일예측</button>`을 추가하고, `panel-ai` 안에 다음 고정 영역을 둔다.

```html
<div id="ai-status" class="muted">AI 판단 기록을 불러오는 중…</div>
<div id="ai-latest"></div>
<h2>누적 성과</h2><div id="ai-summary"></div>
<h2>전체 이력</h2><div id="ai-history"></div>
```

경고문에는 “기존 알고리즘과 거시경제 예측을 사용하지 않은 별도 AI 판단”, “종가 방향은 전일 종가 대비”, “투자 자문이 아님”을 명시한다.

- [x] **Step 4: 안전한 JSON 렌더러 구현**

```javascript
var AI_BASE = "https://raw.githubusercontent.com/namyikim/predict_stock/main/ai_daily_forecast/";
function esc(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function loadAiForecasts() {
  fetch(AI_BASE + "index.json", {cache: "no-store"})
    .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(renderAiForecasts)
    .catch(function (e) {
      $("ai-status").textContent = "";
      $("ai-latest").innerHTML = '<div class="empty">AI 판단 기록을 불러오지 못했습니다 (' + esc(e.message) + ').</div>';
    });
}
```

모든 원장 문자열은 `esc()`를 통과시킨다. 출처 URL은 `https://`만 링크로 만들고 `target="_blank" rel="noopener noreferrer nofollow"`를 붙인다. 최신 예측 카드, 실제 결과, 종목별 요약 타일, 최신순 이력 표를 렌더링한다. `scored_days < 20`이면 표본 부족 경고를 표시한다.

- [x] **Step 5: 기존 탭 전환기를 세 탭으로 확장**

```javascript
["sim", "attr", "ai"].forEach(function (name) {
  $("tab-" + name).addEventListener("click", function () {
    ["sim", "attr", "ai"].forEach(function (other) {
      $("tab-" + other).className = "tab" + (other === name ? " active" : "");
      $("panel-" + other).hidden = other !== name;
    });
    if (name === "attr" && !attrRows.length) loadAttribution();
    if (name === "ai" && !aiLoaded) loadAiForecasts();
  });
});
```

- [x] **Step 6: JavaScript 구문과 탭 회귀 테스트 실행**

Run: `python -m unittest tests.test_lab_page -v`

Expected: 기존 가상 매매·기여도 테스트와 새 AI 탭 테스트가 모두 통과하고 `node --check`가 성공한다.

- [x] **Step 7: Task 2 커밋**

```bash
git add docs/lab/index.html tests/test_lab_page.py
git commit -m "feat: add AI daily forecast lab tab"
```

---

### Task 3: 운영 문서와 실패 복구 계약

**Files:**
- Create: `docs/ai-daily-forecast-operations.md`
- Modify: `tests/test_ai_daily_forecast.py`

**Interfaces:**
- Consumes: Task 1의 CLI와 JSON 스키마
- Produces: 두 예약 작업이 그대로 따라 할 수 있는 입력 예시, 검증 명령, Git 반영 순서

- [x] **Step 1: 운영 문서 계약 테스트 작성**

```python
class OperationsDocTests(unittest.TestCase):
    def test_runbook_names_times_boundaries_and_commands(self):
        text = (ROOT / "docs" / "ai-daily-forecast-operations.md").read_text(encoding="utf-8")
        for required in ("08:00 KST", "16:10 KST", "09:00 KST 이후", "전일 종가 대비",
                         "record --input", "score --date", "rebuild-index",
                         "forecast_history", "거시경제", "소급 예측하지"):
            self.assertIn(required, text)
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m unittest tests.test_ai_daily_forecast.OperationsDocTests -v`

Expected: 운영 문서가 없어 실패한다.

- [x] **Step 3: 오전·오후 실행 절차 작성**

문서에는 다음 순서를 실제 명령과 함께 기록한다.

1. 원격 `main` 최신화와 한국 거래일 확인
2. 오전 공개 자료 조사 및 두 종목 예측 JSON 작성
3. `record` 실행과 테스트, 예측 전용 커밋 생성
4. 오후 실제 시초가·종가를 두 개 이상 공개 자료로 교차 확인
5. `score` 실행과 테스트, 채점 전용 커밋 생성
6. `data_pending` 재시도와 실패 사유 기록
7. 기존 모델 원장과 거시경제 자료를 판단 입력으로 읽지 않는 금지사항

- [x] **Step 4: 운영 문서 테스트 실행**

Run: `python -m unittest tests.test_ai_daily_forecast.OperationsDocTests -v`

Expected: 통과한다.

- [x] **Step 5: Task 3 커밋**

```bash
git add docs/ai-daily-forecast-operations.md tests/test_ai_daily_forecast.py
git commit -m "docs: add AI daily forecast operations runbook"
```

---

### Task 4: 전체 검증과 GitHub 반영

**Files:**
- Verify: `tools/ai_daily_forecast.py`
- Verify: `ai_daily_forecast/index.json`
- Verify: `docs/lab/index.html`
- Verify: `docs/ai-daily-forecast-operations.md`

**Interfaces:**
- Consumes: Tasks 1–3의 모든 산출물
- Produces: 원격 `main`의 검증된 구현

- [x] **Step 1: 타깃 테스트 실행**

Run: `python -m unittest tests.test_ai_daily_forecast tests.test_lab_page -v`

Expected: 모두 통과한다.

- [ ] **Step 2: 전체 회귀 테스트 실행**

Run: `python -m unittest discover -s tests -v`

Expected: 모든 테스트가 통과한다. 실패가 있으면 기능과 무관한 기존 실패인지 추측하지 말고 원인을 확인한다.

- [ ] **Step 3: 정적 검사와 작업 트리 확인**

```bash
git diff --check
node --check /tmp/_lab_check.js
git status --short --branch
```

Expected: 공백 오류와 JavaScript 구문 오류가 없고 의도하지 않은 파일 변경이 없다.

- [x] **Step 4: 원격 최신본을 통합하고 재검증**

```bash
git fetch origin main
git rebase origin/main
python -m unittest tests.test_ai_daily_forecast tests.test_lab_page -v
```

Expected: 충돌 없이 재배치되고 타깃 테스트가 다시 통과한다.

- [x] **Step 5: GitHub 앱으로 `main` 반영 확인**

터미널 자격증명이 없으면 노출된 PAT를 사용하지 않는다. 연결된 GitHub 앱으로 변경 파일을 반영한 뒤 원격 파일과 커밋 SHA를 다시 읽어 확인한다.

---

### Task 5: 주간 보고서 방식의 두 예약 작업 생성

**Files:**
- Read: `docs/ai-daily-forecast-operations.md`
- Read: `docs/superpowers/specs/2026-09-16-ai-daily-forecast-design.md`

**Interfaces:**
- Consumes: 원격 `main`의 구현, 연결된 GitHub 앱, 공개 웹 자료
- Produces: 평일 08:00 KST 예측 자동화와 평일 16:10 KST 채점 자동화

- [x] **Step 1: GitHub 연결을 읽기 전용 호출로 확인**

`namyikim/predict_stock` 저장소 메타데이터를 GitHub 앱으로 조회한다. 연결·재연결·권한 요청이 나타나면 예약 작업을 만들지 말고 사용자의 연결 완료를 기다린다.

- [x] **Step 2: 오전 예측 자동화 생성**

Schedule:

```ical
BEGIN:VEVENT
DTSTART;TZID=Asia/Seoul:20260917T080000
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
END:VEVENT
```

Prompt:

```text
namyikim/predict_stock의 docs/ai-daily-forecast-operations.md와 승인된 설계를 읽고 오늘이 한국거래소 거래일인지 확인하라. 거래일이면 최신 공개 시장자료와 뉴스를 직접 조사해 삼성전자와 SK하이닉스의 당일 시초가, 전일 종가 대비 종가 방향, 종가를 판단하라. 기존 주가예측 알고리즘, forecast_history, 거시경제 예측·점수는 입력으로 사용하지 마라. 출처와 짧은 판단 근거를 포함한 입력 JSON을 만들고 tools/ai_daily_forecast.py record --input으로 검증·저장하라. 09:00 KST 이후이거나 같은 날짜 예측이 이미 있으면 새 예측을 만들거나 덮어쓰지 마라. 관련 테스트를 실행한 뒤 예측 파일과 index.json만 별도 커밋으로 GitHub main에 반영하고, 실패하면 값을 추측하거나 소급 예측하지 말고 원인을 보고하라.
```

- [x] **Step 3: 오후 채점 자동화 생성**

Schedule:

```ical
BEGIN:VEVENT
DTSTART;TZID=Asia/Seoul:20260917T161000
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
END:VEVENT
```

Prompt:

```text
namyikim/predict_stock의 docs/ai-daily-forecast-operations.md와 오늘의 ai_daily_forecast 날짜별 파일을 읽어라. 오전 예측이 있을 때만 삼성전자와 SK하이닉스의 확정된 실제 시초가·종가를 신뢰할 수 있는 공개 자료로 확인하고 tools/ai_daily_forecast.py score --date와 입력 JSON으로 채점하라. 예측 필드와 판단 근거는 절대 수정하지 말고 실제값, 방향 적중 여부, 시초가·종가 오차와 채점 시각만 추가하라. 휴장·거래정지·데이터 미확정이면 추측하지 말고 상태와 사유를 남겨라. 관련 테스트를 실행한 뒤 날짜별 파일과 index.json만 채점 전용 커밋으로 GitHub main에 반영하고 실패 원인을 보고하라.
```

- [x] **Step 4: 예약 상태 검증**

두 자동화가 활성 상태인지 비공개 조회로 확인한다. 제목, KST 시간, 월~금 반복, 오전/오후 역할이 바뀌지 않았는지 검증한다. 즉시 실행은 사용자가 요청하지 않는 한 하지 않는다.

- [x] **Step 5: 완료 기록**

이 계획의 완료된 체크박스를 `[x]`로 갱신하고 마지막 검증 결과와 원격 커밋 SHA를 문서 끝에 기록한 뒤 GitHub `main`에 반영한다.


---

## 구현 기록 (2026-09-17 KST)

- 원격 \`main\` 구현 반영: 코드 기준 커밋 \`a1c8ffc2ad45cbfe02bfaec675592664c2e6a9c5\`
- 브라우저 JavaScript 독립 구문 검사: 통과
- 로컬 타깃 테스트: 구현 중 44개 통과
- 전체 회귀 테스트: 의존성 설치 후 장시간 실행에서 확인된 구간은 통과했으나, 마지막 구간에 실행 환경 연결이 끊겨 최종 \`OK\` 출력을 확보하지 못함
- 위 사유로 Task 4의 전체 회귀 완료와 로컬 작업 트리 최종 확인은 미체크 상태로 남김
- 예약 작업: 평일 08:00 KST 예측과 16:10 KST 채점, 모두 활성 상태 확인
