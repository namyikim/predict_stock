# -*- coding: utf-8 -*-
"""장기 전망 탭의 전망을 기록하고, 나중에 발표된 값으로 채점한다(2026-09-27).

일일 방향·금속·영업이익은 원장이 있지만 장기 전망 탭의 나머지 숫자(경기선행지수·G20·수출 증가율 전망, 장기
주가 모델, 가격 도달 확률, 반도체 수출 한 달 치 속보 환산, 국면 잔여 기간)는 매번 새 값으로 덮여 '지난번에
뭐라고 했고 실제는 어땠나'를 볼 수 없었다. 화면의 성적은 모두 과거로 되돌려 잰 것이고, 선행지수는 나중에
값이 바뀌어 그 성적이 부풀려져 있다.

규칙(영업이익 원장과 같다):
- 전망은 **처음 낸 값만** 남긴다. 키는 (계열, 정보 기준 시점, 대상 시점[, 세부]) — 매일 도는 실행이 같은 달 전망을
  다시 내도 첫 값이 남는다. 예측값은 절대 고쳐 쓰지 않는다.
- 실제 값은 **처음 확인한 값**으로 채점하고 다시 바꾸지 않는다. 매일 확인하므로 사실상 첫 발표치다 — 나중에
  수정된 값으로 채점하면 그때 알 수 없던 개정을 미리 아는 셈이 된다.
- 비교 기준을 함께 둔다: 값 전망은 '마지막 값 그대로'(주가는 '변화 없음'), 확률 전망은 Brier 점수.

원장: forecast_history/<종목>/outlook_log.csv (종목마다 따로 — 두 종목 실행이 같은 파일을 두고 다투지 않는다).
"""
import html
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

KST = timezone(timedelta(hours=9))
LEDGER_NAME = "outlook_log.csv"

COLUMNS = [
    "record_id", "series", "label", "unit", "kind", "issued_at_kst", "info_as_of", "target_period", "horizon",
    "detail", "point", "low", "high", "baseline", "probability", "baseline_probability", "model", "status",
    "actual", "actual_seen_kst", "error", "abs_error", "baseline_abs_error", "in_band", "outcome", "brier",
    "baseline_brier", "note",
]
NUMERIC = ["point", "low", "high", "baseline", "probability", "baseline_probability", "actual", "error",
           "abs_error", "baseline_abs_error", "in_band", "outcome", "brier", "baseline_brier"]

# 계열 이름표(표시 순서). unit: index(지수 포인트), ratio(비율, %로 표시), log_return(로그수익률, %로 표시),
# usd(달러, 억 달러로 표시), months(개월).
SERIES = {
    "cli_kor": ("OECD 한국 경기선행지수", "index", "마지막 값 그대로"),
    "cli_g20": ("OECD G20 경기선행지수", "index", "마지막 값 그대로"),
    "korea_exports_yoy": ("한국 수출 증가율(전년 동월 대비)", "ratio", "마지막 증가율 그대로"),
    "semi_exports_month": ("반도체 수출 한 달 치(관세청 속보 환산)", "usd", "작년 같은 달 그대로"),
    "stock_return": ("장기 주가 모델(월말 기준 수익률)", "log_return", "변화 없음(0%)"),
    "phase_return_12m": ("같은 국면의 1년 뒤 주가 중앙값", "log_return", "변화 없음(0%)"),
    "phase_up_12m": ("같은 국면에서 1년 뒤 오를 확률", "probability", "반반(50%)"),
    "level_reach": ("가격 도달 확률", "probability", "—"),
    "phase_end": ("수출 국면이 끝나는 달", "months", "—"),
}


def now_kst():
    return datetime.now(KST)


def read_ledger(path):
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    return read_ledger_text(path.read_text(encoding="utf-8"))


def to_csv(frame):
    return frame[COLUMNS].to_csv(index=False, lineterminator="\n")


def merge_ledgers(latest_text, ours):
    """원격 최신 원장 + 이 실행에만 있는 record_id. 같은 record_id 는 채점이 앞선 쪽(원격이 채점했으면 원격)을 남긴다."""
    remote = read_ledger_text(latest_text)
    if remote.empty:
        return ours
    keep = remote.set_index("record_id")
    mine = ours.set_index("record_id")
    for rid in mine.index:
        if rid not in keep.index:
            keep.loc[rid] = mine.loc[rid]
        elif keep.loc[rid, "status"] != "scored" and mine.loc[rid, "status"] == "scored":
            keep.loc[rid] = mine.loc[rid]
    return keep.reset_index()[COLUMNS]


