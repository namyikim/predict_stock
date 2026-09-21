# S01 OHLCV 시퀀스·날짜 계약 실행 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 과거 OHLCV에서 5거래일 예측용 입력 배열과 누수 방지 메타데이터를 생성한다.
**Architecture:** 운영 코드와 분리된 순수 함수 모듈을 만든다. 학습 모델·다운로드·운영 발행은 포함하지 않는다. 주어진 KRX 세션 달력에서 시점을 정하고 누락 봉을 압축하거나 보간하지 않는다.
**Tech Stack:** Python, NumPy, pandas, unittest; 실제 거래일 검증에는 기존 exchange_calendars 사용.
**Spec:** [주간 모델 설계](weekly-sequence-model-plan.md)

## 승인·진행 상태

- [x] 사용자 설계 승인: “하나씩 구현을 진행해주세요. 완료되면 git push 하고 완료 체크”.
- [x] S01 상세 실행 계획 작성.
- [ ] 상세 실행 계획 검토.
- [ ] S01 코드 구현·검증.
- [ ] S01 완료 기록 및 main 반영 확인.

이 문서는 실행 계획이다. 아직 아래 API와 테스트 파일은 존재하지 않는다.
직접 순차 실행을 기본으로 하며 자동으로 하위 에이전트를 만들지 않는다.

## Global Constraints

- 60거래일 입력, 5거래일 만기 기본값. 예측일 d의 입력 끝은 d-1, 만기는 d+4.
- 공식 원장·보고서·노트북·자동 실행을 수정하지 않는다.
- 합성 데이터 테스트 통과를 실제 예측 성능 개선으로 표현하지 않는다.
- 학습용 통계 적합은 S02 이후 학습 구간에서만 한다. S01에 전체기간 scaler를 넣지 않는다.
- 일별 available_at과 label_available_at은 실제 제공 시각을 입력으로 받는다. 봉 날짜만으로 데이터 공개 시각을 확정하지 않는다.
- 기존 raw_close 기준 타깃과 동일한 원본 종가를 사용한다. 기업행동 구간은 아래 정책으로 제외한다.

## Review Focus

1. 주말·휴장일: 영업일 오프셋 대신 명시적인 KRX 세션 순서로 만기를 계산한다.
2. 빠진 거래일 봉: 다음 행을 앞당겨 사용하지 않고 해당 입력/타깃 표본을 제외한다.
3. 중복 날짜·잘못된 OHLC·NaN/무한대: 입력 오류는 명확히 거부한다.
4. 분할·배당: 주어진 기업행동 표시가 입력 워밍업부터 만기까지 있으면 제외한다. 기업행동 자료가 없다는 이유로 안전하다고 주장하지 않는다.
5. 미래 변경·공개 지연: 예측 시각 이후 데이터가 입력에 들어가지 않는다. 미도래 타깃은 학습 표본으로 쓰지 않는다.

## Task 1: 입력과 순수 변환 API

Files:
- Create: weekly_sequence_utils.py
- Create: tests/test_weekly_sequence.py

Interfaces:
- build_sequences(bars, sessions, available_at, prediction_at, corporate_actions, lookback=60, horizon=5) -> SequenceBatch
- bars: 날짜 인덱스, 원본 open/high/low/close/volume 숫자 열.
- sessions: 정렬·중복 없는 KRX 날짜 DatetimeIndex. 입력 범위와 만기 범위를 모두 포함한다.
- available_at: 각 봉의 timezone-aware 공개/사용가능 시각 Series.
- prediction_at: 예측 대상 세션 날짜를 인덱스로 갖는 timezone-aware 예측 시각 Series.
- corporate_actions: bars와 동일 날짜 인덱스의 bool Series; 분할 또는 배당 발생 표시.
- SequenceBatch: X(float32, N×lookback×5), y(float64, N), metadata(DataFrame), excluded(DataFrame).
- metadata: origin_date, prediction_date, target_date, available_at, label_available_at, prediction_at, current_close.
- excluded: prediction_date, reason. 정상적으로 표본이 없으면 X shape=(0,lookback,5)를 유지한다.

변환은 각 봉 i에 대해 아래 다섯 채널을 순서대로 사용한다. 이는 최종 scaler가 아닌 인과적 비율 변환이다.

```python
previous = bars.close.shift(1)
features = pd.DataFrame({
    "close_return": bars.close / previous - 1,
    "open_gap": bars.open / previous - 1,
    "high_relative": bars.high / previous - 1,
    "low_relative": bars.low / previous - 1,
    "volume_relative": bars.volume / bars.volume.shift(1).rolling(20).mean() - 1,
}, index=bars.index)
```

- [ ] 위 인터페이스를 호출하는 실패 테스트부터 작성한다. 아래 코드는 최소 타깃 계약이며 실제 fixture를 같은 파일에 정의한다.

