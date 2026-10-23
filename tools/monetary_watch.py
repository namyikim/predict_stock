"""금리·경기 점검: 외부 회고의 관찰 방식을 우리 보관 자료로 계산한다(2026-10-23 요청).

금리 인하 시점·주식 비중·인과관계는 추정하지 않는다. 같은 날짜의 금리와 같은 달의
지표 변화만 비교하며, 현재 보관본은 수정될 수 있어 과거 시점의 투자 신호로 쓰지 않는다.
"""
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

HISTORY = Path(__file__).resolve().parents[1] / "macro_history"
RATE_CODES = {
    "policy": ("722Y001", "0101000"),
    "kr3y": ("817Y002", "010200000"),
    "kr10y": ("817Y002", "010210000"),
}
LABELS = {"spread": "국고채 10년−3년 금리차", "leading": "선행지수 순환변동치", "kospi": "코스피 월말"}


def asof_day(now=None):
    stamp = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="Asia/Seoul")
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("Asia/Seoul").tz_localize(None)
    return stamp.normalize()


def clean_series(dates, values, cutoff):
    series = pd.Series(pd.to_numeric(values, errors="coerce").to_numpy(dtype=float),
                       index=pd.to_datetime(dates, errors="coerce", format="mixed"))
    series = series[series.index.notna() & (series.index <= cutoff)]
    series = series.replace([np.inf, -np.inf], np.nan).dropna()
    return series[~series.index.duplicated(keep="last")].sort_index()


def read_series(path, cutoff, column="value"):
    try:
        frame = pd.read_csv(path)
        return clean_series(frame.iloc[:, 0], frame[column], cutoff)
    except (OSError, ValueError, KeyError, IndexError):
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))


def load_rates(history=HISTORY, now=None, fetch=True, fetch_fn=None):
    """조회 실패 시 계열별 보관본 유지. 빈 응답·부분 실패가 다른 금리를 지우지 않게 한다."""
    cutoff, history = asof_day(now), Path(history)
    cache = history / "korea_rates_daily.csv"
    base = pd.DataFrame({name: read_series(cache, cutoff, name) for name in RATE_CODES})
    info = {"failed": []}
    if not fetch:
        return base, info
    from data_sources.ecos import ecos_key, fetch_ecos_daily
    key = ecos_key()
    if fetch_fn is None and not key:
        info["failed"] = ["일별 금리 조회 설정 없음 · 보관 자료 사용"]
        return base, info
    fetch_fn = fetch_fn or fetch_ecos_daily
    refreshed = False
    for name, (stat, item) in RATE_CODES.items():
        try:
            rows = fetch_fn(stat, item, cutoff - pd.DateOffset(years=3), cutoff, key)
            fresh = clean_series(rows["date"], rows["value"], cutoff)
            if fresh.empty:
                raise ValueError("빈 자료")
            base = base.reindex(base.index.union(fresh.index))
            base[name] = fresh.combine_first(base[name])
            refreshed = True
        except Exception:
            # 예외 본문에는 API 키가 든 URL이 있을 수 있으므로 화면과 로그에 복사하지 않는다.
            info["failed"].append({"policy": "기준금리", "kr3y": "국고채 3년", "kr10y": "국고채 10년"}[name]
                                  + " 조회 실패 · 보관 자료 사용")
    base = base.sort_index()
    if refreshed:
        history.mkdir(parents=True, exist_ok=True)
        base.rename_axis("date").to_csv(cache)
    return base, info


def completed_spread_months(series, cutoff):
    """끝난 달이어도 월초·월말 자료가 없으면 부분 평균을 월 전체 변화로 해석하지 않는다."""
    series = series.loc[series.index < cutoff.replace(day=1)]
    result = {}
    for month, group in series.resample("MS"):
        if (len(group) >= 10 and group.index.min().day <= 7
                and group.index.max() >= month + pd.offsets.MonthEnd(0) - pd.Timedelta(days=7)):
            result[month] = float(group.mean())
    return pd.Series(result, dtype=float, index=pd.DatetimeIndex(list(result)))


