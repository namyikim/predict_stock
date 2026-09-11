"""공용: 요청 헤더(open_url), 월별·일별 정규화, 보관본 노후 판정."""
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




def normalize_monthly(frame):
    columns = ['month', 'value'] + [c for c in ('released_at', 'vintage', 'source') if c in frame]
    out = frame[columns].copy()
    months = out['month'].astype(str).str.replace(r'^(\d{4})[./](\d{1,2})$', r'\1-\2', regex=True)
    months = months.str.replace(r'^(\d{4})(\d{2})$', r'\1-\2', regex=True)
    out['month'] = pd.to_datetime(months, format='mixed', errors='raise').dt.to_period('M').dt.to_timestamp()
    out['value'] = pd.to_numeric(out['value'].astype(str).str.replace(',', ''), errors='coerce')
    keys = ['month']
    if 'released_at' in out:
        releases = []
        for value in out['released_at']:
            stamp = pd.Timestamp(value)
            if pd.isna(stamp) or stamp.tzinfo is None:
                raise ValueError('released_at에는 시간대가 포함된 정확한 발표/수정 시각이 필요합니다.')
            releases.append(stamp.tz_convert('UTC'))
        out['released_at'] = pd.to_datetime(releases, utc=True)
        earliest = (out['month'] + pd.offsets.MonthBegin(1)).dt.tz_localize('Asia/Seoul')
        if (out['released_at'] < earliest).any():
            raise ValueError('완결 월별 통계의 발표 시각이 해당 월 종료보다 빠릅니다.')
        keys.append('released_at')
    if out.duplicated(keys).any():
        raise ValueError('월별 통계에 같은 월이 중복됩니다. 하나의 지표/단위만 선택하세요.')
    out.loc[~np.isfinite(out.value) | (out.value <= 0), 'value'] = np.nan
    if out.value.notna().sum() == 0:
        raise ValueError('월별 통계에 유효한 양수 값이 없습니다.')
    return out.sort_values(keys).reset_index(drop=True)




def normalize_daily(frame):
    out = frame[['date', 'value']].copy()
    out['date'] = pd.to_datetime(out['date'].astype(str).str.replace(r'[^0-9]', '', regex=True),
                                 format='%Y%m%d', errors='raise').dt.normalize()
    out['value'] = pd.to_numeric(out['value'], errors='coerce')
    if out.duplicated('date').any():
        raise ValueError('일별 지수에 같은 날짜가 중복됩니다.')
    out = out.dropna(subset=['value']).sort_values('date').reset_index(drop=True)
    if out.empty:
        raise ValueError('일별 지수에 유효한 값이 없습니다.')
    return out




def cache_age_days(storage, names=None, now=None):
    """보관본이 예상 갱신 시점 이후 며칠 지났는지. {이름: 경과일}.

    보관본은 한국에서 돌린 실행이 갱신한다. 그걸 잊으면 100일 만료 규칙이 걸릴 때까지 낡은 값을
    조용히 쓰게 된다. 일별 자료는 마지막 관측일부터 세고, 발표 이력이 없는 월별 자료는 모델의
    시점 처리와 동일하게 기준월+2개월을 공개 가능 시점으로 보고 그날부터 센다.
    """
    names = names or ('leading_cycle', 'semiconductor_exports', 'news_sentiment', 'term_spread')
    now = pd.Timestamp(now or pd.Timestamp.now(tz='Asia/Seoul').tz_localize(None)).normalize()
    out = {}
    for name in names:
        path = Path(storage) / 'macro_fallback' / f'{name}.csv'
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, dtype=str)
            column = 'month' if 'month' in frame.columns else 'date'
            last = pd.to_datetime(frame[column].astype(str), format='mixed', errors='coerce').max()
        except Exception:
            continue
        if pd.notna(last):
            anchor = pd.Timestamp(last).normalize()
            if column == 'month':
                anchor = anchor.to_period('M').to_timestamp() + pd.offsets.MonthBegin(2)
            out[name] = max(0, int((now - anchor).days))
    return out




def stale_cache_note(ages, warn_after=45, expire_after=100):
    """경고 문구. 경고할 것이 없으면 빈 문자열."""
    stale = {name: age for name, age in ages.items() if age >= warn_after}
    if not stale:
        return ''
    worst = max(stale.values())
    listed = ', '.join(f'{name} {age}일' for name, age in sorted(stale.items(), key=lambda x: -x[1]))
    tail = (' 만료(100일)가 가까워 곧 지표에서 빠집니다.' if worst >= expire_after - 20 else '')
    return (f'참고자료가 예상 공개 시점 이후 오래 갱신되지 않았습니다({listed}). 한국에서 Colab으로 '
            f'노트북을 한 번 실행해 확인하세요. 공식 통계 발표가 늦으면 같은 최신 관측치가 정상일 수 '
            f'있습니다.{tail}')




