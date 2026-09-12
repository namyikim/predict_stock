"""미국 지표 발표 일정 — 발표 결과가 아니라 '언제 발표되는가'만 쓴다.

결과는 미리 알 수 없지만 날짜는 미리 안다. CPI·PPI·FOMC 발표는 한국 시간으로 밤에 나오므로
그 반응은 **다음 한국 거래일 갭**에 나타나고, 그날은 방향과 무관하게 변동폭이 커진다.
그래서 예측이나 구간을 바꾸지 않고 원장에 표시만 남긴다. 표시가 쌓이면 '이벤트일'과 '평일'을
나눠 채점할 수 있고, 구간이 이벤트일에 너무 좁다는 것이 데이터로 드러나면 그때 조정한다.
지금 임의로 넓히면 보정이 깨진다.

날짜는 규칙으로 계산하지 않는다. BLS 는 '매월 둘째 주' 같은 규칙을 따르지 않고(2026-09 는
PPI 목요일·CPI 금요일), 셧다운으로 취소·연기된 전례도 있다. 그래서 공표된 일정을 그대로 적고,
바뀌면 macro_inputs/us_calendar.csv (date,event) 로 덮어쓴다. 표에 없는 기간은 표시하지 않는다
— 잘못된 날짜를 표시하는 것보다 표시하지 않는 편이 낫다.

출처: BLS 공표 일정(bls.gov/schedule), 연준 FOMC 일정(federalreserve.gov),
BEA 개인소득·지출 일정(bea.gov/news/schedule).
"""
from datetime import timedelta
from pathlib import Path

import pandas as pd

# 공표된 일정. 연말에 다음 해 것을 추가한다(없으면 그 기간은 조용히 표시되지 않는다).
#
# dict 가 아니라 (날짜, 이벤트) 쌍이다. 같은 날 둘 이상 열리기 때문이다 — 2026-03-18 은 PPI 와
# FOMC 가 같은 날이고, 2026-09-30 은 마이크론 실적과 PCE 가 겹친다. dict 로 두면 뒤에 적은 것이
# 앞의 것을 조용히 덮어써서 한쪽이 사라진다.
US_RELEASES = (
    # 2026 CPI (8:30 ET)
    ("2026-01-13", "CPI"), ("2026-02-13", "CPI"), ("2026-03-11", "CPI"), ("2026-04-10", "CPI"),
    ("2026-05-12", "CPI"), ("2026-06-10", "CPI"), ("2026-07-14", "CPI"), ("2026-08-12", "CPI"),
    ("2026-09-11", "CPI"), ("2026-10-14", "CPI"), ("2026-11-10", "CPI"), ("2026-12-10", "CPI"),
    # 2026 PPI (8:30 ET)
    ("2026-01-14", "PPI"), ("2026-01-30", "PPI"), ("2026-02-27", "PPI"), ("2026-03-18", "PPI"),
    ("2026-04-14", "PPI"), ("2026-05-13", "PPI"), ("2026-06-11", "PPI"), ("2026-07-15", "PPI"),
    ("2026-08-13", "PPI"), ("2026-09-10", "PPI"), ("2026-10-15", "PPI"),
    # 2026 고용보고서 — 비농업 고용(8:30 ET). BLS 공표 일정.
    # 갭을 가장 크게 벌리는 발표 중 하나라 CPI 와 함께 표시한다.
    ("2026-01-09", "NFP"), ("2026-02-11", "NFP"), ("2026-03-06", "NFP"), ("2026-04-03", "NFP"),
    ("2026-05-08", "NFP"), ("2026-06-05", "NFP"), ("2026-07-02", "NFP"), ("2026-08-07", "NFP"),
    ("2026-09-04", "NFP"), ("2026-10-02", "NFP"), ("2026-11-06", "NFP"), ("2026-12-04", "NFP"),
    # 2026 PCE 물가(개인소득·지출, 8:30 ET). BEA 는 앞으로의 일정만 게시하므로 공표된 분만 적는다.
    ("2026-09-30", "PCE"), ("2026-10-29", "PCE"), ("2026-11-25", "PCE"), ("2026-12-23", "PCE"),
    # FOMC 금리 결정(성명 발표일, 14:00 ET). 이틀 회의의 둘째 날이다.
    ("2026-01-28", "FOMC"), ("2026-03-18", "FOMC"), ("2026-04-29", "FOMC"), ("2026-06-17", "FOMC"),
    ("2026-07-29", "FOMC"), ("2026-09-16", "FOMC"), ("2026-10-28", "FOMC"), ("2026-12-09", "FOMC"),
    # 미국 반도체 실적 발표(장 마감 후). 마이크론은 같은 메모리 업종이라 삼성·하이닉스에 가장 직접적이다.
    # 회사가 공식 공지한 날짜만 적는다. 제3자 캘린더의 '추정'은 출처끼리도 어긋난다(2026-09 엔비디아
    # 11/17 vs 11/25). 추정을 넣으면 엉뚱한 날에 이벤트가 붙고 진짜 날은 놓친다.
    ("2026-09-30", "MU"),        # 마이크론 FQ4 — 2026-08-26 회사 보도자료로 확정
)
# 아직 공식 공지가 없는 실적. 보고서에 '미확정'으로 보여 주어 CSV 로 채우도록 한다.
PENDING_EARNINGS = {
    "NVDA": "엔비디아 3분기(11월 중하순 예상, 회사 공지 대기)",
    "TSM": "TSMC 3분기(10월 중순 예상, 재무 캘린더 미게시)",
    "AMD": "AMD 3분기(11월 초 예상, 미공지)",
}
# 같은 날 둘 이상이면 리스트가 된다(적은 순서대로).
_BY_DAY = {}
for _date, _event in US_RELEASES:
    _BY_DAY.setdefault(_date, []).append(_event)

