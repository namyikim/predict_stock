# -*- coding: utf-8 -*-
"""장 마감 회고 — 오늘 장이 어땠고, 아침 예측이 어디서 맞고 틀렸으며, 흐름이 바뀐 시각 전후에
무슨 뉴스·공시가 있었는지를 보고서에 한 절로 붙인다.

할 수 있는 것과 없는 것을 분명히 한다.
  - 5분봉으로 흐름이 바뀐 시각을 찾고, 그 전후에 발행된 헤드라인·공시를 나란히 놓는다.
  - 갭(밤사이 해외) / 장중 급변 / 지수 동조 / 마감 동시호가 중 어느 성격인지 숫자 근거로 판정한다.
  - **시간이 맞는 것이지 원인 확정이 아니다.** 뉴스 없이 움직이는 날이 많고, 기사 발행 시각은
    실제 정보 유통보다 늦거나 앞선다. 이 절은 설명 도구이며 다음날 예측에 자동 반영하지 않는다.

    python tools/build_session_review.py --target samsung --out runs/review
    python tools/build_session_review.py --target samsung --out runs/review --date 2026-09-09
    python tools/build_session_review.py --target samsung --out runs/review --publish
"""
import argparse
import email.utils
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import github_pages  # noqa: E402

KST = timezone(timedelta(hours=9))
TARGETS = {
    "samsung": {"ticker": "005930.KS", "name": "삼성전자", "peer": "000660.KS", "peer_name": "SK하이닉스",
                "corp_code": "00126380", "headline": "No macro ensemble"},
    "sk_hynix": {"ticker": "000660.KS", "name": "SK하이닉스", "peer": "005930.KS", "peer_name": "삼성전자",
                 "corp_code": "00164779", "headline": "No macro ensemble"},
}
MARK_START, MARK_END = "<!--REVIEW_SECTION_START-->", "<!--REVIEW_SECTION_END-->"
LEDGER_END = "<!--LEDGER_SECTION_END-->"
DISCLAIMER = ("시간이 맞는 것이지 원인 확정이 아닙니다. 뉴스 없이 움직이는 날(수급·프로그램·옵션 만기)이 많고, "
              "기사 발행 시각은 실제 정보 유통보다 늦거나 앞섭니다. 이 절은 설명 도구이며 다음날 예측에 "
              "자동 반영되지 않습니다.")
# 전환점 판정 문턱. 그날 5분 수익률의 robust σ 배수.
EVENT_Z, VOLUME_SPIKE, MERGE_MINUTES, MAX_EVENTS = 3.0, 3.0, 15, 3
NEWS_BEFORE_MIN, NEWS_AFTER_MIN = 90, 15
RELEVANT = ("반도체", "HBM", "메모리", "D램", "DRAM", "낸드", "파운드리", "실적", "영업이익", "공시", "관세", "수출",
            "감산", "증설", "투자", "엔비디아", "마이크론", "TSMC", "금리", "환율", "외국인", "자사주", "배당",
            "목표주가", "증권", "코스피", "주가", "AI")
NOISE = ("갤럭시", "세탁기", "에어컨", "TV", "서비스센터", "히트펌프", "냉장고", "이벤트", "할인", "출시", "체험")


# ---------------------------------------------------------------------------
# 시세
# ---------------------------------------------------------------------------
def _yf(ticker, **kwargs):
    import yfinance as yf
    frame = yf.Ticker(ticker).history(**kwargs)
    if frame.empty:
        return frame
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    return frame


def load_daily(ticker, days=60):
    frame = _yf(ticker, period=f"{days}d", interval="1d")
    if frame.empty:
        return frame
    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
    return frame[~frame.index.duplicated()].sort_index()