def build_watch(history=HISTORY, now=None, fetch=True, fetch_fn=None):
    cutoff, history = asof_day(now), Path(history)
    rates, info = load_rates(history, cutoff, fetch, fetch_fn)
    # 같은 날짜끼리만 뺀다. 발표일이 다른 자료를 전일 값으로 메워 격차를 만들지 않는다.
    pair = rates[["policy", "kr3y"]].dropna()
    curve = rates[["kr3y", "kr10y"]].dropna()
    spread = (curve["kr10y"] - curve["kr3y"]).combine_first(
        read_series(history / "term_spread.csv", cutoff)).sort_index()
    monthly = {"spread": completed_spread_months(spread, cutoff)}
    for name, file in (("leading", "leading_cycle.csv"), ("kospi", "kospi_monthly.csv")):
        series = read_series(history / file, cutoff)
        monthly[name] = series.resample("MS").last().loc[lambda s: s.index < cutoff.replace(day=1)].dropna()
    common = pd.concat(monthly, axis=1).dropna()
    comparison = None
    if len(common):
        month = common.index[-1]
        previous = month - pd.DateOffset(months=1)
        if previous in common.index and common.loc[previous, "kospi"] > 0:
            changes = common.loc[month] - common.loc[previous]
            changes["kospi"] = (common.loc[month, "kospi"] / common.loc[previous, "kospi"] - 1) * 100
            comparison = {"month": month, "previous": previous, "changes": changes,
                          "age_months": (cutoff.year - month.year) * 12 + cutoff.month - month.month}
    policy_monthly = read_series(history / "policy_inflation.csv", cutoff, "policy_rate")
    policy_monthly = policy_monthly[policy_monthly.index < cutoff.replace(day=1)]
    return {"asof": cutoff, "rates": rates, "pair": pair, "spread": spread, "monthly": monthly,
            "common": common, "comparison": comparison, "policy_monthly": policy_monthly, "info": info}


def line_chart(series, label, unit, zero=False):
    """최근 월별 흐름. 빈 달은 연결하지 않아 자료 공백이 추세처럼 보이지 않게 한다."""
    series = series.tail(12)
    if series.empty:
        return '<div class="empty">' + escape(label) + ' 자료 없음</div>'
    series = series.reindex(pd.date_range(series.index[0], series.index[-1], freq="MS"))
    low, high = float(series.min()), float(series.max())
    if zero:
        low, high = min(0, low), max(0, high)
    pad = max((high - low) * .15, .01)
    low, high = low - pad, high + pad
    x = lambda i: 58 + 490 * i / max(len(series) - 1, 1)
    y = lambda value: 116 - 82 * (value - low) / (high - low)
    points, paths = [], []
    for i, value in enumerate(series):
        if pd.isna(value):
            if points:
                paths.append(" ".join(points))
            points = []
        else:
            points.append(f'{"M" if not points else "L"}{x(i):.1f},{y(value):.1f}')
    if points:
        paths.append(" ".join(points))
    baseline = (f'<line x1="58" x2="548" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="#aaa" stroke-dasharray="4 3"/>'
                if zero else "")
    strokes = "".join(f'<path d="{p}" stroke="#1a5490" stroke-width="2.5" fill="none"/>' for p in paths)
    dots = "".join(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="2.5" fill="#1a5490"/>'
                   for i, v in enumerate(series) if pd.notna(v))
    desc = f'{label} · {series.index[0]:%Y-%m}~{series.index[-1]:%Y-%m} · 최근 값 {series.dropna().iloc[-1]:,.3f}{unit}'
    return (f'<svg viewBox="0 0 570 145" role="img" aria-label="{escape(desc)}" '
            'style="display:block;width:100%;height:auto;background:#fafbfc;border-radius:6px;margin-top:8px;font-size:11px">'
            f'<title>{escape(desc)}</title><text x="12" y="19" fill="#1a1a1a">{escape(label)} ({escape(unit)})</text>'
            f'<text x="52" y="38" text-anchor="end" fill="#6b7178">{high:,.2f}</text>'
            f'<text x="52" y="120" text-anchor="end" fill="#6b7178">{low:,.2f}</text>'
            f'{baseline}{strokes}{dots}<text x="58" y="137" fill="#6b7178">{series.index[0]:%Y-%m}</text>'
            f'<text x="548" y="137" text-anchor="end" fill="#6b7178">{series.index[-1]:%Y-%m}</text></svg>')


