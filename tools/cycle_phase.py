"""거시 보고서 '경기 국면' 절(2026-10-01).

거시 경제 유튜브(선행지수 순환변동치)의 판단 방식을 우리 자료로 자동화한 것이다. 숫자·전망·자산배분 조언은
가져오지 않는다(CLAUDE.md '외부 자료로 개선할 때').

  ① 선행지수 순환변동치의 국면 — 최근 24개월 고점과 그 뒤 하락 개월 수. 1개월이면 '정점 가능성', 2개월 이상이면
     '하락 국면 전환'(저점 쪽도 같은 규칙).
  ② 선행-동행 격차 — 선행지수에는 주가 등 금융 변수가 들어가므로 둘의 차이는 금융과 실물의 괴리로 읽는다.
  ③ 선행의 선행 — 뉴스심리지수(3개월 이동평균)·장단기 금리차가 선행지수에 앞서는지. 상관은 매번 우리 자료로 다시
     계산하고, 수준 상관과 변화 상관을 함께 적어 추세 때문에 부풀려진 상관을 과신하지 않게 한다.
  ④ 국면별 코스피 — 선행지수가 오른 달의 다음 달과 내린 달의 다음 달에 코스피 월수익률이 어땠나(발표 지연을 감안해
     한 달 늦춰 짝지음). 비중 조언은 하지 않는다.
"""
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "macro_history"


def _read_monthly(path, column="value"):
    frame = pd.read_csv(path)
    stamps = pd.to_datetime(frame.iloc[:, 0], errors="coerce")
    values = pd.to_numeric(frame[column] if column in frame else frame.iloc[:, -1], errors="coerce")
    series = pd.Series(values.to_numpy(dtype=float), index=stamps)
    return series[series.index.notna()].dropna().sort_index()