EVENT_LABELS = {
    "CPI": "미국 소비자물가(CPI)",
    "PPI": "미국 생산자물가(PPI)",
    "NFP": "미국 고용보고서(비농업)",
    "PCE": "미국 PCE 물가",
    "FOMC": "FOMC 금리 결정",
    "MU": "마이크론 실적",
    "NVDA": "엔비디아 실적",
    "TSM": "TSMC 실적",
    "AMD": "AMD 실적",
}
# 표에 실제로 담긴 기간. 이 밖의 날짜는 '모른다'고 다뤄야 한다.
COVERAGE = (min(d for d, _ in US_RELEASES), max(d for d, _ in US_RELEASES))


def read_us_calendar(storage):
    """macro_inputs/us_calendar.csv (date,event) 로 덮어쓰거나 기간을 넓힌다."""
    if storage is None:
        return {}
    path = Path(storage) / "macro_inputs" / "us_calendar.csv"
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype=str)
    if not {"date", "event"}.issubset(frame.columns):
        raise ValueError("us_calendar.csv 에는 date,event 열이 필요합니다.")
    out = {}
    for _, row in frame.iterrows():
        try:
            day = pd.Timestamp(str(row["date"]).strip()).date().isoformat()
        except (TypeError, ValueError):
            continue
        out.setdefault(day, []).append(str(row["event"]).strip())
    return out


def us_events_on(day, storage=None):
    """그날(미국 동부 기준)에 예정된 발표. 표에 없는 날은 빈 목록."""
    key = pd.Timestamp(day).date().isoformat()
    override = read_us_calendar(storage)
    if key in override:
        return list(override[key])
    return list(_BY_DAY.get(key, []))


def covered(day, storage=None):
    """그 날짜가 일정표가 다루는 기간 안인가. 밖이면 '발표 없음'이 아니라 '모름'이다."""
    key = pd.Timestamp(day).date().isoformat()
    override = read_us_calendar(storage)
    low, high = COVERAGE
    if override:
        low, high = min(low, min(override)), max(high, max(override))
    return low <= key <= high


def korea_event_flags(prediction_date, storage=None, lookback_days=4):
    """한국 예측일 기준 표시.

    미국 발표는 한국 시간 밤이므로 직전 며칠 안의 발표를 본다. 주말·연휴를 건너뛰기 위해
    달력일로 최대 4일까지 훑는다(금요일 발표 → 월요일 예측을 잡기 위해서다).
    """
    day = pd.Timestamp(prediction_date).date()
    flags = []
    for back in range(1, lookback_days + 1):
        past = day - timedelta(days=back)
        for event in us_events_on(past, storage):
            label = f"미국지표:{EVENT_LABELS.get(event, event)}"
            if label not in flags:
                flags.append(label)
    return flags


def pending_earnings_note(storage=None):
    """공식 공지가 없는 실적 목록. CSV 로 확정 날짜가 들어오면 목록에서 빠진다."""
    override = read_us_calendar(storage)
    confirmed = {e for events in override.values() for e in events}
    return {k: v for k, v in PENDING_EARNINGS.items() if k not in confirmed}


def upcoming_us_events(today, storage=None, days=10):
    """앞으로 며칠 안의 발표 예정. 보고서에 '이번 주에 무엇이 있는지' 적는 데 쓴다."""
    start = pd.Timestamp(today).date()
    out = []
    for offset in range(0, days + 1):
        day = start + timedelta(days=offset)
        for event in us_events_on(day, storage):
            out.append({"date": day.isoformat(), "event": event,
                        "label": EVENT_LABELS.get(event, event)})
    return out
