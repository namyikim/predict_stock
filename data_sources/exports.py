"""관세청 품목별 수출입실적(공공데이터포털)과 조업일수 기준 일평균."""
import hashlib
import io
import json
import os
import re
from pathlib import Path
import random
import time
from urllib.parse import quote, urlencode
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
    """저장된 키를 그대로 돌려준다. 인코딩/디코딩 변환은 key_variants 가 맡는다."""
    key = os.environ.get('DATA_GO_KR_KEY')
    if not key:
        try:
            from google.colab import userdata
            key = userdata.get('DATA_GO_KR_KEY')
        except Exception:
            pass
    return key.strip() if key else key


def key_variants(key):
    """시도할 serviceKey 문자열들. 이미 URL 에 넣을 최종 형태이고, 중복은 뺀다.

    data.go.kr 안내: "API 환경 또는 호출 조건에 따라 인증키가 적용되는 방식이 다를 수 있습니다.
    포털에서 제공되는 Encoding/Decoding 된 인증키를 적용하면서 구동되는 키를 사용하시기 바랍니다."
    어느 쪽이 맞는지는 호출해 봐야 안다.

    두 형태는 실제로 서버에 다르게 도착한다.
      as_is: 포털의 인코딩 키를 그대로 붙인다(%2B 를 %2B 로). 2026-09-11 실제 호출로 확인한 결과
             관세청 품목별 수출입실적은 이쪽이 맞다(resultCode 00). 그래서 먼저 시도한다.
      once : 디코딩한 뒤 한 번 인코딩. 대부분의 data.go.kr API 가 이쪽이고, 디코딩 키를 저장한
             경우에는 이것만 생긴다.
    """
    from urllib.parse import quote, unquote
    if not key:
        return []
    out, seen = [], set()
    for label, text in (('as_is', key), ('once', quote(unquote(key), safe=''))):
        if text and text not in seen:
            seen.add(text)
            out.append((label, text))
    return out




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




def _customs_request(hs, start_text, end_text, key, retries=3):
    """HS 한 건 조회. 인증키 형태를 바꿔 가며 시도한다(어느 쪽이 맞는지는 호출해 봐야 안다)."""
    variants = key_variants(key)
    if not variants:
        raise RuntimeError('DATA_GO_KR_KEY 가 비어 있습니다.')
    failures = []
    rest = urlencode({'strtYymm': start_text, 'endYymm': end_text, 'hsSgn': hs})
    for label, service_key in variants:
        query = f'serviceKey={service_key}&{rest}'
        for attempt in range(retries):
            try:
                with open_url(f'{CUSTOMS_URL}?{query}', timeout=90,
                              accept='application/xml, text/xml, */*') as response:
                    return parse_customs_xml(response.read().decode('utf-8'))
            except Exception as exc:
                detail = error_detail(exc)
                registered = 'NOT_REGISTERED' in str(exc) or 'SERVICE_KEY' in str(exc)
                last = attempt == retries - 1
                if registered or last:
                    failures.append(f'{label} 키: {detail}')
                    break            # 키 형태 문제면 재시도해도 같다. 다음 형태로 넘어간다.
                time.sleep(3 * (2 ** attempt) + random.uniform(0, 3))
    raise RuntimeError(
        f'관세청 조회 실패(HS {hs}) — ' + ' / '.join(failures) + '. '
        'SERVICE_KEY_IS_NOT_REGISTERED 가 두 형태 모두에서 나오면 활용신청 승인 상태와 '
        '인증키 재발급을 확인하세요. URLError 면 해외 IP 차단일 수 있습니다.')


def fetch_customs_exports(start, end, key, hs_codes=CUSTOMS_HS, retries=3):
    """반도체 월별 수출액(달러). HS 대분류별로 조회해 합산한다."""
    start_text = pd.Timestamp(start).strftime('%Y%m')
    end_text = pd.Timestamp(end).strftime('%Y%m')
    total = None
    for hs in hs_codes:
        frame = _customs_request(hs, start_text, end_text, key, retries)
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