def load_intraday(ticker, session_date):
    """그 날짜(KST)의 5분봉. 없으면 빈 프레임."""
    frame = _yf(ticker, period="5d", interval="5m")
    if frame.empty:
        return frame
    idx = pd.to_datetime(frame.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx
    frame.index = idx.tz_convert("Asia/Seoul")
    frame = frame[frame.index.date == pd.Timestamp(session_date).date()]
    return frame[frame["volume"] > 0] if "volume" in frame else frame


# ---------------------------------------------------------------------------
# 전환점 탐지 (순수 함수)
# ---------------------------------------------------------------------------
def detect_events(bars, prev_close=None, z_threshold=EVENT_Z, volume_spike=VOLUME_SPIKE,
                  merge_minutes=MERGE_MINUTES, max_events=MAX_EVENTS):
    """5분봉에서 흐름이 바뀐 시각을 찾는다. 결정적이다(같은 입력 → 같은 출력).

    기준은 둘이다. (1) 봉 수익률이 그날 robust σ의 z_threshold배 이상, (2) 거래량이 봉 중앙값의
    volume_spike배 이상. merge_minutes 안의 이웃은 점수가 큰 것 하나로 합친다.
    반환: [{time, ret, z, volume_ratio, cum_before, cum_after, session_share}] 점수 내림차순.
    """
    if bars is None or len(bars) < 12:
        return []
    close = bars["close"].astype(float)
    ret = close.pct_change()
    if prev_close is not None and np.isfinite(prev_close) and prev_close > 0:
        ret.iloc[0] = close.iloc[0] / float(prev_close) - 1      # 첫 봉은 갭을 포함한다
    ret = ret.fillna(0.0)
    mad = float(np.median(np.abs(ret.to_numpy() - np.median(ret.to_numpy()))))
    sigma = 1.4826 * mad if mad > 0 else float(ret.std(ddof=0)) or 1e-9
    vol = bars["volume"].astype(float) if "volume" in bars else pd.Series(1.0, index=bars.index)
    vol_med = float(np.median(vol[vol > 0])) if (vol > 0).any() else 1.0
    open_ = float(bars["open"].iloc[0]) if "open" in bars else float(close.iloc[0])
    session = float(close.iloc[-1] / open_ - 1) if open_ > 0 else 0.0
    cum = close / open_ - 1
    candidates = []
    for i, (stamp, r) in enumerate(ret.items()):
        z = abs(float(r)) / sigma
        vr = float(vol.iloc[i]) / vol_med if vol_med > 0 else 0.0
        # 개장 봉은 동시호가 물량 때문에 거래량이 늘 크다. 거래량만으로는 잡지 않고 수익률(갭)로만 본다.
        # 거래량 급증만으로 잡을 때도 가격이 거의 안 움직였으면(σ의 1배 미만) 전환점이 아니다.
        volume_only = vr >= volume_spike and i > 0 and z >= 1.0
        if z < z_threshold and not volume_only:
            continue
        candidates.append({
            "time": stamp, "ret": float(r), "z": round(z, 2), "volume_ratio": round(vr, 2),
            "cum_before": float(cum.iloc[i - 1]) if i > 0 else 0.0, "cum_after": float(cum.iloc[i]),
            "session_share": (float(r) / session) if abs(session) > 1e-9 else float("nan"),
            "score": z + (1.0 if vr >= volume_spike else 0.0) + min(vr, 5.0) / 5.0,
        })
    candidates.sort(key=lambda e: (-e["score"], e["time"]))
    merged = []
    for cand in candidates:
        if any(abs((cand["time"] - m["time"]).total_seconds()) <= merge_minutes * 60 for m in merged):
            continue
        merged.append(cand)
        if len(merged) >= max_events:
            break
    return sorted(merged, key=lambda e: -e["score"])


def turning_point(bars):
    """시가 대비 누적 수익률의 고점·저점 시각과 어느 쪽이 먼저였는지."""
    if bars is None or len(bars) < 3:
        return None
    open_ = float(bars["open"].iloc[0]) if "open" in bars else float(bars["close"].iloc[0])
    cum = bars["close"].astype(float) / open_ - 1
    hi_t, lo_t = cum.idxmax(), cum.idxmin()
    return {"high_time": hi_t, "high": float(cum.max()), "low_time": lo_t, "low": float(cum.min()),
            "pattern": "고점 후 반락" if hi_t < lo_t else "저점 후 반등"}


def closing_share(bars, session):
    """15:20 이후 봉(마감 동시호가 포함)이 세션 수익률에서 차지한 몫."""
    if bars is None or len(bars) < 3 or abs(session) < 1e-9:
        return float("nan")
    close = bars["close"].astype(float)
    late = bars.index.map(lambda t: (t.hour, t.minute) >= (15, 20))
    if not late.any():
        return float("nan")
    first_late = np.flatnonzero(late)[0]
    if first_late == 0:
        return float("nan")
    late_ret = float(close.iloc[-1] / close.iloc[first_late - 1] - 1)
    return late_ret / session


# ---------------------------------------------------------------------------
# 성격 판정 (순수 함수)
# ---------------------------------------------------------------------------
def classify_session(c2c, gap, session, events, kospi_c2c=None, close_share=float("nan"), intraday_corr=None,
                     close_label=None):
    """흐름의 성격을 규칙으로 판정하고 근거를 함께 돌려준다. 여러 성격이 겹칠 수 있다.

    close_label이 None이면 close_share는 15:20 이후 봉(마감 동시호가)의 몫이고, 문자열이면 그 구간
    (예: "14:55~종가", 5분봉이 끊긴 뒤 일봉 종가까지)의 몫이다. 이름을 달리 붙여 혼동을 막는다.
    """
    labels, reasons = [], []
    if abs(c2c) < 0.003:
        labels.append("방향성 약함")
        reasons.append(f"종가→종가 {c2c:+.2%}로 ±0.3% 이내")
    if abs(gap) > 0.005 and np.sign(gap) == np.sign(c2c) and abs(gap) >= 0.6 * abs(c2c):
        labels.append("갭 주도")
        reasons.append(f"갭 {gap:+.2%}가 종가→종가 {c2c:+.2%}의 {abs(gap) / max(abs(c2c), 1e-9):.0%}")
    strong = [e for e in events if e["z"] >= 3.0 and np.isfinite(e["session_share"]) and abs(e["session_share"]) >= 0.4]
    if strong:
        e = strong[0]
        labels.append(f"장중 급변 {e['time'].strftime('%H:%M')} 주도")
        reasons.append(f"{e['time'].strftime('%H:%M')} 봉 {e['ret']:+.2%}(σ의 {e['z']}배, 거래량 {e['volume_ratio']}배)가 "
                       f"세션 {session:+.2%}의 {abs(e['session_share']):.0%}")
    if kospi_c2c is not None and np.isfinite(kospi_c2c) and abs(c2c) >= 0.003:
        same_sign = np.sign(kospi_c2c) == np.sign(c2c)
        close_enough = abs(c2c - kospi_c2c) <= max(0.5 * abs(c2c), 0.003)
        corr_ok = intraday_corr is not None and np.isfinite(intraday_corr) and intraday_corr >= 0.6
        if same_sign and (close_enough or corr_ok):
            labels.append("지수 동조")
            detail = f"KOSPI {kospi_c2c:+.2%} vs 종목 {c2c:+.2%}"
            if intraday_corr is not None and np.isfinite(intraday_corr):
                detail += f", 5분 수익률 상관 {intraday_corr:.2f}"
            reasons.append(detail + " — 종목 고유 요인이 약함")
    if np.isfinite(close_share) and abs(close_share) >= 0.3 and abs(session) >= 0.003:
        if close_label is None:
            labels.append("마감 동시호가 쏠림")
            reasons.append(f"15:20 이후가 세션 {session:+.2%}의 {abs(close_share):.0%}")
        else:
            labels.append(f"마감 구간 쏠림({close_label})")
            reasons.append(f"{close_label} 구간이 세션 {session:+.2%}의 {abs(close_share):.0%} (5분봉이 없는 구간, 일봉 종가 기준)")
    if not labels:
        labels.append("완만한 추세")
        reasons.append("특정 시각·갭·지수로 설명되는 큰 구간 없음")
    return {"labels": labels, "reasons": reasons}


# ---------------------------------------------------------------------------
# 뉴스·공시
# ---------------------------------------------------------------------------
def parse_rss(raw):
    """Google News RSS → [{time(KST), title, source, link}]. 시각을 못 읽는 항목은 뺀다."""
    items = []
    for it in ET.fromstring(raw).findall(".//item"):
        pub = it.findtext("pubDate")
        try:
            when = email.utils.parsedate_to_datetime(pub).astimezone(KST)
        except Exception:
            continue
        title = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        items.append({"time": when, "title": title, "source": source, "link": (it.findtext("link") or "").strip()})
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["time"]):
        key = re.sub(r"\s+", " ", it["title"]).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def fetch_news(queries, timeout=30):
    out = []
    for query in queries:
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "&hl=ko&gl=KR&ceid=KR:ko")
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; predict-stock-review/1.0)"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                out += parse_rss(response.read())
        except Exception as exc:
            print(f"  ⚠️ 뉴스 조회 실패({query!r}): {type(exc).__name__}")
    seen, merged = set(), []
    for it in sorted(out, key=lambda x: x["time"]):
        key = re.sub(r"\s+", " ", it["title"]).lower()
        if key not in seen:
            seen.add(key)
            merged.append(it)
    return merged


