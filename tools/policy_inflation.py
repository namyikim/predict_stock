"""장기 전망의 한국 기준금리·물가 비교(2026-10-19 요청). 예측 입력과 분리한 관측 자료."""
import html
import math
from pathlib import Path

import pandas as pd

CACHE_NAME = 'policy_inflation.csv'
COLUMNS = ['policy_rate', 'cpi_index']
LABELS = {'policy_rate': '기준금리', 'cpi_index': '소비자물가지수'}


def fetch_series(name, start, end):
    from data_sources.ecos import ecos_key, fetch_ecos_daily, fetch_korea_cpi_monthly
    key = ecos_key()
    if not key:
        raise RuntimeError('ECOS_API_KEY 미설정')
    if name == 'policy_rate':
        # 10년 국채 수익률과 구별한다. 일별 관측치의 월평균임을 화면에 명시한다.
        daily = fetch_ecos_daily('722Y001', '0101000', start, end, key)
        return daily.set_index('date')['value'].resample('MS').mean().dropna()
    return fetch_korea_cpi_monthly(start, end, key)


def normalize(frame):
    out = frame.reindex(columns=COLUMNS).copy()
    out.index = pd.to_datetime(out.index, errors='coerce')
    out = out.loc[out.index.notna()]
    out.index = out.index.to_period('M').to_timestamp()
    out = out.loc[~out.index.duplicated(keep='last')].sort_index()
    for col in COLUMNS:
        out[col] = pd.to_numeric(out[col], errors='coerce').replace([float('inf'), -float('inf')], float('nan'))
    out.loc[out.cpi_index <= 0, 'cpi_index'] = float('nan')
    return out