def load_coincident(fetch=True, start="2013-01-01"):
    """동행지수 순환변동치. KOSIS 에서 받아 macro_history/coincident_cycle.csv 에 누적하고, 실패하면 보관본."""
    cache = HISTORY / "coincident_cycle.csv"
    base = _read_monthly(cache) if cache.exists() else pd.Series(dtype=float)
    info = {"source": "cache" if len(base) else "none"}
    if fetch:
        try:
            from data_sources.kosis import fetch_kosis_monthly, kosis_key
            key = kosis_key()
            if not key:                      # 키가 없는 환경(테스트 등)은 실패가 아니라 '받지 않음'
                info["skipped"] = "KOSIS_API_KEY 없음"
                return base, info
            fresh = fetch_kosis_monthly("coincident_cycle", start, pd.Timestamp.now(), key)
            fresh = pd.Series(fresh["value"].to_numpy(dtype=float), index=pd.to_datetime(fresh["month"]))
            merged = fresh.combine_first(base) if len(base) else fresh
            merged.sort_index().rename("value").rename_axis("month").to_csv(cache)
            base, info["source"] = merged.sort_index(), "KOSIS"
        except Exception as exc:
            info["failed"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return base, info


def phase_of(cycle, window=24):
    """최근 고점·저점 기준 국면. {'state','since','months','level','peak'/'trough'...}"""
    cycle = cycle.dropna()
    if len(cycle) < 6:
        return None
    recent = cycle.tail(window)
    last_month, last = cycle.index[-1], float(cycle.iloc[-1])
    peak_month, trough_month = recent.idxmax(), recent.idxmin()
    down = int(((cycle.index > peak_month) & (cycle.index <= last_month)).sum())
    up = int(((cycle.index > trough_month) & (cycle.index <= last_month)).sum())
    if peak_month > trough_month:        # 저점 뒤 상승해 고점을 찍은 쪽이 최근
        if down == 0:
            state, since, months = "상승 중(최근 고점 경신)", trough_month, up
        elif down == 1:
            state, since, months = "정점 가능성(고점 뒤 1개월 하락)", peak_month, down
        else:
            state, since, months = f"하락 국면 전환(고점 뒤 {down}개월 연속)", peak_month, down
    else:
        if up == 0:
            state, since, months = "하락 중(최근 저점 경신)", peak_month, down
        elif up == 1:
            state, since, months = "저점 가능성(저점 뒤 1개월 상승)", trough_month, up
        else:
            state, since, months = f"상승 국면 전환(저점 뒤 {up}개월 연속)", trough_month, up
    return {"state": state, "since": since, "months": months, "last_month": last_month, "level": last,
            "peak_month": peak_month, "peak": float(recent.max()), "trough_month": trough_month,
            "trough": float(recent.min())}


def lead_lag(target, candidate, max_lag=4, min_n=36):
    """candidate 가 target 보다 몇 개월 앞서는지. 수준 상관과 월간 변화 상관을 따로 돌려준다."""
    out = {"level": [], "change": []}
    cand = candidate.reindex(target.index)
    for lag in range(0, max_lag + 1):
        level = pd.concat([target, cand.shift(lag)], axis=1).dropna()
        change = pd.concat([target.diff(), cand.diff().shift(lag)], axis=1).dropna()
        if len(level) >= min_n:
            out["level"].append((lag, float(level.corr().iloc[0, 1]), len(level)))
        if len(change) >= min_n:
            out["change"].append((lag, float(change.corr().iloc[0, 1]), len(change)))
    best_level = max(out["level"], key=lambda t: t[1]) if out["level"] else None
    best_change = max(out["change"], key=lambda t: t[1]) if out["change"] else None
    return {"level": out["level"], "change": out["change"], "best_level": best_level, "best_change": best_change}


def recent_direction(series, months=3):
    """최근 months 개월의 방향(끝값-시작값)."""
    s = series.dropna().tail(months + 1)
    if len(s) < 2:
        return None
    delta = float(s.iloc[-1] - s.iloc[0])
    return {"delta": delta, "from": s.index[0], "to": s.index[-1], "sign": "상승" if delta > 0 else "하락" if delta < 0 else "보합"}


def phase_returns(cycle, kospi):
    """선행지수가 오른 달·내린 달의 다음 달 코스피 월수익률."""
    ret = kospi.pct_change()
    phase = np.sign(cycle.diff()).reindex(ret.index).shift(1)
    both = pd.concat([ret, phase], axis=1).dropna()
    both.columns = ["ret", "phase"]
    rows = {}
    for sign, label in ((1, "선행지수 상승 뒤 달"), (-1, "선행지수 하락 뒤 달")):
        g = both[both["phase"] == sign]["ret"]
        if len(g):
            rows[label] = {"n": int(len(g)), "mean": float(g.mean()), "down_share": float((g < 0).mean()),
                           "median": float(g.median())}
    return rows


def build_cycle_phase(fetch=True):
    """절에 필요한 모든 계산. 자료가 없으면 None."""
    leading_path = HISTORY / "leading_cycle.csv"
    if not leading_path.exists():
        return None
    leading = _read_monthly(leading_path)
    coincident, coincident_info = load_coincident(fetch=fetch)
    nsi = _read_monthly(HISTORY / "news_sentiment.csv") if (HISTORY / "news_sentiment.csv").exists() else pd.Series(dtype=float)
    spread = _read_monthly(HISTORY / "term_spread.csv") if (HISTORY / "term_spread.csv").exists() else pd.Series(dtype=float)
    kospi = _read_monthly(HISTORY / "kospi_monthly.csv") if (HISTORY / "kospi_monthly.csv").exists() else pd.Series(dtype=float)
    nsi_m = nsi.resample("MS").mean().rolling(3).mean() if len(nsi) else pd.Series(dtype=float)
    spread_m = spread.resample("MS").mean() if len(spread) else pd.Series(dtype=float)
    out = {"leading": leading, "coincident": coincident, "coincident_info": coincident_info,
           "phase": phase_of(leading), "nsi_m": nsi_m, "spread_m": spread_m,
           "nsi_dir": recent_direction(nsi_m), "spread_dir": recent_direction(spread_m),
           "nsi_lag": lead_lag(leading, nsi_m) if len(nsi_m) else None,
           "spread_lag": lead_lag(leading, spread_m) if len(spread_m) else None,
           "returns": phase_returns(leading, kospi) if len(kospi) else {},
           "gap": None}
    if len(coincident):
        both = pd.concat([leading, coincident], axis=1).dropna()
        both.columns = ["leading", "coincident"]
        if len(both) >= 3:
            gap = both["leading"] - both["coincident"]
            out["gap"] = {"now": float(gap.iloc[-1]), "prev": float(gap.iloc[-4]) if len(gap) >= 4 else None,
                          "month": gap.index[-1], "coincident_last": float(both["coincident"].iloc[-1])}
    return out


def _fmt_month(stamp):
    return f"{stamp.year}년 {stamp.month}월"


def cycle_chart_svg(leading, coincident, years=10):
    """선행·동행 순환변동치 꺾은선(최근 years 년). 100 선을 긋는다."""
    start = leading.index.max() - pd.DateOffset(years=years)
    lead = leading[leading.index >= start]
    coin = coincident[coincident.index >= start] if coincident is not None and len(coincident) else pd.Series(dtype=float)
    values = pd.concat([lead, coin]).dropna()
    if values.empty:
        return ""
    lo, hi = float(values.min()) - .5, float(values.max()) + .5
    left, right, top, bottom = 48, 640, 24, 200
    x0, x1 = lead.index.min().value, lead.index.max().value
    def x(stamp):
        return left + (stamp.value - x0) / max(x1 - x0, 1) * (right - left)
    def y(v):
        return bottom - (v - lo) / (hi - lo) * (bottom - top)
    parts = []
    for tick in np.arange(np.ceil(lo), hi + .01, 1 if hi - lo <= 8 else 2):
        parts.append(f'<line x1="{left}" y1="{y(tick):.1f}" x2="{right}" y2="{y(tick):.1f}" stroke="{"#8a9199" if tick == 100 else "#eceef1"}"'
                     f'{" stroke-dasharray=\"4 3\"" if tick == 100 else ""}/>'
                     f'<text x="{left - 6}" y="{y(tick) + 4:.1f}" font-size="11" text-anchor="end" fill="#6b7178">{tick:g}</text>')
    for year in range(lead.index.min().year + 1, lead.index.max().year + 1):
        stamp = pd.Timestamp(year=year, month=1, day=1)
        parts.append(f'<text x="{x(stamp):.1f}" y="{bottom + 16}" font-size="11" text-anchor="middle" fill="#6b7178">{year}</text>')
    for series, color, label, dx in ((lead, "#1a5490", "선행지수 순환변동치", 0), (coin, "#b3541e", "동행지수 순환변동치", 170)):
        if len(series):
            pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in series.items())
            parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>')
            parts.append(f'<rect x="{left + dx}" y="4" width="14" height="4" fill="{color}"/>'
                         f'<text x="{left + dx + 18}" y="9" font-size="11" fill="#1a1a1a">{label}</text>')
    if len(lead):
        last = lead.index[-1]
        parts.append(f'<circle cx="{x(last):.1f}" cy="{y(float(lead.iloc[-1])):.1f}" r="4" fill="#1a5490"/>')
    return (f'<svg viewBox="0 0 660 {bottom + 24}" width="100%" style="max-width:660px;min-width:360px;display:block" role="img" '
            f'aria-label="선행·동행지수 순환변동치">{"".join(parts)}</svg>')


