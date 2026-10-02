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
from review_context import price_context, buyback_transition

KST = timezone(timedelta(hours=9))
TARGETS = {
    "samsung": {"ticker": "005930.KS", "name": "삼성전자", "peer": "000660.KS", "peer_name": "SK하이닉스",
                "corp_code": "00126380", "headline": "No macro ensemble"},
    "sk_hynix": {"ticker": "000660.KS", "name": "SK하이닉스", "peer": "005930.KS", "peer_name": "삼성전자",
                 "corp_code": "00164779", "headline": "No macro ensemble"},
}
# 절 그리기는 forecast_utils 로 옮겼다(2026-09-23) — 노트북이 페이지를 새로 만들 때 같은 함수로 직전 회고를 다시 붙인다.
from forecast_utils import (  # noqa: E402
    REVIEW_DISCLAIMER as DISCLAIMER, REVIEW_END as MARK_END, REVIEW_LEDGER_END as LEDGER_END,
    REVIEW_START as MARK_START, _kr_events, flow_story, insert_review_section, kst_stamp, review_section_html,
    review_tab_html, stamp_changed_panels,
)
PROVISIONAL_RE = re.compile(r"영업\s*\(?\s*잠정\s*\)?\s*실적")
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


def session_context(ticker, session_date):
    """그날이 배당락일이면 주당 배당(원). Yahoo 배당 이력에서 찾고, 실패하거나 없으면 빈 dict.

    macro_inputs/corporate_actions.csv 에 사람이 확인해 적은 값이 있으면 렌더링 때 그쪽을 쓴다(2026-09-30).
    """
    try:
        import yfinance as yf
        dividends = yf.Ticker(ticker).dividends
        if dividends is None or dividends.empty:
            return {}
        dividends.index = pd.to_datetime(dividends.index).tz_localize(None).normalize()
        hit = dividends[dividends.index == pd.Timestamp(session_date).normalize()]
        if len(hit) and float(hit.iloc[0]) > 0:
            return {"dividend_krw": float(hit.iloc[0]), "dividend_source": "Yahoo 배당 이력"}
    except Exception as exc:
        print(f"  배당 이력 미확인({type(exc).__name__})")
    return {}


# ---------------------------------------------------------------------------
# 그날의 맥락 보강(2026-10-01, 장 회고 영상과 비교해 빠진 것)
# ---------------------------------------------------------------------------
def ma_touches(daily, pos):
    """장중 고가·저가가 이동평균선(전일까지로 계산 — 장중에 보이던 선)에 닿고 어떻게 끝났나.

    support: 저가가 선 부근(−1%~+0.5%)까지 내려왔다가 선 위(+0.5% 넘게)에서 마감 — '20일선 지지 후 반등'.
    resistance: 고가가 선 부근(−0.5%~+1%)까지 올랐다가 선 아래에서 마감 — '5일선 넘으려다 밀림'.
    reclaim: 장중 선 아래로 1% 넘게 빠졌다가 선 위에서 마감. fail: 장중 선 위로 1% 넘게 올랐다가 선 아래에서 마감.
    """
    closes = daily["close"].iloc[:pos]
    today = daily.iloc[pos]
    low, high, close = float(today["low"]), float(today["high"]), float(today["close"])
    out = []
    for n in (5, 20, 60):
        if len(closes) < n:
            continue
        ma = float(closes.tail(n).mean())
        if ma * 0.99 <= low <= ma * 1.005 and close > ma * 1.005:
            out.append({"ma": n, "level": ma, "kind": "support", "price": low})
        elif ma * 0.995 <= high <= ma * 1.01 and close < ma * 0.995:
            out.append({"ma": n, "level": ma, "kind": "resistance", "price": high})
        elif low < ma * 0.99 and close > ma:
            out.append({"ma": n, "level": ma, "kind": "reclaim", "price": low})
        elif high > ma * 1.01 and close < ma:
            out.append({"ma": n, "level": ma, "kind": "fail", "price": high})
    return out


