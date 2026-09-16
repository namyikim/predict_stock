# -*- coding: utf-8 -*-
"""거시 경제 보고서 — 환율·금리·채권 등 거시 지표를 모아 보는 페이지.

2026-09-15 시작. 지금은 뼈대만 있고 지표는 하나씩 붙인다. 뼈대를 먼저 두는 이유는, 지표를
추가할 때마다 페이지 구조를 다시 정하지 않고 SECTIONS 에 함수 하나만 더하면 되게 하기 위해서다.

이 저장소의 규칙을 그대로 따른다.
- 받은 자료를 그대로 보여 준다. 예측하거나 매수·매도 의견을 내지 않는다.
- 조회에 실패한 지표는 그 사실과 사유를 적고, 나머지는 정상 표시한다.
- 값이 없으면 '—'로 두고 추측해 채우지 않는다.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from html import escape
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KST = timezone(timedelta(hours=9))
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "namyikim/predict_stock")
TITLE = "거시 경제"
OUT = ROOT / "docs" / "macro" / "index.html"

_STYLE = """<style>
html,body{overflow-x:hidden;overflow-x:clip}
body{margin:0;padding:24px 20px 48px;background:#fff;font-family:-apple-system,'Malgun Gothic',sans-serif;
     line-height:1.65;color:#1a1a1a;-webkit-font-smoothing:antialiased}
.wrap{max-width:980px;margin:0 auto}
a{color:#1a5490}
table{width:100%;border-collapse:collapse;font-size:13px;border:1px solid #e5e5e5}
th{background:#fafafa;font-size:11px;color:#6b7178;padding:8px 10px;text-align:left}
td{padding:7px 10px;border-top:1px solid #eee}
.num{text-align:right;white-space:nowrap}
.muted{color:#8a9199;font-size:11px}
.empty{border:1px dashed #d8dce1;border-radius:6px;padding:18px;color:#6b7178;font-size:13px;
       background:#fafbfc}
@media(max-width:640px){body{padding:16px 12px 32px}}
</style>"""

BACK_BUTTON = ('<div class="back-to-index" style="margin-bottom:10px">'
               '<a href="../" style="display:inline-block;font-size:12px;color:#1a5490;'
               'text-decoration:none;border:1px solid #cedff0;border-radius:5px;padding:5px 11px;'
               'background:#f0f6fc">← 보고서 목록</a></div>')


def planned_section(title, note, items):
    """아직 자료를 붙이지 않은 절. 무엇을 넣을지 적어 두어 다음 작업의 목록이 되게 한다."""
    rows = "".join(f"<li style='margin:3px 0'>{escape(item)}</li>" for item in items)
    return (f'<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;'
            f'border-bottom:1px solid #ddd">{escape(title)}</h3>'
            f'<div class="empty">{escape(note)}'
            f'<ul style="margin:8px 0 0;padding-left:18px;font-size:12px">{rows}</ul></div>')


def fx_decomposition_section(frame, info):
    """원/달러 결정 요인 표. 시차별로 무엇이 몇 %를 설명했는지."""
    sys.path.insert(0, str(ROOT / "tools"))
    from fx_variance import FX_FACTORS, FX_LABELS, HORIZONS, fit_and_decompose

    head = ('<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;'
            'border-bottom:1px solid #ddd">원/달러 결정 요인 '
            '<span style="font-weight:400;color:#8a9199;font-size:12px">'
            '&nbsp;VAR 분산분해 · 과거 자료의 분해이지 예측이 아닙니다</span></h3>')
    if frame is None or frame.empty:
        return head + ('<div class="empty">자료를 받지 못했습니다 — '
                       + escape(", ".join(f"{k}: {v}" for k, v in (info or {}).get("failed", {}).items())
                                or "원인 미상") + '</div>')
    table, diag = fit_and_decompose(frame)
    if table is None:
        return head + f'<div class="empty">{escape(diag.get("error", "계산할 수 없습니다."))}</div>'

    factors = [c for c in FX_FACTORS if c in diag["factors"]]
    rows = ""
    for horizon in HORIZONS:
        cells = ""
        for name in factors:
            share = table[horizon].get(name)
            # 자기 자신(원/달러)은 '다른 요인으로 설명되지 않은 몫'이라 회색으로 눌러 둔다.
            style = "color:#8a9199" if name == "usdkrw" else ""
            cells += (f'<td class="num" style="{style}">—</td>' if share is None or not np.isfinite(share)
                      else f'<td class="num" style="{style}">{share:.0%}</td>')
        rows += f'<tr><td>{horizon}개월</td>{cells}</tr>'
    header = "".join(f'<th class="num">{escape(FX_LABELS[c])}</th>' for c in factors)
    note = (f'{escape(diag["first"])}~{escape(diag["last"])} 월별 {diag["n"]}개 · 시차 {diag["lag"]}개월 · '
            f'{escape(diag["method"])}')
    return (head +
            '<div style="overflow-x:auto"><table style="min-width:620px">'
            f'<tr><th>시차</th>{header}</tr>{rows}</table></div>'
            f'<div class="muted" style="margin-top:6px">{note}</div>'
            '<div style="font-size:12px;color:#6b7178;line-height:1.7;margin-top:8px">'
            '각 행은 그 시차에서 원/달러 변동의 예측오차 분산을 100%로 놓고 나눈 것입니다. '
            '<b>원/달러</b> 칸은 다른 요인으로 설명되지 않은 몫이라, 그 값이 클수록 "밖에서 온 것으로는 '
            '설명이 잘 안 된다"는 뜻입니다. 일반화 분산분해는 합이 100%가 되지 않아 행별로 정규화했습니다. '
            '인과가 아니라 과거 자료에서의 동행·선행 관계입니다.</div>')


# 절 목록. 지표를 붙일 때 여기에 함수를 더하면 페이지 구조는 건드리지 않아도 된다.
SECTIONS = (
    ("환율", "원/달러 결정 요인 표는 위에 있습니다. 아래는 앞으로 더할 것입니다.",
     ("원/달러 종가와 이동평균", "엔/달러·달러지수와 함께 본 상대 강도", "최근 변동성")),
    ("금리", "한국·미국 정책금리와 시장금리를 나란히 놓습니다.",
     ("한국은행 기준금리", "미국 연방기금금리 목표", "한·미 금리차")),
    ("채권", "국채 수익률 곡선과 장단기 금리차를 봅니다.",
     ("한국 3년·10년 국고채", "미국 2년·10년 국채", "장단기 금리차(경기 신호로 읽히는 값)")),
    ("경기 지표", "이미 이 저장소가 받고 있는 지표를 한곳에 모읍니다.",
     ("선행지수 순환변동치", "G20 경기선행지수", "뉴스심리지수", "반도체 수출")),
)


def build_page(now=None, fx_frame=None, fx_info=None):
    now = now or datetime.now(KST)
    body = fx_decomposition_section(fx_frame, fx_info) if fx_frame is not None else ""
    body += "".join(planned_section(title, note, items) for title, note, items in SECTIONS)
    return (
        '<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(TITLE)}</title>{_STYLE}</head><body>'
        '<div class="wrap">'
        + BACK_BUTTON +
        '<div style="border-bottom:3px solid #1a1a1a;padding-bottom:11px;margin-bottom:18px">'
        '<div style="font-size:11px;letter-spacing:2px;color:#8a9199">MACRO ECONOMY</div>'
        f'<h2 style="margin:6px 0 5px;font-size:27px">{escape(TITLE)}</h2>'
        f'<div style="font-size:12px;color:#8a9199">{now:%Y-%m-%d %H:%M} KST 기준 · '
        '환율·금리·채권 등 거시 지표를 모아 봅니다. 예측이 아니라 자료 정리입니다.</div></div>'
        '<div style="background:#fdf8ec;border-left:4px solid #c8952a;padding:11px 15px;'
        'border-radius:0 5px 5px 0;font-size:12px;line-height:1.7;margin-bottom:6px">'
        '<b>준비 중입니다.</b> 아래는 앞으로 넣을 항목이고, 자료가 붙는 대로 하나씩 채웁니다. '
        '값이 없는 절은 비어 있는 그대로 두고 추측해 채우지 않습니다.</div>'
        + body +
        '<div style="margin-top:24px;padding-top:14px;border-top:1px solid #e5e5e5;'
        'font-size:12px;color:#8a9199">'
        f'생성 {now:%Y-%m-%d %H:%M} KST · '
        f'<a href="https://github.com/{GITHUB_REPO}">저장소</a><br>'
        '연구·교육용입니다. 투자 자문이 아닙니다.</div>'
        '</div></body></html>')


FX_CACHE = ROOT / "macro_history" / "fx_inputs.csv"


def load_fx(fetch=True):
    """(자료, 진단). 받은 것은 보관본에 누적해 다음 실행이 실패해도 표가 비지 않게 한다."""
    # 자료원 모듈끼리는 서로 참조하지 않는다(그 규칙을 테스트가 지킨다). 야후와 ECOS 를 여기서
    # 엮는다 — 결합은 도구의 몫이다.
    from data_sources.fx_inputs import build_fx_inputs
    from data_sources.ecos import fetch_korea_rate_monthly, fetch_current_account_monthly
    try:
        frame, info = build_fx_inputs(fetch=fetch, cache_path=FX_CACHE,
                                      korea_rate_fn=fetch_korea_rate_monthly,
                                      current_account_fn=fetch_current_account_monthly)
    except Exception as exc:
        return None, {"failed": {"전체": f"{type(exc).__name__}: {exc}"[:160]}}
    if len(frame):
        FX_CACHE.parent.mkdir(parents=True, exist_ok=True)
        frame.reset_index().to_csv(FX_CACHE, index=False)
    return frame, info


def main():
    parser = argparse.ArgumentParser(description="거시 경제 보고서를 만든다")
    parser.add_argument("--write", action="store_true", help="docs/macro/index.html 에 저장")
    parser.add_argument("--no-fetch", action="store_true", help="보관본만 쓰고 조회하지 않는다")
    args = parser.parse_args()
    frame, info = load_fx(fetch=not args.no_fetch)
    if info.get("failed"):
        for name, reason in info["failed"].items():
            print(f"  ⚠️ {name}: {reason}", flush=True)
    if frame is not None and len(frame):
        print(f"  환율 자료: {info.get('source')} · {info.get('first')}~{info.get('last')} "
              f"({info.get('rows')}개월, {len(info.get('columns', []))}계열)", flush=True)
    page = build_page(fx_frame=frame, fx_info=info)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(page, encoding="utf-8")
        print(f"저장: {OUT.relative_to(ROOT)} ({len(page):,} bytes)")
    else:
        print(page)


if __name__ == "__main__":
    main()
