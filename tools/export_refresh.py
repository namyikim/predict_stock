"""일일 수출 스냅샷과 장기 전망용 최신 통계 시나리오. 과거 검증 행은 변경하지 않는다."""
import hashlib
import json

import numpy as np
import pandas as pd


def resolve_exports(monthly, flash, now=None):
    """월간 우선. 10/20일은 같은 기간 YoY로 추정, 월말은 실제 월 합계를 사용."""
    out = monthly.copy().sort_index()
    if flash is None or flash.empty:
        return out, []
    today = pd.Timestamp(now or pd.Timestamp.now(tz='Asia/Seoul')).tz_localize(None).normalize()
    rows = flash.copy()
    rows['month'] = pd.to_datetime(rows['month'])
    rows['value'] = pd.to_numeric(rows['value'], errors='coerce')
    rows['days'] = pd.to_numeric(rows['days'], errors='coerce')
    rows = rows[np.isfinite(rows.value) & (rows.value > 0)]
    rows = rows[(rows.days.isin([10, 20, 31])) | (rows.days == rows.month.dt.days_in_month)]
    # 아직 끝나지 않은 기간을 쓰지 않는다. 실제 제공 여부는 API 응답으로 확인한다.
    effective_days = np.minimum(rows.days, rows.month.dt.days_in_month)
    rows = rows[rows.month + pd.to_timedelta(effective_days, unit='D') <= today]
    rows = rows.drop_duplicates(['month', 'days'], keep='last').sort_values(['month', 'days'])
    applied = []
    for month, group in rows.groupby('month', sort=True):
        if month in monthly.index and pd.notna(monthly.loc[month]):
            continue
        row = group.iloc[-1]
        days, amount = int(row.days), float(row.value)
        previous_month = month - pd.DateOffset(years=1)
        previous = rows[(rows.month == previous_month) & (rows.days == days)]
        yoy = amount / float(previous.iloc[-1].value) - 1 if len(previous) else None
        full = days >= month.days_in_month
        if full:
            value = amount
        elif yoy is not None and previous_month in monthly.index and pd.notna(monthly.loc[previous_month]):
            value = float(monthly.loc[previous_month]) * (1 + yoy)
        else:
            continue
        out.loc[month] = value
        applied.append({'month': month.strftime('%Y-%m'), 'days': days, 'yoy': yoy,
                        'observed_usd': amount, 'monthly_usd': value,
                        'basis': 'full_month_preliminary' if full else 'partial_month_scenario'})
    return out.sort_index(), applied


def snapshot(exports, applied, flash_info, generated_at):
    rows = [{'month': f'{m:%Y-%m}', 'value': float(v)} for m, v in exports.items()]
    payload = {'series': rows, 'applied': applied, 'flash_info': flash_info}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]
    return {**payload, 'snapshot_hash': digest, 'generated_at': generated_at}


def live_frame(history, exports, now=None):
    """최신 수출 특징만 새 시나리오 행에 반영한다. 월말 가격은 고정한 민감도 분석이다."""
    from data_sources.kosis import _monthly_features
    from data_sources.exports import daily_average
    from build_longterm_report import CYCLE_COMPONENTS, expanding_z, phase_of

    now = pd.Timestamp(now or pd.Timestamp.now(tz='Asia/Seoul')).tz_localize(None).normalize()
    if now <= history.index[-1]:
        raise ValueError('시나리오 날짜는 마지막 월말 검증 행 뒤여야 합니다.')
    exports = exports[exports.index <= now].sort_index().asfreq('MS')
    row = history.iloc[-1].copy()
    features = _monthly_features('semiconductor_exports', exports).iloc[-1]
    for key, value in features.items():
        row[key] = value
    row['exports_cycle'] = features['macro_semiconductor_yoy_3m']
    row['exports_accel'] = _monthly_features('semiconductor_exports', exports)['macro_semiconductor_yoy_3m'].diff(3).iloc[-1]
    row['phase'] = phase_of(row['exports_cycle'], row['exports_accel'])
    da = daily_average(exports.rename_axis('month').reset_index(name='value')).set_index('month').value.asfreq('MS')
    row['exports_daily_yoy'] = da.pct_change(12, fill_method=None).iloc[-1]
    # 과거 주가/수출 비율과 같은 정의. 오늘 받은 값을 과거 행에 역으로 넣지 않는다.
    old_exports = np.exp(history['macro_semiconductor_log_usd'])
    ratio = np.log(history.price) - np.log(old_exports.rolling(12, min_periods=6).mean())
    ratio.loc[now] = np.log(row['price']) - np.log(exports.iloc[-12:].mean())
    row['price_to_exports_z'] = ((ratio - ratio.rolling(60, min_periods=24).mean()) /
                                 ratio.rolling(60, min_periods=24).std()).iloc[-1]
    for key in row.index:
        if key.startswith('fwd_'):
            row[key] = np.nan
    result = history.copy().reindex(history.index.append(pd.DatetimeIndex([now])))
    result.loc[now] = row
    for key in history.select_dtypes(include='number').columns:
        result[key] = pd.to_numeric(result[key])
    have = [key for key in CYCLE_COMPONENTS if key in result]
    if len(have) >= 3:
        scores = pd.concat([expanding_z(result[key]) for key in have], axis=1)
        result.loc[now, 'cycle_score'] = scores.mean(axis=1, skipna=False).iloc[-1]
    return result