def after_hours(ticker, session_date):
    """직전 미국 정규장 종가 → 오늘 09:00 KST 직전까지의 시간외 가격. 실적처럼 장 마감 뒤 소식의 반응이 여기 먼저 나온다.

    {regular_close, last, ret, low_ret, high_ret, last_time} 또는 None. (2026-10-01: 마이크론 '깜짝 실적' 뒤 정규장은 +0.00%였지만
    시간외에서 +1.8%까지 올랐다가 −1.3%까지 밀리고 보합으로 돌아왔다 — 회고에는 +0.00%만 보였다.)
    """
    try:
        bars = _yf(ticker, period="5d", interval="5m", prepost=True)
        if bars.empty:
            return None
        bars.index = pd.DatetimeIndex(bars.index).tz_convert("America/New_York")
        open_kst = pd.Timestamp(session_date).tz_localize("Asia/Seoul") + pd.Timedelta(hours=9)
        before = bars[bars.index < open_kst.tz_convert("America/New_York")]
        regular = before[(before.index.time >= pd.Timestamp("09:30").time()) & (before.index.time < pd.Timestamp("16:00").time())]
        if regular.empty:
            return None
        close_time = regular.index[-1]
        regular_close = float(regular["close"].iloc[-1])
        after = before[before.index > close_time]
        if after.empty or regular_close <= 0:
            return None
        last = float(after["close"].iloc[-1])
        return {"regular_close": regular_close, "last": last, "ret": last / regular_close - 1,
                # 범위는 종가로 — 시간외 봉의 저가·고가에는 체결이 드문 틈의 튀는 값이 섞인다(10/1 MU 저가 −28%).
                "low_ret": float(after["close"].min()) / regular_close - 1, "high_ret": float(after["close"].max()) / regular_close - 1,
                "last_time": after.index[-1].tz_convert("Asia/Seoul").isoformat()}
    except Exception as exc:
        print(f"  시간외 가격 미확인({ticker}: {type(exc).__name__})")
        return None


SESSION_CROSS = (("NQ=F", "나스닥100 선물"), ("ZN=F", "미 10년물 국채 선물"), ("KRW=X", "원/달러"))


def session_cross(session_date, high_time=None):
    """한국 장중(09:00~15:30 KST) 해외 선물·환율의 움직임. 고점 시각이 있으면 고점 이후 구간도.

    국채 선물은 가격이라 오르면 금리가 내린 것이다(영상의 '오후 미국 금리 급등' 같은 말을 자료로 확인한다).
    """
    out = {}
    start = pd.Timestamp(session_date).tz_localize("Asia/Seoul") + pd.Timedelta(hours=9)
    end = start + pd.Timedelta(hours=6, minutes=30)
    for ticker, label in SESSION_CROSS:
        try:
            bars = _yf(ticker, period="5d", interval="5m")
            if bars.empty:
                continue
            bars.index = pd.DatetimeIndex(bars.index).tz_convert("Asia/Seoul")
            seg = bars[(bars.index >= start) & (bars.index <= end)]["close"].dropna()
            if len(seg) < 10:
                continue
            row = {"label": label, "ret": float(seg.iloc[-1] / seg.iloc[0] - 1)}
            if high_time is not None:
                high_at = pd.Timestamp(high_time)
                high_at = high_at.tz_localize("Asia/Seoul") if high_at.tzinfo is None else high_at
                after = seg[seg.index >= high_at]
                if len(after) >= 3:
                    row["after_high"] = float(after.iloc[-1] / after.iloc[0] - 1)
            out[ticker] = row
        except Exception as exc:
            print(f"  장중 {label} 미확인({type(exc).__name__})")
    return out


# 실적 발표일 보관본(2026-10-02, 검토 후속 B). 예전 캐시는 runs/ 아래에만 있어 Actions 의 새 실행마다 비었고,
# 그때마다 2015년 이후 모든 분기(40여 개)를 DART 에 다시 물었다. 수급 보관본처럼 저장소(macro_history/)에 두고
# 회고를 발행할 때 함께 올린다. 다음 실행은 보관본에 없는 분기만 묻는다.
EARNINGS_DATES_ARCHIVE = Path("macro_history")


def earnings_dates_name(target):
    return f"earnings_dates_{target}.csv"


def read_earnings_dates(path):
    """{분기 말일: 발표일 또는 ''}. ''는 조회 창(분기 말 다음 날~45일)이 지났는데 공시를 찾지 못한 분기다.

    옛 캐시(date 열만 있는 파일)도 읽는다 — 발표일 직전 분기 말을 열쇠로 삼는다. 못 읽으면 빈 사전.
    """
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception:
        return {}
    out = {}
    for row in frame.to_dict("records"):
        date = str(row.get("date", "") or "").strip()
        quarter = str(row.get("quarter_end", "") or "").strip()
        try:
            if not quarter and date:
                quarter = (pd.Timestamp(date) - pd.offsets.QuarterEnd(1)).date().isoformat()
            if quarter:
                out[pd.Timestamp(quarter).date().isoformat()] = pd.Timestamp(date).date().isoformat() if date else ""
        except (TypeError, ValueError):
            continue
    return out