def score_headline(title, name):
    score = 3 if name in title else 0
    score += sum(2 for k in RELEVANT if k.lower() in title.lower())
    score -= sum(2 for k in NOISE if k in title)
    return score


def news_in_window(items, start, end, name, limit=5):
    """[start, end] 안의 헤드라인을 관련도 순으로."""
    hits = [it for it in items if start <= it["time"] <= end]
    hits.sort(key=lambda it: (-score_headline(it["title"], name), it["time"]))
    return hits[:limit]


def fetch_disclosures(corp_code, session_date):
    try:
        from data_sources.dart import dart_key_optional, fetch_dart_disclosures
    except Exception:
        return None, "DART 모듈 없음"
    key = dart_key_optional()
    if not key:
        return None, "DART 키 없음 — 공시 미조회"
    day = pd.Timestamp(session_date).strftime("%Y%m%d")
    try:
        rows = fetch_dart_disclosures(corp_code, key, day, day, max_pages=1)
        return rows, None
    except Exception as exc:
        return None, f"DART 조회 실패({type(exc).__name__})"


# ---------------------------------------------------------------------------
# 아침 예측과 비교
# ---------------------------------------------------------------------------
def load_ledger(target, storage, token):
    remote = github_pages.fetch(f"forecast_history/{target}/forecast_log.csv", token) if token else None
    if remote:
        import io
        return pd.read_csv(io.StringIO(remote))
    local = Path(storage) / "forecast_log.csv"
    if local.is_file():
        return pd.read_csv(local)
    for candidate in (ROOT / "forecast_history" / target / "forecast_log.csv",):
        if candidate.is_file():
            return pd.read_csv(candidate)
    return pd.DataFrame()