def live_scenario(history, cols, exports_snapshot, evaluation, now=None):
    """기존 월간 모델의 민감도. 과거 검증 점수/예측구간을 속보 시나리오에 전용하지 않는다."""
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    exports = pd.Series({pd.Timestamp(r['month']): r['value'] for r in exports_snapshot['series']})
    frame = live_frame(history, exports, now)
    row = frame.iloc[-1]
    forecasts = {}
    for h in (3, 6, 12):
        y = history[f'fwd_{h}m']
        valid = history[cols].notna().all(axis=1) & y.notna()
        point = None
        if valid.sum() >= 60 and row[cols].notna().all() and evaluation[str(h)].get('beats_zero'):
            model = make_pipeline(StandardScaler(), Ridge(alpha=10.)).fit(history.loc[valid, cols], y[valid])
            raw = float(model.predict(frame.iloc[[-1]][cols])[0])
            point = raw * evaluation[str(h)].get('shrink_slope', 0.)
        forecasts[str(h)] = {'point': point, 'validated': False, 'interval': None}
    applied = exports_snapshot.get('applied', [])
    latest = next((r for r in reversed(applied) if r['month'] == f'{exports.index[-1]:%Y-%m}'), None)
    return {'as_of': f'{frame.index[-1]:%Y-%m-%d}', 'price_last': f'{history.index[-1]:%Y-%m-%d}',
            'exports_last_month': f'{exports.index[-1]:%Y-%m}', 'latest_period': latest,
            'snapshot_hash': exports_snapshot['snapshot_hash'],
            'generated_at': exports_snapshot['generated_at'], 'flash_info': exports_snapshot.get('flash_info', {}),
            'current': {k: (float(v) if pd.notna(v) else None) for k, v in row.items()
                        if k in cols or k in ('exports_cycle', 'exports_accel', 'exports_daily_yoy')},
            'phase': row.get('phase'), 'forecast': forecasts, 'validated': False}


def render_live(scenario):
    from html import escape
    if not scenario:
        return ''
    s = scenario
    period = s.get('latest_period') or {}
    label = ('월말 잠정치' if period.get('basis') == 'full_month_preliminary' else
             f"1~{period['days']}일 잠정치" if period else '월간 자료')
    yoy = s['current'].get('macro_semiconductor_yoy')
    value = f'{yoy * 100:+.1f}%' if yoy is not None else '산출 불가'
    info = s.get('flash_info', {})
    warning = ('이번 수집 실패로 마지막 성공 보관본을 사용했습니다. 다음 일일 실행에서 다시 확인합니다.<br>'
               if info.get('fetch_error') or info.get('source') == 'customs_flash_cache' else '')
    forecasts = ' · '.join(
        f"{h}개월 {np.expm1(v['point']):+.1%}" if v.get('point') is not None else f'{h}개월 판단 보류'
        for h, v in s.get('forecast', {}).items())
    return ('<div style="padding:14px;background:#eef6ff;border:1px solid #cbdff5;border-radius:6px">'
            '<b>새 수출 통계를 반영한 장기 전망</b><br>'
            + warning +
            f"{escape(s['exports_last_month'])} {escape(label)} · 확인 {escape(s['generated_at'])}<br>"
            f"월 전체 수출 증가율 {'추정' if period.get('basis') == 'partial_month_scenario' else ''}: "
            f"<b>{value}</b> · 수출 국면: <b>{escape(str(s.get('phase') or '판정 불가'))}</b><br>"
            f"주가 민감도 시나리오(미검증): {escape(forecasts)}<br>"
            '10일·20일 자료는 전년 같은 기간 증가율로 월 전체를 추정합니다. '
            '월간 자료가 들어오면 잠정 추정을 대체합니다.<br>'
            f"가격 기준은 {escape(s['price_last'])} 월말로 고정한 수출 민감도 분석입니다. "
            '아래 월간 검증 결과와 구분되며, 속보 반영 예측의 정확도·구간은 아직 검증하지 않았습니다.'
            '</div>')
