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


# 절 목록. 지표를 붙일 때 여기에 함수를 더하면 페이지 구조는 건드리지 않아도 된다.
SECTIONS = (
    ("환율", "원/달러를 중심으로 주요 통화를 함께 봅니다.",
     ("원/달러 종가와 이동평균", "엔/달러·달러지수와 함께 본 상대 강도", "최근 변동성")),
    ("금리", "한국·미국 정책금리와 시장금리를 나란히 놓습니다.",
     ("한국은행 기준금리", "미국 연방기금금리 목표", "한·미 금리차")),
    ("채권", "국채 수익률 곡선과 장단기 금리차를 봅니다.",
     ("한국 3년·10년 국고채", "미국 2년·10년 국채", "장단기 금리차(경기 신호로 읽히는 값)")),
    ("경기 지표", "이미 이 저장소가 받고 있는 지표를 한곳에 모읍니다.",
     ("선행지수 순환변동치", "G20 경기선행지수", "뉴스심리지수", "반도체 수출")),
)


def build_page(now=None):
    now = now or datetime.now(KST)
    body = "".join(planned_section(title, note, items) for title, note, items in SECTIONS)
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


def main():
    parser = argparse.ArgumentParser(description="거시 경제 보고서를 만든다")
    parser.add_argument("--write", action="store_true", help="docs/macro/index.html 에 저장")
    args = parser.parse_args()
    page = build_page()
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(page, encoding="utf-8")
        print(f"저장: {OUT.relative_to(ROOT)} ({len(page):,} bytes)")
    else:
        print(page)


if __name__ == "__main__":
    main()