def explain_forecast(row, c2c, gap, session):
    """방향 예측 한 행을 실제와 대조해 어디서 맞고 틀렸는지 문장으로."""
    band = float(row.get("band", np.nan))
    probs = [float(row.get(k, np.nan)) for k in ("p_down", "p_flat", "p_up")]
    if not np.isfinite(band) or not all(np.isfinite(probs)):
        return None
    actual = 0 if c2c < -band else 2 if c2c > band else 1
    predicted = int(np.argmax(probs))
    names = ["하락", "보합", "상승"]
    hit = actual == predicted
    leg = []
    for label, value in (("갭", gap), ("세션", session)):
        cls = 0 if value < -band else 2 if value > band else 1
        leg.append(f"{label} {value:+.2%}({names[cls]})")
    if hit:
        verdict = f"맞음 — 예측 {names[predicted]} {probs[predicted]:.0%}, 실제 {names[actual]}({c2c:+.2%})"
    else:
        verdict = f"틀림 — 예측 {names[predicted]} {probs[predicted]:.0%}, 실제 {names[actual]}({c2c:+.2%})"
    # 어느 구간이 결과를 만들었나
    if abs(gap) >= abs(session):
        where = "결과는 주로 갭(밤사이)에서 정해졌습니다."
    else:
        where = "결과는 주로 장중에서 정해졌습니다."
    if not hit and np.sign(gap) == np.sign(np.array([-1, 0, 1])[predicted]) and np.sign(session) != np.sign(gap) and abs(session) > band:
        where = "갭은 예측 방향이었으나 장중에 반대로 움직여 결과가 뒤집혔습니다."
    return {"model": row.get("model"), "hit": hit, "verdict": verdict, "legs": " · ".join(leg), "where": where,
            "band": band, "probs": probs, "actual": names[actual], "predicted": names[predicted]}


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
TD = 'style="padding:6px 10px;border-top:1px solid #eee"'
TDR = 'style="padding:6px 10px;border-top:1px solid #eee;text-align:right;font-variant-numeric:tabular-nums"'
TH = 'style="padding:8px 10px;text-align:left;font-size:11px;color:#6b7178;letter-spacing:.5px;background:#fafafa"'


def _pct(x, d=2):
    return "—" if x is None or not np.isfinite(x) else f"{x * 100:+.{d}f}%"


