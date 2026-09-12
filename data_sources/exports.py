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

# 관세청은 한 번에 1년 이내만 조회할 수 있다. 넘기면 서버가 resultCode 99 로 거부한다
# ("시작과 종료의 조회기간은 1년이내 기간만 가능합니다"). 2026-09-12 Actions 실행이 30개월을
# 한 번에 요청해 이 오류로 관세청 계열이 통째로 꺼져 있었다. 그래서 기간을 창으로 나눠 부른다.
# 창 하나는 12개월(시작~종료 11개월 차이)이라 한도 해석이 어느 쪽이든 안전하다.
CUSTOMS_MAX_MONTHS = 12


class CustomsRejected(RuntimeError):
    """서버가 정상 응답했지만 요청을 거부했다(resultCode != 00). 재시도해도 결과가 같다."""


class CustomsEmpty(ValueError):
    """서버가 응답했지만 그 기간에 월별 수출액이 없다(집계 이전이거나 보존 기간 밖)."""


def month_windows(start, end, span=CUSTOMS_MAX_MONTHS):
    """[start, end] 를 span 개월 이하의 겹치지 않는 창으로 나눈다 → [(strtYymm, endYymm)].

    관세청의 1년 한도 때문이다. 창은 월 단위이고 마지막 창만 짧을 수 있다.
    """
    if span < 1:
        raise ValueError(f'창 길이는 1개월 이상이어야 합니다: {span}')
    first = pd.Timestamp(start).to_period('M')
    last = pd.Timestamp(end).to_period('M')
    if last < first:
        raise ValueError(f'종료가 시작보다 빠릅니다: {first} > {last}')
    windows, cursor = [], first
    while cursor <= last:
        stop = min(cursor + (span - 1), last)
        windows.append((cursor.strftime('%Y%m'), stop.strftime('%Y%m')))
        cursor = stop + 1
    return windows




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
        raise CustomsRejected(f'관세청 API 오류 {code}: {message}')
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
        raise CustomsEmpty('관세청 응답에 월별 수출액이 없습니다.')
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
                # 서버가 응답해 거부했거나 자료가 없다고 답한 것은 재시도해도 같다. 기다리지 않는다.
                settled = isinstance(exc, (CustomsRejected, CustomsEmpty))
                last = attempt == retries - 1
                if registered or settled or last:
                    failures.append(f'{label} 키: {detail}')
                    break            # 키 형태 문제면 재시도해도 같다. 다음 형태로 넘어간다.
                time.sleep(3 * (2 ** attempt) + random.uniform(0, 3))
    # 실패 종류에 맞는 설명을 붙인다. 같은 차단이 타임아웃으로도, 인증 오류로도 나타나므로
    # 한 가지 설명을 고정해 두면 엉뚱한 안내가 된다(2026-09-11 실제로 그랬다).
    joined = ' / '.join(failures)
    if all('월별 수출액이 없' in f for f in failures):
        raise CustomsEmpty(f'관세청에 {start_text}~{end_text} HS {hs} 자료가 없습니다.')
    location = f'HS {hs}, {start_text}~{end_text}, {key_fingerprint(key)}'
    cache_note = ('보관본(macro_history/customs_exports.csv)이 있으면 그것을 쓰고, 보관본 갱신은 '
                  '한국에서 Colab 전체 실행으로 한다.')
    if 'NOT_REGISTERED' in joined or 'SERVICE_KEY' in joined:
        cause = ('data.go.kr 이 해외 IP 를 SERVICE_KEY_IS_NOT_REGISTERED 로 거부한 것으로 보인다 '
                 '(2026-09-11 확인: 같은 키가 한국에서는 resultCode 00). 지문이 포털의 키와 다르면 '
                 'Secrets 를 확인하라. '
                 'GitHub Actions 는 미국에서 돌고 한국 정부 API 는 해외 IP 에서 막히므로 '
                 '이 실패는 예상된 것이며, ') + cache_note
    elif 'Timeout' in joined or 'timed out' in joined or 'URLError' in joined:
        cause = ('연결이 되지 않았다(해외 IP 차단이나 일시적 장애). '
                 'GitHub Actions 는 미국에서 돌고 한국 정부 API 는 해외 IP 에서 막히므로 '
                 '이 실패는 예상된 것이며, ') + cache_note
    elif '관세청 API 오류' in joined:
        # 서버가 XML 로 답했다 = 연결도 키도 통과했다. IP 차단이 아니라 요청 자체가 잘못된 것이다.
        # 이때 '해외 IP 차단'이라고 적으면 진짜 원인을 덮는다(2026-09-12 실제로 그랬다: 30개월을
        # 한 번에 요청해 resultCode 99 가 났는데 차단으로 기록됐다).
        cause = ('관세청이 요청을 거부했다 — 서버가 정상 응답했으므로 연결과 인증키에는 문제가 없다. '
                 '위 메시지가 가리키는 요청 조건을 고쳐야 한다(조회 기간은 1년 이내여야 하며 '
                 f'이 호출은 {CUSTOMS_MAX_MONTHS}개월 창으로 나눠 보낸다). ') + cache_note
    else:
        cause = '응답을 해석할 수 없었다. ' + cache_note
    raise RuntimeError(f'관세청 조회 실패({location}) — {joined}. {cause}')


