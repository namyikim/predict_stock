"""관세청 품목별 수출입실적(공공데이터포털)과 조업일수 기준 일평균."""
import hashlib
import io
import json
import os
import re
from pathlib import Path
import random
import time
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd
from data_sources._common import *  # noqa: F401,F403




# ---------------------------------------------------------------------------
# 일평균 수출: 월 합계 / 조업일수(주중일 - 한국 휴장일 근사)
# ---------------------------------------------------------------------------
def korean_working_days(month_start):
    """그 달의 조업일수 근사. 관세청 조업일수(토요일 0.5일)와 정확히 같지는 않지만 달력 효과의 대부분을 없앤다."""
    month_start = pd.Timestamp(month_start).normalize().replace(day=1)
    month_end = month_start + pd.offsets.MonthEnd(0)
    days = pd.bdate_range(month_start, month_end)
    try:
        import exchange_calendars as xc
        cal = xc.get_calendar('XKRX')
        sessions = cal.sessions_in_range(month_start.strftime('%Y-%m-%d'), month_end.strftime('%Y-%m-%d'))
        return float(len(sessions))
    except Exception:
        return float(len(days))




def daily_average(monthly_frame):
    """DataFrame(month, value) → DataFrame(month, value=일평균). 조업일수로 나눈다."""
    out = normalize_monthly(monthly_frame).copy()
    out['value'] = [v / max(korean_working_days(m), 1.0) for m, v in zip(out['month'], out['value'])]
    return out




# ---------------------------------------------------------------------------
# 관세청 품목별 수출입실적 (공공데이터포털 /1220000/Itemtrade/getItemtradeList)
# ---------------------------------------------------------------------------
# KOSIS 품목별 월 확정치는 관세청 원천보다 2~5주 늦다. 같은 숫자를 원천에서 바로 받으면 그만큼
# 나우캐스트를 앞당길 수 있다. 응답은 HS 10자리로 잘게 나오므로 8541(개별소자)·8542(집적회로)를
# 각각 합산한다. expDlr 단위는 달러다(2026-06 디램 111.7억 달러로 확인).
CUSTOMS_URL = 'https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList'


CUSTOMS_HS = ('8541', '8542')       # 반도체: 개별소자 + 집적회로




def data_go_kr_key():
    key = os.environ.get('DATA_GO_KR_KEY')
    if not key:
        try:
            from google.colab import userdata
            key = userdata.get('DATA_GO_KR_KEY')
        except Exception:
            pass
    if key and '%' in key:
        from urllib.parse import unquote
        key = unquote(key)          # Encoding용 키를 넣어도 동작하게 한다
    return key




def parse_customs_xml(text):
    """<item> 목록 → DataFrame(month, value=수출 달러). HS 세부 코드를 월별로 합산한다."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(text)
    message = root.findtext('.//resultMsg') or ''
    code = root.findtext('.//resultCode')
    if code not in (None, '00', '0'):
        raise RuntimeError(f'관세청 API 오류 {code}: {message}')
    rows = []
    for item in root.iter('item'):
        period = (item.findtext('year') or '').strip()
        amount = (item.findtext('expDlr') or '').strip()
        if not period or not amount:
            continue
        # 'year'에는 2026.06(월별)과 2026(연 합계)이 섞여 온다. 월별만 쓴다.
        if not re.fullmatch(r'\d{4}[.\-/]\d{2}', period):
            continue
        try:
            rows.append({'month': period.replace('.', '-').replace('/', '-'), 'value': float(amount.replace(',', ''))})
        except ValueError:
            continue
    if not rows:
        raise ValueError('관세청 응답에 월별 수출액이 없습니다.')
    frame = pd.DataFrame(rows).groupby('month', as_index=False)['value'].sum()
    return normalize_monthly(frame)




def fetch_customs_exports(start, end, key, hs_codes=CUSTOMS_HS, retries=3):
    """반도체 월별 수출액(달러). HS 대분류별로 조회해 합산한다."""
    start_text = pd.Timestamp(start).strftime('%Y%m')
    end_text = pd.Timestamp(end).strftime('%Y%m')
    total = None
    for hs in hs_codes:
        query = urlencode({'serviceKey': key, 'strtYymm': start_text, 'endYymm': end_text, 'hsSgn': hs})
        for attempt in range(retries):
            try:
                with open_url(f'{CUSTOMS_URL}?{query}', timeout=90, accept='application/xml, text/xml, */*') as response:
                    frame = parse_customs_xml(response.read().decode('utf-8'))
                break
            except Exception as exc:
                detail = f'{type(exc).__name__} {getattr(exc, "code", "")}'.strip()
                if attempt == retries - 1:
                    raise RuntimeError(f'관세청 조회 실패({detail}, HS {hs}). 키·활용신청 상태를 확인하세요.') from None
                time.sleep(3 * (2 ** attempt) + random.uniform(0, 3))
        series = frame.set_index('month')['value']
        total = series if total is None else total.add(series, fill_value=0)
    return pd.DataFrame({'month': total.index, 'value': total.to_numpy()})




def reconcile_customs(kosis, customs, tolerance=0.15, min_overlap=6):
    """관세청 계열이 KOSIS 확정치와 같은 크기인지 확인한다.

    품목 정의나 단위가 다르면 조용히 1000배 틀린 값이 들어간다. 겹치는 달의 비율 중앙값이 1에서
    tolerance 이상 벗어나면 쓰지 않는다. 반환: (사용 가능 여부, 진단 정보)
    """
    k = normalize_monthly(kosis).set_index('month')['value']
    c = normalize_monthly(customs).set_index('month')['value']
    overlap = k.index.intersection(c.index)
    info = {'overlap': int(len(overlap)), 'ratio_median': None, 'ratio_min': None, 'ratio_max': None}
    if len(overlap) < min_overlap:
        info['reason'] = f'겹치는 달이 {len(overlap)}개뿐이라 검증할 수 없습니다.'
        return False, info
    ratio = (c.loc[overlap] / k.loc[overlap]).replace([np.inf, -np.inf], np.nan).dropna()
    info.update(ratio_median=float(ratio.median()), ratio_min=float(ratio.min()), ratio_max=float(ratio.max()))
    if not (1 - tolerance) <= info['ratio_median'] <= (1 + tolerance):
        info['reason'] = (f"KOSIS 대비 배율 중앙값이 {info['ratio_median']:.3f}로 1에서 많이 벗어납니다"
                          ' — 품목 정의나 단위가 다를 수 있어 쓰지 않습니다.')
        return False, info
    return True, info




def merge_customs_exports(kosis, customs):
    """KOSIS 확정치를 그대로 두고, KOSIS에 아직 없는 달만 관세청 값으로 채운다."""
    k = normalize_monthly(kosis).set_index('month')['value']
    c = normalize_monthly(customs).set_index('month')['value']
    added = [m for m in c.index if m not in k.index]
    merged = pd.concat([k, c.loc[added]]).sort_index()
    return (pd.DataFrame({'month': merged.index, 'value': merged.to_numpy()}),
            [pd.Timestamp(m).strftime('%Y-%m') for m in added])