def render_section(review):
    e = html.escape
    r = review
    parts = [MARK_START,
             '<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
             f'오늘 장 회고 — {e(r["session_date"])}</h3>',
             f'<div style="font-size:12px;color:#6b7178;margin:4px 0 8px;padding:8px 12px;background:#f7f8fa;border-radius:5px">'
             f'<b>장 마감 후 생성 {e(r["generated_at"])}</b> — 이 절은 오늘 장을 설명할 뿐 위 성능표·다음 거래일 예측을 바꾸지 않습니다.</div>']
    s = r["summary"]
    rows = [("전일 종가 → 시가 (갭)", _pct(s["gap"])), ("시가 → 종가 (세션)", _pct(s["session"])),
            ("전일 종가 → 종가", _pct(s["c2c"])), ("고가 / 저가 (시가 대비)", f'{_pct(s["high_vs_open"])} / {_pct(s["low_vs_open"])}'),
            ("거래량 (20일 평균 대비)", f'{s["volume_ratio"]:.2f}배' if np.isfinite(s["volume_ratio"]) else "—"),
            (f'KOSPI / {e(r["peer_name"])}', f'{_pct(s.get("kospi_c2c"))} / {_pct(s.get("peer_c2c"))}'),
            ("원/달러", _pct(s.get("usdkrw_chg"))), ("전날 밤 SOX / 나스닥", f'{_pct(s.get("sox_ret"))} / {_pct(s.get("nasdaq_ret"))}')]
    if r.get("flows"):
        rows.append(("외국인 / 기관 순매수", e(r["flows"])))
    body = "".join(f'<tr><td {TD}>{e(k)}</td><td {TDR}>{v}</td></tr>' for k, v in rows)
    parts.append('<div style="overflow-x:auto"><table style="width:100%;min-width:420px;border-collapse:collapse;font-size:13px;border:1px solid #e5e5e5">'
                 f'<tr><th {TH}>오늘 장</th><th {TH}></th></tr>{body}</table></div>')
    # 성격
    c = r["classification"]
    badge = " · ".join(f'<b>{e(l)}</b>' for l in c["labels"])
    parts.append(f'<div style="margin:12px 0 4px;font-size:14px">흐름의 성격: {badge}</div>'
                 '<ul style="margin:0 0 10px;padding-left:20px;font-size:13px;color:#4a4f55">'
                 + "".join(f"<li>{e(x)}</li>" for x in c["reasons"]) + "</ul>")
    # 예측 비교
    parts.append('<div style="font-size:14px;margin:14px 0 6px"><b>아침 예측과 비교</b></div>')
    if r["forecasts"]:
        for f in r["forecasts"]:
            color = "#1a7f37" if f["hit"] else "#a8322a"
            parts.append(f'<div style="font-size:13px;margin:4px 0 8px;padding:8px 12px;border-left:3px solid {color};background:#fafafa">'
                         f'<b>{e(str(f["model"]))}</b> · {e(f["verdict"])}<br>'
                         f'<span style="color:#6b7178">{e(f["legs"])} · 보합 밴드 ±{f["band"] * 100:.2f}% · {e(f["where"])}</span></div>')
        if r.get("price_check"):
            parts.append(f'<div style="font-size:12px;color:#6b7178;margin:2px 0 8px">{e(r["price_check"])}</div>')
    else:
        parts.append('<div style="font-size:13px;color:#6b7178">오늘 예측일의 아침 예측 기록이 원장에 없습니다.</div>')
    # 전환점과 뉴스
    parts.append('<div style="font-size:14px;margin:14px 0 6px"><b>흐름이 바뀐 시각과 그 전후의 뉴스</b></div>')
    if r.get("intraday_note"):
        parts.append(f'<div style="font-size:13px;color:#a8322a">{e(r["intraday_note"])}</div>')
    if r.get("overnight_news") is not None:
        parts.append('<div style="font-size:13px;margin:6px 0 2px"><b>밤사이 (전일 15:30 ~ 09:00)</b> → 갭 ' + _pct(s["gap"]) + "</div>")
        parts.append(_news_list(r["overnight_news"]))
    for ev in r["events"]:
        t = ev["time"].strftime("%H:%M") if hasattr(ev["time"], "strftime") else str(ev["time"])
        parts.append(f'<div style="font-size:13px;margin:8px 0 2px"><b>{t}</b> · 봉 {_pct(ev["ret"])} (σ의 {ev["z"]}배, 거래량 {ev["volume_ratio"]}배) · '
                     f'시가 대비 {_pct(ev["cum_before"])} → {_pct(ev["cum_after"])}</div>')
        parts.append(_news_list(ev.get("news", [])))
    if r.get("turning_point"):
        tp = r["turning_point"]
        parts.append(f'<div style="font-size:12px;color:#6b7178;margin:6px 0">경로: 시가 대비 고점 {_pct(tp["high"])}({tp["high_time"].strftime("%H:%M")}) · '
                     f'저점 {_pct(tp["low"])}({tp["low_time"].strftime("%H:%M")}) — {e(tp["pattern"])}</div>')
    # 공시
    if r.get("disclosures") is not None:
        if r["disclosures"]:
            parts.append('<div style="font-size:13px;margin:10px 0 2px"><b>오늘 공시(DART)</b></div><ul style="margin:0;padding-left:20px;font-size:13px">'
                         + "".join(f'<li><a href="{e(d["url"])}" style="color:#1a5490">{e(d["report_nm"])}</a></li>' for d in r["disclosures"]) + "</ul>")
        else:
            parts.append('<div style="font-size:12px;color:#6b7178;margin:6px 0">오늘 공시(DART): 없음</div>')
    elif r.get("disclosure_note"):
        parts.append(f'<div style="font-size:12px;color:#6b7178;margin:6px 0">{e(r["disclosure_note"])}</div>')
    # 그날의 주요 헤드라인
    if r.get("top_news"):
        parts.append('<div style="font-size:13px;margin:10px 0 2px"><b>오늘의 관련 헤드라인 (관련도 순)</b></div>' + _news_list(r["top_news"]))
    parts.append(f'<div style="margin:12px 0 0;padding:10px 14px;background:#fff4e5;border:1px solid #f0c58a;border-radius:6px;font-size:12px;color:#7a4b00">{e(DISCLAIMER)}</div>')
    parts.append(MARK_END)
    return "".join(parts)