# ---------------------------------------------------------------------------
# TSMC 월매출 — 한국 수출 통계보다 빠른 반도체 수요 지표
# ---------------------------------------------------------------------------
# TSMC 는 매달 10일 전후에 전월 매출을 공시한다(대만 증권거래소 공시 의무). KOSIS 반도체 수출
# 확정치는 2~5주 늦으므로, 분기 이익 나우캐스트에 한 달 가까이 앞당겨 넣을 수 있다.
# 다만 '다음 날 주가를 맞히는가'가 아니라 '분기 이익을 더 잘 맞히는가'로만 검증한다.
#
# 대만거래소가 키 없이 쓸 수 있는 공식 OpenAPI 를 제공한다(openapi.twse.com.tw). 응답은 최근
# 공시월 스냅샷이지만 한 행에 당월·전월·전년동월이 함께 있어 한 번 조회로 세 달치를 얻는다.
# 매달 받아 저장소에 누적하면 이력이 알아서 쌓인다. 날짜는 민국 연호라 서기로 바꾼다(11507 → 2026-07).
TWSE_REVENUE_URL = 'https://openapi.twse.com.tw/v1/opendata/t187ap05_L'
TSMC_STOCK_CODE = '2330'
TSMC_RELEASE_DAY = 10        # 매달 10일 전후 공시. 그 전에는 전월 값을 모른다.


def roc_month_to_date(text):
    """민국 연월(11507) → Timestamp(2026-07-01). 형식이 다르면 ValueError."""
    digits = re.sub(r'[^0-9]', '', str(text))
    if len(digits) not in (5, 6):
        raise ValueError(f'민국 연월 형식이 아닙니다: {text!r}')
    year, month = int(digits[:-2]) + 1911, int(digits[-2:])
    if not 1 <= month <= 12:
        raise ValueError(f'월이 범위를 벗어납니다: {text!r}')
    # 서기 표기('2026-07' → 3937년)를 민국으로 잘못 읽지 않도록 결과 연도를 확인한다.
    if not 1990 <= year <= 2100:
        raise ValueError(f'민국 연월로 보기 어렵습니다(변환 결과 {year}년): {text!r}')
    return pd.Timestamp(year=year, month=month, day=1)


def parse_twse_revenue(payload, stock_code=TSMC_STOCK_CODE):
    """TWSE OpenAPI 응답 → DataFrame(month, value). 한 행에서 세 달치를 뽑는다.

    단위는 신대만달러 천 원이고, 모델은 비율만 쓰므로 그대로 둔다.
    """
    rows = [r for r in payload if str(r.get('公司代號', '')).strip() == stock_code]
    if not rows:
        raise ValueError(f'{stock_code} 행이 응답에 없습니다.')
    out = {}
    for row in rows:
        month = roc_month_to_date(row['資料年月'])
        for key, offset in (('營業收入-當月營收', 0), ('營業收入-上月營收', -1),
                            ('營業收入-去年當月營收', -12)):
            raw = str(row.get(key, '')).replace(',', '').strip()
            if raw in ('', '-'):
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            if value > 0:
                out[month + pd.DateOffset(months=offset)] = value
    if not out:
        raise ValueError('응답에서 매출 값을 찾지 못했습니다.')
    frame = pd.DataFrame({'month': list(out), 'value': list(out.values())})
    return normalize_monthly(frame)


def fetch_tsmc_revenue(stock_code=TSMC_STOCK_CODE, retries=3):
    """대만거래소 OpenAPI 에서 최근 공시월을 받는다. 인증키가 필요 없다."""
    for attempt in range(retries):
        try:
            with open_url(TWSE_REVENUE_URL, timeout=60, accept='application/json') as response:
                payload = json.loads(response.read().decode('utf-8-sig'))
            return parse_twse_revenue(payload, stock_code)
        except Exception as exc:
            detail = error_detail(exc)
            if attempt == retries - 1:
                raise RuntimeError(f'TWSE 월매출 조회 실패({detail}). '
                                   'macro_inputs/tsmc_revenue.csv 로 대신할 수 있습니다.') from None
            time.sleep(3 * (2 ** attempt) + random.uniform(0, 3))