def earnings_dates_csv(known):
    return pd.DataFrame({"quarter_end": sorted(known), "date": [known[q] for q in sorted(known)]}).to_csv(index=False)


def collect_earnings_dates(target, corp_code, cache_dir, fetch, today, since=2015, archive_dir=None):
    """잠정실적 발표일을 모은다. (발표일 목록, 이번에 DART 에 물은 분기 수).

    저장소 보관본과 로컬 캐시에 이미 있는 분기는 다시 묻지 않는다. 조회 창이 끝났는데 공시가 없던 분기도 ''로 남겨
    다시 묻지 않는다 — 창이 아직 열려 있는 최근 분기만 발표가 나올 때까지 매번 묻는다.
    fetch(corp_code, start, stop) → 공시 행 목록([{rcept_dt, report_nm}]).
    """
    archive_dir = EARNINGS_DATES_ARCHIVE if archive_dir is None else archive_dir
    cache = Path(cache_dir) / earnings_dates_name(target)
    known = read_earnings_dates(Path(archive_dir) / earnings_dates_name(target))
    for quarter, date in read_earnings_dates(cache).items():
        if date or quarter not in known:
            known[quarter] = date
    today = pd.Timestamp(today).normalize()
    asked = 0
    for quarter_end in pd.date_range(f"{since}-03-31", today, freq="QE"):
        start, stop = quarter_end + pd.Timedelta(days=1), quarter_end + pd.Timedelta(days=45)
        quarter = quarter_end.date().isoformat()
        if start > today or known.get(quarter) or (quarter in known and stop < today):
            continue                                   # 이미 찾았거나 끝난 창에서 없다고 확인한 분기
        rows = fetch(corp_code, start, min(stop, today))
        asked += 1
        hits = [r["rcept_dt"] for r in rows if PROVISIONAL_RE.search(r["report_nm"])]
        if hits:
            known[quarter] = pd.Timestamp(min(hits)).date().isoformat()
        elif stop < today:
            known[quarter] = ""
    if known:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(earnings_dates_csv(known), encoding="utf-8")
    return sorted(date for date in known.values() if date), asked


def earnings_reactions(target, corp_code, cache_dir, since=2015):
    """과거 잠정실적(영업실적 공정공시) 발표일의 주가 반응. 발표 전 20거래일 등락으로 나눠 센다.

    영상의 규칙('발표 전 오르면 발표날 떨어지고, 발표 전 내렸으면 발표 뒤 반등')을 이 종목 기록으로 잰다.
    발표일은 DART 공시 목록에서 찾고(분기 끝 다음 날 ~ 45일), 장 시작 전 공시라 그날 종가/전일 종가가 반응이다.
    {n, down, mean, rows:[{date, ret, prior20, next5}], prior_up:{n, down}, prior_down:{n, up_next5}} 또는 None.
    """
    try:
        from data_sources.dart import dart_key_optional, fetch_dart_disclosures
        key = dart_key_optional()
        if not key:
            return None
        today = pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None).normalize()
        dates, asked = collect_earnings_dates(
            target, corp_code, cache_dir,
            lambda corp, start, stop: fetch_dart_disclosures(corp, key, start, stop, max_pages=3), today, since=since)
        print(f"  실적 발표일 {len(dates)}건 · 이번에 DART 에 물은 분기 {asked}개")
        if not dates:
            return None
        daily = load_daily(TARGETS[target]["ticker"], days=4400)
        close = daily["close"]
        rows = []
        for d in sorted(dates):
            day = pd.Timestamp(d)
            if day not in close.index:
                continue
            i = close.index.get_loc(day)
            if i < 21:
                continue
            rows.append({"date": d, "ret": float(close.iloc[i] / close.iloc[i - 1] - 1),
                         "prior20": float(close.iloc[i - 1] / close.iloc[i - 21] - 1),
                         "next5": float(close.iloc[i + 5] / close.iloc[i] - 1) if i + 5 < len(close) else None})
        if len(rows) < 8:
            return None
        frame = pd.DataFrame(rows)
        up, down = frame[frame["prior20"] > 0], frame[frame["prior20"] <= 0]
        nxt = down["next5"].dropna()
        return {"n": int(len(frame)), "down": int((frame["ret"] < 0).sum()), "mean": float(frame["ret"].mean()),
                "first": frame["date"].iloc[0], "last": frame["date"].iloc[-1],
                "prior_up": {"n": int(len(up)), "down": int((up["ret"] < 0).sum()),
                             "mean": float(up["ret"].mean()) if len(up) else None},
                "prior_down": {"n": int(len(down)), "down": int((down["ret"] < 0).sum()),
                               "mean": float(down["ret"].mean()) if len(down) else None,
                               "next5_n": int(len(nxt)), "next5_up": int((nxt > 0).sum())},
                "rows": rows[-8:]}
    except Exception as exc:
        print(f"  실적 발표일 반응 미확인({type(exc).__name__}: {str(exc)[:100]})")
        return None