def watch_html(watch):
    head = ('<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
            '금리·경기 점검</h3>')
    if watch is None:
        return head + '<div class="empty">금리·경기 점검 자료를 읽지 못했습니다.</div>'
    pair, spread, comparison = watch["pair"], watch["spread"], watch["comparison"]
    rate_note = "기준금리와 국고채 3년의 같은 날짜 자료가 없어 격차 계산을 보류합니다."
    if len(pair):
        row, day = pair.iloc[-1], pair.index[-1]
        age = (watch["asof"] - day).days
        rate_note = (f'{day:%Y-%m-%d} · 기준금리 <b>{row["policy"]:.2f}%</b> / 국고채 3년 <b>{row["kr3y"]:.3f}%</b>'
                     f'<br>국고채 3년−기준금리 <b>{row["kr3y"] - row["policy"]:+.3f}%p</b>')
        if age > 7:
            rate_note += f'<br>자료 지연: {age}일 전 관측값입니다.'
    elif len(watch["policy_monthly"]):
        policy = watch["policy_monthly"]
        rate_note += f'<br>참고: {policy.index[-1]:%Y-%m} 기준금리 월평균 {policy.iloc[-1]:.2f}% (현재 결정금리와 구분).'
    curve_note = "금리차 자료가 없습니다."
    if len(spread):
        day, value = spread.index[-1], spread.iloc[-1]
        curve_note = f'{day:%Y-%m-%d} · 국고채 10년−3년 <b>{value:+.3f}%p</b>'
        if (watch["asof"] - day).days > 7:
            curve_note += '<br>자료 지연: 최근 시장 상황과 다를 수 있습니다.'
    cards = (f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px">'
             f'<div style="background:#f0f6fc;border:1px solid #cedff0;border-radius:6px;padding:12px;font-size:13px">'
             f'<b>시장금리와 기준금리</b><br>{rate_note}</div>'
             f'<div style="background:#f0f6fc;border:1px solid #cedff0;border-radius:6px;padding:12px;font-size:13px">'
             f'<b>장단기 금리차</b><br>{curve_note}<br>10년−1년 금리차와는 다른 지표입니다.</div></div>')
    verdict = "공통 월과 바로 전월의 자료가 부족해 방향 비교를 보류합니다."
    detail = ""
    if comparison:
        change = comparison["changes"]
        if comparison["age_months"] > 3:
            verdict = "공통 비교 기간이 오래되어 현재 방향 판단을 보류합니다."
        elif (change < -1e-8).all():
            verdict = "같은 기간에 금리차 축소·선행지수 하락·코스피 하락이 함께 관측됐습니다."
        elif (change > 1e-8).all():
            verdict = "같은 기간에 금리차 확대·선행지수 상승·코스피 상승이 함께 관측됐습니다."
        else:
            verdict = "세 지표의 방향이 일치하지 않습니다. 한 지표만으로 경기나 주가 방향을 단정하기 어렵습니다."
        detail = (f'<p style="font-size:13px">공통 비교: <b>{comparison["previous"]:%Y-%m} → {comparison["month"]:%Y-%m}</b>'
                  f'<br>금리차 월평균 {change["spread"]:+.3f}%p · 선행지수 {change["leading"]:+.2f}p · '
                  f'코스피 {change["kospi"]:+.2f}%</p>')
    dates = " · ".join(f'{LABELS[name]} {series.index[-1]:%Y-%m}' if len(series) else f'{LABELS[name]} 없음'
                       for name, series in watch["monthly"].items())
    charts = "".join(line_chart(watch["monthly"][name], LABELS[name], unit, zero=name == "spread")
                     for name, unit in (("spread", "%p"), ("leading", "p"), ("kospi", "p")))
    note = ('<p style="font-size:12px;color:#6b7178">진행 중인 달은 월간 비교와 그림에서 제외합니다. '
            '금리차는 월초·월말을 포함해 10일 이상 관측된 달의 평균, 선행지수는 월별 발표치, 코스피는 월말값입니다. '
            '지표별 발표 시차 때문에 공통 비교 기간은 최신 관측일보다 앞선 과거 시점일 수 있습니다. '
            '그림은 지표별 최신 완료 월까지, 방향 비교는 세 지표가 모두 있는 같은 두 달로 맞춥니다.</p>'
            '<p style="font-size:12px;color:#6b7178">기준금리 동결만으로 인상 종료를 뜻하지 않습니다. '
            '시장금리와 금리차에는 정책 기대뿐 아니라 위험 보상·채권 수급 등이 반영됩니다. '
            '선행지수에는 주가 등 금융 지표도 들어가므로 세 지표를 독립적인 증거로 세지 않습니다. '
            '현재 보관본의 동행 비교이며, 과거 공개 시점의 자료를 복원한 예측 검증이나 금리 인하·경기 침체 예측이 아닙니다.</p>'
            '<p class="muted">자료: 한국은행 ECOS·경기선행지수 보관본·코스피 월말 보관본. 해석 참고: '
            '<a href="https://www.bok.or.kr/portal/bbs/B0000347/view.do?menuNo=201106&amp;nttId=10072065&amp;pageIndex=1">'
            '한국은행 장단기금리차 설명</a> · '
            '<a href="https://www.bok.or.kr/portal/bbs/P0000559/view.do?depth=200690&amp;menuNo=200690&amp;nttId=11065823">'
            '2026-10-22 통화정책방향</a></p>')
    run_note = ""
    if watch["info"]["failed"]:
        run_note = '<span data-run-note>' + escape(" / ".join(watch["info"]["failed"])) + '</span>'
    return (head + '<p style="font-size:13px">시장금리·경기·주가의 움직임이 같은 방향인지 자료로 확인합니다.</p>'
            + cards + f'<p style="font-size:14px;color:#1a5490"><b>{verdict}</b></p>' + detail
            + f'<div class="muted">완료 월 자료: {dates}</div>' + charts + note + run_note)