def read_ledger_text(text):
    import io
    if not text:
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame[COLUMNS].copy()
    for column in NUMERIC:
        frame[column] = pd.to_numeric(frame[column].replace("", np.nan), errors="coerce")
    return frame


# ---------------------------------------------------------------------------
# 1) 이번 실행이 화면에 낸 전망을 기록 후보로 모은다
# ---------------------------------------------------------------------------
def _f(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _month(value):
    return str(value)[:7] if value else ""


def _add_months(month, n):
    return (pd.Period(month, freq="M") + int(n)).strftime("%Y-%m")


def collect_forecasts(lt, er=None, level_odds=None, price_date=None):
    """longterm.json·earnings.json 과 가격 도달 확률에서 이번에 화면에 나온 전망을 행으로 만든다.

    화면에 숫자로 나온 것만 담는다(검증 문을 통과하지 못해 '말하지 않는다'고 적힌 것은 전망이 아니다).
    """
    lt, er = lt or {}, er or {}
    rows = []

    def add(series, info_as_of, target_period, horizon, detail="", **values):
        label, unit, _ = SERIES[series]
        rid = f"{series}|{info_as_of}|{target_period}" + (f"|{detail}" if detail else "")
        rows.append({"record_id": rid, "series": series, "label": label, "unit": unit,
                     "kind": "probability" if unit == "probability" else "value",
                     "info_as_of": info_as_of, "target_period": target_period, "horizon": str(horizon),
                     "detail": detail, **values})

    cli = lt.get("cli_outlook") or {}
    for row in cli.get("rows") or []:
        if _f(row.get("point")) is None:
            continue
        add("cli_kor", cli.get("last_month", ""), _month(row.get("month")), f"{row.get('horizon')}개월",
            point=_f(row.get("point")), low=_f(row.get("low")), high=_f(row.get("high")),
            baseline=_f(cli.get("last_value")), model=str(row.get("model", "")))

    g20 = lt.get("g20_outlook") or {}
    for row in g20.get("rows") or []:
        if _f(row.get("point")) is None:
            continue
        add("cli_g20", g20.get("g20_last_month", ""), _month(row.get("month")), f"{row.get('horizon')}개월",
            point=_f(row.get("point")), low=_f(row.get("low")), high=_f(row.get("high")),
            baseline=_f(g20.get("g20_last")), model=str(row.get("model", "")))
    for row in g20.get("growth_rows") or []:
        if _f(row.get("point")) is None:
            continue
        add("korea_exports_yoy", g20.get("growth_last_month", ""), _month(row.get("month")),
            f"{row.get('horizon')}개월", point=_f(row.get("point")), low=_f(row.get("low")),
            high=_f(row.get("high")), baseline=_f(g20.get("growth_last")), model=str(row.get("model", "")))

    # 장기 주가 모델: 검증을 통과해 화면에 수익률로 나온 지평만(요약의 규칙과 같다).
    evaluation, forecast = lt.get("evaluation") or {}, lt.get("forecast") or {}
    as_of = _month(lt.get("as_of"))
    for months in ("3", "6", "12"):
        point = _f((forecast.get(months) or {}).get("point"))
        if (evaluation.get(months) or {}).get("beats_zero") and point is not None and as_of:
            add("stock_return", as_of, _add_months(as_of, int(months)), f"{months}개월",
                point=point, baseline=0.0, model="ridge(축소)")

    # 같은 국면의 1년 뒤 — 요약이 보여 줄 때만(속보 국면이 있으면 월간 국면 통계를 붙이지 않는다).
    live = lt.get("live_exports") or {}
    duration = {} if live else (lt.get("duration") or {})
    phase = duration.get("phase") or (lt.get("current") or {}).get("phase")
    if not live and phase and as_of:
        row = next((r for r in ((lt.get("phases") or {}).get("12") or [])
                    if isinstance(r, dict) and r.get("phase") == phase and _f(r.get("median")) is not None), None)
        if row:
            target = _add_months(as_of, 12)
            add("phase_return_12m", as_of, target, "12개월", point=_f(row["median"]), low=_f(row.get("q25")),
                high=_f(row.get("q75")), baseline=0.0, model=f"과거 같은 국면 {int(row.get('n') or 0)}개월")
            if _f(row.get("positive_share")) is not None:
                add("phase_up_12m", as_of, target, "12개월", probability=_f(row["positive_share"]),
                    baseline_probability=0.5, model=f"과거 같은 국면 {int(row.get('n') or 0)}개월")
    remaining = _f(duration.get("remaining_median"))
    if remaining is not None and duration.get("since") and duration.get("as_of"):
        base = duration["as_of"]
        add("phase_end", base, duration["since"], "잔여", detail=str(phase or ""),
            point=remaining, low=_f(duration.get("remaining_q25")), high=_f(duration.get("remaining_q75")),
            model=f"과거 같은 국면 {int(_f(duration.get('n_conditional')) or 0)}번")

    # 반도체 수출 한 달 치: 1~10일·1~20일 속보를 한 달 치로 환산한 값(영업이익 추정에 들어간다).
    for flash in er.get("flash_applied") or []:
        if not isinstance(flash, dict) or _f(flash.get("monthly_usd")) is None:
            continue
        month, days = _month(flash.get("month")), int(flash.get("days") or 0)
        yoy = _f(flash.get("yoy"))
        # 비교 기준 '작년 같은 달 그대로' = 환산값 / (1 + 속보 전년 대비 증가율)
        last_year = _f(flash["monthly_usd"]) / (1 + yoy) if yoy is not None and yoy > -1 else None
        add("semi_exports_month", f"{month} 1~{days}일", month, f"1~{days}일 속보", detail=f"d{days}",
            point=_f(flash["monthly_usd"]), baseline=last_year, model=str(flash.get("basis", "")))

    # 가격 도달 확률(추세 없음): 레벨마다 6개월·1년·2년 안에 종가가 한 번이라도 닿을 확률. 매일 다시 계산되므로
    # 달마다 처음 낸 값만 남긴다(키의 기준 시점 = 발행 달).
    if level_odds and price_date is not None:
        issued = pd.Timestamp(price_date).strftime("%Y-%m-%d")
        for level in level_odds.get("levels") or []:
            if _f(level.get("change")) is None or level["change"] <= 0:
                continue
            curve = np.asarray(level.get("curve"), dtype=float)
            for label, days in (("6개월", 126), ("1년", 252), ("2년", 504)):
                if curve.size < days:
                    continue
                add("level_reach", issued[:7], f"{int(level['level'])}", label,
                    detail=f"{int(level['level'])}@{days}d", probability=float(curve[days - 1]),
                    model=f"기준일 {issued} 종가 {float(level_odds.get('current') or 0):,.0f}",
                    note=f"issued={issued};days={days}")
    return rows


def record(ledger, rows, stamp=None):
    """처음 낸 전망만 더한다. 이미 있는 record_id 는 그대로 둔다(고쳐 쓰지 않는다)."""
    stamp = stamp or now_kst().strftime("%Y-%m-%d %H:%M")
    have = set(ledger["record_id"].astype(str)) if len(ledger) else set()
    new = [dict(row, issued_at_kst=stamp, status="pending") for row in rows if row["record_id"] not in have]
    if not new:
        return ledger, 0
    frame = pd.DataFrame(new)
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan if column in NUMERIC else ""
    out = frame[COLUMNS] if ledger.empty else pd.concat([ledger, frame[COLUMNS]], ignore_index=True)
    return out[COLUMNS], len(new)


# ---------------------------------------------------------------------------
# 2) 실제 값: 이번 실행이 받은 자료에서 대상 시점의 값을 찾는다
# ---------------------------------------------------------------------------
def monthly_series(path):
    """month,value CSV → 'YYYY-MM' 인덱스 Series. 없으면 None."""
    path = Path(path)
    if not path.exists():
        return None
    frame = pd.read_csv(path)
    if "month" not in frame or "value" not in frame:
        return None
    index = pd.to_datetime(frame["month"], errors="coerce").dt.strftime("%Y-%m")
    return pd.Series(pd.to_numeric(frame["value"], errors="coerce").to_numpy(), index=index).dropna()


def first_existing(*paths):
    for path in paths:
        if path is not None and Path(path).exists():
            return Path(path)
    return None


def exports_actuals(snapshot):
    """수출 스냅숏(series + applied) → 속보 환산이 아닌 '한 달 치 실제'만. applied 달은 아직 실제가 아니다."""
    if not snapshot:
        return None
    applied = {_month(item.get("month")) for item in snapshot.get("applied") or [] if isinstance(item, dict)}
    values = {_month(item["month"]): _f(item.get("value")) for item in snapshot.get("series") or []
              if isinstance(item, dict) and item.get("month")}
    return pd.Series({m: v for m, v in values.items() if m not in applied and v is not None}, dtype=float)


def month_end_close(prices, month):
    """그 달의 마지막 종가. 그 달이 아직 끝나지 않았으면(다음 달 종가가 없으면) None."""
    end = pd.Period(month, freq="M").end_time.normalize()
    if prices is None or len(prices) == 0 or prices.index.max() <= end:
        return None
    before = prices[prices.index <= end]
    return float(before.iloc[-1]) if len(before) else None


def actuals_from(lt_dir, er_dir=None, lt=None, prices=None):
    """계열 → 실제 값 조회 함수. 자료가 없으면 그 계열은 이번에 채점하지 않는다."""
    lt_dir, lt = Path(lt_dir), lt or {}
    out = {}
    cache, fallback = lt_dir / "macro_cache", lt_dir / "macro_fallback"
    for series, name in (("cli_kor", "cli_kor.csv"), ("cli_g20", "cli_g20.csv")):
        path = first_existing(cache / name, fallback / name)
        values = monthly_series(path) if path else None
        if values is not None and len(values):
            out[series] = lambda row, v=values: v.get(row["target_period"])
    path = first_existing(cache / "korea_exports.csv", fallback / "korea_exports.csv")
    exports = monthly_series(path) if path else None
    if exports is not None and len(exports):
        index = pd.PeriodIndex(exports.index, freq="M")
        level = pd.Series(exports.to_numpy(), index=index)
        yoy = (level / level.shift(12, freq="M").reindex(index) - 1).dropna()
        yoy.index = yoy.index.strftime("%Y-%m")
        out["korea_exports_yoy"] = lambda row, v=yoy: v.get(row["target_period"])
    if er_dir is not None:
        import json
        snap_path = Path(er_dir) / "exports_snapshot.json"
        if snap_path.exists():
            actual = exports_actuals(json.loads(snap_path.read_text(encoding="utf-8")))
            if actual is not None and len(actual):
                out["semi_exports_month"] = lambda row, v=actual: v.get(row["target_period"])
    if prices is not None and len(prices):
        prices = prices.sort_index()

        def stock(row, p=prices):
            start, end = month_end_close(p, row["info_as_of"]), month_end_close(p, row["target_period"])
            return None if start is None or end is None else math.log(end / start)
        out["stock_return"] = out["phase_return_12m"] = stock
        out["phase_up_12m"] = lambda row: (None if stock(row) is None else float(stock(row) > 0))
        out["level_reach"] = lambda row, p=prices: level_reach_outcome(p, row)
    episodes = ((lt.get("duration") or {}).get("episodes") or [])
    if episodes:
        def phase_end(row, eps=episodes):
            match = next((e for e in eps if isinstance(e, dict) and str(e.get("start")) == row["target_period"]), None)
            if not match:
                return None
            # 기록 당시 기준 달부터 국면이 끝난 달까지 남은 개월 수
            return float((pd.Period(match["end"], freq="M") - pd.Period(row["info_as_of"], freq="M")).n)
        out["phase_end"] = phase_end
    return out


def level_reach_outcome(prices, row):
    """기준일 뒤 N거래일 안에 종가가 레벨에 닿았으면 1, N거래일이 지나도록 못 닿았으면 0, 아직 모르면 None."""
    note = dict(part.split("=", 1) for part in str(row.get("note") or "").split(";") if "=" in part)
    if "issued" not in note or "days" not in note:
        return None
    issued, days, level = pd.Timestamp(note["issued"]), int(note["days"]), float(row["target_period"])
    after = prices[prices.index > issued].iloc[:days]
    if (after >= level).any():
        return 1.0
    return 0.0 if len(after) >= days else None


# ---------------------------------------------------------------------------
# 3) 채점
# ---------------------------------------------------------------------------
def score(ledger, actuals, stamp=None):
    """아직 채점되지 않은 행 중 실제 값이 나온 것을 채점한다. 이미 채점된 행은 다시 건드리지 않는다."""
    stamp = stamp or now_kst().strftime("%Y-%m-%d %H:%M")
    scored = 0
    for i, row in ledger.iterrows():
        if row["status"] == "scored" or row["series"] not in actuals:
            continue
        try:
            actual = actuals[row["series"]](row)
        except Exception:
            actual = None
        actual = _f(actual)
        if actual is None:
            continue
        ledger.loc[i, ["status", "actual", "actual_seen_kst"]] = ["scored", actual, stamp]
        if row["kind"] == "probability":
            p = _f(row["probability"])
            ledger.loc[i, "outcome"] = actual
            if p is not None:
                ledger.loc[i, "brier"] = (p - actual) ** 2
            q = _f(row["baseline_probability"])
            if q is not None:
                ledger.loc[i, "baseline_brier"] = (q - actual) ** 2
        else:
            point = _f(row["point"])
            if point is not None:
                ledger.loc[i, "error"] = point - actual
                ledger.loc[i, "abs_error"] = abs(point - actual)
            base = _f(row["baseline"])
            if base is not None:
                ledger.loc[i, "baseline_abs_error"] = abs(base - actual)
            low, high = _f(row["low"]), _f(row["high"])
            if low is not None and high is not None:
                ledger.loc[i, "in_band"] = float(low <= actual <= high)
        scored += 1
    return ledger, scored


# ---------------------------------------------------------------------------
# 4) 화면: 지난 전망은 맞았나
# ---------------------------------------------------------------------------
TH = 'style="text-align:left;padding:6px 8px;border-bottom:1px solid #ddd;background:#f7f8fa;font-weight:600"'
THR = 'style="text-align:right;padding:6px 8px;border-bottom:1px solid #ddd;background:#f7f8fa;font-weight:600"'
TD = 'style="padding:6px 8px;border-bottom:1px solid #eee"'
TDR = 'style="text-align:right;padding:6px 8px;border-bottom:1px solid #eee"'


def fmt(value, unit):
    value = _f(value)
    if value is None:
        return "—"
    if unit == "index":
        return f"{value:.2f}"
    if unit == "ratio":
        return f"{value * 100:+.1f}%"
    if unit == "log_return":
        return f"{math.expm1(value) * 100:+.1f}%"
    if unit == "usd":
        return f"{value / 1e8:,.0f}억 달러"
    if unit == "months":
        return f"{value:.0f}개월"
    if unit == "probability":
        return f"{value:.0%}"
    return f"{value:g}"


def err_fmt(value, unit):
    value = _f(value)
    if value is None:
        return "—"
    if unit == "index":
        return f"{value:.2f}p"
    if unit in ("ratio", "log_return"):
        return f"{value * 100:.1f}%p"
    if unit == "usd":
        return f"{value / 1e8:,.0f}억 달러"
    if unit == "months":
        return f"{value:.0f}개월"
    return f"{value:.3f}"


def next_due(rows):
    """아직 채점 전인 전망 중 가장 빨리 결과가 나올 대상 시점."""
    pending = [str(t) for t in rows.loc[rows["status"] != "scored", "target_period"] if str(t)[:4].isdigit()]
    months = sorted(t for t in pending if len(t) == 7)
    return months[0] if months else None


def render(ledger):
    """계열별 요약(채점 수·평균 오차 vs 기준·구간 적중·Brier)과 최근 채점 표."""
    e = html.escape
    parts = ['<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
             '3. 지난 전망은 맞았나 <span style="font-weight:400;color:#8a9199;font-size:12px">'
             '실제로 미리 낸 전망만 · 발표된 값으로 채점</span></h3>']
    if ledger is None or ledger.empty:
        parts.append('<div style="font-size:13px;color:#6b7178">아직 기록된 전망이 없습니다.</div>')
        return "".join(parts)
    body = ""
    for series, (label, unit, base_label) in SERIES.items():
        rows = ledger[ledger["series"] == series]
        if rows.empty:
            continue
        done = rows[rows["status"] == "scored"]
        due = next_due(rows)
        if done.empty:
            score_text = "아직 채점 전" + (f" — 첫 결과 {e(due)} 발표 뒤" if due else "")
            base_text = band_text = "—"
        elif unit == "probability":
            brier = done["brier"].mean()
            hits = done["outcome"].mean()
            mean_p = done["probability"].mean()
            score_text = f"Brier {brier:.3f} · 예상 {mean_p:.0%} / 실제 {hits:.0%}"
            base_brier = done["baseline_brier"].dropna()
            base_text = f"{base_label} Brier {base_brier.mean():.3f}" if len(base_brier) else "—"
            band_text = "—"
        else:
            score_text = f"평균 오차 {err_fmt(done['abs_error'].mean(), unit)}"
            base = done["baseline_abs_error"].dropna()
            if len(base):
                wins = (done.loc[base.index, "abs_error"] < base).mean()
                base_text = f"{base_label} {err_fmt(base.mean(), unit)} · 더 가까운 비율 {wins:.0%}"
            else:
                base_text = "—"
            band = done["in_band"].dropna()
            band_text = f"{band.mean():.0%}({len(band)}건)" if len(band) else "—"
        body += (f'<tr><td {TD}>{e(label)}</td><td {TDR}>{len(rows)}</td><td {TDR}>{len(done)}</td>'
                 f'<td {TD}>{score_text}</td><td {TD}>{base_text}</td><td {TDR}>{band_text}</td></tr>')
    parts.append('<div style="overflow-x:auto"><table style="width:100%;min-width:640px;border-collapse:collapse;'
                 'font-size:13px;border:1px solid #e5e5e5">'
                 f'<tr><th {TH}>전망</th><th {THR}>기록</th><th {THR}>채점</th><th {TH}>성적</th>'
                 f'<th {TH}>비교 기준</th><th {THR}>구간 안</th></tr>{body}</table></div>')

    recent = ledger[ledger["status"] == "scored"].sort_values("actual_seen_kst", ascending=False).head(12)
    if len(recent):
        rows_html = ""
        for _, r in recent.iterrows():
            unit = r["unit"]
            if r["kind"] == "probability":
                said, got = fmt(r["probability"], "probability"), ("일어남" if r["outcome"] == 1 else "안 일어남")
                miss = f"Brier {r['brier']:.3f}" if _f(r["brier"]) is not None else "—"
            else:
                said = fmt(r["point"], unit) + (f" ({fmt(r['low'], unit)}~{fmt(r['high'], unit)})"
                                                if _f(r["low"]) is not None and _f(r["high"]) is not None else "")
                got, miss = fmt(r["actual"], unit), err_fmt(r["error"], unit)
                if unit == "months":
                    got = f"{_f(r['actual']):.0f}개월 뒤 끝남"
            target = r["target_period"] if r["series"] != "level_reach" else f"{int(float(r['target_period'])):,}원 · {r['horizon']}"
            rows_html += (f'<tr><td {TD}>{e(r["label"])}</td><td {TD}>{e(str(target))}</td>'
                          f'<td {TD}>{e(str(r["info_as_of"]))} 기준 · {e(str(r["horizon"]))}</td>'
                          f'<td {TDR}>{e(said)}</td><td {TDR}>{e(got)}</td><td {TDR}>{e(miss)}</td></tr>')
        parts.append('<h4 style="font-size:14px;margin:16px 0 6px">최근 채점</h4>'
                     '<div style="overflow-x:auto"><table style="width:100%;min-width:640px;border-collapse:collapse;'
                     'font-size:13px;border:1px solid #e5e5e5">'
                     f'<tr><th {TH}>전망</th><th {TH}>대상</th><th {TH}>언제 낸 전망</th><th {THR}>전망</th>'
                     f'<th {THR}>실제</th><th {THR}>오차</th></tr>{rows_html}</table></div>')
    parts.append('<div style="font-size:11px;color:#8a9199;margin-top:6px;line-height:1.5">'
                 '장기 전망 탭에 숫자로 나온 전망을 <b>처음 낸 값 그대로</b> 남기고, 대상 시점의 값이 발표되면 '
                 '<b>처음 확인한 값</b>으로 채점합니다(선행지수처럼 나중에 수정되는 값도 발표 당시 값으로). '
                 '같은 달 전망을 다시 내도 첫 값만 셉니다. 값 전망은 “마지막 값 그대로”, 주가는 “변화 없음”과 '
                 '평균 오차를 견주고, 확률 전망은 Brier 점수(0에 가까울수록 좋음)로 봅니다. 영업이익 추정의 성적은 '
                 '위 2절 “지난 분기 추정 vs 실제”에 따로 있습니다. 기록은 2026-09-27부터입니다.</div>')
    return "".join(parts)