def buyback_on(corp_code, session_date, lookback_days=400, include_ended=False):
    """그날이 회사의 자기주식 취득(직접·신탁) 기간 안인가. [{kind, start, end, amount, purpose}] 또는 빈 목록.

    기타 법인 순매수가 클 때 회사의 자사주 매입과 맞는 모양인지 보려고 쓴다(2026-10-01). 공시는 그날까지 나온 것만.
    include_ended=True이면 예정 종료 전후 비교를 위해 조회된 종료 기간도 반환한다.
    """
    try:
        from data_sources.dart import dart_key_optional, fetch_buyback_periods
        key = dart_key_optional()
        if not key:
            return []
        day = pd.Timestamp(session_date).normalize()
        rows = fetch_buyback_periods(corp_code, key, day - pd.Timedelta(days=lookback_days), day)
        if include_ended:
            return rows
        return [r for r in rows if pd.Timestamp(r["start"]) <= day <= pd.Timestamp(r["end"])]
    except Exception as exc:
        print(f"  자사주 매입 기간 미확인({type(exc).__name__})")
        return []


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


def timeline_data(bars, events, session_date):
    """회고 재렌더링용 최소 경로. 결측 구간을 보간하지 않는다(2026-09-30 요청)."""
    if bars is None or bars.empty or 'close' not in bars:
        return [], []
    day = pd.Timestamp(session_date).date()

    def stamp(value):
        t = pd.Timestamp(value)
        return t.tz_localize('Asia/Seoul') if t.tzinfo is None else t.tz_convert('Asia/Seoul')

    points = {}
    for raw_time, row in bars.iterrows():
        t = stamp(raw_time)
        price = pd.to_numeric(row['close'], errors='coerce')
        if pd.isna(t) or t.date() != day or not 540 <= t.hour * 60 + t.minute <= 930:
            continue
        if not np.isfinite(price) or price <= 0:
            continue
        volume = pd.to_numeric(row.get('volume'), errors='coerce')
        points[t.isoformat()] = {'time': t.isoformat(), 'price': float(price),
                                'volume': float(volume) if pd.notna(volume) and np.isfinite(volume) and volume >= 0 else None}
    path = [points[k] for k in sorted(points)]
    items = []
    for ev in events:
        t = stamp(ev['time']).isoformat()
        if t not in points:
            continue
        kind = 'volume' if ev['z'] < EVENT_Z else ('turn_up' if ev['ret'] > 0 else 'turn_down')
        items.append({**ev, 'time': t, 'price': points[t]['price'], 'kind': kind})
    return path, sorted(items, key=lambda item: item['time'])


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


# 시장 전체 숫자(2026-10-02, 장 마감 회고 영상과 비교해 더한 것). 영상은 종목보다 먼저 시장 전체를 본다 —
# 오른 종목이 내린 종목보다 많았는지, 외국인·개인·기관이 시장 전체에서 얼마나 사고팔았는지, 지수가 장 막판에
# 어디서 끝났는지. 회고에는 이 종목의 수급만 있고 시장 전체가 없었다. 네이버 증권의 지수 API 가 세 가지를 한 번에
# 준다(인증 없음). 최신 거래일 값만 주므로 날짜가 회고 대상일과 같을 때만 쓴다 — 지난 날짜 회고를 다시 만들 때
# 오늘 숫자를 끼워 넣지 않는다. 받지 못하면 이 칸만 빠지고 회고는 그대로 나온다.
NAVER_INDEX_URL = "https://m.stock.naver.com/api/index/{index}/{kind}"