```python
def test_target_matches_existing_five_session_definition(self):
    batch = build_sequences(**self.inputs, lookback=60, horizon=5)
    row = batch.metadata.iloc[0]
    d = self.inputs["sessions"].get_loc(row.prediction_date)
    sessions = self.inputs["sessions"]
    close = self.inputs["bars"].close
    self.assertEqual(row.origin_date, sessions[d - 1])
    self.assertEqual(row.target_date, sessions[d + 4])
    self.assertAlmostEqual(
        batch.y[0], close.loc[sessions[d + 4]] / close.loc[sessions[d - 1]] - 1
    )
    self.assertEqual(batch.X.shape[1:], (60, 5))
```

- [ ] Fixture는 KRX sessions_in_range의 2025년 거래일 160개, 양의 증가 종가, open=close, high=close×1.01, low=close×0.99, volume=1000을 쓴다. available_at=각 세션 16:00 Asia/Seoul, prediction_at=각 세션 07:00, 기업행동=False.
- [ ] python -m unittest tests.test_weekly_sequence -v 실행으로 모듈 미존재 실패를 확인한다.
- [ ] 입력 검증과 시퀀스 생성 구현: sessions를 기준으로 재색인하되 누락 데이터는 채우지 않는다. 20일 거래량 워밍업과 60일 입력 전체가 유효한 표본만 사용한다.
- [ ] 예측시각보다 늦은 입력 봉, 거래량 과거 평균 0, 기업행동, 누락 봉은 명확한 제외 사유로 남긴다. 중복/비정렬 날짜·타임존 없는 시각·음수 거래량·양수가 아닌 가격·고저 관계 오류는 ValueError.
- [ ] 타깃 아직 없음은 pending_target으로 제외한다. 라이브 예측 입력 지원은 S02에서 별도 API로 추가하고 학습 y를 가짜로 채우지 않는다.

## Task 2: 경계·누수·기업행동 테스트

Files: 위 두 파일만 수정한다.

- [ ] 예측일 이후 OHLCV를 1.5배로 바꾸어도 해당 예측 X가 그대로임을 검증한다.

```python
np.testing.assert_array_equal(before.X[0], after.X[0])
self.assertNotEqual(before.y[0], after.y[0])
```

- [ ] 만기 이후 값만 변경한 경우 이미 만기 지난 표본 X와 y가 모두 동일함을 확인한다.
- [ ] 2025년 5월 한국 휴장일을 넘기는 표본의 target_date가 세션 인덱스 d+4인지 확인한다.
- [ ] 입력 중간의 봉 한 개를 삭제해 제외 사유 missing_bar를 확인한다. lookback을 줄여 통과시키지 않는다.
- [ ] 입력 마지막 봉 available_at을 예측시각 이후로 바꿔 unavailable_input을 확인한다.
- [ ] corporate_actions를 입력 또는 만기 구간에서 True로 바꾸면 corporate_action으로 제외되는지 각각 확인한다.
- [ ] 분할을 흉내 낸 raw OHLC 반감·거래량 변화에서도 기업행동 표시가 있으면 제외되는지 확인한다.
- [ ] NaN/inf 가격, 음수 거래량, 중복 날짜, 잘못된 타임존, lookback/horizon이 bool·0·음수·실수인 경우 거부 테스트를 추가한다.
- [ ] 기업행동 제외 구간에는 입력 시작 전 20거래일 워밍업을 포함한다. 워밍업의 분할이 거래량 비율을 오염시키는 경우도 검사한다.
- [ ] 모든 정상 표본에서 최대 입력 available_at < prediction_at, origin_date < prediction_date <= target_date를 확인한다.

## Task 3: 검증·인수인계·커밋

- [ ] python -m unittest tests.test_weekly_sequence -v
- [ ] python -m unittest tests.test_medium_horizon.DesignTests -v
- [ ] python -m unittest discover -s tests -v
- [ ] git diff --check
- [ ] 전체 테스트가 의존성 부족으로 실패하면 정확한 테스트명과 원인을 기록한다. 통과했다고 쓰지 않는다.
- [ ] guides/weekly-sequence-model-plan.md에서 S01 완료 여부, 테스트 수·결과, 다음 S02 작업을 갱신한다.
- [ ] 운영 파일과 원장에 변경이 없음을 git diff --stat로 확인한다.
- [ ] 커밋: feat: add leakage-safe weekly OHLCV sequences
- [ ] main에 반영한 뒤 원격 파일·커밋을 다시 읽어 확인한다. 실패 시 체크하지 않는다.

## 다음 단계

S02는 이 API를 입력으로 쓰는 고정 스냅샷 로더와 기준선 실험 러너다.
S01만 완료한 상태에서 TCN이나 실제 성능 개선을 구현했다고 말하지 않는다.
