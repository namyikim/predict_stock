"""D램 현물 가격 — DRAMeXchange 첫 페이지의 당일 스냅샷.

삼성전자·SK하이닉스의 영업이익은 D램 가격에 가장 직접 좌우된다. KOSIS 반도체 수출액은
'가격 × 물량'의 결과를 월+2 지연으로 보여 주지만, 현물가는 매일 나온다. 그래서 8절 분기 이익
나우캐스트의 후보 특징이다.

주의 셋.
1) 첫 페이지는 당일 값만 준다. 이력은 유료다. 그래서 매일 받아 저장소에 누적한다(TSMC 와 같은
   방식). 검증에 쓸 만한 이력이 되려면 몇 달이 걸린다 — 그 전에는 표시만 하고 특징으로 쓰지 않는다.
2) 현물가와 삼성·하이닉스의 고정거래가는 다르다. 고정가가 현물가를 1~2개월 뒤따르는 경향이
   있지만 그 관계도 측정 대상이지 가정이 아니다.
3) 업계 모두가 매일 보는 공개 지표라 시장이 이미 반영한다. 도움이 된다면 일별 방향이 아니라
   분기 이익에서일 것이고, 그것도 쌍체 비교로 재 봐야 안다.

HTML 표 구조(2026-09 확인): <tbody id="tb_NationalDramSpotPrice"> 안에 행마다
Item | Daily High | Daily Low | Session High | Session Low | Session Average | Session Change | History.
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from data_sources._common import *  # noqa: F401,F403

DRAMEXCHANGE_URL = 'https://www.dramexchange.com/'
KST = timezone(timedelta(hours=9))
# 추적할 품목. 첫 페이지 표의 Item 문자열로 찾는다. 이름은 CSV 열로 쓰므로 짧게 둔다.
DRAM_ITEMS = {
    'ddr5_16gb': 'DDR5 16Gb (2Gx8) 4800/5600',
    'ddr4_16gb': 'DDR4 16Gb (2Gx8) 3200',
    'ddr4_8gb': 'DDR4 8Gb (1Gx8) 3200',
}


def parse_dramexchange_spot(html_text, items=DRAM_ITEMS):
    """첫 페이지 HTML → {열이름: 세션 평균(USD)}. 못 찾은 품목은 빠진다."""
    body_match = re.search(r'<tbody id="tb_NationalDramSpotPrice">(.*?)</tbody>', html_text, re.S)
    if not body_match:
        raise ValueError('DRAMeXchange 첫 페이지에서 현물가 표(tb_NationalDramSpotPrice)를 찾지 못했습니다.')
    rows = re.findall(r'<tr>(.*?)</tr>', body_match.group(1), re.S)
    out = {}
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        if len(cells) < 6:
            continue
        label = re.sub(r'<[^>]+>', ' ', cells[0])
        label = re.sub(r'\s+', ' ', label).strip()
        for key, wanted in items.items():
            if label.startswith(wanted):
                # 열 순서: Item, Daily High, Daily Low, Session High, Session Low, Session Average, ...
                raw = re.sub(r'<[^>]+>', '', cells[5]).strip().replace(',', '')
                try:
                    value = float(raw)
                except ValueError:
                    continue
                if value > 0:
                    out[key] = value
    if not out:
        raise ValueError('현물가 표는 있으나 추적 품목을 하나도 읽지 못했습니다. 품목 이름이 바뀌었을 수 있습니다.')
    return out


def fetch_dram_spot(retries=3):
    """오늘 스냅샷 한 행. DataFrame(date, ddr5_16gb, ddr4_16gb, ddr4_8gb)."""
    for attempt in range(retries):
        try:
            with open_url(DRAMEXCHANGE_URL, timeout=60, accept='text/html') as response:
                html_text = response.read().decode('utf-8', errors='ignore')
            values = parse_dramexchange_spot(html_text)
            today = datetime.now(KST).date()
            return pd.DataFrame([{'date': pd.Timestamp(today), **values}])
        except Exception as exc:
            detail = f'{type(exc).__name__} {getattr(exc, "code", "")}'.strip()
            if attempt == retries - 1:
                raise RuntimeError(f'DRAMeXchange 조회 실패({detail}).') from None
            time.sleep(3 * (2 ** attempt) + random.uniform(0, 3))


def load_dram_spot(storage, fallback_dir=None, fetch=True):
    """(DataFrame(date, 품목열...), info). 보관본에 오늘 값을 덧붙여 누적한다.

    같은 날짜가 이미 있으면 새 값으로 바꾼다(장중에 값이 바뀌므로 마지막 조회를 남긴다).
    """
    storage = Path(storage)
    local = storage / 'macro_inputs' / 'dram_spot.csv'
    fallback = Path(fallback_dir) / 'dram_spot.csv' if fallback_dir else None

    def read(path):
        frame = pd.read_csv(path, dtype=str)
        frame['date'] = pd.to_datetime(frame['date']).dt.normalize()
        for column in frame.columns:
            if column != 'date':
                frame[column] = pd.to_numeric(frame[column], errors='coerce')
        return frame.sort_values('date').reset_index(drop=True)

    if local.exists():
        frame, source, error = read(local), 'user_csv', None
    else:
        base = read(fallback) if fallback and fallback.exists() else None
        fresh, error = None, None
        if fetch:
            try:
                fresh = fetch_dram_spot()
            except Exception as exc:
                error = str(exc)
        if fresh is None and base is None:
            raise RuntimeError('D램 현물가 자료가 없습니다. ' + (error or '조회를 하지 않았습니다.'))
        if fresh is None:
            frame, source = base, 'last_successful_fetch'
        elif base is None:
            frame, source = fresh, 'DRAMEXCHANGE'
        else:
            frame = (pd.concat([base, fresh]).drop_duplicates('date', keep='last')
                     .sort_values('date').reset_index(drop=True))
            source = 'DRAMEXCHANGE+cache'
    info = {'source': source, 'fresh': source.startswith(('DRAMEXCHANGE', 'user_csv')),
            'fetch_error': error,
            'first': frame['date'].min().date().isoformat(),
            'last': frame['date'].max().date().isoformat(), 'rows': int(len(frame)),
            'items': [c for c in frame.columns if c != 'date'],
            'note': 'DRAMeXchange 첫 페이지 세션 평균(USD). 매일 누적. 이력이 짧으면 표시만 하고 특징으로 쓰지 않는다'}
    return frame, info


def dram_spot_summary(frame, item='ddr5_16gb'):
    """보고서용 요약: 최신값, 1주·1개월 변화, 보유 일수. 이력이 짧으면 변화는 None."""
    if frame is None or frame.empty or item not in frame.columns:
        return None
    series = frame.set_index('date')[item].dropna()
    if series.empty:
        return None
    last_date, last = series.index[-1], float(series.iloc[-1])

    def change(days):
        cutoff = last_date - pd.Timedelta(days=days)
        earlier = series[series.index <= cutoff]
        if earlier.empty:
            return None
        return last / float(earlier.iloc[-1]) - 1

    return {'item': item, 'date': last_date.date().isoformat(), 'value': last,
            'change_7d': change(7), 'change_30d': change(30), 'days': int(len(series))}