def fetch_customs_exports(start, end, key, hs_codes=CUSTOMS_HS, retries=3,
                          span=CUSTOMS_MAX_MONTHS):
    """반도체 월별 수출액(달러). HS 대분류별로 조회해 합산한다.

    관세청의 1년 한도 때문에 기간을 span 개월 창으로 나눠 여러 번 부르고 이어 붙인다. 자료가 없다고
    답한 창(옛 기간)은 건너뛰되, 한 HS 의 모든 창이 비면 그 사실을 그대로 알린다 — 조용히 빈 계열을
    돌려주면 아래 reconcile 이 '겹치는 달이 없다'는 엉뚱한 이유로 넘어간다.
    """
    windows = month_windows(start, end, span)
    total, skipped = None, []
    for hs in hs_codes:
        parts = []
        for start_text, end_text in windows:
            try:
                parts.append(_customs_request(hs, start_text, end_text, key, retries)
                             .set_index('month')['value'])
            except CustomsEmpty:
                skipped.append(f'{hs} {start_text}~{end_text}')
        if not parts:
            raise RuntimeError(f'관세청에 HS {hs} 자료가 없습니다'
                               f'({windows[0][0]}~{windows[-1][1]}, 창 {len(windows)}개 모두 빔).')
        series = pd.concat(parts)
        # 창은 겹치지 않으므로 중복이 있으면 안 된다. 그래도 생기면 더하지 말고 하나만 남긴다.
        series = series[~series.index.duplicated(keep='last')].sort_index()
        total = series if total is None else total.add(series, fill_value=0)
    if skipped:
        print(f'  관세청: 자료가 없는 구간은 건너뜁니다 — {", ".join(skipped)}', flush=True)
    return pd.DataFrame({'month': total.index, 'value': total.to_numpy()})