def _news_list(items):
    e = html.escape
    if not items:
        return '<div style="font-size:12px;color:#8a9199;margin:2px 0 4px">관련 뉴스 없음(이 창에 발행된 헤드라인이 없거나 조회하지 않음)</div>'
    return ('<ul style="margin:0 0 6px;padding-left:20px;font-size:12px;color:#4a4f55">'
            + "".join(f'<li>{it["time"].strftime("%H:%M")} · <a href="{e(it["link"])}" style="color:#1a5490">{e(it["title"])}</a>'
                      f' <span style="color:#8a9199">{e(it["source"])}</span></li>' for it in items) + "</ul>")


def insert_section(page, section):
    """회고 절을 넣는다. 이미 있으면 교체, 없으면 원장 절 뒤, 그것도 없으면 body 끝."""
    start, end = page.find(MARK_START), page.find(MARK_END)
    if start >= 0 and end > start:
        return page[:start] + section + page[end + len(MARK_END):]
    anchor = page.find(LEDGER_END)
    if anchor >= 0:
        cut = anchor + len(LEDGER_END)
        return page[:cut] + section + page[cut:]
    body_end = page.rfind("</body>")
    return page[:body_end] + section + page[body_end:] if body_end >= 0 else page + section


# ---------------------------------------------------------------------------
def build_review(target, session_date, storage, token=None, use_news=True):
    spec = TARGETS[target]
    session_date = pd.Timestamp(session_date).normalize()
    daily = load_daily(spec["ticker"])
    if daily.empty or session_date not in daily.index:
        raise SystemExit(f"{spec['ticker']}의 {session_date.date()} 일봉이 없습니다(휴장일이거나 아직 마감 전).")
    pos = daily.index.get_loc(session_date)
    if pos == 0:
        raise SystemExit("전일 봉이 없어 갭을 계산할 수 없습니다.")
    today, prev = daily.iloc[pos], daily.iloc[pos - 1]
    prev_close = float(prev["close"])
    gap = float(today["open"]) / prev_close - 1
    session = float(today["close"]) / float(today["open"]) - 1
    c2c = float(today["close"]) / prev_close - 1
    vol_hist = daily["volume"].iloc[max(0, pos - 20):pos]
    summary = {
        "prev_close": prev_close, "open": float(today["open"]), "high": float(today["high"]), "low": float(today["low"]),
        "close": float(today["close"]), "gap": gap, "session": session, "c2c": c2c,
        "high_vs_open": float(today["high"]) / float(today["open"]) - 1, "low_vs_open": float(today["low"]) / float(today["open"]) - 1,
        "volume": float(today["volume"]), "volume_ratio": float(today["volume"]) / float(vol_hist.mean()) if len(vol_hist) and vol_hist.mean() > 0 else float("nan"),
    }

    def c2c_of(ticker):
        frame = load_daily(ticker)
        if frame.empty or session_date not in frame.index:
            return float("nan")
        i = frame.index.get_loc(session_date)
        return float(frame["close"].iloc[i] / frame["close"].iloc[i - 1] - 1) if i > 0 else float("nan")

    def overnight_of(ticker):
        frame = load_daily(ticker)
        frame = frame[frame.index < session_date]
        return float(frame["close"].iloc[-1] / frame["close"].iloc[-2] - 1) if len(frame) >= 2 else float("nan")

    summary["kospi_c2c"] = c2c_of("^KS11")
    summary["peer_c2c"] = c2c_of(spec["peer"])
    summary["usdkrw_chg"] = c2c_of("KRW=X")
    summary["sox_ret"], summary["nasdaq_ret"] = overnight_of("^SOX"), overnight_of("^IXIC")

    bars = load_intraday(spec["ticker"], session_date)
    intraday_note, coverage = None, None
    if len(bars) < 30:
        intraday_note = f"장중 5분봉이 {len(bars)}개뿐이라 전환점 탐지를 건너뜁니다."
        events, tp, cshare, corr, close_label = [], None, float("nan"), None, None
    else:
        close_label = None
        events = detect_events(bars, prev_close=prev_close)
        tp = turning_point(bars)
        cshare = closing_share(bars, session)
        coverage = f"{bars.index.min().strftime('%H:%M')}~{bars.index.max().strftime('%H:%M')}"
        # Yahoo의 KRX 5분봉은 보통 14:55에서 끝나 마감 동시호가(15:20~15:30)가 없다. 그 구간은
        # 일봉 종가로만 본다: 마지막 5분봉 종가 → 일봉 종가가 세션에서 차지한 몫.
        last_bar = bars.index.max()
        if (last_bar.hour, last_bar.minute) < (15, 20):
            tail_ret = summary["close"] / float(bars["close"].iloc[-1]) - 1
            cshare = tail_ret / session if abs(session) > 1e-9 else float("nan")
            close_label = f"{last_bar.strftime('%H:%M')}~종가"
            intraday_note = (f"장중 봉은 {coverage}까지만 있어 마감 구간({last_bar.strftime('%H:%M')}~15:30)은 "
                             f"일봉 종가로만 봅니다(그 구간 {tail_ret:+.2%}).")
        corr = None
        kbars = load_intraday("^KS11", session_date)
        if len(kbars) >= 30:
            a = bars["close"].pct_change().dropna()
            b = kbars["close"].pct_change().dropna()
            joined = pd.concat([a, b], axis=1, join="inner").dropna()
            corr = float(joined.iloc[:, 0].corr(joined.iloc[:, 1])) if len(joined) >= 20 else None
    classification = classify_session(c2c, gap, session, events, summary["kospi_c2c"], cshare, corr, close_label)

    # 뉴스
    overnight_news, top_news = None, []
    if use_news:
        # 날짜를 명시해야 --date로 지난 거래일을 회고할 때도 그날 기사가 온다(when:은 '지금' 기준이다).
        after = (session_date - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        before = (session_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        items = fetch_news([f"{spec['name']} after:{after} before:{before}",
                            f"반도체 주가 after:{session_date.strftime('%Y-%m-%d')} before:{before}"])
        day_start = session_date.tz_localize("Asia/Seoul")
        prev_close_t = (session_date - pd.Timedelta(days=1)).tz_localize("Asia/Seoul") + pd.Timedelta(hours=15, minutes=30)
        overnight_news = news_in_window(items, prev_close_t, day_start + pd.Timedelta(hours=9), spec["name"])
        for ev in events:
            ev["news"] = news_in_window(items, ev["time"] - pd.Timedelta(minutes=NEWS_BEFORE_MIN),
                                        ev["time"] + pd.Timedelta(minutes=NEWS_AFTER_MIN), spec["name"])
        day_items = [it for it in items if it["time"].date() == session_date.date()]
        top_news = sorted(day_items, key=lambda it: (-score_headline(it["title"], spec["name"]), it["time"]))[:6]
    disclosures, disclosure_note = fetch_disclosures(spec["corp_code"], session_date)

    # 수급(장 마감 후 확정). 실패해도 회고는 낸다.
    flows_text = None
    try:
        from data_sources.flows import load_investor_flows
        frame, _ = load_investor_flows(Path(storage), spec["ticker"], (session_date - pd.Timedelta(days=10)).date(),
                                       session_date.date())
        row = frame[frame["date"] == session_date]
        if len(row):
            row = row.iloc[0]
            cols = {c: row[c] for c in frame.columns if c != "date" and pd.notna(row[c])}
            frg = next((v for k, v in cols.items() if "frgn" in k or "foreign" in k), None)
            inst = next((v for k, v in cols.items() if "inst" in k), None)
            if frg is not None or inst is not None:
                flows_text = f"외국인 {frg:+,.0f} / 기관 {inst:+,.0f}" if frg is not None and inst is not None else str(cols)
    except Exception as exc:
        flows_text = None
        print(f"  수급 미확인({type(exc).__name__})")

    # 아침 예측
    ledger = load_ledger(target, storage, token)
    forecasts, price_check = [], None
    if len(ledger):
        rows = ledger[(ledger.get("prediction_date") == session_date.date().isoformat())]
        direction = rows[rows.get("kind", "direction").fillna("direction") == "direction"] if "kind" in rows else rows
        for model in (spec["headline"], "Mean ensemble", "Candidate expanding"):
            sub = direction[direction["model"] == model]
            if len(sub):
                ex = explain_forecast(sub.sort_values("created_at_utc").iloc[0], c2c, gap, session)
                if ex:
                    forecasts.append(ex)
        if "kind" in rows:
            price = rows[(rows["kind"] == "price") & (rows["horizon_days"] == 1)]
            if len(price):
                p = price.sort_values("created_at_utc").iloc[0]
                lo, hi = float(p.get("low_close", np.nan)), float(p.get("high_close", np.nan))
                if np.isfinite(lo) and np.isfinite(hi):
                    inside = lo <= summary["close"] <= hi
                    price_check = (f"1거래일 예상 구간 {lo:,.0f}~{hi:,.0f}원 · 종가 {summary['close']:,.0f}원 → "
                                   + ("구간 안" if inside else "구간 밖"))

    return {
        "target": target, "name": spec["name"], "peer_name": spec["peer_name"],
        "session_date": session_date.date().isoformat(),
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "summary": summary, "classification": classification, "events": events, "turning_point": tp,
        "closing_share": cshare, "intraday_corr_kospi": corr, "intraday_note": intraday_note,
        "intraday_coverage": coverage,
        "overnight_news": overnight_news, "top_news": top_news,
        "disclosures": disclosures, "disclosure_note": disclosure_note, "flows": flows_text,
        "forecasts": forecasts, "price_check": price_check, "disclaimer": DISCLAIMER,
    }


def to_json(review):
    def conv(o):
        if isinstance(o, (pd.Timestamp, datetime)):
            return o.isoformat()
        if isinstance(o, (np.floating, float)):
            return None if not np.isfinite(o) else float(o)
        if isinstance(o, np.integer):
            return int(o)
        return str(o)
    return json.dumps(review, ensure_ascii=False, indent=2, default=conv)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default="samsung", choices=list(TARGETS))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--date", help="회고할 거래일(KST). 기본은 오늘")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--no-news", action="store_true", help="뉴스·공시 조회 없이(오프라인 점검용)")
    parser.add_argument("--allow-partial", action="store_true", help="장 마감 전에도 실행(장중 점검용, 발행 금지)")
    args = parser.parse_args()
    storage = args.out / args.target
    storage.mkdir(parents=True, exist_ok=True)
    token = github_pages.token() if args.publish else None
    session_date = pd.Timestamp(args.date) if args.date else pd.Timestamp(datetime.now(KST).date())
    now_kst = datetime.now(KST)
    if session_date.date() == now_kst.date() and (now_kst.hour, now_kst.minute) < (15, 35) and not args.allow_partial:
        raise SystemExit(f"{session_date.date()} 장이 아직 끝나지 않았습니다(지금 {now_kst:%H:%M} KST). "
                         "마감(15:30) 뒤에 실행하거나 --allow-partial 을 붙이세요(장중 점검용).")

    review = build_review(args.target, session_date, storage, token=token, use_news=not args.no_news)
    section = render_section(review)
    (storage / f"review_{review['session_date']}.html").write_text(section, encoding="utf-8")
    (storage / f"review_{review['session_date']}.json").write_text(to_json(review), encoding="utf-8")
    s = review["summary"]
    print(f"{review['name']} {review['session_date']}: 갭 {s['gap']:+.2%} · 세션 {s['session']:+.2%} · 종가→종가 {s['c2c']:+.2%} · "
          f"거래량 {s['volume_ratio']:.2f}배")
    print("성격:", " · ".join(review["classification"]["labels"]))
    for reason in review["classification"]["reasons"]:
        print("  -", reason)
    for ev in review["events"]:
        print(f"  전환점 {ev['time'].strftime('%H:%M')} {ev['ret']:+.2%} (σ×{ev['z']}, 거래량×{ev['volume_ratio']}) 뉴스 {len(ev.get('news', []))}건")
    for f in review["forecasts"]:
        print(f"  예측 [{f['model']}] {f['verdict']} · {f['where']}")
    if not args.publish:
        print("발행하지 않았습니다(--publish 없음). 조각:", storage / f"review_{review['session_date']}.html")
        return
    if args.allow_partial and session_date.date() == now_kst.date() and (now_kst.hour, now_kst.minute) < (15, 35):
        raise SystemExit("장 마감 전 결과는 발행하지 않습니다.")

    now = datetime.now(KST)
    sha = github_pages.publish(f"forecast_history/{args.target}/reviews/{review['session_date']}.json", to_json(review), token,
                               f"review: {args.target} {review['session_date']} ({now:%H:%M} KST)")
    print(f"회고 기록 저장 forecast_history/{args.target}/reviews/{review['session_date']}.json @ {sha}")
    pages = [f"docs/{args.target}/index.html"]
    for candidate in (session_date, session_date + pd.Timedelta(days=1)):
        path = f"docs/{args.target}/reports/{candidate.date()}.html"
        if github_pages.fetch(path, token) is not None:
            pages.append(path)
    for path in pages:
        page = github_pages.fetch(path, token)
        if page is None:
            print(f"⚠️ {path} 없음 — 건너뜁니다.")
            continue
        sha = github_pages.publish(path, insert_section(page, section), token,
                                   f"review: {path} 장 마감 회고 ({now:%Y-%m-%d %H:%M} KST)")
        print(f"보고서 갱신 {path} @ {sha}")


if __name__ == "__main__":
    main()