def _market_number(text):
    """'-17,725' · '+3,987' · '7,003.74' → float. 읽을 수 없으면 None."""
    try:
        value = float(str(text).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def parse_market_overview(integration, basic=None):
    """네이버 지수 API 응답 → {date, rise, fall, steady, upper, lower, individual, foreign, institution, open, high,
    low, prev_close, close}. 순매수는 억원. 종목 수나 날짜를 읽지 못하면 None."""
    if not isinstance(integration, dict):
        return None
    breadth = integration.get("upDownStockInfo") or {}
    deal = integration.get("dealTrendInfo") or {}
    date = str(deal.get("bizdate") or "")
    counts = {name: _market_number(breadth.get(key)) for name, key in
              (("rise", "riseCount"), ("fall", "fallCount"), ("steady", "steadyCount"),
               ("upper", "upperCount"), ("lower", "lowerCount"))}
    if len(date) != 8 or not date.isdigit() or counts["rise"] is None or counts["fall"] is None:
        return None
    out = {"index": "KOSPI", "date": f"{date[:4]}-{date[4:6]}-{date[6:]}"}
    out.update({name: int(value) if value is not None else None for name, value in counts.items()})
    for name, key in (("individual", "personalValue"), ("foreign", "foreignValue"), ("institution", "institutionalValue")):
        out[name] = _market_number(deal.get(key))
    prices = {str(item.get("code")): _market_number(item.get("value"))
              for item in integration.get("totalInfos") or [] if isinstance(item, dict)}
    for name, key in (("prev_close", "lastClosePrice"), ("open", "openPrice"), ("high", "highPrice"), ("low", "lowPrice")):
        out[name] = prices.get(key)
    out["close"] = _market_number((basic or {}).get("closePrice")) if isinstance(basic, dict) else None
    return out


def fetch_market_overview(session_date, fetch=None, index="KOSPI"):
    """회고 대상일의 시장 전체 숫자. 날짜가 다르거나 받지 못하면 None(회고는 계속 만든다)."""
    def get(kind):
        request = urllib.request.Request(NAVER_INDEX_URL.format(index=index, kind=kind),
                                         headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    fetch = fetch or get
    try:
        integration = fetch("integration")
        try:
            basic = fetch("basic")
        except Exception:
            basic = None                      # 종가를 못 받아도 종목 수·수급은 보인다
        overview = parse_market_overview(integration, basic)
    except Exception as exc:
        print(f"  시장 전체 숫자 미확인({type(exc).__name__})")
        return None
    if not overview or overview["date"] != pd.Timestamp(session_date).date().isoformat():
        if overview:
            print(f"  시장 전체 숫자는 {overview['date']} 기준이라 {pd.Timestamp(session_date).date()} 회고에 쓰지 않습니다")
        return None
    return overview


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


def refresh_recent_disclosures(page, corp_code, now, fetch=None, days=10):
    """'공시·발표 일정' 탭의 최근 공시 목록을 지금 기준으로 다시 채운다(2026-10-02).

    종목 보고서는 아침에 한 번 공시를 받는다. 장중 공시는 회고의 '오늘 공시'에는 나오지만 이 탭에는 다음 보고서가
    만들어질 때까지 없었다. 회고를 발행하면서 최근 10일 목록을 다시 받아 바꾼다. 키가 없거나 조회에 실패하면
    페이지를 그대로 둔다 — 아침에 받은 목록을 '없음'으로 덮어쓰지 않는다. 바뀐 탭에만 '갱신 … KST'를 찍는다.
    """
    try:
        from data_sources.dart import classify_disclosure, dart_key_optional, fetch_dart_disclosures
        from report_html import replace_disclosure_block
        if fetch is None:
            key = dart_key_optional()
            if not key:
                return page
            fetch = lambda start, stop: fetch_dart_disclosures(corp_code, key, start, stop)
        today = pd.Timestamp(now).tz_localize(None).normalize() if pd.Timestamp(now).tzinfo else pd.Timestamp(now).normalize()
        since = today - pd.Timedelta(days=days)
        rows = fetch(since, today)
        info = {"enabled": True, "count": len(rows), "since": since.date().isoformat()}
        updated = replace_disclosure_block(page, rows, info, classify_disclosure)
        return stamp_changed_panels(page, updated, kst_stamp(now, "갱신")) if updated != page else page
    except Exception as exc:
        print(f"  최근 공시 목록 갱신 건너뜀({type(exc).__name__}: {str(exc)[:80]})")
        return page


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


def recent_reviews(target, review, days=4):
    """오늘 회고 + 저장소에 있는 그 전 회고들(최근 것부터). 날짜 단추로 지난 회고를 고를 수 있게 한다(2026-09-30).

    회고 작업은 저장소를 받아 둔 채 돌므로 forecast_history/<종목>/reviews/*.json 을 그대로 읽는다.
    """
    folder = ROOT / "forecast_history" / target / "reviews"
    older = []
    for path in sorted(folder.glob("*.json"), reverse=True):
        if path.stem >= str(review["session_date"])[:10]:
            continue
        try:
            older.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if len(older) >= days - 1:
            break
    return [review] + older


def render_section(review, target=None):
    """장 회고 탭의 내용. 본체는 forecast_utils.review_tab_html(최근 거래일 날짜 단추 포함)."""
    return review_tab_html(recent_reviews(target, review) if target else [review])


def insert_section(page, section):
    """회고 절을 넣는다. 이미 있으면 교체, 없으면 원장 절 뒤, 그것도 없으면 body 끝."""
    return insert_review_section(page, section)


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

    # 미국 '밤사이' 창: 직전 한국 거래일 마감 뒤 ~ 오늘 개장 전. 연휴 뒤 첫날은 하루가 아니라 휴장 기간 전체다
    # (2026-09-28: 추석 뒤 첫날인데 전날 밤 하루치만 봐서 '미국은 올랐는데'를 놓쳤다).
    prev_kr = daily.index[pos - 1] if pos >= 1 else session_date - pd.Timedelta(days=1)

    def overnight_of(ticker):
        frame = load_daily(ticker)
        window = frame[(frame.index >= prev_kr) & (frame.index < session_date)]
        before = frame[frame.index < prev_kr]
        if window.empty or before.empty:
            frame = frame[frame.index < session_date]
            return float(frame["close"].iloc[-1] / frame["close"].iloc[-2] - 1) if len(frame) >= 2 else float("nan")
        summary["us_nights"] = int(len(window))
        return float(window["close"].iloc[-1] / before["close"].iloc[-1] - 1)

    def range_of(ticker):
        """그날 저점→고점 폭. 동종 종목과 견주면 어느 쪽이 더 탄력적으로 움직였는지 보인다(2026-09-30)."""
        frame = load_daily(ticker)
        if frame.empty or session_date not in frame.index:
            return float("nan")
        row = frame.loc[session_date]
        return float(row["high"] / row["low"] - 1) if float(row["low"]) > 0 else float("nan")

    summary["kospi_c2c"] = c2c_of("^KS11")
    summary["peer_c2c"] = c2c_of(spec["peer"])
    summary["usdkrw_chg"] = c2c_of("KRW=X")
    summary["sox_ret"], summary["nasdaq_ret"] = overnight_of("^SOX"), overnight_of("^IXIC")
    summary["micron_ret"] = overnight_of("MU")
    # 직전 연속 상승·하락 일수(오늘 제외). 연속 상승 뒤 하락은 차익 실현과 맞는 모양이다(2026-09-30).
    streak, sign = 0, 0
    for k in range(pos - 1, 0, -1):
        move = float(daily["close"].iloc[k] / daily["close"].iloc[k - 1] - 1)
        s_ = 1 if move > 0 else -1 if move < 0 else 0
        if streak == 0:
            sign = s_
        if s_ == 0 or s_ != sign:
            break
        streak += 1
    summary["prior_streak"] = int(streak * sign)
    # 이동평균선·고점 대비(2026-09-30 회고 영상: '5일선 돌파 못함', '20일선 지지', '전고점 근처').
    long = load_daily(spec["ticker"], days=420)
    if not long.empty and session_date in long.index:
        upto = long[long.index <= session_date]["close"]
        highs = long[long.index <= session_date]["high"]
        if len(upto) >= 20:
            summary["ma5"], summary["ma20"] = float(upto.tail(5).mean()), float(upto.tail(20).mean())
            summary["high20"] = float(highs.tail(20).max())
        if len(upto) >= 120:
            summary["high252"] = float(highs.tail(252).max())          # 전날 밤 마이크론 — 두 종목과 서로 영향을 주고받는 미국 메모리 회사
    summary["price_context"] = price_context(long, load_daily(spec["peer"], days=420), session_date)
    summary["range"] = float(today["high"]) / float(today["low"]) - 1 if float(today["low"]) > 0 else float("nan")
    summary["peer_range"] = range_of(spec["peer"])
    # 직전 5거래일 등락(수급 자료가 없는 날에도 '그날 함께 관찰된 것'에 쓴다, 2026-10-01).
    summary["prior_5d"] = (float(daily["close"].iloc[pos - 1] / daily["close"].iloc[pos - 6] - 1) if pos >= 6 else None)
    # 장중 고가·저가가 이동평균선에 닿았는가('20일선 지지 후 반등', '5일선 넘으려다 밀림').
    if not long.empty and session_date in long.index:
        summary["ma_touches"] = ma_touches(long, long.index.get_loc(session_date))
    # 전날 밤 미국 메모리·AI 대표주의 시간외 반응(실적 발표는 장 마감 뒤라 정규장 등락에 안 보인다).
    summary["micron_ah"] = after_hours("MU", session_date)
    summary["nvidia_ah"] = after_hours("NVDA", session_date)

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
    # 한국 장중의 해외 선물·환율(고점 이후 구간 포함) — '오후에 미국 금리가 올라 상승을 막았다' 같은 설명을 자료로 본다.
    summary["session_cross"] = session_cross(session_date, tp["high_time"] if tp else None)
    if tp and summary["close"] > 0:
        high_price = summary["open"] * (1 + float(tp["high"]))
        summary["from_high"] = summary["close"] / high_price - 1 if high_price > 0 else None

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
    buyback_periods = buyback_on(spec["corp_code"], session_date, include_ended=True)
    summary["buyback"] = [r for r in buyback_periods
                          if pd.Timestamp(r["start"]) <= session_date <= pd.Timestamp(r["end"])]
    # 실적 발표가 열흘 안에 있거나 오늘이 발표일이면, 과거 발표일의 반응 통계를 붙인다.
    upcoming = [ev for ev in _kr_events(session_date, target, days=10)
                if "실적" in ev.get("label", "") and spec["name"] in ev.get("label", "")]
    today_is_earnings = any(PROVISIONAL_RE.search(d.get("report_nm", "")) for d in (disclosures or []))
    earnings_stats = (earnings_reactions(target, spec["corp_code"], Path(storage))
                      if upcoming or today_is_earnings else None)

    # 수급 — 누가 팔고 샀나(2026-09-29 요청). 실패해도 회고는 낸다.
    # 예전에는 이 작업의 pip 설치에 lxml 이 빠져 네이버 표를 읽지 못해 수급이 매일 비어 있었다(9/17~9/28 회고 모두 None).
    # 저장소 보관본(macro_history)을 함께 넘겨 최근 20거래일 규모와 견줄 이력을 얻는다.
    flows_text, flow_story_data = None, None
    # 수급 상태(2026-10-01): ok / pending(받기는 했지만 그날 행이 아직 없음) / failed(KRX·네이버 모두 실패).
    # 예전에는 실패해도 로그 한 줄뿐이라 회고에서 '늦은 것'과 '못 받은 것'을 구별할 수 없었다.
    flow_status = {"state": "failed", "detail": "수급 자료를 읽지 못했습니다"}
    try:
        from data_sources.flows import load_investor_flows
        frame, flow_info = load_investor_flows(Path(storage), spec["ticker"], (session_date - pd.Timedelta(days=100)).date(),
                                               session_date.date(), fallback_dir=Path("macro_history"))
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        row = frame[frame["date"] == session_date]
        failed_sources = [part.split(":")[0].strip() for part in str((flow_info or {}).get("fetch_error") or "").split(" / ")
                          if part.strip()]
        if failed_sources:
            flow_status = {"state": "failed",
                           "detail": f"{'·'.join(failed_sources)}에서 받지 못해 저장소 보관본"
                                     f"(최신 {frame['date'].max().date() if len(frame) else '없음'})을 썼습니다"}
            print(f"::warning::수급 받기 실패({'·'.join(failed_sources)}) — 보관본 사용")
        krx_error = (flow_info or {}).get("krx_error")
        if krx_error and krx_error != "KRX 계정 없음":
            print(f"::notice::수급을 KRX에서 받지 못해 다른 경로를 썼습니다 — {krx_error}")
        if len(row):
            flow_status = {"state": "ok", "detail": str((flow_info or {}).get("source") or ""), "krx_error": krx_error}
            row = row.iloc[0]
            history = frame[frame["date"] < session_date]
            prior_5d = (float(daily["close"].iloc[pos - 1] / daily["close"].iloc[pos - 6] - 1) if pos >= 6 else None)
            source = str((flow_info or {}).get("source") or "")
            note = (f"출처 {source} · 장 마감 직후 잠정치라 저녁 확정치와 다를 수 있습니다"
                    if source else "장 마감 직후 잠정치라 저녁 확정치와 다를 수 있습니다")
            if krx_error and "KRX" not in source:
                note += f" · KRX는 받지 못함({krx_error[:60]})"
            summary["buyback_transition"] = buyback_transition(
                buyback_periods, frame, long.index, session_date)
            flow_story_data = flow_story(row.to_dict(), history, summary, summary["close"], prior_5d,
                                         peer_name=spec.get("peer_name", "동종 종목"), source_note=note)
            frg, inst = row.get("foreign_net"), row.get("inst_net")
            if pd.notna(frg) and pd.notna(inst):
                flows_text = f"외국인 {frg:+,.0f} / 기관 {inst:+,.0f}"
        else:
            if not failed_sources:
                flow_status = {"state": "pending", "detail": f"최신 {frame['date'].max().date() if len(frame) else '없음'}"}
            print(f"  수급: {session_date.date()} 행이 아직 없습니다(최신 {frame['date'].max().date() if len(frame) else '없음'}).")
    except Exception as exc:
        flow_status = {"state": "failed", "detail": "KRX·네이버·보관본 모두에서 받지 못했습니다"}
        print(f"::warning::수급 미확인({type(exc).__name__})")

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

    intraday_path, timeline_items = timeline_data(bars, events, session_date)
    return {
        "target": target, "name": spec["name"], "peer_name": spec["peer_name"],
        "session_date": session_date.date().isoformat(),
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "summary": summary, "classification": classification, "events": events, "turning_point": tp,
        "closing_share": cshare, "intraday_corr_kospi": corr, "intraday_note": intraday_note,
        "intraday_coverage": coverage,
        "intraday_path": intraday_path, "timeline_items": timeline_items,
        "overnight_news": overnight_news, "top_news": top_news,
        "disclosures": disclosures, "disclosure_note": disclosure_note, "flows": flows_text,
        "flow_story": flow_story_data, "flow_status": flow_status,
        "context": session_context(spec["ticker"], session_date),
        "market": fetch_market_overview(session_date) if use_news else None,
        "earnings_reactions": earnings_stats,
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
    section = render_section(review, args.target)
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
    # 회고 기록과 보고서들을 한 커밋으로 올린다(발행 묶기 ②, 2026-09-24). 도중에 실패하면 아무것도 올리지 않는다.
    with github_pages.batch(f"review: {args.target} {review['session_date']} ({now:%H:%M} KST)", token):
        sha = github_pages.publish(f"forecast_history/{args.target}/reviews/{review['session_date']}.json", to_json(review), token,
                                   f"review: {args.target} {review['session_date']} ({now:%H:%M} KST)")
        print(f"회고 기록 저장 forecast_history/{args.target}/reviews/{review['session_date']}.json @ {sha}")
        # 실적 발표일 보관본(후속 B): 이번 실행이 만들었을 때만 날짜로 합쳐 올린다. 바뀐 것이 없으면 올리지 않는다.
        dates_cache = storage / earnings_dates_name(args.target)
        if dates_cache.exists():
            result = github_pages.publish_history(f"macro_history/{earnings_dates_name(args.target)}",
                                                  dates_cache.read_text(encoding="utf-8"), token,
                                                  f"macro: 실적 발표일 보관본 {args.target}")
            print(f"실적 발표일 보관본 macro_history/{earnings_dates_name(args.target)} → {result}")
        spec_corp = TARGETS[args.target]["corp_code"]
        pages = [f"docs/{args.target}/index.html"]
        pages += [f"docs/{args.target}/reports/{candidate.date()}.html"
                  for candidate in (session_date, session_date + pd.Timedelta(days=1))]
        for path in pages:
            page, page_sha = github_pages.fetch_with_sha(path, token)
            if page is None:
                if path == pages[0]:
                    print(f"⚠️ {path} 없음 — 건너뜁니다.")
                continue
            # 보고서는 노트북·오후 갱신도 다시 쓴다. 읽은 뒤 바뀌었으면 최신본에 회고 절만 다시 넣는다(덮어쓰지 않는다).
            # 회고를 넣으면서 '공시·발표 일정' 탭의 최근 공시 목록도 지금 기준으로 다시 채운다(2026-10-02).
            def finish(html_text):
                return refresh_recent_disclosures(html_text, spec_corp, now)
            ours = insert_section(page, section)
            sha = github_pages.publish(path, finish(ours), token,
                                       f"review: {path} 장 마감 회고 ({now:%Y-%m-%d %H:%M} KST)",
                                       expected_sha=page_sha,
                                       merge=lambda latest, ours=ours:
                                           finish(insert_section(latest, section) if latest else ours))
            print(f"보고서 갱신 {path} @ {sha}")


if __name__ == "__main__":
    main()