def comparison(raw, now=None):
    """12개 행이 아닌 12개월 전과 비교. 진행 중인 달은 월평균이 확정되지 않아 제외."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz='Asia/Seoul').tz_localize(None)
    out = normalize(raw)
    out = out.loc[out.index < now.to_period('M').to_timestamp()]
    if out.empty:
        return out.assign(inflation=pd.Series(dtype=float))
    out = out.reindex(pd.date_range(out.index.min(), out.index.max(), freq='MS'))
    out['inflation'] = (out.cpi_index / out.cpi_index.shift(12) - 1) * 100
    return out


def load(cache_path, fetch=True, fallback_paths=(), now=None, fetch_fn=None):
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz='Asia/Seoul').tz_localize(None)
    cache_path = Path(cache_path)
    raw = pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([]), dtype=float)
    failed, fresh = {}, []
    # 이번 실행의 보관본 → 원격 보관본 → 체크아웃 보관본 순으로 우선한다.
    for path in (cache_path, *fallback_paths):
        if Path(path).exists():
            try:
                cached = normalize(pd.read_csv(path, index_col='month'))
                raw = raw.combine_first(cached)
            except (ValueError, OSError, KeyError):
                failed['cache'] = '보관본을 읽지 못했습니다'
    if fetch:
        for name in COLUMNS:
            try:
                series = (fetch_fn or fetch_series)(name, '2007-03-01', now.date())
                series = normalize(pd.DataFrame({name: series}))[name].dropna()
                series = series.loc[series.index < now.to_period('M').to_timestamp()]
                if series.empty:
                    raise ValueError('빈 자료')
                raw = raw.reindex(raw.index.union(series.index))
                raw.loc[series.index, name] = series
                fresh.append(name)
            except Exception:
                # API 예외에는 키가 포함될 수 있으므로 원문을 화면·JSON에 저장하지 않는다.
                failed[name] = '조회 실패 또는 API 키 미설정'
    raw = normalize(raw)
    raw = raw.loc[raw.index < now.to_period('M').to_timestamp()]
    if fresh:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        raw.to_csv(cache_path, index_label='month')
    frame = comparison(raw, now)
    latest = None
    common = frame.dropna(subset=['policy_rate', 'inflation'])
    if not common.empty:
        row = common.iloc[-1]
        latest = {'month': common.index[-1].strftime('%Y-%m'),
                  'policy_rate': float(row.policy_rate), 'inflation': float(row.inflation),
                  'gap': float(row.policy_rate - row.inflation)}
    last = {col: (frame[col].last_valid_index().strftime('%Y-%m') if frame[col].notna().any() else None)
            for col in ('policy_rate', 'inflation')}
    # 최근 10년을 기본 표시하되 과거 보관본만 있으면 그 자료의 마지막 10년을 표시한다.
    shown = frame.loc[frame.index >= frame.index.max() - pd.DateOffset(years=10)] if not frame.empty else frame
    rows = [{'month': date.strftime('%Y-%m'),
             **{col: float(row[col]) if pd.notna(row[col]) else None for col in ('policy_rate', 'inflation')}}
            for date, row in shown.iterrows() if date >= pd.Timestamp('2008-03-01')]
    return {'rows': rows, 'latest': latest, 'last': last, 'fresh': fresh, 'failed': failed,
            'checked_at': now.isoformat(),
            'stale': any(month is None or (now.to_period('M') - pd.Period(month)).n > 2 for month in last.values())}


def chart(rows):
    # 기존 보관 JSON도 바로 그릴 수 있도록 같은 달의 두 관측값에서 계산한다.
    rows = [{**row, 'real_rate': (row['policy_rate'] - row['inflation']
                                 if row['policy_rate'] is not None and row['inflation'] is not None else None)}
            for row in rows]
    values = [row[k] for row in rows for k in ('policy_rate', 'inflation', 'real_rate') if row[k] is not None]
    if not values:
        return ''
    low, high = math.floor(min(0, min(values))), math.ceil(max(values))
    if high <= low:
        high = low + 1
    left, right, top, bottom = 48, 746, 18, 235
    x = lambda i: left + (right - left) * i / max(1, len(rows) - 1)
    y = lambda v: bottom - (bottom - top) * (v - low) / (high - low)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 780 275" role="img" '
             'aria-label="한국 기준금리·물가 상승률·실질금리 참고값 비교" style="width:100%;height:auto">',
             '<title>한국 기준금리·물가 상승률·실질금리 참고값 — 동일한 % 눈금, 0% 위는 양수 아래는 음수</title>']
    zero = y(0)
    parts.append(f'<rect x="{left}" y="{top}" width="{right-left}" height="{zero-top:.1f}" fill="#f2f8f4"/>')
    parts.append(f'<rect x="{left}" y="{zero:.1f}" width="{right-left}" height="{bottom-zero:.1f}" fill="#fff3f1"/>')
    for i in range(5):
        value = low + (high - low) * i / 4
        parts.append(f'<line x1="{left}" x2="{right}" y1="{y(value):.1f}" y2="{y(value):.1f}" stroke="#e5e7eb"/>'
                     f'<text x="40" y="{y(value)+4:.1f}" text-anchor="end" font-size="12" fill="#59636e">{value:.1f}%</text>')
    parts.append(f'<line data-role="zero-line" x1="{left}" x2="{right}" y1="{zero:.1f}" y2="{zero:.1f}" '
                 'stroke="#515965" stroke-width="1.5" stroke-dasharray="4 3"/>'
                 f'<text x="{right}" y="{zero-5:.1f}" text-anchor="end" font-size="12" fill="#515965">0% 기준</text>')
    for i in sorted({round(v * (len(rows) - 1) / 4) for v in range(5)}):
        parts.append(f'<text x="{x(i):.1f}" y="258" text-anchor="middle" font-size="12" fill="#59636e">{rows[i]["month"]}</text>')
    for col, color, dashed, label in (('policy_rate', '#1a5490', '', '기준금리 월평균'),
                                      ('inflation', '#bd5b20', ' stroke-dasharray="6 4"', '물가 상승률'),
                                      ('real_rate', '#7850a4', ' stroke-dasharray="9 3 2 3"', '실질금리 참고값')):
        path, active = [], False
        for i, row in enumerate(rows):
            value = row[col]
            if value is None:
                active = False
                continue
            path.append(f'{"L" if active else "M"}{x(i):.1f},{y(value):.1f}')
            active = True
            shown = f'{value:+.2f}' if col == 'real_rate' else f'{value:.2f}'
            parts.append(f'<circle cx="{x(i):.1f}" cy="{y(value):.1f}" r="2" fill="{color}">'
                         f'<title>{row["month"]} {label}: {shown}%</title></circle>')
        parts.append(f'<path data-series="{col}" d="{" ".join(path)}" fill="none" stroke="{color}" stroke-width="2.2"{dashed}/>')
    return ''.join(parts) + '</svg>'


def render(result):
    r = result or {}
    parts = ['<section id="policy-inflation"><h4 style="font-size:14px;margin:18px 0 6px">'
             '기준금리와 소비자물가 상승률 — 한국</h4>']
    rows = r.get('rows', [])
    svg = chart(rows) if rows else ''
    if not svg:
        return ''.join(parts) + '<p style="font-size:13px;color:#6b7178">비교 자료가 없어 그래프를 표시하지 못했습니다. 다음 장기 전망 갱신 시 다시 조회합니다.</p></section>'
    latest = r.get('latest')
    if latest:
        parts.append(f'<p style="font-size:13px">두 지표가 함께 있는 최신월 <b>{latest["month"]}</b>: '
                     f'기준금리(월평균) <b>{latest["policy_rate"]:.2f}%</b> · 물가 상승률 <b>{latest["inflation"]:.2f}%</b> · '
                     f'차이(금리−물가) <b>{latest["gap"]:+.2f}%p</b></p>')
        real = round(latest['gap'], 2)
        status, color = (('양수(+)', '#24643b') if real > 0 else
                         ('음수(−)', '#a63632') if real < 0 else ('0 수준', '#59636e'))
        value = f'{real:+.2f}%' if real != 0 else '0.00%'
        parts.append(f'<p style="font-size:13px">실질금리 참고값: <b style="color:{color}">{value} · {status}</b>'
                     ' <span style="color:#6b7178">(기준금리−최근 물가 상승률, 근사값)</span></p>')
    parts.append('<p style="font-size:13px"><span style="color:#1a5490">━ 기준금리(월평균)</span> · '
                 '<span style="color:#bd5b20">┄ 소비자물가 상승률(점선, 전년 동월 대비)</span> · '
                 '<span style="color:#7850a4">┄· 실질금리 참고값(일점쇄선)</span></p>' + svg)
    last = r.get('last', {})
    parts.append(f'<p style="font-size:12px;color:#6b7178">최신 관측월: 금리 {html.escape(last.get("policy_rate") or "없음")} · '
                 f'물가 {html.escape(last.get("inflation") or "없음")}. 가로축은 발표일이 아닌 관측월입니다. '
                 '진행 중인 달은 제외하며, 빠진 값은 선으로 이어 붙이지 않습니다.</p>')
    if len(r.get('fresh', [])) < 2:
        parts.append('<p style="font-size:12px;color:#986414">일부 또는 전체 자료는 보관본입니다. '
                     '최신 조회가 반영되지 않은 지표의 기준월을 확인하세요.</p>')
    if r.get('stale'):
        parts.append('<p style="font-size:12px;color:#986414">관측월이 2개월 넘게 지났거나 지표가 누락되어 최신 상황과 다를 수 있습니다.</p>')
    parts.append('<p style="font-size:13px">읽는 법: 보라색 실질금리 선이 <b>0% 위(연녹색)이면 양수(+)</b>, '
                 '<b>0% 아래(연분홍색)이면 음수(−)</b>입니다. 양수는 기준금리가 최근 물가 상승률보다 높고, 음수는 낮다는 뜻입니다. '
                 '기준금리와 최근 12개월 물가의 차이를 이용한 근사값이며, 기대물가를 반영한 실질금리나 실제 예금 수익률은 아닙니다. '
                 '부호만으로 주가 방향을 판단할 수 없습니다.</p>'
                 '<p style="font-size:12px;color:#6b7178">출처: <a href="https://ecos.bok.or.kr/">한국은행 ECOS</a> '
                 '(기준금리 722Y001/0101000, 소비자물가지수 901Y009/0). 물가 상승률 = (당월 지수 ÷ 12개월 전 지수 − 1) × 100. '
                 '이 그림은 경제환경 참고용이며 장기 수익률 예측·과거 성적 계산의 입력에는 사용하지 않습니다.</p></section>')
    return ''.join(parts)