def reconcile_customs(kosis, customs, tolerance=0.15, min_overlap=6):
    """관세청 계열이 KOSIS 확정치와 **같은 크기**인지 확인한다.

    단위 오류(1000배)를 잡는 검사다. 겹치는 달의 비율 중앙값이 1에서 tolerance 이상 벗어나면
    같은 크기가 아니다. 반환: (같은 크기인가, 진단 정보)

    주의: 이것으로 채택을 결정하지 않는다. HS 8541+8542 는 KOSIS '반도체'보다 품목 범위가 좁아
    크기가 **정상적으로** 다르다(2026-09-12 한국에서 직접 확인: 11개월 배율 0.76~0.86,
    중앙값 0.807). 크기가 다르다고 버리면 KOSIS 가 아직 없는 달을 영영 채우지 못한다.
    실제 채택은 customs_scale() 이 정한다 — 배율이 안정적이면 환산해서 쓴다.
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




# 환산 배율을 다시 맞출 때 보는 최근 겹침 개월 수. 배율이 천천히 흐르므로(2025-09 0.838 →
# 2026-06 0.757) 전 구간 중앙값을 쓰면 최근 달에서 6%쯤 틀린다. 최근 구간만 보면 따라간다.
CUSTOMS_SCALE_WINDOW = 6
CUSTOMS_SCALE_TOLERANCE = 0.10       # 그 구간 안에서 배율이 이만큼 넘게 흔들리면 쓰지 않는다


def customs_scale(kosis, customs, window=CUSTOMS_SCALE_WINDOW,
                  tolerance=CUSTOMS_SCALE_TOLERANCE, min_overlap=6):
    """관세청 값을 KOSIS 기준으로 환산할 배수와 진단. 반환: (쓸 수 있는가, 배수, 진단)

    HS 8541+8542 는 KOSIS '반도체'보다 범위가 좁아 계통적으로 작다. 배율이 일정하면 그만큼
    곱해서 KOSIS 기준으로 되돌릴 수 있다. 배율이 흔들리면 관계가 불안정한 것이므로 쓰지 않는다.

    배율은 **겹치는 과거 달**에서만 구한다(양쪽 다 있는 달). 그것을 KOSIS 가 아직 없는 미래 달에
    적용하므로 미래 정보를 당겨 쓰지 않는다.
    """
    k = normalize_monthly(kosis).set_index('month')['value']
    c = normalize_monthly(customs).set_index('month')['value']
    overlap = k.index.intersection(c.index)
    info = {'overlap': int(len(overlap)), 'window': int(window), 'scale': None,
            'spread': None, 'ratios': {}}
    if len(overlap) < min_overlap:
        info['reason'] = f'겹치는 달이 {len(overlap)}개뿐이라 배율을 정할 수 없습니다(최소 {min_overlap}개).'
        return False, 1.0, info
    ratio = (k.loc[overlap] / c.loc[overlap]).replace([np.inf, -np.inf], np.nan).dropna()
    ratio = ratio[ratio > 0].sort_index()
    if len(ratio) < min_overlap:
        info['reason'] = f'쓸 수 있는 배율이 {len(ratio)}개뿐입니다.'
        return False, 1.0, info
    recent = ratio.tail(window)
    scale = float(recent.median())
    # 그 구간 안에서 배율이 얼마나 흔들렸나. 중앙값 대비 최대 이탈로 잰다.
    spread = float((recent / scale - 1).abs().max())
    info.update(scale=scale, spread=spread,
                ratios={pd.Timestamp(m).strftime('%Y-%m'): round(float(v), 4) for m, v in recent.items()})
    if not np.isfinite(scale) or scale <= 0:
        info['reason'] = '배율을 계산할 수 없습니다.'
        return False, 1.0, info
    if spread > tolerance:
        info['reason'] = (f'최근 {len(recent)}개월 배율이 중앙값 {scale:.3f} 대비 최대 {spread:.1%} '
                          f'흔들립니다(허용 {tolerance:.0%}) — 관계가 불안정해 환산하지 않습니다.')
        return False, 1.0, info
    return True, scale, info


def merge_customs_exports(kosis, customs, scale=1.0):
    """KOSIS 확정치를 그대로 두고, KOSIS에 아직 없는 달만 관세청 값으로 채운다.

    scale 은 customs_scale() 이 정한 환산 배수다. 관세청 원값에 곱해 KOSIS 기준으로 맞춘 뒤 넣는다.
    곱하지 않고 넣으면 계열에 단차가 생겨 YoY·MoM 특징이 망가진다(2026-08 기준 20%).
    """
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError(f'환산 배수가 올바르지 않습니다: {scale!r}')
    k = normalize_monthly(kosis).set_index('month')['value']
    c = normalize_monthly(customs).set_index('month')['value'] * float(scale)
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