def load_tsmc_revenue(storage, fallback_dir=None, fetch=True):
    """(DataFrame(month,value), info).

    우선순위: 사용자 CSV > (보관본 + API 로 받은 최근분 병합) > 보관본 > API.
    API 는 최근 공시월만 주므로 보관본과 합쳐야 이력이 이어진다. 매달 실행이 조금씩 누적한다.
    """
    storage = Path(storage)
    local = storage / 'macro_inputs' / 'tsmc_revenue.csv'
    fallback = Path(fallback_dir) / 'tsmc_revenue.csv' if fallback_dir else None
    if local.exists():
        frame = normalize_monthly(pd.read_csv(local, dtype=str))
        source, error = 'user_csv', None
    else:
        base = normalize_monthly(pd.read_csv(fallback, dtype=str)) if (fallback and fallback.exists()) else None
        fresh, error = None, None
        if fetch:
            try:
                fresh = fetch_tsmc_revenue()
            except Exception as exc:
                error = str(exc)
        if fresh is None and base is None:
            raise RuntimeError('TSMC 매출 자료가 없습니다. ' + (error or 'API 조회를 하지 않았습니다.'))
        if fresh is None:
            frame, source = base, 'last_successful_fetch'
        elif base is None:
            frame, source = fresh, 'TWSE_API'
        else:
            merged = pd.concat([base, fresh]).drop_duplicates('month', keep='last').sort_values('month')
            frame, source = merged.reset_index(drop=True), 'TWSE_API+cache'
    info = {'source': source, 'fresh': source.startswith(('TWSE_API', 'user_csv')),
            'fetch_error': error if 'error' in dir() else None,
            'first': frame['month'].min().strftime('%Y-%m'),
            'last': frame['month'].max().strftime('%Y-%m'), 'rows': int(len(frame)),
            'release_day': TSMC_RELEASE_DAY,
            'note': 'TSMC 월매출(대만거래소 OpenAPI). 참조월+1개월 10일 이후에만 사용',
            'snapshot_hash': hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()[:20]}
    return frame, info


def tsmc_features(frame, quarters, months_used, release_day=TSMC_RELEASE_DAY):
    """분기 인덱스에 맞춘 TSMC 매출 특징.

    분기의 앞 k개월만 쓰는 것은 수출액과 같다. 다만 공시가 다음 달 10일이라, k번째 달 값은
    분기가 끝나기 전에는 못 볼 수도 있다. 그래서 '그 분기 시작 시점에 이미 공시된 달'까지만 센다.
    """
    monthly = normalize_monthly(frame).set_index('month')['value'].asfreq('MS')
    rows = {}
    for quarter in pd.PeriodIndex(quarters, freq='Q'):
        start = quarter.start_time
        picked = []
        for offset in range(months_used):
            month = start + pd.DateOffset(months=offset)
            visible_from = month + pd.DateOffset(months=1) + pd.Timedelta(days=release_day - 1)
            asof = start + pd.DateOffset(months=months_used) - pd.Timedelta(days=1)
            if visible_from <= asof and month in monthly.index and pd.notna(monthly.loc[month]):
                picked.append(float(monthly.loc[month]))
        rows[quarter] = np.mean(picked) if picked else np.nan
    series = pd.Series(rows, name='tsmc_rev_k')
    out = pd.DataFrame({'tsmc_rev_k': series})
    out['tsmc_yoy'] = series / series.shift(4) - 1
    out['tsmc_qoq'] = series / series.shift(1) - 1
    return out