# 파이썬 기본 User-Agent(Python-urllib/3.x)는 CDN이 자동으로 막는 일이 흔하다. OECD가 Actions에서
# 403을 돌려준 것도 이 때문이었다(같은 요청이 브라우저·requests로는 통과). 모든 외부 요청에 붙인다.
USER_AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/126.0 Safari/537.36 predict_stock/1.0 (research; non-commercial)')




def urllib_request_with_agent(url, accept=None):
    from urllib.request import Request
    headers = {'User-Agent': USER_AGENT, 'Accept-Language': 'ko,en;q=0.8'}
    if accept:
        headers['Accept'] = accept
    return Request(url, headers=headers)




def open_url(url, timeout=60, accept=None):
    return urlopen(urllib_request_with_agent(url, accept), timeout=timeout)


def error_detail(exc):
    """예외를 한 줄로. URL 에 인증키가 들어가므로 URL 은 절대 넣지 않는다.

    URLError 는 종류만으로는 원인을 알 수 없다(DNS 실패·타임아웃·연결 거부·SSL 오류가 모두
    URLError 다). reason 까지 적어야 '해외 IP 차단'과 '키 문제'를 구분할 수 있다.
    """
    parts = [type(exc).__name__]
    code = getattr(exc, 'code', None)
    if code:
        parts.append(str(code))
    reason = getattr(exc, 'reason', None)
    if reason is not None and str(reason):
        text = str(reason)
        # reason 이 다른 예외를 품고 있으면 그 종류까지 남긴다.
        if not isinstance(reason, str):
            text = f'{type(reason).__name__}: {reason}'
        parts.append(text[:120])
    elif str(exc):
        # reason 이 없는 예외(우리가 만든 RuntimeError 등)는 메시지 자체가 원인이다.
        # 이것을 버리면 'SERVICE_KEY_IS_NOT_REGISTERED' 같은 결정적 단서가 사라진다.
        # 다만 예외 메시지에 URL 이 담기는 경우가 있고 URL 에는 인증키가 들어간다. 그래서
        # URL 처럼 보이는 토큰과 key 류 파라미터는 지운다(기존 테스트가 이 누출을 잡았다).
        parts.append(_scrub_secrets(str(exc))[:160])
    return ' '.join(parts)


_URLISH = re.compile(r'\b(?:https?://|www\.)\S+', re.I)
_PARAMISH = re.compile(r'(?i)\b(service_?key|api_?key|crtfc_?key|auth_?key|token|secret|password)'
                       r'\s*[=:]\s*\S+')


# 메시지에 이 낱말이 있으면 그 안에 인증 정보가 들어 있을 수 있다고 보고 통째로 버린다.
# '지우고 남기기'보다 '의심되면 남기지 않기'가 안전하다 — 값 하나라도 새면 키가 로그에 박힌다.
_SECRET_HINT = re.compile(r'(?i)key|token|secret|password|인증|url|http')


def _scrub_secrets(text):
    """예외 메시지에서 인증 정보가 새지 않게 한다.

    URL·키 파라미터 형태는 가리고, 그래도 키·토큰 같은 낱말이 남아 있으면 메시지를 버린다.
    진단 가치보다 유출 방지가 우선이다. 예외 종류(OSError 등)는 이미 따로 남는다.
    """
    cleaned = _URLISH.sub('<url>', text)
    cleaned = _PARAMISH.sub(lambda m: f'{m.group(1)}=<가림>', cleaned)
    if _SECRET_HINT.search(cleaned.replace('<url>', '').replace('<가림>', '')):
        # 우리가 직접 만든 API 오류 메시지는 진단에 꼭 필요하므로 예외로 통과시킨다.
        if 'SERVICE_KEY_IS_NOT_REGISTERED' in cleaned or 'API 오류' in cleaned:
            return cleaned
        return '<메시지 가림: 인증 정보가 포함될 수 있음>'
    return cleaned


def key_fingerprint(key):
    """인증키를 노출하지 않고 대조할 수 있는 지문.

    "Colab 에서는 되는데 Actions 에서는 안 된다"를 판정하려면 두 곳의 키가 같은지 알아야 한다.
    값을 로그에 남길 수는 없으므로 길이·앞뒤 4자·인코딩 여부만 적는다. 이 정도로는 키를 복원할
    수 없지만 '다른 키가 들어가 있다'는 것은 확실히 드러난다.
    """
    if not key:
        return 'key=없음'
    text = str(key)
    shape = 'encoded' if '%' in text else ('decoded' if any(c in text for c in '+/=') else 'plain')
    head, tail = text[:4], text[-4:]
    return f'key={shape} len={len(text)} {head}…{tail}'
