"""장기 전망 자료의 대체 수집과 기준월 표시(2026-10-19)."""
import html
from pathlib import Path

import numpy as np
import pandas as pd

EXPORT_SERIES = 'XTEXVA01KRM667S'  # OECD/FRED 한국 총수출: 달러, 계절조정. 관세청 원값과 혼합하지 않는다.


def clock(now=None):
    stamp = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz='Asia/Seoul')
    if stamp.tzinfo:
        stamp = stamp.tz_convert('Asia/Seoul').tz_localize(None)
    return stamp


def clean(series, now=None):
    out = pd.to_numeric(series, errors='coerce').replace([np.inf, -np.inf], np.nan).dropna()
    out.index = pd.to_datetime(out.index).to_period('M').to_timestamp()
    out = out[~out.index.duplicated(keep='last')].sort_index()
    return out[(out > 0) & (out.index < clock(now).to_period('M').to_timestamp())]


def load_exports(cache_path, fallback_paths=(), fetch=True, now=None):
    """동일한 FRED 계열의 API → 공개 CSV → 보관본. 계절조정 정의·과거 이력을 보존한다."""
    from data_sources import oecd, fred
    cache_path = Path(cache_path)
    series = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    info = {'source': '보관본', 'fresh': False, 'errors': [],
            'checked_at': clock(now).strftime('%Y-%m-%d %H:%M KST'),
            'definition': '한국 총수출(달러·계절조정, OECD/FRED)'}
    for path in (cache_path, *fallback_paths):
        if Path(path).exists():
            try:
                frame = pd.read_csv(path, parse_dates=['month'])
                series = series.combine_first(clean(frame.set_index('month').value, now))
            except (ValueError, KeyError, OSError):
                info['errors'].append('보관본 읽기 실패')
    fresh = None
    if fetch:
        key = (oecd.fred_key() or '').strip()
        if key:
            try:
                frame = oecd.fetch_fred_monthly(EXPORT_SERIES, key, '1995-01-01')
                fresh = clean(frame.set_index('month').value, now)
                if fresh.empty:
                    raise ValueError('빈 자료')
                info['source'] = 'FRED API'
            except Exception:
                fresh = None
                info['errors'].append('FRED API 조회 실패')
        # API 인증 오류가 나도 공개 CSV는 키 없이 같은 통계를 받을 수 있다.
        if fresh is None or (not series.empty and fresh.index.max() < series.index.max()):
            try:
                csv = clean(fred.fetch_fred(EXPORT_SERIES, retries=1), now)
                if csv.empty:
                    raise ValueError('빈 자료')
                fresh = csv if fresh is None else csv.combine_first(fresh)
                info['source'] = 'FRED 공개 CSV'
            except Exception:
                info['errors'].append('FRED 공개 CSV 조회 실패')
        if fresh is not None:
            older = not series.empty and fresh.index.max() < series.index.max()
            series = fresh.combine_first(series).sort_index()
            info['fresh'] = not older
            if older:
                info['source'] += ' + 최신월 보관본'
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            series.rename_axis('month').reset_index(name='value').to_csv(cache_path,index=False)
    info['last'] = series.index.max().strftime('%Y-%m') if not series.empty else None
    # 결측월을 삭제한 뒤 shift(12) 하면 12개월 전이 아닌 다른 달과 비교하게 된다.
    return (series.asfreq('MS') if not series.empty else series), info


def monthly_exports(base, snapshot=None, now=None):
    """KOSIS 원본을 유지하고 검증된 관세청 환산 월간 값만 서술 그래프에 추가한다."""
    snapshot = snapshot or {}
    out = base.copy()
    out['month'] = pd.to_datetime(out['month'])
    values = out.set_index('month').value.copy()
    monthly_info = snapshot.get('monthly_info') or {}
    customs = monthly_info.get('customs') or {}
    scale = customs.get('scale')
    valid_scale = isinstance(scale, (int, float)) and np.isfinite(scale) and scale > 0
    approved = set(customs.get('months_added') or []) if customs.get('enabled') and valid_scale else set()
    partial = {r['month'] for r in snapshot.get('applied', []) if r.get('basis') == 'partial_month_scenario'}
    added = []
    end = clock(now).to_period('M').to_timestamp()
    for row in snapshot.get('series', []):
        month, value = pd.Timestamp(row['month']), float(row['value'])
        if (row['month'] in approved and row['month'] not in partial and month < end
                and np.isfinite(value) and value > 0 and month not in values.dropna().index):
            values.loc[month] = value
            added.append(row['month'])
    return values.sort_index().rename_axis('month').reset_index(name='value'), {
        'months_added': sorted(added), 'scale': customs.get('scale'),
        'source': customs.get('source'), 'checked_at': snapshot.get('generated_at'),
        'kosis_last': out.month.max().strftime('%Y-%m') if not out.empty else None,
        'last': values.index.max().strftime('%Y-%m') if not values.empty else None,
    }


def note(label, info, now=None):
    """조회 시각과 관측월은 다르다. 호출 성공이어도 오래된 계열은 지연으로 표시한다."""
    e = html.escape
    info = info or {}
    last = info.get('last')
    delayed = last is None or (clock(now).to_period('M') - pd.Period(last, freq='M')).n > 2
    status = ('새 조회 반영' if info.get('fresh') else '보관본/조회 상태 미확인')
    warning = ' · 자료 지연: 최신 발표 여부를 다음 일일 실행에서 다시 확인합니다.' if delayed else ''
    if info.get('errors') and info.get('fresh'):
        status += ' (대체 경로 사용)'
    return ('<div style="font-size:12px;color:#6b7178;margin:6px 0">'
            f'{e(label)} 관측월 <b>{e(str(last or "없음"))}</b> · {e(str(info.get("source") or "출처 미확인"))} · '
            f'{status} · 조회 {e(str(info.get("checked_at") or "시각 미기록"))}{warning}</div>')