def cycle_phase_html(data):
    """절 HTML. data 는 build_cycle_phase 결과."""
    if not data or not data.get("phase"):
        return ""
    phase, lead = data["phase"], data["leading"]
    e = escape
    head = ('<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">경기 국면 — 선행지수 순환변동치 '
            f'<span style="font-weight:400;color:#8a9199;font-size:12px">&nbsp;{_fmt_month(phase["last_month"])}까지 · 월별 · 통계청 경기종합지수</span></h3>')
    color = "#a8322a" if "하락" in phase["state"] or "정점" in phase["state"] else "#1e6b34"
    verdict = (f'<div style="margin:6px 0 10px;padding:9px 12px;border-left:4px solid {color};background:#fafafa;font-size:13px">'
               f'<b>국면 판정: <span style="color:{color}">{e(phase["state"])}</span></b> — '
               f'{_fmt_month(phase["last_month"])} {phase["level"]:.1f}. 최근 24개월 고점 {phase["peak"]:.1f}({_fmt_month(phase["peak_month"])}), '
               f'저점 {phase["trough"]:.1f}({_fmt_month(phase["trough_month"])}). '
               '규칙: 고점 뒤 1개월 하락이면 "정점 가능성", 2개월 이상 연속이면 "하락 국면 전환"(저점 쪽도 같음). '
               '정점·저점은 지나고 나서야 확정되므로 판정은 매달 바뀔 수 있습니다.</div>')
    items = []
    gap = data.get("gap")
    if gap:
        move = ""
        if gap.get("prev") is not None:
            move = " · 3개월 전 " + f'{gap["prev"]:+.1f} → 지금 {gap["now"]:+.1f}' + ("(격차 축소)" if abs(gap["now"]) < abs(gap["prev"]) else "(격차 확대)")
        items.append(f'<b>선행 − 동행</b>: {gap["now"]:+.1f}p ({_fmt_month(gap["month"])}, 동행 {gap["coincident_last"]:.1f}){move}. '
                     '선행지수에는 주가·금리차 같은 금융 변수가 들어가므로, 격차는 금융과 실물의 괴리로 읽습니다.')
    elif data.get("coincident_info", {}).get("failed"):
        items.append(f'동행지수: 받지 못했습니다({e(data["coincident_info"]["failed"])}).')
    for key, name in (("nsi", "뉴스심리지수(3개월 이동평균)"), ("spread", "장단기 금리차(국고채 10년−3년)")):
        d, lag = data.get(f"{key}_dir"), data.get(f"{key}_lag")
        if not d or not lag or not lag.get("best_level"):
            continue
        bl, bc = lag["best_level"], lag.get("best_change")
        items.append(f'<b>{name}</b>: 최근 3개월 {e(d["sign"])}({d["delta"]:+.2f}, {_fmt_month(d["from"])}→{_fmt_month(d["to"])}). '
                     f'우리 자료에서 선행지수에 {bl[0]}개월 앞설 때 수준 상관 {bl[1]:.2f}(n={bl[2]})'
                     + (f', 월간 변화끼리는 {bc[1]:.2f}' if bc else '') + '. 수준 상관은 추세를 함께 타서 커 보이므로 '
                     '방향 신호로는 변화 상관을 봅니다.')
    returns = data.get("returns") or {}
    table = ""
    if returns:
        rows = "".join(f'<tr><td style="padding:4px 8px;border-bottom:1px solid #eee">{e(k)}</td>'
                       f'<td style="padding:4px 8px;text-align:right;border-bottom:1px solid #eee">{v["n"]}</td>'
                       f'<td style="padding:4px 8px;text-align:right;border-bottom:1px solid #eee">{v["mean"]:+.2%}</td>'
                       f'<td style="padding:4px 8px;text-align:right;border-bottom:1px solid #eee">{v["median"]:+.2%}</td>'
                       f'<td style="padding:4px 8px;text-align:right;border-bottom:1px solid #eee">{v["down_share"]:.0%}</td></tr>'
                       for k, v in returns.items())
        table = ('<div style="font-size:13px;margin:10px 0 4px"><b>국면별 코스피 월수익률</b> '
                 '<span style="color:#6b7178;font-size:12px">— 선행지수가 오른 달·내린 달의 다음 달(발표 지연을 감안). 과거 분포이지 전망이 아닙니다</span></div>'
                 '<div style="overflow-x:auto"><table style="border-collapse:collapse;font-size:13px;min-width:420px">'
                 '<tr><th style="text-align:left;padding:4px 8px;border-bottom:1px solid #ddd">국면</th><th style="padding:4px 8px;border-bottom:1px solid #ddd">달 수</th>'
                 '<th style="padding:4px 8px;border-bottom:1px solid #ddd">평균</th><th style="padding:4px 8px;border-bottom:1px solid #ddd">중앙값</th>'
                 '<th style="padding:4px 8px;border-bottom:1px solid #ddd">하락한 달 비율</th></tr>' + rows + '</table></div>')
    bullets = '<ul style="margin:0 0 6px;padding-left:20px;font-size:13px;line-height:1.7">' + "".join(f"<li>{x}</li>" for x in items) + "</ul>" if items else ""
    note = ('<div style="font-size:12px;color:#6b7178;margin-top:6px">선행지수 순환변동치는 통계청 경기종합지수(2020=100)의 추세를 뺀 값입니다. '
            '여기서는 국면과 과거 분포만 적고, 전망이나 자산 배분은 하지 않습니다. 자료: KOSIS(DT_1C8015), 한국은행 ECOS(뉴스심리지수·금리), 코스피 월별 종가.</div>')
    return head + verdict + cycle_chart_svg(lead, data.get("coincident")) + bullets + table + note


def cycle_phase_line(data):
    """종목 보고서 장기 전망 요약용 한 줄(2026-10-01). 자료가 없으면 ''."""
    if not data or not data.get("phase"):
        return ""
    p = data["phase"]
    return (f'선행지수 순환변동치 {p["level"]:.1f}({_fmt_month(p["last_month"])}) — {p["state"]}. '
            f'최근 고점 {p["peak"]:.1f}({_fmt_month(p["peak_month"])})')
