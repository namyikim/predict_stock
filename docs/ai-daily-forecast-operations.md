# AI 일일예측 운영 절차

이 기능은 주간 반도체 보고서와 같은 ChatGPT 예약 작업이 공개 자료를 직접 조사해 남기는 별도 실험 기록이다. 기존 주가예측 알고리즘, \`forecast_history\`, 거시경제 예측·판정·점수는 판단 입력으로 읽거나 사용하지 않는다. 저장소 안에서 OpenAI API를 호출하지 않는다.

## 고정 일정과 정의

- 오전 예측: 한국 거래일 **08:00 KST**
- 오후 채점: 한국 거래일 **16:10 KST**
- 종가 방향: **전일 종가 대비** 상승·보합·하락
- **09:00 KST 이후**에는 예측을 새로 만들거나 예측값·근거를 수정하지 않는다.
- 휴장, 거래정지, 자료 미확정 또는 수집 실패 때는 값을 추정하지 않고 실패 사유를 남긴다.
- 단일 점수는 만들지 않는다. 방향 적중률, 시초가 MAPE, 종가 MAPE, 표본 수를 따로 공개한다.

## 오전 08:00 KST — 예측

1. GitHub \`main\`의 최신 파일과 이 문서를 읽고 한국거래소 정규장 거래일인지 확인한다.
2. 같은 날짜의 \`ai_daily_forecast/YYYY-MM-DD.json\`이 이미 있으면 즉시 중단한다.
3. 신뢰할 수 있는 최신 공개 시장 자료와 밤사이 뉴스를 직접 조사한다. 각 종목의 전일 종가, 예상 시초가, 예상 종가 방향, 예상 종가, 짧은 근거, HTTPS 출처를 기록한다.
4. 다음 형식의 UTF-8 JSON을 임시 파일로 만든다.

\`\`\`json
{
  "schema_version": 1,
  "target_date": "2026-09-17",
  "created_at_kst": "2026-09-17T08:00:00+09:00",
  "status": "predicted",
  "stocks": {
    "samsung": {
      "ticker": "005930",
      "name": "삼성전자",
      "previous_close": 70000,
      "predicted_open": 70500,
      "predicted_close_direction": "상승",
      "predicted_close": 71500,
      "rationale": ["판단 근거"],
      "sources": [{"title": "자료명", "url": "https://example.com"}]
    },
    "sk_hynix": {
      "ticker": "000660",
      "name": "SK하이닉스",
      "previous_close": 190000,
      "predicted_open": 192000,
      "predicted_close_direction": "하락",
      "predicted_close": 188000,
      "rationale": ["판단 근거"],
      "sources": [{"title": "자료명", "url": "https://example.com"}]
    }
  }
}
\`\`\`

5. 아래 명령으로 검증하고 불변 원장과 인덱스를 만든다.

\`\`\`bash
python tools/ai_daily_forecast.py record --input /tmp/ai-prediction.json
python -m unittest tests.test_ai_daily_forecast tests.test_lab_page -v
\`\`\`

6. 날짜별 예측 파일과 \`index.json\`만 예측 전용 커밋으로 GitHub \`main\`에 반영한다. 09:00가 지났거나 저장·검증이 실패하면 **소급 예측하지 않는다**.

## 오후 16:10 KST — 채점

1. 오전 날짜별 예측이 있을 때만 진행한다.
2. 삼성전자와 SK하이닉스의 확정 실제 시초가·종가를 신뢰할 수 있는 공개 자료로 확인한다. 가능하면 거래소 또는 두 개 이상의 출처를 교차 확인한다.
3. 다음 형식의 실제값 JSON을 임시 파일로 만든다.

\`\`\`json
{
  "samsung": {"actual_open": 70400, "actual_close": 71600},
  "sk_hynix": {"actual_open": 191000, "actual_close": 187000}
}
\`\`\`

4. 아래 명령으로 실제값과 분리 지표만 추가한다.

\`\`\`bash
python tools/ai_daily_forecast.py score --date 2026-09-17 --input /tmp/ai-actuals.json
python tools/ai_daily_forecast.py rebuild-index
python -m unittest tests.test_ai_daily_forecast tests.test_lab_page -v
\`\`\`

5. 날짜별 파일과 \`index.json\`만 채점 전용 커밋으로 GitHub \`main\`에 반영한다. 기존 예측 가격·방향·근거·출처는 절대 바꾸지 않는다.

## 실패와 재시도

- 휴장 또는 거래정지: 예측·채점 파일을 억지로 만들지 말고 사유와 다음 거래일을 보고한다.
- 실제값 미확정: \`data_pending\`으로 보고하고 확정 자료가 생긴 뒤 오후 채점만 재시도한다.
- GitHub 충돌: 원격 최신본과 같은 날짜 파일 존재 여부를 다시 확인한다. 기존 예측은 덮어쓰지 않는다.
- 일부 자료 수집 실패: 없는 값을 추정하지 않는다. 필수 가격을 확인할 수 없으면 실행을 중단한다.
- 재시도 중에도 09:00 KST 이후 예측을 만들거나 수정하지 않으며, 과거 날짜를 소급 예측하지 않는다.
